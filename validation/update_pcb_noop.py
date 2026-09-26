#!/usr/bin/env python3
"""
update_pcb_noop.py — would "Update PCB from Schematic" change anything?
======================================================================

What this is
------------
A re-implementation, in Python, of the comparisons KiCad's
``BOARD_NETLIST_UPDATER`` makes in **dry-run mode** — the mode that produces
the "Update PCB from Schematic" preview. Every action that dialog would report
is reproduced here as an :class:`Action`, so the pipeline can assert that the
generated board is already the board KiCad would produce, without KiCad.

The board this repository generates is an ARTEFACT, like the schematic. Both
come from ``main.py``. If opening the board and pressing "Update PCB from
Schematic" mutates it, then the two artefacts disagree, and every DRC result
taken after that press describes a board no generator wrote. That is exactly
what happened to this project for four rounds of review.

What it is NOT
--------------
It is not KiCad. It cannot be: KiCad is not installable in this environment
past 7.0.11, and the file format here is 10.0.5's. So this file states its
provenance instead of asserting its authority — every rule below cites the
KiCad 10.0.5 source line it mirrors, and the rules are **calibrated against
real output**: ``validation/fixtures/kicad_update_pcb_baseline.json`` records
what KiCad 10.0.5 actually reported on the board this project shipped before
these rules existed, and ``self_test()`` re-injects each of those defects and
requires this checker to report the same category with the same count. A rule
that cannot reproduce KiCad's own numbers is a rule that is wrong.

Two captures are recorded, not one, and the second is the more instructive.
After the first six categories were fixed, KiCad still reported four changes —
and printed both of its ``Reconnect`` lines with an identical net name on each
side, because it unescapes net names before reporting them
(``board_netlist_updater.cpp`` 1195-1210). The board held the display form,
the netlist the stored one, and no log could have shown the difference. Two
consequences for this file: the escaping rule is calibrated as its own capture,
and ``self_test()`` fails any injection that **changes nothing** — otherwise a
generator that quietly stopped escaping would take this model down with it and
the two would agree, at zero, on a board that is wrong.

Categories, and where each comes from in KiCad 10.0.5
----------------------------------------------------
=================== ======================================================
``add``             a footprint with no resolvable ``(path ...)``; the
                    updater adds a fresh library copy
                    (board_netlist_updater.cpp ``addNewFootprint``)
``value``           ``updateComponentParameters``
``fpid``            ``fpidsEquivalent`` (line 1324)
``sheetname``       line 676, against ``GetHumanReadablePath()``
``sheetfile``       line 695, against the ``Sheetfile`` property
``fields``          lines 508-560, against the netlist ``<fields>`` set
``units``           ``updateComponentUnits`` (line 1262)
``attributes``      lines 735-845, the fabrication attribute bits
``net``             ``updateComponentPadConnections``; covers both "Add net"
                    and "Connect/Reconnect <ref> pin N"
=================== ======================================================

Usage::

    python3 validation/update_pcb_noop.py            # check the board
    python3 validation/update_pcb_noop.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
for _p in (_REPO_ROOT, os.path.join(_REPO_ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.resolve_kicad_libs import parse_sexp          # noqa: E402

BOARD_FILE = os.path.join(_REPO_ROOT, "stage1_iv.kicad_pcb")
BASELINE_FILE = os.path.join(_HERE, "fixtures",
                             "kicad_update_pcb_baseline.json")

# Footprints the board places that no symbol names. KiCad leaves these alone:
# the updater only walks footprints it can match to a netlist component, which
# is why the reviewer's log has no MH1..MH4 line at all.
BOARD_ONLY_PREFIXES = ("H",)


class Action:
    """One line "Update PCB from Schematic" would print."""

    __slots__ = ("category", "ref", "detail")

    def __init__(self, category, ref, detail):
        self.category = category
        self.ref = ref
        self.detail = detail

    def __str__(self):
        return f"{self.category}: {self.ref}: {self.detail}"

    def __repr__(self):                                    # pragma: no cover
        return f"<Action {self}>"


# ---------------------------------------------------------------------------
# Reading the board
# ---------------------------------------------------------------------------

def _head(node):
    return str(node[0]) if isinstance(node, list) and node else ""


def _kids(node, name):
    return [c for c in node if isinstance(c, list) and _head(c) == name]


def _first(node, name):
    for c in _kids(node, name):
        return c
    return None


def _text(node, name, index=1):
    child = _first(node, name)
    if child is None or len(child) <= index:
        return None
    return str(child[index])


class BoardFootprint:
    """The parts of a placed footprint the netlist updater looks at."""

    def __init__(self, node):
        self.node = node
        self.fpid = str(node[1]) if len(node) > 1 else ""
        self.path = _text(node, "path") or ""
        self.sheetname = _text(node, "sheetname") or ""
        self.sheetfile = _text(node, "sheetfile") or ""

        self.fields = {}
        for prop in _kids(node, "property"):
            if len(prop) > 2:
                self.fields[str(prop[1])] = str(prop[2])
        self.ref = self.fields.get("Reference", "")
        self.value = self.fields.get("Value", "")

        attr = _first(node, "attr")
        self.attributes = {str(a) for a in attr[1:]} if attr is not None \
            else set()

        self.units = []
        units = _first(node, "units")
        if units is not None:
            for unit in _kids(units, "unit"):
                pins = _first(unit, "pins")
                self.units.append((
                    _text(unit, "name") or "",
                    [str(p) for p in (pins[1:] if pins is not None else [])]))

        self.pad_nets = {}
        for pad in _kids(node, "pad"):
            net = _first(pad, "net")
            name = str(pad[1]) if len(pad) > 1 else ""
            self.pad_nets.setdefault(
                name, str(net[2]) if net is not None and len(net) > 2 else "")

    @property
    def comparable_fields(self):
        """What ``fpFieldsAsMap`` holds: every field but Reference and Value.

        board_netlist_updater.cpp 510-518 skips exactly those two (plus the
        Component Class field, which this design never writes).
        """
        return {k: v for k, v in self.fields.items()
                if k not in ("Reference", "Value")}


def read_board(path):
    with open(path, encoding="utf-8") as fh:
        tree = parse_sexp(fh.read())
    root = None
    for child in tree:
        if _head(child) == "kicad_pcb":
            root = child
            break
    if root is None:
        raise RuntimeError(f"{path}: not a kicad_pcb file")
    return board_from_root(root)


def board_from_root(root):
    nets = {str(n[2]) for n in _kids(root, "net")
            if len(n) > 2 and str(n[2])}
    return root, [BoardFootprint(n) for n in _kids(root, "footprint")], nets


# ---------------------------------------------------------------------------
# What the schematic will hand KiCad
# ---------------------------------------------------------------------------

def netlist_components(circuit=None):
    """{ref: component} exactly as the netlist exporter would present them."""
    from generators.schematic import (
        CircuitModel, sheet_file, sheet_name, symbol_fields,
        symbol_instance_path, unconnected_net_name, unit_info,
    )
    if circuit is None:
        import main as project_main
        _n, _p, circuit = project_main.build()
    model = CircuitModel(circuit)

    out = {}
    for part in circuit.parts:
        if not (part.footprint or "").strip():
            continue                     # schematic-only (PWR_FLAG)
        block = part.skidl_block
        pads = {}
        for pin in part.pins:
            num = str(pin.num)
            net = getattr(pin, "net", None)
            name = getattr(net, "name", None) if net is not None else None
            if net is None or name in (None, "", "__NOCONNECT"):
                pads[num] = unconnected_net_name(part.ref, num, pin.name)
            else:
                pads[num] = model.board_net_name(name)
        out[part.ref] = {
            "path": symbol_instance_path(block, part.ref),
            "fpid": part.footprint.strip(),
            "value": str(part.value),
            "sheetname": sheet_name(block),
            "sheetfile": sheet_file(block),
            "fields": dict(symbol_fields(model, part)),
            "units": unit_info(model.part_sym[part.ref]),
            "pads": pads,
            # Nothing in this schematic marks a symbol excluded from the BOM,
            # the board or the position files, and none is DNP; so the netlist
            # carries none of those properties and the footprint must not
            # claim them. See generators/schematic.py SYMBOL_DRIVEN_FP_ATTRS.
            "attributes": set(),
        }
    return out


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------

# Attribute bits that describe the PACKAGE, not the symbol. KiCad never
# rewrites these from the netlist, so they are not compared.
_PACKAGE_ATTRS = {"smd", "through_hole", "board_only",
                  "allow_missing_courtyard", "allow_soldermask_bridges",
                  "dnp"}

_SYMBOL_ATTRS = {"exclude_from_bom", "exclude_from_pos_files",
                 "exclude_from_board"}


def dry_run(board_path=BOARD_FILE, circuit=None, root=None):
    """Every change "Update PCB from Schematic" would make. Empty == no-op.

    ``root`` — an already-parsed ``(kicad_pcb ...)`` to use instead of reading
    ``board_path``.
    """
    _root, footprints, board_nets = read_board(board_path) if root is None \
        else board_from_root(root)
    want = netlist_components(circuit)

    actions = []
    by_path = {}
    for fp in footprints:
        if fp.path:
            by_path.setdefault(fp.path, []).append(fp)

    for ref, comp in sorted(want.items()):
        matches = by_path.get(comp["path"], [])
        if not matches:
            # KiCad matches board footprint to symbol by instance path and by
            # nothing else. No match means the symbol has no footprint, and
            # the updater adds a fresh one from the library.
            actions.append(Action("add", ref,
                                  f"no footprint carries path {comp['path']}"))
            continue
        if len(matches) > 1:
            actions.append(Action("add", ref,
                                  f"{len(matches)} footprints share path "
                                  f"{comp['path']}"))
            continue
        fp = matches[0]

        if fp.ref != ref:
            actions.append(Action("reference", ref,
                                  f"board says {fp.ref!r}"))
        if fp.value != comp["value"]:
            actions.append(Action("value", ref,
                                  f"{fp.value!r} -> {comp['value']!r}"))
        if fp.fpid != comp["fpid"]:
            actions.append(Action("fpid", ref,
                                  f"{fp.fpid!r} -> {comp['fpid']!r}"))
        if fp.sheetname != comp["sheetname"]:
            actions.append(Action("sheetname", ref,
                                  f"{fp.sheetname!r} -> "
                                  f"{comp['sheetname']!r}"))
        if fp.sheetfile != comp["sheetfile"]:
            actions.append(Action("sheetfile", ref,
                                  f"{fp.sheetfile!r} -> "
                                  f"{comp['sheetfile']!r}"))

        have, need = fp.comparable_fields, comp["fields"]
        if have != need:
            missing = sorted(k for k in need if have.get(k) != need[k])
            extra = sorted(k for k in have if k not in need)
            actions.append(Action("fields", ref,
                                  f"differs: {missing or ''}{extra or ''}"))

        if fp.units != [(n, list(p)) for n, p in comp["units"]]:
            actions.append(Action("units", ref,
                                  f"{fp.units} -> {comp['units']}"))

        have_attrs = fp.attributes & _SYMBOL_ATTRS
        need_attrs = comp["attributes"] & _SYMBOL_ATTRS
        for attr in sorted(have_attrs - need_attrs):
            actions.append(Action("attributes", ref, f"remove {attr!r}"))
        for attr in sorted(need_attrs - have_attrs):
            actions.append(Action("attributes", ref, f"add {attr!r}"))

        for num, net in sorted(comp["pads"].items()):
            if num not in fp.pad_nets:
                actions.append(Action("net", ref,
                                      f"pin {num} has no pad in {fp.fpid}"))
            elif fp.pad_nets[num] != net:
                actions.append(Action("net", ref,
                                      f"pin {num}: {fp.pad_nets[num]!r} -> "
                                      f"{net!r}"))
            if net and net not in board_nets:
                actions.append(Action("net", ref, f"board has no net {net!r}"))

    # Footprints with no symbol. Mounting holes are the legitimate case.
    claimed = {c["path"] for c in want.values()}
    for fp in footprints:
        if fp.ref.startswith(BOARD_ONLY_PREFIXES):
            continue
        if fp.path not in claimed:
            actions.append(Action("extra", fp.ref,
                                  "on the board, not in the schematic"))
    return actions


def summarise(actions):
    counts = {}
    for a in actions:
        counts[a.category] = counts.get(a.category, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Calibration: does this checker reproduce KiCad's own numbers?
# ---------------------------------------------------------------------------

def _strip(node, name):
    for i in range(len(node) - 1, -1, -1):
        if isinstance(node[i], list) and _head(node[i]) == name:
            del node[i]


def _drop_per_footprint(name):
    def mutate(root):
        for fp in _kids(root, "footprint"):
            _strip(fp, name)
    return mutate


def _drop_fields(root):
    """Undo generators/pcb.py's add_field: back to Reference and Value only."""
    for fp in _kids(root, "footprint"):
        for i in range(len(fp) - 1, -1, -1):
            child = fp[i]
            if isinstance(child, list) and _head(child) == "property" \
                    and len(child) > 1 and str(child[1]) not in \
                    ("Reference", "Value"):
                del fp[i]


def _drop_unconnected_nets(root):
    """Undo the synthetic nets: pads go back to carrying no net at all."""
    for i in range(len(root) - 1, -1, -1):
        child = root[i]
        if isinstance(child, list) and _head(child) == "net" \
                and len(child) > 2 and str(child[2]).startswith(
                    "unconnected-("):
            del root[i]
    for fp in _kids(root, "footprint"):
        for pad in _kids(fp, "pad"):
            for i in range(len(pad) - 1, -1, -1):
                child = pad[i]
                if isinstance(child, list) and _head(child) == "net" \
                        and len(child) > 2 and str(child[2]).startswith(
                            "unconnected-("):
                    del pad[i]


def _restore_testpoint_attrs(root):
    """Put back the library attributes strip_attrs removed from TP1..TP8."""
    from tools.resolve_kicad_libs import Atom, SexpList
    for fp in _kids(root, "footprint"):
        if not str(fp[1]).startswith("TestPoint:"):
            continue
        fp.insert(len(fp), SexpList([Atom("attr"),
                                     Atom("exclude_from_pos_files"),
                                     Atom("exclude_from_bom")]))


def _unescape_net_names(root):
    """Store net names in their DISPLAY form, as this board once did.

    ``{slash}`` back to ``/`` everywhere a net name appears. KiCad reported
    exactly four actions for this — two nets and two pads — and printed both
    Reconnect lines with the same name on each side, because it unescapes
    before reporting. If the generator ever stops escaping, this injection
    becomes a no-op and the calibration fails instead of quietly agreeing with
    a board that is wrong.
    """
    from tools.resolve_kicad_libs import Atom

    def fix(node):
        for child in node:
            if isinstance(child, list):
                if _head(child) == "net" and len(child) > 2 \
                        and "{slash}" in str(child[2]):
                    child[2] = Atom(str(child[2]).replace("{slash}", "/"),
                                    0, True)
                fix(child)

    fix(root)


# Categories KiCad reports once per footprint (every footprint lacked the
# metadata in the captured board). Their calibrated quantity is "one action per
# footprint", checked against the footprint count of the board under test.
_PER_FOOTPRINT = {"sheetname", "sheetfile", "fields", "units"}

# name -> (which recorded KiCad capture holds the expected count,
#          category it must be reported as,
#          how to put that defect back into the current board)
_INJECTIONS = {
    "sheetname": ("categories", "sheetname",
                  _drop_per_footprint("sheetname")),
    "sheetfile": ("categories", "sheetfile",
                  _drop_per_footprint("sheetfile")),
    "units": ("categories", "units", _drop_per_footprint("units")),
    "fields": ("categories", "fields", _drop_fields),
    "unconnected-nets": ("categories", "net", _drop_unconnected_nets),
    "testpoint-attrs": ("categories", "attributes",
                        _restore_testpoint_attrs),
    "netname-escaping": ("escaping_capture", "net", _unescape_net_names),
}


def self_test(board_path=BOARD_FILE, verbose=True):
    """Re-inject the defects KiCad found, and require the same verdict.

    ``kicad_update_pcb_baseline.json`` is not this project's opinion: it is a
    transcription of the KiCad 10.0.5 dialog output the reviewer captured on
    the board this repository shipped before any of the metadata above
    existed. Removing a category from the current board must reproduce the
    count KiCad reported for it. Under-reporting is the failure mode that
    matters — a checker that says "0 changes" while KiCad says 110 is worse
    than no checker, and this project has already shipped one of those.
    """
    from tools.resolve_kicad_libs import write_sexp

    with open(BASELINE_FILE, encoding="utf-8") as fh:
        baseline = json.load(fh)

    problems = []

    # The circuit is built once: every dry run below compares against the
    # same schematic, and building it is most of a dry run's cost.
    import main as project_main
    _n, _p, circuit = project_main.build()

    live = dry_run(board_path, circuit)
    if live:
        problems.append(
            f"the board itself is not a no-op ({len(live)} actions); "
            f"calibration is meaningless until it is")
        if verbose:
            for a in live[:10]:
                print(f"  live: {a}")
        return problems

    with open(board_path, encoding="utf-8") as fh:
        text = fh.read()
    # Copper (tracks, vias, pours) is neither read by the updater model nor
    # touched by any injection; on a routed board it is most of the file, so
    # it is dropped once here rather than re-parsed for every injection.
    root = next(c for c in parse_sexp(text) if _head(c) == "kicad_pcb")
    root[:] = [c for c in root
               if _head(c) not in ("segment", "arc", "via", "zone")]
    text = write_sexp(root) + "\n"

    n_linked = None          # symbol-linked footprints, counted on demand
    for name, (capture, category, mutate) in sorted(_INJECTIONS.items()):
        root = next(c for c in parse_sexp(text) if _head(c) == "kicad_pcb")
        before = next(c for c in parse_sexp(text) if _head(c) == "kicad_pcb")
        mutate(root)
        if root == before:
            # The injection changed nothing, so the "defect" is not in the
            # board to begin with and the count below would be vacuous. This
            # is how a generator that quietly stopped escaping net names would
            # otherwise pass: the board would already hold the wrong form.
            problems.append(
                f"injection {name!r} did not change the board. It is testing "
                f"nothing, so its result proves nothing. Either the defect it "
                f"reintroduces is already present in the generated board, or "
                f"the injection is stale.")
            continue
        counts = summarise(dry_run(circuit=circuit, root=root))
        source = baseline if capture == "categories" else baseline[capture]
        reported = source["categories"].get(category)
        want, note = reported, ""
        if capture == "categories" and category in _PER_FOOTPRINT:
            # KiCad printed exactly one line per footprint for these, on a
            # board of footprints_on_board footprints. What is calibrated is
            # that ratio, so it is held to exactly one per footprint on the
            # board being checked — which has more parts than the captured
            # one since the bipolar-measurement change (R32, R33, C44).
            captured_fps = baseline["footprints_on_board"]
            if reported != captured_fps:
                problems.append(
                    f"baseline {category!r} = {reported} is not one per "
                    f"footprint ({captured_fps}); the per-footprint scaling "
                    f"below does not apply to it")
                continue
            if n_linked is None:
                n_linked = len(netlist_components(circuit))
            want = n_linked
            note = f", i.e. one per footprint: {reported}/{captured_fps}"
        got = counts.get(category, 0)
        if verbose:
            print(f"  inject {name:<17} -> {category}={got} "
                  f"(KiCad 10.0.5 reported {reported}{note})")
        if got != want:
            problems.append(
                f"reintroducing {name} yields {got} {category!r} action(s), "
                f"but KiCad 10.0.5 reported {reported}{note} on the board "
                f"that carried that defect, so {want} are expected here. "
                f"This checker does not see what KiCad sees; fix the rule, "
                f"do not trust the result.")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--board", default=BOARD_FILE)
    ap.add_argument("--self-test", action="store_true",
                    help="verify the rules against KiCad 10.0.5's own output")
    args = ap.parse_args(argv)

    if args.self_test:
        problems = self_test(args.board)
        if problems:
            for p in problems:
                print(f"FAIL {p}", file=sys.stderr)
            return 1
        print("OK  calibration matches the recorded KiCad 10.0.5 output")
        return 0

    actions = dry_run(args.board)
    if not actions:
        print("OK  Update PCB from Schematic would change nothing")
        return 0
    counts = summarise(actions)
    print(f"FAIL {len(actions)} change(s) KiCad would make:", file=sys.stderr)
    for cat, n in sorted(counts.items()):
        print(f"  {cat:<12} {n}", file=sys.stderr)
    for a in actions[:40]:
        print(f"    {a}", file=sys.stderr)
    if len(actions) > 40:
        print(f"    ... {len(actions) - 40} more", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
