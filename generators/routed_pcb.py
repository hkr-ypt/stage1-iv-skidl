"""
routed_pcb.py — the board of record, generated from source.
============================================================

Combines three sources, none of which is a KiCad board file:

    parts   the footprints, values, fields, pad nets and schematic links —
            from the live SKiDL circuit (generators/pcb.py::prepare_footprints),
            with footprint geometry from the vendored library;
    layout  where each part sits and how the copper runs — layout/*.layout,
            captured from the human-routed board (tools/capture_layout.py);
    ECOs    explicit, reasoned changes to that layout — layout/*.eco;

and writes a complete, routed ``.kicad_pcb``. Placement is exactly the
layout's; nothing is packed, nudged or autorouted. Every footprint is placed
with KiCad's own storage rules (generators/footprint_xform.py), every UUID is
derived from content, and the output is byte-identical run to run.

Zones are written as definitions, without fill: the fill is derived data that
KiCad computes (``kicad-cli pcb drc --refill-zones``, ``export gerbers
--check-zones``, or B in the editor).

The same emitter reproduces the ORIGINAL human-routed board when it is handed
that board's own parts instead of the SKiDL circuit's
(:func:`parts_from_board`); tools/reproduce_reference.py uses that to prove the
layout capture loses nothing.
"""

from __future__ import annotations

import copy
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_REPO_ROOT, os.path.join(_REPO_ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from resolve_kicad_libs import (  # noqa: E402
    KicadLibraries, parse_sexp, write_sexp, Atom, SexpList,
)
from generators.footprint_xform import (  # noqa: E402
    head, kids, first, fmt, place_footprint,
)
from generators.layout import (  # noqa: E402
    Layout, LayoutError, load_layout, apply_eco, strip, one_line,
)
from generators import pcb as _pcb  # noqa: E402

BOARD_VERSION = _pcb.BOARD_VERSION
GENERATOR = _pcb.GENERATOR
uid = _pcb.uid


def A(text):
    return Atom(str(text), 0, False)


def Q(text):
    return Atom(str(text), 0, True)


def L(*items):
    return SexpList(items)


# ---------------------------------------------------------------------------
# parts
# ---------------------------------------------------------------------------

def parts_from_circuit(circuit, libs=None):
    """(nodes by ref, net_names, codes) for the live SKiDL circuit."""
    libs = libs or KicadLibraries()
    prep = _pcb.prepare_footprints(circuit, libs)
    return prep["nodes"], prep["net_names"], prep["codes"]


_KEEP_FROM_BOARD = ("path", "sheetname", "sheetfile", "units", "attr")


def parts_from_board(board_path, libs=None):
    """(nodes, net_names, codes) describing an EXISTING board's parts.

    Geometry still comes from the vendored library; only what the board says
    about each part — reference, value, fields, pad nets, schematic link,
    attributes — is taken from the board. This is how a routed board's own
    electrical content is fed back through the emitter, so that what is
    compared against the original is the layout capture alone.
    """
    libs = libs or KicadLibraries()
    with open(board_path, encoding="utf-8") as fh:
        root = next(c for c in parse_sexp(fh.read()) if head(c) == "kicad_pcb")
    nodes, nets, board_pads = {}, set(), {}
    for fp in kids(root, "footprint"):
        props = [p for p in kids(fp, "property")]
        ref = next(str(p[2]) for p in props if str(p[1]) == "Reference")
        node, _src = _pcb.load_footprint(libs, str(fp[1]))
        _pcb._set_uuid(node, uid(f"fp:{ref}"))
        # Properties: the board's values, in the board's order. Geometry is
        # the library's (or the field default); the layout's text overrides
        # put each one where the designer left it.
        lib_props = {str(p[1]): p for p in kids(node, "property")}
        node[:] = [c for c in node if head(c) != "property"]
        insert_at = next(i for i, c in enumerate(node) if head(c) == "uuid") + 1
        new_props = []
        for p in props:
            name, value = str(p[1]), str(p[2])
            if name in lib_props:
                q = copy.deepcopy(lib_props[name])
                q[2] = Q(value)
            else:
                q = _field_default(ref, name, value)
            new_props.append(q)
        node[insert_at:insert_at] = new_props
        for key in _KEEP_FROM_BOARD:
            for c in list(kids(node, key)):
                node.remove(c)
            for c in kids(fp, key):
                node.append(copy.deepcopy(c))
        pad_nets = {}
        for pad in kids(fp, "pad"):
            net = first(pad, "net")
            name = str(net[-1]) if net is not None and len(net) > 1 else ""
            if not name:
                continue
            fn = first(pad, "pinfunction")
            pt = first(pad, "pintype")
            pad_nets[str(pad[1])] = (name, str(fn[1]) if fn else "",
                                     str(pt[1]) if pt else "")
            nets.add(name)
        board_pads[ref] = pad_nets
        nodes[ref] = node
    for seg in kids(root, "segment") + kids(root, "via") + kids(root, "zone"):
        net = first(seg, "net")
        if net is not None and len(net) > 1 and str(net[-1]):
            nets.add(str(net[-1]))
    net_names = sorted(nets)
    codes = {"": 0}
    for i, name in enumerate(net_names, start=1):
        codes[name] = i
    for ref, node in nodes.items():
        _pcb.assign_nets(node, {num: (codes[n], n, fn, pt) for num, (n, fn, pt)
                                in board_pads[ref].items()})
    return nodes, net_names, codes


def _field_default(ref, name, value):
    """The text node generators/pcb.py::add_field writes for a new field."""
    scratch = L(A("footprint"), Q("x"), L(A("uuid"), Q("x")))
    _pcb.add_field(scratch, ref, name, value)
    return kids(scratch, "property")[0]


def field_default_body(name):
    """Geometry children of a default field (for tools/capture_layout.py)."""
    return [c for c in _field_default("x", name, "") if isinstance(c, list)]


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------

def _apply_text(node, override):
    """Put one footprint text where the layout says it goes."""
    for child in node:
        if head(child) != override.kind:
            continue
        if override.kind == "property" and str(child[1]) != override.name:
            continue
        if override.kind == "fp_text" and str(child[2]) != override.name:
            continue
        fixed = 3                     # (property "Name" "value" ...) and
        keep = child[:fixed]          # (fp_text user "text" ...) alike
        uuid = first(child, "uuid")
        child[:] = keep + copy.deepcopy(override.body) + \
            ([uuid] if uuid is not None else [])
        return True
    return False


def _unique_child_uuids(node, ref):
    """Library items share UUIDs across every instance; make them per-part."""
    for i, child in enumerate(node):
        if not isinstance(child, list) or head(child) in ("uuid",):
            continue
        u = first(child, "uuid")
        if u is not None:
            u[1] = Q(uid(f"fp:{ref}:{i}:{head(child)}"))


def place_parts(nodes, layout):
    """Place every part per the layout. Refuses a mismatch in either direction.

    A part with no placement cannot be laid out by guessing; a placement with
    no part means the layout describes a different circuit. Both are errors.
    """
    missing = sorted(set(nodes) - set(layout.placements))
    stale = sorted(set(layout.placements) - set(nodes))
    if missing or stale:
        raise LayoutError(
            f"layout and circuit disagree: parts with no placement {missing}, "
            f"placements with no part {stale}")
    placed = {}
    skipped = []
    for ref, node in nodes.items():
        p = layout.placements[ref]
        node = copy.deepcopy(node)
        place_footprint(node, p.x, p.y, p.rot, p.side)
        _unique_child_uuids(node, ref)
        placed[ref] = node
    for key, override in sorted(layout.texts.items()):
        ref = override.ref
        if ref not in placed:
            raise LayoutError(f"text override for {ref}, which is not placed")
        if not _apply_text(placed[ref], override):
            # A field the circuit does not have (e.g. a KiCad-added field on
            # the captured board). The text does not exist, so neither does
            # its position; recorded, not fatal.
            skipped.append(key)
    return placed, skipped


def _zone_node(zone, codes):
    """(zone (net "AGND") ...) from the layout -> 20241229 board syntax."""
    net = str(first(zone, "net")[1])
    out = [A("zone"), L(A("net"), A(codes[net])), L(A("net_name"), Q(net))]
    for child in zone[1:]:
        if head(child) == "net":
            continue
        out.append(copy.deepcopy(child))
        if head(child) == "layers":
            out.append(L(A("uuid"), Q(uid(f"zone:{net}:{one_line(zone)}"))))
    return SexpList(out)


def emit_board(nodes, net_names, codes, layout):
    """The complete board as text. Deterministic."""
    placed, skipped = place_parts(nodes, layout)
    for s in layout.segments:
        if s.net not in codes:
            raise LayoutError(f"track on net {s.net!r}, which the circuit "
                              f"does not have: {one_line(s.node())}")
    for v in layout.vias:
        if v.net not in codes:
            raise LayoutError(f"via on net {v.net!r}, which the circuit "
                              f"does not have: {one_line(v.node())}")

    board = [A("kicad_pcb"),
             L(A("version"), A(BOARD_VERSION)),
             L(A("generator"), Q(GENERATOR)),
             L(A("generator_version"), Q("9.0"))]
    board.append(copy.deepcopy(layout.general))
    board.append(copy.deepcopy(layout.paper))
    board.append(_pcb._layers_block())
    board.append(copy.deepcopy(layout.setup))
    board.append(L(A("net"), A("0"), Q("")))
    for name in net_names:
        board.append(L(A("net"), A(codes[name]), Q(name)))
    for ref in nodes:                                 # circuit order
        board.append(placed[ref])
    for g in sorted(layout.graphics, key=one_line):
        g = copy.deepcopy(g)
        g.append(L(A("uuid"), Q(uid(f"graphic:{one_line(g)}"))))
        board.append(g)
    seen = {}
    for s in sorted(layout.segments, key=lambda s: one_line(s.node())):
        key = one_line(s.node())
        n = seen[key] = seen.get(key, -1) + 1
        board.append(L(A("segment"),
                       L(A("start"), A(fmt(s.x1)), A(fmt(s.y1))),
                       L(A("end"), A(fmt(s.x2)), A(fmt(s.y2))),
                       L(A("width"), A(fmt(s.width))),
                       L(A("layer"), Q(s.layer)),
                       L(A("net"), A(codes[s.net])),
                       L(A("uuid"), Q(uid(f"seg:{key}:{n}")))))
    for v in sorted(layout.vias, key=lambda v: one_line(v.node())):
        key = one_line(v.node())
        n = seen[key] = seen.get(key, -1) + 1
        board.append(L(A("via"),
                       L(A("at"), A(fmt(v.x)), A(fmt(v.y))),
                       L(A("size"), A(fmt(v.size))),
                       L(A("drill"), A(fmt(v.drill))),
                       L(A("layers"), *[Q(x) for x in v.layers]),
                       L(A("net"), A(codes[v.net])),
                       L(A("uuid"), Q(uid(f"via:{key}:{n}")))))
    for z in sorted(layout.zones, key=one_line):
        board.append(_zone_node(z, codes))
    text = write_sexp(SexpList(board)) + "\n"
    return text, {"skipped_texts": skipped, "parts": len(placed),
                  "segments": len(layout.segments), "vias": len(layout.vias),
                  "zones": len(layout.zones), "nets": len(net_names)}


# ---------------------------------------------------------------------------
# the board of record
# ---------------------------------------------------------------------------

LAYOUT_DIR = os.path.join(_REPO_ROOT, "layout")
BASE_LAYOUT = os.path.join(LAYOUT_DIR, "stage1_iv.layout")
ECOS = [os.path.join(LAYOUT_DIR, "bipolar.eco")]


def composed_layout(base=BASE_LAYOUT, ecos=None):
    """The base layout with every ECO applied, in order."""
    layout = load_layout(base)
    applied = []
    for eco in (ECOS if ecos is None else ecos):
        layout, meta, ops = apply_eco(layout, eco)
        applied.append((eco, meta, ops))
    return layout, applied


def generate_routed_pcb(circuit, out_dir=_REPO_ROOT, base=BASE_LAYOUT,
                        ecos=None):
    """Write stage1_iv.kicad_pcb from the circuit + layout + ECOs."""
    problems = _pcb.check_against_fab()
    if problems:
        raise RuntimeError("netclasses violate docs/FAB_CONSTRAINTS.md:\n  - "
                           + "\n  - ".join(problems))
    layout, applied = composed_layout(base, ecos)
    nodes, net_names, codes = parts_from_circuit(circuit)
    text, report = emit_board(nodes, net_names, codes, layout)
    path = os.path.join(out_dir, "stage1_iv.kicad_pcb")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    report.update({
        "path": path,
        "layout": os.path.relpath(base, _REPO_ROOT),
        "ecos": [(os.path.relpath(e, _REPO_ROOT), len(ops))
                 for e, _m, ops in applied],
    })
    return path, report
