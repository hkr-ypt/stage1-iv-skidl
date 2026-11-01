"""Sweep builders (exact grids), calibration fits and output writers."""

import json
import math
from decimal import Decimal

import pytest

from stage1iv import calibration as C
from stage1iv import sweeps as S
from stage1iv import writers as W
from stage1iv.records import parse_pt


def test_exact_grid_and_turning_points_once():
    sw = S.hysteresis_loop("1", "0.25", cycles=2)
    pts = [p.v for p in sw.iter_points()]
    assert sw.n_points == len(pts) == 1 + 16 * 2
    assert pts[0] == 0 and max(pts) == 1 and min(pts) == -1
    for a, b in zip(pts, pts[1:]):
        assert abs(b - a) == Decimal("0.25")
    up = pts[1:5]
    down = pts[5:9]
    assert up[:-1] == list(reversed(down))[1:]    # identical setpoints both ways


def test_grid_error_and_suggestion():
    with pytest.raises(S.GridError) as e:
        S.waypoint_sweep([0, 1, Decimal("-0.5")], "0.3")
    assert "0.25" in str(e.value)
    with pytest.raises(ValueError):
        S.linear(0, 11, 1)                 # beyond 10 V


def test_log_list_and_add_chunks():
    pts = S.log_list("0.001", "1", 3)
    assert pts[0] == Decimal("0.001") and pts[-1] == Decimal("1")
    assert all(p == p.quantize(Decimal("0.000001")) for p in pts)
    cmds = S.list_add_commands(pts)
    assert all(len(c.split()) - 1 <= 8 for c in cmds)


def test_fit_gain_offset_known_answer():
    g, o = 1.5e-6, 12.0
    codes = [-600000, -300000, 0, 300000, 600000]
    ref = [g * (c - o) for c in codes]
    fit = C.fit_gain_offset(codes, ref)
    assert fit.gain == pytest.approx(g, rel=1e-9)
    assert fit.offset == pytest.approx(o, abs=1e-3)


def test_fit_dac_and_rs_known_answer():
    g, c0 = 306.4e-6, 32770.0
    codes = list(range(1000, 64000, 7000))
    vs = [g * (k - c0) for k in codes]
    fit = C.fit_dac(codes, vs)
    assert fit.g_dac == pytest.approx(g, rel=1e-9) and fit.c_dac == pytest.approx(c0, abs=1e-3)
    r_s, r_top, r_sd = 119.0, 106.0, 50200.0
    i = [-0.03, -0.02, -0.01, 0.01, 0.02, 0.03]
    vk = [x * 200.0 for x in i]
    vset = [vk[j] * (1 + r_top / r_sd) + 0.0015 + r_s * i[j] for j in range(6)]
    rs = C.fit_series_resistance(vset, vk, i, r_top=r_top, r_sd=r_sd)
    assert rs.r_s == pytest.approx(r_s, abs=1e-6)


def test_plausibility_mirrors_firmware():
    assert C.is_plausible(C.NOMINAL)
    bad = dict(C.NOMINAL)
    bad["r_s"] = 500
    assert not C.is_plausible(bad)


def test_writers(tmp_path):
    line = "PT 1,1,0,0,1,2,3,32768,0.000153,1,491,2287,-,-,0.003427,0.0000061059,0x0380"
    recs = [parse_pt(line)]
    csv_path = W.write_csv(tmp_path / "s.csv", recs)
    rows = W.read_csv(csv_path)
    assert rows[0]["i_raw"] == "491" and "FIRST" in rows[0]["flag_names"]
    js = W.write_sidecar(tmp_path / "s.json", idn={"model": "stage1-iv"}, note="n")
    data = json.loads(js.read_text())
    assert data["format"] == W.SIDECAR_FORMAT
    assert "provisional" in json.dumps(data).lower()
