"""PT record parsing, flags, gap detection and u32 time unwrapping."""

from decimal import Decimal

import pytest

from stage1iv.errors import ProtocolError
from stage1iv.records import (Flag, PTRecord, SeqTracker, decode_flags, find_gaps, parse_pt,
                              unwrap_u32)

LINE = ("PT 1,2,1,1,55100,56300,57500,34400,0.500085,2,36316,299696,1.4,3.5,"
        "0.447612,0.0004474407,0x0300")


def test_parse_every_field():
    r = parse_pt(LINE)
    assert (r.sweep_id, r.seq, r.idx, r.seg) == (1, 2, 1, 1)
    assert (r.t_set_us, r.t_i_us, r.t_v_us) == (55100, 56300, 57500)
    assert r.dac_code == 34400 and r.v_set_v == Decimal("0.500085")
    assert r.n == 2 and r.i_raw == 36316 and r.v_raw == 299696
    assert r.i_sd == Decimal("1.4") and r.v_sd == Decimal("3.5")
    assert r.v_v == Decimal("0.447612") and r.i_a == Decimal("0.0004474407")
    assert r.flags == 0x0300
    assert r.to_line() == LINE


def test_missing_sd_and_flags():
    r = parse_pt("PT 1,1,0,0,1,2,3,32768,0.000153,1,491,2287,-,-,0.003427,0.0000061059,0x0384")
    assert r.i_sd is None and r.v_sd is None
    assert r.has(Flag.FIRST) and r.has(Flag.CLIM)
    assert not r.valid_data
    assert decode_flags(0x0384) == ["CLIM", "FIRST", "CAL_NOMINAL", "ZERO_STALE"]


@pytest.mark.parametrize("bad", [
    "PT 1,2,3", LINE.replace("0x0300", "0x300"), LINE.replace(",2,1,1,", ",2,x,1,"),
    LINE + ",9",
])
def test_malformed_rejected(bad):
    with pytest.raises(ProtocolError):
        parse_pt(bad)


def test_gaps():
    assert find_gaps([1, 2, 3, 6, 7, 9]) == [(4, 5), (8, 8)]
    t = SeqTracker()
    assert t.feed(10) is None
    assert t.feed(11) is None
    assert t.feed(14) == (12, 13)


def test_unwrap_u32():
    w = 2 ** 32
    assert unwrap_u32([w - 10, w - 1, 5, 100]) == [w - 10, w - 1, w + 5, w + 100]
