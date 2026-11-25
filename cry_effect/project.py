"""プロジェクト(区間とパラメータ)の JSON 読み書きと検証。"""

from __future__ import annotations

import json
import os
import warnings

from . import effects
from .i18n import L

GLOBAL_DEFAULT = 100.0


def normalize_region(r: dict, warn=None) -> dict | None:
    warn = warn or (lambda m: None)
    typ = r.get("type")
    eff = effects.get(typ)
    if eff is None:
        warn(L(f"未知の処理 {typ!r} は無視します", f"Unknown effect {typ!r} ignored"))
        return None
    if not eff.implemented:
        warn(L(f"{typ}({eff.label})はまだ実装されていないので、処理せずに保持します",
               f"{typ} ({eff.label_en or eff.label}) is not implemented yet; kept without processing"))
    out = {"type": typ}
    for k in ("id", "seed", "enabled", "label"):
        if k in r:
            out[k] = r[k]
    try:
        if eff.kind == "marker":
            if "time" not in r:
                warn(L(f"{typ}: マーカーに time がないので無視します", f"{typ}: marker has no time; ignored"))
                return None
            out["time"] = max(0.0, float(r["time"]))
        else:
            s, e = float(r["start"]), float(r["end"])
            if e < s:
                s, e = e, s
            if e - s < 0.01:
                warn(L(f"{typ}: 区間 {s:.3f}-{e:.3f} が短すぎるので無視します",
                       f"{typ}: region {s:.3f}-{e:.3f} is too short; ignored"))
                return None
            out["start"], out["end"] = max(0.0, s), max(0.0, e)
    except (KeyError, TypeError, ValueError):
        warn(L(f"{typ}: start / end(または time)が読めないので無視します",
               f"{typ}: cannot read start / end (or time); ignored"))
        return None
    try:
        strength = float(r.get("strength", 100.0))
    except (TypeError, ValueError):
        strength = 100.0
    out["strength"] = min(100.0, max(0.0, strength))
    if "ramp_ms" in r:
        out["ramp_ms"] = r["ramp_ms"]
    out["params"] = eff.normalize_params(r.get("params"), warn=lambda m: warn(m))
    return out


def normalize_project(data: dict, base_dir: str | None = None, warn=None) -> dict:
    warn = warn or (lambda m: warnings.warn(m))
    src = data.get("source")
    if src and base_dir and not os.path.isabs(src):
        src_abs = os.path.normpath(os.path.join(base_dir, src))
    else:
        src_abs = src
    try:
        g = float(data.get("global_cry_amount", GLOBAL_DEFAULT))
    except (TypeError, ValueError):
        g = GLOBAL_DEFAULT
    regions = []
    for r in data.get("regions", []):
        n = normalize_region(r, warn)
        if n is not None:
            regions.append(n)
    return {
        "source": src,
        "source_path": src_abs,
        "global_cry_amount": min(100.0, max(0.0, g)),
        "seed": int(data.get("seed", 0) or 0),
        "regions": regions,
    }


def load_project(path: str, warn=None) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return normalize_project(data, os.path.dirname(os.path.abspath(path)), warn)


def save_project(path: str, project: dict) -> None:
    data = {k: v for k, v in project.items() if k != "source_path"}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
