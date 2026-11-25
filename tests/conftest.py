import os
import sys

import numpy as np
import pytest
import soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from signals import synth_vocal  # noqa: E402

from cry_effect.analysis import analyze  # noqa: E402


@pytest.fixture(autouse=True)
def _japanese_and_private_settings(tmp_path_factory, monkeypatch):
    """テストは日本語表示で行い、利用者の設定ファイル(%LOCALAPPDATA%)には触らない。"""
    from cry_effect import i18n
    monkeypatch.setenv("CRY_DATA_DIR", str(tmp_path_factory.mktemp("data")))
    i18n.set_lang("ja", persist=False)
    yield
    i18n.set_lang("ja", persist=False)


@pytest.fixture(scope="session")
def cache_dir(tmp_path_factory):
    return str(tmp_path_factory.mktemp("cache"))


@pytest.fixture(scope="session")
def vocal44(cache_dir):
    data = synth_vocal(44100)
    a = analyze(data.mean(axis=1), 44100, cache_dir=cache_dir, workers=1)
    return data, a


@pytest.fixture(scope="session")
def vocal48_stereo(cache_dir):
    data = synth_vocal(48000, channels=2)
    a = analyze(data.mean(axis=1), 48000, cache_dir=cache_dir, workers=1)
    return data, a


@pytest.fixture(scope="session")
def vocal44_loud(cache_dir):
    data = synth_vocal(44100, peak=0.97)
    a = analyze(data.mean(axis=1), 44100, cache_dir=cache_dir, workers=1)
    return data, a


ALL_PHASE1 = [
    {"type": "tremor", "start": 0.5, "end": 1.4, "strength": 100,
     "params": {"depth_cents": 120, "rate_hz": 10, "irregularity": 1.0}},
    {"type": "breathiness", "start": 2.0, "end": 2.6, "strength": 100, "params": {"amount": 0.9}},
    {"type": "sob_formant", "start": 2.4, "end": 3.3, "strength": 100, "params": {"warp": 0.78}},
    {"type": "phrase_end", "start": 4.0, "end": 4.75, "strength": 100,
     "params": {"onset": 0.3, "breath": 0.95, "level_drop_db": 40, "f0_drop_cents": -250, "rasp": 1.0}},
]


@pytest.fixture
def all_phase1_project():
    import copy
    return {"global_cry_amount": 100, "regions": copy.deepcopy(ALL_PHASE1)}


def rms(x):
    return float(np.sqrt(np.mean(np.asarray(x) ** 2)))


@pytest.fixture
def client(tmp_path, cache_dir, monkeypatch):
    from fastapi.testclient import TestClient

    from cry_effect import analysis as an
    from cry_effect.server import create_app
    from signals import synth_vocal

    monkeypatch.setattr(an, "DEFAULT_CACHE_DIR", cache_dir)
    wav = tmp_path / "vocal.wav"
    sf.write(wav, synth_vocal(44100, channels=2), 44100, subtype="PCM_24")
    app = create_app()
    c = TestClient(app)
    c.wav = wav
    return c
