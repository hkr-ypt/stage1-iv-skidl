"""
layout.py — the physical design, as source.
============================================

The SKiDL blocks say what is connected to what. This module holds the other
half of a board: where every part sits and how the copper runs. It reads and
writes the **layout source** (``layout/*.layout``), applies **engineering
change orders** (``layout/*.eco``) to it, and captures a layout from a board a
human routed in KiCad.

    blocks/*.py (SKiDL) ──► connectivity, values, footprints, fields
    layout/stage1_iv.layout ──► placement, text, tracks, vias, zones, outline
    layout/bipolar.eco ──────► the bipolar change, as explicit add/remove ops
                     └──► generators/routed_pcb.py ──► stage1_iv.kicad_pcb

What the layout source holds, and what it deliberately does not
---------------------------------------------------------------
Held — everything a person decided while laying the board out:

* ``general``, ``paper``, ``setup`` — board thickness and the fabrication
  settings (via tenting, mask, plot parameters), verbatim.
* ``rules`` — the design rules the layout was drawn to (the routed project's
  Default netclass and board minimums), so the board can be checked against
  the rules it was actually built to.
* ``graphic`` — board graphics: the Edge.Cuts outline and free silkscreen text.
* ``zone`` — copper pour definitions: net, layers, outline, clearances, thermal
  and island settings. NOT the fill, which KiCad computes.
* ``footprint`` — per reference: side, position, rotation.
* ``text`` — a footprint text (a property, or an ``fp_text``) that the designer
  moved away from where the emitter would put it by default. Only differences
  are stored, so a line here always records a decision.
* ``segment``, ``via`` — every track and via, by net name.

Not held — anything that can be derived, or that belongs to another source:

* footprint geometry (from ``libraries/footprints``, the vendored library),
* values, fields, pad nets, sheet paths (from the SKiDL circuit),
* UUIDs (deterministic, derived from content by the emitter),
* zone fills (KiCad refills; ``kicad-cli ... --refill-zones`` / ``--check-zones``).

File format
-----------
An s-expression, one item per line, sorted, so a git diff of a layout change
reads as the list of physical changes it is. Lines whose first non-blank
character is ``#`` are comments. Net names are KiCad board names
(``/SENSE+``, ``AGND``). Lengths are millimetres.

::

    (footprint "R12" F 97.215 82.25 0)
    (text "R12" property "Reference" (at 0 -1.43 0) (layer "F.SilkS") ...)
    (segment "/SENSE+" "F.Cu" 0.2 94.54 84.1 96.39 82.25)
    (via "+15VA" 105 79 0.6 0.3 "F.Cu" "B.Cu")

ECO format
----------
An ECO names a base layout and lists operations, each with the reason it is
needed. Removing an item names it exactly; an item that is not in the base is
an error, so an ECO cannot silently stop applying. ::

    (stage1_iv_eco (version 1) (name "bipolar") (base "stage1_iv.layout")
      (remove (segment "AGND" "F.Cu" 0.2 123.84 49.875 124.708456 49.875)
              (reason "U8 pin 5 (AINCOM) leaves AGND"))
      (add (via "VREF_2V5" 117.1 55.9 0.6 0.3 "F.Cu" "B.Cu")
           (reason "..."))
      (add (footprint "R32" F 98.6 80.95 0) (reason "..."))
      (add (graphic (gr_text "FORCE+" (at 86 50 0) (layer "F.SilkS") ...))
           (reason "..."))
    )

``(graphic ...)`` carries a board graphic verbatim, exactly as the layout's
own ``(graphic ...)`` lines do — free silkscreen text and the like.
"""

from __future__ import annotations

import copy
import os
import re
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_REPO_ROOT, os.path.join(_REPO_ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from resolve_kicad_libs import (  # noqa: E402
    parse_sexp, write_sexp, Atom, SexpList,
)
from generators.footprint_xform import (  # noqa: E402
    head, kids, first, fmt, norm180,
)

LAYOUT_VERSION = 1

# The original, unipolar, human-routed KiCad project the base layout is
# captured from, and which tools/reproduce_reference.py and
# validation/routed_board_check.py compare against. Its location is defined
# here and nowhere else.
REFERENCE_DIR = os.path.join(_REPO_ROOT, "unipolar-routed")
REFERENCE_BOARD = os.path.join(REFERENCE_DIR, "diodes.kicad_pcb")
REFERENCE_PROJECT = os.path.join(REFERENCE_DIR, "diodes.kicad_pro")

# Board-level node kinds kept as graphics (verbatim, minus uuid).
GRAPHIC_KINDS = ("gr_rect", "gr_line", "gr_arc", "gr_circle", "gr_poly",
                 "gr_text", "gr_text_box")


class LayoutError(ValueError):
    """A layout or ECO that cannot be applied as written."""


# ---------------------------------------------------------------------------
# small s-expression helpers
# ---------------------------------------------------------------------------

def Q(text):
    return Atom(str(text), 0, True)


def A(text):
    return Atom(str(text), 0, False)


def L(*items):
    return SexpList(items)


def strip(node, drop=("uuid",)):
    """Deep copy of `node` without the named children (at any depth)."""
    if not isinstance(node, list):
        return node
    return SexpList(strip(c, drop) for c in node
                    if not (isinstance(c, list) and head(c) in drop))


def one_line(node):
    """Render an s-expression on a single line (for the layout file)."""
    if not isinstance(node, list):
        return write_sexp(node)
    return "(" + " ".join(one_line(c) for c in node) + ")"


_NUMBER = re.compile(r"-?[0-9]+(\.[0-9]+)?")


def canon(node):
    """Hashable, formatting-independent form of a node (numbers normalised)."""
    if not isinstance(node, list):
        s = str(node)
        return fmt(s) if _NUMBER.fullmatch(s) else s
    return tuple(canon(c) for c in node)


# ---------------------------------------------------------------------------
# items
# ---------------------------------------------------------------------------

class Placement:
    __slots__ = ("ref", "side", "x", "y", "rot")

    def __init__(self, ref, side, x, y, rot):
        self.ref, self.side = str(ref), str(side)
        self.x, self.y, self.rot = float(x), float(y), norm180(float(rot))

    def node(self):
        return L(A("footprint"), Q(self.ref), A(self.side), A(fmt(self.x)),
                 A(fmt(self.y)), A(fmt(self.rot)))

    def key(self):
        return ("footprint", self.ref)


class Segment:
    __slots__ = ("net", "layer", "width", "x1", "y1", "x2", "y2")

    def __init__(self, net, layer, width, x1, y1, x2, y2):
        self.net, self.layer = str(net), str(layer)
        self.width = float(width)
        self.x1, self.y1, self.x2, self.y2 = (float(x1), float(y1),
                                              float(x2), float(y2))

    def node(self):
        return L(A("segment"), Q(self.net), Q(self.layer), A(fmt(self.width)),
                 *[A(fmt(v)) for v in (self.x1, self.y1, self.x2, self.y2)])

    def key(self):
        """Direction-independent identity."""
        a = (fmt(self.x1), fmt(self.y1))
        b = (fmt(self.x2), fmt(self.y2))
        return ("segment", self.net, self.layer, fmt(self.width),
                min(a, b), max(a, b))


class Via:
    __slots__ = ("net", "x", "y", "size", "drill", "layers")

    def __init__(self, net, x, y, size, drill, layers=("F.Cu", "B.Cu")):
        self.net = str(net)
        self.x, self.y = float(x), float(y)
        self.size, self.drill = float(size), float(drill)
        self.layers = tuple(str(v) for v in layers)

    def node(self):
        return L(A("via"), Q(self.net), A(fmt(self.x)), A(fmt(self.y)),
                 A(fmt(self.size)), A(fmt(self.drill)),
                 *[Q(v) for v in self.layers])

    def key(self):
        return ("via", self.net, fmt(self.x), fmt(self.y), fmt(self.size),
                fmt(self.drill), self.layers)


class TextOverride:
    """Where a footprint text sits, when that is not the emitter's default.

    ``kind`` is ``property`` (keyed by field name) or ``fp_text`` (keyed by the
    text itself). ``body`` is the text node's geometry children: ``(at)``,
    ``(layer)``, ``(hide)``, ``(unlocked)``, ``(effects)`` ...
    """
    __slots__ = ("ref", "kind", "name", "body")

    def __init__(self, ref, kind, name, body):
        self.ref, self.kind, self.name = str(ref), str(kind), str(name)
        self.body = [strip(c) for c in body]

    def node(self):
        return L(A("text"), Q(self.ref), A(self.kind), Q(self.name),
                 *self.body)

    def key(self):
        return ("text", self.ref, self.kind, self.name)


class Graphic:
    """A board graphic (``gr_text``, ``gr_line`` ...) added or removed by an ECO.

    Held verbatim, like the layout's own ``(graphic ...)`` lines; identity is
    the whole node, formatting-independent.
    """
    __slots__ = ("body",)

    def __init__(self, body):
        if head(body) not in GRAPHIC_KINDS:
            raise LayoutError(f"not a board graphic: {one_line(body)[:80]}")
        self.body = strip(body)

    def node(self):
        return L(A("graphic"), self.body)

    def key(self):
        return ("graphic", canon(self.body))


class Layout:
    """The physical design: everything except connectivity and geometry."""

    def __init__(self):
        self.source = ""
        self.general = None
        self.paper = None
        self.setup = None
        self.rules = None
        self.graphics = []            # verbatim board graphics, no uuid
        self.zones = []               # zone nodes: (zone "NET" ...), no fill
        self.placements = {}          # ref -> Placement
        self.texts = {}               # key -> TextOverride
        self.segments = []
        self.vias = []

    # -- serialisation --------------------------------------------------
    def lines(self):
        out = [f"(stage1_iv_layout (version {LAYOUT_VERSION})",
               f"  (source {write_sexp(Q(self.source))})"]
        for node in (self.general, self.paper):
            if node is not None:
                out.append("  " + one_line(node))
        if self.rules is not None:
            out.append("  " + one_line(self.rules))
        if self.setup is not None:
            out.append("  " + one_line(self.setup))
        for g in sorted(self.graphics, key=lambda n: one_line(n)):
            out.append("  " + one_line(L(A("graphic"), g)))
        for z in sorted(self.zones, key=lambda n: one_line(n)):
            out.append("  " + one_line(z))
        for ref in sorted(self.placements, key=_ref_sort):
            out.append("  " + one_line(self.placements[ref].node()))
        for key in sorted(self.texts, key=lambda k: (_ref_sort(k[1]),) + k[2:]):
            out.append("  " + one_line(self.texts[key].node()))
        for s in sorted(self.segments, key=_seg_sort):
            out.append("  " + one_line(s.node()))
        for v in sorted(self.vias, key=lambda v: (v.net, v.x, v.y)):
            out.append("  " + one_line(v.node()))
        out.append(")")
        return out

    def write(self, path, header=""):
        text = "\n".join(self.lines()) + "\n"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(header + text)
        return text

    def copy(self):
        return copy.deepcopy(self)

    # -- item bookkeeping ---------------------------------------------------
    def item_index(self):
        idx = {}
        for s in self.segments:
            idx.setdefault(s.key(), []).append(s)
        for v in self.vias:
            idx.setdefault(v.key(), []).append(v)
        for p in self.placements.values():
            idx.setdefault(p.key(), []).append(p)
        for t in self.texts.values():
            idx.setdefault(t.key(), []).append(t)
        return idx


def _ref_sort(ref):
    ref = str(ref)
    prefix = ref.rstrip("0123456789")
    num = ref[len(prefix):]
    return (prefix, int(num) if num else -1)


def _seg_sort(s):
    a, b = sorted([(s.x1, s.y1), (s.x2, s.y2)])
    return (s.net, s.layer, a, b, s.width)


# ---------------------------------------------------------------------------
# parse a layout file / a single item
# ---------------------------------------------------------------------------

def parse_item(node):
    h = head(node)
    if h == "footprint":
        return Placement(node[1], node[2], node[3], node[4], node[5])
    if h == "segment":
        return Segment(*node[1:8])
    if h == "via":
        layers = [str(v) for v in node[6:]] or ["F.Cu", "B.Cu"]
        return Via(node[1], node[2], node[3], node[4], node[5], layers)
    if h == "text":
        return TextOverride(node[1], node[2], node[3], node[4:])
    if h == "graphic":
        return Graphic(node[1])
    raise LayoutError(f"not a layout item: {one_line(node)[:80]}")


def _strip_comments(text):
    """Drop lines whose first non-blank character is '#' (as .kicad_dru does).

    Only whole-line comments: a '#' inside a quoted string is data.
    """
    return "\n".join("" if line.lstrip().startswith("#") else line
                     for line in text.split("\n"))


def _root(path, want):
    with open(path, encoding="utf-8") as fh:
        tree = parse_sexp(_strip_comments(fh.read()))
    for child in tree:
        if head(child) == want:
            return child
    raise LayoutError(f"{path}: no ({want} ...) node")


def load_layout(path):
    root = _root(path, "stage1_iv_layout")
    lay = Layout()
    version = first(root, "version")
    if version is None or int(version[1]) != LAYOUT_VERSION:
        raise LayoutError(f"{path}: layout version {version} is not "
                          f"{LAYOUT_VERSION}")
    for child in root[1:]:
        h = head(child)
        if h == "version":
            continue
        if h == "source":
            lay.source = str(child[1])
        elif h == "general":
            lay.general = child
        elif h == "paper":
            lay.paper = child
        elif h == "setup":
            lay.setup = child
        elif h == "rules":
            lay.rules = child
        elif h == "graphic":
            lay.graphics.append(child[1])
        elif h == "zone":
            lay.zones.append(child)
        elif h in ("footprint", "segment", "via", "text"):
            item = parse_item(child)
            _add(lay, item, path)
        else:
            raise LayoutError(f"{path}: unknown layout item ({h} ...)")
    return lay


def _add(lay, item, where):
    if isinstance(item, Placement):
        if item.ref in lay.placements:
            raise LayoutError(f"{where}: {item.ref} placed twice")
        lay.placements[item.ref] = item
    elif isinstance(item, TextOverride):
        if item.key() in lay.texts:
            raise LayoutError(f"{where}: text {item.key()} given twice")
        lay.texts[item.key()] = item
    elif isinstance(item, Segment):
        lay.segments.append(item)
    elif isinstance(item, Via):
        lay.vias.append(item)
    elif isinstance(item, Graphic):
        if any(canon(g) == item.key()[1] for g in lay.graphics):
            raise LayoutError(f"{where}: graphic given twice: "
                              f"{one_line(item.body)[:80]}")
        lay.graphics.append(item.body)
    else:                                              # pragma: no cover
        raise LayoutError(f"{where}: cannot add {item!r}")


def _remove(lay, item, where):
    key = item.key()
    stale = (f"{where}: remove {one_line(item.node())[:90]}: the base layout "
             f"has something else. The ECO no longer matches the board it "
             f"was written against.")
    if isinstance(item, Placement):
        if item.ref not in lay.placements:
            raise LayoutError(f"{where}: remove {item.ref}: not placed")
        if canon(lay.placements[item.ref].node()) != canon(item.node()):
            raise LayoutError(stale)
        del lay.placements[item.ref]
        return
    if isinstance(item, Graphic):
        for i, g in enumerate(lay.graphics):
            if canon(g) == key[1]:
                del lay.graphics[i]
                return
        raise LayoutError(f"{where}: remove graphic "
                          f"{one_line(item.body)[:90]}: not in the base "
                          f"layout. The ECO no longer matches the board it "
                          f"was written against.")
    if isinstance(item, TextOverride):
        if key not in lay.texts:
            raise LayoutError(f"{where}: remove text {key}: not present")
        if canon(lay.texts[key].node()) != canon(item.node()):
            raise LayoutError(stale)
        del lay.texts[key]
        return
    pool = lay.segments if isinstance(item, Segment) else lay.vias
    for i, existing in enumerate(pool):
        if existing.key() == key:
            del pool[i]
            return
    raise LayoutError(f"{where}: remove {one_line(item.node())}: no such "
                      f"item in the base layout. The ECO no longer matches "
                      f"the board it was written against.")


# ---------------------------------------------------------------------------
# ECO
# ---------------------------------------------------------------------------

class EcoOp:
    __slots__ = ("op", "item", "reason")

    def __init__(self, op, item, reason):
        self.op, self.item, self.reason = op, item, reason


def load_eco(path):
    root = _root(path, "stage1_iv_eco")
    meta = {"name": "", "base": ""}
    ops = []
    for child in root[1:]:
        h = head(child)
        if h in ("version", "name", "base", "description"):
            meta[h] = str(child[1])
            continue
        if h not in ("add", "remove", "move"):
            raise LayoutError(f"{path}: unknown ECO operation ({h} ...)")
        items = [c for c in child[1:] if head(c) != "reason"]
        reason = first(child, "reason")
        if reason is None or not str(reason[1]).strip():
            raise LayoutError(f"{path}: every ECO operation needs a "
                              f"(reason ...): {one_line(child)[:90]}")
        if len(items) != 1:
            raise LayoutError(f"{path}: one item per operation: "
                              f"{one_line(child)[:90]}")
        ops.append(EcoOp(h, parse_item(items[0]), str(reason[1])))
    return meta, ops


def apply_eco(layout, eco_path):
    """Return a new Layout: `layout` with the ECO's operations applied."""
    meta, ops = load_eco(eco_path)
    out = layout.copy()
    for op in ops:
        where = f"{os.path.basename(eco_path)} ({op.reason[:40]})"
        if op.op == "remove":
            _remove(out, op.item, where)
        elif op.op == "add":
            _add(out, op.item, where)
        elif op.op == "move":
            if not isinstance(op.item, Placement):
                raise LayoutError(f"{where}: only footprints can be moved")
            if op.item.ref not in out.placements:
                raise LayoutError(f"{where}: move {op.item.ref}: not placed")
            out.placements[op.item.ref] = op.item
    return out, meta, ops
