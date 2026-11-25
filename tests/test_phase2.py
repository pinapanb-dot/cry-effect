"""フェーズ2 の処理のテスト。"""

import copy

import numpy as np
import pytest
import soundfile as sf
from scipy import signal

from cry_effect import effects
from cry_effect.effects.base import SegmentState
from cry_effect.project import normalize_project
from cry_effect.render import CROSSFADE_S, GUARD_S, make_context, render

from test_render import _hf_burst_ratio

# 合成ボーカル: 音符 0.30-1.60 / 1.90-3.40 / 3.70-4.80、その間は無音
PHASE2 = [
    {"id": "p1", "type": "sob_pulse", "start": 0.4, "end": 1.5, "strength": 100,
     "params": {"rate_hz": 8, "depth_db": 24, "irregularity": 1.0}},
    {"id": "v1", "type": "voice_break", "time": 2.6, "strength": 100,
     "params": {"semitones": 10, "duration_ms": 220, "harmonic_cut": 1.0}},
    {"id": "a1", "type": "articulation_blur", "start": 1.95, "end": 2.45, "strength": 100,
     "params": {"smooth_ms": 120, "toward_a": 0.8}},
    {"id": "i1", "type": "sob_inhale", "start": 3.42, "end": 3.68, "strength": 100,
     "params": {"level_db": 0, "duration_ms": 600, "rise": 1.0}},
]


def _norm(p):
    return normalize_project(p, warn=lambda m: None)


@pytest.fixture
def phase2_project():
    return {"global_cry_amount": 100, "regions": copy.deepcopy(PHASE2)}


@pytest.mark.parametrize("fixture", ["vocal44", "vocal48_stereo", "vocal44_loud"])
def test_phase2_basic_properties(request, fixture, phase2_project):
    data, a = request.getfixturevalue(fixture)
    proj = _norm(phase2_project)
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
    # 各処理が実際に音を変えている
    for r in proj["regions"]:
        s, e = effects.get(r["type"]).extent(r)
        d = y[int(s * fs):int(e * fs), 0] - data[int(s * fs):int(e * fs), 0]
        assert np.sqrt(np.mean(d ** 2)) > 1e-3, r["type"]


@pytest.mark.parametrize("fixture", ["vocal44", "vocal48_stereo"])
def test_phase2_no_clicks(request, fixture, phase2_project):
    data, a = request.getfixturevalue(fixture)
    proj = _norm(phase2_project)
    res = render(data, a, proj)
    fs = a.fs
    y, x = res.data[:, 0], data[:, 0]
    times = []
    for sp in res.spans:
        times += [sp.t0, sp.t0 + CROSSFADE_S, sp.t1 - CROSSFADE_S, sp.t1]
    for r in proj["regions"]:
        if r["type"] == "sob_inhale":
            continue  # 吸気音そのものが高域を含むので別に調べる
        times += list(effects.get(r["type"]).extent(r))
    for t in times:
        if 0.11 < t < len(y) / fs - 0.11:
            ry, rx = _hf_burst_ratio(y, fs, t), _hf_burst_ratio(x, fs, t)
            assert ry < max(4.0, 2.0 * rx), f"t={t:.3f}"
    # 吸気音の両端に段差がない(立ち上がり・消え際がなめらか)
    r = proj["regions"][3]
    for t in (r["start"], r["end"]):
        i = int(round(t * fs))
        seg = y[i - 200:i + 200]
        assert np.max(np.abs(np.diff(seg))) < 0.02


def test_sob_pulse_timing_and_depth(vocal44):
    _, a = vocal44
    eff = effects.get("sob_pulse")
    for rate in (2.0, 4.0, 6.0):
        p = {"rate_hz": rate, "depth_db": 10, "irregularity": 0.6}
        pulses = eff.pulse_times(0.0, 20.0, p, np.random.default_rng(1))
        n_per_s = len(pulses) / 20.0
        assert 0.6 * rate < n_per_s < 1.4 * rate, (rate, n_per_s)
        iv = np.diff([t for t, _, _ in pulses])
        assert iv.min() >= eff.MIN_INTERVAL_S - 1e-9
    # ばらつき 0 なら等間隔、1 ならばらつく
    reg = lambda irr: [t for t, _, _ in eff.pulse_times(0, 20, {"rate_hz": 4, "depth_db": 10, "irregularity": irr},
                                                         np.random.default_rng(2))]
    assert np.std(np.diff(reg(0.0))) < 1e-9
    assert np.std(np.diff(reg(1.0))) / np.mean(np.diff(reg(1.0))) > 0.3
    # 音量の落ち込みは深さを超えない
    proj = _norm({"regions": [{"type": "sob_pulse", "start": 0.4, "end": 1.5, "strength": 100,
                               "params": {"depth_db": 12}}]})
    ctx = make_context(a, proj)
    st = SegmentState(0, a.f0.copy(), np.ones((a.n_frames, 4)), np.zeros((a.n_frames, 4)), np.zeros(a.n_frames))
    ctx.n_bins = 4
    eff.apply(st, proj["regions"][0], ctx)
    assert st.gain_db.min() >= -12.0 - 1e-9
    assert st.gain_db.min() < -6.0


def test_voice_break_f0_jump(vocal44):
    _, a = vocal44
    proj = _norm({"regions": [{"type": "voice_break", "time": 0.8, "strength": 100,
                               "params": {"semitones": 5, "duration_ms": 100, "harmonic_cut": 0.5}}]})
    r = proj["regions"][0]
    ctx = make_context(a, proj)
    st = SegmentState(0, a.f0.copy(), np.array(a.sp[:]), np.array(a.ap[:], dtype=float), np.zeros(a.n_frames))
    effects.get("voice_break").apply(st, r, ctx)
    t = a.times
    semis = 12 * np.log2(np.where(a.f0 > 0, st.f0 / np.maximum(a.f0, 1e-9), 1.0))
    mid = np.argmin(np.abs(t - 0.85))
    assert 4.5 < semis[mid] <= 5.0 + 1e-6  # 真ん中で跳ねている
    outside = (t < 0.795) | (t > 0.905)
    assert np.allclose(semis[outside], 0.0)  # マーカーの外は変わらない
    # 高域が弱まる(2.5kHz 以上の包絡が下がる)
    hi = np.arange(a.n_bins) * a.fs / a.fft_size > 2500
    assert np.mean(st.sp[mid, hi]) < 0.5 * np.mean(np.asarray(a.sp[mid])[hi])


def test_voice_break_on_silence_reports_note(vocal44):
    data, a = vocal44
    proj = _norm({"regions": [{"type": "voice_break", "time": 1.7, "strength": 100, "params": {}}]})
    res = render(data, a, proj)
    assert any("無声" in n for n in res.notes)


def test_articulation_blur_smooths_envelope_over_time(vocal44):
    _, a = vocal44
    proj = _norm({"regions": [{"type": "articulation_blur", "start": 1.95, "end": 3.35, "strength": 100,
                               "params": {"smooth_ms": 80, "toward_a": 0.0}}]})
    ctx = make_context(a, proj)
    sp = np.array(a.sp[:])
    st = SegmentState(0, a.f0.copy(), sp.copy(), np.array(a.ap[:], dtype=float), np.zeros(a.n_frames))
    effects.get("articulation_blur").apply(st, proj["regions"][0], ctx)
    m = (a.times > 2.1) & (a.times < 3.2)
    before = np.mean(np.abs(np.diff(np.log(sp[m]), axis=0)))
    after = np.mean(np.abs(np.diff(np.log(st.sp[m]), axis=0)))
    assert after < 0.7 * before
    # 区間の外と無音は変わらない
    out = (a.times < 1.9) | (a.times > 3.4)
    assert np.allclose(st.sp[out], sp[out])


def test_articulation_blur_toward_a_moves_formants(vocal44):
    """「い」の形の包絡が「あ」の形に近づく。"""
    _, a = vocal44
    from cry_effect.effects.phase2 import a_pattern
    bin_hz = a.fs / a.fft_size
    f = np.arange(a.n_bins) * bin_hz
    i_env = np.full(a.n_bins, 0.01)
    for fc, bw, amp in ((300, 80, 1.0), (2300, 150, 0.5), (3000, 200, 0.3)):
        i_env += amp / (1 + ((f - fc) / bw) ** 2)
    sp = np.tile(i_env, (a.n_frames, 1))
    proj = _norm({"regions": [{"type": "articulation_blur", "start": 1.95, "end": 3.35, "strength": 100,
                               "params": {"smooth_ms": 30, "toward_a": 0.5}}]})
    ctx = make_context(a, proj)
    st = SegmentState(0, a.f0.copy(), sp.copy(), np.zeros_like(sp), np.zeros(a.n_frames))
    effects.get("articulation_blur").apply(st, proj["regions"][0], ctx)
    m = (a.times > 2.2) & (a.times < 3.1)
    pat = a_pattern(a.n_bins, bin_hz)
    band = (f > 200) & (f < 4000)

    def corr(x):
        lx = np.log(x[:, band])
        return np.mean([np.corrcoef(row, pat[band])[0, 1] for row in lx])

    assert corr(st.sp[m]) > corr(sp[m]) + 0.2


def test_sob_inhale_synth_and_level(vocal44):
    data, a = vocal44
    fs = a.fs
    proj = _norm({"regions": [{"type": "sob_inhale", "start": 1.62, "end": 1.88, "strength": 100,
                               "params": {"level_db": -14, "duration_ms": 200}}]})
    res = render(data, a, proj)
    y = res.data[:, 0]
    ins = y[int(1.62 * fs):int(1.88 * fs)] - data[int(1.62 * fs):int(1.88 * fs), 0]
    nz = np.flatnonzero(np.abs(ins) > 1e-6)
    # 区間の終わりで吸い終わり、長さは約 200ms
    assert abs(nz[-1] / fs - 0.26) < 0.01
    assert 0.15 < (nz[-1] - nz[0]) / fs < 0.21
    # 音量は声の代表 RMS の約 -14dB(±4dB)
    from cry_effect.render import reference_rms
    lvl = 20 * np.log10(np.sqrt(np.mean(ins[nz[0]:nz[-1]] ** 2)) / reference_rms(a))
    assert -18 < lvl < -10, lvl
    # 中心周波数が上がっていく
    f, t, Z = signal.stft(ins[nz[0]:nz[-1]], fs, nperseg=1024)
    band = (f > 300) & (f < 6000)
    mag = np.abs(Z[band])
    cent = (mag * f[band, None]).sum(0) / (mag.sum(0) + 1e-12)
    q = len(cent) // 4
    assert np.mean(cent[-2 * q:-q]) > np.mean(cent[q:2 * q])
    # 再合成していない(挿入だけのスパンは元の音 + 吸気音)
    outside = np.ones(len(y), dtype=bool)
    outside[int(1.62 * fs):int(1.88 * fs)] = False
    assert np.array_equal(y[outside], data[outside, 0])


def test_sob_inhale_uses_material(vocal44, tmp_path, monkeypatch):
    data, a = vocal44
    fs = a.fs
    monkeypatch.setenv("CRY_MATERIALS_DIR", str(tmp_path))
    (tmp_path / "inhale").mkdir()
    tone = 0.3 * np.sin(2 * np.pi * 1000 * np.arange(int(0.5 * 22050)) / 22050)  # 22.05kHz の素材
    sf.write(tmp_path / "inhale" / "tone.wav", tone, 22050)
    from cry_effect import materials
    assert materials.list_files("inhale") == ["tone.wav"]
    proj = _norm({"regions": [{"type": "sob_inhale", "start": 1.62, "end": 1.88, "strength": 100,
                               "params": {"sample": "tone.wav"}}]})
    y = render(data, a, proj).data[:, 0]
    ins = y[int(1.62 * fs):int(1.88 * fs)] - data[int(1.62 * fs):int(1.88 * fs), 0]
    f, P = signal.welch(ins, fs, nperseg=2048)
    assert abs(f[np.argmax(P)] - 1000) < 30  # 素材の音(1kHz)が入っている
    # フォルダの外は読まない
    assert materials.load("inhale", "../inhale/tone.wav", fs) is None


def test_sob_inhale_warns_on_voiced(client):
    from test_server import _wait_ready
    c = client
    c.post("/api/open", json={"path": str(c.wav)})
    _wait_ready(c)
    res = c.post("/api/project", json={"regions": [
        {"id": "ok", "type": "sob_inhale", "start": 1.62, "end": 1.88, "params": {}},
        {"id": "bad", "type": "sob_inhale", "start": 1.0, "end": 1.3, "params": {}},
    ]}).json()
    assert res["warn_ids"] == ["bad"]
    assert any("有声区間に重なって" in w for w in res["warnings"])
    m = c.get("/api/materials/inhale").json()
    assert "files" in m and "dir" in m
    assert c.get("/api/materials/..").status_code in (400, 404)


def test_marker_and_choice_params_roundtrip():
    p = _norm({"regions": [
        {"type": "voice_break", "time": 1.0, "params": {"semitones": 12, "duration_ms": 500}},
        {"type": "sob_inhale", "start": 1, "end": 1.3, "params": {"sample": 5, "level_db": 10}},
    ]})
    vb, si = p["regions"]
    assert vb["params"] == {"semitones": 10.0, "duration_ms": 220.0, "harmonic_cut": 0.5}
    assert si["params"]["sample"] == "" and si["params"]["level_db"] == 0.0
