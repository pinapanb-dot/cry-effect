"""出力の基本的な性質のテスト(長さ / NaN / クリップ / 区間外 / クリック)。"""

import numpy as np
import pytest
from scipy import signal

from cry_effect.project import normalize_project
from cry_effect.render import CROSSFADE_S, GUARD_S, SYNTH_MARGIN_S, render


def _render(data, a, proj):
    return render(data, a, normalize_project(proj, warn=lambda m: None))


@pytest.mark.parametrize("fixture", ["vocal44", "vocal48_stereo", "vocal44_loud"])
def test_length_shape_finite_and_peak(request, fixture, all_phase1_project):
    data, a = request.getfixturevalue(fixture)
    res = _render(data, a, all_phase1_project)
    y = res.data
    assert y.shape == data.shape  # サンプル単位で同じ長さ・同じチャンネル数
    assert np.all(np.isfinite(y))
    assert np.max(np.abs(y)) <= 1.0
    # 元音声のピークを超えて大きくしない(上限 0.98 か元のピーク)
    assert np.max(np.abs(y)) <= max(0.98, np.max(np.abs(data))) + 1e-9


def test_no_regions_is_identity(vocal44):
    data, a = vocal44
    res = _render(data, a, {"regions": []})
    assert np.array_equal(res.data, data)


def test_zero_global_amount_is_identity(vocal44, all_phase1_project):
    data, a = vocal44
    all_phase1_project["global_cry_amount"] = 0
    res = _render(data, a, all_phase1_project)
    assert np.array_equal(res.data, data)


@pytest.mark.parametrize("fixture", ["vocal44", "vocal48_stereo"])
def test_outside_regions_unchanged(request, fixture, all_phase1_project):
    data, a = request.getfixturevalue(fixture)
    proj = normalize_project(all_phase1_project, warn=lambda m: None)
    res = render(data, a, proj)
    fs = a.fs
    # スパン(クロスフェードを含む再合成範囲)の外は完全に一致する
    inside = np.zeros(len(data), dtype=bool)
    for sp in res.spans:
        inside[int(round(sp.t0 * fs)):int(round(sp.t1 * fs))] = True
    assert np.max(np.abs(res.data[~inside] - data[~inside])) == 0.0
    # スパンは区間の前後 (XF + 余裕 + 整列誤差) 以内に収まる
    from cry_effect import effects
    margin = CROSSFADE_S + GUARD_S + 0.011
    for sp in res.spans:
        ext = [effects.get(r["type"]).extent(r) for r in sp.regions]
        assert sp.t0 >= min(e[0] for e in ext) - margin - 1e-9
        assert sp.t1 <= max(e[1] for e in ext) + margin + 1e-9
    # 区間の外側 50ms より先は、元とほぼ同じ(差が -60dB 以下)
    far = np.ones(len(data), dtype=bool)
    for r in proj["regions"]:
        s, e = effects.get(r["type"]).extent(r)
        far[int((s - 0.05 - CROSSFADE_S - GUARD_S) * fs):int((e + 0.05 + CROSSFADE_S + GUARD_S) * fs)] = False
    diff = res.data[far] - data[far]
    assert np.max(np.abs(diff)) < 1e-3


def test_regions_change_audio(vocal44, all_phase1_project):
    data, a = vocal44
    proj = normalize_project(all_phase1_project, warn=lambda m: None)
    res = render(data, a, proj)
    fs = a.fs
    for r in proj["regions"]:
        s0, s1 = int(r["start"] * fs), int(r["end"] * fs)
        d = res.data[s0:s1, 0] - data[s0:s1, 0]
        assert np.sqrt(np.mean(d ** 2)) > 1e-3, r["type"]


def _hf_burst_ratio(y, fs, t, win_s=0.004, ctx_s=0.1):
    """時刻 t の近くの高域の短時間 RMS の最大値 / 周囲の中央値。クリックがあると大きくなる。"""
    sos = signal.butter(4, 9000, btype="high", fs=fs, output="sos")
    hp = signal.sosfiltfilt(sos, y)
    w = int(win_s * fs)
    a, b = int((t - ctx_s) * fs), int((t + ctx_s) * fs)
    a, b = max(0, a), min(len(y), b)
    seg = hp[a:b]
    n = len(seg) // w
    frames = np.sqrt(np.mean(seg[: n * w].reshape(n, w) ** 2, axis=1))
    centre = int((t - a / fs) / win_s) if a > 0 else int(t / win_s)
    near = frames[max(0, centre - 4): centre + 5]
    floor = 3e-4  # -70dB 程度の絶対的な下限
    return (np.max(near) + floor) / (np.median(frames) + floor)


@pytest.mark.parametrize("fixture", ["vocal44", "vocal48_stereo"])
def test_no_clicks_at_boundaries(request, fixture, all_phase1_project):
    data, a = request.getfixturevalue(fixture)
    proj = normalize_project(all_phase1_project, warn=lambda m: None)
    res = render(data, a, proj)
    fs = a.fs
    from cry_effect import effects
    times = []
    for sp in res.spans:
        times += [sp.t0, sp.t0 + CROSSFADE_S, sp.t1 - CROSSFADE_S, sp.t1]
    for r in proj["regions"]:
        times += list(effects.get(r["type"]).extent(r))
    y = res.data[:, 0]
    x = data[:, 0]
    for t in times:
        if t <= 0.11 or t >= len(y) / fs - 0.11:
            continue
        ry = _hf_burst_ratio(y, fs, t)
        rx = _hf_burst_ratio(x, fs, t)
        assert ry < max(4.0, 2.0 * rx), f"t={t:.3f}s で高域の突出 {ry:.2f}(元 {rx:.2f})"
    # 波形の段差: つなぎ目のサンプル差分が、元音声の差分の最大値を大きく超えない
    dx = np.max(np.abs(np.diff(x)))
    for sp in res.spans:
        for t in (sp.t0, sp.t1):
            i = int(round(t * fs))
            if 1 <= i < len(y) - 1:
                local = np.abs(np.diff(y[i - 2:i + 3]))
                assert np.max(local) <= dx * 1.5


def test_span_regions_inside_synth_range(vocal44):
    """区間をファイルの端に置いても壊れない。"""
    data, a = vocal44
    dur = len(data) / a.fs
    proj = {"regions": [
        {"type": "tremor", "start": 0.0, "end": 0.5, "strength": 100, "params": {}},
        {"type": "phrase_end", "start": dur - 0.6, "end": dur, "strength": 100, "params": {}},
    ]}
    res = _render(data, a, proj)
    assert res.data.shape == data.shape
    assert np.all(np.isfinite(res.data))
    assert SYNTH_MARGIN_S > 0
