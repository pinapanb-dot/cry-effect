"""フェーズ1 の処理: tremor / phrase_end / breathiness / sob_formant。"""

from __future__ import annotations

import numpy as np

from .. import dsp
from .base import Effect, ParamSpec, RenderContext, SegmentState


def _cents_to_ratio(c: np.ndarray) -> np.ndarray:
    return np.power(2.0, c / 1200.0)


class Tremor(Effect):
    """不規則な震え。F0 に帯域制限ノイズ由来の揺れを足す(正弦波は使わない)。"""

    name = "tremor"
    label = "不規則な震え"
    label_en = "Irregular tremor"
    description = "F0 に 3〜10Hz の不規則な揺れを加える"
    description_en = 'Adds an irregular 3–10 Hz wobble to F0 (band-limited noise, not a sine vibrato)'
    params = (
        ParamSpec("depth_cents", "深さ", 30.0, 0.0, 120.0, 1.0, "cent", label_en='Depth'),
        ParamSpec("rate_hz", "速さの中心", 5.0, 3.0, 10.0, 0.1, "Hz", label_en='Center rate'),
        ParamSpec("irregularity", "ばらつき", 0.5, 0.0, 1.0, 0.01, label_en='Irregularity'),
    )

    # ばらつき 0 でも規則的なビブラートにならないよう、帯域幅に下限を設ける
    MIN_BW_HZ = 2.5
    MAX_BW_HZ = 5.0
    BAND_LO_HZ = 2.5
    BAND_HI_HZ = 12.0

    def cents_curve(self, n: int, p: dict, rng: np.random.Generator, fs_ctrl: float) -> np.ndarray:
        """深さ p["depth_cents"] を絶対上限とする揺れ(セント)。"""
        irr = p["irregularity"]
        rate = p["rate_hz"]
        bw = self.MIN_BW_HZ + (self.MAX_BW_HZ - self.MIN_BW_HZ) * irr
        lo = max(self.BAND_LO_HZ, rate - bw / 2.0)
        hi = min(self.BAND_HI_HZ, rate + bw / 2.0)
        if hi - lo < 1.0:
            lo, hi = hi - 1.0, hi
        wobble = dsp.bandpass_noise(rng, n, fs_ctrl, lo, hi, order=1)
        # 振幅のばらつき(ゆっくり変わる包絡)
        am = 1.0 + (0.3 + 0.5 * irr) * dsp.lowpass_noise(rng, n, fs_ctrl, 0.8)
        am = np.clip(am, 0.2, 1.8)
        # tanh で丸めるので |cents| は必ず depth 以下
        return p["depth_cents"] * np.tanh(0.8 * wobble * am)

    def apply(self, state: SegmentState, region: dict, ctx: RenderContext) -> None:
        p = region["params"]
        i0, i1, w = self.envelope(region, ctx)
        if i1 <= i0:
            return
        cents = self.cents_curve(i1 - i0, p, self.rng(region, ctx), ctx.fs_ctrl) * w
        sl = state.local(i0, i1)
        state.f0[sl] = state.f0[sl] * _cents_to_ratio(cents)


class PhraseEnd(Effect):
    """語尾の崩れ。区間の終わりへ向けて息を増やし、音量を下げ、F0 を少し下げる。"""

    name = "phrase_end"
    label = "語尾の崩れ"
    label_en = "Phrase-end breakdown"
    description = "区間の終わりに向けて息に溶けるように消す(効果は区間の後ろ ramp_ms で解放)"
    description_en = 'Dissolves the end of the region into breath (released ramp_ms after the end)'
    release_after_end = True
    params = (
        ParamSpec("onset", "崩れ始める位置", 0.5, 0.0, 0.95, 0.01, label_en='Breakdown start'),
        ParamSpec("breath", "最終的な息の量", 0.7, 0.0, 0.95, 0.01, label_en='Final breath amount'),
        ParamSpec("level_drop_db", "音量の落とし幅", 15.0, 0.0, 40.0, 0.5, "dB", label_en='Level drop'),
        ParamSpec("f0_drop_cents", "F0 の下げ幅", -50.0, -250.0, 0.0, 1.0, "cent", label_en='F0 drop'),
        ParamSpec("rasp", "かすれ", 0.0, 0.0, 1.0, 0.01, label_en='Rasp'),
    )

    def collapse_curve(self, t: np.ndarray, start: float, end: float, onset: float) -> np.ndarray:
        t_on = start + onset * (end - start)
        span = max(end - t_on, 1e-3)
        c = dsp.smoothstep((t - t_on) / span)
        c[t > end] = 1.0
        return c

    def apply(self, state: SegmentState, region: dict, ctx: RenderContext) -> None:
        p = region["params"]
        i0, i1, w_all = self.envelope(region, ctx, voiced_only=False)
        if i1 <= i0:
            return
        t = ctx.times(i0, i1)
        c = self.collapse_curve(t, float(region["start"]), float(region["end"]), p["onset"])
        vw = ctx.voiced_weight[i0:i1]
        k = c * w_all  # 音量は無声部分も含めて下げる(息の部分だけ大きく残らないように)
        kv = k * vw  # 声質の変化は有声部分だけ
        sl = state.local(i0, i1)

        # 息: 低域も含めて非周期成分を増やす(低域は控えめ)
        fw = dsp.freq_weight(ctx.n_bins, ctx.bin_hz, 200.0, 1500.0, 0.6)
        add = p["breath"] * kv[:, None] * fw[None, :]
        state.ap[sl] = state.ap[sl] + (1.0 - state.ap[sl]) * add

        # 音量
        state.gain_db[sl] -= p["level_drop_db"] * k

        # F0 を少し下げる
        state.f0[sl] = state.f0[sl] * _cents_to_ratio(p["f0_drop_cents"] * kv)

        # かすれ: 周期と振幅の不規則な揺れ(ジッタ / シマー)で、低い声の不規則なパルスに近づける
        if p["rasp"] > 0:
            rng = self.rng(region, ctx)
            n = i1 - i0
            jit = dsp.lowpass_noise(rng, n, ctx.fs_ctrl, 40.0)
            shim = dsp.lowpass_noise(rng, n, ctx.fs_ctrl, 30.0)
            r = p["rasp"] * kv
            state.f0[sl] = state.f0[sl] * _cents_to_ratio(np.clip(jit, -3, 3) * 60.0 * r)
            state.gain_db[sl] += np.clip(shim, -3, 3) * 2.5 * r
            state.ap[sl] = state.ap[sl] + (1.0 - state.ap[sl]) * (0.25 * r[:, None])


class Breathiness(Effect):
    """息っぽさ。非周期成分を増やす(高域ほど多く)。"""

    name = "breathiness"
    label = "息っぽさ"
    label_en = "Breathiness"
    description = "非周期成分を増やす"
    description_en = 'Adds aperiodic (breath) noise'
    params = (ParamSpec("amount", "量", 0.3, 0.0, 0.9, 0.01, label_en='Amount'),)

    def apply(self, state: SegmentState, region: dict, ctx: RenderContext) -> None:
        p = region["params"]
        i0, i1, w = self.envelope(region, ctx)
        if i1 <= i0:
            return
        sl = state.local(i0, i1)
        fw = dsp.freq_weight(ctx.n_bins, ctx.bin_hz, 300.0, 2500.0, 0.35)
        add = p["amount"] * w[:, None] * fw[None, :]
        state.ap[sl] = state.ap[sl] + (1.0 - state.ap[sl]) * add


class SobFormant(Effect):
    """むせび泣き。スペクトル包絡を周波数軸方向に縮めてフォルマント全体を下げる。F0 は変えない。"""

    name = "sob_formant"
    label = "むせび泣き(フォルマント低下)"
    label_en = "Sob (lower formants)"
    description = "包絡を周波数方向に縮め、喉頭を下げた暗い響きにする"
    description_en = 'Compresses the envelope in frequency for a darker, lowered-larynx tone'
    params = (ParamSpec("warp", "縮める率", 0.94, 0.78, 1.00, 0.005, label_en='Warp ratio'),)

    def apply(self, state: SegmentState, region: dict, ctx: RenderContext) -> None:
        p = region["params"]
        i0, i1, w = self.envelope(region, ctx)
        if i1 <= i0:
            return
        active = w > 1e-4
        if not np.any(active):
            return
        sl = state.local(i0, i1)
        rows = np.flatnonzero(active) + sl.start
        alpha = 1.0 - (1.0 - p["warp"]) * w[active]
        state.sp[rows] = dsp.warp_envelope(state.sp[rows], dsp.scale_map(ctx.n_bins, alpha))
