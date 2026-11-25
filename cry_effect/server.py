"""ローカル Web アプリのサーバー(FastAPI)。

画面は web/ 以下の静的ファイル。音声の再生と A/B 切り替えはブラウザ側(Web Audio)で行い、
区間を編集するとサーバーが変わった範囲だけを再処理して、その範囲のサンプルを返す。
"""

from __future__ import annotations

import io
import os
import threading
import time
import traceback
import uuid

import numpy as np
import soundfile as sf
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, effects, i18n, materials, paths
from .analysis import analyze
from .audio_io import default_output_path, load_wav, save_wav
from .live import LiveRenderer
from .i18n import L, Msg
from .project import load_project, normalize_project, save_project

WEB_DIR = paths.web_dir()


def sibling_project_path(wav_path: str) -> str:
    return os.path.splitext(wav_path)[0] + ".cry.json"


class Session:
    def __init__(self):
        self.lock = threading.RLock()
        self.gen = 0
        self.audio = None
        self.analysis = None
        self.live: LiveRenderer | None = None
        self.project: dict = {"global_cry_amount": 60.0, "seed": 0, "regions": []}
        self.project_path: str | None = None
        self.status = {"state": "empty", "progress": 0.0,
                       "message": Msg("WAV を開いてください", "Open a WAV file")}
        self.messages: list = []

    # ---- 開く ------------------------------------------------------------
    def open(self, path: str) -> dict:
        path = os.path.abspath(path)
        if not os.path.exists(path):
            raise HTTPException(404, L(f"ファイルが見つかりません: {path}", f"File not found: {path}"))
        msgs: list = []
        project, project_path = None, None
        if path.lower().endswith(".json"):
            project = load_project(path, warn=msgs.append)
            project_path = path
            wav = project.get("source_path")
            if not wav or not os.path.exists(wav):
                raise HTTPException(400, L(f"プロジェクトの source が見つかりません: {wav}",
                                           f"The project's source WAV was not found: {wav}"))
        else:
            wav = path
            sib = sibling_project_path(wav)
            if os.path.exists(sib):
                project = load_project(sib, warn=msgs.append)
                project_path = sib
                msgs.append(Msg(f"同じ名前のプロジェクトを読み込みました: {os.path.basename(sib)}",
                                f"Loaded the project with the same name: {os.path.basename(sib)}"))

        audio = load_wav(wav)
        with self.lock:
            self.gen += 1
            gen = self.gen
            self.audio = audio
            self.analysis = None
            self.live = None
            self.project = project or {"global_cry_amount": 60.0, "seed": 0, "regions": []}
            # 区間ごとの乱数の種を id で固定する(画面で動かしても揺れの形が変わらないように)
            for r in self.project["regions"]:
                if not r.get("id"):
                    r["id"] = "r" + uuid.uuid4().hex[:8]
            self.project["source"] = os.path.basename(wav)
            self.project["source_path"] = wav
            self.project_path = project_path
            self.messages = msgs
            self.status = {"state": "analyzing", "progress": 0.0, "message": Msg("分析中…", "Analyzing…")}
        threading.Thread(target=self._analyze, args=(gen,), daemon=True).start()
        return {"path": wav, "project_path": project_path, "messages": [str(m) for m in msgs]}

    def _analyze(self, gen: int) -> None:
        def prog(p):
            with self.lock:
                if self.gen == gen:
                    self.status = {"state": "analyzing", "progress": p,
                                   "message": Msg(f"分析中… {int(p * 100)}%", f"Analyzing… {int(p * 100)}%")}

        try:
            audio = self.audio
            t = time.time()
            mono = audio.mono()
            a = analyze(mono, audio.fs, progress=prog)
            live = LiveRenderer(audio.data, a, mono)
            with self.lock:
                if self.gen != gen:
                    return
                self.analysis = a
                self.live = live
                self.status = {"state": "rendering", "progress": 1.0, "message": Msg("処理中…", "Processing…")}
            live.update(self.project)
            with self.lock:
                if self.gen != gen:
                    return
                if a.meta.get("from_cache"):
                    done = Msg("分析完了(キャッシュ)", "Analysis done (cached)")
                else:
                    sec = time.time() - t
                    done = Msg(f"分析完了({sec:.1f} 秒)", f"Analysis done ({sec:.1f} s)")
                self.status = {"state": "ready", "progress": 1.0, "message": done}
        except Exception as e:  # pragma: no cover
            traceback.print_exc()
            with self.lock:
                if self.gen == gen:
                    self.status = {"state": "error", "progress": 0.0,
                                   "message": Msg(f"分析に失敗しました: {e}", f"Analysis failed: {e}")}

    # ---- 情報 ------------------------------------------------------------
    def require_ready(self):
        if self.live is None or self.analysis is None:
            raise HTTPException(409, L("まだ準備ができていません", "Not ready yet"))

    def info(self) -> dict:
        self.require_ready()
        a = self.analysis
        audio = self.audio
        return {
            "path": audio.path,
            "name": os.path.basename(audio.path),
            "fs": audio.fs,
            "channels": audio.n_channels,
            "n_samples": audio.n_samples,
            "duration": audio.duration,
            "frame_period": a.frame_period,
            "f0": np.round(a.f0, 2).tolist(),
            # F0 推定は音の消え際の無音まで有声とすることがあるので、無音でない部分だけを有声として見せる
            "voiced": [[round(s, 4), round(e, 4)] for s, e in a.segments(a.voiced & ~a.silent)],
            "silent": [[round(s, 4), round(e, 4)] for s, e in a.segments(a.silent)],
            "silence_threshold_db": a.silence_threshold_db,
            "project": self.project_for_client(),
            "project_path": self.project_path,
            "messages": [str(m) for m in self.messages],
            "version": self.live.version,
        }

    def project_for_client(self) -> dict:
        return {k: v for k, v in self.project.items() if k != "source_path"}

    # ---- 更新 ------------------------------------------------------------
    def update_project(self, data: dict) -> dict:
        self.require_ready()
        msgs: list = []
        base = os.path.dirname(self.audio.path)
        p = normalize_project(dict(data, source=self.audio.path), base, warn=msgs.append)
        p["source"] = os.path.basename(self.audio.path)
        p["source_path"] = self.audio.path
        t = time.time()
        res = self.live.update(p)
        with self.lock:
            self.project = p
        res["elapsed"] = round(time.time() - t, 3)
        overlap = _overlap_warnings(p, self.analysis)
        msgs = [str(m) for m in msgs]
        res["warnings"] = ([m for m in msgs if "実装されていない" not in m and "not implemented" not in m]
                           + [w["text"] for w in overlap]
                           + list(dict.fromkeys(str(n) for n in res.get("notes", []))))
        res["notes"] = [str(n) for n in res.get("notes", [])]
        res["warn_ids"] = [w["id"] for w in overlap if w.get("id")]
        res["project"] = self.project_for_client()
        return res

    def save(self, path: str | None) -> str:
        self.require_ready()
        path = path or self.project_path or sibling_project_path(self.audio.path)
        path = os.path.abspath(path)
        if os.path.normcase(path) == os.path.normcase(self.audio.path):
            raise HTTPException(400, L("元の WAV にプロジェクトを上書きすることはできません",
                                       "Cannot overwrite the source WAV with a project"))
        data = self.project_for_client()
        try:
            data["source"] = os.path.relpath(self.audio.path, os.path.dirname(path))
        except ValueError:  # 別ドライブ
            data["source"] = self.audio.path
        save_project(path, data)
        self.project_path = path
        return path

    def export(self, path: str | None) -> str:
        self.require_ready()
        path = os.path.abspath(path or default_output_path(self.audio.path))
        with self.live.lock:
            out = self.live.out.copy()
        save_wav(path, out, self.audio.fs, protect=[self.audio.path])
        return path


def _overlap_warnings(project: dict, analysis) -> list[str]:
    """無音に挿入する処理が有声区間に重なっていたら警告する(フェーズ2 以降で使う)。"""
    out = []
    t = analysis.times
    for r in project.get("regions", []):
        eff = effects.get(r["type"])
        if eff is None or not eff.inserts_in_silence or "start" not in r:
            continue
        m = (t >= r["start"]) & (t <= r["end"])
        # 音符の端で 30ms 以内だけ重なるのは許す(F0 推定の端の揺れ)
        sounding = analysis.voiced[m] & ~analysis.silent[m]
        if np.sum(sounding) * analysis.frame_period / 1000.0 > 0.03:
            out.append({"id": r.get("id"),
                        "text": L(f"{eff.label}({r['start']:.2f}〜{r['end']:.2f} 秒)が有声区間に重なっています",
                                  f"{eff.label_en} ({r['start']:.2f}–{r['end']:.2f} s) overlaps a voiced part")})
    return out


# --------------------------------------------------------------------------
# ファイル選択ダイアログ(サーバーは同じ PC で動くので tkinter のダイアログを出す)
# --------------------------------------------------------------------------

_dialog_lock = threading.Lock()


def ask_path(mode: str, kind: str, initial: str | None = None) -> str | None:
    import tkinter as tk
    from tkinter import filedialog

    all_ = (L("すべて", "All files"), "*.*")
    types = {
        "wav": [("WAV", "*.wav"), all_],
        "project": [(L("泣き声化プロジェクト", "Cry Effect project"), "*.json"), all_],
        "open": [(L("WAV / プロジェクト", "WAV / project"), "*.wav *.json"), all_],
    }.get(kind, [all_])
    title = {
        ("open", "open"): L("WAV またはプロジェクトを開く", "Open a WAV or project"),
        ("save", "project"): L("プロジェクトを保存", "Save project"),
        ("save", "wav"): L("処理済み WAV を書き出す", "Export processed WAV"),
    }.get((mode, kind), "")
    init_dir = init_file = None
    if initial:
        init_dir, init_file = os.path.split(initial)
    with _dialog_lock:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        try:
            if mode == "open":
                p = filedialog.askopenfilename(parent=root, filetypes=types, initialdir=init_dir, title=title)
            else:
                ext = ".json" if kind == "project" else ".wav"
                p = filedialog.asksaveasfilename(parent=root, filetypes=types, initialdir=init_dir,
                                                 initialfile=init_file, defaultextension=ext, title=title)
        finally:
            root.destroy()
    return os.path.normpath(p) if p else None


# --------------------------------------------------------------------------
# アプリ
# --------------------------------------------------------------------------

class Lifetime:
    """画面(ブラウザのタブ / アプリのウィンドウ)が閉じられたら、サーバーを止めるための見張り。

    画面は定期的に /api/ping を送り、閉じるときに /api/bye を送る。bye の後 grace 秒以内に
    ping が来なければ(再読み込みではなく本当に閉じた)、または ping が timeout 秒途絶えたら on_idle を呼ぶ。
    """

    def __init__(self, on_idle, grace: float = 6.0, timeout: float = 300.0):
        self.on_idle = on_idle
        self.grace, self.timeout = grace, timeout
        self.last = None
        self.bye_at = None
        self._lock = threading.Lock()
        self._fired = False
        threading.Thread(target=self._watch, daemon=True).start()

    def ping(self):
        with self._lock:
            if self.last is None:
                print("window connected", flush=True)
            self.last = time.time()
            self.bye_at = None

    def bye(self):
        with self._lock:
            self.bye_at = time.time()

    def _watch(self):
        while not self._fired:
            time.sleep(1.0)
            now = time.time()
            with self._lock:
                gone = self.bye_at is not None and now - self.bye_at > self.grace
                stale = self.last is not None and now - self.last > self.timeout
            if gone or stale:
                self._fired = True
                self.on_idle()


def create_app(initial_path: str | None = None, session: Session | None = None,
               lifetime: Lifetime | None = None) -> FastAPI:
    app = FastAPI(title="Cry Effect")
    s = session or Session()
    app.state.session = s
    app.state.lifetime = lifetime

    if initial_path:
        s.open(initial_path)

    @app.get("/")
    def index():
        return FileResponse(os.path.join(WEB_DIR, "index.html"), headers={"Cache-Control": "no-store"})

    @app.get("/favicon.ico")
    def favicon():
        return FileResponse(os.path.join(WEB_DIR, "icon.png"), media_type="image/png")

    @app.get("/api/effects")
    def get_effects():
        return effects.specs_json()

    @app.get("/api/settings")
    def get_settings():
        return {"lang": i18n.get_lang(), "langs": list(i18n.LANGS), "version": __version__,
                "materials_dir": paths.materials_dir()}

    @app.post("/api/settings")
    def post_settings(body: dict = Body(...)):
        if "lang" in body:
            if body["lang"] not in i18n.LANGS:
                raise HTTPException(400, f"unknown language: {body['lang']!r}")
            i18n.set_lang(body["lang"])
        return get_settings()

    @app.post("/api/ping")
    def post_ping():
        if lifetime:
            lifetime.ping()
        return {"ok": True}

    @app.post("/api/bye")
    def post_bye():
        if lifetime:
            lifetime.bye()
        return {"ok": True}

    @app.get("/api/materials/{kind}")
    def get_materials(kind: str):
        if not kind.isidentifier():
            raise HTTPException(400, L("不正な種類です", "Invalid kind"))
        return {"dir": materials.folder(kind), "files": materials.list_files(kind)}

    @app.get("/api/status")
    def get_status():
        with s.lock:
            return dict(s.status, message=str(s.status["message"]), gen=s.gen)

    @app.post("/api/open")
    def post_open(body: dict = Body(...)):
        return s.open(body["path"])

    @app.get("/api/session")
    def get_session():
        return s.info()

    @app.get("/api/audio/original")
    def audio_original():
        s.require_ready()
        return FileResponse(s.audio.path, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.get("/api/audio/processed")
    def audio_processed():
        s.require_ready()
        with s.live.lock:
            out = s.live.out.astype(np.float32)
            ver = s.live.version
        buf = io.BytesIO()
        sf.write(buf, out, s.audio.fs, subtype="FLOAT", format="WAV")
        return Response(buf.getvalue(), media_type="audio/wav",
                        headers={"Cache-Control": "no-store", "X-Version": str(ver)})

    @app.post("/api/project")
    def post_project(body: dict = Body(...)):
        return s.update_project(body)

    @app.get("/api/patch")
    def get_patch(s0: int, s1: int):
        s.require_ready()
        arr = s.live.patch(s0, s1)
        return Response(arr.tobytes(), media_type="application/octet-stream",
                        headers={"X-Channels": str(arr.shape[0]), "X-Frames": str(arr.shape[1])})

    @app.post("/api/save")
    def post_save(body: dict = Body(default={})):
        return {"path": s.save(body.get("path"))}

    @app.post("/api/export")
    def post_export(body: dict = Body(default={})):
        try:
            return {"path": s.export(body.get("path"))}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/dialog")
    def post_dialog(body: dict = Body(...)):
        initial = body.get("initial")
        if not initial and s.audio is not None:
            if body.get("kind") == "project":
                initial = s.project_path or sibling_project_path(s.audio.path)
            elif body.get("kind") == "wav" and body.get("mode") == "save":
                initial = default_output_path(s.audio.path)
            else:
                initial = s.audio.path
        try:
            return {"path": ask_path(body.get("mode", "open"), body.get("kind", "open"), initial)}
        except Exception as e:  # tkinter が使えない環境
            return JSONResponse({"path": None, "error": str(e)}, status_code=200)

    @app.middleware("http")
    async def no_cache_static(request, call_next):
        resp = await call_next(request)
        if request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"  # 更新した画面がすぐ反映されるように
        return resp

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
