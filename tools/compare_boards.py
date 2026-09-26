#!/usr/bin/env python3
"""
compare_boards.py — are two .kicad_pcb files the same physical board?
======================================================================

Compares what a board IS — copper, placement, connectivity, outline,
fabrication setup — not how a file spells it. Independent of the generator:
it reads both files directly and never calls the emitter's transforms, so it
can catch the emitter being wrong.

Normalised away (serialisation only, never a difference):
    UUIDs; item order; number formatting (``119.550000`` == ``119.55``);
    board schema (``(net 5 "AGND")`` and KiCad 10's ``(net "AGND")``); net
    codes; the header's version/generator; zone FILL (derived — compare fills
    by refilling both in KiCad); KiCad 10's defaulted
    ``(duplicate_pad_numbers_are_jumpers no)``.

Compared, per category:
    setup      general/paper/layers/setup (thickness, tenting, plot params)
    graphics   board graphics: Edge.Cuts outline, free silkscreen text
    parts      the set of references
    placement  footprint id, side, position, rotation
    pads       every pad: number, local geometry, layers, net, pin function
    footprint  footprint graphics, texts, 3D models, attributes, schematic
               link (path/sheet), unit metadata
    values     Value and every field
    tracks     every segment: net, layer, width, end points (either direction)
    vias       every via: net, position, size, drill, layers
    zones      pour definitions: net, layers, outline, clearances, thermals

Usage::

    python3 tools/compare_boards.py A.kicad_pcb B.kicad_pcb [--json]

Exit 0 when nothing differs.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from resolve_kicad_libs import parse_sexp  # noqa: E402

_NUMBER = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_IGNORED_FP_CHILDREN = {"uuid", "duplicate_pad_numbers_are_jumpers"}


def head(n):
    return str(n[0]) if isinstance(n, list) and n else None


def kids(n, k):
    return [c for c in n if head(c) == k]


def first(n, k):
    for c in n:
        if head(c) == k:
            return c
    return None


def fmt(v):
    t = f"{float(v):.6f}".rstrip("0").rstrip(".")
    return "0" if t in ("", "-0") else t


def canon(node, drop=("uuid",), sort=False):
    """Hashable canonical form: numbers normalised, `drop` removed."""
    if not isinstance(node, list):
        s = str(node)
        return fmt(s) if _NUMBER.fullmatch(s) else s
    items = [canon(c, drop, sort) for c in node
             if not (isinstance(c, list) and head(c) in drop)]
    if sort and len(items) > 1:
        return (items[0],) + tuple(sorted(items[1:], key=repr))
    return tuple(items)


def _angle(v):
    a = float(v) % 360.0
    return fmt(0 if abs(a - 360) < 1e-9 else a)


class Model:
    """Everything physical and electrical about one board."""

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
            return by_code.get(tok, "") if tok.isdigit() and not \
                getattr(net[1], "quoted", False) else tok

        self.net_of = net_of
        self.setup = {k: canon(first(root, k)) for k in
                      ("general", "paper", "layers", "setup")}
        self.graphics = collections.Counter(
            canon(c) for c in root if head(c) and head(c).startswith("gr_"))
        self.parts = {}
        for fp in kids(root, "footprint"):
            ref = next(str(p[2]) for p in kids(fp, "property")
                       if str(p[1]) == "Reference")
            self.parts[ref] = self._footprint(fp)
        self.tracks = collections.Counter()
        for s in kids(root, "segment"):
            a = (fmt(first(s, "start")[1]), fmt(first(s, "start")[2]))
            b = (fmt(first(s, "end")[1]), fmt(first(s, "end")[2]))
            self.tracks[(net_of(s), str(first(s, "layer")[1]),
                         fmt(first(s, "width")[1]), min(a, b), max(a, b))] += 1
        self.vias = collections.Counter()
        for v in kids(root, "via"):
            at = first(v, "at")
            self.vias[(net_of(v), fmt(at[1]), fmt(at[2]),
                       fmt(first(v, "size")[1]), fmt(first(v, "drill")[1]),
                       tuple(str(x) for x in first(v, "layers")[1:]))] += 1
        self.zones = collections.Counter()
        for z in kids(root, "zone"):
            body = canon([c for c in z if not (isinstance(c, list) and head(c)
                          in ("net", "net_name", "uuid", "filled_polygon",
                              "fill_segments"))])
            self.zones[(net_of(z) or str(first(z, "net_name")[1]), body)] += 1
        self.nets = set()
        for part in self.parts.values():
            self.nets |= {n for n, _f, _t in part["pads"].values() if n}
        self.nets |= {t[0] for t in self.tracks} | {v[0] for v in self.vias}

    def _footprint(self, fp):
        at = first(fp, "at")
        props, fields, texts = {}, {}, collections.Counter()
        for p in kids(fp, "property"):
            body = canon([c for c in p[3:] if isinstance(c, list)])
            props[str(p[1])] = (str(p[2]), body)
        for t in kids(fp, "fp_text"):
            texts[canon(t)] += 1
        pads = {}
        pad_geom = collections.Counter()
        for pad in kids(fp, "pad"):
            fn = first(pad, "pinfunction")
            pt = first(pad, "pintype")
            pads.setdefault(str(pad[1]), (self.net_of(pad),
                                          str(fn[1]) if fn else "",
                                          str(pt[1]) if pt else ""))
            pad_geom[canon(pad, drop=("uuid", "net", "pinfunction",
                                      "pintype"))] += 1
        graphics = collections.Counter(
            canon(c) for c in fp if head(c) and head(c).startswith("fp_")
            and head(c) != "fp_text")
        other = collections.Counter(
            canon(c) for c in fp if isinstance(c, list) and head(c) not in
            _IGNORED_FP_CHILDREN | {"at", "layer", "property", "fp_text",
                                    "pad"} and not head(c).startswith("fp_"))
        return {
            "fpid": str(fp[1]),
            "side": str(first(fp, "layer")[1]),
            "at": (fmt(at[1]), fmt(at[2]),
                   _angle(at[3]) if len(at) > 3 else "0"),
            "value": props.get("Value", ("", None))[0],
            "props": props, "texts": texts, "pads": pads,
            "pad_geometry": pad_geom, "graphics": graphics, "other": other,
        }


def _counter_diff(a, b):
    only_a = a - b
    only_b = b - a
    return list(only_a.elements()), list(only_b.elements())


def compare(a_path, b_path):
    """{category: [difference strings]} — empty dict means same board."""
    A, B = Model(a_path), Model(b_path)
    diffs = collections.defaultdict(list)
    for k in A.setup:
        if A.setup[k] != B.setup[k]:
            diffs["setup"].append(f"({k} ...) differs")
    ga, gb = _counter_diff(A.graphics, B.graphics)
    diffs["graphics"] += [f"only in A: {g}" for g in ga] + \
                         [f"only in B: {g}" for g in gb]
    for ref in sorted(set(A.parts) - set(B.parts)):
        diffs["parts"].append(f"{ref} only in A")
    for ref in sorted(set(B.parts) - set(A.parts)):
        diffs["parts"].append(f"{ref} only in B")
    for ref in sorted(set(A.parts) & set(B.parts)):
        pa, pb = A.parts[ref], B.parts[ref]
        for key in ("fpid", "side", "at"):
            if pa[key] != pb[key]:
                diffs["placement"].append(f"{ref} {key}: {pa[key]} -> "
                                          f"{pb[key]}")
        if pa["pad_geometry"] != pb["pad_geometry"]:
            diffs["pads"].append(f"{ref}: pad geometry differs")
        for num in sorted(set(pa["pads"]) | set(pb["pads"])):
            if pa["pads"].get(num) != pb["pads"].get(num):
                diffs["pads"].append(f"{ref}.{num}: {pa['pads'].get(num)} -> "
                                     f"{pb['pads'].get(num)}")
        if pa["graphics"] != pb["graphics"]:
            diffs["footprint"].append(f"{ref}: footprint graphics differ")
        if pa["texts"] != pb["texts"]:
            diffs["footprint"].append(f"{ref}: fp_text differs")
        oa, ob = _counter_diff(pa["other"], pb["other"])
        for o in oa:
            diffs["footprint"].append(f"{ref}: only in A: {str(o)[:140]}")
        for o in ob:
            diffs["footprint"].append(f"{ref}: only in B: {str(o)[:140]}")
        for name in sorted(set(pa["props"]) | set(pb["props"])):
            va, vb = pa["props"].get(name), pb["props"].get(name)
            if va is None or vb is None:
                diffs["values"].append(f"{ref} field {name!r}: "
                                       f"{'missing in A' if va is None else 'missing in B'}")
                continue
            if va[0] != vb[0]:
                diffs["values"].append(f"{ref} {name}: {va[0]!r} -> {vb[0]!r}")
            if va[1] != vb[1]:
                diffs["footprint"].append(f"{ref} {name} text placement: "
                                          f"{va[1]} -> {vb[1]}")
    for cat, ca, cb in (("tracks", A.tracks, B.tracks),
                        ("vias", A.vias, B.vias),
                        ("zones", A.zones, B.zones)):
        xa, xb = _counter_diff(ca, cb)
        diffs[cat] += [f"only in A: {x}" for x in xa] + \
                      [f"only in B: {x}" for x in xb]
    return {k: v for k, v in diffs.items() if v}, A, B


def summary(A):
    return {"parts": len(A.parts), "tracks": sum(A.tracks.values()),
            "vias": sum(A.vias.values()), "zones": sum(A.zones.values()),
            "nets": len(A.nets), "graphics": sum(A.graphics.values())}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=25)
    args = ap.parse_args(argv)
    diffs, A, B = compare(args.a, args.b)
    if args.json:
        print(json.dumps({"a": summary(A), "b": summary(B), "diffs": diffs},
                         indent=1))
        return 1 if diffs else 0
    print(f"A {args.a}: {summary(A)}")
    print(f"B {args.b}: {summary(B)}")
    if not diffs:
        print("SAME BOARD: no physical or electrical difference")
        return 0
    for cat, items in diffs.items():
        print(f"{cat}: {len(items)} difference(s)")
        for item in items[:args.limit]:
            print(f"    {item}")
        if len(items) > args.limit:
            print(f"    ... {len(items) - args.limit} more")
    return 1


if __name__ == "__main__":
    sys.exit(main())
