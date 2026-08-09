#!/usr/bin/env python3
"""
measure_pad_gaps.py — the minimum pad-to-pad gap in every footprint used.
=========================================================================

The board's global minimum clearance cannot exceed the tightest intrinsic pad
gap of any footprint on it, or those footprints are un-buildable as drawn and
DRC reports a clearance error that no routing decision can fix.

This measures that number from the footprint files rather than taking it from a
datasheet or a DRC report: for every footprint the design uses, it computes the
edge-to-edge distance between every pair of pads on a shared copper layer and
reports the minimum.

Rectangular/roundrect/oval pads are treated as their bounding rectangle, which
*understates* the gap for rounded corners — i.e. the answer errs on the safe
side. Pads sharing a pad number (a thermal pad split into several, or the four
USB shell tabs) are one electrical pad and are skipped.

Usage::

    python3 tools/measure_pad_gaps.py [--json]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO_ROOT)

from resolve_kicad_libs import KicadLibraries, LibraryNotFound, parse_sexp  # noqa: E402


def _head(node):
    return node[0] if isinstance(node, list) and node and isinstance(node[0], str) else ""


def _kids(node, head):
    return [c for c in node if isinstance(c, list) and _head(c) == head]


def _first(node, head):
    k = _kids(node, head)
    return k[0] if k else None


def _floats(node, n):
    out = []
    for tok in node[1:1 + n]:
        try:
            out.append(float(tok))
        except (TypeError, ValueError):
            out.append(0.0)
    return out + [0.0] * (n - len(out))


def pads_of(path):
    """[(number, x, y, w, h, rot, {layers})] for every pad in a .kicad_mod."""
    with open(path, encoding="utf-8") as fh:
        tree = parse_sexp(fh.read())
    fp = _first(tree, "footprint") or _first(tree, "module")
    if fp is None:
        return []
    out = []
    for pad in _kids(fp, "pad"):
        num = str(pad[1]) if len(pad) > 1 else ""
        at = _first(pad, "at")
        size = _first(pad, "size")
        layers = _first(pad, "layers")
        if at is None or size is None:
            continue
        x, y, rot = (*_floats(at, 2), (float(at[3]) if len(at) > 3 else 0.0))
        w, h = _floats(size, 2)
        lset = {str(t) for t in (layers[1:] if layers is not None else [])}
        out.append((num, x, y, w, h, rot, lset))
    return out


def drills_of(path):
    """[(number, x, y, dx, dy)] for every *drilled* pad in a .kicad_mod.

    ``dx``/``dy`` are the hole's extents: equal for a round drill, different
    for ``(drill oval dx dy)``. Pads with no ``(drill ...)`` are SMD and are
    skipped — they have no hole to clash with.
    """
    with open(path, encoding="utf-8") as fh:
        tree = parse_sexp(fh.read())
    fp = _first(tree, "footprint") or _first(tree, "module")
    if fp is None:
        return []
    out = []
    for pad in _kids(fp, "pad"):
        at = _first(pad, "at")
        drill = _first(pad, "drill")
        if at is None or drill is None:
            continue
        nums = []
        for tok in drill[1:]:
            if isinstance(tok, list):
                break                       # (offset x y) — not a diameter
            try:
                nums.append(float(tok))
            except (TypeError, ValueError):
                continue                    # the literal "oval"
        if not nums:
            continue
        dx = nums[0]
        dy = nums[1] if len(nums) > 1 else nums[0]
        x, y = _floats(at, 2)
        out.append((str(pad[1]) if len(pad) > 1 else "", x, y, dx, dy))
    return out


def min_hole_gap(path):
    """Smallest edge-to-edge distance between two holes, and the pair."""
    holes = drills_of(path)
    best, pair = None, None
    for i in range(len(holes)):
        for j in range(i + 1, len(holes)):
            a, b = holes[i], holes[j]
            if a[0] == b[0]:
                continue                    # same electrical pad
            # Oval holes are treated as their bounding rectangle, which
            # understates the gap — the answer errs on the safe side.
            dx = max(abs(a[1] - b[1]) - (a[3] + b[3]) / 2, 0.0)
            dy = max(abs(a[2] - b[2]) - (a[4] + b[4]) / 2, 0.0)
            g = math.hypot(dx, dy)
            if best is None or g < best:
                best, pair = g, (a[0], b[0])
    return best, pair, len(holes)


def _bbox(x, y, w, h, rot):
    """Axis-aligned bounding box of a rotated rectangular pad."""
    a = math.radians(rot % 180.0)
    ew = abs(w * math.cos(a)) + abs(h * math.sin(a))
    eh = abs(w * math.sin(a)) + abs(h * math.cos(a))
    return x - ew / 2, y - eh / 2, x + ew / 2, y + eh / 2


def _gap(p, q):
    ax0, ay0, ax1, ay1 = _bbox(*p[1:6])
    bx0, by0, bx1, by1 = _bbox(*q[1:6])
    dx = max(bx0 - ax1, ax0 - bx1, 0.0)
    dy = max(by0 - ay1, ay0 - by1, 0.0)
    return math.hypot(dx, dy)


def _share_copper(a, b):
    cu = {"F.Cu", "B.Cu", "*.Cu"}
    la, lb = a[6], b[6]
    if "*.Cu" in la or "*.Cu" in lb:
        return True
    return bool((la & cu) & (lb & cu))


def min_gap(path):
    pads = pads_of(path)
    best, pair = None, None
    for i in range(len(pads)):
        for j in range(i + 1, len(pads)):
            a, b = pads[i], pads[j]
            if a[0] == b[0]:
                continue            # same electrical pad
            if not _share_copper(a, b):
                continue
            g = _gap(a, b)
            if best is None or g < best:
                best, pair = g, (a[0], b[0])
    return best, pair, len(pads)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        libs = KicadLibraries()
    except LibraryNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 2

    import main as project_main
    _n, _p, circuit = project_main.build()

    used = {}
    for part in circuit.parts:
        fp_id = (part.footprint or "").strip()
        if fp_id:
            used.setdefault(fp_id, []).append(part.ref)

    rows = []
    for fp_id, refs in sorted(used.items()):
        fp = libs.get_footprint(fp_id)
        if fp is None:
            continue
        gap, pair, npads = min_gap(fp.file)
        hgap, hpair, nholes = min_hole_gap(fp.file)
        rows.append({"footprint": fp_id, "refs": sorted(refs), "pads": npads,
                     "min_gap_mm": None if gap is None else round(gap, 4),
                     "between": list(pair) if pair else None,
                     "holes": nholes,
                     "min_hole_gap_mm": None if hgap is None else round(hgap, 4),
                     "holes_between": list(hpair) if hpair else None,
                     "file": os.path.basename(fp.file)})

    measured = [r for r in rows if r["min_gap_mm"] is not None]
    tightest = min(measured, key=lambda r: r["min_gap_mm"]) if measured else None
    drilled = [r for r in rows if r["min_hole_gap_mm"] is not None]
    tightest_hole = (min(drilled, key=lambda r: r["min_hole_gap_mm"])
                     if drilled else None)

    if args.json:
        print(json.dumps({"footprints": rows, "tightest": tightest,
                          "tightest_hole": tightest_hole}, indent=2))
        return 0

    print("=" * 78)
    print("Intrinsic pad-to-pad gap, measured from the footprint files")
    print("=" * 78)
    print(f"  {'footprint':<52}{'pads':>5}{'min gap':>10}  between")
    for r in sorted(rows, key=lambda r: (r["min_gap_mm"] is None, r["min_gap_mm"])):
        g = "n/a" if r["min_gap_mm"] is None else f"{r['min_gap_mm']:.3f} mm"
        pair = "/".join(r["between"]) if r["between"] else "-"
        print(f"  {r['footprint']:<52}{r['pads']:>5}{g:>10}  {pair}")
    if tightest:
        print()
        print(f"  TIGHTEST: {tightest['min_gap_mm']:.3f} mm in "
              f"{tightest['footprint']}")
        print(f"            (pads {'/'.join(tightest['between'])}, used by "
              f"{', '.join(tightest['refs'])})")
        print()
        print("  The board's global minimum clearance must be <= this value, or")
        print("  these footprints are un-buildable as drawn. See")
        print("  docs/FAB_CONSTRAINTS.md and docs/pcb_generation.md.")

    print()
    print("=" * 78)
    print("Intrinsic hole-to-hole gap (drilled pads only)")
    print("=" * 78)
    for r in sorted(drilled, key=lambda r: r["min_hole_gap_mm"]):
        print(f"  {r['footprint']:<52}{r['holes']:>5}"
              f"{r['min_hole_gap_mm']:>9.3f} mm  "
              f"{'/'.join(r['holes_between'])}")
    if tightest_hole:
        print()
        print(f"  TIGHTEST: {tightest_hole['min_hole_gap_mm']:.3f} mm in "
              f"{tightest_hole['footprint']}")
        print(f"            (holes {'/'.join(tightest_hole['holes_between'])}, "
              f"used by {', '.join(tightest_hole['refs'])})")
        print()
        print("  The board's min_hole_to_hole rule must be <= this value for the")
        print("  same reason: the drill spacing is a property of the part.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
