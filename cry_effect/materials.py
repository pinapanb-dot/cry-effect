"""素材 WAV(吸気音など)の一覧と読み込み。

素材は materials/<種類>/ に置く(例: materials/inhale/inhale1.wav)。
置き場所は環境変数 CRY_MATERIALS_DIR で変更できる。
"""

from __future__ import annotations

import os
import threading

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from . import paths

_cache: dict = {}
_lock = threading.Lock()


def root() -> str:
    return paths.materials_dir()


def folder(kind: str) -> str:
    return os.path.join(root(), kind)


def list_files(kind: str) -> list[str]:
    d = folder(kind)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.lower().endswith(".wav"))


def load(kind: str, name: str, fs: int) -> np.ndarray | None:
    """素材をモノラル・fs Hz で返す。見つからなければ None。"""
    if not name or os.path.basename(name) != name:  # フォルダの外は読まない
        return None
    path = os.path.join(folder(kind), name)
    if not os.path.isfile(path):
        return None
    key = (path, os.path.getmtime(path), fs)
    with _lock:
        if key in _cache:
            return _cache[key]
    x, sr = sf.read(path, dtype="float64", always_2d=True)
    x = x.mean(axis=1)
    if sr != fs:
        g = np.gcd(int(sr), int(fs))
        x = resample_poly(x, fs // g, sr // g)
    with _lock:
        _cache[key] = x
    return x
