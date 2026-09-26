#!/usr/bin/env python3
"""
board_geometry.py — the copper and courtyards of a .kicad_pcb, as geometry.
===========================================================================

Reads a board file (either schema) into shapely geometry in absolute board
coordinates, so that placement and routing decisions for an ECO are made
against the real board — actual pad shapes, actual tracks, drills, courtyards
and outline — not against a netlist. Used by tools/eco_route.py and by the
ECO review in docs/bipolar_measurement.md; needs ``shapely``.

Conventions (checked against the routed board, where every track end lands
on its pad): a footprint at (X, Y, R) maps a stored local point (x, y) to
    X + x cos R + y sin R,   Y - x sin R + y cos R
and a pad's stored angle is already absolute.
"""

from __future__ import annotations

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shapely.geometry import (  # noqa: E402
    Point, LineString, Polygon, box, MultiPolygon,
)
from shapely.ops import unary_union, polygonize  # noqa: E402
from shapely import affinity  # noqa: E402

from resolve_kicad_libs import parse_sexp  # noqa: E402

COPPER = ("F.Cu", "B.Cu")


def head(n):
    return str(n[0]) if isinstance(n, list) and n else None


def kids(n, k):
    return [c for c in n if head(c) == k]


def first(n, k):
    for c in n:
        if head(c) == k:
            return c
    return None


def to_abs(fx, fy, rot, x, y):
    r = math.radians(rot)
    return (fx + x * math.cos(r) + y * math.sin(r),
            fy - x * math.sin(r) + y * math.cos(r))


class Item:
    __slots__ = ("kind", "ref", "pad", "net", "layer", "geom", "extra")

    def __init__(self, kind, net, layer, geom, ref="", pad="", extra=None):
        self.kind, self.net, self.layer, self.geom = kind, net, layer, geom
        self.ref, self.pad, self.extra = ref, pad, extra or {}

    def __repr__(self):
        where = f"{self.ref}.{self.pad}" if self.ref else self.kind
        return f"<{where} {self.net} {self.layer}>"


def _pad_shape(pad):
    shape = str(pad[3])
    size = first(pad, "size")
    w, h = float(size[1]), float(size[2] if len(size) > 2 else size[1])
    if shape == "circle":
        g = Point(0, 0).buffer(w / 2, quad_segs=16)
    elif shape == "oval":
        r = min(w, h) / 2
        dx, dy = (w / 2 - r, 0) if w >= h else (0, h / 2 - r)
        g = LineString([(-dx, -dy), (dx, dy)]).buffer(r, quad_segs=16) \
            if (dx or dy) else Point(0, 0).buffer(r, quad_segs=16)
    elif shape == "roundrect":
        rr = first(pad, "roundrect_rratio")
        ratio = float(rr[1]) if rr is not None else 0.25
        r = min(w, h) * ratio
        g = box(-w / 2 + r, -h / 2 + r, w / 2 - r, h / 2 - r).buffer(
            r, quad_segs=8) if r > 0 else box(-w / 2, -h / 2, w / 2, h / 2)
    else:                       # rect, trapezoid, custom: bounding rectangle
        g = box(-w / 2, -h / 2, w / 2, h / 2)
    return g


class Board:
    def __init__(self, path):
        with open(path, encoding="utf-8") as fh:
            root = next(c for c in parse_sexp(fh.read())
                        if head(c) == "kicad_pcb")
        self.path = path
        by_code = {str(n[1]): str(n[2]) for n in kids(root, "net")
                   if len(n) > 2}

        def net_of(node):
            net = first(node, "net")
            if net is None or len(net) < 2:
                return ""
            if len(net) > 2:
                return str(net[2])
            tok = str(net[1])
            return by_code.get(tok, tok) if tok.isdigit() else tok

        self.items = []            # copper: pads, tracks, vias
        self.drills = []           # (Point geometry, ref/kind)
        self.courtyards = {}       # ref -> (side, polygon)
        self.pads = {}             # (ref, pad) -> [Item...]
        self.footprints = {}       # ref -> dict
        for fp in kids(root, "footprint"):
            ref = next(str(p[2]) for p in kids(fp, "property")
                       if str(p[1]) == "Reference")
            at = first(fp, "at")
            fx, fy = float(at[1]), float(at[2])
            rot = float(at[3]) if len(at) > 3 else 0.0
            side = "B" if str(first(fp, "layer")[1]) == "B.Cu" else "F"
            self.footprints[ref] = {"at": (fx, fy, rot), "side": side,
                                    "fpid": str(fp[1])}
            for pad in kids(fp, "pad"):
                pat = first(pad, "at")
                cx, cy = to_abs(fx, fy, rot, float(pat[1]), float(pat[2]))
                ang = float(pat[3]) if len(pat) > 3 else 0.0
                g = affinity.rotate(_pad_shape(pad), -ang, origin=(0, 0))
                g = affinity.translate(g, cx, cy)
                layers = [str(x) for x in first(pad, "layers")[1:]]
                cu = [l for l in COPPER if l in layers or "*.Cu" in layers]
                net = net_of(pad)
                for layer in cu:
                    it = Item("pad", net, layer, g, ref, str(pad[1]),
                              {"center": (cx, cy)})
                    self.items.append(it)
                    self.pads.setdefault((ref, str(pad[1])), []).append(it)
                drill = first(pad, "drill")
                if drill is not None and len(drill) > 1:
                    try:
                        d = float(drill[1] if str(drill[1]) != "oval"
                                  else drill[2])
                        self.drills.append((Point(cx, cy).buffer(d / 2), ref))
                    except ValueError:
                        pass
            self.courtyards[ref] = (side, self._courtyard(fp, fx, fy, rot))
        for s in kids(root, "segment"):
            a, b = first(s, "start"), first(s, "end")
            w = float(first(s, "width")[1])
            line = LineString([(float(a[1]), float(a[2])),
                               (float(b[1]), float(b[2]))])
            g = line.buffer(w / 2, quad_segs=8) if line.length else \
                Point(float(a[1]), float(a[2])).buffer(w / 2, quad_segs=8)
            self.items.append(Item("track", net_of(s),
                                   str(first(s, "layer")[1]), g,
                                   extra={"line": line, "width": w}))
        for v in kids(root, "via"):
            at = first(v, "at")
            c = Point(float(at[1]), float(at[2]))
            size = float(first(v, "size")[1])
            drill = float(first(v, "drill")[1])
            for layer in COPPER:
                self.items.append(Item("via", net_of(v), layer,
                                       c.buffer(size / 2, quad_segs=12),
                                       extra={"center": (c.x, c.y)}))
            self.drills.append((c.buffer(drill / 2), "via"))
        rect = next((g for g in kids(root, "gr_rect")
                     if str(first(g, "layer")[1]) == "Edge.Cuts"), None)
        s, e = first(rect, "start"), first(rect, "end")
        self.outline = box(float(s[1]), float(s[2]), float(e[1]),
                           float(e[2]))

    @staticmethod
    def _courtyard(fp, fx, fy, rot):
        polys, lines = [], []
        for g in fp:
            h = head(g)
            lay = first(g, "layer") if isinstance(g, list) else None
            if lay is None or str(lay[1]) not in ("F.CrtYd", "B.CrtYd"):
                continue
            if h == "fp_rect":
                s, e = first(g, "start"), first(g, "end")
                pts = [(float(s[1]), float(s[2])), (float(e[1]), float(s[2])),
                       (float(e[1]), float(e[2])), (float(s[1]), float(e[2]))]
                polys.append(Polygon([to_abs(fx, fy, rot, *p) for p in pts]))
            elif h == "fp_circle":
                c, e = first(g, "center"), first(g, "end")
                r = math.hypot(float(e[1]) - float(c[1]),
                               float(e[2]) - float(c[2]))
                polys.append(Point(*to_abs(fx, fy, rot, float(c[1]),
                                           float(c[2]))).buffer(r))
            elif h == "fp_poly":
                pts = [to_abs(fx, fy, rot, float(p[1]), float(p[2]))
                       for p in first(g, "pts")[1:] if head(p) == "xy"]
                polys.append(Polygon(pts))
            elif h in ("fp_line", "fp_arc"):
                s, e = first(g, "start"), first(g, "end")
                lines.append(LineString([
                    to_abs(fx, fy, rot, float(s[1]), float(s[2])),
                    to_abs(fx, fy, rot, float(e[1]), float(e[2]))]))
        if lines:
            polys.extend(polygonize(unary_union(lines)))
        return unary_union(polys) if polys else None

    # -- queries -----------------------------------------------------------
    def copper(self, layer, exclude_net=None):
        return [i for i in self.items if i.layer == layer and
                (exclude_net is None or i.net != exclude_net)]

    def pad(self, ref, num, layer=None):
        items = self.pads[(ref, str(num))]
        if layer:
            items = [i for i in items if i.layer == layer]
        return items[0]


if __name__ == "__main__":                                  # pragma: no cover
    from generators.layout import REFERENCE_BOARD
    b = Board(sys.argv[1] if len(sys.argv) > 1 else REFERENCE_BOARD)
    print(f"{len(b.items)} copper items, {len(b.drills)} drills, "
          f"{len(b.courtyards)} courtyards, outline {b.outline.bounds}")
