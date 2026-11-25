"""区間だけを WORLD で再合成し、元の音声にクロスフェードで埋め戻す。

区間の外は元の音声のサンプルをそのまま使う(全体を再合成すると音色が変わるため)。

時間の配置(1 つの「スパン」= まとめて再合成する範囲):

    |<- 合成の余白 ->|<- XF ->|<- 余裕 ->|== 区間(効果) ==|<- 余裕 ->|<- XF ->|<- 合成の余白 ->|
                     t0                                                    t1
    t0〜t1 の外は元音声そのまま。XF の間に元音声 ⇔ 再合成をクロスフェードする。
    XF の間は効果がまだ 0 なので、つなぎ目では「元音声」と「無加工の再合成」が混ざるだけ。
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import pyworld as pw

from . import dsp, effects
from .analysis import Analysis, frame_alignment, hop_samples
from .effects.base import RenderContext, SegmentState, effective_strength

CROSSFADE_S = 0.04  # 仕様: 30〜50ms
GUARD_S = 0.02  # クロスフェードの終わりから効果が始まるまでの余裕
SYNTH_MARGIN_S = 0.06  # 合成の端の乱れを避けるため、スパンより広く合成する
MERGE_GAP_S = 0.15  # これより近いスパンはまとめて合成する
LIMIT_CEILING = 0.98  # 出力ピークの上限(元音声のピークがこれより大きければそちらに合わせる)


@dataclass
class Span:
    t0: float
    t1: float
    regions: list = field(default_factory=list)

    @property
    def key(self) -> tuple:
        return (round(self.t0, 4), round(self.t1, 4))


@dataclass
class RenderResult:
    data: np.ndarray
    spans: list
    notes: list


def reference_rms(analysis: Analysis) -> float:
    """有声部分の RMS の中央値(挿入する音の音量の基準)。"""
    v = analysis.rms_db[analysis.voiced]
    db = float(np.median(v)) if v.size else -30.0
    return float(10.0 ** (db / 20.0))


def make_context(analysis: Analysis, project: dict) -> RenderContext:
    return RenderContext(
        fs=analysis.fs,
        frame_period=analysis.frame_period,
        n_frames=analysis.n_frames,
        n_bins=analysis.n_bins,
        fft_size=analysis.fft_size,
        voiced_weight=dsp.smooth_mask(analysis.voiced, analysis.frame_period, 30.0),
        silent=analysis.silent,
        global_amount=float(project.get("global_cry_amount", 100.0)) / 100.0,
        seed=int(project.get("seed", 0) or 0),
        active_weight=dsp.smooth_mask(~analysis.silent, analysis.frame_period, 30.0),
        ref_rms=reference_rms(analysis),
    )


def active_regions(project: dict, ctx: RenderContext) -> list[dict]:
    out = []
    for r in project.get("regions", []):
        eff = effects.get(r.get("type"))
        if eff is None or not eff.implemented or r.get("enabled", True) is False:
            continue
        if effective_strength(r, ctx) <= 0:
            continue
        out.append(r)
    return out


def plan_spans(regions: list[dict], ctx: RenderContext, duration: float) -> list[Span]:
    fp = ctx.frame_period / 1000.0
    k = frame_alignment(ctx.fs, ctx.frame_period)
    step = k * fp
    pad = CROSSFADE_S + GUARD_S
    items = []
    for r in regions:
        s, e = effects.get(r["type"]).extent(r)
        s, e = max(0.0, s), min(duration, e)
        if e <= s:
            continue
        t0 = max(0.0, np.floor((s - pad) / step) * step)
        t1 = min(duration, np.ceil((e + pad) / step) * step)
        items.append((t0, t1, r))
    items.sort(key=lambda x: x[0])
    spans: list[Span] = []
    for t0, t1, r in items:
        if spans and t0 <= spans[-1].t1 + MERGE_GAP_S:
            spans[-1].t1 = max(spans[-1].t1, t1)
            spans[-1].regions.append(r)
        else:
            spans.append(Span(t0, t1, [r]))
    return spans


def _sorted_for_apply(regions: list[dict]) -> list[dict]:
    order = {n: i for i, n in enumerate(effects.ORDER)}
    return sorted(regions, key=lambda r: (order.get(r["type"], 99), r.get("start", r.get("time", 0.0))))


def synthesize_span(analysis: Analysis, ctx: RenderContext, span: Span, notes: list | None = None):
    """スパンの再合成(モノラル)。戻り値: (先頭サンプル番号, 信号)。"""
    fp = analysis.frame_period / 1000.0
    k = frame_alignment(analysis.fs, analysis.frame_period)
    hop = hop_samples(analysis.fs, analysis.frame_period)
    T = analysis.n_frames

    fa = int(np.floor((span.t0 - SYNTH_MARGIN_S) / fp / k)) * k
    fa = max(0, fa)
    fb = int(np.ceil((span.t1 + SYNTH_MARGIN_S) / fp))
    fb = min(T - 1, fb)

    state = SegmentState(
        offset=fa,
        f0=np.array(analysis.f0[fa:fb + 1], dtype=np.float64),
        sp=np.array(analysis.sp[fa:fb + 1], dtype=np.float64),
        ap=np.array(analysis.ap[fa:fb + 1], dtype=np.float64),
        gain_db=np.zeros(fb + 1 - fa),
    )
    for r in _sorted_for_apply(span.regions):
        effects.get(r["type"]).apply(state, r, ctx)
    if notes is not None:
        notes.extend(state.notes)

    f0 = np.nan_to_num(state.f0, nan=0.0, posinf=0.0, neginf=0.0)
    f0 = np.clip(f0, 0.0, analysis.fs / 2.0 - 1.0)
    sp = np.ascontiguousarray(np.maximum(np.nan_to_num(state.sp, nan=1e-20), 1e-20))
    ap = np.ascontiguousarray(np.clip(np.nan_to_num(state.ap, nan=1.0), 1e-6, 1.0 - 1e-6))
    y = pw.synthesize(np.ascontiguousarray(f0), sp, ap, analysis.fs, analysis.frame_period)
    y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)

    # フレームごとの音量をサンプル単位に補間してかける
    gdb = np.clip(state.gain_db, -80.0, 12.0)
    if np.any(gdb != 0):
        ft = np.arange(len(gdb)) * hop
        g = np.power(10.0, np.interp(np.arange(len(y)), ft, gdb) / 20.0)
        y = y * g
    sa = int(round(fa * hop))
    return sa, y


def crossfade_mask(n: int, xf: int, fade_in: bool, fade_out: bool) -> np.ndarray:
    m = np.ones(n)
    xf = min(xf, n // 2)
    if xf > 0:
        ramp = dsp.raised_cosine((np.arange(xf) + 0.5) / xf)
        if fade_in:
            m[:xf] = ramp
        if fade_out:
            m[n - xf:] = ramp[::-1]
    return m


def _crossfade_gains(x: np.ndarray, y: np.ndarray, m: np.ndarray, xf: int):
    """元音声 x と再合成 y のクロスフェードの係数 (a, b)。

    WORLD の再合成は元の波形と位相が揃わないため、単純な a + b = 1 では
    クロスフェードの途中で音量が落ちる。つなぎ目ごとに x と y の相関 rho を測り、
    a^2 + b^2 + 2*rho*a*b = 1 になるよう正規化する(rho = 1 なら a + b = 1 と同じ)。
    """
    a = 1.0 - m
    b = m.copy()
    n = len(m)
    trans = np.flatnonzero((m > 0.0) & (m < 1.0))
    if trans.size == 0:
        return a, b
    # 連続した遷移区間ごとに処理
    breaks = np.flatnonzero(np.diff(trans) > 1)
    groups = np.split(trans, breaks + 1)
    for g in groups:
        lo, hi = g[0], g[-1] + 1
        xs, ys = x[lo:hi], y[lo:hi]
        den = np.sqrt(np.dot(xs, xs) * np.dot(ys, ys))
        rho = float(np.clip(np.dot(xs, ys) / den, 0.0, 1.0)) if den > 1e-12 else 1.0
        aa, bb = a[lo:hi], b[lo:hi]
        norm = np.sqrt(aa * aa + bb * bb + 2.0 * rho * aa * bb)
        a[lo:hi] = aa / norm
        b[lo:hi] = bb / norm
    return a, b


def render_span(data: np.ndarray, mono: np.ndarray, analysis: Analysis, ctx: RenderContext,
                span: Span, notes: list | None = None):
    """スパン 1 つ分を処理して (s0, s1, block[s1-s0, ch]) を返す。block の両端は元音声と一致する。"""
    fs = analysis.fs
    N = data.shape[0]
    s0 = int(round(span.t0 * fs))
    s1 = min(N, int(round(span.t1 * fs)))
    x = data[s0:s1]
    xm = mono[s0:s1]

    def _is(r, attr):
        return getattr(effects.get(r["type"]), attr)

    inserts = [r for r in span.regions if _is(r, "inserts_in_silence")]
    gain_regions = [r for r in span.regions if _is(r, "gain_only")]
    world = [r for r in span.regions if not _is(r, "inserts_in_silence") and not _is(r, "gain_only")]

    if world:
        sa, y = synthesize_span(analysis, ctx, Span(span.t0, span.t1, world), notes)
        seg = np.zeros(s1 - s0)
        have = np.zeros(s1 - s0, dtype=bool)
        a = max(s0, sa)
        b = min(s1, sa + len(y))
        if b > a:
            seg[a - s0:b - s0] = y[a - sa:b - sa]
            have[a - s0:b - s0] = True

        m = crossfade_mask(s1 - s0, int(round(CROSSFADE_S * fs)), fade_in=s0 > 0, fade_out=s1 < N)
        m[~have] = 0.0

        rms_m = np.sqrt(np.mean(xm ** 2)) if len(xm) else 0.0
        if data.shape[1] == 1:
            gains = np.ones(1)
        else:
            rms_c = np.sqrt(np.mean(x ** 2, axis=0))
            gains = rms_c / rms_m if rms_m > 1e-9 else np.ones(data.shape[1])
        g_old, g_new = _crossfade_gains(xm, seg, m, int(round(CROSSFADE_S * fs)))
        block = x * g_old[:, None] + (seg * g_new)[:, None] * gains[None, :]
    else:
        # 再合成する処理がないスパンは元の音をそのまま使う
        block = x.copy()

    # 音量だけを変える処理(声の詰まり)はサンプル単位でかける
    if gain_regions:
        t = (s0 + np.arange(s1 - s0)) / fs
        gdb = np.zeros(s1 - s0)
        for r in gain_regions:
            eff = effects.get(r["type"])
            gdb += eff.gain_db_at(r, ctx, t)
            if notes is not None:
                notes.extend(eff.check(r, ctx))
        block = block * np.power(10.0, np.clip(gdb, -80.0, 12.0) / 20.0)[:, None]

    # 無音に重ねる音(全チャンネルに同じ音量で足す)
    for r in inserts:
        res = effects.get(r["type"]).insert(r, ctx)
        if res is None:
            continue
        st, sig = res
        a = max(s0, st)
        b = min(s1, st + len(sig))
        if b > a:
            block[a - s0:b - s0] += sig[a - st:b - st, None]

    ceiling = min(1.0, max(LIMIT_CEILING, float(np.max(np.abs(x))) if len(x) else 0.0))
    block = dsp.limit_peaks(block, ceiling, fs)
    return s0, s1, block


def render(audio_data: np.ndarray, analysis: Analysis, project: dict, progress=None,
           mono: np.ndarray | None = None) -> RenderResult:
    """audio_data: (n, ch)。出力は同じ形・同じ長さ。"""
    data = np.asarray(audio_data, dtype=np.float64)
    if data.ndim == 1:
        data = data[:, None]
    if mono is None:
        mono = data.mean(axis=1)
    if data.shape[0] != analysis.n_samples:
        raise ValueError("analysis and audio lengths do not match")
    ctx = make_context(analysis, project)
    regions = active_regions(project, ctx)
    spans = plan_spans(regions, ctx, data.shape[0] / analysis.fs)
    out = data.copy()
    notes: list = []
    for i, sp in enumerate(spans):
        s0, s1, block = render_span(data, mono, analysis, ctx, sp, notes)
        out[s0:s1] = block
        if progress:
            progress((i + 1) / len(spans))
    if not np.all(np.isfinite(out)):
        raise RuntimeError("the processed audio contains NaN / Inf")
    return RenderResult(out, spans, notes)
