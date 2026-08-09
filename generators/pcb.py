"""
pcb.py — PCB emitter for the Stage 1 I–V system.
================================================

Reads the LIVE SKiDL ``Circuit`` — the same one that produced ``stage1_iv.net``
and the schematic — and writes ``stage1_iv.kicad_pcb``: board outline, board
setup, netclass-aware design rules, placed footprints with their nets, mounting
holes, and the two ground zones.

WHAT THIS DOES AND DOES NOT DO
------------------------------
It makes the board **checkable and correct-by-construction**: after running it,
the only DRC violations that should remain are ``unconnected_items`` — the
ratsnest — and every one of those is a real routing task for a human.

It does **not** route. No autorouting is attempted, and nothing here should be
read as a claim that DRC passes. See ``docs/pcb_generation.md`` for the routing
plan and the honest violation count.

BOARD FORMAT
------------
The board is emitted in the **KiCad 9/10 board schema** (``version 20241229``),
which is what the vendored footprints already are — so nothing is converted and
nothing is migrated.

It was previously emitted as KiCad 7, with each footprint rewritten down to v7
syntax, on the reasoning that KiCad 7 is the newest version installable here and
that KiCad 8/9 migrate v7 boards on load. **They do not migrate it faithfully.**
KiCad 8 renumbered every layer — ``B.Cu`` moved from 31 to 2, ``Edge.Cuts`` from
44 to 25 — and a reviewer's KiCad 9 reported "Board has malformed outline (no
edges found on Edge.Cuts layer)" for a closed rectangle that KiCad 7 read
without complaint.

Emitting the schema the design's KiCad actually uses removes the migration step
entirely, and lets the vendored footprints go in verbatim. The cost is that the
KiCad installed in this build environment (7.x) can no longer read the board, so
``kicad_drc`` and the board half of ``tools/check_kicad_loads.py`` report
**SKIPPED** there — never a pass. ``validation/drc_check.py::check_schema_version``
fails the build if the board and the footprints ever drift apart again.

PLACEMENT
---------
Footprints are grouped by the ``skidl_block`` attribute ``main.py`` stamps on
every part, and each block gets its own rectangular region. Within a region,
parts are packed by courtyard size, largest first, so nothing overlaps. Analog
blocks are placed on one side of the board and digital on the other, matching
the AGND/DGND zone split, with R31 — the star-ground resistor — straddling the
boundary.

The placement is a **sane starting point for manual routing**, not an optimised
layout. What it guarantees: no overlapping courtyards, block coherence, and
decoupling capacitors adjacent to the part they decouple.
"""

from __future__ import annotations

import math
import os
import sys
import uuid as _uuid

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "tools"))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from resolve_kicad_libs import (  # noqa: E402
    KicadLibraries, LibraryNotFound, parse_sexp, write_sexp, SexpList, Atom,
)
from libraries.netclasses import (  # noqa: E402
    NETCLASSES, DEFAULT_CLASS, GLOBAL_MIN_CLEARANCE, FAB,
    classify, check_against_fab,
)

BOARD_VERSION = "20241229"          # KiCad 9/10 .kicad_pcb schema
GENERATOR = "stage1_iv_skidl"
UUID_NS = _uuid.uuid5(_uuid.NAMESPACE_DNS, "stage1-iv-skidl.pcb")

# Which side of the ground split each block belongs to. This is the layout
# expression of the design's grounding architecture: AGND and DGND are separate
# planes that meet only at R31 (blocks/power.py, and enforced in the netlist by
# validation/assertions.py::a_star_ground).
ANALOG_BLOCKS = ["dac_sweep", "output_buffer", "current_sense",
                 "voltage_sense", "protection"]
DIGITAL_BLOCKS = ["mcu"]
SPLIT_BLOCKS = ["power", "adc"]      # straddle the boundary; see docs

# Board geometry, all in mm. Derived in size_board() from the actual placement.
EDGE_CLEARANCE = FAB["edge_clearance"]
BLOCK_GUTTER = 4.0                   # between block regions
# Room for a reference designator between neighbouring courtyards: the text is
# SILK_TEXT_HEIGHT tall and needs SILK_TEXT_GAP either side of it.
PART_GUTTER = 2.0                    # between courtyards inside a block
MOUNTING_HOLE_DIA = 3.2              # M3 clearance
MOUNTING_HOLE_PAD = 6.0
MOUNTING_HOLE_INSET = 5.0
# _mounting_hole draws its courtyard 0.25 mm outside the pad; keep the two in
# step so size_board() budgets the space KiCad will actually check.
MOUNTING_HOLE_CRTYD = MOUNTING_HOLE_PAD / 2 + 0.25
ZONE_GAP = 2.0                       # AGND zone to DGND zone, see docs

# Silkscreen. The board is a lab instrument that gets probed at the bench, so
# every designator stays visible; none is hidden or deleted to quiet DRC.
# Height and thickness both clear docs/FAB_CONSTRAINTS.md F9 (0.15 mm minimum
# line width) and KiCad's own min_text_height of 0.8 mm.
SILK_TEXT_HEIGHT = 1.0
SILK_TEXT_THICKNESS = FAB["silk_line_width"]        # F9
SILK_TEXT_GAP = 0.3                  # clear space around a designator
# KiCad's stroke font advances roughly one glyph height per character, plus
# inter-character spacing. 0.72 was measured off a rendered SVG and was WRONG
# for this purpose: it understated the width by about a third, which is why 47
# designators landed on copper and 69 on other silk while this project's own
# check reported zero. 1.15 is deliberately generous - overestimating the box
# costs board area, underestimating it costs a DRC warning per part.
SILK_CHAR_ADVANCE = 1.15
# KiCad flags silk that comes within this of copper or other silk. It is a
# clearance, not an overlap: two objects 0.6 mm apart were reported.
SILK_CLEARANCE = 0.25
# Solder-mask openings are slightly larger than the pads they expose, and
# silk_over_copper is really "silk over the mask aperture".
SOLDER_MASK_MARGIN = 0.05


def silk_text_box(text, cx, cy, height=None, clearance=SILK_CLEARANCE):
    """The area a piece of silkscreen text needs, centred on (cx, cy).

    THE single definition, imported by validation/drc_check.py so the placer
    and the checker cannot disagree. They did: each had its own copy of this
    formula, the checker's was the smaller, and it certified as clean a board
    KiCad then reported 161 silkscreen warnings on.
    """
    height = SILK_TEXT_HEIGHT if height is None else height
    w = SILK_CHAR_ADVANCE * height * len(text) + SILK_TEXT_THICKNESS
    h = height + SILK_TEXT_THICKNESS
    return (cx - w / 2 - clearance, cy - h / 2 - clearance,
            cx + w / 2 + clearance, cy + h / 2 + clearance)

# Shelf packing on courtyard bounding boxes wastes roughly half the area
# (rectangles of very different heights on shared shelves). 0.45 is measured
# from this design's own placement, not assumed: see docs/pcb_generation.md.
PACKING_EFFICIENCY = 0.45
TARGET_ASPECT = 1.5                  # width : height of the placement area


def uid(key):
    return str(_uuid.uuid5(UUID_NS, key))


def A(text, quoted=None):
    """Atom helper for building nodes."""
    return Atom(str(text), 0, bool(quoted) if quoted is not None else False)


def Q(text):
    return Atom(str(text), 0, True)


def L(*items):
    return SexpList(items)


def num(value):
    """Format a length the way KiCad does: trim trailing zeros."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return A(text if text not in ("", "-0") else "0")


# =====================================================================
#  Footprint loading: a vendored .kicad_mod goes into the board VERBATIM
# =====================================================================

# A standalone .kicad_mod carries these; a footprint inside a board does not.
# Everything else - descr, tags, properties, graphics, pads, models - is copied
# unchanged, because the file is already in the board's own schema.
_DROP_AT_TOP = {"version", "generator", "generator_version"}


def load_footprint(libs, fp_id):
    """Parse a vendored footprint and prepare it for placement on the board.

    No format conversion happens. ``libraries/footprints`` holds KiCad 9 files
    and the board is emitted in the KiCad 9/10 schema, so the footprint is
    already in the right syntax — which is the point: the previous version of
    this file rewrote each footprint into KiCad 7 syntax, and relying on
    KiCad's v7 board migration is what lost the Edge.Cuts outline on the
    reviewer's KiCad 9.
    """
    fp = libs.get_footprint(fp_id)
    if fp is None:
        raise RuntimeError(f"footprint {fp_id!r} does not resolve; run "
                           f"validation/footprint_check.py")
    with open(fp.file, encoding="utf-8") as fh:
        tree = parse_sexp(fh.read())
    src = None
    for child in tree:
        if isinstance(child, list) and child and str(child[0]) == "footprint":
            src = child
            break
    if src is None:
        raise RuntimeError(f"{fp.file}: no (footprint ...) node")

    # The footprint's own name in a board file is the FULL library id. Emitting
    # the bare name makes KiCad report "the current configuration does not
    # include the library ''" for every footprint on the board.
    out = [src[0], Q(fp_id), L(A("layer"), Q("F.Cu")),
           L(A("uuid"), Q(uid(f"fp:{fp_id}")))]
    for child in src[2:]:
        if isinstance(child, list) and child and \
                str(child[0]) in _DROP_AT_TOP:
            continue
        if isinstance(child, list) and child and str(child[0]) == "layer":
            continue                    # replaced above
        out.append(child)
    return SexpList(out), fp.file


def _primitive_points(node):
    """Every (x, y) a graphic primitive occupies — circles expanded properly.

    A ``(fp_circle (center cx cy) (end ex ey))`` is defined by two points, and
    taking their bounding box understates the shape badly: a 6.1 mm banana-jack
    courtyard collapses to a line. KiCad's own DRC does not make that mistake,
    which is how 45 courtyards_overlap errors appeared on a board this project
    had certified as overlap-free. Expand the circle to centre +/- radius.
    """
    head = str(node[0]) if node and isinstance(node[0], str) else ""
    kids = {}
    for c in node:
        if isinstance(c, list) and c and isinstance(c[0], str):
            kids.setdefault(str(c[0]), []).append(c)

    if head in ("fp_circle", "gr_circle", "circle"):
        ctr = kids.get("center", [None])[0]
        end = kids.get("end", [None])[0]
        if ctr is not None and end is not None:
            cx, cy = float(ctr[1]), float(ctr[2])
            r = math.hypot(float(end[1]) - cx, float(end[2]) - cy)
            return [(cx - r, cy - r), (cx + r, cy + r)]

    pts = []
    for key in ("start", "end", "center", "mid"):
        for c in kids.get(key, []):
            if len(c) > 2:
                pts.append((float(c[1]), float(c[2])))
    for c in kids.get("pts", []):
        for p in c:
            if isinstance(p, list) and str(p[0]) == "xy" and len(p) > 2:
                pts.append((float(p[1]), float(p[2])))
    return pts


def courtyard_bbox(node):
    """(w, h, cx, cy) of the footprint courtyard, or the pad extent if absent."""
    xs, ys = [], []

    def walk(n, want_courtyard):
        if not isinstance(n, list):
            return
        head = str(n[0]) if n and isinstance(n[0], str) else ""
        layer = None
        for c in n:
            if isinstance(c, list) and c and str(c[0]) == "layer":
                layer = str(c[1]) if len(c) > 1 else None
        on_crtyd = layer in ("F.CrtYd", "B.CrtYd")
        if head in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
            if on_crtyd == want_courtyard:
                for px, py in _primitive_points(n):
                    xs.append(px); ys.append(py)
        if head == "pad" and not want_courtyard:
            at = size = None
            for c in n:
                if isinstance(c, list) and str(c[0]) == "at":
                    at = (float(c[1]), float(c[2]))
                if isinstance(c, list) and str(c[0]) == "size":
                    size = (float(c[1]), float(c[2]))
            if at and size:
                xs.extend([at[0] - size[0] / 2, at[0] + size[0] / 2])
                ys.extend([at[1] - size[1] / 2, at[1] + size[1] / 2])
        for c in n:
            if isinstance(c, list):
                walk(c, want_courtyard)

    walk(node, True)
    if not xs:
        walk(node, False)
    if not xs:
        return 2.0, 2.0, 0.0, 0.0
    return (max(xs) - min(xs), max(ys) - min(ys),
            (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2)


def set_position(node, x, y):
    """Set the footprint's (at x y), inserting it if absent."""
    for i, child in enumerate(node):
        if isinstance(child, list) and str(child[0]) == "at":
            node[i] = L(A("at"), num(x), num(y))
            return
    node.insert(2, L(A("at"), num(x), num(y)))


def _set_uuid(node, value):
    for i, child in enumerate(node):
        if isinstance(child, list) and str(child[0]) == "uuid":
            node[i] = L(A("uuid"), Q(value))
            return


def _set_property(node, name, value):
    """Set a v9 footprint field, which is (property "Name" "value" ...)."""
    for child in node:
        if (isinstance(child, list) and str(child[0]) == "property"
                and len(child) > 2 and str(child[1]) == name):
            child[2] = Q(value)
            return True
    return False


def set_reference(node, ref):
    _set_property(node, "Reference", ref)


def set_value(node, value):
    _set_property(node, "Value", value)


# SKiDL pin function -> KiCad pad pintype. The board carries the same
# electrical type the schematic does; nothing here invents one.
PINTYPE = {
    "INPUT": "input", "OUTPUT": "output", "BIDIR": "bidirectional",
    "TRISTATE": "tri_state", "PASSIVE": "passive", "UNSPEC": "unspecified",
    "PWRIN": "power_in", "PWROUT": "power_out", "OPENCOLL": "open_collector",
    "OPENEMIT": "open_emitter", "NOCONNECT": "no_connect", "FREE": "free",
    "PULLUP": "passive", "PULLDN": "passive",
}


def assign_nets(node, pad_nets):
    """Attach (net N "name") to each pad. Unlisted pads stay unconnected."""
    for child in node:
        if not (isinstance(child, list) and str(child[0]) == "pad"):
            continue
        pad_name = str(child[1]) if len(child) > 1 else ""
        entry = pad_nets.get(pad_name)
        if entry is None:
            continue
        code, name, pin_name, pin_type = entry
        child.append(L(A("net"), A(code), Q(name)))
        if pin_name:
            child.append(L(A("pinfunction"), Q(pin_name)))
        child.append(L(A("pintype"), Q(pin_type)))


# =====================================================================
#  Placement
# =====================================================================

class Placed:
    __slots__ = ("ref", "node", "w", "h", "cx", "cy", "x", "y", "block",
                 "split_offset")

    def __init__(self, ref, node, block):
        self.ref, self.node, self.block = ref, node, block
        self.w, self.h, self.cx, self.cy = courtyard_bbox(node)
        self.x = self.y = 0.0
        # For a part that bonds to both ground systems: the footprint-local x
        # at which its AGND pads end and its DGND pads begin. None otherwise.
        self.split_offset = None


def _ground_bridges(circuit):
    """{ref: (max AGND pad x, min DGND pad x)} for parts touching both grounds.

    The ADS1256 (U8) has AGND and DGND pins because the die has two separate
    grounds; the star resistor R31 is the single deliberate bridge between the
    planes. Neither is a mistake, and neither may be placed wholly on one side
    of the split — the AGND pins have to sit over the analog pour and the DGND
    pins over the digital pour, or the layout contradicts the schematic.

    Which parts these are is *read from the circuit*, not listed here, so a
    part that grows a second ground connection cannot slip past.
    """
    out = {}
    for part in circuit.parts:
        sides = {}
        for pin in part.pins:
            for net in pin.get_nets():
                if net.name in ("AGND", "DGND"):
                    sides.setdefault(net.name, []).append(str(pin.num))
        if len(sides) == 2:
            out[part.ref] = {k: set(v) for k, v in sides.items()}
    return out


def _pad_x(node, pad_names):
    """Footprint-local x of every pad in ``pad_names``."""
    xs = []
    for child in node:
        if not (isinstance(child, list) and str(child[0]) == "pad"):
            continue
        if str(child[1]) not in pad_names:
            continue
        for c in child:
            if isinstance(c, list) and str(c[0]) == "at":
                xs.append(float(c[1]))
                break
    return xs


def _split_offset(node, sides, ref):
    """Where the ground split must fall inside this footprint, locally.

    Raises if the footprint's AGND pads are not all left of its DGND pads: that
    means the part is oriented wrongly for the plane split, and silently
    placing it anyway would put digital return current under the analog pour.
    """
    agnd = _pad_x(node, sides["AGND"])
    dgnd = _pad_x(node, sides["DGND"])
    if not agnd or not dgnd:
        raise RuntimeError(f"{ref}: ground pads not found in the footprint")
    if max(agnd) >= min(dgnd):
        raise RuntimeError(
            f"{ref} cannot straddle the AGND/DGND split as placed: its AGND "
            f"pads reach x={max(agnd):.3f} but its DGND pads start at "
            f"x={min(dgnd):.3f}. Rotate or mirror the footprint so the analog "
            f"ground pins face the analog pour.")
    return (max(agnd) + min(dgnd)) / 2


def _decoupling_partner(circuit):
    """{cap_ref: ic_ref} for caps that share a net with exactly one IC.

    Decoupling capacitors get placed next to the part they decouple rather than
    in a generic grid, which is both better layout and what keeps the four 8 mm
    electrolytics from piling up on top of each other.
    """
    out = {}
    for part in circuit.parts:
        if not part.ref.startswith("C"):
            continue
        neighbours = set()
        for pin in part.pins:
            for net in pin.get_nets():
                if type(net).__name__ == "NCNet":
                    continue
                for other in net.get_pins():
                    r = other.part.ref
                    if r != part.ref and r[0] == "U":
                        neighbours.add(r)
        if len(neighbours) == 1:
            out[part.ref] = next(iter(neighbours))
    return out


def lane_extents(bridges):
    """(left, right) width the straddle lane needs either side of the boundary."""
    if not bridges:
        return 0.0, 0.0
    left = max(p.split_offset - p.cx + p.w / 2 for p in bridges)
    right = max(-p.split_offset + p.cx + p.w / 2 for p in bridges)
    return left + PART_GUTTER, right + PART_GUTTER


def pack_lane(bridges, boundary, origin_y):
    """Stack the straddling parts down the boundary, each centred on it."""
    y = origin_y
    for item in sorted(bridges, key=lambda p: (-p.h, p.ref)):
        item.x = boundary - item.split_offset
        item.y = y + item.h / 2 - item.cy
        y += item.h + PART_GUTTER
    return max(y - PART_GUTTER - origin_y, 0.0)


def _pack_columns(by_block, column_order, widths, bridges=(), lane_after=0):
    """Pack every column at the given widths, with the straddle lane inserted.

    ``lane_after`` is the index of the column the lane follows — the analog
    column, so the lane sits exactly where the AGND pour ends. Returns
    ``(placed, col_widths, col_heights, boundary)``.
    """
    lane_l, lane_r = lane_extents(bridges)
    placed, cols, heights = {}, [], []
    x_cursor, boundary = 0.0, None
    for index, (column, max_w) in enumerate(zip(column_order, widths)):
        if max_w <= 0:
            cols.append(0.0)
            heights.append(0.0)
            continue
        col_w, y = 0.0, 0.0
        for block in column:
            items = by_block.get(block)
            if not items:
                continue
            group, w, h = pack_block(items, x_cursor, y, max_w)
            placed[block] = group
            col_w = max(col_w, w)
            y += h + BLOCK_GUTTER
        cols.append(col_w)
        heights.append(max(y - BLOCK_GUTTER, 0.0))
        x_cursor += col_w + BLOCK_GUTTER * 2
        if index == lane_after and bridges:
            boundary = x_cursor + lane_l
            pack_lane(bridges, boundary, 0.0)
            x_cursor += lane_l + lane_r + BLOCK_GUTTER * 2
    return placed, cols, heights, boundary


def layout_columns(by_block, column_order, bridges=(), iterations=24):
    """Place every block into its column, choosing column widths by relaxation.

    The starting widths are proportional to the courtyard area each column has
    to hold — the area-conserving guess — and the total width is chosen so the
    placement lands near :data:`TARGET_ASPECT` given :data:`PACKING_EFFICIENCY`.

    Shelf packing does not honour that guess exactly (a column of five small
    blocks pays four inter-block gutters; a column holding one LQFP-64 has a
    hard width floor), so the widths are then relaxed: each round scales a
    column's width by how tall it came out relative to the tallest column, and
    re-packs. This *measures* the packer rather than assuming a fill factor,
    and it converges in a handful of rounds. It is deterministic — same circuit,
    same board.
    """
    areas = [sum(p.w * p.h for b in column for p in by_block.get(b, []))
             for column in column_order]
    total = sum(areas) or 1.0
    floors = [max([p.w for b in column for p in by_block.get(b, [])] or [0.0])
              + PART_GUTTER for column in column_order]

    n_live = len([a for a in areas if a > 0])
    usable = math.sqrt(total / PACKING_EFFICIENCY * TARGET_ASPECT)
    usable = max(usable - 2 * BLOCK_GUTTER * max(n_live - 1, 0), 1.0)
    widths = [max(usable * a / total, f) if a > 0 else 0.0
              for a, f in zip(areas, floors)]

    best = None
    for _ in range(iterations):
        _placed, _cols, heights, _b = _pack_columns(by_block, column_order,
                                                    widths, bridges)
        span = max(heights) - min(h for h in heights if h > 0)
        if best is None or span < best[0]:
            best = (span, list(widths))
        if span < PART_GUTTER:
            break
        tallest = max(heights)
        new = []
        for w, h, f, a in zip(widths, heights, floors, areas):
            if a <= 0:
                new.append(0.0)
                continue
            # Widen columns that came out tall, narrow the ones that came out
            # short. The 0.5 exponent damps the step so it cannot oscillate.
            new.append(max(w * (h / tallest) ** 0.5 if tallest > 0 else w, f))
        scale = sum(w for w in widths if w > 0) / (sum(new) or 1.0)
        widths = [w * scale if w > 0 else 0.0 for w in new]

    # Re-pack at the best widths: pack_block mutates the Placed objects, so the
    # coordinates on record must come from the last pack performed.
    placed, _cols, _heights, boundary = _pack_columns(
        by_block, column_order, best[1], bridges)
    return placed, best[1], boundary


def pack_block(items, origin_x, origin_y, max_width):
    """Shelf-pack courtyards left-to-right, tallest-first per row."""
    items = sorted(items, key=lambda p: (-p.h, -p.w, p.ref))
    x, y, row_h, placed = origin_x, origin_y, 0.0, []
    for it in items:
        if x > origin_x and (x + it.w + PART_GUTTER) - origin_x > max_width:
            x = origin_x
            y += row_h + PART_GUTTER
            row_h = 0.0
        it.x = x + it.w / 2 - it.cx
        it.y = y + it.h / 2 - it.cy
        placed.append(it)
        x += it.w + PART_GUTTER
        row_h = max(row_h, it.h)
    height = (y + row_h) - origin_y
    width = min(max_width, max((p.x + p.cx + p.w / 2) for p in placed) - origin_x
                if placed else 0.0)
    return placed, width, height


# =====================================================================
#  Board emission
# =====================================================================

# The KiCad 9/10 layer numbering, copied from a board KiCad itself wrote
# (kicad/qa/data/pcbnew/api_kitchen_sink.kicad_pcb @ 10.0.5). KiCad 8 renumbered
# every layer: B.Cu moved from 31 to 2 and Edge.Cuts from 44 to 25. Emitting the
# KiCad 7 numbering and trusting the loader to migrate it is what silently lost
# the board outline - KiCad 9 reported "no edges found on Edge.Cuts layer" for a
# rectangle KiCad 7 read without complaint.
LAYERS = [
    (0, "F.Cu", "signal"), (2, "B.Cu", "signal"),
    (9, "F.Adhes", "user", "F.Adhesive"), (11, "B.Adhes", "user", "B.Adhesive"),
    (13, "F.Paste", "user"), (15, "B.Paste", "user"),
    (5, "F.SilkS", "user", "F.Silkscreen"),
    (7, "B.SilkS", "user", "B.Silkscreen"),
    (1, "F.Mask", "user"), (3, "B.Mask", "user"),
    (17, "Dwgs.User", "user", "User.Drawings"),
    (19, "Cmts.User", "user", "User.Comments"),
    (21, "Eco1.User", "user", "User.Eco1"),
    (23, "Eco2.User", "user", "User.Eco2"),
    (25, "Edge.Cuts", "user"), (27, "Margin", "user"),
    (31, "F.CrtYd", "user", "F.Courtyard"),
    (29, "B.CrtYd", "user", "B.Courtyard"),
    (35, "F.Fab", "user"), (33, "B.Fab", "user"),
]


def _layers_block():
    rows = []
    for entry in LAYERS:
        idx, name, kind = entry[0], entry[1], entry[2]
        row = [A(idx), Q(name), A(kind)]
        if len(entry) > 3:
            row.append(Q(entry[3]))
        rows.append(L(*row))
    return L(A("layers"), *rows)


def _setup_block():
    return L(
        A("setup"),
        L(A("pad_to_mask_clearance"), A("0")),
        L(A("allow_soldermask_bridges_in_footprints"), A("no")),
        L(A("pcbplotparams"),
          L(A("layerselection"), A("0x00010fc_ffffffff")),
          L(A("plot_on_all_layers_selection"), A("0x0000000_00000000")),
          L(A("disableapertmacros"), A("false")),
          L(A("usegerberextensions"), A("false")),
          L(A("usegerberattributes"), A("true")),
          L(A("usegerberadvancedattributes"), A("true")),
          L(A("creategerberjobfile"), A("true")),
          L(A("dashed_line_dash_ratio"), A("12.000000")),
          L(A("dashed_line_gap_ratio"), A("3.000000")),
          L(A("svgprecision"), A("4")),
          L(A("plotframeref"), A("false")),
          L(A("viasonmask"), A("false")),
          L(A("mode"), A("1")),
          L(A("useauxorigin"), A("false")),
          L(A("hpglpennumber"), A("1")),
          L(A("hpglpenspeed"), A("20")),
          L(A("hpglpendiameter"), A("15.000000")),
          L(A("pdf_front_fp_property_popups"), A("true")),
          L(A("pdf_back_fp_property_popups"), A("true")),
          L(A("dxfpolygonmode"), A("true")),
          L(A("dxfimperialunits"), A("true")),
          L(A("dxfusepcbnewfont"), A("true")),
          L(A("psnegative"), A("false")),
          L(A("psa4output"), A("false")),
          L(A("plotreference"), A("true")),
          L(A("plotvalue"), A("true")),
          L(A("plotfptext"), A("true")),
          L(A("plotinvisibletext"), A("false")),
          L(A("sketchpadsonfab"), A("false")),
          L(A("subtractmaskfromsilk"), A("false")),
          L(A("outputformat"), A("1")),
          L(A("mirror"), A("false")),
          L(A("drillshape"), A("1")),
          L(A("scaleselection"), A("1")),
          L(A("outputdirectory"), Q("")),
          ),
    )


def _edge_rect(x0, y0, x1, y1):
    """The board outline, as a single gr_rect.

    It was four chained gr_line segments. That is a valid outline — every
    endpoint shared by exactly two segments, verified — and KiCad 7 read it
    without complaint, but a reviewer's KiCad still reported "no edges found on
    Edge.Cuts layer". A single rectangle removes chaining from the problem
    entirely: there are no endpoints to match and no epsilon to fall foul of,
    and it is what KiCad itself writes when you draw a rectangular board.

    HONEST STATUS: this is a hypothesis. The KiCad in this build environment
    cannot open a v9 board, so it has not been confirmed against the tool that
    reported the problem. See docs/silkscreen_and_packages.md.
    """
    return [L(A("gr_rect"),
              L(A("start"), num(x0), num(y0)),
              L(A("end"), num(x1), num(y1)),
              L(A("stroke"), L(A("width"), A("0.1")),
                L(A("type"), A("default"))),
              L(A("fill"), A("no")),
              L(A("layer"), Q("Edge.Cuts")),
              L(A("uuid"), Q(uid("edge:rect"))))]


def _mounting_hole(x, y, index):
    """A non-plated M3 mounting hole, in the board's own (KiCad 9/10) syntax.

    Hand-built rather than vendored because MountingHole is a mechanical part
    with no schematic symbol, so it has no entry in the circuit. Every node
    below matches what KiCad itself writes: (property "Reference" ...) rather
    than the KiCad 7 (fp_text reference ...), (hide yes) rather than a bare
    hide, and (uuid "...") rather than (tstamp ...).
    """
    return L(
        A("footprint"), Q("MountingHole:MountingHole_3.2mm_M3_Pad"),
        L(A("layer"), Q("F.Cu")),
        L(A("uuid"), Q(uid(f"mount:{index}"))),
        L(A("at"), num(x), num(y)),
        L(A("attr"), A("through_hole"), A("exclude_from_pos_files"),
          A("exclude_from_bom")),
        L(A("property"), Q("Reference"), Q(f"H{index}"),
          L(A("at"), A("0"), num(-MOUNTING_HOLE_PAD / 2 - 1), A("0")),
          L(A("layer"), Q("F.SilkS")),
          L(A("uuid"), Q(uid(f"mount:ref:{index}"))),
          L(A("effects"), L(A("font"), L(A("size"), A("1"), A("1")),
                            L(A("thickness"), A("0.15"))))),
        L(A("property"), Q("Value"), Q("MountingHole"),
          L(A("at"), A("0"), num(MOUNTING_HOLE_PAD / 2 + 1), A("0")),
          L(A("layer"), Q("F.Fab")),
          L(A("hide"), A("yes")),
          L(A("uuid"), Q(uid(f"mount:val:{index}"))),
          L(A("effects"), L(A("font"), L(A("size"), A("1"), A("1")),
                            L(A("thickness"), A("0.15"))))),
        L(A("fp_circle"),
          L(A("center"), A("0"), A("0")),
          L(A("end"), num(MOUNTING_HOLE_CRTYD), A("0")),
          L(A("stroke"), L(A("width"), A("0.05")), L(A("type"), A("default"))),
          L(A("fill"), A("no")),
          L(A("layer"), Q("F.CrtYd")),
          L(A("uuid"), Q(uid(f"mount:crt:{index}")))),
        # Non-plated: a mask opening only, no copper. Matches how KiCad writes
        # an np_thru_hole pad.
        L(A("pad"), Q(""), A("np_thru_hole"), A("circle"),
          L(A("at"), A("0"), A("0")),
          L(A("size"), num(MOUNTING_HOLE_DIA), num(MOUNTING_HOLE_DIA)),
          L(A("drill"), num(MOUNTING_HOLE_DIA)),
          L(A("layers"), Q("*.Mask")),
          L(A("uuid"), Q(uid(f"mount:pad:{index}")))),
    )


def _zone(net_code, net_name, poly, index, clearance, min_width):
    pts = L(A("pts"), *[L(A("xy"), num(x), num(y)) for x, y in poly])
    return L(
        A("zone"),
        L(A("net"), A(net_code)),
        L(A("net_name"), Q(net_name)),
        L(A("layers"), Q("F.Cu"), Q("B.Cu")),
        L(A("uuid"), Q(uid(f"zone:{net_name}:{index}"))),
        L(A("name"), Q(f"{net_name}_zone")),
        L(A("hatch"), A("edge"), A("0.5")),
        L(A("connect_pads"), L(A("clearance"), num(clearance))),
        L(A("min_thickness"), num(min_width)),
        L(A("filled_areas_thickness"), A("no")),
        L(A("fill"), A("yes"),
          L(A("thermal_gap"), A("0.5")),
          L(A("thermal_bridge_width"), A("0.5"))),
        L(A("polygon"), pts),
    )



# =====================================================================
#  Silkscreen: reference designators, placed so they can be read
# =====================================================================

def _silk_bbox(text, cx, cy):
    return silk_text_box(text, cx, cy)


def _overlaps(a, b):
    return (a[0] < b[2] - 1e-9 and b[0] < a[2] - 1e-9
            and a[1] < b[3] - 1e-9 and b[1] < a[3] - 1e-9)


def _silk_obstacles(items):
    """Everything a designator must not sit on, in board coordinates.

    Three kinds, and all three are real DRC classes:
      * pads            -> silk_over_copper ("clipped by solder mask")
      * courtyards      -> text under a part body, unreadable once assembled
      * footprint silk  -> silk_overlap, silk-to-silk
    """
    boxes = []
    for item in items:
        boxes.append((item.x + item.cx - item.w / 2,
                      item.y + item.cy - item.h / 2,
                      item.x + item.cx + item.w / 2,
                      item.y + item.cy + item.h / 2))
        for child in item.node:
            if not isinstance(child, list):
                continue
            head = str(child[0])
            if head == "pad":
                at = size = None
                for c in child:
                    if isinstance(c, list) and str(c[0]) == "at":
                        at = (float(c[1]), float(c[2]))
                    if isinstance(c, list) and str(c[0]) == "size":
                        size = (float(c[1]), float(c[2]))
                if at and size:
                    m = SOLDER_MASK_MARGIN
                    boxes.append((item.x + at[0] - size[0] / 2 - m,
                                  item.y + at[1] - size[1] / 2 - m,
                                  item.x + at[0] + size[0] / 2 + m,
                                  item.y + at[1] + size[1] / 2 + m))
            elif head.startswith("fp_"):
                layer = None
                for c in child:
                    if isinstance(c, list) and str(c[0]) == "layer":
                        layer = str(c[1])
                if layer not in ("F.SilkS", "B.SilkS"):
                    continue
                pts = _primitive_points(child)
                if pts:
                    boxes.append((item.x + min(p[0] for p in pts),
                                  item.y + min(p[1] for p in pts),
                                  item.x + max(p[0] for p in pts),
                                  item.y + max(p[1] for p in pts)))
    return boxes


def place_references(items):
    """Give every reference designator a position clear of copper and silk.

    The algorithm, in order:

    1. Candidates are generated on a ring around the part's own courtyard —
       above, below, right, left, then the four diagonals — each at
       ``SILK_TEXT_GAP`` from the courtyard edge. The ring then widens in
       0.5 mm steps, up to 3 mm out, so a designator that cannot fit tight
       against its part can still sit near it.
    2. A candidate is accepted when its text box (including clear space)
       overlaps no pad, no courtyard and no other footprint's silkscreen — the
       three things that produce ``silk_over_copper`` and ``silk_overlap`` —
       and no designator already placed.
    3. Every designator is horizontal. One convention, applied uniformly: a
       board read at the bench should not need turning.
    4. If nothing fits, the designator moves to **F.Fab**, the assembly-drawing
       layer, at the part's centre. It is never hidden and never deleted — the
       part remains identifiable on the fabrication drawing, and the trade-off
       is reported rather than buried.

    Returns ``(fallbacks, boxes)`` — the references that had to fall back to
    F.Fab, and the area every placed designator occupies, so that the
    board-level silkscreen can avoid them too.
    """
    obstacles = _silk_obstacles(items)
    placed_text = []
    fallbacks = []

    # Largest parts first: they have the most neighbours competing for space,
    # and a small part's designator is easier to fit into what is left.
    for item in sorted(items, key=lambda p: -(p.w * p.h)):
        cx = item.x + item.cx
        cy = item.y + item.cy
        half_w = item.w / 2
        half_h = item.h / 2
        box0 = silk_text_box(item.ref, 0.0, 0.0)
        tw, th = box0[2], box0[3]

        chosen = None
        for extra in [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0]:
            ring = [
                (cx, cy - half_h - th - extra),          # above
                (cx, cy + half_h + th + extra),          # below
                (cx + half_w + tw + extra, cy),          # right
                (cx - half_w - tw - extra, cy),          # left
                (cx + half_w + tw + extra, cy - half_h - th - extra),
                (cx - half_w - tw - extra, cy - half_h - th - extra),
                (cx + half_w + tw + extra, cy + half_h + th + extra),
                (cx - half_w - tw - extra, cy + half_h + th + extra),
            ]
            for bx, by in ring:
                box = _silk_bbox(item.ref, bx, by)
                if any(_overlaps(box, o) for o in obstacles):
                    continue
                if any(_overlaps(box, o) for o in placed_text):
                    continue
                chosen = (bx, by)
                break
            if chosen:
                break

        if chosen is None:
            _set_reference_placement(item.node, item.cx, item.cy, "F.Fab")
            fallbacks.append(item.ref)
            continue

        if not _set_reference_placement(item.node, chosen[0] - item.x,
                                        chosen[1] - item.y, "F.SilkS"):
            raise RuntimeError(
                f"{item.ref}: no Reference property to position. A designator "
                f"that cannot be moved must not be reported as placed.")
        placed_text.append(_silk_bbox(item.ref, *chosen))
    return fallbacks, placed_text


def _set_reference_placement(node, dx, dy, layer):
    """Move a footprint's Reference field to a local offset, on a given layer.

    The field is never hidden: this board is a lab instrument that gets probed,
    and a designator nobody can read costs more at the bench than a silkscreen
    warning costs in DRC.
    """
    for index, child in enumerate(node):
        if not (isinstance(child, list) and str(child[0]) == "property"
                and len(child) > 2 and str(child[1]) == "Reference"):
            continue
        keep = [child[0], child[1], child[2]]
        for c in child[3:]:
            if isinstance(c, list) and str(c[0]) in ("at", "layer", "hide",
                                                     "effects"):
                continue
            keep.append(c)
        keep.append(L(A("at"), num(dx), num(dy), A("0")))
        keep.append(L(A("layer"), Q(layer)))
        keep.append(L(A("effects"),
                      L(A("font"),
                        L(A("size"), num(SILK_TEXT_HEIGHT),
                          num(SILK_TEXT_HEIGHT)),
                        L(A("thickness"), num(SILK_TEXT_THICKNESS)))))
        # Assign by the index we are already holding. This used to be
        # node.index(child), an equality search - and it silently resolved to
        # the wrong element (or failed) for 62 of 114 footprints, leaving their
        # designators at the library default position while place_references
        # believed it had moved them.
        node[index] = SexpList(keep)
        return True
    return False



def _gr_text(text, x, y, layer="F.SilkS", height=SILK_TEXT_HEIGHT, key=""):
    return L(A("gr_text"), Q(text),
             L(A("at"), num(x), num(y), A("0")),
             L(A("layer"), Q(layer)),
             L(A("uuid"), Q(uid(f"silk:{key or text}:{x:.3f}:{y:.3f}"))),
             L(A("effects"),
               L(A("font"), L(A("size"), num(height), num(height)),
                 L(A("thickness"), num(SILK_TEXT_THICKNESS)))))


def board_silkscreen(x0, y0, x1, y1, boundary, items, circuit, ref_boxes=()):
    """Board-level silkscreen: identity, the ground split, and the terminals.

    Everything here is placed in the board margin — between the outline and the
    parts — so none of it can land on copper or on a part's own silk. The
    margin exists because size_board() budgets the mounting-hole courtyards,
    and it is the one region guaranteed to be clear.

    Mislabelling a +/-15 V bench supply terminal is a real hazard, so J1's pins
    are named individually rather than with a single block label.
    """
    out = []
    margin_top = y0 + EDGE_CLEARANCE + SILK_TEXT_HEIGHT
    margin_bot = y1 - EDGE_CLEARANCE - SILK_TEXT_HEIGHT
    obstacles = _silk_obstacles(items) + list(ref_boxes)

    def emit(text, x, y, height=SILK_TEXT_HEIGHT, key=""):
        """Emit board text and register the space it takes."""
        obstacles.append(silk_text_box(text, x, y, height))
        out.append(_gr_text(text, x, y, height=height, key=key))

    # --- identity -------------------------------------------------------
    emit("Stage 1 I-V Characterization System", x0 + (x1 - x0) / 2,
         margin_top, height=1.5, key="title")
    emit("rev A  -  generated by generators/pcb.py  -  UNROUTED",
         x0 + (x1 - x0) / 2, margin_top + 2.6, key="rev")

    # --- the ground split, marked so a bench user can see it ------------
    # Ticks in the margins rather than a line across the board: the split lane
    # holds R31 and U8, and silk over them would be both unreadable and a
    # silk_overlap violation.
    for i, (ya, yb) in enumerate((
            (y0 + EDGE_CLEARANCE, y0 + EDGE_CLEARANCE + 3.0),
            (y1 - EDGE_CLEARANCE - 3.0, y1 - EDGE_CLEARANCE))):
        out.append(L(A("gr_line"),
                     L(A("start"), num(boundary), num(ya)),
                     L(A("end"), num(boundary), num(yb)),
                     L(A("stroke"), L(A("width"), num(SILK_TEXT_THICKNESS)),
                       L(A("type"), A("dash"))),
                     L(A("layer"), Q("F.SilkS")),
                     L(A("uuid"), Q(uid(f"silk:split:{i}")))))
    out.append(_gr_text("AGND <", boundary - 6.0, margin_bot, key="agnd"))
    out.append(_gr_text("> DGND", boundary + 6.0, margin_bot, key="dgnd"))

    # --- terminal labelling ---------------------------------------------
    # The designators are obstacles too. Leaving them out is how the terminal
    # labels came to overlap J1's and J5's reference fields.
    obstacles = _silk_obstacles(items) + list(ref_boxes)
    by_ref = {i.ref: i for i in items}
    dut_missing = []

    def label(ref, pad_name, text):
        """Put `text` beside a specific pad, in the first clear spot."""
        item = by_ref.get(ref)
        if item is None:
            return
        for child in item.node:
            if not (isinstance(child, list) and str(child[0]) == "pad"
                    and str(child[1]) == pad_name):
                continue
            at = None
            for c in child:
                if isinstance(c, list) and str(c[0]) == "at":
                    at = (float(c[1]), float(c[2]))
            if at is None:
                return
            px, py = item.x + at[0], item.y + at[1]
            for radius in (2.2, 3.0, 4.0, 5.0, 6.5, 8.0):
                for dx, dy in ((0, -radius), (0, radius),
                               (-radius * 1.6, 0), (radius * 1.6, 0)):
                    box = _silk_bbox(text, px + dx, py + dy)
                    if not any(_overlaps(box, o) for o in obstacles):
                        emit(text, px + dx, py + dy,
                             key=f"{ref}.{pad_name}")
                        return True
            return False
        return False


    # J1 is the bench supply input. Every pin named: getting +15 V onto the
    # -15 V terminal destroys the analog front end.
    j1_pins = (("1", "+15V IN"), ("2", "GND IN"),
               ("3", "-15V IN"), ("4", "+5V IN"))
    j1_placed = all(label("J1", pad, text) for pad, text in j1_pins)

    # J1 carries the bench supply. Putting +15 V on the -15 V terminal destroys
    # the analog front end, so its pinout is ALSO written as a legend in the
    # board margin, in pin order. The margin is guaranteed clear, so this text
    # always lands even when the per-pad labels cannot be fitted - the labelling
    # is never left to chance on this connector.
    legend = "J1 (bench supply), pin 1 -> 4:  " + \
             "  |  ".join(f"{n} {t.replace(' IN', '')}"
                          for n, t in j1_pins)
    emit(legend, x0 + (x1 - x0) / 2, margin_bot - 2.6, key="j1legend")
    if not j1_placed:
        emit("see J1 pinout legend below", x0 + (x1 - x0) / 2,
             margin_bot - 5.2, key="j1note")
    # The four DUT terminals - a Kelvin set, and easy to cross-connect.
    for ref, text in (("J4", "FORCE+"), ("J5", "SENSE+"),
                      ("J6", "SENSE-"), ("J7", "FORCE-")):
        if not label(ref, "1", text):
            dut_missing.append(f"{ref} ({text})")
    return out


def size_board(placed_blocks):
    """Board rectangle from the actual placement, plus edge clearance."""
    xs = [p.x + p.cx - p.w / 2 for group in placed_blocks.values() for p in group]
    xs += [p.x + p.cx + p.w / 2 for group in placed_blocks.values() for p in group]
    ys = [p.y + p.cy - p.h / 2 for group in placed_blocks.values() for p in group]
    ys += [p.y + p.cy + p.h / 2 for group in placed_blocks.values() for p in group]
    # The mounting hole's COURTYARD, not its pad, is what must clear the parts:
    # KiCad checks courtyards, and _mounting_hole draws one 0.25 mm outside the
    # pad. Budgeting only the pad radius left the corner holes overlapping the
    # nearest part by 0.25 mm, which KiCad reported as courtyards_overlap.
    margin = (EDGE_CLEARANCE + MOUNTING_HOLE_INSET + MOUNTING_HOLE_CRTYD
              + PART_GUTTER)
    return (min(xs) - margin, min(ys) - margin,
            max(xs) + margin, max(ys) + margin)


def generate_pcb(circuit, out_dir="."):
    """Emit stage1_iv.kicad_pcb. Returns (path, report dict)."""
    problems = check_against_fab()
    if problems:
        raise RuntimeError("netclasses violate docs/FAB_CONSTRAINTS.md:\n  - "
                           + "\n  - ".join(problems))

    libs = KicadLibraries()

    # --- nets -----------------------------------------------------------
    net_names = sorted({n.name for n in circuit.get_nets()
                        if n.name != "__NOCONNECT"})
    net_class = classify(net_names)          # raises on an unclassified net
    codes = {"": 0}
    for i, name in enumerate(net_names, start=1):
        codes[name] = i

    pad_net = {}
    for part in circuit.parts:
        for pin in part.pins:
            for net in pin.get_nets():
                if type(net).__name__ == "NCNet" or net.name == "__NOCONNECT":
                    continue
                func = getattr(getattr(pin, "func", None), "name", None) or ""
                pad_net.setdefault(part.ref, {})[str(pin.num)] = (
                    codes[net.name], net.name, str(pin.name or ""),
                    PINTYPE.get(func, "unspecified"))

    # --- footprints -----------------------------------------------------
    physical = [p for p in circuit.parts if (p.footprint or "").strip()]
    partners = _decoupling_partner(circuit)

    bridge_pins = _ground_bridges(circuit)

    by_block, bridges = {}, []
    for part in physical:
        node, _src = load_footprint(libs, part.footprint.strip())
        _set_uuid(node, uid(f"fp:{part.ref}"))
        set_reference(node, part.ref)
        set_value(node, str(part.value))
        assign_nets(node, pad_net.get(part.ref, {}))
        node.append(L(A("path"), Q(f"/{uid('sheet:' + part.ref)}")))
        item = Placed(part.ref, node, getattr(part, "skidl_block", "misc"))
        if part.ref in bridge_pins:
            # Parts that bond to both ground systems leave their block and go
            # into the straddle lane on the plane boundary. See _ground_bridges.
            item.split_offset = _split_offset(node, bridge_pins[part.ref],
                                              part.ref)
            bridges.append(item)
        else:
            by_block.setdefault(item.block, []).append(item)

    # Decoupling caps ride with the block of the IC they serve, and are placed
    # immediately after it so the shelf packer keeps them adjacent.
    for group in by_block.values():
        order = {p.ref: i for i, p in enumerate(group)}
        group.sort(key=lambda p: (order.get(partners.get(p.ref, p.ref), 1e9),
                                  p.ref != partners.get(p.ref, p.ref)))

    # --- placement: analog left, digital right, split blocks in between ---
    column_order = (ANALOG_BLOCKS, SPLIT_BLOCKS, DIGITAL_BLOCKS)
    placed_blocks, _widths, boundary = layout_columns(by_block, column_order,
                                                      bridges)
    if boundary is None:
        raise RuntimeError("no part bonds AGND to DGND, so the ground split "
                           "has no defined location; check blocks/power.py")
    placed_blocks["ground_split"] = bridges

    # Designators are positioned before the board is sized, so the text is
    # inside the outline like everything else.
    all_items = [i for g in placed_blocks.values() for i in g]
    silk_fallbacks, ref_boxes = place_references(all_items)

    x0, y0, x1, y1 = size_board(placed_blocks)

    # --- assemble -------------------------------------------------------
    board = [A("kicad_pcb"),
             L(A("version"), A(BOARD_VERSION)),
             L(A("generator"), Q(GENERATOR)),
             L(A("generator_version"), Q("9.0")),
             L(A("general"), L(A("thickness"), A("1.6")),
               L(A("legacy_teardrops"), A("no"))),
             L(A("paper"), Q("A3")),
             _layers_block(),
             _setup_block()]

    board.append(L(A("net"), A("0"), Q("")))
    for name in net_names:
        board.append(L(A("net"), A(codes[name]), Q(name)))

    for group in placed_blocks.values():
        for item in group:
            set_position(item.node, item.x, item.y)
            board.append(item.node)

    board.extend(_edge_rect(x0, y0, x1, y1))
    board.extend(board_silkscreen(x0, y0, x1, y1, boundary, all_items,
                                  circuit, ref_boxes))

    inset = EDGE_CLEARANCE + MOUNTING_HOLE_INSET
    holes = [(x0 + inset, y0 + inset), (x1 - inset, y0 + inset),
             (x0 + inset, y1 - inset), (x1 - inset, y1 - inset)]
    for i, (hx, hy) in enumerate(holes, start=1):
        board.append(_mounting_hole(hx, hy, i))

    # --- ground zones ---------------------------------------------------
    # Two pours, one either side of `boundary`, separated by ZONE_GAP. The
    # boundary is not chosen here: it is wherever the straddle lane put the
    # parts that bond both grounds, so the copper split and the parts that
    # cross it cannot drift apart.
    zone_inset = EDGE_CLEARANCE + 0.5
    gz = ZONE_GAP / 2

    agnd_poly = [(x0 + zone_inset, y0 + zone_inset),
                 (boundary - gz, y0 + zone_inset),
                 (boundary - gz, y1 - zone_inset),
                 (x0 + zone_inset, y1 - zone_inset)]
    dgnd_poly = [(boundary + gz, y0 + zone_inset),
                 (x1 - zone_inset, y0 + zone_inset),
                 (x1 - zone_inset, y1 - zone_inset),
                 (boundary + gz, y1 - zone_inset)]

    gclear = NETCLASSES["GROUND"]["clearance"]
    gwidth = NETCLASSES["GROUND"]["track_width"]
    board.append(_zone(codes["AGND"], "AGND", agnd_poly, 1, gclear, gwidth))
    board.append(_zone(codes["DGND"], "DGND", dgnd_poly, 1, gclear, gwidth))

    text = write_sexp(SexpList(board)) + "\n"
    path = os.path.join(out_dir, "stage1_iv.kicad_pcb")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)

    report = {
        "path": path,
        "parts": len(physical),
        "nets": len(net_names),
        "board_mm": (round(x1 - x0, 2), round(y1 - y0, 2)),
        "outline_rect": (round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)),
        "zone_boundary_x": round(boundary, 3),
        "zone_gap_mm": ZONE_GAP,
        "ground_bridges": sorted(p.ref for p in bridges),
        "silk_fallbacks": sorted(silk_fallbacks),
        "mounting_holes": len(holes),
        "netclass_counts": {k: sum(1 for v in net_class.values() if v == k)
                            for k in sorted(set(net_class.values()))},
        "global_min_clearance": GLOBAL_MIN_CLEARANCE,
        "placed_blocks": {b: len(g) for b, g in sorted(placed_blocks.items())},
    }
    return path, report
