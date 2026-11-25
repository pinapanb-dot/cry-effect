"""ファイルの置き場所。開発時(ソースから実行)と配布版(PyInstaller で固めた exe)で切り替える。

- 同梱ファイル(web/ など): 開発時はプロジェクト直下、配布版は exe に展開されたフォルダ
- 素材(materials/): exe と同じフォルダ(利用者が WAV を足せるように)
- 設定・キャッシュ・ログ: %LOCALAPPDATA%\\CryEffect
"""

from __future__ import annotations

import os
import sys

APP_NAME = "CryEffect"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """exe(配布版)またはプロジェクト(開発時)のフォルダ。"""
    return os.path.dirname(os.path.abspath(sys.executable)) if frozen() else _ROOT


def resource_dir() -> str:
    """同梱ファイルの置き場所。"""
    return getattr(sys, "_MEIPASS", _ROOT) if frozen() else _ROOT


def web_dir() -> str:
    return os.path.join(resource_dir(), "web")


def user_data_dir() -> str:
    d = os.environ.get("CRY_DATA_DIR")
    if not d:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
        d = os.path.join(base, APP_NAME)
    return d


def default_cache_dir() -> str:
    if os.environ.get("CRY_CACHE_DIR"):
        return os.environ["CRY_CACHE_DIR"]
    if frozen():
        return os.path.join(user_data_dir(), "cache")
    return os.path.join(_ROOT, ".cache", "analysis")


def materials_dir() -> str:
    return os.environ.get("CRY_MATERIALS_DIR") or os.path.join(app_dir(), "materials")


def log_dir() -> str:
    return os.path.join(user_data_dir(), "logs")
