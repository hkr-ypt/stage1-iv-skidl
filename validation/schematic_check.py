#!/usr/bin/env python3
"""
schematic_check.py — Schematic <-> netlist consistency validator.
=================================================================

Independently re-derives connectivity from the generated ``.kicad_sch`` files
(parsed with ``simp_sexp``) and compares it against ``stage1_iv.net`` (parsed
with ``kinparse``). It never imports the schematic generator's internals — it
reconstructs pin positions from the embedded ``lib_symbols``, follows wire
segments with a union-find, and reads the labels attached to each pin, exactly
as KiCad's connectivity engine would.

Checks (all must pass; exits non-zero on any failure):

  1. Connection sets match: the set of (reference, pin_number, net_name)
     tuples reconstructed from labels/sheet-pins equals the netlist's set.
  2. Part sets match (same references, no duplicates across sheets).
  3. Every net carries the correct label class per the documented rule:
     GLOBAL_POWER (PWR_FLAG'd rails from blocks/nets.py + NET_MANIFEST["power"])
     -> global_label; nets spanning >= 2 sheets -> hierarchical_label;
     single-sheet nets -> local label. One single class per net everywhere;
     local label names are never reused on a second sheet.
  4. Hierarchy integrity: sheet-pin names on each top-level (sheet ...) match
     the hierarchical_label names in its child file exactly, in both
     directions; every top-side sheet pin is stubbed to a same-name local
     label; every top local label name appears at least twice (no dangling
     inter-sheet nets).
  5. Structural validity: every file re-parses with simp_sexp; every (uuid )
     is a well-formed UUID and unique across all files.
  6. ERC-level: no duplicate references; every non-no-connect pin reaches
     exactly one net label; no-connect pins carry a (no_connect) marker and
     no label; every netlist net has >= 2 endpoints.

Run:  python3 validation/schematic_check.py
"""

import re
import sys
import uuid as uuid_mod
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from simp_sexp import Sexp                     # noqa: E402
from kinparse import parse_netlist             # noqa: E402

from blocks.nets import (NET_MANIFEST,  # noqa: E402
                         EXTERNALLY_SOURCED_RAILS)

BLOCK_ORDER = [
    "power", "mcu", "dac_sweep", "output_buffer",
    "current_sense", "voltage_sense", "adc", "protection",
]
TOP_FILE = "stage1_iv.kicad_sch"
NETLIST_FILE = "stage1_iv.net"

# Nets that must use global power labels: the externally-sourced rails that
# carry a PWR_FLAG, plus the shared power/reference group of the net manifest
# (which adds +3V3D and VREF_2V5 — rails with a real on-board driver and so
# deliberately unflagged).
POWER_NET_NAMES = (set(EXTERNALLY_SOURCED_RAILS.values())
                   | set(NET_MANIFEST["power"]))


# ---------------------------------------------------------------------------
# s-expression helpers
# ---------------------------------------------------------------------------

def subs(node, tag):
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == tag]


def sub1(node, tag):
    found = subs(node, tag)
    if len(found) != 1:
        raise AssertionError(f"expected exactly one ({tag} ...), "
                             f"got {len(found)} in ({node[0]} ...)")
    return found[0]


def opt1(node, tag):
    found = subs(node, tag)
    return found[0] if found else None


def pt(x, y):
    """Coordinate key in exact hundredths of a mm."""
    return (round(float(x) * 100), round(float(y) * 100))


def prop(node, name):
    for p in subs(node, "property"):
        if str(p[1]) == name:
            return str(p[2])
    return None


# ---------------------------------------------------------------------------
# Netlist side (kinparse)
# ---------------------------------------------------------------------------

def load_netlist(path):
    """Parse the netlist with kinparse.

    kinparse's grammar predates two SKiDL 2.2.3 emissions, so the text is
    shimmed first WITHOUT touching the nets section: the empty
    ``(component_classes)`` clause is dropped and value-less
    ``(field (name X))`` clauses get an explicit empty value.
    """
    text = path.read_text()
    text = re.sub(r"\(component_classes\s*\)", "", text)
    text = re.sub(r'\(field\s*\(name\s+("(?:[^"\\]|\\.)*")\)\s*\)',
                  r"(field (name \1) \"\")", text)
    nl = parse_netlist(text)
    tuples = {(str(node.ref), str(node.num), str(net.name))
              for net in nl.nets for node in net.pins}
    refs = [str(p.ref) for p in nl.parts]
    net_sizes = {str(net.name): len(net.pins) for net in nl.nets}
    return tuples, refs, net_sizes


# ---------------------------------------------------------------------------
# Schematic side (simp_sexp)
# ---------------------------------------------------------------------------

class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, a):
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def lib_pin_offsets(root):
    """lib_id -> {pin_num: (lx, ly)} from the embedded lib_symbols."""
    out = {}
    libsyms = opt1(root, "lib_symbols")
    if libsyms is None:
        return out
    for symdef in subs(libsyms, "symbol"):
        lib_id = str(symdef[1])
        pins = {}
        for unit in subs(symdef, "symbol"):
            for pin in subs(unit, "pin"):
                num = str(sub1(pin, "number")[1])
                at = sub1(pin, "at")
                pins[num] = (float(at[1]), float(at[2]))
        out[lib_id] = pins
    return out


def read_child_sheet(path, errors):
    """Reconstruct one child sheet. Returns a dict of extracted facts."""
    root = Sexp(path.read_text())
    if str(root[0]) != "kicad_sch":
        errors.append(f"{path.name}: root element is not kicad_sch")
    lib_pins = lib_pin_offsets(root)

    uf = UnionFind()
    wire_points = set()
    for wire in subs(root, "wire"):
        xs = subs(wire, "pts")[0]
        points = [pt(xy[1], xy[2]) for xy in subs(xs, "xy")]
        for a, b in zip(points, points[1:]):
            uf.union(a, b)
        wire_points.update(points)

    labels = []          # (kind, name, point)
    for kind in ("label", "global_label", "hierarchical_label"):
        for lab in subs(root, kind):
            at = sub1(lab, "at")
            labels.append((kind, str(lab[1]), pt(at[1], at[2])))

    no_connects = {pt(sub1(nc, "at")[1], sub1(nc, "at")[2])
                   for nc in subs(root, "no_connect")}

    pins = []            # (ref, pin_num, point)
    refs = []
    for sym in subs(root, "symbol"):
        lib_id_node = opt1(sym, "lib_id")
        if lib_id_node is None:
            continue
        lib_id = str(lib_id_node[1])
        at = sub1(sym, "at")
        sx, sy, rot = float(at[1]), float(at[2]), float(at[3])
        if rot != 0:
            errors.append(f"{path.name}: symbol rotation {rot} unsupported "
                          f"by checker (generator emits 0 only)")
            continue
        ref = prop(sym, "Reference")
        refs.append(ref)
        if lib_id not in lib_pins:
            errors.append(f"{path.name}: {ref}: lib_id {lib_id!r} missing "
                          f"from embedded lib_symbols (not self-contained)")
            continue
        for num, (lx, ly) in lib_pins[lib_id].items():
            # KiCad symbol frames are Y-up; sheet coordinates are Y-down.
            pins.append((ref, num, pt(sx + lx, sy - ly)))

    return {
        "root": root, "uf": uf, "labels": labels, "pins": pins,
        "refs": refs, "no_connects": no_connects,
        "wire_points": wire_points,
    }


def resolve_connectivity(sheet_name, sheet, errors):
    """(ref, pin, net) tuples + per-net label kinds for one child sheet."""
    uf = sheet["uf"]
    comp_labels = {}
    for kind, name, point in sheet["labels"]:
        comp_labels.setdefault(uf.find(point), []).append((kind, name))

    tuples = set()
    net_kinds = {}
    nc_points_used = set()
    for ref, num, point in sheet["pins"]:
        if point in sheet["no_connects"]:
            nc_points_used.add(point)
            if point in sheet["wire_points"]:
                errors.append(f"{sheet_name}: no-connect pin {ref}.{num} "
                              f"also has a wire attached")
            continue
        attached = comp_labels.get(uf.find(point), [])
        names = {name for _k, name in attached}
        if not names:
            errors.append(f"{sheet_name}: pin {ref}.{num} has no net label "
                          f"(floating)")
            continue
        if len(names) > 1:
            errors.append(f"{sheet_name}: pin {ref}.{num} reaches multiple "
                          f"net names {sorted(names)}")
            continue
        net = names.pop()
        tuples.add((ref, num, net))
        for kind, name in attached:
            net_kinds.setdefault(name, set()).add(kind)

    unused_nc = sheet["no_connects"] - nc_points_used
    if unused_nc:
        errors.append(f"{sheet_name}: {len(unused_nc)} (no_connect) markers "
                      f"not on any pin")
    return tuples, net_kinds


def read_top_sheet(path, errors):
    root = Sexp(path.read_text())
    uf = UnionFind()
    for wire in subs(root, "wire"):
        xs = subs(wire, "pts")[0]
        points = [pt(xy[1], xy[2]) for xy in subs(xs, "xy")]
        for a, b in zip(points, points[1:]):
            uf.union(a, b)

    top_labels = {}
    for lab in subs(root, "label"):
        at = sub1(lab, "at")
        top_labels.setdefault(uf.find(pt(at[1], at[2])), []).append(
            str(lab[1]))
    for kind in ("global_label", "hierarchical_label"):
        if subs(root, kind):
            errors.append(f"{path.name}: unexpected {kind} on the top sheet")

    sheets = []
    for sheet in subs(root, "sheet"):
        name = prop(sheet, "Sheetname")
        fname = prop(sheet, "Sheetfile")
        pin_names = []
        for pin in subs(sheet, "pin"):
            pname = str(pin[1])
            at = sub1(pin, "at")
            attached = top_labels.get(uf.find(pt(at[1], at[2])), [])
            if attached != [pname]:
                errors.append(f"{path.name}: sheet {name!r} pin {pname!r} is "
                              f"not stubbed to exactly one matching local "
                              f"label (got {attached})")
            pin_names.append(pname)
        if len(set(pin_names)) != len(pin_names):
            errors.append(f"{path.name}: sheet {name!r} has duplicate pins")
        sheets.append((name, fname, set(pin_names)))

    label_names = [nm for names in top_labels.values() for nm in names]
    for nm in sorted(set(label_names)):
        if label_names.count(nm) < 2:
            errors.append(f"{path.name}: inter-sheet net {nm!r} labelled "
                          f"only once on the top sheet (dangling)")
    return root, sheets


def collect_uuids(root, path, errors, seen):
    def walk(node):
        if not isinstance(node, list):
            return
        if node and node[0] == "uuid" and len(node) >= 2:
            val = str(node[1])
            try:
                uuid_mod.UUID(val)
            except ValueError:
                errors.append(f"{path.name}: malformed uuid {val!r}")
            if val in seen:
                errors.append(f"{path.name}: duplicate uuid {val} "
                              f"(also in {seen[val]})")
            seen[val] = path.name
        for child in node:
            walk(child)
    walk(root)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def check(out_dir=HERE, verbose=True):
    errors = []
    results = []

    def report(name, ok, detail=""):
        results.append((name, ok, detail))
        if verbose:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
            if detail:
                print(f"         {detail}")

    out_dir = Path(out_dir)
    netlist_tuples, netlist_refs, net_sizes = load_netlist(
        out_dir / NETLIST_FILE)

    # ---- parse all sheets (structural validity: files must re-parse) ----
    top_path = out_dir / TOP_FILE
    parse_errors = []
    top_root, top_sheets = read_top_sheet(top_path, parse_errors)
    child = {}
    for block in BLOCK_ORDER:
        child[block] = read_child_sheet(out_dir / f"{block}.kicad_sch",
                                        parse_errors)
    report("All 9 .kicad_sch files parse with simp_sexp",
           not parse_errors, "; ".join(parse_errors[:5]))
    errors.extend(parse_errors)

    # ---- reconstruct connectivity per child sheet ----
    conn_errors = []
    sch_tuples = set()
    net_kinds = {}
    ref_sheet = {}
    dup_refs = []
    for block in BLOCK_ORDER:
        tuples, kinds, = resolve_connectivity(
            f"{block}.kicad_sch", child[block], conn_errors)
        sch_tuples |= tuples
        for name, ks in kinds.items():
            net_kinds.setdefault(name, {})[block] = ks
        for ref in child[block]["refs"]:
            if ref in ref_sheet:
                dup_refs.append(ref)
            ref_sheet[ref] = block
    report("Every pin resolves to exactly one label; no floating pins; "
           "no-connects marked", not conn_errors, "; ".join(conn_errors[:5]))
    errors.extend(conn_errors)

    # ---- check 1: connection set equality ----
    missing = netlist_tuples - sch_tuples
    extra = sch_tuples - netlist_tuples
    detail = (f"netlist={len(netlist_tuples)} schematic={len(sch_tuples)} "
              f"missing={sorted(missing)[:6] or 'none'} "
              f"extra={sorted(extra)[:6] or 'none'}")
    report("Schematic (ref, pin, net) set == netlist set",
           not missing and not extra, detail)
    if missing or extra:
        errors.append(f"connection mismatch: missing={sorted(missing)}, "
                      f"extra={sorted(extra)}")

    # ---- check 2: part sets ----
    sch_refs = set(ref_sheet)
    nl_refs = set(netlist_refs)
    ok = (sch_refs == nl_refs and not dup_refs
          and len(netlist_refs) == len(nl_refs))
    report("Part references match netlist, no duplicates",
           ok, f"schematic={len(sch_refs)} netlist={len(nl_refs)} "
              f"dups={dup_refs or 'none'}")
    if not ok:
        errors.append(f"ref mismatch: only-sch={sorted(sch_refs - nl_refs)}, "
                      f"only-net={sorted(nl_refs - sch_refs)}, "
                      f"dups={dup_refs}")

    # ---- check 3: label classes ----
    class_errors = []
    net_sheets = {}
    for ref, num, net in netlist_tuples:
        if ref in ref_sheet:
            net_sheets.setdefault(net, set()).add(ref_sheet[ref])
    for net, sheets_used in sorted(net_sheets.items()):
        if net in POWER_NET_NAMES:
            expected = "global_label"
        elif len(sheets_used) >= 2:
            expected = "hierarchical_label"
        else:
            expected = "local label"
        seen = set()
        for _block, ks in net_kinds.get(net, {}).items():
            seen |= ks
        want = {"global_label": {"global_label"},
                "hierarchical_label": {"hierarchical_label"},
                "local label": {"label"}}[expected]
        if seen != want:
            class_errors.append(f"{net}: expected {expected}, saw "
                                f"{sorted(seen) or 'nothing'}")
        if seen == {"label"} and len(net_kinds.get(net, {})) > 1:
            class_errors.append(f"{net}: local label name reused on "
                                f"{sorted(net_kinds[net])}")
    report("Every net uses its correct label class "
           "(global power / hierarchical / local)",
           not class_errors, "; ".join(class_errors[:5]))
    errors.extend(class_errors)

    # ---- check 4: hierarchy integrity ----
    hier_errors = []
    sheetfile_map = {fname: (name, pins) for name, fname, pins in top_sheets}
    if len(top_sheets) != len(BLOCK_ORDER):
        hier_errors.append(f"top sheet has {len(top_sheets)} (sheet) "
                           f"instances, expected {len(BLOCK_ORDER)}")
    for block in BLOCK_ORDER:
        fname = f"{block}.kicad_sch"
        if fname not in sheetfile_map:
            hier_errors.append(f"top sheet has no (sheet) for {fname}")
            continue
        _name, pin_names = sheetfile_map[fname]
        hlabels = {name for kind, name, _pt in child[block]["labels"]
                   if kind == "hierarchical_label"}
        for orphan in sorted(pin_names - hlabels):
            hier_errors.append(f"{block}: sheet pin {orphan!r} has no "
                               f"hierarchical label in child")
        for dangling in sorted(hlabels - pin_names):
            hier_errors.append(f"{block}: hierarchical label {dangling!r} "
                               f"has no sheet pin on the top sheet")
    report("Sheet pins <-> hierarchical labels match 1:1 per block, "
           "no orphans", not hier_errors, "; ".join(hier_errors[:5]))
    errors.extend(hier_errors)

    # ---- check 5: UUID validity/uniqueness ----
    uuid_errors = []
    seen_uuids = {}
    collect_uuids(top_root, top_path, uuid_errors, seen_uuids)
    for block in BLOCK_ORDER:
        collect_uuids(child[block]["root"],
                      out_dir / f"{block}.kicad_sch", uuid_errors, seen_uuids)
    report("All UUIDs well-formed and unique "
           f"({len(seen_uuids)} objects)", not uuid_errors,
           "; ".join(uuid_errors[:5]))
    errors.extend(uuid_errors)

    # ---- check 6: ERC-level netlist sanity ----
    small = {n: c for n, c in net_sizes.items() if c < 2}
    report("Every netlist net has >= 2 endpoints", not small,
           f"single-ended nets: {small or 'none'}")
    if small:
        errors.append(f"single-ended nets: {small}")

    ok = not errors
    if verbose:
        print()
        if ok:
            print(f"SCHEMATIC CHECK: PASS "
                  f"({len(sch_tuples)} connections verified across "
                  f"{len(BLOCK_ORDER)} sheets)")
        else:
            print(f"SCHEMATIC CHECK: FAIL ({len(errors)} problem(s))")
            for e in errors:
                print(f"  - {e}")
    return ok


def main():
    print("=" * 70)
    print("SCHEMATIC <-> NETLIST CONSISTENCY CHECK")
    print("=" * 70)
    return 0 if check() else 1


if __name__ == "__main__":
    sys.exit(main())
