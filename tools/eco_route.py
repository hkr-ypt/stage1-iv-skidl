#!/usr/bin/env python3
"""
eco_route.py — find a clearance-correct path for ONE new connection.
=====================================================================

This is not an autorouter for the board, and nothing calls it at build time.
It is the instrument used to design the few tracks an ECO adds, against the
real copper of the real board: a two-layer A* search on a fine grid, where
every existing item of another net is an obstacle inflated by the netclass
clearance the repository asks for (the larger of the two nets' classes,
libraries/netclasses.py), every drill by the hole clearance, and the board
edge by the copper-edge clearance. Same-net copper is the goal.

Its output — segments and vias — is reviewed and copied into layout/*.eco
with the reason for each, and then the generated board is checked by KiCad's
DRC. The route is a proposal; DRC is the judge.

Usage (as a library)::

    from board_geometry import Board
    from eco_route import Router
    board = Board("stage1_iv.kicad_pcb")
    start = [(i.layer, i.geom) for i in board.pads[("U7", "5")]]
    goal = [(i.layer, i.geom) for i in board.items
            if i.net == "VREF_2V5" and i.ref != "U7"]
    r = Router(board, net="VREF_2V5", width=0.25)
    steps = r.route(start, goal, window=(100, 66, 122, 88), via_cost=2.0)
    # [("seg", "B.Cu", (x1, y1), (x2, y2)), ("via", (x, y)), ...] or None
"""

from __future__ import annotations

import heapq
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import shapely  # noqa: E402
from shapely.geometry import LineString, Point  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from libraries.netclasses import NET_TO_CLASS, NETCLASSES  # noqa: E402

LAYERS = ("F.Cu", "B.Cu")
HOLE_CLEARANCE = 0.25        # as-built min_hole_clearance
EDGE_CLEARANCE = 0.5         # as-built min_copper_edge_clearance


def net_clearance(net):
    """Policy clearance of a board net name (``/voltage_sense/X`` -> X)."""
    if not net:
        return 0.2
    name = net.split("/")[-1]
    cls = NET_TO_CLASS.get(name) or NET_TO_CLASS.get(net)
    return NETCLASSES[cls]["clearance"] if cls else 0.2


class Router:
    def __init__(self, board, net, width, via_size=0.6, via_drill=0.3,
                 grid=0.05, extra_items=(), clearance=None):
        self.board, self.net, self.width = board, net, width
        self.via_size, self.via_drill, self.grid = via_size, via_drill, grid
        self.own = clearance if clearance is not None else net_clearance(net)
        self.extra = list(extra_items)          # [(net, layer, geom)]

    def _obstacles(self, layer, inflate):
        geoms = []
        for it in self.board.items:
            if it.layer != layer or it.net == self.net:
                continue
            need = max(self.own, net_clearance(it.net))
            geoms.append(it.geom.buffer(need + inflate, quad_segs=4))
        for net, lay, g in self.extra:
            if lay == layer and net != self.net:
                need = max(self.own, net_clearance(net))
                geoms.append(g.buffer(need + inflate, quad_segs=4))
        return unary_union(geoms)

    def _drills(self, inflate):
        return unary_union([d.buffer(HOLE_CLEARANCE + inflate, quad_segs=4)
                            for d, _ in self.board.drills])

    def route(self, start, goal, window, via_cost=2.0, layer_cost=None):
        """A* from `start` to `goal`, each a list of (layer, geometry).

        `window` = (x0, y0, x1, y1) limits the search. Returns a list of
        ("seg", layer, (x1, y1), (x2, y2)) and ("via", (x, y)) steps, or None.
        """
        x0, y0, x1, y1 = window
        g = self.grid
        xs = np.arange(x0, x1 + g / 2, g)
        ys = np.arange(y0, y1 + g / 2, g)
        X, Y = np.meshgrid(xs, ys, indexing="ij")
        half = self.width / 2
        edge_ok = shapely.contains_xy(
            self.board.outline.buffer(-(EDGE_CLEARANCE + half)), X, Y)
        free, start_m, goal_m = {}, {}, {}
        drills = self._drills(half)
        for lay in LAYERS:
            obst = self._obstacles(lay, half)
            f = edge_ok & ~shapely.contains_xy(obst, X, Y) & \
                ~shapely.contains_xy(drills, X, Y)
            free[lay] = f
            s = np.zeros_like(f)
            t = np.zeros_like(f)
            for sl, sg in start:
                if sl == lay:
                    s |= shapely.contains_xy(sg, X, Y)
            for gl, gg in goal:
                if gl == lay:
                    t |= shapely.contains_xy(gg, X, Y)
            start_m[lay], goal_m[lay] = s, t
        via_r = self.via_size / 2
        via_ok = edge_ok & ~shapely.contains_xy(
            self._drills(via_r), X, Y)
        for lay in LAYERS:
            via_ok &= ~shapely.contains_xy(self._obstacles(lay, via_r), X, Y)

        nx, ny = X.shape
        lidx = {lay: i for i, lay in enumerate(LAYERS)}
        goals = {(lidx[lay], i, j) for lay in LAYERS
                 for i, j in zip(*np.nonzero(goal_m[lay]))}
        if not goals:
            return None
        gpts = np.array([(xs[i], ys[j]) for _l, i, j in goals])

        def h(i, j):
            d = np.hypot(gpts[:, 0] - xs[i], gpts[:, 1] - ys[j]).min()
            return d

        openq, came, cost = [], {}, {}
        for lay in LAYERS:
            for i, j in zip(*np.nonzero(start_m[lay])):
                node = (lidx[lay], i, j)
                cost[node] = 0.0
                heapq.heappush(openq, (h(i, j), 0.0, node))
        steps = [(1, 0), (-1, 0), (0, 1), (0, -1),
                 (1, 1), (1, -1), (-1, 1), (-1, -1)]
        lc = layer_cost or {}
        best = None
        while openq:
            _f, c, node = heapq.heappop(openq)
            if c > cost.get(node, 1e18):
                continue
            if node in goals:
                best = node
                break
            li, i, j = node
            lay = LAYERS[li]
            for di, dj in steps:
                a, b = i + di, j + dj
                if not (0 <= a < nx and 0 <= b < ny):
                    continue
                if not free[lay][a, b] and (li, a, b) not in goals:
                    continue
                step = g * (math.sqrt(2) if di and dj else 1.0) * \
                    lc.get(lay, 1.0)
                # prefer to keep going the same way (fewer corners)
                prev = came.get(node)
                if prev is not None and prev[0] == li and \
                        (i - prev[1], j - prev[2]) != (di, dj):
                    step += g * 0.5
                nn = (li, a, b)
                nc = c + step
                if nc < cost.get(nn, 1e18):
                    cost[nn] = nc
                    came[nn] = node
                    heapq.heappush(openq, (nc + h(a, b), nc, nn))
            if via_ok[i, j]:
                other = 1 - li
                nn = (other, i, j)
                nc = c + via_cost
                if (free[LAYERS[other]][i, j] or nn in goals) and \
                        nc < cost.get(nn, 1e18):
                    cost[nn] = nc
                    came[nn] = node
                    heapq.heappush(openq, (nc + h(i, j), nc, nn))
        if best is None:
            return None
        path = [best]
        while path[-1] in came:
            path.append(came[path[-1]])
        path.reverse()
        return self._simplify(path, xs, ys)

    @staticmethod
    def _simplify(path, xs, ys):
        out = []
        run = [path[0]]
        for node in path[1:]:
            if node[0] != run[-1][0]:                    # layer change: via
                if len(run) > 1:
                    out.append(("seg", LAYERS[run[0][0]],
                                (xs[run[0][1]], ys[run[0][2]]),
                                (xs[run[-1][1]], ys[run[-1][2]])))
                out.append(("via", (xs[node[1]], ys[node[2]])))
                run = [node]
                continue
            if len(run) >= 2:
                d0 = (run[-1][1] - run[-2][1], run[-1][2] - run[-2][2])
                d1 = (node[1] - run[-1][1], node[2] - run[-1][2])
                if d0 != d1:
                    out.append(("seg", LAYERS[run[0][0]],
                                (xs[run[0][1]], ys[run[0][2]]),
                                (xs[run[-1][1]], ys[run[-1][2]])))
                    run = [run[-1]]
            run.append(node)
        if len(run) > 1:
            out.append(("seg", LAYERS[run[0][0]],
                        (xs[run[0][1]], ys[run[0][2]]),
                        (xs[run[-1][1]], ys[run[-1][2]])))
        return [(k, *[tuple(round(float(v), 4) for v in p) if isinstance(p, tuple)
                      else p for p in rest]) for k, *rest in out]
