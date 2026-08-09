#!/usr/bin/env python3
"""
vendor_footprints.py — pin the footprints this design uses, by committing them.
==============================================================================

Why this exists
---------------
KiCad's *Update PCB from Schematic* reported, against a board built from this
repository::

    Warning: No net found for component J3 pad SH (no pin SH in symbol). (x4)
    Error:   J3 pad S1 not found in Connector_USB:USB_C_Receptacle_GCT_USB4085

Neither the symbol nor the design was wrong. Upstream renamed the USB-C shield
pads from ``S1`` to ``SH`` in commit ``a7f48c6c`` ("Connector_USB: rename shield
pads to SH", 2026-01-25), which first shipped in **KiCad library 10.0.0**. This
repository pins **9.0.9.1**, where they are still ``S1`` — but ``fp-lib-table``
resolved footprints through ``${KICADn_FOOTPRINT_DIR}``, i.e. through whatever
KiCad the reader happened to have installed. On a KiCad 10 machine the shield
lost its net and four pads went unconnected.

Prompt 2 fixed exactly this class of drift for *symbols*, by generating a
committed ``libraries/stage1_iv.kicad_sym`` that every schematic symbol resolves
to. Footprints were deliberately left as a runtime dependency. This is the bill
for that decision, so footprints get the same treatment.

What it does
------------
Copies **only the footprints this design actually uses** — read from the live
circuit, so it cannot vendor something unused or miss something used — from the
resolved upstream tree into ``libraries/footprints/<Nickname>.pretty/``. The
library nicknames are preserved, so **no footprint identifier changes**: parts
still say ``Connector_USB:USB_C_Receptacle_GCT_USB4085`` and the netlist is
untouched.

This is not "vendor the entire KiCad library set" — that remains forbidden. It
is ~30 files, each one a file the design names explicitly.

Refreshing to a newer upstream is a deliberate, reviewable act: bump the tag in
``tools/install_kicad_libs.sh``, re-run this, and the diff shows every pad that
moved or was renamed.

Usage::

    python3 tools/vendor_footprints.py            # copy / refresh
    python3 tools/vendor_footprints.py --check    # verify the committed copies
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO_ROOT)

from resolve_kicad_libs import (  # noqa: E402
    KicadLibraries, LibraryNotFound, PROJECT_FOOTPRINT_DIR,
)


def used_footprints():
    """{nickname: {name}} for every footprint the live circuit names."""
    import main as project_main
    _n, _p, circuit = project_main.build()
    out = {}
    for part in circuit.parts:
        fp_id = (part.footprint or "").strip()
        if ":" not in fp_id:
            continue
        nick, name = fp_id.split(":", 1)
        out.setdefault(nick, set()).add(name)
    return out


def plan(libs):
    """[(nickname, name, source_path, dest_path)] for everything to vendor."""
    rows = []
    for nick, names in sorted(used_footprints().items()):
        for name in sorted(names):
            src = libs.footprint_file(nick, name)
            if src is None:
                raise LibraryNotFound(
                    f"{nick}:{name} does not exist in the upstream footprint "
                    f"library at {libs.footprint_dir}. Install the pinned "
                    f"revision with tools/install_kicad_libs.sh.")
            dest = os.path.join(PROJECT_FOOTPRINT_DIR, f"{nick}.pretty",
                                f"{name}.kicad_mod")
            rows.append((nick, name, src, dest))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--check", action="store_true",
                    help="verify the committed footprints match upstream")
    args = ap.parse_args(argv)

    try:
        # Read from upstream only: vendoring from the vendored copy would be
        # a no-op that could never detect drift.
        libs = KicadLibraries(use_vendored_footprints=False)
    except LibraryNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 2

    rows = plan(libs)

    if args.check:
        problems = []
        for nick, name, src, dest in rows:
            if not os.path.isfile(dest):
                problems.append(f"missing: {nick}:{name}")
                continue
            with open(src, encoding="utf-8") as a, \
                    open(dest, encoding="utf-8") as b:
                if a.read() != b.read():
                    problems.append(
                        f"differs from upstream: {nick}:{name} — the installed "
                        f"library is not the revision this repository vendored")
        # Anything vendored that the design no longer uses is also a failure:
        # a stale footprint is a footprint nobody is checking.
        wanted = {os.path.abspath(d) for _n, _m, _s, d in rows}
        for root, _dirs, files in os.walk(PROJECT_FOOTPRINT_DIR):
            for f in files:
                if f.endswith(".kicad_mod") and \
                        os.path.abspath(os.path.join(root, f)) not in wanted:
                    problems.append(f"vendored but unused: "
                                    f"{os.path.relpath(os.path.join(root, f), REPO_ROOT)}")
        if problems:
            for p in problems:
                print(f"FAIL {p}", file=sys.stderr)
            print("\nRe-run `python3 tools/vendor_footprints.py`.",
                  file=sys.stderr)
            return 1
        print(f"OK  {len(rows)} vendored footprints match the installed "
              f"library at {libs.footprint_dir}")
        return 0

    if os.path.isdir(PROJECT_FOOTPRINT_DIR):
        shutil.rmtree(PROJECT_FOOTPRINT_DIR)
    for nick, name, src, dest in rows:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(src, dest)
    nicks = sorted({n for n, _m, _s, _d in rows})
    print(f"vendored {len(rows)} footprints from {len(nicks)} libraries into "
          f"{os.path.relpath(PROJECT_FOOTPRINT_DIR, REPO_ROOT)}")
    for nick in nicks:
        count = sum(1 for n, _m, _s, _d in rows if n == nick)
        print(f"  {nick}.pretty  {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
