"""テスト用の合成ボーカル(倍音 + フォルマント + 音符ごとの音量包絡 + 無音)。"""

from __future__ import annotations

import numpy as np

# (開始秒, 終了秒, 開始 Hz, 終了 Hz)
NOTES = [
    (0.30, 1.60, 220.0, 220.0),
    (1.90, 3.40, 261.6, 293.7),
    (3.70, 4.80, 246.9, 246.9),
]
DURATION = 5.2
FORMANTS = [(700.0, 110.0, 1.0), (1220.0, 120.0, 0.5), (2600.0, 160.0, 0.25)]


def _formant_gain(f: np.ndarray) -> np.ndarray:
    g = np.zeros_like(f)
    for fc, bw, amp in FORMANTS:
        g += amp / (1.0 + ((f - fc) / bw) ** 2)
    return g + 0.02


def synth_vocal(fs: int = 44100, peak: float = 0.5, channels: int = 1, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(DURATION * fs)
    t = np.arange(n) / fs
    y = np.zeros(n)
    for s, e, f_a, f_b in NOTES:
        m = (t >= s) & (t < e)
        tt = t[m] - s
        L = e - s
        f0 = f_a + (f_b - f_a) * np.clip(tt / L, 0, 1) + 2.0 * np.sin(2 * np.pi * 5.5 * tt) * np.clip(tt / 0.4, 0, 1)
        phase = 2 * np.pi * np.cumsum(f0) / fs
        env = np.clip(tt / 0.04, 0, 1) * np.clip((L - tt) / 0.08, 0, 1)
        x = np.zeros(m.sum())
        for h in range(1, 40):
            fh = h * f0
            ok = fh < min(6000.0, fs / 2 - 500)
            x += ok * _formant_gain(fh) * np.sin(h * phase) / h ** 0.3
        y[m] += x * env
    y += 1e-4 * rng.standard_normal(n)  # わずかなノイズ床
    y *= peak / np.max(np.abs(y))
    if channels == 1:
        return y[:, None]
    # ステレオ: 左右で少し音量差をつける
    return np.stack([y * 1.0, y * 0.8], axis=1)
