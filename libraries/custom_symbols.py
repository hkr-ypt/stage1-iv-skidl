"""
custom_symbols.py — part factories for the Stage 1 I–V Characterization System
==============================================================================

WHY THIS FILE EXISTS
--------------------
Every part in this design is a SKiDL-native part (``tool=SKIDL``) so the design
stays fully executable — ERC, netlist, schematic, validation — without KiCad
itself. What changed: **the pins are no longer written here.** Each factory
names an official KiCad ``lib_id`` and the pin numbers, pin names and pin
electrical types are *read out of the installed KiCad symbol library at build
time* by :mod:`tools.resolve_kicad_libs`.

That means:

  * no pin number in this project can be stale, invented, or remembered — it is
    whatever the symbol file on disk says, and the audit tool prints the file
    and line every value came from;
  * a symbol that does not exist upstream fails the build immediately instead
    of silently producing a netlist that maps nets onto pads that do not exist.

Building therefore requires the KiCad libraries to be installed. See
``tools/install_kicad_libs.sh`` and ``kicad_libs.json``.

PIN NAMES AND THE ALIAS LAYER
-----------------------------
Blocks connect **by pin function name** (``dac["VREF"] += VREF_2V5``), never by
raw pin number. The official symbols sometimes spell a pin differently from the
design (``V_{REF}`` vs ``VREF``; INA128 names both gain pins ``Rg``). We do
**not** rename the official pin. Instead each factory declares an ``aliases``
map ``{design_name: pin_number}`` and the design name is added to that pin's
SKiDL aliases, so the block files keep reading naturally while the netlist
carries the official name and the official number.

Every alias is listed in ``ALIAS_DOCUMENTATION`` at the bottom of this file.

PIN-NUMBER PROVENANCE (``Pins_Verified`` field)
-----------------------------------------------
    "RESOLVED"          Pin numbers were read from an official KiCad symbol
                        library file, or from a project symbol authored
                        directly from the manufacturer data sheet. Fabrication
                        safe.
    "VERIFY-DATASHEET"  Pin numbers were read from a real library file, but
                        that file is not the manufacturer data sheet (see
                        libraries/provenance/). Confirm before fabrication.

``PLACEHOLDER-REMAP`` no longer exists anywhere in this project;
``validation/footprint_check.py`` hard-fails if it reappears.
"""

import os
import sys

from skidl import Part, Pin, SKIDL, TEMPLATE

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "tools"))

from resolve_kicad_libs import KicadLibraries, LibraryNotFound  # noqa: E402


class SymbolResolutionError(RuntimeError):
    """A declared lib_id could not be resolved from any source."""


PROJECT_LIB_NICK = "stage1_iv"
PROJECT_LIB_PATH = os.path.join(_REPO_ROOT, "libraries", "stage1_iv.kicad_sym")

_LIBS = None
_PROJECT_LIB = None
_UPSTREAM_STATE = {"available": None, "origin": None, "used": set(),
                   "fallback_used": set(), "authored_used": set()}


def libs():
    """View of the installed upstream KiCad libraries, or None if absent.

    Returns None rather than raising: the design must still build on a machine
    with no KiCad, falling back to the committed project library. What must
    never happen is a *silent* fallback, so :func:`upstream_state` records
    exactly which source every symbol came from and the build banner prints it.
    """
    global _LIBS
    if _UPSTREAM_STATE["available"] is None:
        try:
            _LIBS = KicadLibraries()
            _UPSTREAM_STATE["available"] = True
            _UPSTREAM_STATE["origin"] = _LIBS.origin
        except LibraryNotFound as exc:
            _LIBS = None
            _UPSTREAM_STATE["available"] = False
            _UPSTREAM_STATE["origin"] = str(exc).splitlines()[0]
    return _LIBS


def project_lib():
    """The committed project symbol library, used when upstream is absent."""
    global _PROJECT_LIB
    if _PROJECT_LIB is None:
        if not os.path.isfile(PROJECT_LIB_PATH):
            raise SymbolResolutionError(
                f"neither the installed KiCad libraries nor the committed "
                f"project library {PROJECT_LIB_PATH} is available; there is "
                f"no source for pin numbers and this design will not guess.")
        _PROJECT_LIB = _parse_project_lib(PROJECT_LIB_PATH)
    return _PROJECT_LIB


def _parse_project_lib(path):
    """{symbol_name: SimpleSymbol} from a .kicad_sym, without needing KiCad."""
    from resolve_kicad_libs import KicadLibraries as _K
    with open(path, encoding="utf-8") as fh:
        from resolve_kicad_libs import parse_sexp
        tree = parse_sexp(fh.read())
    out = {}
    for node in tree:
        if not isinstance(node, list) or not node or node[0] != "kicad_symbol_lib":
            continue
        for child in node:
            if isinstance(child, list) and child and child[0] == "symbol":
                sym = _K._build_symbol(PROJECT_LIB_NICK, path, child)
                out[sym.name] = sym
    return out


def upstream_state():
    """Report which symbol source was used, for the build capability banner."""
    return dict(_UPSTREAM_STATE)


def _authored_pins(symbol_name):
    """Pins of a datasheet-authored symbol, parsed from libraries/provenance/.

    ADS1256 and OPA548T have no upstream symbol. Their pins are read from the
    committed provenance excerpts rather than from the generated project
    library, so ``tools/author_project_symbols.py --check`` is an independent
    check and not a comparison of the library against itself.
    """
    import author_project_symbols as authored
    return {"ADS1256": authored.parse_ads1256,
            "OPA548T": authored.parse_opa548t}[symbol_name]()


AUTHORED_SYMBOLS = ("ADS1256", "OPA548T")


class _Sym:
    """Minimal symbol record, shaped like resolve_kicad_libs.Symbol."""

    def __init__(self, lib, name, file, line, pins):
        self.lib, self.name, self.file, self.line = lib, name, file, line
        self.pins = pins
        self.properties = {}


class _SymPin:
    def __init__(self, number, name, etype):
        self.number, self.name, self.etype = number, name, etype


def resolve_symbol(lib_id):
    """Resolve a lib_id to a symbol, recording which source answered.

    Order: datasheet-authored provenance -> installed upstream library ->
    committed project library. Every outcome is recorded in
    :func:`upstream_state`; nothing resolves silently.
    """
    nick, _, name = lib_id.partition(":")

    if nick == PROJECT_LIB_NICK and name in AUTHORED_SYMBOLS:
        rows = sorted(_authored_pins(name), key=lambda r: int(r["number"]))
        _UPSTREAM_STATE["authored_used"].add(lib_id)
        src = rows[0]["source"].split(":")[0] if rows else "libraries/provenance"
        return _Sym(PROJECT_LIB_NICK, name, os.path.join(_REPO_ROOT, src), 1,
                    [_SymPin(r["number"], r["name"], r["etype"]) for r in rows])

    installed = libs()
    if installed is not None:
        sym = installed.get_symbol(lib_id)
        if sym is not None:
            _UPSTREAM_STATE["used"].add(lib_id)
            return sym

    sym = project_lib().get(name)
    if sym is not None:
        _UPSTREAM_STATE["fallback_used"].add(lib_id)
        return sym
    return None


# KiCad electrical-type string -> SKiDL pin function.
_ETYPE = {
    "input": Pin.types.INPUT,
    "output": Pin.types.OUTPUT,
    "bidirectional": Pin.types.BIDIR,
    "tri_state": Pin.types.TRISTATE,
    "passive": Pin.types.PASSIVE,
    "free": Pin.types.PASSIVE,
    "unspecified": Pin.types.UNSPEC,
    "power_in": Pin.types.PWRIN,
    "power_out": Pin.types.PWROUT,
    "open_collector": Pin.types.OPENCOLL,
    "open_emitter": Pin.types.OPENEMIT,
    "no_connect": Pin.types.NOCONNECT,
}


def _part(lib_id, ref_prefix, footprint, mpn, pins_verified, description,
          aliases=None, extra_aliases=None, keywords="", datasheet="",
          name=None, symbol_class=""):
    """Build a TEMPLATE SKiDL part whose pins come from an installed symbol.

    ``aliases`` maps a design-facing pin name to the official pin *number* it
    refers to (a number, or a list of numbers when the design name legitimately
    covers several pads — e.g. USB-C ``DP`` is pads A6 *and* B6). That name
    becomes the pin's SKiDL name and the symbol's own name is kept as an alias,
    so both spellings address the pin. The design name is a *label*; the pin
    NUMBER — the fabrication-critical value — always comes from the symbol
    file and is never restated here.

    ``extra_aliases`` adds further names without changing the pin's name; use
    it when the official name is already the one the blocks should read
    (ADS1256 ``XTAL1/CLKIN`` is one pad addressable as ``XTAL1`` or ``CLKIN``).

    ``symbol_class`` marks a **virtual** symbol — one that exists only in the
    schematic and never reaches the board (currently ``"power"``, i.e.
    PWR_FLAG). Virtual parts carry no footprint by design; the footprint checks
    exempt exactly the parts carrying this field rather than being loosened.
    """
    sym = resolve_symbol(lib_id)
    if sym is None:
        installed = libs()
        nick = lib_id.split(":", 1)[0] if ":" in lib_id else lib_id
        known = installed is not None and installed.has_symbol_library(nick)
        raise SymbolResolutionError(
            f"symbol {lib_id!r} does not exist in the installed KiCad "
            f"libraries ({'library found, symbol missing' if known else 'library not found'}) "
            f"nor in the committed project library {PROJECT_LIB_PATH}.\n"
            f"Resolve the correct name with:\n"
            f"    python3 tools/resolve_kicad_libs.py --find-symbol "
            f"'{lib_id.split(':')[-1]}*'")

    official_numbers = {sp.number for sp in sym.pins}
    design_name_of = {}
    for design_name, numbers in (aliases or {}).items():
        for num in (numbers if isinstance(numbers, (list, tuple)) else [numbers]):
            num = str(num)
            if num not in official_numbers:
                raise SymbolResolutionError(
                    f"{lib_id}: cannot name pin {num!r} {design_name!r} — "
                    f"that pin number is not in the symbol")
            design_name_of[num] = design_name

    p = Part(
        name=name or sym.name,
        tool=SKIDL,
        ref_prefix=ref_prefix,
        dest=TEMPLATE,
        footprint=footprint,
        pins=[Pin(num=sp.number,
                  name=design_name_of.get(sp.number, sp.name),
                  func=_ETYPE.get(sp.etype, Pin.types.UNSPEC))
              for sp in sym.pins],
    )

    # Keep the symbol's own pin name reachable alongside the design name.
    for sp in sym.pins:
        if sp.number in design_name_of and sp.name != design_name_of[sp.number]:
            p[sp.number].aliases += sp.name

    for design_name, numbers in (extra_aliases or {}).items():
        for num in (numbers if isinstance(numbers, (list, tuple)) else [numbers]):
            target = p[str(num)]
            if target is None:
                raise SymbolResolutionError(
                    f"{lib_id}: cannot alias {design_name!r} to pin {num!r} — "
                    f"that pin number is not in the symbol")
            target.aliases += design_name

    p.description = description
    p.keywords = keywords
    p.datasheet = datasheet or sym.properties.get("Datasheet", "")
    # Documentation fields carried into the KiCad netlist / BOM.
    p.fields["MPN"] = mpn
    p.fields["KiCad_Lib"] = lib_id
    p.fields["Pins_Verified"] = pins_verified
    # Machine-checkable provenance: exactly where these pin numbers were read.
    p.fields["Symbol_Source"] = f"{os.path.basename(sym.file)}:{sym.line}"
    # The project-library symbol every schematic instance points at. See
    # generators/schematic.py::lib_id_for and docs/LIBRARY_DEPENDENCIES.md.
    p.fields["Project_Symbol"] = f"{PROJECT_LIB_NICK}:{sym.name}"
    if symbol_class:
        p.fields["Symbol_Class"] = symbol_class
    return p


def is_virtual(part):
    """True for schematic-only symbols that never reach the board (PWR_FLAG).

    The footprint checks call this instead of special-casing an empty
    footprint string, so a *real* part that lost its footprint is still an
    error while a virtual symbol is legitimately exempt.
    """
    return bool(part.fields.get("Symbol_Class"))


# =====================================================================
#  POWER FLAG  (virtual — schematic only, never on the board)
# =====================================================================

def PWR_FLAG():
    """KiCad's PWR_FLAG: declares a net as driven when nothing on the board drives it.

    KiCad ERC's ``power_pin_not_driven`` rule fires on any net that has a
    power-input pin but no power-output pin. Rails fed from outside the board
    — the bench supply, the USB host — legitimately have no on-board driver,
    and PWR_FLAG is the mechanism KiCad provides to say so. Its single pin is
    ``power_out``, which is exactly what satisfies both KiCad's rule and
    SKiDL's "insufficient drive current" check.

    This is a *real symbol with a real pin* that is serialised into the netlist
    and the schematic — unlike the ``Net.drive = POWER`` attribute it replaces,
    which existed only inside the Python process and silenced SKiDL's ERC
    without telling KiCad anything. See blocks/nets.py.
    """
    # Pins_Verified is "RESOLVED", not the "STD" of the pre-resolver vocabulary:
    # this symbol's single pin number is read from power.kicad_sym like every
    # other pin in the design, and "STD" no longer exists as a provenance class.
    return _part("power:PWR_FLAG", "FLG", "", "n/a", "RESOLVED",
                 "Power flag: declares an externally-sourced net as driven",
                 keywords="power flag pwr_flag virtual",
                 symbol_class="power")


# =====================================================================
#  GENERIC PASSIVES
# =====================================================================

def R():
    return _part("Device:R", "R", "Resistor_SMD:R_0603_1608Metric",
                 "generic", "RESOLVED", "Resistor")


def C():
    return _part("Device:C", "C", "Capacitor_SMD:C_0603_1608Metric",
                 "generic", "RESOLVED", "Unpolarized capacitor")


def C_POL():
    # Device:C_Polarized draws the "+" glyph on the pin-1 side of the body
    # (polylines at y=+2.286/+2.794, pin 1 at y=+3.81), so pin 1 is the anode.
    return _part("Device:C_Polarized", "C",
                 "Capacitor_THT:CP_Radial_D8.0mm_P3.50mm",
                 "generic", "RESOLVED", "Polarized capacitor",
                 aliases={"+": 1, "-": 2})


def L_FerriteBead():
    return _part("Device:FerriteBead", "FB", "Inductor_SMD:L_0805_2012Metric",
                 "BLM21PG221SN1D", "RESOLVED", "Ferrite bead, 220R @ 100MHz")


def D():
    return _part("Device:D", "D", "Diode_SMD:D_SOD-123",
                 "1N4148", "RESOLVED", "Diode (cathode = pin K)")


def D_TVS():
    return _part("Device:D_TVS", "D", "Diode_SMD:D_SMB",
                 "SMBJ15CA", "RESOLVED", "Bidirectional TVS diode")


def LED():
    return _part("Device:LED", "D", "LED_SMD:LED_0805_2012Metric",
                 "generic", "RESOLVED", "Light emitting diode")


def Fuse_PTC():
    return _part("Device:Polyfuse", "F", "Fuse:Fuse_Bourns_MF-RG300",
                 "0ZRM0010FF1E", "RESOLVED", "PTC resettable fuse")


def Crystal():
    return _part("Device:Crystal", "Y", "Crystal:Crystal_SMD_HC49-SD",
                 "generic", "RESOLVED", "Two pin crystal")


def TestPoint():
    return _part("Connector:TestPoint", "TP", "TestPoint:TestPoint_Pad_D1.5mm",
                 "n/a", "RESOLVED", "Test point")


def SW_Push():
    return _part("Switch:SW_Push", "SW", "Button_Switch_THT:SW_PUSH_6mm",
                 "generic", "RESOLVED", "Momentary push switch")


# =====================================================================
#  CONNECTORS
# =====================================================================

def TerminalBlock_4():
    # Footprint selection: of the six 4-position 5.08 mm terminal blocks in the
    # installed libraries, the Phoenix MKDS-1,5-4-5.08 is the 1.5 mm^2 industry
    # reference part with a 1.3 mm drill (adequate for the bench-supply rails)
    # and the smallest board depth (9.8 mm) among the 1.3 mm-drill options.
    # See docs/identifier_resolution.md for the full candidate table.
    return _part(
        "Connector:Screw_Terminal_01x04", "J",
        "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-4-5.08"
        "_1x04_P5.08mm_Horizontal",
        "MKDS 1,5/4-5,08", "RESOLVED",
        "4-pos terminal block: +15V_RAW,-15V_RAW,+5V_RAW,GND")


def Header_1x05():
    return _part("Connector_Generic:Conn_01x05", "J",
                 "Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical",
                 "generic-2.54mm", "RESOLVED",
                 "SWD header: SWDIO,SWCLK,GND,3V3,NRST")


def BananaJack(function_name):
    """One panel-mount banana jack, aliased to the DUT terminal it carries.

    A 4-position banana jack does not exist as a single KiCad part — only
    Banana_Jack_1Pin / 2Pin / 3Pin — so the DUT connector is four separate
    single-pin jacks (J4..J7), one per Kelvin wire.
    """
    # The jack's pin keeps the symbol's own name (``Pin_1``) and gains the
    # terminal function as an alias: all four jacks then share one identical
    # symbol definition, while the block still connects by function name.
    return _part("Connector_Generic:Conn_01x01", "J",
                 "Connector:Banana_Jack_1Pin",
                 "panel-mount-banana-4mm", "RESOLVED",
                 f"DUT banana jack: {function_name}",
                 extra_aliases={function_name: 1})


def USB_C_Receptacle():
    # USB2.0 USB-C receptacle. The GCT USB4085 footprint has 17 distinct pad
    # names (A1 A4 A5 A6 A7 A8 A9 A12 B1 B4 B5 B6 B7 B8 B9 B12 S1); the
    # _16P symbol variant declares exactly those 17 pins, while _14P omits
    # A8/B8 (SBU1/SBU2) and would leave two pads permanently unnetted.
    return _part("Connector:USB_C_Receptacle_USB2.0_16P", "J",
                 "Connector_USB:USB_C_Receptacle_GCT_USB4085",
                 "GCT USB4085", "RESOLVED",
                 "USB-C receptacle, USB 2.0, 16 signal pins + shield",
                 aliases={"DP": ["A6", "B6"], "DM": ["A7", "B7"]})


# =====================================================================
#  POWER / REFERENCE
# =====================================================================

def LD1117S33():
    return _part("Regulator_Linear:LD1117S33TR_SOT223", "U",
                 "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
                 "LD1117S33TR", "RESOLVED", "3.3V 800mA LDO",
                 aliases={"VOUT": 2, "VIN": 3},
                 keywords="LDO regulator 3V3")


def REF5025():
    # MPN REF5025AIDGKT -> A grade, DGK (VSSOP-8) package -> REF5025ADGK.
    return _part("Reference_Voltage:REF5025ADGK", "U",
                 "Package_SO:VSSOP-8_3x3mm_P0.65mm",
                 "REF5025AIDGKT", "RESOLVED",
                 "2.500V precision voltage reference",
                 aliases={"VIN": 2, "TEMP": 3, "TRIM/NR": 5, "VOUT": 6,
                          "NC1": 1, "NC2": 7, "NC3": 8},
                 keywords="voltage reference 2.5V")


# =====================================================================
#  DAC / SWEEP
# =====================================================================

def DAC8552():
    return _part("Analog_DAC:DAC8552", "U",
                 "Package_SO:VSSOP-8_3x3mm_P0.65mm",
                 "DAC8552IDGKR", "RESOLVED",
                 "16-bit dual voltage-output DAC, SPI",
                 aliases={"VDD": 1, "VREF": 2, "VOUTB": 3, "VOUTA": 4,
                          "SYNC": 5, "DIN": 7},
                 keywords="DAC 16-bit dual SPI")


def OPA2277():
    return _part("Amplifier_Operational:OPA2277", "U",
                 "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                 "OPA2277UA", "RESOLVED", "Dual precision op-amp",
                 aliases={"OUTA": 1, "-INA": 2, "+INA": 3,
                          "+INB": 5, "-INB": 6, "OUTB": 7},
                 keywords="opamp precision dual")


# =====================================================================
#  POWER BUFFER
# =====================================================================

def OPA548():
    # No OPA548 symbol exists in the official KiCad libraries, so this is the
    # project-local symbol in libraries/stage1_iv.kicad_sym. Its pin numbers
    # come from a published third-party KiCad library, NOT from TI SBOS070,
    # which is unreachable from this environment — hence VERIFY-DATASHEET.
    # See libraries/provenance/opa548t_akl_symbol.txt.
    return _part("stage1_iv:OPA548T", "U",
                 "Package_TO_SOT_THT:TO-220-7_P2.54x3.7mm"
                 "_StaggerOdd_Lead3.8mm_Vertical",
                 "OPA548T", "VERIFY-DATASHEET",
                 "High-current power op-amp with current limit",
                 aliases={"+IN": 1, "-IN": 2, "ILIM": 3, "OUT": 6},
                 keywords="power opamp current limit")


# =====================================================================
#  INSTRUMENTATION AMPLIFIERS (current + voltage sense)
# =====================================================================

def INA128P():
    # The official symbol is Amplifier_Instrumentation:INA128 (there is no
    # "INA128P"); the P suffix is the DIP-8 package option, carried in the MPN.
    return _part("Amplifier_Instrumentation:INA128", "U",
                 "Package_DIP:DIP-8_W7.62mm",
                 "INA128P", "RESOLVED",
                 "Instrumentation amplifier, G=1+50k/RG",
                 aliases={"RG1": 1, "-IN": 2, "+IN": 3, "REF": 5,
                          "OUT": 6, "RG2": 8},
                 keywords="instrumentation amplifier inamp")


# =====================================================================
#  ADC
# =====================================================================

def ADS1256():
    # Analog_ADC has no ADS1256 (only ADS1251), so this is the project-local
    # symbol authored directly from the TI data sheet: SBAS288K, "Terminal
    # Functions" table, page 5, column "TERMINAL NO. / ADS1256".
    # Package is SSOP-28 (TI DB, R-PDSO-G28, JEDEC MO-150) per the data
    # sheet's MECHANICAL DATA page — NOT the 4.4 mm-wide TSSOP-28.
    return _part("stage1_iv:ADS1256", "U",
                 "Package_SO:SSOP-28_5.3x10.2mm_P0.65mm",
                 "ADS1256IDBR", "RESOLVED",
                 "24-bit delta-sigma ADC, 8ch, PGA, SPI",
                 # XTAL1/CLKIN (19) and SYNC/PDWN (14) are single pads whose
                 # official names the blocks read directly; the halves are kept
                 # addressable so an older reference still resolves to the pad.
                 extra_aliases={"XTAL1": 19, "CLKIN": 19,
                                "SYNC": 14, "PDWN": 14},
                 keywords="ADC 24-bit delta-sigma SPI")


# =====================================================================
#  MCU
# =====================================================================

def STM32F411RET6():
    # The official symbol already uses the design's own pin names (PA5, PB13,
    # VDD, VSS, VCAP1, NRST, BOOT0, PH0, PH1 ...), so no aliases are needed.
    # VDD is four pads (19/32/48/64) and VSS four (18/31/47/63); addressing
    # U9["VDD"] returns all of them, so every rail pad gets connected.
    return _part("MCU_ST_STM32F4:STM32F411RETx", "U",
                 "Package_QFP:LQFP-64_10x10mm_P0.5mm",
                 "STM32F411RET6", "RESOLVED",
                 "ARM Cortex-M4 MCU, 2x SPI, USB OTG FS",
                 keywords="STM32 MCU Cortex-M4")


# =====================================================================
#  USB ESD PROTECTION
# =====================================================================

def USBLC6_2SC6():
    # Pins 1/6 are the two ends of I/O1 and pins 3/4 the two ends of I/O2;
    # the design routes the signal in on one end and out on the other, so the
    # aliases keep those two ends distinguishable by name.
    return _part("Power_Protection:USBLC6-2SC6", "U",
                 "Package_TO_SOT_SMD:SOT-23-6",
                 "USBLC6-2SC6", "RESOLVED", "USB ESD protection array",
                 aliases={"IO1": 1, "IO2": 3, "IO2b": 4, "IO1b": 6},
                 keywords="ESD USB protection")


# =====================================================================
#  ALIAS DOCUMENTATION
# =====================================================================
# Every design-facing pin name that is NOT the official symbol's own pin name.
# Format: factory -> {design name: (pin number, official symbol pin name)}.
ALIAS_DOCUMENTATION = {
    "C_POL / Device:C_Polarized": {
        "+": (1, "~ (anode; '+' glyph is drawn on the pin-1 side)"),
        "-": (2, "~ (cathode; filled bar)"),
    },
    "BananaJack / Connector_Generic:Conn_01x01": {
        "<FORCE+|SENSE+|SENSE-|FORCE->": (1, "Pin_1"),
    },
    "USB_C_Receptacle / Connector:USB_C_Receptacle_USB2.0_16P": {
        "DP": ("A6 + B6", "D+  (both orientations)"),
        "DM": ("A7 + B7", "D-  (both orientations)"),
    },
    "LD1117S33 / Regulator_Linear:LD1117S33TR_SOT223": {
        "VOUT": (2, "VO"), "VIN": (3, "VI"),
    },
    "REF5025 / Reference_Voltage:REF5025ADGK": {
        "VIN": (2, "Vin"), "TEMP": (3, "Temp"), "TRIM/NR": (5, "Trim/NR"),
        "VOUT": (6, "Vout"), "NC1": (1, "DNC"), "NC2": (7, "NC"),
        "NC3": (8, "DNC"),
    },
    "DAC8552 / Analog_DAC:DAC8552": {
        "VDD": (1, "V_{DD}"), "VREF": (2, "V_{REF}"), "VOUTB": (3, "V_{OUT}B"),
        "VOUTA": (4, "V_{OUT}A"), "SYNC": (5, "~{SYNC}"), "DIN": (7, "D_{IN}"),
    },
    "OPA2277 / Amplifier_Operational:OPA2277": {
        "OUTA": (1, "~"), "-INA": (2, "-"), "+INA": (3, "+"),
        "+INB": (5, "+"), "-INB": (6, "-"), "OUTB": (7, "~"),
    },
    "OPA548 / stage1_iv:OPA548T": {
        "+IN": (1, "+"), "-IN": (2, "-"),
        "ILIM": (3, "CS  (current-limit set)"), "OUT": (6, "~"),
    },
    "INA128P / Amplifier_Instrumentation:INA128": {
        "RG1": (1, "Rg"), "-IN": (2, "-"), "+IN": (3, "+"), "REF": (5, "Ref"),
        "OUT": (6, "~"), "RG2": (8, "Rg"),
    },
    "ADS1256 / stage1_iv:ADS1256": {
        "XTAL1": (19, "XTAL1/CLKIN"), "CLKIN": (19, "XTAL1/CLKIN"),
        "SYNC": (14, "SYNC/PDWN"), "PDWN": (14, "SYNC/PDWN"),
    },
    "USBLC6_2SC6 / Power_Protection:USBLC6-2SC6": {
        "IO1": (1, "I/O1"), "IO2": (3, "I/O2"),
        "IO2b": (4, "I/O2"), "IO1b": (6, "I/O1"),
    },
}
