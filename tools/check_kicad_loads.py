#!/usr/bin/env python3
"""
check_kicad_loads.py — hand the generated files to KiCad and see if it can read them.
=====================================================================================

Every other check in this repository validates the output with *this project's*
parser. That proves the emitter is self-consistent; it does not prove KiCad
accepts the result. It did not: ``libraries/stage1_iv.kicad_sym`` was emitted
with a leading ``;`` comment block and a KiCad-9-only ``(generator_version)``
field over v6/v7 symbol bodies, and KiCad refused to load the library — every
one of the 118 symbol lookups then reported "symbol library not found", which
is what the schematic reviewer saw.

Nothing here re-implements a rule. It runs ``kicad-cli`` and believes it:

    kicad-cli sym export svg     the symbol library parses and every symbol
                                 renders
    kicad-cli sch export netlist the schematic parses and KiCad can read its
                                 connectivity

Both are skipped, loudly, when ``kicad-cli`` is not installed — never reported
as a pass.

Usage::

    python3 tools/check_kicad_loads.py [--sym LIB] [--sch SCH]

Exit codes: 0 all checks passed, 1 KiCad rejected a file, 3 kicad-cli absent.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

DEFAULT_SYM = os.path.join(REPO_ROOT, "libraries", "stage1_iv.kicad_sym")
DEFAULT_SCH = os.path.join(REPO_ROOT, "stage1_iv.kicad_sch")


def _symbol_names(path):
    """Top-level symbol names in a .kicad_sym, for the expected-count check."""
    sys.path.insert(0, HERE)
    from resolve_kicad_libs import parse_sexp
    with open(path, encoding="utf-8") as fh:
        tree = parse_sexp(fh.read())
    names = []
    for node in tree:
        if isinstance(node, list) and node and node[0] == "kicad_symbol_lib":
            for child in node:
                if isinstance(child, list) and child and child[0] == "symbol":
                    names.append(str(child[1]))
    return names


def check_symbol_library(kicad_cli, path):
    expected = _symbol_names(path)
    tmp = tempfile.mkdtemp(prefix="kicad-symcheck-")
    try:
        proc = subprocess.run(
            [kicad_cli, "sym", "export", "svg", "--output", tmp, path],
            capture_output=True, text=True)
        rendered = len([f for f in os.listdir(tmp) if f.endswith(".svg")])
        out = (proc.stdout + proc.stderr).strip()
        if rendered == 0:
            print(f"  [FAIL] KiCad could NOT load {os.path.relpath(path, REPO_ROOT)}")
            print(f"         kicad-cli said: {out or '(no message)'}")
            print("         The library is unreadable, so every symbol lookup "
                  "in the schematic will\n"
                  "         report 'symbol library not found'. Common causes: a "
                  "leading ';' comment\n"
                  "         block (the file must start with '(kicad_symbol_lib'), "
                  "a (generator_version)\n"
                  "         field, or a (version) stamp newer than the syntax "
                  "of the symbol bodies.")
            return False
        if rendered != len(expected):
            print(f"  [FAIL] KiCad rendered {rendered} of {len(expected)} "
                  f"symbols in {os.path.relpath(path, REPO_ROOT)}")
            return False
        print(f"  [ OK ] KiCad loaded {os.path.relpath(path, REPO_ROOT)} and "
              f"rendered all {rendered} symbols")
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_schematic(kicad_cli, path):
    tmp = tempfile.mkdtemp(prefix="kicad-schcheck-")
    out_net = os.path.join(tmp, "export.net")
    try:
        proc = subprocess.run(
            [kicad_cli, "sch", "export", "netlist", "--output", out_net, path],
            capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.isfile(out_net):
            print(f"  [FAIL] KiCad could NOT read "
                  f"{os.path.relpath(path, REPO_ROOT)}")
            print(f"         {(proc.stdout + proc.stderr).strip()}")
            return False
        n = len(re.findall(r"\(comp ", open(out_net, encoding="utf-8").read()))
        print(f"  [ OK ] KiCad read {os.path.relpath(path, REPO_ROOT)} "
              f"({n} components)")
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--sym", default=DEFAULT_SYM)
    ap.add_argument("--sch", default=DEFAULT_SCH)
    args = ap.parse_args(argv)

    print("=" * 70)
    print("KiCad can read the generated files")
    print("=" * 70)

    kicad_cli = shutil.which("kicad-cli")
    if not kicad_cli:
        print("  SKIPPED — kicad-cli is not installed. The generated files have")
        print("  NOT been shown to be readable by KiCad. This is not a pass.")
        return 3
    version = subprocess.run([kicad_cli, "version"], capture_output=True,
                             text=True).stdout.strip()
    print(f"  kicad-cli {version}")

    ok = check_symbol_library(kicad_cli, args.sym)
    ok = check_schematic(kicad_cli, args.sch) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
