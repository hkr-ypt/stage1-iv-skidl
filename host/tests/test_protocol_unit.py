"""Line classification, payload parsing and exact number formatting."""

from decimal import Decimal

import pytest

from stage1iv import protocol as P


def test_classify():
    assert P.classify("OK") == P.Kind.OK
    assert P.classify("ERR RANGE x") == P.Kind.ERR
    assert P.classify("EVT STATE ARMED") == P.Kind.EVT
    assert P.classify("PT 1,2,3") == P.Kind.PT
    assert P.classify("# log") == P.Kind.LOG


def test_payload_preserves_order_and_converts():
    p = P.parse_payload("STAT state=ARMED warn=0x0206 vset_v=1.500000 zero_age_s=- dac=32768")
    vals = p.values()
    assert list(vals)[:2] == ["state", "warn"]
    assert vals["warn"] == 0x206
    assert vals["vset_v"] == Decimal("1.500000")
    assert vals["dac"] == 32768
    assert vals["zero_age_s"] is None


def test_err_parse():
    assert P.parse_err("ERR PRECOND bor") == ("PRECOND", "bor")


@pytest.mark.parametrize("value,text", [
    (Decimal("2.5"), "2.5"), ("-0.000125", "-0.000125"), (10, "10"),
    ("1e-3", "0.001"), (0, "0"),
])
def test_format_volts_exact(value, text):
    assert P.format_volts(value) == text


@pytest.mark.parametrize("bad", ["1.0000001", 1.0000001, float("nan"), float("inf"), "abc"])
def test_format_volts_rejects_inexact_and_garbage(bad):
    with pytest.raises(ValueError):
        P.format_volts(bad)


def test_format_amps_resolution():
    assert P.format_amps("0.001") == "0.001"
    with pytest.raises(ValueError):
        P.format_amps("1e-10")


def test_cfg_args_and_line_limits():
    args = P.format_cfg_args(adc_rate=2.5, n_avg=4, v_limit="5", on_compliance="fault")
    assert args == ["adc_rate=2.5", "n_avg=4", "v_limit=5", "on_compliance=fault"]
    with pytest.raises(ValueError):
        P.format_cfg_args(adc_rate=2.55)
    with pytest.raises(ValueError):
        P.check_line("X" * 300)


def test_deferred_verbs_include_cal_save():
    for v in ("MEAS?", "ZERO", "ADC.SELFCAL", "SELFTEST", "CAL.SAVE"):
        assert P.is_deferred(v + " X=1")
    assert not P.is_deferred("PING")
