"""Tests for firmware/tools/check_nominal.py (run: python3 -m pytest firmware/tools/tests)."""

import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("check_nominal", HERE.parent / "check_nominal.py")
cn = importlib.util.module_from_spec(spec)
sys.modules["check_nominal"] = cn
spec.loader.exec_module(cn)


@pytest.mark.parametrize("text,value", [
    ("2.49k 0.1%", 2490.0), ("10.0R 0.1% 3W", 10.0), ("100R", 100.0), ("1M", 1e6),
    ("4k7", 4700.0), ("470n", 470e-9), ("35.7k 0.1%", 35700.0), ("0R", 0.0),
])
def test_parse_value(text, value):
    assert cn.parse_value(text) == pytest.approx(value, rel=1e-12)


def test_parse_value_rejects_garbage():
    with pytest.raises(ValueError):
        cn.parse_value("ten ohms")


def test_repository_nominal_matches_design():
    assert cn.check() == []


def _fake_repo(tmp_path, h_value, block_value):
    (tmp_path / "blocks").mkdir()
    (tmp_path / "blocks" / "x.py").write_text(
        f'R7 = R()(ref="R7", tag="R7", value="{block_value}")\n')
    h = tmp_path / "nominal.h"
    h.write_text(f"#define NOM_R7_OHM {h_value}  /* @ref R7 blocks/x.py */\n"
                 "#define NOM_CONST 1.0  /* datasheet */\n")
    return h


def test_mismatch_is_reported(tmp_path):
    h = _fake_repo(tmp_path, "1000.0", "1.1k 1%")
    errs = cn.check(h, tmp_path)
    assert len(errs) == 1 and "NOM_R7_OHM" in errs[0] and "1100" in errs[0]


def test_match_passes(tmp_path):
    assert cn.check(_fake_repo(tmp_path, "1100.0", "1.10k 1%"), tmp_path) == []


def test_missing_part_and_file(tmp_path):
    h = _fake_repo(tmp_path, "1.0", "1R")
    h.write_text("#define NOM_A 1.0 /* @ref R99 blocks/x.py */\n"
                 "#define NOM_B 1.0 /* @ref R1 blocks/nope.py */\n")
    errs = cn.check(h, tmp_path)
    assert "R99 not found" in errs[0] and "does not exist" in errs[1]
    assert len(errs) == 3 and "no @ref" in errs[2]   # nothing could be checked


def test_no_refs_is_an_error(tmp_path):
    h = tmp_path / "nominal.h"
    h.write_text("#define NOM_A 1.0\n")
    assert cn.check(h, tmp_path) != []
