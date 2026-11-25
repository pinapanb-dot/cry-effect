"""表示言語(日本語 / 英語)と、配布版の起動まわりのテスト。"""

import json
import os
import re
import time

import pytest

from cry_effect import effects, i18n, paths
from cry_effect.i18n import Msg
from cry_effect.project import normalize_project
from cry_effect.render import render

JP = re.compile(r"[぀-ヿ一-鿿]")


def test_every_effect_has_english_text():
    for e in effects.REGISTRY.values():
        assert e.label_en and not JP.search(e.label_en), e.name
        assert e.description_en and not JP.search(e.description_en), e.name
        for p in e.params:
            assert p.label_en and not JP.search(p.label_en), (e.name, p.name)
            assert not JP.search(p.unit_en or p.unit), (e.name, p.name)


def test_specs_follow_language():
    i18n.set_lang("en", persist=False)
    en = {e["name"]: e for e in effects.specs_json()}
    assert en["tremor"]["label"] == "Irregular tremor"
    assert en["voice_break"]["params"][0]["unit"] == "st"
    assert not any(JP.search(json.dumps(e, ensure_ascii=False)) for e in en.values())
    i18n.set_lang("ja", persist=False)
    ja = {e["name"]: e for e in effects.specs_json()}
    assert ja["tremor"]["label"] == "不規則な震え"
    assert ja["voice_break"]["params"][0]["unit"] == "半音"


def test_messages_and_notes_follow_language(vocal44):
    data, a = vocal44
    msgs = []
    i18n.set_lang("en", persist=False)
    normalize_project({"regions": [{"type": "tremor", "start": 1, "end": 2, "params": {"depth_cents": 999}},
                                   {"type": "nope", "start": 1, "end": 2}]}, warn=msgs.append)
    assert msgs and not any(JP.search(m) for m in msgs)
    # 描画の注意書きは表示するときの言語になる
    proj = normalize_project({"regions": [{"type": "glottal_catch", "time": 1.7, "params": {}}]}, warn=lambda m: None)
    notes = render(data, a, proj).notes
    assert notes and isinstance(notes[0], Msg)
    assert "unvoiced" in str(notes[0])
    i18n.set_lang("ja", persist=False)
    assert "無声" in str(notes[0])


def test_settings_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("CRY_DATA_DIR", str(tmp_path))
    i18n.set_lang("en")
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["lang"] == "en"
    monkeypatch.setattr(i18n, "_lang", None)
    assert i18n.get_lang() == "en"  # 保存した言語を読む
    with pytest.raises(ValueError):
        i18n.set_lang("fr")


def test_system_lang_env(monkeypatch):
    monkeypatch.setenv("CRY_LANG", "en")
    assert i18n.system_lang() == "en"
    monkeypatch.setenv("CRY_LANG", "ja")
    assert i18n.system_lang() == "ja"


def test_settings_api_switches_language(client):
    from test_server import _wait_ready
    c = client
    st = c.get("/api/settings").json()
    assert st["lang"] == "ja" and set(st["langs"]) == {"ja", "en"} and st["version"]
    assert c.post("/api/settings", json={"lang": "fr"}).status_code == 400
    assert c.post("/api/settings", json={"lang": "en"}).json()["lang"] == "en"
    assert c.get("/api/status").json()["message"] == "Open a WAV file"
    assert c.get("/api/effects").json()[0]["label"] in {e.label_en for e in effects.REGISTRY.values()}
    c.post("/api/open", json={"path": str(c.wav)})
    _wait_ready(c)
    res = c.post("/api/project", json={"regions": [
        {"id": "bad", "type": "sfx", "start": 2.0, "end": 2.3, "params": {}}]}).json()
    assert any("overlaps a voiced part" in w for w in res["warnings"])
    assert c.get("/api/status").json()["message"].startswith("Analysis done")
    c.post("/api/settings", json={"lang": "ja"})
    assert c.get("/api/status").json()["message"].startswith("分析完了")  # 状態の文も切り替わる
    assert c.get("/favicon.ico").status_code == 200


def test_lifetime_stops_after_bye_but_not_on_reload():
    from cry_effect.server import Lifetime
    fired = []
    lt = Lifetime(lambda: fired.append(1), grace=0.5, timeout=60)
    lt.ping()
    lt.bye()
    lt.ping()  # 再読み込み: すぐ次の画面がつながる
    time.sleep(1.8)
    assert not fired
    lt.bye()  # 本当に閉じた
    time.sleep(2.2)
    assert fired == [1]


def test_lifetime_timeout():
    from cry_effect.server import Lifetime
    fired = []
    lt = Lifetime(lambda: fired.append(1), grace=10, timeout=0.5)
    lt.ping()
    time.sleep(2.2)
    assert fired == [1]


def test_paths_dev_mode(monkeypatch, tmp_path):
    monkeypatch.delenv("CRY_MATERIALS_DIR", raising=False)
    monkeypatch.delenv("CRY_CACHE_DIR", raising=False)
    assert not paths.frozen()
    assert os.path.isfile(os.path.join(paths.web_dir(), "index.html"))
    assert paths.materials_dir() == os.path.join(paths.app_dir(), "materials")
    monkeypatch.setenv("CRY_DATA_DIR", str(tmp_path))
    assert paths.log_dir().startswith(str(tmp_path))


def test_web_dictionaries_have_same_keys():
    src = open(os.path.join(paths.web_dir(), "i18n.js"), encoding="utf-8").read()
    ja = src[src.index("ja: {"):src.index("en: {")]
    en = src[src.index("en: {"):src.index("export const LANGS")]
    keys = lambda block: set(re.findall(r"^\s*'([\w.]+)':", block, re.M))
    assert keys(ja) == keys(en) and len(keys(ja)) > 50
    assert not JP.search(en)
    # app.js / index.html で使っているキーはすべて辞書にある
    used = set()
    for f in ("app.js", "index.html"):
        s = open(os.path.join(paths.web_dir(), f), encoding="utf-8").read()
        used |= set(re.findall(r"\bt\('([\w.]+)'", s))
        used |= set(re.findall(r'data-i18n(?:-title|-placeholder)?="([\w.]+)"', s))
    assert used and used <= keys(ja), used - keys(ja)
