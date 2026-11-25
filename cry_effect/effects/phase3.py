"""フェーズ3 の処理: glottal_catch / f2_raise / nasal / sfx。"""

from __future__ import annotations

import numpy as np
from scipy import signal

from .. import dsp, materials
from ..i18n import Msg
from .base import Effect, ParamSpec, RenderContext, SegmentState, effective_strength
from .phase2 import fade_edges


# --------------------------------------------------------------------------
# 9. 声の詰まり(マーカー)
# --------------------------------------------------------------------------

class GlottalCatch(Effect):
    """マーカーの位置で、声を一瞬ほぼ無音まで落とす(喉が詰まって声が途切れる)。

    音量だけを変える処理なので再合成はせず、元の音にサンプル単位の音量変化をかける。
    """

    name = "glottal_catch"
    label = "声の詰まり"
    label_en = "Glottal catch"
    kind = "marker"
    phase = 3
    gain_only = True
    uses_ramp = False
    description = "マーカーの位置から 20〜100ms、声をほぼ無音まで落とす(元の音に音量変化だけをかける)"
    description_en = 'Drops the voice to near silence for 20–100 ms from the marker (gain only, no resynthesis)'
    params = (
        ParamSpec("duration_ms", "長さ", 40.0, 20.0, 100.0, 1.0, "ms", label_en='Duration'),
        ParamSpec("depth_db", "落とす量", 40.0, 12.0, 80.0, 1.0, "dB", label_en='Drop'),
    )

    ATTACK_S = 0.003  # マーカーの手前で落ち始める
    RELEASE_S = 0.010  # 長さの後で戻りきる(声の立ち上がりは少しゆっくり)

    def extent(self, region: dict) -> tuple[float, float]:
        t = float(region["time"])
        d = float(region.get("params", {}).get("duration_ms", 40.0)) / 1000.0
        return t - self.ATTACK_S - 0.002, t + d + self.RELEASE_S + 0.002

    def shape(self, t: np.ndarray, t0: float, dur: float) -> np.ndarray:
        """0..1。t0 の手前 ATTACK_S で 1 になり、dur の間保ち、RELEASE_S で 0 に戻る。"""
        a = dsp.raised_cosine((t - (t0 - self.ATTACK_S)) / self.ATTACK_S)
        r = dsp.raised_cosine(((t0 + dur + self.RELEASE_S) - t) / self.RELEASE_S)
        return np.minimum(a, r)

    def gain_db_at(self, region: dict, ctx: RenderContext, t: np.ndarray) -> np.ndarray:
        p = region["params"]
        k = self.shape(t, float(region["time"]), p["duration_ms"] / 1000.0)
        return -p["depth_db"] * effective_strength(region, ctx) * k

    def check(self, region: dict, ctx: RenderContext) -> list[str]:
        t0 = float(region["time"])
        i0, i1 = ctx.frame_range(t0, t0 + region["params"]["duration_ms"] / 1000.0)
        if i1 > i0 and np.mean(ctx.voiced_weight[i0:i1]) < 0.5:
            return [Msg(f"{self.label}({t0:.2f} 秒)は無声の部分にかかっているため、ほとんど聞こえません",
                        f"{self.label_en} ({t0:.2f} s) is on an unvoiced part and will be barely audible")]
        return []

    def apply(self, state, region, ctx) -> None:
        return None


# --------------------------------------------------------------------------
# 10. F2 の上昇
# --------------------------------------------------------------------------

class F2Raise(Effect):
    """900〜2500Hz の帯域だけ包絡を上へ伸ばす(泣いて顔がゆがむ、口角が横に引かれた響き)。"""

    name = "f2_raise"
    label = "F2 の上昇"
    label_en = "F2 raise"
    phase = 3
    F2_LO, F2_HI, F2_PIVOT = 900.0, 2500.0, 1700.0
    description = "900〜2500Hz の帯域だけ包絡を上へ伸ばす(1700Hz 付近を 1+量 倍へ動かす。帯域の外は動かさない)"
    description_en = 'Stretches only the 900–2500 Hz band upward (moves ~1700 Hz to 1+amount times; nothing outside the band moves)'
    params = (ParamSpec("amount", "持ち上げる量", 0.15, 0.0, 0.45, 0.005, label_en='Raise amount'),)

    def apply(self, state: SegmentState, region: dict, ctx: RenderContext) -> None:
        i0, i1, w = self.envelope(region, ctx)
        if i1 <= i0:
            return
        active = w > 1e-4
        if not np.any(active):
            return
        sl = state.local(i0, i1)
        rows = np.flatnonzero(active) + sl.start
        amount = region["params"]["amount"] * w[active]
        src = dsp.band_stretch_map(ctx.n_bins, ctx.bin_hz, self.F2_LO, self.F2_HI, amount, self.F2_PIVOT)
        state.sp[rows] = dsp.warp_envelope(state.sp[rows], src)


# --------------------------------------------------------------------------
# 11. 鼻にかかる声
# --------------------------------------------------------------------------

def nasal_curve_db(n_bins: int, bin_hz: float, high_cut_db: float, low_boost_db: float) -> np.ndarray:
    """周波数ごとの増減(dB)。300〜500Hz を持ち上げ、1.2kHz から上を 3.5kHz にかけて下げる。"""
    f = np.maximum(np.arange(n_bins) * bin_hz, 1.0)
    boost = low_boost_db * np.exp(-0.5 * (np.log2(f / 400.0) / 0.35) ** 2)
    cut = high_cut_db * dsp.smoothstep((f - 1200.0) / 2300.0)
    return boost - cut


class Nasal(Effect):
    """鼻が詰まったような声のおおまかな近似。高域を下げ、300〜500Hz を少し持ち上げる。"""

    name = "nasal"
    label = "鼻にかかる声"
    label_en = "Nasal voice"
    phase = 3
    description = "高域(1.2kHz〜)を下げ、300〜500Hz を少し持ち上げる(鼻詰まりの声のおおまかな近似)"
    description_en = 'Cuts the highs (from 1.2 kHz) and lifts 300–500 Hz a little (a rough stuffy-nose approximation)'
    params = (
        ParamSpec("high_cut_db", "高域を下げる量", 6.0, 0.0, 18.0, 0.5, "dB", label_en='High cut'),
        ParamSpec("low_boost_db", "300〜500Hz を上げる量", 3.0, 0.0, 9.0, 0.5, "dB", label_en='300–500 Hz boost'),
    )

    def apply(self, state: SegmentState, region: dict, ctx: RenderContext) -> None:
        p = region["params"]
        i0, i1, w = self.envelope(region, ctx, voiced_only=False)
        if i1 <= i0:
            return
        active = ctx.active_weight if ctx.active_weight is not None else (~ctx.silent).astype(float)
        w = w * active[i0:i1]
        curve = nasal_curve_db(ctx.n_bins, ctx.bin_hz, p["high_cut_db"], p["low_boost_db"])
        sl = state.local(i0, i1)
        state.sp[sl] = state.sp[sl] * np.power(10.0, w[:, None] * curve[None, :] / 10.0)


# --------------------------------------------------------------------------
# 12. 鼻をすする音などの素材(無音に重ねる)
# --------------------------------------------------------------------------

def synth_sniff(rng: np.random.Generator, fs: int, dur: float) -> np.ndarray:
    """鼻をすする音の合成(RMS 1)。鼻腔を通る息の、高めの帯域のノイズ。"""
    n = max(16, int(dur * fs))
    nper = 1024 if fs <= 48000 else 2048
    noise = rng.standard_normal(n + nper)
    f, _, Z = signal.stft(noise, fs, nperseg=nper, noverlap=nper * 3 // 4)
    ff = np.maximum(f, 1.0)
    shape = (np.exp(-0.5 * (np.log2(ff / 3200.0) / 0.7) ** 2)
             + 0.35 * np.exp(-0.5 * (np.log2(ff / 1100.0) / 0.3) ** 2))  # 鼻腔の響き
    shape *= dsp.smoothstep((ff - 300.0) / 500.0)
    _, y = signal.istft(Z * shape[:, None], fs, nperseg=nper, noverlap=nper * 3 // 4)
    y = y[:n]
    uu = np.arange(n) / n
    att = 0.25
    env = np.where(uu < att, dsp.raised_cosine(uu / att), dsp.raised_cosine((1.0 - uu) / (1.0 - att)) ** 1.5)
    y = y * env
    r = np.sqrt(np.mean(y ** 2))
    return y / r if r > 0 else y


class Sfx(Effect):
    """無音区間に素材(鼻をすする音など)を重ねる。区間の始まりから鳴らし、区間の終わりで切る。"""

    name = "sfx"
    label = "鼻をすする音などの素材"
    label_en = "Sound effect (sniff etc.)"
    phase = 3
    inserts_in_silence = True
    uses_ramp = False
    description = "無音の部分に素材を重ねる。区間の始まりから鳴らし、終わりで切る。素材は materials/sfx の WAV か合成音(鼻をすする音)"
    description_en = 'Adds a sound in a silent part, starting at the region start and cut at its end. Uses a WAV from materials/sfx or a synthesized sniff'
    params = (
        ParamSpec("sample", "音源", "", kind="choice", choices_source="sfx", label_en='Source'),
        ParamSpec("level_db", "音量(声に対して)", -16.0, -36.0, 0.0, 0.5, "dB", label_en='Level (vs. voice)'),
        ParamSpec("duration_ms", "長さ(合成音)", 220.0, 100.0, 600.0, 10.0, "ms", label_en='Duration (synth)'),
    )

    def apply(self, state, region, ctx) -> None:
        return None

    def sound(self, region: dict, ctx: RenderContext) -> tuple[np.ndarray, bool]:
        """(RMS 1 の音, 素材を使ったか)。"""
        p = region["params"]
        length = float(region["end"]) - float(region["start"])
        x = materials.load("sfx", p.get("sample", ""), ctx.fs)
        if x is not None and len(x) > 0:
            n = min(len(x), int(length * ctx.fs))
            x = fade_edges(x[:n], ctx.fs, fade_in_ms=2.0)
            r = np.sqrt(np.mean(x ** 2))
            return (x / r if r > 0 else x), True
        dur = min(p["duration_ms"] / 1000.0, length)
        return synth_sniff(self.rng(region, ctx), ctx.fs, dur), False

    def insert(self, region: dict, ctx: RenderContext):
        s = effective_strength(region, ctx)
        if s <= 0:
            return None
        y, _ = self.sound(region, ctx)
        y = y * ctx.ref_rms * (10.0 ** (region["params"]["level_db"] / 20.0)) * s
        return int(round(float(region["start"]) * ctx.fs)), y
