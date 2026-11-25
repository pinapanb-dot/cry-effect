"""Cry Effect(泣き声化ツール)画面版の起動。

    python app.py                 # サーバーを起動して、アプリのウィンドウ(なければブラウザ)を開く
    python app.py vocal.wav       # WAV(またはプロジェクト JSON)を開いた状態で起動
    python app.py --no-browser    # サーバーだけ起動する(開発用。ウィンドウを閉じても終わらない)

ウィンドウは Microsoft Edge(なければ Google Chrome)のアプリモードで開く。どちらもなければ
既定のブラウザで開く。ウィンドウ(タブ)を閉じるとサーバーも終わる。
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser


def _free_port(start: int) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
            try:
                sk.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free port found / 空いているポートが見つかりません")


def _setup_logging() -> str | None:
    """コンソールがない(配布版)ときは出力をログファイルへ送る。"""
    from cry_effect import paths

    if sys.stdout is not None and sys.stderr is not None and not paths.frozen():
        return None
    d = paths.log_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "app.log")
    try:
        if os.path.getsize(path) > 2_000_000:
            os.replace(path, path + ".old")
    except OSError:
        pass
    f = open(path, "a", encoding="utf-8", buffering=1)
    f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    if sys.stdout is None or paths.frozen():
        sys.stdout = f
    if sys.stderr is None or paths.frozen():
        sys.stderr = f
    return path


def _show_error(text: str) -> None:
    """コンソールのない配布版でも起動時のエラーが分かるように、ダイアログで知らせる。"""
    print(text, file=sys.stderr, flush=True)
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Cry Effect", text)
        root.destroy()
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# アプリのウィンドウ(Edge / Chrome のアプリモード)
# --------------------------------------------------------------------------

def _registry_app_path(exe: str) -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key) as k:
                p, _ = winreg.QueryValueEx(k, None)
                if p and os.path.isfile(p):
                    return p
        except OSError:
            continue
    return None


def find_app_browser() -> str | None:
    for exe in ("msedge.exe", "chrome.exe"):
        p = _registry_app_path(exe)
        if p:
            return p
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            p = os.path.join(base, rel)
            if os.path.isfile(p):
                return p
    return None


def open_window(url: str, mode: str) -> subprocess.Popen | None:
    """mode: "app"(アプリのウィンドウ。なければ既定のブラウザ)または "browser"。"""
    from cry_effect import paths

    if mode == "app":
        exe = find_app_browser()
        if exe:
            profile = os.path.join(paths.user_data_dir(), "window")
            args = [exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
                    "--no-default-browser-check", "--disable-features=Translate", "--window-size=1440,900"]
            try:
                return subprocess.Popen(args, close_fds=True)
            except OSError as e:
                print(f"app window failed: {e}", file=sys.stderr)
    webbrowser.open(url)
    return None


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cry Effect (泣き声化ツール)")
    ap.add_argument("path", nargs="?", help="WAV or project JSON to open / 開く WAV またはプロジェクト JSON")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="start the server only / サーバーだけ起動する")
    ap.add_argument("--browser", action="store_true",
                    help="open in the default browser instead of an app window / 既定のブラウザで開く")
    args = ap.parse_args(argv)

    log_path = _setup_logging()

    try:
        import uvicorn

        from cry_effect.server import Lifetime, create_app

        port = _free_port(args.port)
        url = f"http://127.0.0.1:{port}/"
        holder: dict = {}

        def stop():
            srv = holder.get("server")
            if srv is not None:
                srv.should_exit = True

        lifetime = None if args.no_browser else Lifetime(stop)
        app = create_app(args.path, lifetime=lifetime)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        holder["server"] = server
    except Exception as e:  # noqa: BLE001
        _show_error(f"起動できませんでした / Could not start:\n{e}\n\n{traceback.format_exc()}")
        return 1

    print(f"Cry Effect: {url}" + (f"  (log: {log_path})" if log_path else "  (Ctrl+C to quit)"), flush=True)

    if not args.no_browser:
        def launch():
            while not server.started and not server.should_exit:
                time.sleep(0.05)
            if server.should_exit:
                return
            t0 = time.time()
            proc = open_window(url, "browser" if args.browser else "app")
            # 一度も画面がつながらないまま 2 分たったら終わる(ウィンドウが開けなかった場合)
            if proc is not None:
                proc.wait()
                # すぐ終わった場合は、既に開いていた Edge にウィンドウを渡しただけなので見張りに任せる
                if time.time() - t0 > 5.0:
                    stop()

        def first_ping_guard():
            time.sleep(120.0)
            if lifetime is not None and lifetime.last is None:
                print("no window connected within 120 s; exiting", file=sys.stderr)
                stop()

        threading.Thread(target=launch, daemon=True).start()
        threading.Thread(target=first_ping_guard, daemon=True).start()

    try:
        server.run()
    except Exception as e:  # noqa: BLE001
        _show_error(f"サーバーが止まりました / The server stopped:\n{e}")
        return 1
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()  # 配布版で分析の並列処理(子プロセス)を動かすのに必要
    sys.exit(main())
