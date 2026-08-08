#!/usr/bin/env python3
"""
kicad_erc_power.py — measure KiCad's power-driver ERC rules on the schematic.
=============================================================================

WHY THIS EXISTS
---------------
``kicad-cli sch erc`` first shipped in KiCad 8. KiCad 7 is the newest version
installable in this environment (the KiCad PPA host is refused by the network
egress policy), so **the ERC dialog cannot be run here.** Rather than report
nothing, this script evaluates KiCad's two power-driver rules directly against
the emitted ``.kicad_sch`` files — the exact artifact KiCad's ERC reads — using
the schematic's own pin electrical types and its own label-based connectivity.

The rules, as KiCad defines them:

  ``power_pin_not_driven``   a net with at least one ``power_in`` pin and no
                             ``power_out`` pin (PWR_FLAG's pin is ``power_out``,
                             which is how a flag clears the error)
  ``different_unit_net`` /   two or more ``power_out`` pins on the same net,
  driver conflict            i.e. two things driving one rail

This is a re-implementation of a rule, not a KiCad ERC run, and is labelled as
such wherever its numbers are quoted. It reads the schematic files rather than
``stage1_iv.net`` on purpose: KiCad omits ``#``-prefixed power symbols from
netlist export, so a netlist-based measurement would not see the PWR_FLAGs and
would report a false positive.

Usage::

    python3 tools/kicad_erc_power.py [--dir .] [--json]

Exits non-zero if any power_pin_not_driven or driver-conflict violation is
found.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from resolve_kicad_libs import parse_sexp  # noqa: E402

POWER_IN = "power_in"
POWER_OUT = "power_out"


def _head(node):
    return node[0] if isinstance(node, list) and node and isinstance(node[0], str) else ""


def _kids(node, head):
    return [c for c in node if isinstance(c, list) and _head(c) == head]


def _first(node, head):
    kids = _kids(node, head)
    return kids[0] if kids else None


def _prop(node, name):
    for p in _kids(node, "property"):
        if len(p) > 2 and str(p[1]) == name:
            return str(p[2])
    return ""


def _xy(node):
    at = _first(node, "at")
    if at is None or len(at) < 3:
        return None
    return (round(float(at[1]), 4), round(float(at[2]), 4))


def _rotate(lx, ly, angle):
    """Rotate a lib-frame offset by the instance angle (degrees, CCW)."""
    if angle == 0:
        return lx, ly
    if angle == 90:
        return -ly, lx
    if angle == 180:
        return -lx, -ly
    return ly, -lx


def read_sheet(path):
    """Return (pin_points, label_points) for one .kicad_sch.

    pin_points: {(x, y): [(ref, pin_number, electrical_type)]}
    label_points: {(x, y): [label_text]}
    """
    with open(path, encoding="utf-8") as fh:
        tree = parse_sexp(fh.read())
    sheet = _first(tree, "kicad_sch")
    if sheet is None:
        return {}, {}, [], set()

    # --- library symbol pin geometry, per lib_id ---
    lib_pins = {}
    lib_syms = _first(sheet, "lib_symbols")
    for sym in _kids(lib_syms or [], "symbol"):
        lib_id = str(sym[1])
        pins = []
        for unit in _kids(sym, "symbol"):
            for pin in _kids(unit, "pin"):
                etype = str(pin[1]) if len(pin) > 1 else "unspecified"
                at = _first(pin, "at")
                num = _first(pin, "number")
                if at is None or num is None or len(at) < 4:
                    continue
                pins.append((str(num[1]), etype,
                             float(at[1]), float(at[2]), int(float(at[3]))))
        lib_pins[lib_id] = pins

    # --- instances: place each pin in sheet coordinates ---
    pin_points = defaultdict(list)
    parts = []
    for inst in _kids(sheet, "symbol"):
        lid_node = _first(inst, "lib_id")
        if lid_node is None:
            continue
        lib_id = str(lid_node[1])
        pos = _xy(inst)
        at = _first(inst, "at")
        angle = int(float(at[3])) if at is not None and len(at) > 3 else 0
        ref = _prop(inst, "Reference")
        parts.append((ref, lib_id))
        for num, etype, lx, ly, _pin_angle in lib_pins.get(lib_id, []):
            rx, ry = _rotate(lx, ly, angle)
            # KiCad symbol libs are Y-up, sheets are Y-down.
            px = round(pos[0] + rx, 4)
            py = round(pos[1] - ry, 4)
            pin_points[(px, py)].append((ref, num, etype))

    # --- labels and wires, to resolve which net a pin sits on ---
    label_points = defaultdict(list)
    for kind in ("label", "global_label", "hierarchical_label"):
        for lab in _kids(sheet, kind):
            pt = _xy(lab)
            if pt is not None:
                label_points[pt].append(str(lab[1]))

    nc_points = set()
    for ncm in _kids(sheet, "no_connect"):
        pt = _xy(ncm)
        if pt is not None:
            nc_points.add(pt)

    wires = []
    for wire in _kids(sheet, "wire"):
        pts = _first(wire, "pts")
        if pts is None:
            continue
        coords = [(round(float(c[1]), 4), round(float(c[2]), 4))
                  for c in _kids(pts, "xy") if len(c) > 2]
        if len(coords) == 2:
            wires.append(tuple(coords))

    return pin_points, label_points, wires, nc_points


def net_of_pins(pin_points, label_points, wires, nc_points):
    """Map each pin to the label it reaches through its wire stub.

    The emitter gives every connected pin a single wire stub ending in exactly
    one label (see SCHEMATIC.md), so resolution is a one-hop walk; anything
    that does not resolve that way is reported rather than guessed.
    """
    adj = defaultdict(set)
    for a, b in wires:
        adj[a].add(b)
        adj[b].add(a)

    out = []
    unresolved = []
    nc_count = 0
    for point, pins in pin_points.items():
        # A pin carrying a KiCad (no_connect) marker is deliberately open;
        # KiCad's power rules do not apply to it.
        if point in nc_points:
            nc_count += len(pins)
            continue
        names = set(label_points.get(point, ()))
        for nbr in adj.get(point, ()):
            names |= set(label_points.get(nbr, ()))
        for ref, num, etype in pins:
            if len(names) == 1:
                out.append((next(iter(names)), ref, num, etype))
            elif not names:
                unresolved.append((ref, num, "no label"))
            else:
                unresolved.append((ref, num, f"ambiguous: {sorted(names)}"))
    return out, unresolved, nc_count


def analyse(directory="."):
    sheets = sorted(p for p in os.listdir(directory) if p.endswith(".kicad_sch"))
    per_net = defaultdict(list)
    unresolved = []
    nc_total = 0
    for name in sheets:
        pin_points, label_points, wires, nc_points = read_sheet(
            os.path.join(directory, name))
        resolved, bad, nc_n = net_of_pins(pin_points, label_points, wires,
                                          nc_points)
        nc_total += nc_n
        for net, ref, num, etype in resolved:
            per_net[net].append((ref, num, etype, name))
        unresolved.extend((name, *b) for b in bad)

    not_driven, conflicts, driven = [], [], []
    for net, nodes in sorted(per_net.items()):
        ins = [n for n in nodes if n[2] == POWER_IN]
        outs = [n for n in nodes if n[2] == POWER_OUT]
        if ins and not outs:
            not_driven.append((net, [f"{r}.{p}" for r, p, _t, _s in ins]))
        elif ins and outs:
            driven.append((net, [f"{r}.{p}" for r, p, _t, _s in outs]))
        if len(outs) > 1:
            conflicts.append((net, [f"{r}.{p}" for r, p, _t, _s in outs]))
    return {
        "sheets": sheets,
        "nets": len(per_net),
        "no_connect_pins": nc_total,
        "power_pin_not_driven": not_driven,
        "driver_conflicts": conflicts,
        "driven_power_nets": driven,
        "unresolved_pins": unresolved,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--dir", default=REPO_ROOT, help="directory of .kicad_sch files")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    result = analyse(args.dir)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("=" * 70)
        print("KiCad power-driver ERC rules, evaluated on the .kicad_sch files")
        print("=" * 70)
        print("NOTE: this is a re-implementation of KiCad's rules, not a KiCad")
        print("      ERC run — kicad-cli sch erc does not exist before KiCad 8.")
        print(f"  sheets analysed .......... {len(result['sheets'])}")
        print(f"  nets seen ................ {result['nets']}")
        print(f"  no-connect pins .......... {result['no_connect_pins']} "
              f"(marked (no_connect); power rules do not apply)")
        print(f"  power_pin_not_driven ..... {len(result['power_pin_not_driven'])}")
        for net, pins in result["power_pin_not_driven"]:
            print(f"      [ERROR] {net}: power_in pins {pins} with no power_out")
        print(f"  driver conflicts ......... {len(result['driver_conflicts'])}")
        for net, pins in result["driver_conflicts"]:
            print(f"      [ERROR] {net}: {len(pins)} power_out pins {pins}")
        print(f"  driven power nets ........ {len(result['driven_power_nets'])}")
        for net, pins in result["driven_power_nets"]:
            print(f"      {net:<12} driven by {pins}")
        if result["unresolved_pins"]:
            print(f"  UNRESOLVED pins .......... {len(result['unresolved_pins'])}")
            for row in result["unresolved_pins"][:20]:
                print(f"      {row}")

    bad = (len(result["power_pin_not_driven"]) + len(result["driver_conflicts"])
           + len(result["unresolved_pins"]))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
