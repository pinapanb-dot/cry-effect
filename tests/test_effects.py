"""各処理・パラメータ・キャッシュ・入出力のテスト。"""

import json
import os

import numpy as np
import pytest

from cry_effect import dsp, effects
from cry_effect.analysis import analyze
from cry_effect.audio_io import load_wav, save_wav
from cry_effect.project import normalize_project
from cry_effect.render import make_context, render


def test_params_are_clamped_to_safety_limits():
    msgs = []
    p = normalize_project({"regions": [
        {"type": "tremor", "start": 1, "end": 2, "strength": 150,
         "params": {"depth_cents": 300, "rate_hz": 20, "irregularity": -1}},
        {"type": "breathiness", "start": 1, "end": 2, "params": {"amount": 5}},
        {"type": "sob_formant", "start": 1, "end": 2, "params": {"warp": 0.1}},
        {"type": "nope", "start": 1, "end": 2},
    ]}, warn=msgs.append)
    t, b, s = p["regions"]
    assert t["strength"] == 100
    assert t["params"] == {"depth_cents": 120.0, "rate_hz": 10.0, "irregularity": 0.0}
    assert b["params"]["amount"] == 0.9
    assert s["params"]["warp"] == 0.78
    assert any("nope" in m for m in msgs)


def test_defaults_filled():
    p = normalize_project({"regions": [{"type": "phrase_end", "start": 1, "end": 2}]}, warn=lambda m: None)
    prm = p["regions"][0]["params"]
    assert prm["f0_drop_cents"] == -50.0
    assert p["global_cry_amount"] == 100.0


def test_tremor_curve_bounded_and_irregular():
    eff = effects.get("tremor")
    fs_ctrl = 200.0
    n = int(10 * fs_ctrl)
    for irr in (0.0, 0.5, 1.0):
        p = {"depth_cents": 80.0, "rate_hz": 5.0, "irregularity": irr}
        c = eff.cents_curve(n, p, np.random.default_rng(3), fs_ctrl)
        assert np.max(np.abs(c)) <= 80.0
        assert np.max(np.abs(c)) > 30.0  # ちゃんと揺れている
        # 規則的なビブラートではない: 自己相関の 2 周期目のピークが小さい
        c0 = c - c.mean()
        ac = np.correlate(c0, c0, "full")[n - 1:] / np.dot(c0, c0)
        lag2 = int(2 * fs_ctrl / 5.0)
        assert np.max(ac[lag2 - 10:lag2 + 10]) < 0.7, irr
        # 揺れの間隔(ゼロ交差の間隔)がばらつく
        zc = np.flatnonzero(np.diff(np.sign(c0)) != 0)
        iv = np.diff(zc)
        assert np.std(iv) / np.mean(iv) > 0.15


def test_tremor_f0_change_within_depth(vocal44):
    data, a = vocal44
    proj = normalize_project({"regions": [
        {"type": "tremor", "start": 0.5, "end": 1.4, "strength": 100,
         "params": {"depth_cents": 40, "rate_hz": 5, "irregularity": 0.5}}]}, warn=lambda m: None)
    ctx = make_context(a, proj)
    from cry_effect.effects.base import SegmentState
    st = SegmentState(0, a.f0.copy(), np.zeros((a.n_frames, 1)), np.zeros((a.n_frames, 1)), np.zeros(a.n_frames))
    effects.get("tremor").apply(st, proj["regions"][0], ctx)
    v = a.f0 > 0
    cents = 1200 * np.log2(st.f0[v] / a.f0[v])
    assert np.max(np.abs(cents)) <= 40.0 + 1e-6
    assert np.max(np.abs(cents)) > 5.0
    # 区間の外は変わらない
    t = a.times
    out = v & ((t < 0.5) | (t > 1.4))
    assert np.allclose(st.f0[out], a.f0[out])


def test_region_weight_ramps():
    t = np.linspace(0, 2, 2001)
    w = dsp.region_weight(t, 0.5, 1.5, 0.05)
    assert w[0] == 0 and w[-1] == 0
    assert np.isclose(w[np.searchsorted(t, 1.0)], 1.0)
    assert np.all(w[t <= 0.5] == 0) and np.all(w[t >= 1.5] == 0)
    assert np.max(np.abs(np.diff(w))) < 0.05  # 急な段差がない
    w2 = dsp.region_weight(t, 0.5, 1.5, 0.05, release_s=0.05)
    assert np.isclose(w2[np.searchsorted(t, 1.5)], 1.0)  # 終わりまで保つ
    assert np.all(w2[t >= 1.55] == 0)


def test_warp_lowers_formant_peak():
    n_bins = 1025
    bin_hz = 44100 / 2048
    f = np.arange(n_bins) * bin_hz
    env = 1.0 / (1.0 + ((f - 1000) / 80) ** 2) + 1e-3
    sp = np.tile(env, (3, 1))
    out = dsp.warp_envelope(sp, dsp.scale_map(n_bins, np.array([1.0, 0.94, 0.85])))
    peaks = f[np.argmax(out, axis=1)]
    assert abs(peaks[0] - 1000) < bin_hz
    assert abs(peaks[1] - 940) < 2 * bin_hz
    assert abs(peaks[2] - 850) < 2 * bin_hz


def test_band_stretch_map_moves_only_band():
    n_bins = 1025
    bin_hz = 44100 / 2048
    m = dsp.band_stretch_map(n_bins, bin_hz, 900, 2500, np.array([0.1]))
    f = np.arange(n_bins) * bin_hz
    src_hz = m[0] * bin_hz
    outside = (f < 900) | (f > 2500)
    assert np.allclose(src_hz[outside], f[outside], atol=1e-6)
    inside = (f > 1000) & (f < 2400)
    assert np.all(src_hz[inside] <= f[inside] + 1e-9)  # 帯域内は下の周波数を読む = 上へ伸びる


def test_phrase_end_fades_level(vocal44):
    data, a = vocal44
    proj = normalize_project({"regions": [
        {"type": "phrase_end", "start": 0.6, "end": 1.55, "strength": 100,
         "params": {"onset": 0.3, "level_drop_db": 20}}]}, warn=lambda m: None)
    y = render(data, a, proj).data[:, 0]
    fs = a.fs
    seg = slice(int(1.35 * fs), int(1.5 * fs))
    ref = slice(int(0.7 * fs), int(0.8 * fs))
    drop_out = 20 * np.log10(np.std(y[seg]) / np.std(y[ref]))
    drop_in = 20 * np.log10(np.std(data[seg, 0]) / np.std(data[ref, 0]))
    assert drop_out < drop_in - 8.0


def test_breathiness_increases_noise(vocal44):
    data, a = vocal44
    proj = normalize_project({"regions": [
        {"type": "breathiness", "start": 0.5, "end": 1.4, "strength": 100, "params": {"amount": 0.6}}]},
        warn=lambda m: None)
    y = render(data, a, proj).data[:, 0]
    fs = a.fs
    from scipy import signal
    sos = signal.butter(4, [1000, 4000], btype="band", fs=fs, output="sos")

    def periodicity(z):
        # 1〜4kHz 帯の、ピッチ周期での正規化自己相関(息が増えると下がる)
        z = signal.sosfiltfilt(sos, z)
        w = int(0.04 * fs)
        vals = []
        for s in range(int(0.7 * fs), int(1.2 * fs) - 2 * w, w):
            f = z[s:s + 2 * w]
            a0 = f[:w]
            best = 0.0
            for lag in range(int(fs / 260), int(fs / 190)):
                b = f[lag:lag + w]
                best = max(best, np.dot(a0, b) / np.sqrt(np.dot(a0, a0) * np.dot(b, b) + 1e-20))
            vals.append(best)
        return float(np.mean(vals))

    px, py = periodicity(data[:, 0]), periodicity(y)
    assert py < px - 0.1, (px, py)


def test_deterministic(vocal44, all_phase1_project):
    data, a = vocal44
    p = normalize_project(all_phase1_project, warn=lambda m: None)
    y1 = render(data, a, p).data
    y2 = render(data, a, p).data
    assert np.array_equal(y1, y2)


def test_analysis_cache_reused(tmp_path):
    from signals import synth_vocal
    x = synth_vocal(44100)[: 44100 * 2, 0]
    a1 = analyze(x, 44100, cache_dir=str(tmp_path), workers=1)
    assert a1.meta["from_cache"] is False
    a2 = analyze(x, 44100, cache_dir=str(tmp_path), workers=1)
    assert a2.meta["from_cache"] is True
    assert np.array_equal(a1.f0, a2.f0)
    assert np.array_equal(np.asarray(a1.sp), np.asarray(a2.sp))


def test_chunked_analysis_matches_single(tmp_path, monkeypatch):
    """区間に分けた並列分析が、一括の分析とほぼ一致する。"""
    from cry_effect import analysis as an
    from signals import synth_vocal
    x = synth_vocal(44100)[:, 0]
    whole = analyze(x, 44100, use_cache=False, workers=1)
    monkeypatch.setattr(an, "CHUNK_SECONDS", 1.5)
    chunked = analyze(x, 44100, use_cache=False, workers=2)
    assert chunked.n_frames == whole.n_frames
    agree = np.mean((chunked.f0 > 0) == (whole.f0 > 0))
    assert agree > 0.98
    both = (chunked.f0 > 0) & (whole.f0 > 0)
    cents = np.abs(1200 * np.log2(chunked.f0[both] / whole.f0[both]))
    assert np.median(cents) < 1.0


def test_voiced_and_silence_detection(vocal44):
    _, a = vocal44
    t = a.times
    assert np.mean(a.voiced[(t > 0.5) & (t < 1.4)]) > 0.95
    assert np.mean(a.silent[(t > 1.65) & (t < 1.85)]) > 0.9
    assert np.mean(a.silent[(t > 0.5) & (t < 1.4)]) < 0.05


def test_cli_roundtrip_and_never_overwrites_source(tmp_path, cache_dir, monkeypatch):
    from signals import synth_vocal
    import cli
    monkeypatch.setenv("CRY_CACHE_DIR", cache_dir)
    from cry_effect import analysis as an
    monkeypatch.setattr(an, "DEFAULT_CACHE_DIR", cache_dir)
    src = tmp_path / "vocal.wav"
    data = synth_vocal(48000, channels=2)
    import soundfile as sf
    sf.write(src, data, 48000, subtype="PCM_24")
    before = src.read_bytes()
    proj = {"source": "vocal.wav", "global_cry_amount": 60, "regions": [
        {"type": "tremor", "start": 0.5, "end": 1.4, "strength": 70,
         "params": {"depth_cents": 30, "rate_hz": 5, "irregularity": 0.6}},
        {"type": "sob_formant", "start": 2.0, "end": 3.2, "strength": 50, "params": {"warp": 0.94}},
        {"type": "voice_break", "time": 2.5, "strength": 80,
         "params": {"semitones": 5, "duration_ms": 100, "harmonic_cut": 0.5}},
    ]}
    pj = tmp_path / "project.json"
    pj.write_text(json.dumps(proj), encoding="utf-8")

    assert cli.main(["render", str(pj)]) == 0
    out = tmp_path / "vocal_cry.wav"
    y, fs = sf.read(out, always_2d=True)
    info = sf.info(out)
    assert info.subtype == "FLOAT" and fs == 48000
    assert y.shape == data.shape
    assert src.read_bytes() == before

    # 出力先に元ファイルを指定しても上書きしない
    rc = cli.main(["render", str(pj), "-o", str(src)])
    assert rc != 0
    assert src.read_bytes() == before


def test_save_wav_refuses_source(tmp_path):
    p = tmp_path / "a.wav"
    save_wav(str(p), np.zeros((10, 1)), 44100)
    with pytest.raises(ValueError):
        save_wav(str(p), np.zeros((10, 1)), 44100, protect=[str(p)])
    with pytest.raises(ValueError):
        save_wav(str(tmp_path / "b.wav"), np.full((10, 1), np.nan), 44100)
