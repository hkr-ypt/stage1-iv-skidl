"""
routed_board_check.py — the board of record, checked.
======================================================

``stage1_iv.kicad_pcb`` is generated from three sources (the SKiDL circuit,
``layout/stage1_iv.layout`` and ``layout/bipolar.eco``; see
generators/routed_pcb.py). Its placement and nearly all of its copper were
decided by a person, on the board kept in ``unipolar-routed/``. This module checks the
generated board in three layers, and says which ones it could not run.

1. The board is what the sources say, and agrees with the circuit
   (fails on any finding):

   ==========================  ===============================================
   ``generated_from_source``   the file is byte-for-byte what circuit + layout
                               + ECO produce now: nothing was edited by hand
   ``board_parity``            every part, pad net and net of the circuit
   ``schematic_linkage``       every footprint resolves to a schematic symbol
   ``update_pcb_noop``         KiCad's Update PCB from Schematic changes nothing
   ``update_pcb_calibration``  ...and that model still matches real KiCad output
   ``schema_version``          board and vendored footprints share a schema
   ``netclass_coverage``       every net has an explicit netclass
   ``fab_conformance``         no netclass asks for less than the fab can build
   ``dru_parses``              the custom rules file is one KiCad can compile
   ``rule_satisfiability``     no clearance rule demands more than a package has
   ``courtyards_defined``      every footprint has a courtyard
   ==========================  ===============================================

2. The repository's placement heuristics, **relative to the original**
   (validation/drc_check.py's courtyard, edge, ground-split, hole and
   silkscreen checks). They were written for the auto-placer and are coarser
   than KiCad — the courtyard check compares bounding boxes on both sides at
   once, so an SMD part on the back under a DIP on the front "overlaps" it.
   On the human layout they report findings that are properties of that layout
   (or of the heuristic). Every such finding on ``unipolar-routed/diodes.kicad_pcb`` is
   listed as pre-existing; the check fails on any finding the original does
   NOT have — that is, on anything the bipolar ECO introduced.

3. KiCad's own DRC (``kicad-cli pcb drc --refill-zones --all-track-errors
   --schematic-parity``), the authority. It needs KiCad 10 to read the board;
   without it this layer is SKIPPED, loudly — never passed.

   * **as-built rules** — the netclass and minimums the board was actually
     routed to, as captured in the layout source ``(rules ...)`` from
     ``unipolar-routed/diodes.kicad_pro``, plus the repository's scoped rules for the
     pads inside U2, U4 and J3 (``stage1_iv.kicad_dru``), with every KiCad
     check on — including clearance, which the routed project had switched
     off. Must be **clean**: 0 errors, 0 unconnected items, and no warning the
     original does not also have. The original is run under the same rules
     and must be error-free too, which proves the captured rules are ones the
     human board meets.
   * **repository netclass policy** — ``stage1_iv.kicad_pro`` /
     ``stage1_iv.kicad_dru`` as tools/gen_project_config.py writes them
     (libraries/netclasses.py). These are stricter than what the board was
     routed to (0.25-0.30 mm for sense and power nets against the human's
     0.20 mm): the original has clearance errors under them, and
     layout/policy_clearance.eco re-routes every one. Required, with zones
     refilled at these clearances: **0 errors** (clearance included, whether
     or not the original has the same one), 0 unconnected items, 0
     schematic-parity problems, and no warning the original does not also
     have — matched by what and where (item kind, layer, position, measured
     value), not by net, because the ECO renames the nets of a few pads.

Run standalone::

    python3 validation/routed_board_check.py
    python3 validation/routed_board_check.py --json
    python3 validation/routed_board_check.py --no-kicad
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_REPO_ROOT, "tools"), _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from validation import drc_check as _dc  # noqa: E402
from libraries.netclasses import check_against_fab, FAB  # noqa: E402

BOARD_FILE = os.path.join(_REPO_ROOT, "stage1_iv.kicad_pcb")
from generators.layout import REFERENCE_BOARD as REFERENCE  # noqa: E402
PROJECT = os.path.join(_REPO_ROOT, "stage1_iv.kicad_pro")
DRU = os.path.join(_REPO_ROOT, "stage1_iv.kicad_dru")
CACHE_DIR = os.path.join(_REPO_ROOT, "build", "drc_cache")


class RoutedBoardError(RuntimeError):
    """The board of record has a defect, or is not what its sources say."""


# ---------------------------------------------------------------------------
# 1. absolute checks
# ---------------------------------------------------------------------------

def check_generated_from_source(board_path):
    """The file must be exactly what the sources generate: no hand edits."""
    import io
    import contextlib
    from generators.layout import LayoutError
    from generators.routed_pcb import composed_layout, parts_from_circuit, \
        emit_board
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            import main as project_main
            _n, _parts, circuit = project_main.build()
            layout, _applied = composed_layout()
            nodes, net_names, codes = parts_from_circuit(circuit)
            text, _report = emit_board(nodes, net_names, codes, layout)
    except LayoutError as exc:
        return [f"the layout source does not apply: {exc}"]
    except Exception as exc:                # noqa: BLE001 - reported, not hidden
        return [f"the sources do not generate a board "
                f"({type(exc).__name__}): {str(exc).splitlines()[0][:300]}"]
    with open(board_path, encoding="utf-8") as fh:
        have = fh.read()
    if have != text:
        return [f"{os.path.relpath(board_path, _REPO_ROOT)} is not what "
                f"circuit + layout/stage1_iv.layout + layout/*.eco generate. "
                f"Either it was edited by hand (put the change in a .eco) or "
                f"it is stale (run `python3 main.py`)."]
    return []


ABSOLUTE_CHECKS = (
    ("generated_from_source", None),
    ("fab_conformance", lambda b: check_against_fab()),
    ("dru_parses", lambda b: _dc.check_dru_parses()),
    ("schema_version", _dc.check_schema_version),
    ("schematic_linkage", _dc.check_schematic_linkage),
    ("board_parity", _dc.check_board_parity),
    ("update_pcb_noop", _dc.check_update_pcb_noop),
    ("update_pcb_calibration", _dc.check_update_pcb_calibration),
    ("netclass_coverage", _dc.check_netclass_coverage),
    ("rule_satisfiability", _dc.check_rule_satisfiability),
    ("courtyards_defined", _dc.check_courtyards_defined),
)


# ---------------------------------------------------------------------------
# 2. heuristics, relative to the original
# ---------------------------------------------------------------------------

def _ground_split(board):
    out = _dc.check_ground_split(board)
    return out[0] if isinstance(out, tuple) else out


_PAIR = re.compile(r"^(\S+) and (\S+) (courtyards overlap .*)$")


def _courtyard_overlap(board):
    """Findings name the pair in file order; the files order parts
    differently (circuit order vs. the order a person added them)."""
    out = []
    for p in _dc.check_courtyard_overlap(board):
        m = _PAIR.match(p)
        if m:
            a, b = sorted(m.group(1, 2), key=_dc_ref_key)
            p = f"{a} and {b} {m.group(3)}"
        out.append(p)
    return out


def _dc_ref_key(ref):
    head = ref.rstrip("0123456789")
    return (head, int(ref[len(head):] or -1))


RELATIVE_CHECKS = (
    ("courtyard_overlap", _courtyard_overlap),
    ("edge_clearance",
     lambda b: _dc.check_edge_clearance(b, FAB["edge_clearance"])),
    ("ground_split", _ground_split),
    ("hole_spacing", _dc.check_hole_spacing),
    ("silkscreen", lambda b: _dc.check_silkscreen(b, limit=None)[0]),
)


# ---------------------------------------------------------------------------
# 3. KiCad DRC
# ---------------------------------------------------------------------------

def layout_rules():
    """{'rules': {...}, 'netclass': {...}} from the layout source."""
    from generators.layout import load_layout
    from generators.routed_pcb import BASE_LAYOUT
    rules = load_layout(BASE_LAYOUT).rules
    out = {"rules": {}, "netclasses": {}}
    for child in rules[1:]:
        head = str(child[0])
        if head == "source":
            continue
        if head == "netclass":
            out["netclasses"][str(child[1])] = {
                str(c[0]): float(c[1]) for c in child[2:]}
        else:
            out["rules"][head] = float(child[1])
    return out


def as_built_project(policy):
    """The generated project with the layout's as-built rules in place of the
    repository policy: board minimums and netclasses exactly as captured, no
    netclass patterns (the routed project assigned none).

    Severities are NOT captured: they stay KiCad's defaults, every check an
    error. The routed project had ``"clearance": "ignore"`` — clearance was
    never checked on the human board — and a check that inherited that would
    prove nothing. Run with clearance on, the board's own 0.20 mm Default class
    fails only between adjacent pads INSIDE U2, U4 (VSSOP-8) and J3 (USB-C),
    whose 0.15 mm gaps are the package's; stage1_iv.kicad_dru narrows exactly
    those (libraries/netclasses.py), so it is applied here too."""
    pro = json.loads(json.dumps(policy))
    captured = layout_rules()
    ds = pro["board"]["design_settings"]
    ds["rules"].update(captured["rules"])
    template = next(c for c in pro["net_settings"]["classes"]
                    if c["name"] == "Default")
    classes = []
    for name, values in captured["netclasses"].items():
        cls = dict(template)
        cls["name"] = name
        cls.update(values)
        classes.append(cls)
    pro["net_settings"]["classes"] = classes
    pro["net_settings"]["netclass_patterns"] = []
    pro["net_settings"]["netclass_assignments"] = None
    return pro


def kicad_version():
    exe = shutil.which("kicad-cli")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "version"], capture_output=True, text=True,
                             timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


def _stage(work, board_path, project, dru_text, schematic):
    """A directory KiCad can open: board, project, rules, libraries."""
    os.makedirs(work, exist_ok=True)
    board = os.path.join(work, "stage1_iv.kicad_pcb")
    shutil.copy(board_path, board)
    with open(os.path.join(work, "stage1_iv.kicad_pro"), "w",
              encoding="utf-8") as fh:
        json.dump(project, fh, indent=2)
    if dru_text is not None:
        with open(os.path.join(work, "stage1_iv.kicad_dru"), "w",
                  encoding="utf-8") as fh:
            fh.write(dru_text)
    for table in ("fp-lib-table", "sym-lib-table"):
        src = os.path.join(_REPO_ROOT, table)
        if os.path.isfile(src):
            with open(src, encoding="utf-8") as fh:
                text = fh.read().replace("${KIPRJMOD}", _REPO_ROOT)
            with open(os.path.join(work, table), "w", encoding="utf-8") as fh:
                fh.write(text)
    if schematic:
        for name in os.listdir(_REPO_ROOT):
            if name.endswith(".kicad_sch"):
                shutil.copy(os.path.join(_REPO_ROOT, name), work)
    return board


def run_drc(board_path, project, dru_text, schematic, work):
    """kicad-cli DRC. Returns (report dict, None) or (None, reason)."""
    board = _stage(work, board_path, project, dru_text, schematic)
    out = os.path.join(work, "drc.json")
    cmd = ["kicad-cli", "pcb", "drc", "--refill-zones", "--all-track-errors",
           "--severity-all", "--format", "json", "-o", out]
    if schematic:
        cmd.append("--schematic-parity")
    try:
        proc = subprocess.run(cmd + [board], capture_output=True, text=True,
                              timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"kicad-cli pcb drc could not run: {exc}"
    if not os.path.isfile(out):
        return None, (f"kicad-cli pcb drc produced no report (exit "
                      f"{proc.returncode}): "
                      f"{(proc.stderr or proc.stdout).strip()[:300]}")
    with open(out, encoding="utf-8") as fh:
        return json.load(fh), None


def _cached_drc(board_path, project, dru_text, version, tag):
    """DRC of an input that never changes between runs (the reference)."""
    h = hashlib.sha256()
    for part in (open(board_path, "rb").read(),
                 json.dumps(project, sort_keys=True).encode(),
                 (dru_text or "").encode(), (version or "").encode()):
        h.update(hashlib.sha256(part).digest())
    path = os.path.join(CACHE_DIR, f"{tag}-{h.hexdigest()[:24]}.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    with tempfile.TemporaryDirectory() as tmp:
        data, why = run_drc(board_path, project, dru_text, False, tmp)
    if data is not None:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    return data, why


_NET = re.compile(r"\[[^\]]*\]")
_ACTUAL = re.compile(r"actual ([0-9.]+)")


def violation_key(v):
    """What and where — never which net (the ECO renames a few pads)."""
    items = tuple(sorted(
        (_NET.sub("[]", i["description"]), round(i["pos"]["x"], 3),
         round(i["pos"]["y"], 3)) for i in v.get("items", [])))
    m = _ACTUAL.search(v.get("description", ""))
    return (v["type"], v.get("severity", ""), items, m.group(1) if m else "")


def _describe(key):
    kind, sev, items, actual = key
    where = "; ".join(f"{d} @({x}, {y})" for d, x, y in items)
    return f"{kind}/{sev} {where}" + (f" (actual {actual} mm)" if actual else "")


def _counts(data):
    c = collections.Counter(f"{v['type']}/{v['severity']}"
                            for v in data.get("violations", []))
    return dict(sorted(c.items()))


def kicad_checks(board_path, reference=REFERENCE):
    """Layer 3. Returns (results dict, problems list)."""
    version = kicad_version()
    if version is None:
        return {"status": "SKIPPED", "reason": "kicad-cli is not installed"}, []
    with open(PROJECT, encoding="utf-8") as fh:
        policy = json.load(fh)
    dru_text = open(DRU, encoding="utf-8").read() if os.path.isfile(DRU) \
        else None
    built = as_built_project(policy)
    problems = []
    res = {"status": "PASS", "ran_with": f"kicad-cli {version}"}
    with tempfile.TemporaryDirectory() as tmp:
        board_pol, why = run_drc(board_path, policy, dru_text, True,
                                 os.path.join(tmp, "policy"))
        if board_pol is None:
            # KiCad before 10 cannot read the board's footprint (units ...).
            return {"status": "SKIPPED", "ran_with": f"kicad-cli {version}",
                    "reason": why}, []
        board_ab, why_ab = run_drc(board_path, built, dru_text, False,
                                   os.path.join(tmp, "as_built"))
    ref_pol, why_rp = _cached_drc(reference, policy, dru_text, version,
                                  "reference-policy")
    ref_ab, why_ra = _cached_drc(reference, built, dru_text, version,
                                 "reference-as-built")
    for label, data, why in (("as-built", board_ab, why_ab),
                             ("reference policy", ref_pol, why_rp),
                             ("reference as-built", ref_ab, why_ra)):
        if data is None:
            return {"status": "FAIL", "ran_with": f"kicad-cli {version}",
                    "reason": f"{label} DRC did not run: {why}"}, \
                [f"kicad_drc: {label} DRC did not run: {why}"]

    # --- as-built rules: clean ------------------------------------------
    ab_errors = [v for v in board_ab["violations"] if v["severity"] == "error"]
    ref_ab_errors = [v for v in ref_ab["violations"]
                     if v["severity"] == "error"]
    ref_warn = collections.Counter(violation_key(v) for v in
                                   ref_ab["violations"]
                                   if v["severity"] != "error")
    new_warn = collections.Counter(violation_key(v) for v in
                                   board_ab["violations"]
                                   if v["severity"] != "error") - ref_warn
    res["as_built"] = {
        "violations": _counts(board_ab),
        "unconnected": len(board_ab.get("unconnected_items", [])),
        "reference_violations": _counts(ref_ab),
        "new_warnings": [_describe(k) for k in new_warn.elements()],
    }
    for v in ab_errors:
        problems.append(f"as-built DRC error: {v['type']}: "
                        f"{v['description']} "
                        f"{[i['description'] for i in v['items']]}")
    for k in new_warn.elements():
        problems.append(f"as-built DRC warning the original does not have: "
                        f"{_describe(k)}")
    if board_ab.get("unconnected_items"):
        problems.append(f"{len(board_ab['unconnected_items'])} unconnected "
                        f"item(s): the board is not fully routed — "
                        + "; ".join(" / ".join(i["description"]
                                               for i in u["items"])
                                    for u in board_ab["unconnected_items"][:4]))
    if ref_ab_errors:
        problems.append(f"the captured as-built rules do not reproduce the "
                        f"original board's own DRC result: "
                        f"{os.path.relpath(reference, _REPO_ROOT)} has "
                        f"{len(ref_ab_errors)} error(s) under them "
                        f"({_counts(ref_ab)}). Re-capture the layout.")

    # --- policy rules: clean -------------------------------------------
    # The original is NOT clean under these rules (its routing is 0.20 mm
    # where the policy asks 0.25-0.30 mm); layout/policy_clearance.eco
    # re-routes those runs. So no policy error is accepted here, pre-existing
    # or not; a warning is accepted only if the original has it too.
    ref_keys = collections.Counter(violation_key(v)
                                   for v in ref_pol["violations"])
    have = collections.Counter(violation_key(v)
                               for v in board_pol["violations"])
    gone = ref_keys - have
    pol_errors = [v for v in board_pol["violations"]
                  if v["severity"] == "error"]
    ref_pol_warn = collections.Counter(violation_key(v) for v in
                                       ref_pol["violations"]
                                       if v["severity"] != "error")
    new_pol_warn = collections.Counter(violation_key(v) for v in
                                       board_pol["violations"]
                                       if v["severity"] != "error") \
        - ref_pol_warn
    res["policy"] = {
        "violations": _counts(board_pol),
        "unconnected": len(board_pol.get("unconnected_items", [])),
        "parity": len(board_pol.get("schematic_parity", [])),
        "reference_violations": _counts(ref_pol),
        "errors": len(pol_errors),
        "new_warnings": [_describe(k) for k in new_pol_warn.elements()],
        "removed": [_describe(k) for k in gone.elements()],
    }
    for v in pol_errors:
        problems.append(f"policy DRC error: {v['type']}: "
                        f"{v['description']} "
                        f"{[i['description'] for i in v['items']]}")
    for k in new_pol_warn.elements():
        problems.append(f"policy DRC warning the original does not have: "
                        f"{_describe(k)}")
    if board_pol.get("unconnected_items"):
        problems.append(f"policy DRC: {len(board_pol['unconnected_items'])} "
                        f"unconnected item(s) with zones filled at the "
                        f"policy clearances — "
                        + "; ".join(" / ".join(i["description"]
                                               for i in u["items"])
                                    for u in board_pol["unconnected_items"][:4]))
    for p in board_pol.get("schematic_parity", []):
        problems.append(f"schematic parity: {p['description']} "
                        f"{[i['description'] for i in p.get('items', [])]}")
    if problems:
        res["status"] = "FAIL"
    return res, problems


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def run_routed_board_check(board_path=BOARD_FILE, reference=REFERENCE,
                           run_kicad=True):
    """Every layer. Returns a report dict; raises RoutedBoardError on a
    finding."""
    for path in (board_path, reference):
        if not os.path.isfile(path):
            raise RoutedBoardError(f"{os.path.relpath(path, _REPO_ROOT)} does "
                                   f"not exist")
    board = _dc.Board(board_path)
    ref = _dc.Board(reference)
    results, failures = {}, []

    for name, fn in ABSOLUTE_CHECKS:
        found = check_generated_from_source(board_path) if fn is None \
            else fn(board)
        results[name] = {"status": "FAIL" if found else "PASS",
                         "problems": found}
        failures += [f"{name}: {p}" for p in found]

    for name, fn in RELATIVE_CHECKS:
        have = collections.Counter(fn(board))
        base = collections.Counter(fn(ref))
        new = list((have - base).elements())
        results[name] = {"status": "FAIL" if new else "PASS",
                         "problems": new,
                         "pre_existing": sum((have & base).values()),
                         "fixed": sum((base - have).values())}
        failures += [f"{name} (not on the original): {p}" for p in new]

    if run_kicad:
        drc, problems = kicad_checks(board_path, reference)
    else:
        drc, problems = {"status": "SKIPPED", "reason": "not requested"}, []
    results["kicad_drc"] = drc
    failures += problems

    from compare_boards import Model
    model = Model(board_path)
    report = {
        "board": os.path.relpath(board_path, _REPO_ROOT),
        "reference": os.path.relpath(reference, _REPO_ROOT),
        "footprints": len(model.parts),
        "tracks": sum(model.tracks.values()),
        "vias": sum(model.vias.values()),
        "zones": sum(model.zones.values()),
        "nets": len(model.nets),
        "checks": results,
    }
    if failures:
        raise RoutedBoardError(f"{len(failures)} finding(s):\n  - "
                               + "\n  - ".join(failures))
    return report


def print_report(report):
    print("=" * 78)
    print("Board of record: stage1_iv.kicad_pcb")
    print("=" * 78)
    print(f"  {report['footprints']} footprints, {report['tracks']} tracks, "
          f"{report['vias']} vias, {report['zones']} zones, "
          f"{report['nets']} nets")
    print(f"  checked against the circuit, the layout source, and the "
          f"original {report['reference']}")
    print()
    for name, res in report["checks"].items():
        if name == "kicad_drc":
            continue
        extra = ""
        if "pre_existing" in res:
            extra = (f"   ({res['pre_existing']} finding(s) also on the "
                     f"original, none new)" if res["pre_existing"] else
                     "   (nothing on either board)")
        print(f"  {res['status']:<8} {name}{extra}")
    drc = report["checks"]["kicad_drc"]
    print()
    if drc["status"] == "SKIPPED":
        print(f"  KiCad DRC: NOT RUN — {drc.get('reason')}.")
        print("  This is not a pass. The board's DRC state is UNKNOWN here; it")
        print("  needs KiCad 10 (python3 validation/routed_board_check.py).")
        return
    ab, pol = drc["as_built"], drc["policy"]
    print(f"  KiCad DRC ({drc['ran_with']}), zones refilled, all track errors:")
    print("    as-built rules (the 0.20 mm the board was routed to + the scoped")
    print("    intra-footprint rules; every check on, clearance included):")
    print(f"      {ab['violations'] or 'no violations'}; "
          f"{ab['unconnected']} unconnected")
    print(f"      original under the same rules: "
          f"{ab['reference_violations'] or 'no violations'}")
    print(f"    repository netclass policy (stage1_iv.kicad_pro):")
    print(f"      {pol['violations'] or 'no violations'}; "
          f"{pol['unconnected']} unconnected; "
          f"{pol['parity']} schematic-parity")
    print(f"      original under the same rules: {pol['reference_violations']}"
          f" (its 0.20 mm routing; layout/policy_clearance.eco re-routes it)")
    print(f"      {pol['errors']} error(s), {len(pol['new_warnings'])} warning(s)"
          f" the original does not have; {len(pol['removed'])} of the "
          f"original's are gone")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--board", default=BOARD_FILE)
    ap.add_argument("--reference", default=REFERENCE)
    ap.add_argument("--no-kicad", action="store_true")
    args = ap.parse_args(argv)
    try:
        report = run_routed_board_check(args.board, args.reference,
                                        run_kicad=not args.no_kicad)
    except RoutedBoardError as exc:
        if args.json:
            print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2))
        else:
            print("=" * 78)
            print("BOARD OF RECORD CHECK FAILED")
            print("=" * 78)
            print(str(exc))
        return 1
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
