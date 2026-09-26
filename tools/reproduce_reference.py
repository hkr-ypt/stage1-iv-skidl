#!/usr/bin/env python3
"""
reproduce_reference.py — prove the layout source reproduces the routed board.
==============================================================================

``layout/stage1_iv.layout`` claims to hold the human-routed board's physical
design. This regenerates that board from source and checks the claim:

1. **Capture is current.** ``layout/stage1_iv.layout`` equals a fresh capture of
   ``unipolar-routed/diodes.kicad_pcb`` (tools/capture_layout.py --check).
2. **Reproduction.** generators/routed_pcb.py emits a board from the layout
   plus the reference board's OWN electrical content (values, fields, pad nets,
   schematic links — :func:`parts_from_board`), with footprint geometry from
   the vendored library. Written to ``build/reference_reproduction.kicad_pcb``.
3. **Semantic comparison** against the original (tools/compare_boards.py):
   placement, pads, footprint graphics and texts, values, tracks, vias, zones,
   outline, setup. Any difference fails, unless it is one of the
   :data:`KNOWN` differences below, each of which says why it is not a layout
   difference.
4. **KiCad's opinion** (skipped, and said so, without KiCad 10): the
   reproduction and the original are each put in a copy of ``unipolar-routed/`` and run
   through ``kicad-cli pcb drc --refill-zones --schematic-parity`` against the
   routed project's own rules and schematic, with the vendored footprint
   library attached. The two results must be identical. That project switches
   clearance checking off, so both are run again with every check on (the
   captured as-built rules plus the scoped intra-footprint rules; see
   validation/routed_board_check.py), and must again agree. Then both are
   loaded and refilled by pcbnew and the zone fills compared, and both are
   re-saved by KiCad and compared as text.

Usage::

    python3 tools/reproduce_reference.py            # all of the above
    python3 tools/reproduce_reference.py --no-kicad # 1-3 only
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generators.layout import load_layout  # noqa: E402
from generators.routed_pcb import parts_from_board, emit_board  # noqa: E402
import compare_boards  # noqa: E402

from generators.layout import REFERENCE_BOARD as REFERENCE, \
    REFERENCE_DIR  # noqa: E402
LAYOUT = os.path.join(ROOT, "layout", "stage1_iv.layout")
OUT_DIR = os.path.join(ROOT, "build")
OUT = os.path.join(OUT_DIR, "reference_reproduction.kicad_pcb")

# Differences that are not layout differences, and why. Each entry names the
# category, the exact parts and the exact footprint fields it covers; anything
# else that differs fails. Kept short: every entry is a claim a reviewer can
# check.
KNOWN = {
    "library revision of the hand-added capacitors": (
        "footprint", ("C31", "C44", "C45"), ("('descr'", "('model'"),
        "C31, C44 and C45 were added by hand in KiCad 10 from KiCad 10's own "
        "library; every other part, and every regenerated part, comes from the "
        "vendored 9.0.9.1 library. The two revisions differ in two strings on "
        "this footprint: the description text (KiCad 10 drops \", generated "
        "with kicad-footprint-generator\") and the 3D-model path variable "
        "(${KICAD10_3DMODEL_DIR} vs ${KICAD9_3DMODEL_DIR}). Pads, courtyard, "
        "silk and fab geometry are identical; no copper, no connectivity."),
}


def _known(category, item):
    for name, (cat, refs, fields, _why) in KNOWN.items():
        if cat != category:
            continue
        ref = item.split(":", 1)[0]
        if ref in refs and any(f"only in A: {f}" in item or
                               f"only in B: {f}" in item for f in fields):
            return name
    return None


def reproduce(out_path=OUT):
    layout = load_layout(LAYOUT)
    nodes, net_names, codes = parts_from_board(REFERENCE)
    text, report = emit_board(nodes, net_names, codes, layout)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return report


def semantic(out_path=OUT):
    diffs, a, b = compare_boards.compare(REFERENCE, out_path)
    explained, unexplained = [], []
    for cat, items in diffs.items():
        for item in items:
            name = _known(cat, item)
            (explained if name else unexplained).append((cat, item, name))
    return compare_boards.summary(a), compare_boards.summary(b), \
        explained, unexplained


# ---------------------------------------------------------------------------
# KiCad
# ---------------------------------------------------------------------------

def kicad_version():
    exe = shutil.which("kicad-cli")
    if not exe:
        return None
    out = subprocess.run([exe, "version"], capture_output=True, text=True)
    return out.stdout.strip() or None


def _stage_routed_copy(board, work):
    """A copy of the reference project whose board is `board`, with the
    vendored library."""
    dst = os.path.join(work, os.path.basename(REFERENCE_DIR))
    shutil.copytree(REFERENCE_DIR, dst)
    shutil.copy(board, os.path.join(dst, os.path.basename(REFERENCE)))
    with open(os.path.join(ROOT, "fp-lib-table"), encoding="utf-8") as fh:
        table = fh.read().replace("${KIPRJMOD}", ROOT)
    with open(os.path.join(dst, "fp-lib-table"), "w", encoding="utf-8") as fh:
        fh.write(table)
    return os.path.join(dst, os.path.basename(REFERENCE))


def kicad_drc(board, work):
    path = _stage_routed_copy(board, work)
    report = os.path.join(work, "drc.json")
    subprocess.run(["kicad-cli", "pcb", "drc", "--refill-zones",
                    "--schematic-parity", "--severity-all", "--format",
                    "json", "-o", report, path],
                   capture_output=True, text=True, timeout=900)
    with open(report, encoding="utf-8") as fh:
        data = json.load(fh)
    counts = {}
    for v in data.get("violations", []):
        key = f"{v['type']}/{v['severity']}"
        counts[key] = counts.get(key, 0) + 1
    return {"violations": counts,
            "unconnected": len(data.get("unconnected_items", [])),
            "parity": len(data.get("schematic_parity", []))}


_FILL_SCRIPT = r"""
import sys, pcbnew, json
a_path, b_path = sys.argv[1], sys.argv[2]
boards = []
for path in (a_path, b_path):
    board = pcbnew.LoadBoard(path)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    fills = {}
    for z in board.Zones():
        for layer in z.GetLayerSet().Seq():
            fills[(z.GetNetname(), int(layer))] = z.GetFilledPolysList(layer)
    boards.append((board, fills))
(_ba, fa), (_bb, fb) = boards
out = {"keys_a": len(fa), "keys_b": len(fb), "zones": []}
for key in sorted(set(fa) | set(fb)):
    a, b = fa.get(key), fb.get(key)
    if a is None or b is None:
        out["zones"].append([key[0], key[1], None, None, None]); continue
    only_a = a.CloneDropTriangulation(); only_a.BooleanSubtract(b)
    only_b = b.CloneDropTriangulation(); only_b.BooleanSubtract(a)
    out["zones"].append([key[0], key[1], a.Area() / 1e12, b.Area() / 1e12,
                         (only_a.Area() + only_b.Area()) / 1e12])
print(json.dumps(out))
"""


def _interpreters():
    """This interpreter, then every other python3 on PATH: pcbnew is a SWIG
    module built for the distro's python, which a CI runner's own python3
    shadows (validation/drc_check.py::_other_interpreters finds it)."""
    from validation.drc_check import _other_interpreters
    return [sys.executable] + _other_interpreters()


def zone_fills(a, b):
    """Refill both boards in pcbnew; per zone and layer, the copper area of
    each and the area of their symmetric difference (mm^2). Fractured fill
    polygons can differ vertex by vertex while covering identical copper, so
    area, not vertex lists, is what is compared."""
    for exe in _interpreters():
        res = subprocess.run([exe, "-c", _FILL_SCRIPT, a, b],
                             capture_output=True, text=True, timeout=900)
        if res.returncode == 0 and res.stdout.strip():
            return json.loads(res.stdout.strip().splitlines()[-1])
    return None


_RESAVE_SCRIPT = r"""
import sys, pcbnew
for src, dst in zip(sys.argv[1::2], sys.argv[2::2]):
    board = pcbnew.LoadBoard(src)
    pcbnew.SaveBoard(dst, board)
"""

_UUID = re.compile(r'"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-'
                   r'[0-9a-f]{12}"')
# KiCad 10 writes these per-via flags when it upgrades a pre-10 via; a native
# KiCad 10 via inherits them from the board setup (all "no" on this board).
_VIA_LEGACY = re.compile(r"\n\t\t\((capping|filling) no\)"
                         r"|\n\t\t\((covering|plugging)\n\t\t\t\(front no\)"
                         r"\n\t\t\t\(back no\)\n\t\t\)")


def _top_items(path):
    with open(path, encoding="utf-8") as fh:
        text = _UUID.sub('"UUID"', fh.read())
    blocks, cur, depth = [], [], 0
    for line in text.split("\n")[1:]:
        if depth == 0 and line.strip() == ")":
            break
        cur.append(line)
        depth += line.count("(") - line.count(")")
        if depth == 0:
            blocks.append("\n".join(cur))
            cur = []
    return blocks


def kicad_resave_items(a, b, work):
    """Both boards loaded and saved by KiCad; top-level items compared as
    multisets with UUIDs masked and zone fills excluded (zone copper is
    compared by area). This is as close to byte identity as UUIDs allow."""
    outs = [os.path.join(work, "a.kicad_pcb"), os.path.join(work, "b.kicad_pcb")]
    for exe in _interpreters():
        res = subprocess.run([exe, "-c", _RESAVE_SCRIPT, a, outs[0], b,
                              outs[1]], capture_output=True, text=True,
                             timeout=900)
        if res.returncode == 0 and all(os.path.exists(o) for o in outs):
            break
    else:
        return None
    zone_fill = re.compile(r"\n\t\t\(filled_polygon.*?\n\t\t\)", re.S)
    items = []
    for out in outs:
        items.append(collections.Counter(
            _VIA_LEGACY.sub("", zone_fill.sub("", blk))
            for blk in _top_items(out)))
    only_a, only_b = items[0] - items[1], items[1] - items[0]
    kinds = lambda c: dict(collections.Counter(  # noqa: E731
        blk.strip().split()[0].strip("(") for blk in c.elements()))
    return {"items": sum(items[0].values()),
            "identical": sum((items[0] & items[1]).values()),
            "only_original": kinds(only_a), "only_reproduction": kinds(only_b)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--no-kicad", action="store_true")
    args = ap.parse_args(argv)
    ok = True

    from capture_layout import main as capture_main
    print("1. capture is current")
    if capture_main([REFERENCE, LAYOUT, "--check"]) != 0:
        ok = False

    print("2. reproduce the routed board from layout source")
    rep = reproduce()
    print(f"   wrote {os.path.relpath(OUT, ROOT)}: {rep['parts']} parts, "
          f"{rep['segments']} segments, {rep['vias']} vias, "
          f"{rep['zones']} zones")
    if rep["skipped_texts"]:
        print(f"   FAIL text overrides not applied: {rep['skipped_texts']}")
        ok = False

    print(f"3. compare with {os.path.relpath(REFERENCE, ROOT)}")
    sa, sb, explained, unexplained = semantic()
    print(f"   original      {sa}")
    print(f"   reproduction  {sb}")
    for cat, item, name in unexplained:
        print(f"   DIFFERENT [{cat}] {item[:160]}")
    names = sorted({n for _c, _i, n in explained})
    for name in names:
        n = sum(1 for _c, _i, m in explained if m == name)
        print(f"   known, not layout ({n}x) {name}: {KNOWN[name][3]}")
    if unexplained:
        ok = False
        print(f"   FAIL {len(unexplained)} unexplained difference(s)")
    else:
        print(f"   OK  identical copper, placement, connectivity, outline and "
              f"setup ({len(explained)} known non-layout difference(s))")

    print("4. KiCad")
    version = None if args.no_kicad else kicad_version()
    if not version or not version.startswith("10."):
        print(f"   SKIPPED — needs KiCad 10 (found: {version or 'none'}). "
              f"This is not a pass.")
        return 0 if ok else 1
    with tempfile.TemporaryDirectory() as w1, \
            tempfile.TemporaryDirectory() as w2:
        orig = kicad_drc(REFERENCE, w1)
        repro = kicad_drc(OUT, w2)
    print(f"   KiCad {version} DRC, the routed project as the designer left "
          f"it (+ schematic parity):")
    print(f"     original      {orig}")
    print(f"     reproduction  {repro}")
    if orig != repro:
        ok = False
        print("   FAIL KiCad does not see the same board")
    # That project switches clearance checking off ("clearance": "ignore").
    # Run both again with every check on: the captured as-built rules plus the
    # repository's scoped intra-footprint rules (validation/routed_board_check).
    from validation.routed_board_check import as_built_project, run_drc, \
        PROJECT, DRU
    with open(PROJECT, encoding="utf-8") as fh:
        built = as_built_project(json.load(fh))
    with open(DRU, encoding="utf-8") as fh:
        dru = fh.read()
    with tempfile.TemporaryDirectory() as w1, \
            tempfile.TemporaryDirectory() as w2:
        on = [run_drc(b, built, dru, False, w)[0] for b, w in
              ((REFERENCE, w1), (OUT, w2))]
    summary = [None if d is None else {
        "violations": dict(sorted(collections.Counter(
            f"{v['type']}/{v['severity']}"
            for v in d["violations"]).items())),
        "unconnected": len(d["unconnected_items"])} for d in on]
    print("   KiCad DRC, as-built rules with every check on (clearance "
          "included):")
    print(f"     original      {summary[0]}")
    print(f"     reproduction  {summary[1]}")
    if summary[0] is None or summary[0] != summary[1]:
        ok = False
        print("   FAIL KiCad does not see the same board with clearance on")
    fills = zone_fills(REFERENCE, OUT)
    if fills is None:
        print("   zone fill comparison SKIPPED: pcbnew not importable")
    else:
        worst = 0.0
        for net, layer, area_a, area_b, xor in fills["zones"]:
            if xor is None:
                print(f"   FAIL zone {net} layer {layer} exists on one board only")
                ok = False
                continue
            worst = max(worst, xor)
            print(f"   zone {net:<5} layer {layer:>2}: copper {area_a:9.3f} vs "
                  f"{area_b:9.3f} mm^2, symmetric difference {xor:.6f} mm^2")
        if worst > 1e-6:
            ok = False
            print("   FAIL refilled copper differs")
        else:
            print("   OK  refilled zone copper identical")
    with tempfile.TemporaryDirectory() as w3:
        resave = kicad_resave_items(REFERENCE, OUT, w3)
    if resave is None:
        print("   KiCad re-save comparison SKIPPED: pcbnew not importable")
    else:
        print(f"   re-saved by KiCad, UUIDs masked, fills excluded: "
              f"{resave['identical']}/{resave['items']} top-level items "
              f"textually identical; differing: original "
              f"{resave['only_original']}, reproduction "
              f"{resave['only_reproduction']}")
        # Only the three hand-added capacitors may differ (library revision).
        if resave["only_original"] not in ({}, {"footprint": 3}) or \
                resave["only_reproduction"] != resave["only_original"]:
            ok = False
            print("   FAIL unexpected textual difference after KiCad re-save")
    print("REPRODUCTION: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
