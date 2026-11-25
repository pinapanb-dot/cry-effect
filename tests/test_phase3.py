"""フェーズ3 の処理のテスト。"""

import copy

import numpy as np
import pytest
import soundfile as sf
from scipy import signal

from cry_effect import effects
from cry_effect.effects.base import SegmentState
from cry_effect.project import normalize_project
from cry_effect.render import CROSSFADE_S, make_context, reference_rms, render

from test_render import _hf_burst_ratio

# 合成ボーカル: 音符 0.30-1.60 / 1.90-3.40 / 3.70-4.80、その間は無音
PHASE3 = [
    {"id": "g1", "type": "glottal_catch", "time": 0.9, "strength": 100,
     "params": {"duration_ms": 100, "depth_db": 80}},
    {"id": "f1", "type": "f2_raise", "start": 1.95, "end": 2.6, "strength": 100, "params": {"amount": 0.45}},
    {"id": "n1", "type": "nasal", "start": 2.7, "end": 3.35, "strength": 100,
     "params": {"high_cut_db": 18, "low_boost_db": 9}},
    {"id": "s1", "type": "sfx", "start": 3.42, "end": 3.68, "strength": 100, "params": {"level_db": 0}},
]


def _norm(p):
    return normalize_project(p, warn=lambda m: None)


@pytest.fixture
def phase3_project():
    return {"global_cry_amount": 100, "regions": copy.deepcopy(PHASE3)}


def _full_state(a):
    return SegmentState(0, a.f0.copy(), np.array(a.sp[:]), np.array(a.ap[:], dtype=float), np.zeros(a.n_frames))


def _db(x):
    return 10 * np.log10(np.maximum(x, 1e-30))


@pytest.mark.parametrize("fixture", ["vocal44", "vocal48_stereo", "vocal44_loud"])
def test_phase3_basic_properties(request, fixture, phase3_project):
    data, a = request.getfixturevalue(fixture)
    proj = _norm(phase3_project)
    res = render(data, a, proj)
    y = res.data
    assert y.shape == data.shape
    assert np.all(np.isfinite(y))
    assert np.max(np.abs(y)) <= max(0.98, np.max(np.abs(data))) + 1e-9
    fs = a.fs
    inside = np.zeros(len(data), dtype=bool)
    for sp in res.spans:
        inside[int(round(sp.t0 * fs)):int(round(sp.t1 * fs))] = True
    assert np.max(np.abs(y[~inside] - data[~inside])) == 0.0
    for r in proj["regions"]:
        s, e = effects.get(r["type"]).extent(r)
        d = y[int(s * fs):int(e * fs), 0] - data[int(s * fs):int(e * fs), 0]
        assert np.sqrt(np.mean(d ** 2)) > 1e-3, r["type"]


@pytest.mark.parametrize("fixture", ["vocal44", "vocal48_stereo"])
def test_phase3_no_clicks(request, fixture, phase3_project):
    data, a = request.getfixturevalue(fixture)
    proj = _norm(phase3_project)
    res = render(data, a, proj)
    fs = a.fs
    y, x = res.data[:, 0], data[:, 0]
    times = []
    for sp in res.spans:
        times += [sp.t0, sp.t0 + CROSSFADE_S, sp.t1 - CROSSFADE_S, sp.t1]
    for r in proj["regions"]:
        if r["type"] != "sfx":  # 素材の音そのものが高域を含む
            times += list(effects.get(r["type"]).extent(r))
    for t in times:
        if 0.11 < t < len(y) / fs - 0.11:
            ry, rx = _hf_burst_ratio(y, fs, t), _hf_burst_ratio(x, fs, t)
            assert ry < max(4.0, 2.0 * rx), f"t={t:.3f}"
    r = proj["regions"][3]
    for t in (r["start"], r["start"] + 0.22):
        i = int(round(t * fs))
        assert np.max(np.abs(np.diff(y[i - 200:i + 200]))) < 0.02


def test_glottal_catch_drops_level_without_resynthesis(vocal48_stereo):
    data, a = vocal48_stereo
    fs = a.fs
    proj = _norm({"regions": [{"type": "glottal_catch", "time": 0.9, "strength": 100,
                               "params": {"duration_ms": 40, "depth_db": 40}}]})
    res = render(data, a, proj)
    y = res.data
    # 落ちている間は約 -40dB
    seg = slice(int(0.905 * fs), int(0.935 * fs))
    drop = 20 * np.log10(np.sqrt(np.mean(y[seg] ** 2)) / np.sqrt(np.mean(data[seg] ** 2)))
    assert -40.5 < drop < -39.5, drop
    # 再合成しないので、変化させた範囲の外は元の音と完全に同じ(ステレオの両チャンネルとも)
    s, e = effects.get("glottal_catch").extent(proj["regions"][0])
    out = np.ones(len(y), dtype=bool)
    out[int(s * fs):int(np.ceil(e * fs)) + 1] = False
    assert np.array_equal(y[out], data[out])
    # 強さ 50 なら半分の dB
    proj["regions"][0]["strength"] = 50
    y2 = render(data, a, proj).data
    drop2 = 20 * np.log10(np.sqrt(np.mean(y2[seg] ** 2)) / np.sqrt(np.mean(data[seg] ** 2)))
    assert -20.5 < drop2 < -19.5, drop2


def test_glottal_catch_combines_with_resynthesis(vocal44):
    """再合成する処理と重なっても、詰まりは効く。"""
    data, a = vocal44
    fs = a.fs
    base = {"type": "tremor", "start": 0.5, "end": 1.4, "strength": 100, "params": {}}
    catch = {"type": "glottal_catch", "time": 0.9, "strength": 100, "params": {"duration_ms": 40, "depth_db": 40}}
    y1 = render(data, a, _norm({"regions": [base]})).data[:, 0]
    y2 = render(data, a, _norm({"regions": [base, catch]})).data[:, 0]
    seg = slice(int(0.905 * fs), int(0.935 * fs))
    drop = 20 * np.log10(np.sqrt(np.mean(y2[seg] ** 2)) / np.sqrt(np.mean(y1[seg] ** 2)))
    assert -40.5 < drop < -39.5, drop
    far = slice(int(0.6 * fs), int(0.8 * fs))
    assert np.allclose(y1[far], y2[far])


def test_glottal_catch_on_silence_reports_note(vocal44):
    data, a = vocal44
    res = render(data, a, _norm({"regions": [{"type": "glottal_catch", "time": 1.7, "params": {}}]}))
    assert any("無声" in n for n in res.notes)


def test_f2_raise_moves_only_the_band(vocal44):
    _, a = vocal44
    proj = _norm({"regions": [{"type": "f2_raise", "start": 2.0, "end": 3.3, "strength": 100,
                               "params": {"amount": 0.3}}]})
    ctx = make_context(a, proj)
    st = _full_state(a)
    effects.get("f2_raise").apply(st, proj["regions"][0], ctx)
    f = np.arange(a.n_bins) * a.fs / a.fft_size
    i = np.argmin(np.abs(a.times - 2.6))
    before, after = _db(np.asarray(a.sp[i])), _db(st.sp[i])
    # 帯域の外は変わらない
    outside = (f < 880) | (f > 2520)
    assert np.allclose(before[outside], after[outside], atol=1e-6)
    # 帯域の中のエネルギーの重心が上がる
    band = (f > 900) & (f < 2500)
    cen = lambda d: np.sum(f[band] * 10 ** (d[band] / 10)) / np.sum(10 ** (d[band] / 10))
    assert cen(after) > cen(before) + 50
    # 1700Hz の値は 1700 / 1.3 Hz 付近にあった値になる
    j = np.argmin(np.abs(f - 1700 * 1.3))
    k = np.argmin(np.abs(f - 1700))
    assert abs(after[j] - before[k]) < 1.0
    # 区間の外のフレームは変わらない
    o = np.argmin(np.abs(a.times - 1.0))
    assert np.allclose(st.sp[o], np.asarray(a.sp[o]))


def test_nasal_curve_and_amounts(vocal44):
    _, a = vocal44
    proj = _norm({"regions": [{"type": "nasal", "start": 2.0, "end": 3.3, "strength": 100,
                               "params": {"high_cut_db": 8, "low_boost_db": 4}}]})
    ctx = make_context(a, proj)
    st = _full_state(a)
    effects.get("nasal").apply(st, proj["regions"][0], ctx)
    f = np.arange(a.n_bins) * a.fs / a.fft_size
    i = np.argmin(np.abs(a.times - 2.6))
    diff = _db(st.sp[i]) - _db(np.asarray(a.sp[i]))
    at = lambda hz: diff[np.argmin(np.abs(f - hz))]
    assert abs(at(400) - 4.0) < 0.1  # 300〜500Hz を持ち上げる
    assert at(300) > 2.0 and at(500) > 2.0
    assert abs(at(5000) + 8.0) < 0.1  # 高域を下げる
    assert abs(at(100)) < 0.5 and abs(at(1000)) < 0.6  # その間はほぼ変えない
    # 量を 0 にすれば何も変わらない
    proj["regions"][0]["params"] = {"high_cut_db": 0, "low_boost_db": 0}
    st0 = _full_state(a)
    effects.get("nasal").apply(st0, proj["regions"][0], ctx)
    assert np.allclose(st0.sp, np.asarray(a.sp[:]))


def test_sfx_synth_placement_and_level(vocal44):
    data, a = vocal44
    fs = a.fs
    proj = _norm({"regions": [{"type": "sfx", "start": 1.62, "end": 1.88, "strength": 100,
                               "params": {"level_db": -16, "duration_ms": 200}}]})
    y = render(data, a, proj).data[:, 0]
    ins = y[int(1.62 * fs):int(1.88 * fs)] - data[int(1.62 * fs):int(1.88 * fs), 0]
    nz = np.flatnonzero(np.abs(ins) > 1e-6)
    # 区間の始まりから鳴り、長さは約 200ms
    assert nz[0] / fs < 0.005
    assert 0.17 < (nz[-1] - nz[0]) / fs < 0.205
    lvl = 20 * np.log10(np.sqrt(np.mean(ins[nz[0]:nz[-1]] ** 2)) / reference_rms(a))
    assert -20 < lvl < -12, lvl
    # 高めの帯域のノイズ(鼻をすする音)
    fw, P = signal.welch(ins[nz[0]:nz[-1]], fs, nperseg=1024)
    assert np.sum(fw * P) / np.sum(P) > 1500
    # 再合成していない
    outside = np.ones(len(y), dtype=bool)
    outside[int(1.62 * fs):int(1.88 * fs)] = False
    assert np.array_equal(y[outside], data[outside, 0])


def test_sfx_uses_material_and_cuts_at_region_end(vocal44, tmp_path, monkeypatch):
    data, a = vocal44
    fs = a.fs
    monkeypatch.setenv("CRY_MATERIALS_DIR", str(tmp_path))
    (tmp_path / "sfx").mkdir()
    tone = 0.3 * np.sin(2 * np.pi * 700 * np.arange(fs) / fs)  # 1 秒の素材
    sf.write(tmp_path / "sfx" / "sniff.wav", np.stack([tone, tone], axis=1), fs)
    from cry_effect import materials
    assert materials.list_files("sfx") == ["sniff.wav"]
    proj = _norm({"regions": [{"type": "sfx", "start": 1.62, "end": 1.85, "strength": 100,
                               "params": {"sample": "sniff.wav"}}]})
    y = render(data, a, proj).data[:, 0]
    d = y - data[:, 0]
    nz = np.flatnonzero(np.abs(d) > 1e-6)
    assert abs(nz[0] / fs - 1.62) < 0.002 and nz[-1] / fs <= 1.85 + 1e-3  # 区間の終わりで切る
    f, P = signal.welch(d[nz[0]:nz[-1]], fs, nperseg=2048)
    assert abs(f[np.argmax(P)] - 700) < 30


def test_sfx_warns_on_voiced_and_lists_materials(client):
    from test_server import _wait_ready
    c = client
    c.post("/api/open", json={"path": str(c.wav)})
    _wait_ready(c)
    res = c.post("/api/project", json={"regions": [
        {"id": "ok", "type": "sfx", "start": 1.62, "end": 1.88, "params": {}},
        {"id": "bad", "type": "sfx", "start": 2.0, "end": 2.3, "params": {}},
        {"id": "gc", "type": "glottal_catch", "time": 1.0, "params": {}},
    ]}).json()
    assert res["warn_ids"] == ["bad"]
    m = c.get("/api/materials/sfx").json()
    assert "files" in m and "dir" in m
    eff = {e["name"]: e for e in c.get("/api/effects").json()}
    assert all(e["implemented"] for e in eff.values())
    assert eff["sfx"]["inserts_in_silence"] and eff["glottal_catch"]["gain_only"]


def test_phase3_params_clamped():
    p = _norm({"regions": [
        {"type": "glottal_catch", "time": 1.0, "params": {"duration_ms": 5, "depth_db": 120}},
        {"type": "f2_raise", "start": 1, "end": 2, "params": {"amount": 2}},
        {"type": "nasal", "start": 1, "end": 2, "params": {"high_cut_db": -5, "low_boost_db": 50}},
        {"type": "sfx", "start": 1, "end": 1.3, "params": {"level_db": 20}},
    ]})
    gc, f2, na, sx = p["regions"]
    assert gc["params"] == {"duration_ms": 20.0, "depth_db": 80.0}
    assert f2["params"] == {"amount": 0.45}
    assert na["params"] == {"high_cut_db": 0.0, "low_boost_db": 9.0}
    assert sx["params"]["level_db"] == 0.0 and sx["params"]["sample"] == ""
