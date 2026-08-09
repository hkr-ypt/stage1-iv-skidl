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
The board is emitted in the **KiCad 7 board schema** (``version 20221018``).
That is deliberate and it is the same reasoning as
``tools/author_project_symbols.py``: KiCad 7 is the newest version installable
in this environment, so a v7 board is one this project can actually hand to
``kicad-cli`` and have parsed. KiCad 8/9 migrate v7 boards on load.

The installed footprints are KiCad 9 files, so :func:`load_footprint` converts
each one to v7 syntax on the way in — ``(uuid …)`` → ``(tstamp …)``,
``(property "Reference" …)`` → ``(fp_text reference …)``, ``(hide yes)`` →
``hide``, and the v9-only ``(version)``/``(generator)``/``(embedded_fonts)``
nodes dropped. ``tools/check_kicad_loads.py`` verifies the result parses.

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

BOARD_VERSION = "20221018"          # KiCad 7 .kicad_pcb schema
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
PART_GUTTER = 1.2                    # between courtyards inside a block
MOUNTING_HOLE_DIA = 3.2              # M3 clearance
MOUNTING_HOLE_PAD = 6.0
MOUNTING_HOLE_INSET = 5.0
# _mounting_hole draws its courtyard 0.25 mm outside the pad; keep the two in
# step so size_board() budgets the space KiCad will actually check.
MOUNTING_HOLE_CRTYD = MOUNTING_HOLE_PAD / 2 + 0.25
ZONE_GAP = 2.0                       # AGND zone to DGND zone, see docs

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
#  Footprint loading: KiCad 9 .kicad_mod -> KiCad 7 board footprint
# =====================================================================

_DROP_AT_TOP = {"version", "generator", "generator_version", "embedded_fonts",
                "model", "tags", "descr"}

# Nodes KiCad 9 spells "(name yes)" / "(name no)" but KiCad 7 spells as a bare
# "(name)" when true and omits when false.
_BOOL_FLAGS = {"remove_unused_layers", "keep_end_layers", "locked", "placed",
               "board_only", "exclude_from_pos_files", "exclude_from_bom",
               "allow_solder_mask_bridges", "allow_missing_courtyard",
               "dnp", "fields_autoplaced", "unlocked", "mirror", "bold",
               "italic"}


def _convert(node):
    """Recursively rewrite a v9 footprint node into v7 board syntax."""
    if not isinstance(node, list):
        return node
    head = str(node[0]) if node and isinstance(node[0], str) else ""

    if head == "uuid":
        return L(A("tstamp"), *[A(str(x)) for x in node[1:]])

    if head in _BOOL_FLAGS and len(node) == 2 and str(node[1]) in ("yes", "no"):
        # v9 writes these as "(flag yes|no)"; v7 writes the bare token when set
        # and omits it entirely when clear.
        return L(A(head)) if str(node[1]) == "yes" else None

    if head == "fill" and len(node) == 2 and str(node[1]) in ("yes", "no"):
        # v9 graphic fill is yes/no; v7 spells the same thing solid/none.
        return L(A("fill"), A("solid" if str(node[1]) == "yes" else "none"))

    if head == "hide" and len(node) > 1:
        # v9 "(hide yes)" is v7 bare "hide"; "(hide no)" simply disappears.
        return A("hide") if str(node[1]) == "yes" else None

    if head == "property" and len(node) > 2:
        name, value = str(node[1]), str(node[2])
        kind = {"Reference": "reference", "Value": "value"}.get(name)
        if kind is None:
            return None                      # Datasheet/Description: v7 has no slot
        rest = [_convert(c) for c in node[3:] if isinstance(c, list)]
        return L(A("fp_text"), A(kind), Q(value),
                 *[r for r in rest if r is not None])

    out = [node[0]] if head else []
    for child in node[1:]:
        if isinstance(child, list):
            ch = str(child[0]) if child and isinstance(child[0], str) else ""
            if ch in _DROP_AT_TOP and head == "footprint":
                continue
            conv = _convert(child)
            if conv is not None:
                out.append(conv)
        else:
            out.append(child)
    return SexpList(out)


def load_footprint(libs, fp_id):
    """Parse a footprint from the installed libraries, converted to v7."""
    fp = libs.get_footprint(fp_id)
    if fp is None:
        raise RuntimeError(f"footprint {fp_id!r} does not resolve; run "
                           f"validation/footprint_check.py")
    with open(fp.file, encoding="utf-8") as fh:
        tree = parse_sexp(fh.read())
    node = None
    for child in tree:
        if isinstance(child, list) and child and str(child[0]) == "footprint":
            node = child
            break
    if node is None:
        raise RuntimeError(f"{fp.file}: no (footprint ...) node")
    node = _convert(node)
    # The footprint's own name in a board file is the FULL library id. Emitting
    # the bare name makes KiCad report "The current configuration does not
    # include the library ''" for every footprint on the board, and breaks
    # "Update Footprints from Library".
    node[1] = Q(fp_id)
    return node, fp.file


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


def set_reference(node, ref):
    for child in node:
        if (isinstance(child, list) and str(child[0]) == "fp_text"
                and len(child) > 1 and str(child[1]) == "reference"):
            child[2] = Q(ref)
            return


def set_value(node, value):
    for child in node:
        if (isinstance(child, list) and str(child[0]) == "fp_text"
                and len(child) > 1 and str(child[1]) == "value"):
            child[2] = Q(value)
            return


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

LAYERS = [
    (0, "F.Cu", "signal"), (31, "B.Cu", "signal"),
    (32, "B.Adhes", "user", "B.Adhesive"), (33, "F.Adhes", "user", "F.Adhesive"),
    (34, "B.Paste", "user"), (35, "F.Paste", "user"),
    (36, "B.SilkS", "user", "B.Silkscreen"), (37, "F.SilkS", "user", "F.Silkscreen"),
    (38, "B.Mask", "user"), (39, "F.Mask", "user"),
    (40, "Dwgs.User", "user", "User.Drawings"),
    (41, "Cmts.User", "user", "User.Comments"),
    (42, "Eco1.User", "user", "User.Eco1"), (43, "Eco2.User", "user", "User.Eco2"),
    (44, "Edge.Cuts", "user"), (45, "Margin", "user"),
    (46, "B.CrtYd", "user", "B.Courtyard"), (47, "F.CrtYd", "user", "F.Courtyard"),
    (48, "B.Fab", "user"), (49, "F.Fab", "user"),
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
    out = []
    corners = [(x0, y0, x1, y0), (x1, y0, x1, y1),
               (x1, y1, x0, y1), (x0, y1, x0, y0)]
    for i, (ax, ay, bx, by) in enumerate(corners):
        out.append(L(A("gr_line"),
                     L(A("start"), num(ax), num(ay)),
                     L(A("end"), num(bx), num(by)),
                     L(A("stroke"), L(A("width"), A("0.1")),
                       L(A("type"), A("default"))),
                     L(A("layer"), Q("Edge.Cuts")),
                     L(A("tstamp"), A(uid(f"edge:{i}")))))
    return out


def _mounting_hole(x, y, index):
    """A plated-through mounting hole as a bare pad-less footprint."""
    return L(
        A("footprint"), Q("MountingHole:MountingHole_3.2mm_M3_Pad"),
        L(A("layer"), Q("F.Cu")),
        L(A("tstamp"), A(uid(f"mount:{index}"))),
        L(A("at"), num(x), num(y)),
        L(A("attr"), A("through_hole"), A("exclude_from_pos_files"),
          A("exclude_from_bom")),
        L(A("fp_text"), A("reference"), Q(f"H{index}"),
          L(A("at"), A("0"), num(-MOUNTING_HOLE_PAD / 2 - 1)),
          L(A("layer"), Q("F.SilkS")),
          L(A("effects"), L(A("font"), L(A("size"), A("1"), A("1")),
                            L(A("thickness"), A("0.15")))),
          L(A("tstamp"), A(uid(f"mount:ref:{index}")))),
        L(A("fp_text"), A("value"), Q("MountingHole"),
          L(A("at"), A("0"), num(MOUNTING_HOLE_PAD / 2 + 1)),
          L(A("layer"), Q("F.Fab")),
          A("hide"),
          L(A("effects"), L(A("font"), L(A("size"), A("1"), A("1")),
                            L(A("thickness"), A("0.15")))),
          L(A("tstamp"), A(uid(f"mount:val:{index}")))),
        L(A("fp_circle"),
          L(A("center"), A("0"), A("0")),
          L(A("end"), num(MOUNTING_HOLE_PAD / 2 + 0.25), A("0")),
          L(A("stroke"), L(A("width"), A("0.05")), L(A("type"), A("default"))),
          L(A("fill"), A("none")),
          L(A("layer"), Q("F.CrtYd")),
          L(A("tstamp"), A(uid(f"mount:crt:{index}")))),
        L(A("pad"), Q(""), A("np_thru_hole"), A("circle"),
          L(A("at"), A("0"), A("0")),
          L(A("size"), num(MOUNTING_HOLE_DIA), num(MOUNTING_HOLE_DIA)),
          L(A("drill"), num(MOUNTING_HOLE_DIA)),
          L(A("layers"), Q("F&B.Cu"), Q("*.Mask")),
          L(A("tstamp"), A(uid(f"mount:pad:{index}")))),
    )


def _zone(net_code, net_name, poly, index, clearance, min_width):
    pts = L(A("pts"), *[L(A("xy"), num(x), num(y)) for x, y in poly])
    return L(
        A("zone"),
        L(A("net"), A(net_code)),
        L(A("net_name"), Q(net_name)),
        L(A("layers"), Q("F&B.Cu")),
        L(A("tstamp"), A(uid(f"zone:{net_name}:{index}"))),
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

    x0, y0, x1, y1 = size_board(placed_blocks)

    # --- assemble -------------------------------------------------------
    board = [A("kicad_pcb"),
             L(A("version"), A(BOARD_VERSION)),
             L(A("generator"), A(GENERATOR)),
             L(A("general"), L(A("thickness"), A("1.6"))),
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
        "mounting_holes": len(holes),
        "netclass_counts": {k: sum(1 for v in net_class.values() if v == k)
                            for k in sorted(set(net_class.values()))},
        "global_min_clearance": GLOBAL_MIN_CLEARANCE,
        "placed_blocks": {b: len(g) for b, g in sorted(placed_blocks.items())},
    }
    return path, report
