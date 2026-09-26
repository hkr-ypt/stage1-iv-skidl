#!/usr/bin/env python3
"""
compare_routed_board.py — what separates a hand-routed board from the SKiDL design?
==================================================================================

``validation/update_pcb_noop.py`` proves the GENERATED board agrees with the
schematic. The board that gets built is the human-routed one in ``unipolar-routed/``,
which this repository never regenerates. This tool reports, part by part, what
would have to change on that board for it to implement the current SKiDL
circuit — the engineering-change list — without touching the file.

It matches footprints by REFERENCE DESIGNATOR, not by instance path: parts a
designer added in KiCad carry KiCad-assigned UUIDs, so path matching would call
them foreign even when they agree. It reads both pad-net spellings, the
generated board's ``(net 5 "AGND")`` and KiCad 10.0's ``(net "AGND")``.

Reported, in this order:
    NEW     in the SKiDL design, not on the board (needs placing and routing)
    VALUE   same part, different value (BOM change only)
    FPID    same part, different footprint
    NET     pad on a different net (copper change)
    BOARD   on the board, not in the SKiDL design (back-port it or remove it)

Usage::

    python3 tools/compare_routed_board.py                  # unipolar-routed/diodes.kicad_pcb
    python3 tools/compare_routed_board.py --board other.kicad_pcb

Exit 0 when the board implements the SKiDL netlist exactly, 1 otherwise. It is
a report, not a CI gate: ``unipolar-routed/`` legitimately lags the SKiDL source until a
designer applies the change (see docs/bipolar_measurement.md).
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from tools.resolve_kicad_libs import parse_sexp          # noqa: E402

from generators.layout import REFERENCE_BOARD as DEFAULT_BOARD  # noqa: E402


def _head(node):
    return str(node[0]) if isinstance(node, list) and node else None


def _kids(node, name):
    return [c for c in node if _head(c) == name]


def read_board(path):
    """{ref: {"value", "fpid", "pads": {num: net}}} from a .kicad_pcb."""
    with open(path, encoding="utf-8") as fh:
        tree = parse_sexp(fh.read())
    root = next(c for c in tree if _head(c) == "kicad_pcb")
    out = {}
    for fp in _kids(root, "footprint"):
        props = {str(p[1]): str(p[2]) for p in _kids(fp, "property")
                 if len(p) > 2}
        pads = {}
        for pad in _kids(fp, "pad"):
            net = next(iter(_kids(pad, "net")), None)
            # (net 5 "AGND") on the generated board, (net "AGND") in KiCad 10.0
            name = str(net[-1]) if net is not None and len(net) > 1 else ""
            pads.setdefault(str(pad[1]), name)
        out[props.get("Reference", "")] = {
            "value": props.get("Value", ""), "fpid": str(fp[1]), "pads": pads}
    return out


def compare(board_path):
    from validation.update_pcb_noop import (
        netlist_components, BOARD_ONLY_PREFIXES,
    )
    want = netlist_components()
    have = read_board(board_path)
    rows = []
    for ref in sorted(set(want) - set(have)):
        w = want[ref]
        nets = ", ".join(f"{n}={v}" for n, v in sorted(w["pads"].items()))
        rows.append(("NEW", ref, f"{w['value']}  {w['fpid']}  [{nets}]"))
    for ref in sorted(set(want) & set(have)):
        w, h = want[ref], have[ref]
        if w["value"] != h["value"]:
            rows.append(("VALUE", ref, f"{h['value']!r} -> {w['value']!r}"))
        if w["fpid"] != h["fpid"]:
            rows.append(("FPID", ref, f"{h['fpid']} -> {w['fpid']}"))
        for num, net in sorted(w["pads"].items()):
            if h["pads"].get(num) != net:
                rows.append(("NET", f"{ref}.{num}",
                             f"{h['pads'].get(num)!r} -> {net!r}"))
    for ref in sorted(set(have) - set(want)):
        if ref.startswith(BOARD_ONLY_PREFIXES):
            continue                       # mounting holes: board-only parts
        h = have[ref]
        nets = ", ".join(f"{n}={v}" for n, v in sorted(h["pads"].items()))
        rows.append(("BOARD", ref, f"{h['value']}  [{nets}]"))
    order = {k: i for i, k in enumerate(("NEW", "VALUE", "FPID", "NET",
                                         "BOARD"))}
    return sorted(rows, key=lambda r: (order[r[0]], r[1]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--board", default=DEFAULT_BOARD)
    args = ap.parse_args(argv)
    rows = compare(args.board)
    print(f"{os.path.relpath(args.board, ROOT)} vs the SKiDL design: "
          f"{len(rows) or 'no'} difference(s)")
    for kind, ref, detail in rows:
        print(f"  {kind:<6} {ref:<8} {detail}")
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
