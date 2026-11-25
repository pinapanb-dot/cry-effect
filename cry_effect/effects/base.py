"""処理(エフェクト)の共通部分。

各処理は WORLD のフレーム領域(f0 / sp / ap と、フレームごとの音量 gain_db)を書き換える。
描画(render)側が、処理する区間ぶんのフレームを SegmentState に切り出して渡す。
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

import numpy as np

from .. import dsp
from ..i18n import L

DEFAULT_RAMP_MS = 50.0
RAMP_MIN_MS = 10.0
RAMP_MAX_MS = 300.0


@dataclass(frozen=True)
class ParamSpec:
    name: str
    label: str
    default: float | str
    min: float = 0.0
    max: float = 1.0  # 安全上限。これを超える値は受け付けない(読み込み時に丸める)
    step: float = 0.01
    unit: str = ""
    kind: str = "float"  # "float" または "choice"(文字列。選択肢は choices_source から)
    choices_source: str = ""  # 例: "inhale" → 素材フォルダ materials/inhale の WAV 一覧
    label_en: str = ""  # 英語表示(空なら label)
    unit_en: str = ""  # 英語表示の単位(空なら unit)

    def clamp(self, v):
        if self.kind == "choice":
            return v if isinstance(v, str) else self.default
        try:
            v = float(v)
        except (TypeError, ValueError):
            return self.default
        if not np.isfinite(v):
            return self.default
        return float(min(self.max, max(self.min, v)))

    def to_json(self) -> dict:
        return {
            "name": self.name, "label": L(self.label, self.label_en or self.label), "default": self.default,
            "min": self.min, "max": self.max, "step": self.step, "unit": L(self.unit, self.unit_en or self.unit),
            "kind": self.kind, "choices_source": self.choices_source,
        }


@dataclass
class RenderContext:
    fs: int
    frame_period: float  # ms
    n_frames: int
    n_bins: int
    fft_size: int
    voiced_weight: np.ndarray  # (T,) 有声区間を平滑化した重み 0..1
    silent: np.ndarray  # (T,) bool
    global_amount: float = 1.0  # 0..1(泣き度)
    seed: int = 0
    active_weight: np.ndarray | None = None  # (T,) 無音でない部分を平滑化した重み 0..1
    ref_rms: float = 0.1  # 有声部分の代表的な RMS(挿入する音の音量の基準)

    @property
    def fs_ctrl(self) -> float:
        """制御信号(フレーム)のサンプルレート。5ms なら 200Hz。"""
        return 1000.0 / self.frame_period

    @property
    def bin_hz(self) -> float:
        return self.fs / self.fft_size

    def frame_range(self, t0: float, t1: float) -> tuple[int, int]:
        fp = self.frame_period / 1000.0
        i0 = max(0, int(np.floor(t0 / fp)))
        i1 = min(self.n_frames, int(np.ceil(t1 / fp)) + 1)
        return i0, max(i0, i1)

    def times(self, i0: int, i1: int) -> np.ndarray:
        return np.arange(i0, i1) * self.frame_period / 1000.0


@dataclass
class SegmentState:
    """再合成する区間ぶんのフレーム(offset はファイル先頭からのフレーム番号)。"""
    offset: int
    f0: np.ndarray
    sp: np.ndarray
    ap: np.ndarray
    gain_db: np.ndarray
    notes: list = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.f0)

    def local(self, i0: int, i1: int) -> slice:
        a, b = i0 - self.offset, i1 - self.offset
        if a < 0 or b > self.n:
            raise IndexError(f"frames {i0}-{i1} are outside the resynthesis range {self.offset}-{self.offset + self.n}")
        return slice(a, b)


class Effect:
    name: str = ""
    label: str = ""
    label_en: str = ""
    kind: str = "region"  # "region"(区間)または "marker"(点)
    phase: int = 1
    params: tuple[ParamSpec, ...] = ()
    description: str = ""
    description_en: str = ""
    # 区間の終わりで効果が最大になり、区間の後ろで解放する処理は True
    release_after_end: bool = False
    # 無音区間に音を足す処理(有声区間だけにかける制限を受けない)。
    # True の処理は apply ではなく insert で、足す音(モノラル)を返す。
    inserts_in_silence: bool = False
    # 音量だけを変える処理(再合成しない)。True の処理は apply ではなく gain_db_at で、
    # 元の音(または再合成した音)にサンプル単位でかける音量変化を返す。
    gain_only: bool = False
    # 区間の端の立ち上げ/戻し(ramp_ms)を使うか。独自の形を持つ処理は False(画面に欄を出さない)
    uses_ramp: bool = True

    # ---- パラメータ -------------------------------------------------------
    def param_spec(self, name: str) -> ParamSpec | None:
        for p in self.params:
            if p.name == name:
                return p
        return None

    def normalize_params(self, params: dict | None, warn=None) -> dict:
        params = dict(params or {})
        if not self.implemented:
            return params  # 未実装の処理はそのまま保持する(保存時に失わない)
        out = {}
        for p in self.params:
            raw = params.pop(p.name, p.default)
            v = p.clamp(raw)
            if warn and raw is not None and p.kind == "choice":
                if raw != v:
                    warn(L(f"{self.name}.{p.name} = {raw!r} は文字列ではないので既定値にしました",
                           f"{self.name}.{p.name} = {raw!r} is not a string; using the default"))
            elif warn and raw is not None:
                try:
                    if abs(float(raw) - v) > 1e-9:
                        warn(L(f"{self.name}.{p.name} = {raw} は範囲 {p.min}〜{p.max} の外なので {v} にしました",
                               f"{self.name}.{p.name} = {raw} is outside {p.min}–{p.max}; set to {v}"))
                except (TypeError, ValueError):
                    warn(L(f"{self.name}.{p.name} = {raw!r} は数値ではないので既定値 {v} にしました",
                           f"{self.name}.{p.name} = {raw!r} is not a number; using the default {v}"))
            out[p.name] = v
        if warn:
            for k in params:
                warn(L(f"{self.name}: 未知のパラメータ {k} は無視します",
                       f"{self.name}: unknown parameter {k} ignored"))
        return out

    def to_json(self) -> dict:
        return {
            "name": self.name, "label": L(self.label, self.label_en or self.label),
            "kind": self.kind, "phase": self.phase,
            "description": L(self.description, self.description_en or self.description), "params": [p.to_json() for p in self.params],
            "implemented": self.implemented,
            "inserts_in_silence": self.inserts_in_silence, "gain_only": self.gain_only,
            "uses_ramp": self.uses_ramp,
        }

    implemented: bool = True

    # ---- 範囲 -------------------------------------------------------------
    def extent(self, region: dict) -> tuple[float, float]:
        """この処理が音を変える時間範囲(秒)。"""
        ramp = region_ramp_s(region)
        if self.kind == "marker":
            t = float(region["time"])
            return t - ramp, t + ramp
        s, e = float(region["start"]), float(region["end"])
        if self.release_after_end:
            e += ramp
        return s, e

    def envelope(self, region: dict, ctx: RenderContext, voiced_only: bool = True):
        """(i0, i1, w)。w は端の立ち上げ/戻し × 強さ × 泣き度(× 有声の重み)。"""
        s, e = self.extent(region)
        i0, i1 = ctx.frame_range(s, e)
        t = ctx.times(i0, i1)
        ramp = region_ramp_s(region)
        if self.release_after_end:
            w = dsp.region_weight(t, float(region["start"]), float(region["end"]), ramp, release_s=ramp)
        else:
            w = dsp.region_weight(t, s, e, ramp)
        w = w * effective_strength(region, ctx)
        if voiced_only:
            w = w * ctx.voiced_weight[i0:i1]
        return i0, i1, w

    def rng(self, region: dict, ctx: RenderContext) -> np.random.Generator:
        return np.random.default_rng(region_seed(region, ctx.seed))

    # ---- 本体 -------------------------------------------------------------
    def apply(self, state: SegmentState, region: dict, ctx: RenderContext) -> None:
        raise NotImplementedError

    def insert(self, region: dict, ctx: RenderContext) -> tuple[int, np.ndarray] | None:
        """(先頭サンプル番号, 足す音)。inserts_in_silence の処理だけが実装する。"""
        return None

    def gain_db_at(self, region: dict, ctx: RenderContext, t: np.ndarray) -> np.ndarray:
        """時刻 t(秒)での音量変化(dB)。gain_only の処理だけが実装する。"""
        return np.zeros_like(t)

    def check(self, region: dict, ctx: RenderContext) -> list[str]:
        """再合成しない処理の注意書き(例: 無声の部分に置いた)。"""
        return []


def region_ramp_s(region: dict) -> float:
    try:
        ms = float(region.get("ramp_ms", DEFAULT_RAMP_MS))
    except (TypeError, ValueError):
        ms = DEFAULT_RAMP_MS
    return min(RAMP_MAX_MS, max(RAMP_MIN_MS, ms)) / 1000.0


def effective_strength(region: dict, ctx: RenderContext) -> float:
    s = float(region.get("strength", 100.0))
    return float(np.clip(s, 0.0, 100.0)) / 100.0 * float(np.clip(ctx.global_amount, 0.0, 1.0))


def region_seed(region: dict, base_seed: int = 0) -> int:
    """区間ごとの乱数の種。id があれば id から、なければ種類と開始時刻から決める。"""
    if region.get("seed") is not None:
        key = f"seed:{region['seed']}"
    elif region.get("id"):
        key = f"id:{region['id']}"
    else:
        t = region.get("start", region.get("time", 0.0))
        key = f"{region.get('type')}:{float(t):.3f}"
    return zlib.crc32(f"{base_seed}|{key}".encode())
