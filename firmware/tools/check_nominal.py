#!/usr/bin/env python3
"""
check_nominal.py — the firmware's nominal component values must match the
SKiDL design.

firmware/src/core/nominal.h holds the component values the nominal scaling
constants are computed from. Every line of the form

    #define NOM_<NAME> <value>   /* @ref <REF> blocks/<file>.py */

names the part it was copied from. This script finds ``ref="<REF>"`` in that
block, parses its ``value="..."`` (E-series notation: 2.49k, 10.0R, 1M ...)
and fails if the numbers differ, or if a reference or a part cannot be found.
Lines without ``@ref`` are datasheet constants and are only listed.

Run from anywhere::

    python3 firmware/tools/check_nominal.py [--verbose]

Exit status 0 = every referenced value matches.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
NOMINAL_H = REPO / "firmware" / "src" / "core" / "nominal.h"

DEFINE_RE = re.compile(
    r"^\s*#define\s+(NOM_\w+)\s+([-+0-9.eE]+)\s*(?:/\*(.*?)\*/)?\s*$")
REF_RE = re.compile(r"@ref\s+(\S+)\s+(\S+)")
PREFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "R": 1.0, "k": 1e3, "K": 1e3,
          "M": 1e6, "G": 1e9}


def parse_value(text: str) -> float:
    """First token of a SKiDL value string in E-series notation."""
    tok = text.strip().split()[0]
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)([pnumRkKMG])?([0-9]*)", tok)
    if not m:
        m = re.fullmatch(r"([0-9]+)([pnumRkKMG])([0-9]+)", tok)
        if not m:
            raise ValueError(f"cannot parse component value {text!r}")
    head, prefix, tail = m.group(1), m.group(2), m.group(3)
    if tail:
        number = float(f"{head}.{tail}")      # 4k7 style
    else:
        number = float(head)
    return number * PREFIX.get(prefix or "R", 1.0)


def find_part_value(block: Path, ref: str, label: str) -> float:
    src = block.read_text(encoding="utf-8")
    m = re.search(r'ref\s*=\s*"' + re.escape(ref) + r'"(.*?)\)', src, re.S)
    if not m:
        raise LookupError(f"{ref} not found in {label}")
    v = re.search(r'value\s*=\s*"([^"]+)"', m.group(1))
    if not v:
        raise LookupError(f"{ref} in {label} has no value=")
    return parse_value(v.group(1))


def check(nominal_h: Path = NOMINAL_H, repo: Path = REPO, verbose: bool = False) -> list[str]:
    errors: list[str] = []
    n_checked = 0
    for lineno, line in enumerate(nominal_h.read_text(encoding="utf-8").splitlines(), 1):
        m = DEFINE_RE.match(line)
        if not m:
            continue
        name, value, comment = m.group(1), float(m.group(2)), m.group(3) or ""
        ref = REF_RE.search(comment)
        if not ref:
            if verbose:
                print(f"  {name} = {value:g}  (datasheet constant, not checked)")
            continue
        part, rel = ref.group(1), ref.group(2)
        block = repo / rel
        if not block.is_file():
            errors.append(f"nominal.h:{lineno}: {name}: {rel} does not exist")
            continue
        try:
            design = find_part_value(block, part, rel)
        except (LookupError, ValueError) as exc:
            errors.append(f"nominal.h:{lineno}: {name}: {exc}")
            continue
        n_checked += 1
        if abs(design - value) > 1e-9 * max(abs(design), 1.0):
            errors.append(f"nominal.h:{lineno}: {name} = {value:g} but {part} in {rel} is {design:g}")
        elif verbose:
            print(f"  {name} = {value:g}  matches {part} ({rel})")
    if n_checked == 0:
        errors.append("no @ref lines found in nominal.h (format changed?)")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    errors = check(verbose=args.verbose)
    for e in errors:
        print("ERROR:", e)
    if errors:
        return 1
    print("nominal.h matches the SKiDL blocks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
