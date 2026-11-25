"""WAV の読み書き。"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass

import numpy as np
import soundfile as sf

from .i18n import L

SUPPORTED_RATES = (44100, 48000)


@dataclass
class Audio:
    data: np.ndarray  # (n_samples, n_channels) float64
    fs: int
    path: str | None = None

    @property
    def n_samples(self) -> int:
        return self.data.shape[0]

    @property
    def n_channels(self) -> int:
        return self.data.shape[1]

    @property
    def duration(self) -> float:
        return self.n_samples / self.fs

    def mono(self) -> np.ndarray:
        if self.n_channels == 1:
            return self.data[:, 0].copy()
        return self.data.mean(axis=1)


def load_wav(path: str) -> Audio:
    data, fs = sf.read(path, dtype="float64", always_2d=True)
    if fs not in SUPPORTED_RATES:
        warnings.warn(L(f"サンプルレート {fs} Hz は想定外です(44100 / 48000 を推奨)",
                        f"Unexpected sample rate {fs} Hz (44100 / 48000 recommended)"))
    if data.shape[1] > 2:
        warnings.warn(L(f"{data.shape[1]} チャンネルの入力です。分析は全チャンネルの平均で行います",
                        f"{data.shape[1]}-channel input; analysis uses the average of all channels"))
    return Audio(data=data, fs=int(fs), path=os.path.abspath(path))


def _same_file(a: str, b: str) -> bool:
    if os.path.exists(a) and os.path.exists(b):
        try:
            return os.path.samefile(a, b)
        except OSError:
            pass
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def save_wav(path: str, data: np.ndarray, fs: int, protect: list[str] | None = None) -> None:
    """32bit float で書き出す。protect に含まれるファイル(元音源)は絶対に上書きしない。"""
    for p in protect or []:
        if p and _same_file(path, p):
            raise ValueError(L(f"元ファイルを上書きしようとしました: {path}",
                               f"Refusing to overwrite the source file: {path}"))
    if not np.all(np.isfinite(data)):
        raise ValueError(L("出力に NaN / Inf が含まれています", "The output contains NaN / Inf"))
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    sf.write(path, data.astype(np.float32), fs, subtype="FLOAT")


def default_output_path(source: str) -> str:
    base, _ = os.path.splitext(source)
    return base + "_cry.wav"
