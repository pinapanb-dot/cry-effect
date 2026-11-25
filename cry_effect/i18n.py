"""表示言語(日本語 / 英語)と設定ファイル。

使い方: メッセージは L("日本語", "English") で書く。今の言語の方が返る。
描画結果に残して後で表示する注意書きは Msg(...) にしておくと、表示するときの言語になる。
"""

from __future__ import annotations

import json
import locale
import os
import sys
import threading

from . import paths

LANGS = ("ja", "en")
_lang: str | None = None
_lock = threading.Lock()


# --------------------------------------------------------------------------
# 設定ファイル(%LOCALAPPDATA%\CryEffect\settings.json)
# --------------------------------------------------------------------------

def settings_path() -> str:
    return os.path.join(paths.user_data_dir(), "settings.json")


def load_settings() -> dict:
    try:
        with open(settings_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(**updates) -> dict:
    with _lock:
        data = load_settings()
        data.update(updates)
        path = settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return data


# --------------------------------------------------------------------------
# 言語
# --------------------------------------------------------------------------

def system_lang() -> str:
    """OS の表示言語が日本語なら ja、それ以外は en。"""
    env = os.environ.get("CRY_LANG", "").lower()
    if env in LANGS:
        return env
    if sys.platform == "win32":
        try:
            import ctypes
            lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "ja" if (lid & 0x3FF) == 0x11 else "en"
        except Exception:  # noqa: BLE001
            pass
    try:
        code = (locale.getlocale()[0] or "").lower()
    except ValueError:
        code = ""
    return "ja" if code.startswith("ja") else "en"


def get_lang() -> str:
    global _lang
    if _lang is None:
        saved = load_settings().get("lang")
        _lang = saved if saved in LANGS else system_lang()
    return _lang


def set_lang(lang: str, persist: bool = True) -> str:
    global _lang
    if lang not in LANGS:
        raise ValueError(lang)
    _lang = lang
    if persist:
        save_settings(lang=lang)
    return lang


def L(ja: str, en: str) -> str:
    return ja if get_lang() == "ja" else en


class Msg:
    """表示するときに言語を決めるメッセージ。str() で今の言語の文になる。"""

    __slots__ = ("ja", "en")

    def __init__(self, ja: str, en: str):
        self.ja, self.en = ja, en

    def __str__(self) -> str:
        return L(self.ja, self.en)

    def __repr__(self) -> str:
        return f"Msg({self.ja!r}, {self.en!r})"

    def __eq__(self, other) -> bool:
        return isinstance(other, Msg) and (self.ja, self.en) == (other.ja, other.en)

    def __hash__(self) -> int:
        return hash((self.ja, self.en))

    def __contains__(self, item: str) -> bool:
        return item in str(self)
