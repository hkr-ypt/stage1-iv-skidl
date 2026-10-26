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
import math
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


# Footprints the BOARD uses that no schematic symbol names. Mounting holes are
# mechanical: they have no symbol, so the circuit does not mention them, but
# generators/pcb.py places them and KiCad checks them against the library like
# any other footprint.
#   MountingHole_3.2mm_M3_Pad ............ generators/pcb.py's auto-placed
#                                           bootstrap board
#   stage1_iv:MountingHole_3.2mm_M3_Isolated  the routed board of record
#                                           (layout/final_corrections.eco)
BOARD_ONLY_FOOTPRINTS = ["MountingHole:MountingHole_3.2mm_M3_Pad",
                         "stage1_iv:MountingHole_3.2mm_M3_Isolated"]


def _circle_pts(r, n=32, per_line=4, indent="\t\t\t\t"):
    """Polygon points of an n-gon of circumradius r, KiCad layout."""
    pts = [f"(xy {r * math.cos(2 * math.pi * i / n):.6f} "
           f"{r * math.sin(2 * math.pi * i / n):.6f})" for i in range(n)]
    pts = [p.replace("-0.000000", "0.000000") for p in pts]
    return "\n".join(indent + " ".join(pts[i:i + per_line])
                     for i in range(0, n, per_line))


# Back courtyard and a 4.0 mm copper keepout for the isolated M3 hole below.
_M3_KEEPOUT = (
    "\t(fp_circle\n\t\t(center 0 0)\n\t\t(end 4.25 0)\n"
    "\t\t(stroke\n\t\t\t(width 0.05)\n\t\t\t(type solid)\n\t\t)\n"
    "\t\t(fill no)\n\t\t(layer \"B.CrtYd\")\n\t)\n"
    "\t(zone\n\t\t(net 0)\n\t\t(net_name \"\")\n"
    "\t\t(layers \"F.Cu\" \"B.Cu\")\n"
    "\t\t(uuid \"5f0c3c3e-6d1a-4c1e-9a52-2b1e3a7d4c01\")\n"
    "\t\t(name \"M3 hardware keepout\")\n"
    "\t\t(hatch edge 0.5)\n"
    "\t\t(connect_pads\n\t\t\t(clearance 0)\n\t\t)\n"
    "\t\t(min_thickness 0.25)\n\t\t(filled_areas_thickness no)\n"
    "\t\t(keepout\n\t\t\t(tracks not_allowed)\n\t\t\t(vias not_allowed)\n"
    "\t\t\t(pads allowed)\n\t\t\t(copperpour not_allowed)\n"
    "\t\t\t(footprints allowed)\n\t\t)\n"
    "\t\t(placement\n\t\t\t(enabled no)\n\t\t\t(sheetname \"\")\n\t\t)\n"
    "\t\t(fill\n\t\t\t(thermal_gap 0.5)\n\t\t\t(thermal_bridge_width 0.5)\n\t\t)\n"
    "\t\t(polygon\n\t\t\t(pts\n" + _circle_pts(4.0) + "\n\t\t\t)\n\t\t)\n\t)\n")


# PROJECT FOOTPRINTS — derived, never hand-drawn.
# A footprint the design needs that upstream does not ship is written here as
# "this upstream footprint, with these changes", and generated from the
# VENDORED copy of that upstream footprint (which --check still compares
# against upstream). The base stays vendored: tools/reproduce_reference.py
# rebuilds the original routed board, which uses it.
PROJECT_FOOTPRINT_NICK = "stage1_iv"
PROJECT_FOOTPRINTS = {
    "TO-220-7_P2.54x3.7mm_StaggerOdd_Lead3.8mm_Vertical_Drill1.2mm": {
        "base": ("Package_TO_SOT_THT:"
                 "TO-220-7_P2.54x3.7mm_StaggerOdd_Lead3.8mm_Vertical"),
        "replace": [("(drill 1.1)", "(drill 1.2)")],
        "count": 7,
        "why": ("OPA548T (TI KVT): leads up to 0.91 x 0.56 mm, a 1.07 mm "
                "diagonal; upstream's 1.1 mm hole leaves 0.03 mm at maximum "
                "material. Holes opened to 1.2 mm; pads (1.8 mm) unchanged, "
                "annular ring 0.30 mm."),
    },
    # Replacements may carry their own expected count as a third element.
    "MountingHole_3.2mm_M3_Isolated": {
        "base": "MountingHole:MountingHole_3.2mm_M3",
        "replace": [
            # courtyard 3.45 -> 4.25 mm, on BOTH sides (head on top,
            # nut / standoff underneath)
            ("(end 3.45 0)", "(end 4.25 0)", 1),
            ("(at 0 -4.15 0)", "(at 0 -5.25 0)", 1),
            ("(at 0 4.15 0)", "(at 0 5.25 0)", 1),
            # designator on F.Fab; "board_only": no symbol, by design, so
            # KiCad's schematic parity does not report it as extra
            ("(layer \"F.SilkS\")", "(layer \"F.Fab\")", 1),
            ("(attr exclude_from_pos_files exclude_from_bom)",
             "(attr board_only exclude_from_pos_files exclude_from_bom)", 1),
            ("\t(embedded_fonts no)\n",
             _M3_KEEPOUT + "\t(embedded_fonts no)\n", 1),
        ],
        "count": 1,
        "why": ("NPTH M3 hole with no annular ring, plus a 4.0 mm-radius "
                "keepout on F.Cu and B.Cu (no track, via or pour) and a "
                "4.25 mm courtyard on both sides. An M3 washer (7.0 mm, "
                "DIN 125) or a 5.5 mm hex standoff (6.35 mm across corners) "
                "fits inside 7.0 mm, so the hardware cannot touch copper of "
                "any net: the chassis is not bonded to AGND or DGND here."),
    },
}


def project_footprint_text(name, vendored_dir=None):
    """The generated .kicad_mod text of one PROJECT_FOOTPRINTS entry."""
    spec = PROJECT_FOOTPRINTS[name]
    nick, base = spec["base"].split(":", 1)
    src = os.path.join(vendored_dir or PROJECT_FOOTPRINT_DIR, f"{nick}.pretty",
                       f"{base}.kicad_mod")
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    head = f'(footprint "{base}"'
    if head not in text:
        raise LibraryNotFound(f"{src}: no {head} header to rename")
    text = text.replace(head, f'(footprint "{name}"', 1)
    for rep in spec["replace"]:
        old, new = rep[0], rep[1]
        want = rep[2] if len(rep) > 2 else spec["count"]
        n = text.count(old)
        if n != want:
            raise LibraryNotFound(
                f"{spec['base']}: expected {want} x {old!r}, found "
                f"{n}. Upstream changed; re-derive {name} deliberately.")
        text = text.replace(old, new)
    # Say what this file is, in the file.
    text = text.replace('(descr "', '(descr "PROJECT FOOTPRINT, generated by '
                        'tools/vendor_footprints.py from ' + spec["base"] +
                        ': ' + spec["why"] + ' Upstream: ', 1)
    return text


def used_footprints():
    """{nickname: {name}} for every footprint the design or the board names."""
    import main as project_main
    _n, _p, circuit = project_main.build()
    out = {}
    for fp_id in BOARD_ONLY_FOOTPRINTS:
        nick, name = fp_id.split(":", 1)
        out.setdefault(nick, set()).add(name)
    for part in circuit.parts:
        fp_id = (part.footprint or "").strip()
        if ":" not in fp_id:
            continue
        nick, name = fp_id.split(":", 1)
        out.setdefault(nick, set()).add(name)
    # The upstream base of every project footprint stays vendored.
    for spec in PROJECT_FOOTPRINTS.values():
        nick, name = spec["base"].split(":", 1)
        out.setdefault(nick, set()).add(name)
    # So does every footprint the original routed board names:
    # tools/reproduce_reference.py rebuilds that board from these copies,
    # including parts the design has since changed (R10 was a DIN0207).
    for fp_id in reference_board_footprints():
        nick, name = fp_id.split(":", 1)
        out.setdefault(nick, set()).add(name)
    return out


def reference_board_footprints():
    """Footprint identifiers on the original routed board (unipolar-routed/)."""
    from generators.layout import REFERENCE_BOARD
    import re
    with open(REFERENCE_BOARD, encoding="utf-8") as fh:
        text = fh.read()
    return sorted(set(re.findall(r'^\t\(footprint "([^"]+:[^"]+)"', text,
                                 re.M)))


def plan(libs):
    """[(nickname, name, source_path, dest_path)] for everything to vendor."""
    rows = []
    for nick, names in sorted(used_footprints().items()):
        if nick == PROJECT_FOOTPRINT_NICK:
            unknown = sorted(set(names) - set(PROJECT_FOOTPRINTS))
            if unknown:
                raise LibraryNotFound(
                    f"{nick}:{unknown[0]} is used but is not declared in "
                    f"tools/vendor_footprints.py PROJECT_FOOTPRINTS")
            for name in sorted(names):
                dest = os.path.join(PROJECT_FOOTPRINT_DIR, f"{nick}.pretty",
                                    f"{name}.kicad_mod")
                rows.append((nick, name, None, dest))
            continue
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
            if src is None:
                with open(dest, encoding="utf-8") as b:
                    if b.read() != project_footprint_text(name):
                        problems.append(
                            f"project footprint {nick}:{name} is not what its "
                            f"declared base + changes generate")
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
        if src is None:
            continue                       # derived below, from the copies
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(src, dest)
    for nick, name, src, dest in rows:
        if src is not None:
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(project_footprint_text(name))
    nicks = sorted({n for n, _m, _s, _d in rows})
    print(f"vendored {len(rows)} footprints from {len(nicks)} libraries into "
          f"{os.path.relpath(PROJECT_FOOTPRINT_DIR, REPO_ROOT)}")
    for nick in nicks:
        count = sum(1 for n, _m, _s, _d in rows if n == nick)
        print(f"  {nick}.pretty  {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
