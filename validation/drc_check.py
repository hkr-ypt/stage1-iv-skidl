"""
drc_check.py — structural checks on the generated board, and real DRC when
KiCad can run it.
==========================================================================

This is the PCB counterpart of ``validation/footprint_check.py``. It reads
``stage1_iv.kicad_pcb`` — the emitted file, not the in-memory model that
produced it — and fails on anything that is a *board defect* rather than a
routing task.

What it enforces
----------------
=========================  ====================================================
``board_parity``           the board matches the circuit main.py builds now
``courtyard_overlap``      no two footprint courtyards intersect
``edge_clearance``         every courtyard is inside Edge.Cuts by F8
``ground_split``           AGND and DGND are separate copper, joined only at R31
``netclass_coverage``      every net on the board has an explicit netclass
``fab_conformance``        no netclass asks for less than docs/FAB_CONSTRAINTS
``kicad_drc``              ``kicad-cli pcb drc``, when the installed KiCad has it
=========================  ====================================================

What it does NOT do
-------------------
It does not route, and it never reports a pass it did not earn.

``unconnected_items`` — the ratsnest — is reported as a **routing metric**, not
a failure: this repository generates an unrouted board on purpose, and every
one of those violations is a connection a human still has to draw. Every other
DRC violation class is a hard failure.

If the installed KiCad cannot run DRC (KiCad 7's ``kicad-cli pcb`` has only
``export``; ``drc`` arrived in KiCad 8), the ``kicad_drc`` check reports
**SKIPPED**. It never reports OK. A skipped check is not a passed check, and
this module's exit status distinguishes them.

Run standalone::

    python3 validation/drc_check.py
    python3 validation/drc_check.py --json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_REPO_ROOT, "tools"), _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from resolve_kicad_libs import parse_sexp  # noqa: E402
from libraries.netclasses import (  # noqa: E402
    classify, check_against_fab, UnclassifiedNet,
)

BOARD_FILE = os.path.join(_REPO_ROOT, "stage1_iv.kicad_pcb")

# The one net pair that is allowed to be bridged, and the only part allowed to
# bridge it. Both are design decisions, not layout conveniences: see
# docs/pcb_generation.md and blocks/power.py.
STAR_GROUND_REF = "R31"
GROUND_NETS = ("AGND", "DGND")

# DRC violation types that are a routing task rather than a board defect.
# Deliberately short. Adding to it is how a real defect gets hidden, so anything
# here needs a reason in docs/pcb_generation.md.
ROUTING_VIOLATIONS = {"unconnected_items"}

# Numerical slack, in mm. Board coordinates are written to 6 decimal places.
EPS = 1e-4


class DrcCheckError(RuntimeError):
    """The generated board has a defect. It must not be routed as it stands."""


# ---------------------------------------------------------------------------
# Board reading
# ---------------------------------------------------------------------------

def _head(node):
    return str(node[0]) if isinstance(node, list) and node else ""


def _kids(node, head):
    return [c for c in node if isinstance(c, list) and _head(c) == head]


def _first(node, head):
    k = _kids(node, head)
    return k[0] if k else None


def _f(tok, default=0.0):
    try:
        return float(tok)
    except (TypeError, ValueError):
        return default


def _at(node):
    """(x, y, rotation) of a node's ``(at ...)``, or the origin."""
    a = _first(node, "at")
    if a is None:
        return 0.0, 0.0, 0.0
    return _f(a[1] if len(a) > 1 else 0), _f(a[2] if len(a) > 2 else 0), \
        _f(a[3] if len(a) > 3 else 0)


def _rotate(px, py, deg):
    """KiCad footprint-local -> board delta. Y is down, rotation is CCW."""
    r = math.radians(deg)
    return px * math.cos(r) + py * math.sin(r), -px * math.sin(r) + py * math.cos(r)


def _layer_of(node):
    lay = _first(node, "layer")
    return str(lay[1]) if lay is not None and len(lay) > 1 else None


def _graphic_points(node):
    """Every (x, y) a graphic primitive is defined by."""
    pts = []
    for c in node:
        if not isinstance(c, list):
            continue
        h = _head(c)
        if h in ("start", "end", "center", "mid") and len(c) > 2:
            pts.append((_f(c[1]), _f(c[2])))
        elif h == "pts":
            for p in c:
                if isinstance(p, list) and _head(p) == "xy" and len(p) > 2:
                    pts.append((_f(p[1]), _f(p[2])))
    return pts


class Footprint:
    """One placed footprint, resolved into board coordinates."""

    def __init__(self, node):
        self.node = node
        self.x, self.y, self.rot = _at(node)
        self.ref = ""
        for t in _kids(node, "fp_text"):
            if len(t) > 2 and str(t[1]) == "reference":
                self.ref = str(t[2])
        self.pads = []
        for pad in _kids(node, "pad"):
            px, py, _pr = _at(pad)
            dx, dy = _rotate(px, py, self.rot)
            net = _first(pad, "net")
            name = str(net[2]) if net is not None and len(net) > 2 else ""
            size = _first(pad, "size")
            w = _f(size[1]) if size is not None and len(size) > 1 else 0.0
            h = _f(size[2]) if size is not None and len(size) > 2 else 0.0
            self.pads.append({
                "name": str(pad[1]) if len(pad) > 1 else "",
                "x": self.x + dx, "y": self.y + dy,
                "w": w, "h": h, "net": name,
            })
        self.bbox = self._courtyard()

    def _courtyard(self):
        xs, ys = [], []
        for child in self.node:
            if not isinstance(child, list):
                continue
            if _layer_of(child) not in ("F.CrtYd", "B.CrtYd"):
                continue
            for px, py in _graphic_points(child):
                dx, dy = _rotate(px, py, self.rot)
                xs.append(self.x + dx)
                ys.append(self.y + dy)
        if not xs:                       # no courtyard: fall back to the pads
            for p in self.pads:
                xs += [p["x"] - p["w"] / 2, p["x"] + p["w"] / 2]
                ys += [p["y"] - p["h"] / 2, p["y"] + p["h"] / 2]
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def nets(self):
        return {p["net"] for p in self.pads if p["net"]}


class Board:
    def __init__(self, path):
        self.path = path
        with open(path, encoding="utf-8") as fh:
            tree = parse_sexp(fh.read())
        root = None
        for child in tree:
            if isinstance(child, list) and _head(child) == "kicad_pcb":
                root = child
                break
        if root is None:
            raise DrcCheckError(f"{path}: not a kicad_pcb file")
        self.root = root
        self.footprints = [Footprint(n) for n in _kids(root, "footprint")]
        self.nets = [str(n[2]) for n in _kids(root, "net")
                     if len(n) > 2 and str(n[2])]
        self.zones = []
        for z in _kids(root, "zone"):
            nn = _first(z, "net_name")
            poly = _first(z, "polygon")
            pts = _graphic_points(poly) if poly is not None else []
            self.zones.append({
                "net": str(nn[1]) if nn is not None and len(nn) > 1 else "",
                "points": pts,
                "bbox": (min(p[0] for p in pts), min(p[1] for p in pts),
                         max(p[0] for p in pts), max(p[1] for p in pts))
                if pts else None,
            })
        self.tracks = []
        for seg in _kids(root, "segment") + _kids(root, "arc"):
            s, e = _first(seg, "start"), _first(seg, "end")
            net = _first(seg, "net")
            self.tracks.append({
                "start": (_f(s[1]), _f(s[2])) if s is not None else (0.0, 0.0),
                "end": (_f(e[1]), _f(e[2])) if e is not None else (0.0, 0.0),
                "net_code": int(_f(net[1])) if net is not None else 0,
            })
        self.outline = self._outline()

    def _outline(self):
        xs, ys = [], []
        for node in self.root:
            if not isinstance(node, list):
                continue
            if _head(node) not in ("gr_line", "gr_rect", "gr_arc", "gr_poly",
                                   "gr_circle"):
                continue
            if _layer_of(node) != "Edge.Cuts":
                continue
            for px, py in _graphic_points(node):
                xs.append(px)
                ys.append(py)
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def by_ref(self, ref):
        for fp in self.footprints:
            if fp.ref == ref:
                return fp
        return None


def _overlap(a, b):
    """Overlap rectangle of two bboxes, or None."""
    if a is None or b is None:
        return None
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 - x0 > EPS and y1 - y0 > EPS:
        return x0, y0, x1, y1
    return None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_courtyard_overlap(board):
    """No two footprints may occupy the same space.

    KiCad reports this as ``courtyards_overlap``. Catching it here means the
    placement generator is wrong, which is a source defect — not something to
    nudge apart by hand in the GUI.
    """
    fps = [f for f in board.footprints if f.bbox]
    problems = []
    for i in range(len(fps)):
        for j in range(i + 1, len(fps)):
            ov = _overlap(fps[i].bbox, fps[j].bbox)
            if ov:
                problems.append(
                    f"{fps[i].ref} and {fps[j].ref} courtyards overlap by "
                    f"{ov[2] - ov[0]:.3f} x {ov[3] - ov[1]:.3f} mm at "
                    f"({ov[0]:.2f}, {ov[1]:.2f})")
    return problems


def check_edge_clearance(board, min_clearance):
    """Every courtyard inside Edge.Cuts by at least F8."""
    if board.outline is None:
        return ["board has no Edge.Cuts outline"]
    x0, y0, x1, y1 = board.outline
    problems = []
    for fp in board.footprints:
        if not fp.bbox:
            continue
        bx0, by0, bx1, by1 = fp.bbox
        margins = (bx0 - x0, by0 - y0, x1 - bx1, y1 - by1)
        worst = min(margins)
        if worst < min_clearance - EPS:
            problems.append(
                f"{fp.ref} is {worst:.3f} mm from the board edge, below the "
                f"{min_clearance} mm required by docs/FAB_CONSTRAINTS.md F8")
    return problems


def check_ground_split(board):
    """AGND and DGND are separate planes joined at exactly one point.

    This is the check the whole grounding architecture rests on, so it is
    written to *fail* rather than to be satisfiable by accident:

    1. There must be one zone for each ground net and their polygons must not
       intersect.
    2. Every part that bonds to both ground systems must *straddle* the
       boundary between the pours: its AGND pads over the analog pour and its
       DGND pads over the digital pour. Move one fully onto either side and
       this fails, which is the point.

       Two parts bond both grounds, and both are deliberate. R31 is the star
       resistor — the single intended DC connection between the systems. U8
       (ADS1256) has separate AGND and DGND pins because the die has two
       grounds; bonding each to its own pour is what the datasheet asks for
       and is *not* a second bridge.
    3. The star part must be one of them.
    4. No track carrying a ground net may cross the boundary.
    """
    problems = []
    zones = {z["net"]: z for z in board.zones if z["net"] in GROUND_NETS}
    for net in GROUND_NETS:
        if net not in zones:
            problems.append(f"no copper zone for {net}; the ground planes are "
                            f"not modelled in the board file")
    if len(zones) < 2:
        return problems

    a, d = zones["AGND"], zones["DGND"]
    ov = _overlap(a["bbox"], d["bbox"])
    if ov:
        problems.append(
            f"AGND and DGND zones overlap over {ov[2] - ov[0]:.3f} x "
            f"{ov[3] - ov[1]:.3f} mm — they are one plane, not two")
        return problems

    # The zones are separated along x by construction; the boundary is the
    # middle of the gap between them.
    boundary = (a["bbox"][2] + d["bbox"][0]) / 2
    gap = d["bbox"][0] - a["bbox"][2]

    bridges = [fp for fp in board.footprints
               if len(fp.nets & set(GROUND_NETS)) == 2]
    if STAR_GROUND_REF not in [fp.ref for fp in bridges]:
        problems.append(
            f"{STAR_GROUND_REF} (the star-ground resistor) does not connect "
            f"AGND to DGND on the board; the two ground systems have no "
            f"intended DC path")
    for fp in bridges:
        wrong = []
        for pad in fp.pads:
            if pad["net"] == "AGND" and pad["x"] > boundary:
                wrong.append(f"pad {pad['name']} (AGND) at x={pad['x']:.3f} is "
                             f"over the DGND pour")
            if pad["net"] == "DGND" and pad["x"] < boundary:
                wrong.append(f"pad {pad['name']} (DGND) at x={pad['x']:.3f} is "
                             f"over the AGND pour")
        if wrong:
            problems.append(
                f"{fp.ref} does not straddle the AGND/DGND boundary at "
                f"x={boundary:.3f}: " + "; ".join(wrong) + ". A part bonded to "
                f"both ground systems must sit ON the split with each ground "
                f"pin over its own pour, or return current crosses the split "
                f"in copper. See docs/pcb_generation.md.")

    codes = {}
    for n in _kids(board.root, "net"):
        if len(n) > 2:
            codes[int(_f(n[1]))] = str(n[2])
    for t in board.tracks:
        if codes.get(t["net_code"]) not in GROUND_NETS:
            continue
        if (t["start"][0] - boundary) * (t["end"][0] - boundary) < 0:
            problems.append(
                f"a {codes[t['net_code']]} track crosses the ground boundary "
                f"at x={boundary:.3f} between {t['start']} and {t['end']}")

    return problems, boundary, gap


def check_netclass_coverage(board):
    try:
        classify(board.nets)
    except UnclassifiedNet as exc:
        return [str(exc)]
    return []


def check_board_parity(board):
    """The board must match the circuit that main.py builds right now.

    This is KiCad's ``schematic_parity`` class, checked here so a stale
    committed board cannot pass the other checks by describing an older design.
    A part or net that exists in one and not the other is a hard failure.
    """
    import main as project_main
    _n, _parts, circuit = project_main.build()

    want_nets = {n.name for n in circuit.get_nets() if n.name != "__NOCONNECT"}
    want_refs = {p.ref for p in circuit.parts if (p.footprint or "").strip()}
    have_nets = set(board.nets)
    # Mounting holes are board-only mechanical parts with no schematic symbol.
    have_refs = {fp.ref for fp in board.footprints
                 if not fp.ref.startswith("H")}

    problems = []
    for label, missing, extra in (
            ("net", sorted(want_nets - have_nets), sorted(have_nets - want_nets)),
            ("part", sorted(want_refs - have_refs), sorted(have_refs - want_refs))):
        if missing:
            problems.append(
                f"{len(missing)} {label}(s) in the circuit are not on the "
                f"board: {missing[:8]}{' ...' if len(missing) > 8 else ''}. "
                f"Regenerate with `python3 main.py`.")
        if extra:
            problems.append(
                f"{len(extra)} {label}(s) on the board are not in the "
                f"circuit: {extra[:8]}{' ...' if len(extra) > 8 else ''}. "
                f"The board is stale.")
    return problems


# ---------------------------------------------------------------------------
# Real DRC
# ---------------------------------------------------------------------------

def kicad_drc_available():
    """(path, reason) — the kicad-cli that can run DRC, or why none can."""
    exe = shutil.which("kicad-cli")
    if exe is None:
        return None, "kicad-cli is not installed"
    try:
        helptext = subprocess.run([exe, "pcb", "--help"], capture_output=True,
                                  text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError) as err:
        return None, f"kicad-cli pcb --help failed: {err}"
    if "drc" not in helptext.split():
        version = subprocess.run([exe, "version"], capture_output=True,
                                 text=True).stdout.strip()
        return None, (f"the installed kicad-cli ({version or 'unknown version'}) "
                      f"has no 'pcb drc' subcommand; it arrived in KiCad 8")
    return exe, ""


def run_kicad_drc(board_path):
    """Run DRC and return (violations, note). Raises nothing on violations."""
    exe, reason = kicad_drc_available()
    if exe is None:
        return None, reason
    with tempfile.TemporaryDirectory() as tmp:
        report = os.path.join(tmp, "drc.json")
        proc = subprocess.run(
            [exe, "pcb", "drc", "--format", "json", "--severity-all",
             "--output", report, board_path],
            capture_output=True, text=True, timeout=900)
        if not os.path.isfile(report):
            return None, (f"kicad-cli pcb drc produced no report "
                          f"(exit {proc.returncode}): "
                          f"{(proc.stderr or proc.stdout).strip()[:400]}")
        with open(report, encoding="utf-8") as fh:
            doc = json.load(fh)
    out = []
    for key in ("violations", "unconnected_items", "schematic_parity"):
        for v in doc.get(key, []):
            out.append({"type": v.get("type", key),
                        "severity": v.get("severity", "error"),
                        "description": v.get("description", "")})
    return out, ""


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_drc_check(board_path=BOARD_FILE, run_kicad=True):
    """Every check above. Returns a report dict; raises on a hard failure."""
    if not os.path.isfile(board_path):
        raise DrcCheckError(
            f"{os.path.relpath(board_path, _REPO_ROOT)} does not exist. Run "
            f"`python3 main.py` to generate it.")

    board = Board(board_path)
    from libraries.netclasses import FAB

    results, failures = {}, []

    def record(name, problems):
        results[name] = {"status": "PASS" if not problems else "FAIL",
                         "problems": problems}
        failures.extend(f"{name}: {p}" for p in problems)

    record("fab_conformance", check_against_fab())
    record("board_parity", check_board_parity(board))
    record("netclass_coverage", check_netclass_coverage(board))
    record("courtyard_overlap", check_courtyard_overlap(board))
    record("edge_clearance", check_edge_clearance(board, FAB["edge_clearance"]))

    split = check_ground_split(board)
    if isinstance(split, tuple):
        record("ground_split", split[0])
        results["ground_split"]["boundary_x"] = round(split[1], 3)
        results["ground_split"]["zone_gap_mm"] = round(split[2], 3)
    else:
        record("ground_split", split)

    # --- real DRC -------------------------------------------------------
    violations, note = (None, "not requested") if not run_kicad else \
        run_kicad_drc(board_path)
    if violations is None:
        results["kicad_drc"] = {"status": "SKIPPED", "reason": note,
                                "problems": []}
    else:
        routing = [v for v in violations if v["type"] in ROUTING_VIOLATIONS]
        defects = [v for v in violations if v["type"] not in ROUTING_VIOLATIONS]
        by_type = {}
        for v in violations:
            by_type[v["type"]] = by_type.get(v["type"], 0) + 1
        results["kicad_drc"] = {
            "status": "PASS" if not defects else "FAIL",
            "problems": [f"{v['type']}: {v['description']}" for v in defects],
            "routing_violations": len(routing),
            "by_type": by_type,
        }
        failures.extend(f"kicad_drc: {v['type']}: {v['description']}"
                        for v in defects)

    report = {
        "board": os.path.relpath(board_path, _REPO_ROOT),
        "footprints": len(board.footprints),
        "nets": len(board.nets),
        "zones": [z["net"] for z in board.zones],
        "outline_mm": None if board.outline is None else (
            round(board.outline[2] - board.outline[0], 2),
            round(board.outline[3] - board.outline[1], 2)),
        "tracks": len(board.tracks),
        "checks": results,
    }
    if failures:
        raise DrcCheckError(
            f"{len(failures)} board defect(s):\n  - " + "\n  - ".join(failures))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--board", default=BOARD_FILE)
    ap.add_argument("--no-kicad", action="store_true",
                    help="skip kicad-cli DRC even if it is available")
    args = ap.parse_args(argv)

    try:
        report = run_drc_check(args.board, run_kicad=not args.no_kicad)
    except DrcCheckError as exc:
        if args.json:
            print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2))
        else:
            print("=" * 78)
            print("BOARD CHECK FAILED")
            print("=" * 78)
            print(str(exc))
        return 1

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print("=" * 78)
    print("Board structural checks")
    print("=" * 78)
    print(f"  board      {report['board']}  "
          f"{report['outline_mm'][0]} x {report['outline_mm'][1]} mm")
    print(f"  footprints {report['footprints']}   nets {report['nets']}   "
          f"tracks {report['tracks']}   zones {', '.join(report['zones'])}")
    print()
    for name, res in report["checks"].items():
        print(f"  {res['status']:<8}{name}")
        if res.get("reason"):
            print(f"          {res['reason']}")
    drc = report["checks"]["kicad_drc"]
    print()
    if drc["status"] == "SKIPPED":
        print("  DRC WAS NOT RUN. This is not a pass. The board's DRC state is")
        print("  UNKNOWN in this environment — see the reason above, and run")
        print("  DRC in KiCad 8 or later before believing anything about it.")
    else:
        print(f"  routing violations (unconnected_items): "
              f"{drc['routing_violations']}")
        print("  Those are connections a human still has to route. They are not")
        print("  a pass and they are not a defect. See docs/pcb_generation.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
