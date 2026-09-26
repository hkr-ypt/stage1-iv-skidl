#!/usr/bin/env python3
"""
capture_layout.py — turn a hand-routed KiCad board into layout source.
=======================================================================

A person lays a board out in KiCad. This reads the saved ``.kicad_pcb`` (and
the ``.kicad_pro`` beside it, for the design rules it was drawn to) and writes
the **layout source** (``layout/*.layout``, format in generators/layout.py):
placement, the text positions that differ from the defaults, tracks, vias,
zone definitions, outline and fabrication setup.

It stores decisions, not derivations. Footprint geometry is left to the
vendored library, values and nets to the SKiDL circuit, UUIDs to the emitter,
zone fill to KiCad. A footprint text is recorded only if the designer moved it
from where generators/routed_pcb.py would put it anyway.

The capture is only worth anything if nothing is lost, so it is checked, not
trusted: ``tools/reproduce_reference.py`` feeds the captured layout back through
the emitter and compares the result with the board it came from.

Usage::

    python3 tools/capture_layout.py unipolar-routed/diodes.kicad_pcb layout/stage1_iv.layout
    python3 tools/capture_layout.py unipolar-routed/diodes.kicad_pcb --check layout/stage1_iv.layout
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from resolve_kicad_libs import KicadLibraries, parse_sexp  # noqa: E402
from generators.footprint_xform import (  # noqa: E402
    head, kids, first, place_footprint,
)
from generators.layout import (  # noqa: E402
    Layout, Placement, Segment, Via, TextOverride, strip, canon, A, Q, L,
    GRAPHIC_KINDS, load_layout,
)
from generators import pcb as _pcb  # noqa: E402
from generators.routed_pcb import field_default_body  # noqa: E402

HEADER = """\
# stage1_iv.layout — PHYSICAL DESIGN SOURCE (placement, copper, outline).
#
# Captured from the human-routed board by tools/capture_layout.py; format in
# generators/layout.py. Change the layout in KiCad, then re-capture - or, for
# a reviewed change against this baseline, write an ECO (layout/*.eco).
# generators/routed_pcb.py combines this file with the SKiDL circuit to write
# stage1_iv.kicad_pcb. tools/reproduce_reference.py proves this file
# reproduces the board it was captured from.
"""

# design_settings keys that say what a layout was drawn to.
_RULE_KEYS = ("min_clearance", "min_track_width", "min_via_diameter",
              "min_via_annular_width", "min_through_hole_diameter",
              "min_hole_clearance", "min_hole_to_hole",
              "min_copper_edge_clearance", "min_connection",
              "min_silk_clearance", "min_text_height", "min_text_thickness")
_CLASS_KEYS = ("clearance", "track_width", "via_diameter", "via_drill")


def _board_root(path):
    with open(path, encoding="utf-8") as fh:
        return next(c for c in parse_sexp(fh.read()) if head(c) == "kicad_pcb")


def _num(node, key):
    c = first(node, key)
    return float(c[1]) if c is not None else None


def capture_rules(pro_path):
    """(rules ...) node: the netclasses and board minimums in the project."""
    with open(pro_path, encoding="utf-8") as fh:
        pro = json.load(fh)
    rules = pro["board"]["design_settings"]["rules"]
    node = [A("rules"), L(A("source"), Q(os.path.relpath(pro_path, ROOT)))]
    for key in _RULE_KEYS:
        if key in rules:
            node.append(L(A(key), A(_pcb.num(rules[key]))))
    for cls in pro["net_settings"]["classes"]:
        body = [A("netclass"), Q(cls["name"])]
        for key in _CLASS_KEYS:
            body.append(L(A(key), _pcb.num(cls[key])))
        node.append(L(*body))
    return L(*node)


def _net_name(node):
    """Net name in either schema: (net "AGND") or (net 5 "AGND")."""
    net = first(node, "net")
    if net is None or len(net) < 2:
        return ""
    return str(net[-1]) if len(net) > 2 or not str(net[1]).isdigit() \
        else str(net[1])


def _net_names_by_code(root):
    return {str(n[1]): str(n[2]) for n in kids(root, "net") if len(n) > 2}


def _zone(zone, by_code):
    net = first(zone, "net")
    name = str(net[1]) if len(net) == 2 and not str(net[1]).isdigit() \
        else by_code.get(str(net[1]), str(first(zone, "net_name")[1]))
    out = [A("zone"), L(A("net"), Q(name))]
    for child in zone[1:]:
        if head(child) in ("net", "net_name", "uuid", "filled_polygon",
                           "fill_segments"):
            continue
        out.append(strip(child))
    return L(*out)


def _text_body(node):
    """Geometry children of a text node: everything but name, value, uuid."""
    return [strip(c) for c in node[3:] if isinstance(c, list)
            and head(c) != "uuid"]


def _default_texts(libs, fp, ref, rot, side):
    """What routed_pcb would emit for this footprint's texts, by key."""
    node, _src = _pcb.load_footprint(libs, str(fp[1]))
    lib_names = {str(p[1]) for p in kids(node, "property")}
    for p in kids(fp, "property"):
        name = str(p[1])
        if name not in lib_names:
            node.append(L(A("property"), Q(name), Q(""),
                          *field_default_body(name)))
    place_footprint(node, 0, 0, rot, side)
    out = {}
    for p in kids(node, "property"):
        out[("property", str(p[1]))] = _text_body(p)
    for t in kids(node, "fp_text"):
        out[("fp_text", str(t[2]))] = _text_body(t)
    return out


def capture(board_path, pro_path=None):
    root = _board_root(board_path)
    by_code = _net_names_by_code(root)
    libs = KicadLibraries()
    lay = Layout()
    lay.source = os.path.relpath(board_path, ROOT)
    lay.general = strip(first(root, "general"))
    lay.paper = strip(first(root, "paper"))
    lay.setup = strip(first(root, "setup"))
    if pro_path and os.path.isfile(pro_path):
        lay.rules = capture_rules(pro_path)

    for child in root:
        if head(child) in GRAPHIC_KINDS:
            lay.graphics.append(strip(child))
        elif head(child) == "zone":
            lay.zones.append(_zone(child, by_code))

    for fp in kids(root, "footprint"):
        ref = next(str(p[2]) for p in kids(fp, "property")
                   if str(p[1]) == "Reference")
        at = first(fp, "at")
        rot = float(at[3]) if len(at) > 3 else 0.0
        side = "B" if str(first(fp, "layer")[1]) == "B.Cu" else "F"
        lay.placements[ref] = Placement(ref, side, at[1], at[2], rot)
        defaults = _default_texts(libs, fp, ref, rot, side)
        texts = [("property", str(p[1]), p) for p in kids(fp, "property")] + \
                [("fp_text", str(t[2]), t) for t in kids(fp, "fp_text")]
        for kind, name, node in texts:
            body = _text_body(node)
            want = defaults.get((kind, name))
            if want is None or canon(L(*body)) != canon(L(*want)):
                lay.texts[("text", ref, kind, name)] = TextOverride(
                    ref, kind, name, body)

    for seg in kids(root, "segment"):
        s, e = first(seg, "start"), first(seg, "end")
        lay.segments.append(Segment(_net_of(seg, by_code),
                                    str(first(seg, "layer")[1]),
                                    first(seg, "width")[1],
                                    s[1], s[2], e[1], e[2]))
    for via in kids(root, "via"):
        at = first(via, "at")
        layers = [str(x) for x in first(via, "layers")[1:]]
        lay.vias.append(Via(_net_of(via, by_code), at[1], at[2],
                            first(via, "size")[1], first(via, "drill")[1],
                            layers))
    unsupported = sorted({head(c) for c in root if isinstance(c, list)} -
                         {"version", "generator", "generator_version",
                          "general", "paper", "layers", "setup", "net",
                          "footprint", "segment", "via", "zone",
                          "embedded_fonts", "title_block"} -
                         set(GRAPHIC_KINDS))
    if unsupported:
        raise SystemExit(f"{board_path}: board items this capture does not "
                         f"understand, refusing to drop them: {unsupported}")
    return lay


def _net_of(node, by_code):
    net = first(node, "net")
    if len(net) == 2 and not str(net[1]).isdigit():
        return str(net[1])                       # KiCad 10: (net "AGND")
    if len(net) == 2:
        return by_code.get(str(net[1]), "")      # 20241229: (net 5)
    return str(net[2])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("board")
    ap.add_argument("layout")
    ap.add_argument("--project", help="the .kicad_pro holding the rules "
                    "(default: next to the board)")
    ap.add_argument("--check", action="store_true",
                    help="fail if the committed layout differs from a fresh "
                         "capture")
    args = ap.parse_args(argv)
    pro = args.project or os.path.splitext(args.board)[0] + ".kicad_pro"
    lay = capture(args.board, pro)
    text = HEADER + "\n".join(lay.lines()) + "\n"
    if args.check:
        with open(args.layout, encoding="utf-8") as fh:
            if fh.read() != text:
                print(f"FAIL {args.layout} is not a fresh capture of "
                      f"{args.board}", file=sys.stderr)
                return 1
        print(f"OK  {args.layout} is the capture of {args.board}")
        return 0
    os.makedirs(os.path.dirname(os.path.abspath(args.layout)), exist_ok=True)
    with open(args.layout, "w", encoding="utf-8") as fh:
        fh.write(text)
    again = load_layout(args.layout)
    if again.lines() != lay.lines():
        print("FAIL the layout file does not read back as written",
              file=sys.stderr)
        return 1
    print(f"wrote {args.layout}: {len(lay.placements)} placements, "
          f"{len(lay.texts)} text overrides, {len(lay.segments)} segments, "
          f"{len(lay.vias)} vias, {len(lay.zones)} zones, "
          f"{len(lay.graphics)} graphics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
