"""WORLD 分析(harvest / cheaptrick / d4c)とキャッシュ。

- フレーム周期 5ms。フレーム i の時刻は i * frame_period / 1000 秒。
- 長い音源は区間に分けて並列に分析する(境界は前後 1 秒の余白で吸収)。
- 結果は音声内容のハッシュをキーにして .npy で保存し、開き直しても再計算しない。
  sp は対数 float16、ap は float16 で保存し、読み込み時は memmap で開く。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from . import paths
from .i18n import L

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import pyworld as pw

ANALYSIS_VERSION = 2
FRAME_PERIOD_MS = 5.0
F0_FLOOR = 65.0  # harvest の下限
FFT_F0_FLOOR = 71.0  # cheaptrick の FFT 長の基準(WORLD 既定。44.1k/48k とも 2048 点)
F0_CEIL = 1100.0

CHUNK_SECONDS = 10.0
PAD_SECONDS = 1.0

DEFAULT_CACHE_DIR = paths.default_cache_dir()


def hop_samples(fs: int, frame_period: float) -> float:
    return fs * frame_period / 1000.0


def frame_alignment(fs: int, frame_period: float) -> int:
    """フレーム境界がちょうど整数サンプルになる最小のフレーム数(44.1kHz なら 2)。"""
    hop = hop_samples(fs, frame_period)
    for k in range(1, 2001):
        v = k * hop
        if abs(v - round(v)) < 1e-9:
            return k
    raise ValueError(f"cannot align frames for fs={fs}, frame_period={frame_period}")


def n_frames_for(n_samples: int, fs: int, frame_period: float) -> int:
    # pyworld の harvest と同じ式
    return int(1000.0 * n_samples / fs / frame_period) + 1


@dataclass
class Analysis:
    fs: int
    frame_period: float
    n_samples: int
    f0: np.ndarray  # (T,)
    sp: "LogSpView"  # (T, B) 切り出すとパワー包絡(float64)になる
    ap: np.ndarray  # (T, B) float16(memmap のことがある)
    rms_db: np.ndarray  # (T,)
    silence_threshold_db: float
    fft_size: int
    key: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def n_frames(self) -> int:
        return len(self.f0)

    @property
    def n_bins(self) -> int:
        return self.sp.shape[1]

    @property
    def times(self) -> np.ndarray:
        return np.arange(self.n_frames) * self.frame_period / 1000.0

    @property
    def voiced(self) -> np.ndarray:
        return self.f0 > 0

    @property
    def silent(self) -> np.ndarray:
        return self.rms_db < self.silence_threshold_db

    def segments(self, mask: np.ndarray) -> list[tuple[float, float]]:
        """真のフレームが続く範囲を (開始秒, 終了秒) のリストにする。"""
        fp = self.frame_period / 1000.0
        m = np.concatenate([[False], mask.astype(bool), [False]])
        d = np.diff(m.astype(np.int8))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        return [(s * fp, e * fp) for s, e in zip(starts, ends)]


class LogSpView:
    """対数パワー包絡(float16)を保持し、切り出した時にパワー包絡(float64)に戻す。

    3 分の音源でも 150MB 程度に収まる(float16 の対数で 0.1dB 程度の量子化)。
    """

    def __init__(self, log: np.ndarray):
        self.log = log

    @property
    def shape(self):
        return self.log.shape

    def __len__(self):
        return self.log.shape[0]

    def __getitem__(self, idx):
        return np.exp(np.asarray(self.log[idx], dtype=np.float64))

    def __array__(self, dtype=None, copy=None):
        a = self[:]
        return a.astype(dtype) if dtype is not None else a


# --------------------------------------------------------------------------
# 分析本体
# --------------------------------------------------------------------------

def _analyze_chunk(args):
    x, fs, fp, fft_size, core_off, core_n = args
    x = np.ascontiguousarray(x, dtype=np.float64)
    f0, t = pw.harvest(x, fs, f0_floor=F0_FLOOR, f0_ceil=F0_CEIL, frame_period=fp)
    sp = pw.cheaptrick(x, f0, t, fs, fft_size=fft_size)
    ap = pw.d4c(x, f0, t, fs, fft_size=fft_size)
    sl = slice(core_off, core_off + core_n)
    logsp = np.log(np.maximum(sp[sl], 1e-30)).astype(np.float16)
    return f0[sl].copy(), logsp, ap[sl].astype(np.float16)


def frame_rms_db(x: np.ndarray, fs: int, frame_period: float, n_frames: int, win_ms: float = 20.0) -> np.ndarray:
    win = max(1, int(round(fs * win_ms / 1000.0)))
    csum = np.concatenate([[0.0], np.cumsum(x.astype(np.float64) ** 2)])
    centers = np.round(np.arange(n_frames) * hop_samples(fs, frame_period)).astype(np.int64)
    lo = np.clip(centers - win // 2, 0, len(x))
    hi = np.clip(centers + win // 2 + 1, 0, len(x))
    energy = (csum[hi] - csum[lo]) / np.maximum(hi - lo, 1)
    return 10.0 * np.log10(np.maximum(energy, 1e-12))


def silence_threshold(rms_db: np.ndarray) -> float:
    """最大 RMS から 40dB 下(ただし -60dBFS より下にはしない)。"""
    top = float(np.percentile(rms_db, 99)) if len(rms_db) else -120.0
    return max(top - 40.0, -60.0)


def _compute(x: np.ndarray, fs: int, fp: float, workers: int | None, progress=None):
    n = len(x)
    T = n_frames_for(n, fs, fp)
    hop = hop_samples(fs, fp)
    k = frame_alignment(fs, fp)
    fft_size = pw.get_cheaptrick_fft_size(fs, FFT_F0_FLOOR)

    chunk_frames = max(k, int(round(CHUNK_SECONDS * 1000 / fp / k)) * k)
    pad_frames = max(k, int(round(PAD_SECONDS * 1000 / fp / k)) * k)

    jobs = []
    for a in range(0, T, chunk_frames):
        b = min(T, a + chunk_frames)
        sa = max(0, a - pad_frames)
        s0 = int(round(sa * hop))
        s1 = min(n, int(round((b + pad_frames) * hop)) + 1)
        jobs.append((x[s0:s1], fs, fp, fft_size, a - sa, b - a))

    if workers is None:
        workers = max(1, min(len(jobs), (os.cpu_count() or 2) - 1, 8))

    results = []
    if workers <= 1 or len(jobs) == 1:
        for i, j in enumerate(jobs):
            results.append(_analyze_chunk(j))
            if progress:
                progress((i + 1) / len(jobs))
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for i, r in enumerate(ex.map(_analyze_chunk, jobs)):
                results.append(r)
                if progress:
                    progress((i + 1) / len(jobs))

    f0 = np.concatenate([r[0] for r in results])
    sp = np.concatenate([r[1] for r in results])
    ap = np.concatenate([r[2] for r in results])
    assert len(f0) == T, (len(f0), T)
    return f0, sp, ap, fft_size


def audio_key(x: np.ndarray, fs: int, fp: float) -> str:
    h = hashlib.sha1()
    h.update(f"v{ANALYSIS_VERSION}|fs{fs}|fp{fp}|f0{F0_FLOOR}-{F0_CEIL}|fft{FFT_F0_FLOOR}|".encode())
    h.update(np.ascontiguousarray(x, dtype=np.float32).tobytes())
    return h.hexdigest()[:20]


def analyze(
    x: np.ndarray,
    fs: int,
    frame_period: float = FRAME_PERIOD_MS,
    use_cache: bool = True,
    cache_dir: str | None = None,
    workers: int | None = None,
    progress=None,
    mmap: bool = True,
) -> Analysis:
    """モノラル信号 x を WORLD で分析する(キャッシュがあればそれを返す)。"""
    x = np.asarray(x, dtype=np.float64)
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    key = audio_key(x, fs, frame_period)
    d = os.path.join(cache_dir, key)

    if use_cache:
        a = _load_cache(d, mmap=mmap)
        if a is not None:
            a.meta["from_cache"] = True
            return a

    t0 = time.time()
    f0, sp, ap, fft_size = _compute(x, fs, frame_period, workers, progress)
    rms = frame_rms_db(x, fs, frame_period, len(f0))
    thr = silence_threshold(rms)
    meta = {
        "version": ANALYSIS_VERSION,
        "fs": fs,
        "frame_period": frame_period,
        "n_samples": int(len(x)),
        "fft_size": int(fft_size),
        "silence_threshold_db": thr,
        "elapsed_sec": round(time.time() - t0, 2),
    }
    a = Analysis(fs, frame_period, len(x), f0, LogSpView(sp), ap, rms, thr, fft_size, key, dict(meta, from_cache=False))

    if use_cache:
        _save_cache(d, a, meta)
        if mmap:
            loaded = _load_cache(d, mmap=True)
            if loaded is not None:
                loaded.meta["from_cache"] = False
                return loaded
    return a


def _save_cache(d: str, a: Analysis, meta: dict) -> None:
    tmp = d + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    np.save(os.path.join(tmp, "f0.npy"), a.f0.astype(np.float64))
    np.save(os.path.join(tmp, "logsp.npy"), a.sp.log)
    np.save(os.path.join(tmp, "ap.npy"), np.asarray(a.ap, dtype=np.float16))
    np.save(os.path.join(tmp, "rms_db.npy"), a.rms_db.astype(np.float32))
    with open(os.path.join(tmp, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    shutil.rmtree(d, ignore_errors=True)
    os.replace(tmp, d)


def _load_cache(d: str, mmap: bool = True) -> Analysis | None:
    mp = os.path.join(d, "meta.json")
    if not os.path.exists(mp):
        return None
    try:
        with open(mp, encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("version") != ANALYSIS_VERSION:
            return None
        mode = "r" if mmap else None
        f0 = np.load(os.path.join(d, "f0.npy"))
        sp = LogSpView(np.load(os.path.join(d, "logsp.npy"), mmap_mode=mode))
        ap = np.load(os.path.join(d, "ap.npy"), mmap_mode=mode)
        rms = np.load(os.path.join(d, "rms_db.npy")).astype(np.float64)
    except Exception as e:  # 壊れたキャッシュは作り直す
        warnings.warn(L(f"分析キャッシュを読めませんでした({e})。再分析します",
                        f"Could not read the analysis cache ({e}); analyzing again"))
        return None
    return Analysis(
        meta["fs"], meta["frame_period"], meta["n_samples"], f0, sp, ap, rms,
        meta["silence_threshold_db"], meta["fft_size"], os.path.basename(d), meta,
    )
