"""
nets.py — Global (cross-sheet) net definitions for the Stage 1 I–V system.
==========================================================================

In KiCad these are the hierarchical-sheet connector nets: names that cross
sheet boundaries. In SKiDL, nets merge by *connection*, not by name, so every
block must connect to the *same* Net objects. ``create_nets()`` builds all
global nets once; ``main.py`` passes the returned namespace into every block.

Power/ground rails are stamped with ``drive = POWER`` so SKiDL ERC treats them
as driven (the electrical equivalent of a KiCad PWR_FLAG) and does not report
"no driving pins" on rails fed from the bench supply.

The set of names below is the authoritative "net manifest" required by the
spec; ``validation/assertions.py`` checks that each one exists and is populated.
"""

from types import SimpleNamespace
from skidl import Net, POWER


# Names that map 1:1 to a valid Python attribute.
_SIMPLE = [
    # ---- digital control ----
    "SCK1", "MOSI1", "CS_DAC",
    "SCK2", "MOSI2", "MISO2", "CS_ADC", "DRDY", "RESET_ADC",
    "SWDIO", "SWCLK", "NRST", "BOOT0",
    "USB_DM", "USB_DP", "VBUS", "VBUS_SENSE", "LED_STATUS",
    "VDDA",
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

# Rails that must be marked as driven (power/ground) for ERC. VBUS is supplied
# externally by the USB host, so it is marked driven as well.
_POWER_RAILS = [
    "P15VA", "N15VA", "P5VA", "P5V_RAW", "P3V3D", "VDDA", "VBUS",
    "AGND", "DGND",
]


def create_nets():
    """Build every global net exactly once and return them in a namespace."""
    n = SimpleNamespace()
    for name in _SIMPLE:
        setattr(n, name, Net(name))
    for attr, kicad_name in _SPECIAL.items():
        setattr(n, attr, Net(kicad_name))

    # Mark supply/ground rails as driven so ERC does not flag them.
    for attr in _POWER_RAILS:
        getattr(n, attr).drive = POWER

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
