"""画面版のサーバーと差分レンダリングのテスト。"""

import copy
import json
import time

import numpy as np
import pytest
import soundfile as sf

from cry_effect.live import LiveRenderer
from cry_effect.project import normalize_project
from cry_effect.render import render


def _norm(p):
    return normalize_project(p, warn=lambda m: None)


def test_incremental_render_matches_full_render(vocal44, all_phase1_project):
    """追加・移動・パラメータ変更・削除・泣き度の変更を重ねても、一括処理と同じ結果になる。"""
    data, a = vocal44
    live = LiveRenderer(data, a)
    proj = copy.deepcopy(all_phase1_project)
    for i, r in enumerate(proj["regions"]):
        r["id"] = f"r{i}"

    steps = []
    p = copy.deepcopy(proj)
    p["regions"] = p["regions"][:2]
    steps.append(p)                                   # 2 つだけ
    steps.append(copy.deepcopy(proj))                 # 全部
    p = copy.deepcopy(proj)
    p["regions"][0]["start"] += 0.1                   # 移動
    p["regions"][0]["end"] += 0.1
    steps.append(p)
    p = copy.deepcopy(p)
    p["regions"][2]["params"]["warp"] = 0.9           # パラメータ変更
    steps.append(p)
    p = copy.deepcopy(p)
    del p["regions"][1]                               # 削除
    steps.append(p)
    p = copy.deepcopy(p)
    p["global_cry_amount"] = 40                       # 泣き度
    steps.append(p)
    p = copy.deepcopy(p)
    p["regions"][0]["enabled"] = False                # 無効化
    steps.append(p)

    for st in steps:
        res = live.update(_norm(st))
        full = render(data, a, _norm(st)).data
        assert np.array_equal(live.out, full), res

    # 同じ内容をもう一度送っても何も処理しない
    res = live.update(_norm(steps[-1]))
    assert res["rendered"] == 0 and res["changed"] == []


def test_incremental_only_touches_changed_span(vocal44, all_phase1_project):
    data, a = vocal44
    live = LiveRenderer(data, a)
    proj = copy.deepcopy(all_phase1_project)
    for i, r in enumerate(proj["regions"]):
        r["id"] = f"r{i}"
    live.update(_norm(proj))
    proj["regions"][0]["strength"] = 50  # tremor だけ変える
    res = live.update(_norm(proj))
    assert res["rendered"] == 1
    (s0, s1), = res["changed"]
    fs = a.fs
    assert s0 <= int(proj["regions"][0]["start"] * fs) and s1 >= int(proj["regions"][0]["end"] * fs)
    assert (s1 - s0) / fs < 1.5


def test_patch_layout(vocal48_stereo):
    data, a = vocal48_stereo
    live = LiveRenderer(data, a)
    p = live.patch(100, 300)
    assert p.shape == (2, 200) and p.dtype == np.float32
    assert np.allclose(p[1], data[100:300, 1].astype(np.float32))


# --------------------------------------------------------------------------
# HTTP API
# --------------------------------------------------------------------------

def _wait_ready(c, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        st = c.get("/api/status").json()
        if st["state"] == "ready":
            return st
        assert st["state"] != "error", st
        time.sleep(0.1)
    raise AssertionError("timeout")


@pytest.fixture
def planned_effect(monkeypatch):
    """未実装の処理(仕様に載っているが処理しないもの)を 1 つ登録する。"""
    from cry_effect import effects as fx
    eff = fx._Planned("future_fx", "将来の処理", "marker", 9)
    monkeypatch.setitem(fx.REGISTRY, eff.name, eff)
    monkeypatch.setattr(fx, "ORDER", fx.ORDER + [eff.name])
    return eff


def test_api_open_edit_save_export(client, tmp_path, planned_effect):
    c = client
    before = c.wav.read_bytes()
    assert c.get("/api/status").json()["state"] == "empty"
    assert c.post("/api/open", json={"path": str(c.wav)}).status_code == 200
    _wait_ready(c)

    info = c.get("/api/session").json()
    assert info["channels"] == 2 and info["fs"] == 44100
    assert len(info["f0"]) > 100 and info["voiced"] and info["silent"]

    effects = {e["name"]: e for e in c.get("/api/effects").json()}
    assert effects["tremor"]["implemented"] and not effects["future_fx"]["implemented"]

    proj = {"global_cry_amount": 80, "regions": [
        {"id": "a1", "type": "tremor", "start": 0.5, "end": 1.4, "strength": 70, "params": {"depth_cents": 999}},
        {"id": "a2", "type": "future_fx", "time": 2.5, "strength": 80, "params": {"ms": 40}},
    ]}
    res = c.post("/api/project", json=proj).json()
    assert res["rendered"] == 1 and len(res["changed"]) == 1
    assert res["project"]["regions"][0]["params"]["depth_cents"] == 120.0  # 上限で丸める
    s0, s1 = res["changed"][0]
    r = c.get(f"/api/patch?s0={s0}&s1={s1}")
    assert int(r.headers["X-Channels"]) == 2 and int(r.headers["X-Frames"]) == s1 - s0
    assert len(r.content) == 4 * 2 * (s1 - s0)

    # 処理済み音声は元と同じ長さ
    r = c.get("/api/audio/processed")
    y, fs = sf.read(__import__("io").BytesIO(r.content), always_2d=True)
    assert y.shape == (info["n_samples"], 2)

    # 保存: 既定は <wav>.cry.json、source は相対パス、未実装の処理も保持
    path = c.post("/api/save", json={}).json()["path"]
    assert path.endswith("vocal.cry.json")
    saved = json.loads(open(path, encoding="utf-8").read())
    assert saved["source"] == "vocal.wav"
    assert [r["type"] for r in saved["regions"]] == ["tremor", "future_fx"]
    assert saved["regions"][1]["params"] == {"ms": 40}  # 未実装の処理はそのまま保持

    # 書き出し: 元ファイルへは拒否、別名は 32bit float
    assert c.post("/api/export", json={"path": str(c.wav)}).status_code == 400
    out = c.post("/api/export", json={}).json()["path"]
    assert out.endswith("vocal_cry.wav")
    assert sf.info(out).subtype == "FLOAT"
    assert c.wav.read_bytes() == before

    # 開き直すと同じ名前のプロジェクトが自動で読まれる
    res = c.post("/api/open", json={"path": str(c.wav)}).json()
    assert res["project_path"] and res["project_path"].endswith("vocal.cry.json")
    _wait_ready(c)
    info = c.get("/api/session").json()
    assert [r["id"] for r in info["project"]["regions"]] == ["a1", "a2"]
    assert info["project"]["global_cry_amount"] == 80


def test_api_open_project_json(client, tmp_path):
    c = client
    pj = tmp_path / "p.json"
    pj.write_text(json.dumps({"source": "vocal.wav", "regions": [
        {"type": "breathiness", "start": 0.5, "end": 1.0, "params": {"amount": 0.4}}]}), encoding="utf-8")
    c.post("/api/open", json={"path": str(pj)})
    _wait_ready(c)
    info = c.get("/api/session").json()
    r = info["project"]["regions"][0]
    assert r["type"] == "breathiness" and r["id"]  # id が振られる
    assert info["project_path"] == str(pj)


def test_api_open_missing_file(client):
    assert client.post("/api/open", json={"path": "Z:/nope/missing.wav"}).status_code == 404
