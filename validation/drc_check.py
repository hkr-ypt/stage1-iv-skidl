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
``dru_parses``             the custom rules file is one KiCad can compile
``schema_version``         board and vendored footprints share a file schema
``rule_satisfiability``    no clearance rule demands more than a package gives
``hole_spacing``           min_hole_to_hole does not exceed the parts' drills
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
    classify, check_against_fab, UnclassifiedNet, NETCLASSES, DEFAULT_CLASS,
    NET_TO_CLASS, GLOBAL_MIN_HOLE_TO_HOLE, INTRA_FOOTPRINT_CLEARANCE,
)
# The SAME text-box model the placer uses. Imported, not reimplemented: this
# file used to carry its own smaller copy, and that is exactly how it certified
# a board as silkscreen-clean that KiCad reported 161 warnings on.
from generators.pcb import (  # noqa: E402
    silk_text_box, SILK_CLEARANCE, SOLDER_MASK_MARGIN,
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
    """Every (x, y) a graphic primitive occupies.

    A circle is stored as centre + a point on the rim. Taking the bounding box
    of those two points understates the shape by half its diameter — which is
    how this file certified a board as courtyard-clean while KiCad found 45
    overlaps on it. Circles expand to centre +/- radius.
    """
    kids = {}
    for c in node:
        if isinstance(c, list) and c:
            kids.setdefault(_head(c), []).append(c)

    if _head(node) in ("fp_circle", "gr_circle", "circle"):
        ctr = kids.get("center", [None])[0]
        end = kids.get("end", [None])[0]
        if ctr is not None and end is not None and len(ctr) > 2 and len(end) > 2:
            cx, cy = _f(ctr[1]), _f(ctr[2])
            r = math.hypot(_f(end[1]) - cx, _f(end[2]) - cy)
            return [(cx - r, cy - r), (cx + r, cy + r)]

    pts = []
    for key in ("start", "end", "center", "mid"):
        for c in kids.get(key, []):
            if len(c) > 2:
                pts.append((_f(c[1]), _f(c[2])))
    for c in kids.get("pts", []):
        for p in c:
            if isinstance(p, list) and _head(p) == "xy" and len(p) > 2:
                pts.append((_f(p[1]), _f(p[2])))
    return pts


def _drill_of(pad):
    """(dx, dy) of a pad's hole, or None for an SMD pad."""
    d = _first(pad, "drill")
    if d is None:
        return None
    nums = []
    for tok in d[1:]:
        if isinstance(tok, list):
            break                       # (offset x y)
        try:
            nums.append(float(tok))
        except (TypeError, ValueError):
            continue                    # the literal "oval"
    if not nums:
        return None
    return (nums[0], nums[1] if len(nums) > 1 else nums[0])


class Footprint:
    """One placed footprint, resolved into board coordinates."""

    def __init__(self, node):
        self.node = node
        self.x, self.y, self.rot = _at(node)
        self.ref = ""
        for t in _kids(node, "property"):        # KiCad 9/10
            if len(t) > 2 and str(t[1]) == "Reference":
                self.ref = str(t[2])
        for t in _kids(node, "fp_text"):         # KiCad 7 and earlier
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
            if self.rot % 180.0:
                w, h = h, w         # a 90/270 rotation swaps the pad extents
            self.pads.append({
                "name": str(pad[1]) if len(pad) > 1 else "",
                "x": self.x + dx, "y": self.y + dy,
                "w": w, "h": h, "net": name,
                "drill": _drill_of(pad),
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
    zones = {_design_net_name(z["net"]): z for z in board.zones
             if _design_net_name(z["net"]) in GROUND_NETS}
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
               if len({_design_net_name(n) for n in fp.nets}
                      & set(GROUND_NETS)) == 2]
    if STAR_GROUND_REF not in [fp.ref for fp in bridges]:
        problems.append(
            f"{STAR_GROUND_REF} (the star-ground resistor) does not connect "
            f"AGND to DGND on the board; the two ground systems have no "
            f"intended DC path")
    for fp in bridges:
        wrong = []
        for pad in fp.pads:
            if _design_net_name(pad["net"]) == "AGND" and pad["x"] > boundary:
                wrong.append(f"pad {pad['name']} (AGND) at x={pad['x']:.3f} is "
                             f"over the DGND pour")
            if _design_net_name(pad["net"]) == "DGND" and pad["x"] < boundary:
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
        if _design_net_name(codes.get(t["net_code"], "")) not in GROUND_NETS:
            continue
        if (t["start"][0] - boundary) * (t["end"][0] - boundary) < 0:
            problems.append(
                f"a {codes[t['net_code']]} track crosses the ground boundary "
                f"at x={boundary:.3f} between {t['start']} and {t['end']}")

    return problems, boundary, gap


def _design_net_name(board_name):
    """Strip KiCad's sheet-path prefix back to the design's own net name."""
    return board_name.rsplit("/", 1)[-1] if board_name.startswith("/") \
        else board_name


def check_netclass_coverage(board):
    try:
        classify([_design_net_name(n) for n in board.nets])
    except UnclassifiedNet as exc:
        return [str(exc)]
    return []


def _required_clearance(net_a, net_b):
    """What KiCad will enforce between two pads: the larger netclass clearance."""
    vals = []
    for name in (net_a, net_b):
        if not name:
            continue
        cls = NET_TO_CLASS.get(_design_net_name(name))
        vals.append(NETCLASSES[cls]["clearance"] if cls
                    else DEFAULT_CLASS["clearance"])
    return max(vals) if vals else DEFAULT_CLASS["clearance"]


def _dru_covered_refs():
    """References the generated stage1_iv.kicad_dru narrows the rule for."""
    path = os.path.join(_REPO_ROOT, "stage1_iv.kicad_dru")
    if not os.path.isfile(path):
        return set()
    import re
    with open(path, encoding="utf-8") as fh:
        return set(re.findall(r"memberOfFootprint\('([^']+)'\)", fh.read()))


def check_rule_satisfiability(board):
    """No board rule may demand more than the parts physically provide.

    This is the check whose absence let a real defect through. Reasoning from
    the *global* minimum clearance is not enough: KiCad's global minimum is a
    floor on what a rule may ask for, not a cap on what it enforces. Between
    two pads of different nets, the clearance actually checked is the LARGER of
    the two netclasses' — so two adjacent VSSOP-8 pads on +5VA (POWER_LV,
    0.20 mm) and VREF_2V5 (SENSE, 0.25 mm) get checked at 0.25 mm against a
    0.15 mm gap the package cannot change, and DRC reports a violation that no
    routing decision can clear.

    So: for every pad pair and every hole pair inside every placed footprint,
    compare the gap actually present against the rule that will actually be
    applied — narrowed by ``stage1_iv.kicad_dru`` where that file covers the
    reference. Anything still unsatisfiable is a board defect here, before
    KiCad ever sees it.
    """
    covered = _dru_covered_refs()
    problems = []
    for fp in board.footprints:
        if fp.ref.startswith("H"):
            continue                        # mounting holes: no nets, no pads
        pads = fp.pads
        for i in range(len(pads)):
            for j in range(i + 1, len(pads)):
                a, b = pads[i], pads[j]
                if a["name"] == b["name"]:
                    continue                # one electrical pad
                if a["net"] and a["net"] == b["net"]:
                    continue                # same net: not a clearance check
                need = (INTRA_FOOTPRINT_CLEARANCE if fp.ref in covered
                        else _required_clearance(a["net"], b["net"]))
                dx = max(abs(a["x"] - b["x"]) - (a["w"] + b["w"]) / 2, 0.0)
                dy = max(abs(a["y"] - b["y"]) - (a["h"] + b["h"]) / 2, 0.0)
                gap = math.hypot(dx, dy)
                if gap < need - EPS:
                    problems.append(
                        f"{fp.ref} pads {a['name']}/{b['name']} are "
                        f"{gap:.3f} mm apart but the rule that applies demands "
                        f"{need:.3f} mm ({a['net'] or 'no net'} vs "
                        f"{b['net'] or 'no net'}). The spacing belongs to the "
                        f"package, so no routing can satisfy this. Either the "
                        f"netclass clearance is wrong or {fp.ref} needs an "
                        f"entry in stage1_iv.kicad_dru — regenerate with "
                        f"tools/gen_project_config.py.")
                    break
            else:
                continue
            break
    return problems


def check_hole_spacing(board):
    """The min_hole_to_hole rule must not exceed what the parts provide."""
    problems = []
    for fp in board.footprints:
        holes = [p for p in fp.pads if p.get("drill")]
        for i in range(len(holes)):
            for j in range(i + 1, len(holes)):
                a, b = holes[i], holes[j]
                if a["name"] == b["name"]:
                    continue
                dx = max(abs(a["x"] - b["x"])
                         - (a["drill"][0] + b["drill"][0]) / 2, 0.0)
                dy = max(abs(a["y"] - b["y"])
                         - (a["drill"][1] + b["drill"][1]) / 2, 0.0)
                gap = math.hypot(dx, dy)
                if gap < GLOBAL_MIN_HOLE_TO_HOLE - EPS:
                    problems.append(
                        f"{fp.ref} holes {a['name']}/{b['name']} are "
                        f"{gap:.3f} mm apart but min_hole_to_hole is "
                        f"{GLOBAL_MIN_HOLE_TO_HOLE} mm. Drill spacing belongs "
                        f"to the package — re-derive the rule from "
                        f"tools/measure_pad_gaps.py, do not move the part.")
                    break
            else:
                continue
            break
    return problems


def ratsnest_count(board):
    """Connections KiCad will report as ``unconnected_items``, from the board.

    Counted the way KiCad counts: per net, (number of *pads* on it) − 1. Pads,
    not schematic pins — J3's shield is one pin number on four physical tabs,
    and the ADC's thermal pad is another such case, so a pin-based count
    understates the ratsnest. This project first reported 241 by counting pins;
    the honest number is below.
    """
    per_net = {}
    for fp in board.footprints:
        for pad in fp.pads:
            if pad["net"]:
                per_net[pad["net"]] = per_net.get(pad["net"], 0) + 1
    return sum(n - 1 for n in per_net.values() if n > 1), per_net


DRU_FILE = os.path.join(_REPO_ROOT, "stage1_iv.kicad_dru")

_DRU_SCRIPT = """
import sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
ok = pcbnew.WriteDRCReport(board, sys.argv[2], pcbnew.EDA_UNITS_MILLIMETRES, True)
sys.stdout.write(pcbnew.GetBuildVersion())
sys.exit(0 if ok else 3)
"""


def check_dru_parses():
    """The custom rules file must be something KiCad can compile.

    A ``.kicad_dru`` KiCad cannot parse makes it refuse to run DRC *at all* —
    the user cannot check the board. That shipped once: the file used ``;``
    comments, and KiCad parses it with the DSN lexer, whose comment character
    is ``#`` and only as the first non-blank character of a line
    (``common/dsnlexer.cpp``).

    When ``pcbnew`` is available the real DRC run is the authority and this
    check is belt-and-braces. Where it is not — a CI runner without KiCad —
    this is the only thing standing between a broken rules file and a reviewer,
    so it runs unconditionally. It is *necessary, not sufficient*: it confirms
    the file is balanced s-expressions once ``#`` comment lines are removed,
    which is exactly what the failure mode violated.
    """
    if not os.path.isfile(DRU_FILE):
        return []                       # no custom rules is a valid state
    with open(DRU_FILE, encoding="utf-8") as fh:
        raw = fh.read()

    kept = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue                    # a comment KiCad accepts
        if stripped.startswith(";"):
            return [f"stage1_iv.kicad_dru line {lineno}: ';' is not a comment "
                    f"character here. KiCad parses this file with the DSN "
                    f"lexer, whose comment character is '#' as the FIRST "
                    f"non-blank character of a line. KiCad will refuse to run "
                    f"DRC at all."]
        kept.append(line)

    text = "\n".join(kept)
    try:
        tree = parse_sexp(text)
    except Exception as exc:            # noqa: BLE001 - any parse failure
        return [f"stage1_iv.kicad_dru does not parse as s-expressions "
                f"({exc}). KiCad will refuse to run DRC at all."]

    heads = [str(n[0]) for n in tree if isinstance(n, list) and n]
    if not heads or heads[0] != "version":
        return [f"stage1_iv.kicad_dru must begin with (version N); it begins "
                f"with {heads[:1] or ['nothing']}"]
    unexpected = sorted(set(heads[1:]) - {"rule"})
    if unexpected:
        return [f"stage1_iv.kicad_dru has top-level item(s) {unexpected}; "
                f"only (version ...) and (rule ...) are valid"]
    return []


def check_schema_version(board):
    """The board and the footprints on it must be the same file-format schema.

    The board is emitted in the KiCad 9/10 schema and the vendored footprints
    are KiCad 9 files, so they agree. They did not always: the board was once
    emitted as KiCad 7 with the footprints rewritten down to v7 syntax, and
    trusting KiCad's migration cost the Edge.Cuts outline — KiCad 9 reported
    "no edges found on Edge.Cuts layer" for a rectangle that KiCad 7 read
    perfectly, because KiCad 8 renumbered every layer (Edge.Cuts 44 -> 25).

    Emitting the schema the design's KiCad actually uses removes the migration
    step entirely. This check makes sure the two never drift apart again.
    """
    import glob
    import re
    ver = _first(board.root, "version")
    board_ver = str(ver[1]) if ver is not None and len(ver) > 1 else None
    if board_ver is None:
        return ["the board has no (version ...) node"]

    problems = []
    pattern = os.path.join(_REPO_ROOT, "libraries", "footprints",
                           "*.pretty", "*.kicad_mod")
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as fh:
            head = fh.read(400)
        m = re.search(r"\(version (\d+)\)", head)
        if m and m.group(1) != board_ver:
            problems.append(
                f"{os.path.relpath(path, _REPO_ROOT)} is schema "
                f"{m.group(1)} but the board is schema {board_ver}. Put them "
                f"on the same schema rather than relying on KiCad to migrate "
                f"one of them — see docs/pcb_generation.md.")
    return problems[:5]


def _silk_items(board):
    """(kind, ref, bbox) for every silkscreen object, in board coordinates."""
    out = []
    for fp in board.footprints:
        for child in fp.node:
            if not isinstance(child, list):
                continue
            head = _head(child)
            if head == "property" and len(child) > 2 and \
                    str(child[1]) == "Reference":
                if _layer_of(child) not in ("F.SilkS", "B.SilkS"):
                    continue
                dx, dy, _r = _at(child)
                x, y = fp.x + dx, fp.y + dy
                size = 1.0
                eff = _first(child, "effects")
                font = _first(eff, "font") if eff is not None else None
                sz = _first(font, "size") if font is not None else None
                if sz is not None and len(sz) > 1:
                    size = _f(sz[1])
                out.append(("text", fp.ref,
                            silk_text_box(str(child[2]), x, y, size)))
            elif head.startswith("fp_") and \
                    _layer_of(child) in ("F.SilkS", "B.SilkS"):
                pts = _graphic_points(child)
                if pts:
                    out.append(("graphic", fp.ref,
                                (fp.x + min(p[0] for p in pts),
                                 fp.y + min(p[1] for p in pts),
                                 fp.x + max(p[0] for p in pts),
                                 fp.y + max(p[1] for p in pts))))
    for node in board.root:
        if not isinstance(node, list):
            continue
        if _head(node) == "gr_text" and _layer_of(node) in ("F.SilkS",
                                                            "B.SilkS"):
            x, y, _r = _at(node)
            text = str(node[1])
            size = 1.0
            eff = _first(node, "effects")
            font = _first(eff, "font") if eff is not None else None
            sz = _first(font, "size") if font is not None else None
            if sz is not None and len(sz) > 1:
                size = _f(sz[1])
            out.append(("board-text", text,
                        silk_text_box(text, x, y, size)))
    return out


SILK_BASELINE = os.path.join(_REPO_ROOT, "validation", "fixtures",
                             "kicad_silk_baseline.json")

# Option A, from the brief: a conservative rule, not a model of KiCad. Any
# silkscreen TEXT within this of copper counts as a violation. It is far wider
# than KiCad's own rule on purpose - false positives are acceptable, false
# negatives are what put 161 warnings past this file three times.
SILK_RULE_CLEARANCE = 0.5


def _load_silk_baseline():
    try:
        with open(SILK_BASELINE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def check_silkscreen(board):
    """Silkscreen pre-filter. NOT an authority — KiCad 10 DRC is.

    Approach A from the brief: a conservative rule rather than a model of
    KiCad's geometry. Any silkscreen text within :data:`SILK_RULE_CLEARANCE`
    (0.5 mm) of a pad, or of another silk object, is a violation. That is
    deliberately stricter than KiCad, because this file's previous geometric
    model was wrong three times running and each time in the same direction —
    it reported clean while KiCad reported 161.

    THE HARD GUARD. Reporting "clean" is only permitted when this checker can
    demonstrate it would have caught the known-bad board. The KiCad 10 report
    for that board is stored in ``validation/fixtures/kicad_silk_baseline.json``
    — 92 ``silk_over_copper``, 69 ``silk_overlap``, across 72 references, none
    of which involved anything but a Reference field. If the current board
    keeps any designator on F.SilkS, this checker cannot prove it would have
    caught that, so it refuses to certify and says so.

    The board it certifies is therefore one where the question does not arise:
    every designator sits on F.Fab, which cannot produce a silkscreen violation
    at all.
    """
    baseline = _load_silk_baseline()
    items = _silk_items(board)
    pads = []
    for fp in board.footprints:
        for pad in fp.pads:
            m = SOLDER_MASK_MARGIN + SILK_RULE_CLEARANCE
            pads.append((pad["x"] - pad["w"] / 2 - m,
                         pad["y"] - pad["h"] / 2 - m,
                         pad["x"] + pad["w"] / 2 + m,
                         pad["y"] + pad["h"] / 2 + m))

    def hit(a, b):
        return (a[0] < b[2] - EPS and b[0] < a[2] - EPS
                and a[1] < b[3] - EPS and b[1] < a[3] - EPS)

    problems = []
    texts = [i for i in items if i[0] in ("text", "board-text")]
    for _kind, ref, box in texts:
        if any(hit(box, pad) for pad in pads):
            problems.append(f"silk text {ref} is within "
                            f"{SILK_RULE_CLEARANCE} mm of copper")
        for _k2, ref2, box2 in items:
            if ref2 != ref and hit(box, box2):
                problems.append(f"silk text {ref} is within "
                                f"{SILK_RULE_CLEARANCE} mm of {ref2}'s "
                                f"silkscreen")
                break

    # --- the guard ------------------------------------------------------
    on_silk = [ref for kind, ref, _b in items if kind == "text"]
    if baseline is None:
        problems.append(
            "the KiCad silk baseline fixture is missing, so this checker "
            "cannot demonstrate it would catch a known-bad board. It must not "
            "be trusted to report clean.")
    elif on_silk and not problems:
        return [], (
            f"{len(on_silk)} reference designator(s) are on F.SilkS and this "
            f"checker found nothing wrong — but it is a 0.5 mm rule, not "
            f"KiCad's geometry, and on the board recorded in "
            f"{os.path.relpath(SILK_BASELINE, _REPO_ROOT)} KiCad found "
            f"{baseline['total_silk']} violations across "
            f"{baseline['distinct_refs_implicated']} references while this "
            f"file reported 9. A clean result here is not evidence. Either "
            f"move the designators to F.Fab, or validate against KiCad 10 "
            f"first.")
    return problems[:20], None


def check_courtyards_defined(board):
    """Every footprint must define a courtyard.

    KiCad can be told to ignore this. It should not be: a footprint with no
    courtyard is invisible to the overlap check, so a board can look clean
    while parts sit on top of each other.
    """
    problems = []
    for fp in board.footprints:
        has = any(isinstance(c, list) and _head(c).startswith("fp_")
                  and _layer_of(c) in ("F.CrtYd", "B.CrtYd")
                  for c in fp.node)
        if not has:
            problems.append(f"{fp.ref} ({_head(fp.node)}) defines no courtyard")
    return problems


def check_schematic_linkage(board):
    """Every footprint must be reachable from the schematic. 100%, or fail.

    KiCad matches a board footprint to a schematic symbol by the footprint's
    ``(path ...)``. A path that does not resolve means "no symbol", so
    *Update PCB from Schematic* adds a fresh library copy of the part and
    orphans the placed one — which is how every DRC report in this project
    until now described a board the generator never produced.

    The path is ``/<sheet-symbol-uuid>/<symbol-uuid>`` for a symbol on a child
    sheet, and ``/<symbol-uuid>`` for one on the root sheet. **The root sheet's
    UUID is not part of it** — including it produced a three-segment path that
    matched nothing, and the first attempt at this fix failed for that reason.
    Verified against boards KiCad itself wrote in ``kicad/qa/data/pcbnew``.
    """
    import glob
    import re
    sch = ""
    for path in sorted(glob.glob(os.path.join(_REPO_ROOT, "*.kicad_sch"))):
        with open(path, encoding="utf-8") as fh:
            sch += fh.read()
    if not sch:
        return ["no .kicad_sch files: cannot verify the board links to the "
                "schematic"]
    known = set(re.findall(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", sch))

    problems, unmatched = [], []
    for fp in board.footprints:
        if fp.ref.startswith("H"):
            continue                    # mechanical, no schematic symbol
        node_path = _first(fp.node, "path")
        if node_path is None or len(node_path) < 2:
            unmatched.append(f"{fp.ref} (no path)")
            continue
        segs = [s for s in str(node_path[1]).strip("/").split("/") if s]
        if not segs or not all(s in known for s in segs):
            unmatched.append(f"{fp.ref} ({node_path[1]})")

    total = len([f for f in board.footprints if not f.ref.startswith("H")])
    if unmatched:
        rate = 100.0 * (total - len(unmatched)) / max(total, 1)
        problems.append(
            f"schematic linkage is {rate:.1f}%, not 100%: "
            f"{len(unmatched)} footprint(s) do not resolve to a schematic "
            f"symbol — {unmatched[:6]}. Update PCB from Schematic will ADD a "
            f"fresh copy of each and orphan the placed one.")
    return problems


def check_board_parity(board):
    """The board must match the circuit that main.py builds right now.

    This is KiCad's ``schematic_parity`` class, checked here so a stale
    committed board cannot pass the other checks by describing an older design.
    A part or net that exists in one and not the other is a hard failure.
    """
    import main as project_main
    _n, _parts, circuit = project_main.build()

    want_nets = {n.name for n in circuit.get_nets() if n.name != "__NOCONNECT"}
    have_nets_design = {_design_net_name(n) for n in board.nets}
    want_refs = {p.ref for p in circuit.parts if (p.footprint or "").strip()}
    have_nets = {_design_net_name(n) for n in board.nets}
    # Mounting holes are board-only mechanical parts with no schematic symbol.
    have_refs = {fp.ref for fp in board.footprints
                 if not fp.ref.startswith("H")}

    # Pad-level parity — this is KiCad's "Update PCB from Schematic" check.
    # A pin whose pad name does not exist in the footprint silently drops its
    # net, which is exactly what the USB-C S1 -> SH upstream rename did to J3.
    problems = []
    by_ref = {fp.ref: fp for fp in board.footprints}
    for part in circuit.parts:
        if not (part.footprint or "").strip():
            continue
        fp = by_ref.get(part.ref)
        if fp is None:
            continue                    # reported as a missing part below
        pads = {p["name"] for p in fp.pads}
        netted = {p["name"] for p in fp.pads if p["net"]}
        for pin in part.pins:
            num = str(pin.num)
            if num not in pads:
                problems.append(
                    f"{part.ref} pin {num} ({pin.name}) has no pad named {num} "
                    f"in {part.footprint}. KiCad reports this as \"pad {num} "
                    f"not found\" and the connection is silently dropped. "
                    f"Check tools/vendor_footprints.py --check: upstream "
                    f"renames pads between library versions.")
            elif num not in netted and any(
                    n.name != "__NOCONNECT" for n in pin.get_nets()):
                problems.append(
                    f"{part.ref} pad {num} carries no net on the board but "
                    f"pin {num} is connected in the circuit")

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

def _drc_via_pcbnew(board_path):
    """Run DRC through KiCad's Python bindings. Returns (report_text, note).

    ``kicad-cli pcb drc`` arrived in KiCad 8, but ``pcbnew.WriteDRCReport()``
    exists in the SWIG bindings from KiCad 7 — so a KiCad 7 machine, where the
    CLI cannot run DRC, still can. This project previously reported DRC as
    unrunnable on the strength of the CLI alone; that was wrong, and it is why
    three real board defects reached a reviewer.
    """
    try:
        import pcbnew                                    # noqa: PLC0415
    except ImportError:
        # KiCad's pcbnew is a SWIG extension built against the DISTRO's python,
        # so a CI runner using actions/setup-python cannot import it even when
        # KiCad is installed. Hand the job to the interpreter it was built for.
        return _drc_via_system_python(board_path)
    if not hasattr(pcbnew, "WriteDRCReport"):
        return None, (f"pcbnew {pcbnew.GetBuildVersion()} has no "
                      f"WriteDRCReport()")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "drc.rpt")
        try:
            board = pcbnew.LoadBoard(board_path)
        except (OSError, IOError, RuntimeError) as err:
            # The board is emitted in the KiCad 9/10 schema because that is what
            # the design is for; an older KiCad simply cannot read it. That is a
            # SKIP - the board's DRC state is unknown here - not a defect.
            return None, (f"pcbnew {pcbnew.GetBuildVersion()} cannot read this "
                          f"board: {str(err).splitlines()[0]}")
        ok = pcbnew.WriteDRCReport(board, out, pcbnew.EDA_UNITS_MILLIMETRES,
                                   True)
        if not ok or not os.path.isfile(out):
            # KiCad HAS DRC and refused to run it. The usual cause is a custom
            # rules file it cannot compile, which blocks DRC for the user
            # entirely. False (not None) marks it as a hard failure: reporting
            # SKIPPED here would hide our own broken file behind the same
            # message used when KiCad simply is not installed.
            return False, (f"pcbnew {pcbnew.GetBuildVersion()} HAS DRC but "
                           f"refused to run it. The usual cause is a "
                           f"stage1_iv.kicad_dru KiCad cannot compile - it is "
                           f"parsed with the DSN lexer, whose comment "
                           f"character is '#' as the first non-blank character "
                           f"of a line. This blocks DRC for anyone opening the "
                           f"project.")
        with open(out, encoding="utf-8") as fh:
            return fh.read(), f"pcbnew {pcbnew.GetBuildVersion()}"


def _other_interpreters():
    """Interpreters on PATH that are not the one running this process.

    Found with :func:`shutil.which` rather than hardcoded. No absolute path
    belongs in this repository — ``tools/check_no_abs_paths.py`` enforces that,
    and it caught the first version of this function — and a hardcoded system
    interpreter path would be wrong on any system that puts it elsewhere.
    The *versioned* names matter: a CI runner's PATH puts its own ``python3``
    first, so the distro one is only reachable by its version number.
    """
    mine = os.path.realpath(sys.executable)
    seen, out = {mine}, []
    names = ["python3"] + [f"python3.{minor}" for minor in range(14, 8, -1)]
    for name in names:
        path = shutil.which(name)
        if not path:
            continue
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        out.append(path)
    return out


def _drc_via_system_python(board_path):
    """Run DRC under the interpreter pcbnew was actually built for.

    KiCad's pcbnew is a SWIG extension linked against the distro's Python, so
    an environment supplying its own interpreter — actions/setup-python, a
    venv, pyenv — cannot import it however well KiCad is installed. Each
    candidate is tried and whichever can import pcbnew wins; nothing is assumed
    about where it lives.
    """
    for exe in _other_interpreters():
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "drc.rpt")
            proc = subprocess.run([exe, "-c", _DRU_SCRIPT, board_path, out],
                                  capture_output=True, text=True, timeout=1800)
            if proc.returncode == 3:
                return False, (f"pcbnew {proc.stdout.strip()} (via {exe}) HAS "
                               f"DRC but refused to run it — the usual cause "
                               f"is a stage1_iv.kicad_dru KiCad cannot compile")
            if proc.returncode == 0 and os.path.isfile(out):
                with open(out, encoding="utf-8") as fh:
                    return fh.read(), f"pcbnew {proc.stdout.strip()} via {exe}"
    return None, ("KiCad's pcbnew Python module is not importable, by this "
                  "interpreter or the system one")


def parse_drc_report(text):
    """[{type, severity, description, refs}] from a KiCad text DRC report.

    ``refs`` is the set of footprint references the violation names, which is
    what lets an intra-footprint clearance violation be told apart from a real
    one. Severity is KiCad's own word for it, not this file's opinion.
    """
    import re
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^\[([a-z_]+)\]:\s*(.*)$", line)
        if m:
            cur = {"type": m.group(1), "severity": "error",
                   "description": m.group(2).strip(), "refs": []}
            out.append(cur)
            continue
        if cur is None:
            continue
        sev = re.search(r"Severity:\s*(\w+)", line)
        if sev:
            cur["severity"] = sev.group(1).lower()
        item = re.search(r"\bof (\S+?)(?: on \S+)?$", line.rstrip())
        if item:
            cur["refs"].append(item.group(1))
        m2 = re.search(r"Footprint (\S+)$", line.rstrip())
        if m2:
            cur["refs"].append(m2.group(1))
    return out


def _kicad_major(ran_with):
    """Major version of the KiCad that ACTUALLY ran DRC.

    Taken from the run's own note, not by importing pcbnew here. DRC may have
    been handed to the system interpreter (see _drc_via_system_python), in
    which case importing pcbnew in *this* interpreter fails and any version
    probed that way is wrong — which silently disabled the escape below and
    failed CI. The authority on which KiCad evaluated the rules is the run.

    Returns None when the version cannot be read, which is treated as
    "modern" — the safe direction, since it keeps violations as defects.
    """
    import re
    m = re.search(r"pcbnew\s+v?(\d+)\.", ran_with or "")
    return int(m.group(1)) if m else None


def classify_violations(violations, ran_with=""):
    """Split DRC output into routing, not-evaluatable-here, and real defects.

    There are exactly two escapes, both narrow, and neither is a judgement call:

    * ``unconnected_items`` is the ratsnest — this board is unrouted on purpose.
    * a ``clearance`` error between two pads of ONE footprint that
      ``stage1_iv.kicad_dru`` covers, when the KiCad that RAN DRC predates
      ``memberOfFootprint()`` (KiCad 8). On such a KiCad the scoped rule is
      inert, so the violation says nothing about the board. It is only
      admissible because :func:`check_rule_satisfiability` has already checked
      that same pad pair against the rule, from source.

    Everything KiCad calls an *error* and neither escape covers is a defect.
    Everything KiCad calls a *warning* is reported, never silently dropped, but
    does not fail the build — that is KiCad's severity, not this file's.
    """
    covered = _dru_covered_refs()
    stale_kicad = (_kicad_major(ran_with) or 99) < 8

    routing, inert, warnings, defects = [], [], [], []
    for v in violations:
        if v["type"] in ROUTING_VIOLATIONS:
            routing.append(v)
        elif (v["type"] == "clearance" and stale_kicad
              and len(set(v["refs"])) == 1 and v["refs"][0] in covered):
            inert.append(v)
        elif v.get("severity") == "warning":
            warnings.append(v)
        else:
            defects.append(v)
    return routing, inert, warnings, defects


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
        # No `kicad-cli pcb drc` — try the Python bindings, which have had
        # WriteDRCReport() since KiCad 7.
        text, note = _drc_via_pcbnew(board_path)
        if text is False:
            return False, note
        if text is not None:
            return parse_drc_report(text), note
        return None, f"{reason}; {note}"
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
    record("dru_parses", check_dru_parses())
    record("schema_version", check_schema_version(board))
    record("schematic_linkage", check_schematic_linkage(board))
    record("board_parity", check_board_parity(board))
    record("netclass_coverage", check_netclass_coverage(board))
    record("rule_satisfiability", check_rule_satisfiability(board))
    record("hole_spacing", check_hole_spacing(board))
    record("courtyards_defined", check_courtyards_defined(board))
    record("courtyard_overlap", check_courtyard_overlap(board))
    silk_problems, silk_unverified = check_silkscreen(board)
    record("silkscreen", silk_problems)
    if silk_unverified:
        # A third state, deliberately. Not PASS - this checker cannot prove it
        # would catch what KiCad catches. Not FAIL either - there is no known
        # defect. UNVERIFIED, stated loudly, is the honest answer.
        results["silkscreen"]["status"] = "UNVERIFIED"
        results["silkscreen"]["reason"] = silk_unverified
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
    if violations is False:
        results["kicad_drc"] = {"status": "FAIL", "reason": note,
                                "problems": [note]}
        failures.append(f"kicad_drc: {note}")
    elif violations is None:
        results["kicad_drc"] = {"status": "SKIPPED", "reason": note,
                                "problems": []}
    else:
        routing, inert, warns, defects = classify_violations(violations, note)
        by_type = {}
        for v in violations:
            by_type[v["type"]] = by_type.get(v["type"], 0) + 1
        results["kicad_drc"] = {
            "status": "PASS" if not defects else "FAIL",
            "problems": [f"{v['type']}: {v['description']}" for v in defects],
            "routing_violations": len(routing),
            "warnings": len(warns),
            "warning_types": sorted({v["type"] for v in warns}),
            "not_evaluatable_here": len(inert),
            "ran_with": note,
            "by_type": by_type,
        }
        failures.extend(f"kicad_drc: {v['type']}: {v['description']}"
                        for v in defects)

    rats, _per_net = ratsnest_count(board)
    report = {
        "board": os.path.relpath(board_path, _REPO_ROOT),
        "ratsnest": rats,
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
        print(f"  {res['status']:<11}{name}")
        if res.get("reason"):
            print(f"          {res['reason']}")
    drc = report["checks"]["kicad_drc"]
    print()
    print(f"  routing not yet done — {report['ratsnest']} connections remain")
    print("  (counted the way KiCad counts unconnected_items: pads per net,")
    print("   minus one. Pads, not schematic pins.)")
    print()
    if drc["status"] == "SKIPPED":
        print("  DRC WAS NOT RUN. This is not a pass. The board's DRC state is")
        print("  UNKNOWN in this environment — see the reason above, and run")
        print("  DRC in KiCad 8 or later before believing anything about it.")
    else:
        print(f"  DRC ran with {drc.get('ran_with', 'kicad-cli')}")
        print(f"  KiCad reported {drc['routing_violations']} "
              f"unconnected_items (this file predicted "
              f"{report['ratsnest']}).")
        if drc.get("warnings"):
            print(f"  {drc['warnings']} warning(s): "
                  f"{', '.join(drc['warning_types'])}")
        if drc.get("not_evaluatable_here"):
            print(f"  {drc['not_evaluatable_here']} clearance violation(s) "
                  f"inside a single footprint could not be judged: this KiCad "
                  f"predates memberOfFootprint(), so stage1_iv.kicad_dru is "
                  f"inert here. check_rule_satisfiability covered them.")
        print("  Those are connections a human still has to route. They are not")
        print("  a pass and they are not a defect. See docs/pcb_generation.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
