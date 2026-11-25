"""処理で共通に使う信号処理の部品。"""

from __future__ import annotations

import numpy as np
from scipy import signal


# --------------------------------------------------------------------------
# 立ち上がり・減衰のカーブ
# --------------------------------------------------------------------------

def raised_cosine(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return 0.5 - 0.5 * np.cos(np.pi * x)


def smoothstep(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def region_weight(times: np.ndarray, start: float, end: float, ramp_s: float,
                  release_s: float | None = None) -> np.ndarray:
    """区間の重み 0..1。start から ramp_s で立ち上がり、end の手前 ramp_s で 0 に戻る。

    release_s を指定すると、end までは 1 を保ち、end から release_s かけて 0 に戻る
    (語尾の崩れのように、区間の終わりで効果が最大になる処理用)。
    """
    length = max(end - start, 1e-6)
    if release_s is None:
        r = max(min(ramp_s, length / 2.0), 1e-4)
        return raised_cosine((times - start) / r) * raised_cosine((end - times) / r)
    r = max(min(ramp_s, length), 1e-4)
    up = raised_cosine((times - start) / r)
    rel = max(release_s, 1e-4)
    down = raised_cosine((end + rel - times) / rel)
    return up * down


def smooth_mask(mask: np.ndarray, frame_period_ms: float, width_ms: float = 30.0) -> np.ndarray:
    """0/1 のマスクを窓幅 width_ms のハン窓で平滑化する(切り替わりでのクリック防止)。"""
    n = max(3, int(round(width_ms / frame_period_ms)) | 1)
    w = np.hanning(n + 2)[1:-1]
    w /= w.sum()
    return np.clip(np.convolve(mask.astype(np.float64), w, mode="same"), 0.0, 1.0)


# --------------------------------------------------------------------------
# ランダムな制御信号
# --------------------------------------------------------------------------

def _filtered_noise(rng: np.random.Generator, n: int, sos, pad: int) -> np.ndarray:
    w = rng.standard_normal(n + 2 * pad)
    y = signal.sosfiltfilt(sos, w)
    core = y[pad:pad + n]
    ref = y[pad // 2: len(y) - pad // 2]  # 長めの範囲で正規化(短い区間でも振幅が安定する)
    s = np.std(ref)
    return core / s if s > 0 else core


def bandpass_noise(rng: np.random.Generator, n: int, fs_ctrl: float, lo: float, hi: float,
                   order: int = 2) -> np.ndarray:
    """帯域制限したガウスノイズ(RMS 1)。周期も振幅も自然にばらつく。"""
    nyq = fs_ctrl / 2.0
    lo = max(lo, 0.05)
    hi = min(hi, nyq * 0.9)
    sos = signal.butter(order, [lo, hi], btype="band", fs=fs_ctrl, output="sos")
    pad = int(fs_ctrl * 2.0)
    return _filtered_noise(rng, n, sos, pad)


def lowpass_noise(rng: np.random.Generator, n: int, fs_ctrl: float, cutoff: float,
                  order: int = 2) -> np.ndarray:
    sos = signal.butter(order, min(cutoff, fs_ctrl * 0.45), btype="low", fs=fs_ctrl, output="sos")
    pad = int(fs_ctrl * 2.0)
    return _filtered_noise(rng, n, sos, pad)


# --------------------------------------------------------------------------
# スペクトル包絡の周波数軸伸縮(sob_formant / f2_raise で共用)
# --------------------------------------------------------------------------

def warp_envelope(sp: np.ndarray, src_bins: np.ndarray) -> np.ndarray:
    """出力の各ビンが参照する元のビン位置 src_bins (T, B) で包絡を読み替える。

    対数振幅で線形補間する。src_bins は小数でよく、範囲外は端の値になる。
    """
    sp = np.asarray(sp, dtype=np.float64)
    B = sp.shape[1]
    logsp = np.log(np.maximum(sp, 1e-20))
    s = np.clip(src_bins, 0.0, B - 1.0)
    i0 = np.floor(s).astype(np.int64)
    i1 = np.minimum(i0 + 1, B - 1)
    fr = s - i0
    a = np.take_along_axis(logsp, i0, axis=1)
    b = np.take_along_axis(logsp, i1, axis=1)
    return np.exp(a + (b - a) * fr)


def scale_map(n_bins: int, alpha: np.ndarray) -> np.ndarray:
    """フォルマントを alpha 倍の周波数へ動かす写像(alpha<1 で全体が下がる)。

    出力 f は元の f / alpha を読む。alpha はフレームごとの配列。
    """
    alpha = np.maximum(np.asarray(alpha, dtype=np.float64), 1e-3)
    bins = np.arange(n_bins, dtype=np.float64)
    return bins[None, :] / alpha[:, None]


def piecewise_map(n_bins: int, bin_hz: float, out_hz: np.ndarray, src_hz: np.ndarray) -> np.ndarray:
    """折れ線の写像。out_hz / src_hz は (T, K) の対応点(各行は単調増加)。

    例: 帯域 900〜2500Hz の中だけ上へ伸ばす(F2 の上昇)
        out = [0, 900, 1700*(1+a), 2500, nyq], src = [0, 900, 1700, 2500, nyq]
    """
    out_hz = np.atleast_2d(out_hz)
    src_hz = np.atleast_2d(src_hz)
    freqs = np.arange(n_bins) * bin_hz
    res = np.empty((out_hz.shape[0], n_bins))
    for i in range(out_hz.shape[0]):
        res[i] = np.interp(freqs, out_hz[i], src_hz[i]) / bin_hz
    return res


def band_stretch_map(n_bins: int, bin_hz: float, lo_hz: float, hi_hz: float,
                     amount: np.ndarray, pivot_hz: float | None = None) -> np.ndarray:
    """lo〜hi の帯域だけ包絡を上方向(amount>0)に伸ばす写像。帯域外は動かさない。"""
    amount = np.atleast_1d(np.asarray(amount, dtype=np.float64))
    nyq = bin_hz * (n_bins - 1)
    pivot = pivot_hz if pivot_hz is not None else (lo_hz + hi_hz) / 2.0
    moved = np.clip(pivot * (1.0 + amount), lo_hz + 1.0, hi_hz - 1.0)
    T = len(amount)
    out = np.stack([np.zeros(T), np.full(T, lo_hz), moved, np.full(T, hi_hz), np.full(T, nyq)], axis=1)
    src = np.tile([0.0, lo_hz, pivot, hi_hz, nyq], (T, 1))
    return piecewise_map(n_bins, bin_hz, out, src)


def freq_weight(n_bins: int, bin_hz: float, lo_hz: float, hi_hz: float, floor: float) -> np.ndarray:
    """lo_hz 以下で floor、hi_hz 以上で 1 になる周波数の重み。"""
    f = np.arange(n_bins) * bin_hz
    return floor + (1.0 - floor) * smoothstep((f - lo_hz) / max(hi_hz - lo_hz, 1.0))


# --------------------------------------------------------------------------
# ピーク制限
# --------------------------------------------------------------------------

def limit_peaks(y: np.ndarray, ceiling: float, fs: int, window_ms: float = 10.0) -> np.ndarray:
    """簡易リミッタ。|y| が ceiling を超える所の前後 window_ms だけ滑らかにゲインを下げる。

    最小値フィルタ(幅 2L+1)の後に移動平均(幅 L+1)をかけるので、
    ピーク位置のゲインは必ず必要量以下になる。y は (n,) または (n, ch)。
    """
    from scipy.ndimage import minimum_filter1d, uniform_filter1d

    mag = np.abs(y) if y.ndim == 1 else np.max(np.abs(y), axis=1)
    if mag.size == 0 or mag.max() <= ceiling:
        return y
    need = np.minimum(1.0, ceiling / np.maximum(mag, 1e-12))
    L = max(1, int(fs * window_ms / 1000.0))
    g = minimum_filter1d(need, size=2 * L + 1, mode="nearest")
    g = uniform_filter1d(g, size=L + 1, mode="nearest")
    out = y * (g if y.ndim == 1 else g[:, None])
    return np.clip(out, -ceiling, ceiling)  # 丸め誤差分の最終保証
