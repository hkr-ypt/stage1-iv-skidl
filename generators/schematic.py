"""
schematic.py — Hierarchical KiCad schematic emitter for the Stage 1 I–V system.
===============================================================================

Reads the LIVE SKiDL ``Circuit`` object (the same one that produced
``stage1_iv.net``) and writes a self-contained, hierarchical KiCad 7+
schematic: one top sheet (``stage1_iv.kicad_sch``) plus one child
``.kicad_sch`` per block. It never re-parses the netlist, so schematic and
netlist are consistent by construction.

Why not ``skidl.generate_schematic()``: SKiDL's own schematic backend needs
installed KiCad symbol libraries to fetch symbol graphics and does not emit
hierarchical KiCad 7 ``.kicad_sch`` sheets in a library-less environment, so a
purpose-built emitter is used instead.

Design (documented in SCHEMATIC.md and manifest.yaml -> schematic_assumptions):

* Symbols are synthesized generic rectangles — one per distinct part type,
  keyed by the part's ``KiCad_Lib`` field (fallback: MPN, then ref prefix) —
  and embedded in each sheet's ``(lib_symbols)`` block, so the files open in
  KiCad with zero external libraries. Pin names/numbers are the SKiDL pin
  names/numbers. ``Reference``/``Value``/``Footprint``/``Datasheet``/``MPN``/
  ``KiCad_Lib``/``Sheet``/``Pins_Verified`` are carried as symbol properties;
  ``Pins_Verified`` is drawn VISIBLE on any part not flagged ``STD``.

* Connectivity is expressed by name-matched labels at short wire stubs on
  every connected pin (no rat's-nest routing):
    - GLOBAL_POWER nets (rails carrying a PWR_FLAG, plus the shared power/
      reference group blocks/nets.py NET_MANIFEST["power"])
                                                      -> (global_label ...)
    - HIER_SIGNAL nets (span >= 2 blocks, not power)  -> (hierarchical_label)
      in children, matched by (sheet ... (pin ...)) on the top sheet, tied
      together there with stubs + local (label ...)s
    - LOCAL nets (all pins in one block)              -> local (label ...)
  Label text is EXACTLY the SKiDL net name used in the netlist. Pins tied to
  SKiDL's no-connect net get a KiCad (no_connect) marker.

* Placement is deterministic: parts in circuit build order, one rectangular
  cell per part (body + pins + 5.08 mm stubs + label allowances), cells flowed
  left-to-right into rows on a 1.27 mm grid; the smallest landscape paper of
  A4/A3/A2/A1/A0 that fits is chosen per sheet. All geometry is computed in
  integer 1.27 mm grid units, so output is byte-stable across runs.

* UUIDs are UUIDv5 in a fixed namespace, keyed by stable object identities
  (e.g. "power:sym:R31", "top:sheetpin:adc:SCK2"), so repeated runs emit
  byte-identical files.

Emitted schema: KiCad 7 s-expression, ``(version 20230121)`` — also opened
(and migrated forward) transparently by KiCad 8/9. KiCad itself is NOT needed
to run this generator.

Entry point:  generate_schematic(circuit, out_dir=".") -> list[pathlib.Path]
"""

import uuid
from pathlib import Path

from skidl import Pin

from blocks.nets import NET_MANIFEST

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT = "stage1_iv"
PWR_FLAG_LIB_ID = "power:PWR_FLAG"
SCHEMA_VERSION = "20230121"          # KiCad 7 .kicad_sch schema
GENERATOR_NAME = "stage1_iv_skidl"

# Fixed UUIDv5 namespace -> stable UUIDs across runs.
UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "stage1-iv-skidl.schematic")

# Block order == sheet/page order (page 2..9); top sheet is page 1.
BLOCK_ORDER = [
    "power", "mcu", "dac_sweep", "output_buffer",
    "current_sense", "voltage_sense", "adc", "protection",
]

BLOCK_TITLES = {
    "power": "Sheet 1 - Power Regulation & Reference",
    "mcu": "Sheet 2 - MCU & Digital I/O",
    "dac_sweep": "Sheet 3 - DAC & Sweep Level-Shift",
    "output_buffer": "Sheet 4 - Power Buffer (Voltage Source Output Stage)",
    "current_sense": "Sheet 5 - Current Sensing",
    "voltage_sense": "Sheet 6 - Voltage Sensing (4-Wire Kelvin)",
    "adc": "Sheet 7 - ADC",
    "protection": "Sheet 8 - Protection & DUT Connector",
}

TOP_FILENAME = "stage1_iv.kicad_sch"

# All geometry in integer units of the 1.27 mm KiCad half-grid.
PITCH = 2        # pin row pitch            (2.54 mm)
PIN_LEN = 2      # pin length               (2.54 mm)
STUB = 4         # wire stub length         (5.08 mm)
CHAR_W = 1       # per-character text width (1.27 mm)
MARGIN = 10      # sheet margin             (12.70 mm)
TITLE_RESERVE = 20   # bottom strip kept clear for the title block (25.4 mm)

# Landscape paper sizes in mm (name, width, height), smallest first.
PAPERS = [
    ("A4", 297, 210), ("A3", 420, 297), ("A2", 594, 420),
    ("A1", 841, 594), ("A0", 1189, 841),
]

FONT = "(effects (font (size 1.27 1.27)))"

# SKiDL pin function -> KiCad pin electrical type. Built by name so it works
# across SKiDL versions that lack some members.
_PIN_TYPE_NAMES = {
    "INPUT": "input", "OUTPUT": "output", "BIDIR": "bidirectional",
    "TRISTATE": "tri_state", "PASSIVE": "passive", "UNSPEC": "unspecified",
    "PWRIN": "power_in", "PWROUT": "power_out",
    "OPENCOLL": "open_collector", "OPENEMIT": "open_emitter",
    "PULLUP": "passive", "PULLDN": "passive",
    "NOCONNECT": "no_connect", "FREE": "free",
}
PIN_TYPE_MAP = {getattr(Pin.types, name): kicad
                for name, kicad in _PIN_TYPE_NAMES.items()
                if hasattr(Pin.types, name)}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def U(units):
    """Integer 1.27 mm grid units -> exact decimal millimetre string."""
    v = units * 127            # hundredths of a millimetre, exact
    sign = "-" if v < 0 else ""
    v = abs(v)
    whole, frac = divmod(v, 100)
    if frac == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{f'{frac:02d}'.rstrip('0')}"


def q(text):
    """Quote a string for s-expression output."""
    s = str(text).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


class UuidFactory:
    """Deterministic UUIDv5 per stable key; refuses duplicate keys."""

    def __init__(self):
        self._used = {}

    def get(self, key):
        if key in self._used:
            raise ValueError(f"duplicate UUID key: {key!r}")
        val = str(uuid.uuid5(UUID_NAMESPACE, key))
        self._used[key] = val
        return val


def symbol_instance_path(block, ref):
    """The KiCad instance path of one schematic symbol.

    This is what a board footprint's ``(path ...)`` must contain. KiCad matches
    board footprints to schematic symbols by this string and by nothing else;
    a footprint whose path does not resolve is treated as having no symbol, so
    "Update PCB from Schematic" adds a fresh copy of the part and orphans the
    placed one.

    Every UUID here is a deterministic UUIDv5 over the same namespace and the
    same keys :class:`UuidFactory` uses, so this can be computed without
    running the schematic generator - but it must be kept in step with it,
    which is why it lives in this file and not in generators/pcb.py.
    """
    def u(key):
        return str(uuid.uuid5(UUID_NAMESPACE, key))
    # The ROOT sheet's UUID is NOT part of the path. Verified against boards
    # KiCad itself wrote (kicad/qa/data/pcbnew): a symbol on the root sheet has
    # a one-segment path /<symbol-uuid>, and a symbol on a child sheet has
    # /<sheet-symbol-uuid>/<symbol-uuid>. Including the root gave a
    # three-segment path that matched nothing, so "Update PCB from Schematic"
    # still added all 110 footprints after the first fix.
    return f"/{u(f'top:sheetsym:{block}')}/{u(f'{block}:sym:{ref}')}"


def even_up(units):
    """Round grid units up to an even number (full 2.54 mm grid)."""
    return units + (units % 2)


def visible_len(name):
    """Rendered length of a pin name ('~' renders empty)."""
    return 0 if name == "~" else len(name)


# ---------------------------------------------------------------------------
# Symbol synthesis
# ---------------------------------------------------------------------------

class SymbolDef:
    """A synthesized rectangular lib symbol shared by all parts of one type."""

    def __init__(self, lib_id, template_part):
        self.lib_id = lib_id
        self.name = lib_id.split(":", 1)[1] if ":" in lib_id else lib_id
        self.ref_prefix = template_part.ref_prefix
        self.description = getattr(template_part, "description", "") or ""
        # Virtual, schematic-only symbols (PWR_FLAG) are emitted as KiCad
        # power symbols: (power), matching the attributes of the upstream
        # power:PWR_FLAG symbol exactly. What keeps them off the board and out
        # of the netlist is the "#" reference prefix KiCad reserves for power
        # symbols (upstream's own Reference default is "#FLG"), not an
        # on_board override.
        self.is_power = template_part.fields.get("Symbol_Class") == "power"
        pins = list(template_part.pins)
        self.signature = tuple(
            (str(p.num), str(p.name), PIN_TYPE_MAP[p.func]) for p in pins)

        n_left = (len(pins) + 1) // 2
        self.left = pins[:n_left]
        self.right = pins[n_left:]
        rows = max(len(self.left), len(self.right))
        self.height = (rows + 1) * PITCH

        widest = 0
        for i in range(rows):
            ln = visible_len(self.left[i].name) if i < len(self.left) else 0
            rn = visible_len(self.right[i].name) if i < len(self.right) else 0
            widest = max(widest, ln + rn)
        self.width = even_up(max(6, widest * CHAR_W + 2 * PITCH))

        # (num, name, kicad_type, lx, ly, angle) in lib coords (Y up).
        self.pin_geo = []
        for i, p in enumerate(self.left):
            ly = self.height // 2 - (i + 1) * PITCH
            self.pin_geo.append((str(p.num), str(p.name), PIN_TYPE_MAP[p.func],
                                 -self.width // 2 - PIN_LEN, ly, 0))
        for i, p in enumerate(self.right):
            ly = self.height // 2 - (i + 1) * PITCH
            self.pin_geo.append((str(p.num), str(p.name), PIN_TYPE_MAP[p.func],
                                 self.width // 2 + PIN_LEN, ly, 180))

    def pin_offset(self, num):
        """Lib-frame connection point (lx, ly, side) for pin number `num`."""
        for pnum, _name, _tp, lx, ly, angle in self.pin_geo:
            if pnum == num:
                return lx, ly, ("left" if angle == 0 else "right")
        raise KeyError(f"{self.lib_id}: no pin {num!r}")

    def sexpr(self):
        w2, h2 = self.width // 2, self.height // 2
        out = []
        power = "(power) " if self.is_power else ""
        out.append(f'    (symbol {q(self.lib_id)} {power}'
                   f'(pin_names (offset 0.508)) '
                   f'(in_bom yes) (on_board yes)')
        out.append(f'      (property "Reference" {q(self.ref_prefix)} '
                   f'(at {U(-w2)} {U(h2 + 1)} 0) '
                   f'(effects (font (size 1.27 1.27)) (justify left)))')
        out.append(f'      (property "Value" {q(self.name)} '
                   f'(at {U(-w2)} {U(-h2 - 1)} 0) '
                   f'(effects (font (size 1.27 1.27)) (justify left)))')
        for prop in ("Footprint", "Datasheet"):
            out.append(f'      (property {q(prop)} "" (at 0 0 0) '
                       f'(effects (font (size 1.27 1.27)) hide))')
        out.append(f'      (property "ki_description" {q(self.description)} '
                   f'(at 0 0 0) (effects (font (size 1.27 1.27)) hide))')
        out.append(f'      (symbol {q(self.name + "_0_1")}')
        out.append(f'        (rectangle (start {U(-w2)} {U(h2)}) '
                   f'(end {U(w2)} {U(-h2)}) '
                   f'(stroke (width 0.254) (type default)) '
                   f'(fill (type background)))')
        out.append('      )')
        out.append(f'      (symbol {q(self.name + "_1_1")}')
        for num, name, ktype, lx, ly, angle in self.pin_geo:
            out.append(f'        (pin {ktype} line '
                       f'(at {U(lx)} {U(ly)} {angle}) (length {U(PIN_LEN)})')
            out.append(f'          (name {q(name)} {FONT})')
            out.append(f'          (number {q(num)} {FONT})')
            out.append('        )')
        out.append('      )')
        out.append('    )')
        return out


def lib_id_for(part):
    """The lib_id every schematic instance points at: the PROJECT library.

    This used to return the part's ``KiCad_Lib`` field — the official upstream
    identifier, e.g. ``Device:R``. That made KiCad's ``lib_symbol_issues`` and
    ``lib_symbol_mismatch`` fire by construction: the schematic claimed each
    symbol was an instance of the official one while the body cached in
    ``(lib_symbols)`` was a synthesized rectangle sharing nothing with it but
    the pin names. A cache that has drifted from its library is exactly what
    that rule exists to detect, and it was right.

    Now every instance names ``stage1_iv:<SymbolName>``, and
    ``libraries/stage1_iv.kicad_sym`` is generated from these same
    :class:`SymbolDef` objects — so the cache and the library are the same
    bytes and cannot disagree. The upstream identifier is preserved as the
    ``KiCad_Lib`` property on every instance, still verified against the
    installed libraries by validation/footprint_check.py.
    """
    project_symbol = (part.fields.get("Project_Symbol") or "").strip()
    if project_symbol:
        return project_symbol
    lib = (part.fields.get("KiCad_Lib") or "").strip()
    if lib:
        return f"{PROJECT}:{lib.split(':', 1)[-1]}"
    mpn = (part.fields.get("MPN") or "").strip()
    if mpn and mpn not in ("generic", "n/a"):
        return f"{PROJECT}:{mpn}"
    return f"{PROJECT}:{part.ref_prefix}"


# ---------------------------------------------------------------------------
# Circuit model extraction
# ---------------------------------------------------------------------------

class CircuitModel:
    """Everything the emitter needs, pulled once from the live circuit."""

    def __init__(self, circuit):
        self.parts = list(circuit.parts)          # build order (deterministic)
        for part in self.parts:
            if not getattr(part, "skidl_block", None):
                raise ValueError(
                    f"part {part.ref} has no 'skidl_block' attribute; "
                    "build the circuit through main.build() so every part "
                    "is tagged with its owning block")
            if part.skidl_block not in BLOCK_ORDER:
                raise ValueError(
                    f"part {part.ref}: unknown block {part.skidl_block!r}")

        self.block_parts = {b: [p for p in self.parts if p.skidl_block == b]
                            for b in BLOCK_ORDER}

        # Nets carrying a PWR_FLAG. These are the board's externally-sourced
        # supply rails (blocks/nets.py::EXTERNALLY_SOURCED_RAILS), so they get
        # global power labels exactly like the NET_MANIFEST power group. This
        # replaces the old `net.drive == POWER` test, which only ever answered
        # True because blocks/nets.py stamped the attribute by hand.
        self.power_flag_nets = set()
        for part in self.parts:
            if part.fields.get("KiCad_Lib") != PWR_FLAG_LIB_ID:
                continue
            for pin in part.pins:
                for net in pin.get_nets():
                    if type(net).__name__ != "NCNet":
                        self.power_flag_nets.add(net.name)

        # (ref, pin_num) -> net name, and net name -> set of blocks touched.
        self.pin_net = {}
        self.net_blocks = {}
        for net in sorted(circuit.get_nets(), key=lambda x: x.name):
            blocks = set()
            for pin in net.get_pins():
                self.pin_net[(pin.part.ref, str(pin.num))] = net.name
                blocks.add(pin.part.skidl_block)
            self.net_blocks[net.name] = blocks

        # No-connect pins (SKiDL NC net) -> KiCad (no_connect) markers.
        self.nc_pins = {(pin.part.ref, str(pin.num))
                        for pin in circuit.NC.get_pins()}

        # Net classification (documented in SCHEMATIC.md).
        power_group = set(NET_MANIFEST["power"]) | self.power_flag_nets
        self.net_class = {}
        for name, blocks in self.net_blocks.items():
            if name in power_group:
                self.net_class[name] = "GLOBAL_POWER"
            elif len(blocks) >= 2:
                self.net_class[name] = "HIER_SIGNAL"
            else:
                self.net_class[name] = "LOCAL"

        # Hierarchical nets entering/leaving each block, sorted by name.
        self.block_hier_nets = {
            b: sorted(name for name, blocks in self.net_blocks.items()
                      if self.net_class[name] == "HIER_SIGNAL" and b in blocks)
            for b in BLOCK_ORDER
        }

        # Shared symbol definitions, keyed by lib id; signature collisions are
        # a hard error (two part types may not share one lib id).
        self.symbols = {}
        self.part_sym = {}
        for part in self.parts:
            lid = lib_id_for(part)
            sig = tuple((str(p.num), str(p.name), PIN_TYPE_MAP[p.func])
                        for p in part.pins)
            if lid in self.symbols:
                if self.symbols[lid].signature != sig:
                    raise ValueError(
                        f"lib id {lid!r} used by two different pin sets "
                        f"({part.ref} vs earlier part)")
            else:
                self.symbols[lid] = SymbolDef(lid, part)
            self.part_sym[part.ref] = self.symbols[lid]

    def board_net_name(self, net_name):
        """The net name KiCad computes for the board, from the schematic.

        KiCad does not use the bare net name on the board. It prefixes by the
        sheet path of the highest sheet the net reaches, and *Update PCB from
        Schematic* rewrites any pad that disagrees — which is why the update
        log was full of "Reconnect C24 pin 1" onto a sheet-qualified name.

        The three cases follow directly from the label this generator emits:

        ``global_label``        the net is global; the name is bare.
        ``hierarchical_label``  the net crosses sheets through hierarchical
                                pins, so it resolves at the root: ``/NAME``.
        ``label``               a plain local label, confined to one sheet;
                                the name is qualified by that sheet.
        """
        kind = self.net_class[net_name]
        if kind == "GLOBAL_POWER":
            return net_name
        if kind == "HIER_SIGNAL":
            return f"/{net_name}"
        block = sorted(self.net_blocks[net_name])[0]
        return f"/{block}/{net_name}"

    def label_kind(self, net_name):
        return {"GLOBAL_POWER": "global_label",
                "HIER_SIGNAL": "hierarchical_label",
                "LOCAL": "label"}[self.net_class[net_name]]


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

def flow_layout(cells):
    """Deterministic row-flow. cells = [(w, h)] in grid units.

    Returns (paper_name, [(x, y) top-left grid offsets incl. margin]).
    Picks the smallest paper (landscape A4..A0) whose usable area fits.
    """
    for paper, pw_mm, ph_mm in PAPERS:
        usable_w = int((pw_mm - 2 * MARGIN * 1.27) // 1.27)
        usable_h = int((ph_mm - (2 * MARGIN + TITLE_RESERVE) * 1.27) // 1.27)
        if any(w > usable_w for w, _h in cells):
            continue
        x = y = row_h = 0
        pos = []
        for w, h in cells:
            if x > 0 and x + w > usable_w:
                x = 0
                y += row_h
                row_h = 0
            pos.append((x, y))
            x += w
            row_h = max(row_h, h)
        if y + row_h <= usable_h:
            return paper, [(px + MARGIN, py + MARGIN) for px, py in pos]
    raise ValueError("layout does not fit on A0 — sheet is overfull")


def label_allowance(names, kind):
    """Grid-unit width reserved for the widest label among `names`."""
    if not names:
        return 2
    extra = 3 if kind in ("global_label", "hierarchical_label") else 1
    return max(2, max(len(nm) for nm in names) * CHAR_W + extra)


# ---------------------------------------------------------------------------
# Emission of one child sheet
# ---------------------------------------------------------------------------

def emit_label(kind, name, x, y, side, uid):
    """One label of any class at a stub end. side: which way text extends."""
    angle = 180 if side == "left" else 0
    justify = "right" if side == "left" else "left"
    out = []
    if kind == "label":
        out.append(f'  (label {q(name)} (at {U(x)} {U(y)} {angle}) '
                   f'(effects (font (size 1.27 1.27)) '
                   f'(justify {justify} bottom)) (uuid {uid}))')
    elif kind == "hierarchical_label":
        out.append(f'  (hierarchical_label {q(name)} (shape bidirectional) '
                   f'(at {U(x)} {U(y)} {angle}) '
                   f'(effects (font (size 1.27 1.27)) (justify {justify})) '
                   f'(uuid {uid}))')
    elif kind == "global_label":
        out.append(f'  (global_label {q(name)} (shape bidirectional) '
                   f'(at {U(x)} {U(y)} {angle}) (fields_autoplaced)')
        out.append(f'    (effects (font (size 1.27 1.27)) (justify {justify}))')
        out.append(f'    (uuid {uid})')
        out.append(f'    (property "Intersheetrefs" "${{INTERSHEET_REFS}}" '
                   f'(at {U(x)} {U(y)} 0) '
                   f'(effects (font (size 1.27 1.27)) hide))')
        out.append('  )')
    else:
        raise ValueError(kind)
    return out


def emit_wire(x1, y1, x2, y2, uid):
    return [f'  (wire (pts (xy {U(x1)} {U(y1)}) (xy {U(x2)} {U(y2)})) '
            f'(stroke (width 0) (type default)) (uuid {uid}))']


def part_cell(model, part):
    """Cell (w, h) plus per-side label allowances for one part instance."""
    sym = model.part_sym[part.ref]
    left_names, right_names = [], []
    for num, _name, _tp, _lx, _ly, angle in sym.pin_geo:
        key = (part.ref, num)
        if key in model.nc_pins:
            continue
        net = model.pin_net.get(key)
        if net is None:
            continue
        kind = model.label_kind(net)
        (left_names if angle == 0 else right_names).append(
            (net, kind))
    lab_l = label_allowance([n for n, _k in left_names],
                            _widest_kind(left_names))
    lab_r = label_allowance([n for n, _k in right_names],
                            _widest_kind(right_names))
    body_w = sym.width + 2 * PIN_LEN
    w = lab_l + STUB + body_w + STUB + lab_r + 2 * PITCH
    # Keep room for the visible Reference/Value/Pins_Verified texts.
    flag = part.fields.get("Pins_Verified", "")
    texts = [part.ref, str(part.value)]
    if flag and flag != "STD":
        texts.append(flag)
    w = max(w, max(len(t) for t in texts) * CHAR_W + 2 * PITCH)
    h = sym.height + 14      # 5.08 above (ref) + 12.70 below (value + flag)
    return even_up(w), even_up(h), lab_l


def _widest_kind(named):
    for _n, k in named:
        if k in ("global_label", "hierarchical_label"):
            return k
    return "label"


def emit_child_sheet(model, block, path, uids):
    parts = model.block_parts[block]
    cells = []
    for part in parts:
        w, h, lab_l = part_cell(model, part)
        cells.append((w, h, lab_l, part))
    paper, positions = flow_layout([(w, h) for w, h, _l, _p in cells])

    lib_ids = sorted({model.part_sym[p.ref].lib_id for p in parts})

    wires, labels, no_connects, symbols = [], [], [], []
    for (cw, ch, lab_l, part), (cx, cy) in zip(cells, positions):
        sym = model.part_sym[part.ref]
        sx = cx + lab_l + STUB + PIN_LEN + sym.width // 2
        sy = cy + 4 + sym.height // 2          # 5.08 mm headroom for ref text
        uid = uids.get(f"{block}:sym:{part.ref}")

        props = []
        rx, ry = sx - sym.width // 2, sy - sym.height // 2 - PITCH
        props.append(f'    (property "Reference" {q(part.ref)} '
                     f'(at {U(rx)} {U(ry)} 0) '
                     f'(effects (font (size 1.27 1.27)) (justify left)))')
        vy = sy + sym.height // 2 + PITCH
        props.append(f'    (property "Value" {q(str(part.value))} '
                     f'(at {U(rx)} {U(vy)} 0) '
                     f'(effects (font (size 1.27 1.27)) (justify left)))')
        flag = part.fields.get("Pins_Verified", "")
        flag_hide = "" if (flag and flag != "STD") else " hide"
        props.append(f'    (property "Pins_Verified" {q(flag)} '
                     f'(at {U(rx)} {U(vy + PITCH)} 0) '
                     f'(effects (font (size 1.27 1.27)) '
                     f'(justify left){flag_hide}))')
        hidden = [
            ("Footprint", part.footprint or ""),
            ("Datasheet", getattr(part, "datasheet", "") or ""),
            ("MPN", part.fields.get("MPN", "")),
            ("KiCad_Lib", part.fields.get("KiCad_Lib", "")),
            ("Sheet", block),
        ]
        for pname, pval in hidden:
            props.append(f'    (property {q(pname)} {q(pval)} '
                         f'(at {U(sx)} {U(sy)} 0) '
                         f'(effects (font (size 1.27 1.27)) hide))')

        pin_refs = []
        for num, _name, _tp, lx, ly, angle in sym.pin_geo:
            px, py = sx + lx, sy - ly          # lib frame is Y-up
            key = (part.ref, num)
            puid = uids.get(f"{block}:pinref:{part.ref}:{num}")
            pin_refs.append(f'    (pin {q(num)} (uuid {puid}))')
            if key in model.nc_pins:
                nuid = uids.get(f"{block}:nc:{part.ref}:{num}")
                no_connects.append(
                    f'  (no_connect (at {U(px)} {U(py)}) (uuid {nuid}))')
                continue
            net = model.pin_net.get(key)
            if net is None:
                continue
            side = "left" if angle == 0 else "right"
            ex = px - STUB if side == "left" else px + STUB
            wuid = uids.get(f"{block}:wire:{part.ref}:{num}")
            wires.extend(emit_wire(px, py, ex, py, wuid))
            luid = uids.get(f"{block}:lbl:{part.ref}:{num}")
            labels.extend(emit_label(model.label_kind(net), net,
                                     ex, py, side, luid))

        symbols.append(f'  (symbol (lib_id {q(sym.lib_id)}) '
                       f'(at {U(sx)} {U(sy)} 0) (unit 1) '
                       f'(in_bom yes) (on_board yes) (dnp no)')
        symbols.append(f'    (uuid {uid})')
        symbols.extend(props)
        symbols.extend(pin_refs)
        symbols.append('    (instances')
        symbols.append(f'      (project {q(PROJECT)}')
        symbols.append(f'        (path "/{uids.root}/'
                       f'{uids.sheetsym[block]}" '
                       f'(reference {q(part.ref)}) (unit 1))')
        symbols.append('      )')
        symbols.append('    )')
        symbols.append('  )')

    out = []
    out.append(f'(kicad_sch (version {SCHEMA_VERSION}) '
               f'(generator {GENERATOR_NAME})')
    out.append('')
    out.append(f'  (uuid {uids.sheetfile[block]})')
    out.append('')
    out.append(f'  (paper {q(paper)})')
    out.append('')
    out.append('  (title_block')
    out.append(f'    (title {q(BLOCK_TITLES[block])})')
    out.append(f'    (comment 1 "Generated from SKiDL by {GENERATOR_NAME}; '
               f'connectivity by net-name labels")')
    out.append('  )')
    out.append('')
    out.append('  (lib_symbols')
    for lid in lib_ids:
        out.extend(model.symbols[lid].sexpr())
    out.append('  )')
    out.append('')
    out.extend(no_connects)
    out.extend(wires)
    out.extend(labels)
    out.extend(symbols)
    out.append(')')
    path.write_text("\n".join(out) + "\n")
    return path


# ---------------------------------------------------------------------------
# Emission of the top sheet
# ---------------------------------------------------------------------------

def emit_top_sheet(model, path, uids, child_files):
    cells = []
    for block in BLOCK_ORDER:
        hier = model.block_hier_nets[block]
        fname = child_files[block]
        box_w = even_up(max(
            20,
            (max((len(nm) for nm in hier), default=0) + 2) * CHAR_W,
            len(block) * CHAR_W,
            len(fname) * CHAR_W,
        ) + 2 * PITCH)
        box_h = max((len(hier) + 1) * PITCH, 10)
        lab = label_allowance(hier, "label")
        w = even_up(lab + STUB + box_w + 2 * PITCH)
        h = even_up(box_h + 10)     # 6.35 above (sheetname) + below (file)
        cells.append((w, h, lab, box_w, box_h, block, hier, fname))
    paper, positions = flow_layout([(w, h) for w, h, *_rest in cells])

    wires, labels, sheets = [], [], []
    for (w, h, lab, box_w, box_h, block, hier, fname), (cx, cy) in zip(
            cells, positions):
        bx = cx + lab + STUB
        by = cy + 5
        suid = uids.sheetsym[block]
        page = 2 + BLOCK_ORDER.index(block)
        sheets.append(f'  (sheet (at {U(bx)} {U(by)}) '
                      f'(size {U(box_w)} {U(box_h)}) (fields_autoplaced)')
        sheets.append('    (stroke (width 0.1524) (type solid)) '
                      '(fill (color 0 0 0 0.0000))')
        sheets.append(f'    (uuid {suid})')
        sheets.append(f'    (property "Sheetname" {q(block)} '
                      f'(at {U(bx)} {U(by - 1)} 0) '
                      f'(effects (font (size 1.27 1.27)) '
                      f'(justify left bottom)))')
        sheets.append(f'    (property "Sheetfile" {q(fname)} '
                      f'(at {U(bx)} {U(by + box_h + 1)} 0) '
                      f'(effects (font (size 1.27 1.27)) (justify left top)))')
        for i, net in enumerate(hier):
            py = by + (i + 1) * PITCH
            puid = uids.get(f"top:sheetpin:{block}:{net}")
            sheets.append(f'    (pin {q(net)} bidirectional '
                          f'(at {U(bx)} {U(py)} 180) '
                          f'(effects (font (size 1.27 1.27)) (justify right)) '
                          f'(uuid {puid}))')
            wuid = uids.get(f"top:wire:{block}:{net}")
            wires.extend(emit_wire(bx, py, bx - STUB, py, wuid))
            luid = uids.get(f"top:lbl:{block}:{net}")
            labels.extend(emit_label("label", net, bx - STUB, py,
                                     "left", luid))
        sheets.append('    (instances')
        sheets.append(f'      (project {q(PROJECT)}')
        sheets.append(f'        (path "/{uids.root}" (page {q(str(page))}))')
        sheets.append('      )')
        sheets.append('    )')
        sheets.append('  )')

    out = []
    out.append(f'(kicad_sch (version {SCHEMA_VERSION}) '
               f'(generator {GENERATOR_NAME})')
    out.append('')
    out.append(f'  (uuid {uids.root})')
    out.append('')
    out.append(f'  (paper {q(paper)})')
    out.append('')
    out.append('  (title_block')
    out.append('    (title "Stage 1 I-V Characterization System - Top")')
    out.append(f'    (comment 1 "Generated from SKiDL by {GENERATOR_NAME}; '
               f'one child sheet per block")')
    out.append('  )')
    out.append('')
    out.append('  (lib_symbols)')
    out.append('')
    out.extend(wires)
    out.extend(labels)
    out.extend(sheets)
    out.append('')
    out.append('  (sheet_instances')
    out.append('    (path "/" (page "1"))')
    out.append('  )')
    out.append(')')
    path.write_text("\n".join(out) + "\n")
    return path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

class _Uids(UuidFactory):
    """UuidFactory plus the pre-computed root/sheet UUIDs the emitters share."""

    def __init__(self):
        super().__init__()
        self.root = self.get("sch:top")
        self.sheetfile = {b: self.get(f"sch:{b}") for b in BLOCK_ORDER}
        self.sheetsym = {b: self.get(f"top:sheetsym:{b}") for b in BLOCK_ORDER}


def generate_schematic(circuit, out_dir="."):
    """Write the hierarchical KiCad schematic for `circuit`.

    Returns the list of written Paths: [top, child1..child8] in sheet order.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model = CircuitModel(circuit)
    uids = _Uids()

    child_files = {b: f"{b}.kicad_sch" for b in BLOCK_ORDER}

    written = []
    top = emit_top_sheet(model, out_dir / TOP_FILENAME, uids, child_files)
    for block in BLOCK_ORDER:
        written.append(
            emit_child_sheet(model, block, out_dir / child_files[block], uids))
    return [top] + written
