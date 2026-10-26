"""
nets.py — Global (cross-sheet) net definitions for the Stage 1 I–V system.
==========================================================================

In KiCad these are the hierarchical-sheet connector nets: names that cross
sheet boundaries. In SKiDL, nets merge by *connection*, not by name, so every
block must connect to the *same* Net objects. ``create_nets()`` builds all
global nets once; ``main.py`` passes the returned namespace into every block.

WHY THERE IS NO ``drive = POWER`` HERE ANY MORE
-----------------------------------------------
This module used to stamp every supply rail with ``Net.drive = POWER`` and
describe it as "the electrical equivalent of a KiCad PWR_FLAG". **It was not.**
``Net.drive`` is a runtime Python attribute read only by ``skidl.ERC()``. It is
never serialised into ``stage1_iv.net`` and never emitted into any
``.kicad_sch`` — the outputs contained zero power symbols of any kind. All it
did was stop SKiDL from reporting the eight undriven rails, while KiCad, which
never saw it, reported every one of them as a ``power_pin_not_driven`` error.
It converted a real finding into a silent pass.

The mechanism is now a real one: each externally-sourced rail carries a
**PWR_FLAG part** (``power:PWR_FLAG``, one ``power_out`` pin) instantiated in
the block where that rail physically enters the design — see
:data:`EXTERNALLY_SOURCED_RAILS` below for the list and ``blocks/power.py`` /
``blocks/mcu.py`` for the instances and their justifications. A PWR_FLAG is a
component: it appears in the netlist, it appears in the schematic, and both
SKiDL's ERC and KiCad's ERC can see it.

The set of names below is the authoritative "net manifest" required by the
spec; ``validation/assertions.py`` checks that each one exists and is populated.
"""

from types import SimpleNamespace
from skidl import Net


# Names that map 1:1 to a valid Python attribute.
_SIMPLE = [
    # ---- digital control ----
    "SCK1", "MOSI1", "CS_DAC",
    "SCK2", "MOSI2", "MISO2", "CS_ADC", "DRDY", "RESET_ADC",
    "SWDIO", "SWCLK", "NRST", "BOOT0",
    "USB_DM", "USB_DP", "VBUS", "VBUS_SENSE", "LED_STATUS",
    "VDDA",
    # charge-pump output (Sheet 2) -> DAC reference gate SEL (Sheet 3)
    "DAC_ARM",
    # ---- analog signal chain ----
    "V_DAC", "VREF_HALF", "V_SWEEP",
    "VO_RAW", "VO_SENSE",
    "I_SENSE_OUT", "V_SENSE_OUT",
    "F1_MID",
]

# Names containing characters illegal in Python identifiers → explicit mapping
# of attribute_name -> kicad_net_name.
_SPECIAL = {
    "P15VA": "+15VA",
    "N15VA": "-15VA",
    "P5VA": "+5VA",
    "P5V_RAW": "+5V_RAW",
    "P3V3D": "+3V3D",
    "AGND": "AGND",
    "DGND": "DGND",
    "VREF_2V5": "VREF_2V5",
    "FORCE_PLUS": "FORCE+",
    "FORCE_MINUS": "FORCE-",
    "SENSE_PLUS": "SENSE+",
    "SENSE_MINUS": "SENSE-",
}

# Rails whose energy originates OUTSIDE this board, so nothing on the board can
# drive them. Each one carries exactly one PWR_FLAG, instantiated at its
# physical point of entry in blocks/power.py or blocks/mcu.py (the reason for
# each is written there, next to the part).
#
# This is a declaration of intent, not a mechanism: it drives nothing at build
# time. validation/erc.py::check_power_flags asserts that the set of nets
# actually carrying a PWR_FLAG equals this set exactly, so the table cannot
# drift away from the circuit in either direction.
#
# Deliberately absent — these have a genuine on-board power_out driver, and a
# flag on them would mask a future wiring error:
#     +3V3D      driven by U1 (LD1117S33) VOUT
#     VREF_2V5   driven by U2 (REF5025)   Vout
EXTERNALLY_SOURCED_RAILS = {
    "P5V_RAW": "+5V_RAW",   # bench supply via J1.3
    "P15VA": "+15VA",       # bench supply via J1.1
    "N15VA": "-15VA",       # bench supply via J1.2
    "DGND": "DGND",         # bench supply return via J1.4
    "AGND": "AGND",         # analog return; separate net from DGND (R31 star)
    "P5VA": "+5VA",         # +5V_RAW through ferrite bead FB1
    "VBUS": "VBUS",         # USB host, via J3
    "VDDA": "VDDA",         # +3V3D through ferrite bead FB2
    # VREF_2V5 through the DAC reference gate U11 (passive switch pins), to
    # U4's power_in VREF pin. Local to Sheet 3, so its key is the bare name.
    "DAC_VREF": "DAC_VREF",
}


def create_nets():
    """Build every global net exactly once and return them in a namespace."""
    n = SimpleNamespace()
    for name in _SIMPLE:
        setattr(n, name, Net(name))
    for attr, kicad_name in _SPECIAL.items():
        setattr(n, attr, Net(kicad_name))
    return n


def nc(*pins):
    """Mark one or more pins as intentional no-connects (suppresses ERC).

    Accepts single pins and lists of pins, so ``nc(part["VSS"])`` works even
    when a name maps to several pads.
    """
    for p in pins:
        if isinstance(p, (list, tuple, set)):
            nc(*p)
        else:
            p += p.circuit.NC


def nc_named(part, *names):
    """No-connect every pin of `part` addressed by the given function names.

    Used to state, explicitly and greppably, that a pad is deliberately
    unconnected. Nothing may be left out silently:
    ``validation/footprint_check.py`` errors on any pad that is neither netted
    nor listed here.
    """
    for name in names:
        pin = part[name]
        if pin is None:
            raise KeyError(f"{part.ref}: no pin named {name!r} to no-connect")
        nc(pin)


# The canonical net manifest (KiCad net names), grouped, for validation/reports.
NET_MANIFEST = {
    "power": ["+15VA", "-15VA", "+5VA", "+3V3D", "AGND", "DGND", "VREF_2V5"],
    "digital": ["SCK1", "MOSI1", "CS_DAC", "SCK2", "MOSI2", "MISO2",
                "CS_ADC", "DRDY", "RESET_ADC"],
    "analog": ["V_SWEEP", "VO_SENSE", "FORCE+", "FORCE-", "SENSE+", "SENSE-",
               "I_SENSE_OUT", "V_SENSE_OUT"],
}
