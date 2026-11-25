"""フェーズ2 の処理: sob_pulse / voice_break / articulation_blur / sob_inhale。"""

from __future__ import annotations

import numpy as np
from scipy import fft as sfft
from scipy import signal
from scipy.ndimage import convolve1d

from .. import dsp, materials
from ..i18n import Msg
from .base import Effect, ParamSpec, RenderContext, SegmentState, effective_strength


def _semi_to_ratio(s: np.ndarray) -> np.ndarray:
    return np.power(2.0, s / 12.0)


# --------------------------------------------------------------------------
# 5. 嗚咽の脈動
# --------------------------------------------------------------------------

class SobPulse(Effect):
    """母音の途中に、息が引きつるような短い音量の落ち込みと息の増加を不規則に入れる。"""

    name = "sob_pulse"
    label = "嗚咽の脈動"
    label_en = "Sob pulses"
    phase = 2
    description = "母音の途中に、息が引きつるような音量の脈動を不規則に入れる(1 秒に 2〜8 回)"
    description_en = 'Irregular gasping dips in level inside vowels (2–8 per second)'
    params = (
        ParamSpec("rate_hz", "回数の目安", 3.5, 2.0, 8.0, 0.1, "回/秒", label_en='Rate (approx.)', unit_en='/s'),
        ParamSpec("depth_db", "深さ", 8.0, 0.0, 24.0, 0.5, "dB", label_en='Depth'),
        ParamSpec("irregularity", "ばらつき", 0.6, 0.0, 1.0, 0.01, label_en='Irregularity'),
    )

    MIN_INTERVAL_S = 0.12
    MAX_INTERVAL_S = 1.2
    WIDTH_S = 0.09  # 脈動 1 回の長さ
    BREATH = 0.55  # 脈動の頂点で足す非周期成分の量

    def pulse_times(self, start: float, end: float, p: dict, rng: np.random.Generator):
        """脈動の (開始時刻, 長さ, 大きさ) のリスト。"""
        rate, irr = p["rate_hz"], p["irregularity"]
        out = []
        t = start + rng.uniform(0.3, 1.0) / rate
        while t < end:
            width = self.WIDTH_S * float(np.clip(1.0 + 0.3 * irr * rng.standard_normal(), 0.55, 1.6))
            amp = 1.0 - 0.45 * irr * rng.random()
            out.append((t, width, amp))
            iv = (1.0 / rate) * float(np.exp(0.55 * irr * rng.standard_normal()))
            t += float(np.clip(iv, self.MIN_INTERVAL_S, self.MAX_INTERVAL_S))
        return out

    @staticmethod
    def pulse_shape(u: np.ndarray) -> np.ndarray:
        """0..1 の時間で、素早く落ち込み(25%)ゆっくり戻る(75%)形。"""
        att = 0.25
        a = dsp.raised_cosine(u / att)
        r = dsp.raised_cosine((1.0 - u) / (1.0 - att))
        out = np.where(u < att, a, r)
        out[(u < 0) | (u > 1)] = 0.0
        return out

    def pulse_curve(self, t: np.ndarray, region: dict, p: dict, rng) -> np.ndarray:
        env = np.zeros_like(t)
        for t0, width, amp in self.pulse_times(float(region["start"]), float(region["end"]), p, rng):
            env = np.maximum(env, amp * self.pulse_shape((t - t0) / width))
        return env

    def apply(self, state: SegmentState, region: dict, ctx: RenderContext) -> None:
        p = region["params"]
        i0, i1, w = self.envelope(region, ctx)
        if i1 <= i0:
            return
        env = self.pulse_curve(ctx.times(i0, i1), region, p, self.rng(region, ctx)) * w
        sl = state.local(i0, i1)
        state.gain_db[sl] -= p["depth_db"] * env
        fw = dsp.freq_weight(ctx.n_bins, ctx.bin_hz, 300.0, 2000.0, 0.5)
        add = self.BREATH * env[:, None] * fw[None, :]
        state.ap[sl] = state.ap[sl] + (1.0 - state.ap[sl]) * add


# --------------------------------------------------------------------------
# 6. 声の裏返り(マーカー)
# --------------------------------------------------------------------------

class VoiceBreak(Effect):
    """マーカーの位置で短く上へ跳ねて戻る。跳ねている間は高域の倍音を弱め、息を増やす。"""

    name = "voice_break"
    label = "声の裏返り"
    label_en = "Voice break"
    kind = "marker"
    phase = 2
    uses_ramp = False
    description = "マーカーの位置から短く上へ跳ねて戻る(ファルセットらしく高域を弱める)"
    description_en = 'Briefly jumps up from the marker and back (highs reduced, falsetto-like)'
    params = (
        ParamSpec("semitones", "跳ねる幅", 5.0, 3.0, 10.0, 0.5, "半音", label_en='Jump size', unit_en='st'),
        ParamSpec("duration_ms", "長さ", 100.0, 60.0, 220.0, 5.0, "ms", label_en='Duration'),
        ParamSpec("harmonic_cut", "倍音を弱める量", 0.5, 0.0, 1.0, 0.01, label_en='Harmonic cut'),
    )

    RISE = 0.25  # 長さに対する立ち上がりの割合
    FALL = 0.35  # 戻りの割合
    MAX_CUT_DB = 18.0  # harmonic_cut = 1 のときの高域の減衰
    BREATH = 0.45

    def extent(self, region: dict) -> tuple[float, float]:
        t = float(region["time"])
        d = float(region.get("params", {}).get("duration_ms", 100.0)) / 1000.0
        return t - 0.005, t + d + 0.005

    def shape(self, t: np.ndarray, t0: float, dur: float) -> np.ndarray:
        u = (t - t0) / dur
        e = dsp.raised_cosine(u / self.RISE) * dsp.raised_cosine((1.0 - u) / self.FALL)
        e[(u < 0) | (u > 1)] = 0.0
        return e

    def apply(self, state: SegmentState, region: dict, ctx: RenderContext) -> None:
        p = region["params"]
        t0 = float(region["time"])
        dur = p["duration_ms"] / 1000.0
        i0, i1 = ctx.frame_range(t0 - 0.01, t0 + dur + 0.01)
        if i1 <= i0:
            return
        vw = ctx.voiced_weight[i0:i1]
        e = self.shape(ctx.times(i0, i1), t0, dur)
        k = e * effective_strength(region, ctx) * vw
        if np.sum(e * vw) < 0.5 * np.sum(e):
            state.notes.append(Msg(f"{self.label}({t0:.2f} 秒)は無声の部分にかかっているため、ほとんど効きません",
                                   f"{self.label_en} ({t0:.2f} s) is on an unvoiced part and will have little effect"))
        sl = state.local(i0, i1)
        state.f0[sl] = state.f0[sl] * _semi_to_ratio(p["semitones"] * k)
        # ファルセットらしさ: 800Hz から 3kHz にかけて高域を弱める
        f = np.arange(ctx.n_bins) * ctx.bin_hz
        tilt_db = -self.MAX_CUT_DB * p["harmonic_cut"] * dsp.smoothstep((f - 800.0) / 2200.0)
        state.sp[sl] = state.sp[sl] * np.power(10.0, (k[:, None] * tilt_db[None, :]) / 10.0)
        fw = dsp.freq_weight(ctx.n_bins, ctx.bin_hz, 300.0, 2500.0, 0.3)
        add = self.BREATH * p["harmonic_cut"] * k[:, None] * fw[None, :]
        state.ap[sl] = state.ap[sl] + (1.0 - state.ap[sl]) * add


# --------------------------------------------------------------------------
# 7. 発音の崩れ
# --------------------------------------------------------------------------

# 「あ」のフォルマント(Hz, 帯域幅, 強さ)
A_FORMANTS = ((800.0, 130.0, 1.0), (1250.0, 150.0, 0.6), (2650.0, 220.0, 0.25))


def a_pattern(n_bins: int, bin_hz: float) -> np.ndarray:
    """「あ」の包絡の形(対数パワー、200〜4000Hz の平均が 0)。"""
    f = np.arange(n_bins) * bin_hz
    pw_ = np.full(n_bins, 0.01)
    for fc, bw, a in A_FORMANTS:
        pw_ += a / (1.0 + ((f - fc) / bw) ** 2)
    lp = np.log(pw_)
    band = (f >= 200) & (f <= 4000)
    return lp - lp[band].mean()


def spectral_tilt(logsp: np.ndarray, n_coef: int = 4) -> np.ndarray:
    """対数包絡のおおまかな傾き(DCT の低次だけ残す)。"""
    c = sfft.dct(logsp, axis=1, norm="ortho")
    c[:, n_coef:] = 0.0
    return sfft.idct(c, axis=1, norm="ortho")


class ArticulationBlur(Effect):
    """スペクトル包絡を時間方向に平滑化して、子音の切れ目をぼかす。"""

    name = "articulation_blur"
    label = "発音の崩れ"
    label_en = "Blurred articulation"
    phase = 2
    description = "包絡を時間方向にならして子音の切れ目をぼかす(無声の子音にもかける。無音にはかけない)"
    description_en = 'Smooths the envelope over time to blur consonant boundaries (also on unvoiced consonants, not on silence)'
    params = (
        ParamSpec("smooth_ms", "平滑化の幅", 50.0, 30.0, 120.0, 1.0, "ms", label_en='Smoothing width'),
        ParamSpec("toward_a", "「あ」に寄せる量", 0.0, 0.0, 0.8, 0.01, label_en='Pull toward "ah"'),
    )

    def apply(self, state: SegmentState, region: dict, ctx: RenderContext) -> None:
        p = region["params"]
        i0, i1, w = self.envelope(region, ctx, voiced_only=False)
        if i1 <= i0:
            return
        active = ctx.active_weight if ctx.active_weight is not None else (~ctx.silent).astype(float)
        w = w * active[i0:i1]
        n = max(3, int(round(p["smooth_ms"] / ctx.frame_period)) | 1)
        half = n // 2
        # 平滑化には区間の前後 half フレームも使う(再合成範囲の中に収まる分だけ)
        a = max(i0 - half, state.offset)
        b = min(i1 + half, state.offset + state.n)
        sl_all = state.local(a, b)
        logsp = np.log(np.maximum(state.sp[sl_all], 1e-20))
        # 無音のフレームは平均に入れない(語頭・語尾の音量が引きずられないように)
        aw = np.maximum(active[a:b], 1e-3)
        win = np.hanning(n + 2)[1:-1]
        num = convolve1d(logsp * aw[:, None], win, axis=0, mode="nearest")
        den = convolve1d(aw, win, mode="nearest")
        smooth = num / den[:, None]
        r0, r1 = i0 - a, i1 - a
        cur = logsp[r0:r1]
        out = cur + w[:, None] * (smooth[r0:r1] - cur)
        if p["toward_a"] > 0:
            target = spectral_tilt(out) + a_pattern(ctx.n_bins, ctx.bin_hz)[None, :]
            k = (p["toward_a"] * w)[:, None]
            out = out + k * (target - out)
        state.sp[state.local(i0, i1)] = np.exp(out)


# --------------------------------------------------------------------------
# 8. しゃくり上げ(無音に吸気音を重ねる)
# --------------------------------------------------------------------------

def synth_inhale(rng: np.random.Generator, fs: int, dur: float, rise: float) -> np.ndarray:
    """帯域を絞ったノイズで、中心周波数が上がっていく吸気音(RMS 1)。"""
    n = max(16, int(dur * fs))
    nper = 1024 if fs <= 48000 else 2048
    noise = rng.standard_normal(n + nper)
    f, t, Z = signal.stft(noise, fs, nperseg=nper, noverlap=nper * 3 // 4)
    u = np.clip(t / dur, 0.0, 1.0)
    fc = 900.0 * np.power(2.0, (0.3 + 1.2 * rise) * u)  # 900Hz から最大で約 1.5 オクターブ上がる
    ff = np.maximum(f, 1.0)[:, None]
    band = np.exp(-0.5 * (np.log2(ff / fc[None, :]) / 0.35) ** 2)
    broad = 0.04 * np.exp(-0.5 * (np.log2(ff / 2000.0) / 0.9) ** 2)  # 息らしいざらつき(控えめ)
    _, y = signal.istft(Z * (band + broad), fs, nperseg=nper, noverlap=nper * 3 // 4)
    y = y[:n]
    uu = np.arange(n) / n
    peak = 0.85
    env = np.where(uu < peak, (uu / peak) ** 1.3, dsp.raised_cosine((1.0 - uu) / (1.0 - peak)))
    y = y * env
    r = np.sqrt(np.mean(y ** 2))
    return y / r if r > 0 else y


def fade_edges(x: np.ndarray, fs: int, fade_in_ms: float = 5.0, fade_out_ms: float = 15.0) -> np.ndarray:
    x = x.copy()
    a = min(len(x) // 2, int(fs * fade_in_ms / 1000))
    b = min(len(x) // 2, int(fs * fade_out_ms / 1000))
    if a > 0:
        x[:a] *= dsp.raised_cosine(np.arange(a) / a)
    if b > 0:
        x[len(x) - b:] *= dsp.raised_cosine(np.arange(b, 0, -1) / b)
    return x


class SobInhale(Effect):
    """無音区間に吸気音を重ねる。時間はずらさない(区間の終わりに吸い終わるように置く)。"""

    name = "sob_inhale"
    label = "しゃくり上げ"
    label_en = "Sob inhale"
    phase = 2
    inserts_in_silence = True
    uses_ramp = False
    description = "無音の部分に吸気音を重ねる。区間の終わりで吸い終わる。素材は materials/inhale の WAV か合成音"
    description_en = 'Adds an inhale in a silent part, ending at the region end. Uses a WAV from materials/inhale or a synthesized sound'
    params = (
        ParamSpec("sample", "音源", "", kind="choice", choices_source="inhale", label_en='Source'),
        ParamSpec("level_db", "音量(声に対して)", -14.0, -30.0, 0.0, 0.5, "dB", label_en='Level (vs. voice)'),
        ParamSpec("duration_ms", "長さ(合成音)", 280.0, 150.0, 600.0, 10.0, "ms", label_en='Duration (synth)'),
        ParamSpec("rise", "音程の上がり方(合成音)", 0.7, 0.0, 1.0, 0.01, label_en='Pitch rise (synth)'),
    )

    def apply(self, state, region, ctx) -> None:
        return None

    def sound(self, region: dict, ctx: RenderContext) -> tuple[np.ndarray, bool]:
        """(RMS 1 の音, 素材を使ったか)。"""
        p = region["params"]
        length = float(region["end"]) - float(region["start"])
        x = materials.load("inhale", p.get("sample", ""), ctx.fs)
        if x is not None and len(x) > 0:
            n = min(len(x), int(length * ctx.fs))
            x = fade_edges(x[:n], ctx.fs)
            r = np.sqrt(np.mean(x ** 2))
            return (x / r if r > 0 else x), True
        dur = min(p["duration_ms"] / 1000.0, length)
        return synth_inhale(self.rng(region, ctx), ctx.fs, dur, p["rise"]), False

    def insert(self, region: dict, ctx: RenderContext):
        s = effective_strength(region, ctx)
        if s <= 0:
            return None
        y, _ = self.sound(region, ctx)
        y = y * ctx.ref_rms * (10.0 ** (region["params"]["level_db"] / 20.0)) * s
        end = int(round(float(region["end"]) * ctx.fs))
        start = max(0, end - len(y))
        return start, y[len(y) - (end - start):]
