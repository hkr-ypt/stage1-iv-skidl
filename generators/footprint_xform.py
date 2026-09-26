"""
footprint_xform.py — place a library footprint the way KiCad stores it.
========================================================================

A footprint inside a ``.kicad_pcb`` is not the library file with an ``(at)``
added. KiCad stores a placed footprint in a specific, partly-local form, and a
board that stores it any other way is a different board to KiCad's footprint
comparator and to anyone diffing the file. The rules below were derived from,
and are checked against, every one of the 113 footprints on the human-routed
board in ``routed/diodes.kicad_pcb`` (``tools/reproduce_reference.py``):

``(at X Y R)`` on the footprint
    ``R`` in (-180, 180], omitted when 0.

Graphics (``fp_line``, ``fp_rect``, ``fp_arc``, ``fp_circle``, ``fp_poly``)
    Coordinates stay in the footprint's own, UNROTATED frame. Rotation is not
    applied to them at all.

Pads ``(at x y a)``
    Position in the unrotated local frame; the angle is ABSOLUTE:
    library pad angle + R, in [0, 360), omitted when 0.

Text (``property``, ``fp_text``) ``(at x y a)``
    Position in the unrotated local frame; the angle is absolute and kept
    upright: library angle + R, and a text that would read upside down
    (angle in (90, 270]) is turned by 180. Always written.

Bottom side (``B.Cu``) — KiCad flips top-to-bottom
    Every local y is negated (pads, graphics, text), every angle is negated
    before R is added, every ``F.*`` layer becomes ``B.*`` and vice versa, and
    text gains ``(justify mirror)``.

Nothing here reads or needs KiCad.
"""

from __future__ import annotations

import copy

GRAPHICS = ("fp_line", "fp_rect", "fp_arc", "fp_circle", "fp_poly",
            "fp_curve")
TEXTS = ("property", "fp_text")


def head(node):
    return str(node[0]) if isinstance(node, list) and node else None


def kids(node, name):
    return [c for c in node if head(c) == name]


def first(node, name):
    for c in node:
        if head(c) == name:
            return c
    return None


def fmt(value):
    """A length or angle the way KiCad writes it: no trailing zeros, no -0."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def norm360(angle):
    a = float(angle) % 360.0
    return 0.0 if abs(a - 360.0) < 1e-9 or abs(a) < 1e-9 else a


def norm180(angle):
    """KiCad's footprint orientation range, (-180, 180]."""
    a = norm360(angle)
    return a - 360.0 if a > 180.0 else a


def keep_upright(angle):
    a = norm360(angle)
    if 90.0 < a <= 270.0:
        a -= 180.0
    return norm360(a)


def flip_layer(name):
    s = str(name)
    if s.startswith("F."):
        return "B." + s[2:]
    if s.startswith("B."):
        return "F." + s[2:]
    return s


def _atom_like(template, text, quoted=None):
    """A token of the same class, and quoting, as `template`.

    parse_sexp's Atom remembers whether it was quoted; a layer name must stay
    a quoted string and a number must stay bare.
    """
    cls = type(template)
    if quoted is None:
        quoted = bool(getattr(template, "quoted", False))
    try:
        return cls(text, 0, quoted)
    except TypeError:
        return text


def _set_num(node, index, value):
    node[index] = _atom_like(node[index], fmt(value))


def _negate_y(point):
    if point is not None and len(point) > 2:
        _set_num(point, 2, -float(point[2]))


def _flip_layers(node):
    for key in ("layer", "layers"):
        for lay in kids(node, key):
            for i in range(1, len(lay)):
                lay[i] = _atom_like(lay[i], flip_layer(lay[i]))


def _set_angle(at, angle, always):
    template = at[1]
    del at[3:]
    if always or angle:
        at.append(_atom_like(template, fmt(angle)))


def place_graphic(node, bottom):
    if not bottom:
        return
    for key in ("start", "end", "mid", "center"):
        for p in kids(node, key):
            _negate_y(p)
    for pts in kids(node, "pts"):
        for p in pts[1:]:
            if head(p) == "xy":
                _negate_y(p)
    _flip_layers(node)


def place_pad(node, rot, bottom):
    at = first(node, "at")
    local = float(at[3]) if len(at) > 3 else 0.0
    if bottom:
        _negate_y(at)
        local = -local
    _set_angle(at, norm360(local + rot), always=False)
    if bottom:
        _flip_layers(node)
        # Custom-shape pad primitives are local geometry like any graphic.
        for prims in kids(node, "primitives"):
            for prim in prims[1:]:
                if isinstance(prim, list):
                    place_graphic(prim, True)


def place_text(node, rot, bottom):
    at = first(node, "at")
    local = float(at[3]) if len(at) > 3 else 0.0
    if bottom:
        _negate_y(at)
        local = -local
    _set_angle(at, keep_upright(local + rot), always=True)
    if bottom:
        _flip_layers(node)
        effects = first(node, "effects")
        if effects is not None:
            justify = first(effects, "justify")
            if justify is None:
                effects.append(type(node)(
                    [_atom_like(node[0], "justify"),
                     _atom_like(node[0], "mirror")]))
            elif "mirror" not in [str(j) for j in justify[1:]]:
                justify.append(_atom_like(justify[0], "mirror"))


def place_footprint(node, x, y, rot, side):
    """Move a library footprint node onto the board, in place.

    ``node`` is a ``(footprint ...)`` list as loaded from a ``.kicad_mod``
    (with its ``(layer "F.Cu")``); ``side`` is ``"F"`` or ``"B"``.
    """
    if side not in ("F", "B"):
        raise ValueError(f"side must be 'F' or 'B', not {side!r}")
    bottom = side == "B"
    rot = norm180(rot)
    for child in node:
        h = head(child)
        if h in GRAPHICS:
            place_graphic(child, bottom)
        elif h == "pad":
            place_pad(child, rot, bottom)
        elif h in TEXTS:
            place_text(child, rot, bottom)
    lay = first(node, "layer")
    if lay is not None:
        lay[1] = _atom_like(lay[1], "B.Cu" if bottom else "F.Cu")
    at = [_atom_like(node[0], "at"), _atom_like(node[0], fmt(x)),
          _atom_like(node[0], fmt(y))]
    if rot:
        at.append(_atom_like(node[0], fmt(rot)))
    at = type(node)(at)
    for i, child in enumerate(node):
        if head(child) == "at":
            node[i] = at
            break
    else:
        # KiCad writes (at) right after (layer) and (uuid).
        pos = 2
        for i, child in enumerate(node):
            if head(child) in ("layer", "uuid"):
                pos = i + 1
        node.insert(pos, at)
    return node


def placed_copy(node, x, y, rot, side):
    return place_footprint(copy.deepcopy(node), x, y, rot, side)
