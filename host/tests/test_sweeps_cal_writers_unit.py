"""Sweep builders (exact grids), calibration fits and output writers."""

import json
import math
from decimal import Decimal

import pytest

from stage1iv import calibration as C
from stage1iv import sweeps as S
from stage1iv import writers as W
from stage1iv.records import PT_FIELDS, parse_pt


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


# ---- review fixes (host-library audit) ----------------------------------------------

def _result(lines=(), est_s="0.12"):
    from stage1iv.client import SweepResult
    return SweepResult(sweep_id=1, points_planned=len(lines), est_s=Decimal(est_s),
                       status="complete", records=[parse_pt(x) for x in lines])


def test_write_sweep_keeps_dots_in_the_base_name(tmp_path):
    """HL-5: 'diode_0.5V' must not become 'diode_0.csv'."""
    c1, j1 = W.write_sweep(tmp_path / "diode_0.5V", _result())
    c2, j2 = W.write_sweep(tmp_path / "diode_0.7V", _result())
    assert (c1.name, j1.name) == ("diode_0.5V.csv", "diode_0.5V.json")
    assert (c2.name, j2.name) == ("diode_0.7V.csv", "diode_0.7V.json")
    assert json.loads(j1.read_text())["csv"] == "diode_0.5V.csv"
    assert W.sweep_paths(tmp_path / "run1.csv") == (tmp_path / "run1.csv", tmp_path / "run1.json")
    assert W.sweep_paths(tmp_path / "run1.JSON")[0].name == "run1.csv"
    assert W.sweep_paths("a.b.c")[0].name == "a.b.c.csv"


def test_write_sweep_overwrite_is_opt_out(tmp_path):
    W.write_sweep(tmp_path / "r", _result())
    W.write_sweep(tmp_path / "r", _result())          # default: replace (as before)
    with pytest.raises(FileExistsError):
        W.write_sweep(tmp_path / "r", _result(), overwrite=False)
    c, j = W.write_sweep(tmp_path / "new", _result(), overwrite=False)
    assert c.exists() and j.exists()


def test_csv_keeps_fixed_decimals_for_small_currents(tmp_path):
    """HL-6: the CSV holds the device's text, never 1.234E-7 / 0E-10."""
    lines = ["PT 1,5,0,0,100,200,300,32768,0.000153,4,12,-34,1.2,0.7,-0.000051,0.0000001234,0x0380",
             "PT 1,6,1,1,100,200,300,32768,0.000000,1,0,0,-,-,0.000000,0.0000000000,0x0300"]
    c, j = W.write_sweep(tmp_path / "small", _result(lines, est_s="0.0000001"))
    rows = W.read_csv(c)
    assert [r["i_A"] for r in rows] == ["0.0000001234", "0.0000000000"]
    assert rows[1]["v_V"] == "0.000000"
    assert [",".join(r[k] for k in PT_FIELDS) for r in rows] == [x[3:] for x in lines]
    assert json.loads(j.read_text())["sweep"]["est_s"] == "0.0000001"
    doc = W.build_sidecar(cfg={"i_a": Decimal("0.0000000124"), "vk_v": None, "n": 3})
    assert doc["cfg"] == {"i_a": "0.0000000124", "vk_v": "-", "n": "3"}


def test_list_point_omitted_settle_uses_the_configuration():
    """HL-7: an omitted settle time is None (plain V), never 0."""
    assert S.ListPoint(Decimal("0.5")).token() == "0.5"
    assert S.list_add_commands([S.ListPoint(Decimal("0.5"), None), ("-0.5", None), ["1"],
                                ["0.25", 2000], (Decimal("2"), 100)]) == \
        ["SWP.LIST.ADD 0.5 -0.5 1 0.25:2000 2:100"]
    with pytest.raises(ValueError):
        S.ListPoint(Decimal("0.5"), 0).token()       # an explicit 0 is still invalid
    with pytest.raises(ValueError):
        S.as_list_point(("0.5", "2000.5"))           # inexact settle time


def test_session_zero_from_sweep_begin():
    assert C.session_zero({"zero": "valid", "o_v": "-112.40", "o_i": "387.10"}) == (-112.4, 387.1)
    assert C.session_zero({"zero": "stale", "o_v": "-112.40", "o_i": "387.10"}) is None
    assert C.session_zero({"zero": "none", "o_v": "-", "o_i": "-"}) is None
    assert C.session_zero({}) is None and C.session_zero(None) is None


def test_cal_offsets_never_fall_back_to_zero():
    """HL-9: without ZERO/gains the offsets come from the session zero, else error."""
    wf = C.CalibrationWorkflow(inst=None)
    with pytest.raises(ValueError):
        wf._offsets({"zero": "none", "o_v": "-", "o_i": "-"})
    assert wf._offsets({"zero": "valid", "o_v": "-112.40", "o_i": "387.10"}) == (-112.4, 387.1)
    codes = [-600000, 0, 600000]
    wf.v_fit = C.fit_gain_offset(codes, [1.5e-6 * (c - 12.0) for c in codes])
    wf.i_fit = C.fit_gain_offset(codes, [1.2e-8 * (c - 7.0) for c in codes])
    o_v, o_i = wf._offsets({"zero": "valid", "o_v": "-112.40", "o_i": "387.10"})
    assert o_v == pytest.approx(12.0, abs=1e-3) and o_i == pytest.approx(7.0, abs=1e-3)


def test_cal_dac_step_refuses_without_offsets(device, inst):
    """HL-9 (FakeDevice): no zero step, no gains, session zero 'none' -> refuse
    before anything is measured."""
    wf = C.CalibrationWorkflow(inst, ask=lambda p: "", say=lambda t: None)
    with pytest.raises(ValueError, match="zero"):
        wf.step_dac()
    assert not any(x.startswith(("SWP.", "ARM")) for x in device.received)


def test_cal_dac_step_uses_the_session_zero(device, inst):
    """HL-9 (FakeDevice): with a valid session zero the SWEEP_BEGIN offsets are used."""
    device.zero, device.zero_o_v, device.zero_o_i = "valid", "-112.40", "387.10"
    wf = C.CalibrationWorkflow(inst, ask=lambda p: "", say=lambda t: None)
    fit = wf.step_dac(v_max=1, step="0.25")
    recs = inst.last_sweep.records
    g_v = C.NOMINAL["g_v_uv"] * 1e-6
    expect = C.fit_dac([r.dac_code for r in recs],
                       [C.vsweep_from_vk(g_v * (r.v_raw + 112.4)) for r in recs])
    assert fit.c_dac == pytest.approx(expect.c_dac, abs=1e-9)
    biased = C.fit_dac([r.dac_code for r in recs],
                       [C.vsweep_from_vk(g_v * r.v_raw) for r in recs])
    assert abs(biased.c_dac - fit.c_dac) > 0.1       # what the old 0 fallback produced


def test_c_dac_window_mirrors_the_firmware():
    """The host check accepts what the firmware accepts (the DAC8552 worst
    case, about 615 codes) and refuses a dropped or extra digit."""
    nom = C.NOMINAL["c_dac"]
    assert C.C_DAC_WINDOW_CODES == 1000.0
    assert not C.check_plausible({"c_dac": nom + 616.3})
    assert not C.check_plausible({"c_dac": nom - 616.3})
    assert not C.check_plausible({"c_dac": nom - 999.0})
    assert C.check_plausible({"c_dac": nom + 1001.0})
    assert C.check_plausible({"c_dac": nom / 10.0})
    assert C.check_plausible({"c_dac": nom * 10.0})


def test_cal_info_reports_which_core_coefficients_are_still_nominal():
    """CAL-7: ``measured=`` is parsed; the five coefficients CAL_NOMINAL
    depends on are listed while unmeasured."""
    f = {"source": "stored", "seq": "3", "measured": "0x40", "r_s": "121.0000"}
    info = C.CalInfo.from_fields(f)
    assert info.measured == 0x40
    assert info.nominal_core == ["g_dac_uv", "c_dac", "g_v_uv", "g_i_na", "r_s"]
    assert C.CalInfo.from_fields({**f, "measured": "0x5F"}).nominal_core == []
    assert C.CalInfo.from_fields({**f, "measured": "0x4F"}).nominal_core == ["r_s"]
    assert C.CalInfo.from_fields({"source": "nominal"}).measured is None


def test_cal_apply_says_when_cal_nominal_remains(device, inst):
    """CAL-7: after a partial calibration the operator is told which core
    coefficients are still nominal (the FakeDevice tracks measured= like the
    firmware: a key set to a value other than its nominal one)."""
    said: list[str] = []
    wf = C.CalibrationWorkflow(inst, ask=lambda p: "y", say=said.append)
    wf.load_current()
    assert wf.apply({"r_s": "118.5"}) == 1
    assert any(t.startswith("Still nominal: g_dac_uv, c_dac, g_v_uv, g_i_na.") for t in said)
    said.clear()
    assert wf.apply({"g_dac_uv": "306.52", "c_dac": "32790.3", "g_v_uv": "1.4993",
                     "g_i_na": "12.4211"}) == 1
    assert not any(t.startswith("Still nominal") for t in said)


@pytest.mark.parametrize("failure", ["error", "session lost"])
def test_cal_apply_reports_but_does_not_fail_when_cal_cannot_be_read_back(device, inst,
                                                                          failure):
    """The record is saved; a failing CAL? afterwards (an error, or the
    session lost, e.g. the cable pulled) only loses the report."""
    said: list[str] = []
    wf = C.CalibrationWorkflow(inst, ask=lambda p: "y", say=said.append)
    wf.load_current()
    if failure == "error":
        device.override["CAL?"] = ["ERR INTERNAL flash busy"]
    else:
        real = device.handle

        def handle(line):
            if line.startswith("CAL?"):
                device.t.device_disconnect("USB cable pulled")
                return
            real(line)
        device.handle = handle
    assert wf.apply({"r_s": "118.5"}) == 1
    assert any(t.startswith("Could not read CAL? back") for t in said)


def test_cal_proposal_moves_a_fit_equal_to_nominal_one_step():
    """A fit that rounds to exactly the nominal value would count as nominal
    in the firmware; it is moved one step towards the unrounded fit, so at
    most one step from it (also where the unrounded nominal lies between the
    fit and its rounded value, as for g_v_uv)."""
    wf = C.CalibrationWorkflow(inst=None)
    wf.rs_fit = type("Fit", (), {"r_s": 121.00003})()
    assert wf.proposal() == {"r_s": Decimal("121.0001")}
    assert wf.nudged == {"r_s": Decimal("121.0001")}
    wf.rs_fit = type("Fit", (), {"r_s": 120.99996})()
    assert wf.proposal() == {"r_s": Decimal("120.9999")}
    wf.rs_fit = type("Fit", (), {"r_s": 118.71234})()
    assert wf.proposal() == {"r_s": Decimal("118.7123")} and wf.nudged == {}
    wf.rs_fit = None
    nominal_g_v = C.round_cal(g_v_uv=C.NOMINAL["g_v_uv"])["g_v_uv"]     # 1.498587
    assert Decimal(repr(C.NOMINAL["g_v_uv"])) < nominal_g_v
    for fit in (1.4985868, 1.49858699, 1.4985872):
        wf.v_fit = type("Fit", (), {"gain_uv": fit})()
        sent = wf.proposal()["g_v_uv"]
        assert sent != nominal_g_v
        assert abs(sent - Decimal(repr(fit))) <= Decimal("0.000001")   # one step at most


def test_cal_apply_notes_only_what_is_sent(device, inst):
    """The nudge note is for the value proposal() made; a value equal to its
    nominal one sent anyway is reported as counting as nominal."""
    said: list[str] = []
    wf = C.CalibrationWorkflow(inst, ask=lambda p: "y", say=said.append)
    wf.load_current()
    wf.rs_fit = type("Fit", (), {"r_s": 121.00003})()
    wf.proposal()
    assert wf.apply({"r_s": Decimal("121.0000")}) == 1
    assert not any("sent one step away" in t for t in said)
    assert any("will count it as nominal" in t for t in said)
    said.clear()
    assert wf.apply() == 1                   # the proposal itself: 121.0001
    assert any("sent one step away" in t for t in said)
