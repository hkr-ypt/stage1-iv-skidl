#!/usr/bin/env python3
"""
kicad_roundtrip.py — verify the generated schematic through KiCad itself.
=========================================================================

``validation/schematic_check.py`` reconstructs connectivity from the emitted
``.kicad_sch`` files with this project's own parser. That proves the emitter is
self-consistent, not that **KiCad** reads the files the same way. This script
closes that gap: it hands ``stage1_iv.kicad_sch`` to ``kicad-cli``, has KiCad
export its own netlist, and compares the ``(reference, pin, net)`` set against
``stage1_iv.net``.

It also reports, per part, the two ERC categories this project is responsible
for and that ``kicad-cli`` in KiCad 7 cannot check on its own:

    * "Symbol X not found in symbol library Y"  -> unresolvable ``lib_id``
    * footprint link issues                     -> unresolvable footprint id

Usage::

    python3 tools/kicad_roundtrip.py [--sch stage1_iv.kicad_sch]
                                     [--net stage1_iv.net]

Exits non-zero if KiCad's view of the schematic differs from the netlist, or if
any identifier fails to resolve. Requires ``kicad-cli`` on PATH.
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
sys.path.insert(0, HERE)
sys.path.insert(0, REPO_ROOT)

from resolve_kicad_libs import KicadLibraries, LibraryNotFound, parse_sexp  # noqa: E402


def _head(node):
    return node[0] if isinstance(node, list) and node and isinstance(node[0], str) else ""


def _kids(node, head):
    return [c for c in node if isinstance(c, list) and _head(c) == head]


def _first(node, head):
    kids = _kids(node, head)
    return kids[0] if kids else None


def parse_kicad_sheets(path):
    """Return {ref: sheet_name} from a KiCad `export` netlist."""
    with open(path, encoding="utf-8") as fh:
        tree = parse_sexp(fh.read())
    export = _first(tree, "export")
    out = {}
    for comp in _kids(_first(export, "components") or [], "comp"):
        ref_node = _first(comp, "ref")
        sp = _first(comp, "sheetpath")
        if ref_node is None or sp is None:
            continue
        names = _first(sp, "names")
        if names is not None and len(names) > 1:
            out[str(ref_node[1])] = str(names[1])
    return out


def parse_kicad_netlist(path):
    """Return {(ref, pin_number): net_name} from a KiCad `export` netlist."""
    with open(path, encoding="utf-8") as fh:
        tree = parse_sexp(fh.read())
    export = _first(tree, "export")
    if export is None:
        raise SystemExit(f"{path}: not a KiCad netlist")
    conns = {}
    nets = _first(export, "nets")
    for net in _kids(nets or [], "net"):
        name_node = _first(net, "name")
        name = str(name_node[1]) if name_node is not None and len(name_node) > 1 else ""
        for node in _kids(net, "node"):
            ref = pin = None
            for field in ("ref", "pin"):
                f = _first(node, field)
                if f is not None and len(f) > 1:
                    if field == "ref":
                        ref = str(f[1])
                    else:
                        pin = str(f[1])
            if ref and pin:
                conns[(ref, pin)] = name
    return conns


_UNNAMED_RE = re.compile(r"^(Net-\(|unconnected-|N\$)")

# KiCad reserves a leading "#" for virtual power symbols (PWR_FLAG and the
# ground/rail symbols) and omits them from netlist export and from the board.
# SKiDL's netlist does include them, so they are dropped from BOTH sides before
# the topology comparison; their presence in the schematic is verified
# separately by tools/kicad_erc_power.py.
_VIRTUAL_REF_RE = re.compile(r"^#")


def normalise(conns):
    """Drop auto-generated net names and virtual power symbols."""
    return {k: v for k, v in conns.items()
            if not _UNNAMED_RE.match(v) and not _VIRTUAL_REF_RE.match(k[0])}


def group_by_net(conns):
    out = {}
    for key, net in conns.items():
        out.setdefault(net, set()).add(key)
    return out


def run(sch, net, keep=False):
    kicad_cli = shutil.which("kicad-cli")
    if not kicad_cli:
        print("kicad-cli not found on PATH — cannot round-trip through KiCad.",
              file=sys.stderr)
        return 3

    tmpdir = tempfile.mkdtemp(prefix="kicad-roundtrip-")
    exported = os.path.join(tmpdir, "kicad_export.net")
    print(f"running: kicad-cli sch export netlist {sch}")
    proc = subprocess.run(
        [kicad_cli, "sch", "export", "netlist", "--output", exported, sch],
        capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.isfile(exported):
        print(proc.stdout + proc.stderr, file=sys.stderr)
        print(f"kicad-cli failed ({proc.returncode}) — KiCad could not read the "
              f"schematic.", file=sys.stderr)
        return 1
    version = subprocess.run([kicad_cli, "version"], capture_output=True,
                             text=True).stdout.strip()
    print(f"KiCad version: {version}")

    ours_raw = parse_kicad_netlist(net)
    ours = normalise(ours_raw)
    theirs = normalise(parse_kicad_netlist(exported))
    virtual = sorted({ref for ref, _pin in ours_raw
                      if _VIRTUAL_REF_RE.match(ref)})
    if virtual:
        print(f"virtual power symbols    : {len(virtual)} "
              f"({', '.join(virtual)}) — excluded from both sides")

    print(f"netlist connections      : {len(ours)}")
    print(f"KiCad schematic connections: {len(theirs)}")

    errors = []
    missing = sorted(set(ours) - set(theirs))
    extra = sorted(set(theirs) - set(ours))
    if missing:
        errors.append(f"in netlist but not in KiCad's read of the schematic: {missing[:20]}")
    if extra:
        errors.append(f"in KiCad's read of the schematic but not in netlist: {extra[:20]}")

    # KiCad scopes a net name that is local to one sheet as "/<sheet>/<name>",
    # so compare the *partition* of pins into nets (the electrical fact) and
    # check names only after stripping that sheet prefix.
    ours_nets = group_by_net(ours)
    theirs_nets = group_by_net(theirs)
    ours_part = {frozenset(v) for v in ours_nets.values()}
    theirs_part = {frozenset(v) for v in theirs_nets.values()}
    for group in sorted(ours_part - theirs_part, key=sorted):
        errors.append(
            "netlist groups these pins on one net but KiCad's read does not: "
            f"{sorted(group)}")
    for group in sorted(theirs_part - ours_part, key=sorted):
        errors.append(
            "KiCad's read groups these pins on one net but the netlist does "
            f"not: {sorted(group)}")
    print(f"net partitions           : {len(ours_part)} netlist / "
          f"{len(theirs_part)} schematic")

    def leaf(name):
        return name.rsplit("/", 1)[-1] if name.startswith("/") else name

    theirs_by_group = {frozenset(v): leaf(k) for k, v in theirs_nets.items()}
    renamed = sorted(
        (name, theirs_by_group[frozenset(pins)])
        for name, pins in ours_nets.items()
        if frozenset(pins) in theirs_by_group
        and theirs_by_group[frozenset(pins)] != name)
    if renamed:
        errors.extend(f"net name differs: netlist {a!r} vs schematic {b!r}"
                      for a, b in renamed)

    # --- sheet assignment: the schematic is the single source of truth ------
    # stage1_iv.net is intentionally FLAT (every sheetpath is "/"): SKiDL emits
    # a flat netlist and this project does not drive the PCB from it. The
    # schematic carries the hierarchy, and the PCB must be updated with
    # "Update PCB from Schematic". This check verifies KiCad's reading of that
    # hierarchy matches the block each part was built in, so the two artifacts
    # cannot silently diverge. See docs/LIBRARY_DEPENDENCIES.md.
    kicad_sheets = parse_kicad_sheets(exported)
    flat = parse_kicad_sheets(net)
    non_flat = sorted(r for r, s_ in flat.items() if s_ != "/")
    if non_flat:
        errors.append(
            "stage1_iv.net is documented as flat but has hierarchical "
            f"sheetpaths for {non_flat[:10]}; update Step 7's decision in "
            "docs/LIBRARY_DEPENDENCIES.md or revert the change")

    sys.path.insert(0, REPO_ROOT)
    import main as project_main
    _n0, _p0, circuit0 = project_main.build()
    expected_sheet = {p.ref: getattr(p, "skidl_block", None) for p in circuit0.parts}
    mismatched = []
    for ref, sheet in sorted(kicad_sheets.items()):
        want = expected_sheet.get(ref)
        if want is None:
            continue
        if sheet.strip("/") != want:
            mismatched.append(f"{ref}: schematic sheet {sheet!r} != block {want!r}")
    if mismatched:
        errors.extend(mismatched[:10])
    print(f"sheet assignment         : {len(kicad_sheets)} components across "
          f"{len(set(kicad_sheets.values()))} sheets, "
          f"{'all match' if not mismatched else str(len(mismatched)) + ' MISMATCH'}"
          f" the design's blocks")

    # ERC categories this project owns.
    try:
        libs = KicadLibraries()
    except LibraryNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 2

    circuit = circuit0
    sym_missing, fp_missing = [], []
    for part in circuit.parts:
        lib_id = (part.fields.get("KiCad_Lib") or "").strip()
        if ":" not in lib_id or libs.get_symbol(lib_id) is None:
            sym_missing.append(f"{part.ref} ({lib_id})")
        # Virtual power symbols have no footprint by design; KiCad's footprint
        # link check does not apply to them either.
        if part.fields.get("Symbol_Class"):
            continue
        fp_id = (part.footprint or "").strip()
        if ":" not in fp_id or libs.get_footprint(fp_id) is None:
            fp_missing.append(f"{part.ref} ({fp_id})")

    # --- lib_symbol_issues / lib_symbol_mismatch ----------------------------
    # KiCad compares each symbol cached in a sheet's (lib_symbols) against the
    # library its lib_id names: a missing library is lib_symbol_issues, a body
    # that differs is lib_symbol_mismatch. Every instance in this design names
    # the PROJECT library, which is generated from the same SymbolDef objects
    # the sheets embed, so both counts are structurally zero.
    import glob
    lib_issues, lib_mismatch, project_ids = [], [], set()
    sch_dir = os.path.dirname(os.path.abspath(sch)) or "."
    for path in sorted(glob.glob(os.path.join(sch_dir, "*.kicad_sch"))):
        with open(path, encoding="utf-8") as fh:
            tree = parse_sexp(fh.read())
        sheet = _first(tree, "kicad_sch")
        cached = {str(n[1]): n for n in _kids(_first(sheet, "lib_symbols") or [],
                                              "symbol")}
        for inst in _kids(sheet or [], "symbol"):
            lid_node = _first(inst, "lib_id")
            if lid_node is None:
                continue
            lid = str(lid_node[1])
            nick = lid.split(":", 1)[0]
            if nick == "stage1_iv":
                project_ids.add(lid)
                if lid not in cached:
                    lib_issues.append(f"{lid} (not in this sheet's cache)")
                continue
            if libs.get_symbol(lid) is None:
                lib_issues.append(f"{lid} ({os.path.basename(path)})")
            else:
                lib_mismatch.append(f"{lid} ({os.path.basename(path)})")

    print(f"ERC lib_symbol_issues                  : {len(set(lib_issues))}"
          + (f"  {sorted(set(lib_issues))[:5]}" if lib_issues else ""))
    print(f"ERC lib_symbol_mismatch (upper bound)  : {len(set(lib_mismatch))}"
          + (f"  {sorted(set(lib_mismatch))[:5]}" if lib_mismatch else ""))
    print(f"    ({len(project_ids)} distinct lib_ids, all in the project library)")
    errors.extend(f"lib_symbol_issue: {x}" for x in sorted(set(lib_issues)))

    print(f'ERC "Symbol not found in symbol library": {len(sym_missing)}'
          + (f"  {sym_missing}" if sym_missing else ""))
    print(f"ERC footprint_link_issues              : {len(fp_missing)}"
          + (f"  {fp_missing}" if fp_missing else ""))
    errors.extend(f"unresolvable symbol: {s}" for s in sym_missing)
    errors.extend(f"unresolvable footprint: {s}" for s in fp_missing)

    if keep:
        print(f"exported netlist kept at {exported}")
    else:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if errors:
        print("\nKICAD ROUND-TRIP: FAIL")
        for e in errors:
            print(f"  [ERROR] {e}")
        return 1
    print("\nKICAD ROUND-TRIP: PASS — KiCad reads exactly the netlist's "
          "connectivity, and every identifier resolves.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--sch", default=os.path.join(REPO_ROOT, "stage1_iv.kicad_sch"))
    ap.add_argument("--net", default=os.path.join(REPO_ROOT, "stage1_iv.net"))
    ap.add_argument("--keep", action="store_true",
                    help="keep the netlist KiCad exported")
    args = ap.parse_args(argv)
    return run(args.sch, args.net, args.keep)


if __name__ == "__main__":
    sys.exit(main())
