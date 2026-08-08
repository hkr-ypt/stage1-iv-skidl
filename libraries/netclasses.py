"""
netclasses.py — board netclasses, derived from the design and the fab capability.
=================================================================================

Every net in :mod:`blocks.nets` is assigned to exactly one class. **There is no
default class**: :func:`classify` raises on an unassigned net, so adding a net
without deciding what it carries fails the build rather than silently inheriting
someone else's clearance.

Every number below cites its derivation — a row of ``docs/FAB_CONSTRAINTS.md``
(``F1``…``F12``), a package pitch, a current, or an impedance target. Nothing
here was arrived at by adjusting a value until DRC went quiet.

THE GLOBAL MINIMUM CLEARANCE
----------------------------
:data:`GLOBAL_MIN_CLEARANCE` is **0.13 mm**, and it is a DRC *floor*, not a
routing target.

Two footprints on this board have an intrinsic pad-to-pad gap of exactly
0.15 mm — measured, not assumed, by ``tools/measure_pad_gaps.py``:

    Connector_USB:USB_C_Receptacle_GCT_USB4085   pads A5/A6   0.150 mm   (J3)
    Package_SO:VSSOP-8_3x3mm_P0.65mm             pads 1/2     0.150 mm   (U2, U4)

That gap is a property of the parts, not of the layout: no routing decision can
widen it. A global minimum clearance above 0.15 mm therefore produces clearance
errors that are literally unfixable, because the violating pair of pads is
*inside a single footprint* and moving neither pad is an option.

0.13 mm sits **0.02 mm below** the intrinsic gap. The margin covers KiCad
evaluating roundrect and oval pads against their true outline rather than the
bounding box this project measures, so a pad pair that measures 0.150 mm cannot
land marginally under the limit.

Nothing is *routed* at 0.13 mm. The tightest clearance any net actually uses is
0.20 mm — above the fab floor F4 — and the unused ``Default`` class sits at F4
itself. The global floor exists only so that the footprints' own geometry is
legal.
"""

# ---------------------------------------------------------------------------
# Fabrication capability — the single source of these numbers is
# docs/FAB_CONSTRAINTS.md. Restated here so the generator can enforce them.
# ---------------------------------------------------------------------------
FAB = {
    "min_track": 0.15,          # F3
    "min_clearance": 0.15,      # F4
    "min_drill": 0.30,          # F5
    "min_annular_ring": 0.15,   # F6
    "min_via_pad": 0.60,        # F7  = F5 + 2 * F6
    "edge_clearance": 0.50,     # F8
    "silk_line_width": 0.15,    # F9
    "copper_oz": 1,             # F2
    "layers": 2,                # F1
}

# See the module docstring. Measured by tools/measure_pad_gaps.py.
INTRINSIC_MIN_PAD_GAP = 0.15
GLOBAL_CLEARANCE_MARGIN = 0.02
GLOBAL_MIN_CLEARANCE = round(INTRINSIC_MIN_PAD_GAP - GLOBAL_CLEARANCE_MARGIN, 3)


# ---------------------------------------------------------------------------
# Netclass definitions
# ---------------------------------------------------------------------------
# clearance / track_width / via_diameter / via_drill in mm.
# `derivation` is not decoration: validation/drc_check.py prints it, and
# docs/pcb_generation.md is generated from it.
NETCLASSES = {
    "POWER_HV": {
        "clearance": 0.30,
        "track_width": 0.50,
        "via_diameter": 0.80,
        "via_drill": 0.40,
        "derivation": (
            "clearance 0.30 mm = 2x fab floor F4 (0.15). IPC-2221B B1 external "
            "uncoated for 16-30 V is 0.13 mm, BELOW the fab floor, so IPC is "
            "not binding here; the doubling is margin for the 30 V rail-to-rail "
            "between +15VA and -15VA under assembly-residue variation. "
            "track 0.50 mm: OPA548 supply feed at I_LIMIT 49 mA is trivial for "
            "this width; 0.50 mm is chosen for low impedance on the buffer's "
            "supply rather than for current. via 0.8/0.4 mm >= F7."),
        "nets": ["+15VA", "-15VA"],
    },
    "POWER_LV": {
        "clearance": 0.20,
        "track_width": 0.40,
        "via_diameter": 0.70,
        "via_drill": 0.35,
        "derivation": (
            "clearance 0.20 mm = F4 + 0.05 mm margin; 5 V needs nothing more "
            "than the fab floor (IPC-2221B for <=15 V is 0.05 mm). "
            "track 0.40 mm: U1 LD1117 is rated 800 mA; at 1 oz copper (F2) "
            "0.40 mm carries ~1.1 A at a 10 C rise (IPC-2152 external), so "
            "0.40 mm covers the regulator's full rating with margin. "
            "via 0.7/0.35 mm >= F7."),
        "nets": ["+5V_RAW", "+5VA", "+3V3D", "VDDA", "VBUS"],
    },
    "GROUND": {
        "clearance": 0.20,
        "track_width": 0.40,
        "via_diameter": 0.70,
        "via_drill": 0.35,
        "derivation": (
            "Matches POWER_LV: ground returns carry the same currents as the "
            "rails they serve. Both nets are poured as zones (see "
            "generators/pcb.py), so track_width applies only to stitching and "
            "to the R31 star connection. AGND and DGND are SEPARATE zones "
            "joined only through R31 — validation/drc_check.py fails if they "
            "touch anywhere else."),
        "nets": ["AGND", "DGND"],
    },
    "FORCE": {
        "clearance": 0.25,
        "track_width": 0.50,
        "via_diameter": 0.80,
        "via_drill": 0.40,
        "derivation": (
            "The DUT drive path: OPA548 -> R9 -> F1 -> R30 -> D5 -> J4. "
            "clearance 0.25 mm for +/-11 V compliance with margin over F4. "
            "track 0.50 mm: OPA548 ILIM is set to ~49 mA (R7 = 1.43 M), but "
            "the fuse F1 holds 100 mA and the TVS D5 conducts fault current, "
            "so the track is sized for the FAULT case, not the operating one; "
            "0.50 mm at 1 oz carries ~1.4 A at a 10 C rise."),
        "nets": ["FORCE+", "FORCE-", "VO_RAW", "VO_SENSE", "F1_MID"],
    },
    "SENSE": {
        "clearance": 0.25,
        "track_width": 0.25,
        "via_diameter": 0.60,
        "via_drill": 0.30,
        "derivation": (
            "High-impedance precision analog: the Kelvin pair, the shared "
            "2.500 V reference, and the DAC/amplifier chain. Current is "
            "negligible (INA128 input bias is nA), so track 0.25 mm is chosen "
            "for etch consistency, not current. clearance 0.25 mm buys "
            "leakage margin around the high-Z nodes — at 25 C/50% RH the "
            "surface leakage across 0.25 mm of clean solder mask is well below "
            "the INA128's input bias, so it does not degrade the measurement. "
            "via 0.6/0.3 mm = F7 exactly (minimum via, to keep stubs short)."),
        "nets": ["SENSE+", "SENSE-", "VREF_2V5", "V_DAC", "VREF_HALF",
                 "V_SWEEP", "I_SENSE_OUT", "V_SENSE_OUT"],
    },
    "DIGITAL": {
        "clearance": 0.20,
        "track_width": 0.25,
        "via_diameter": 0.60,
        "via_drill": 0.30,
        "derivation": (
            "SPI, SWD, reset/boot and the status LED. SPI2 to the ADC runs at "
            "a few MHz; nothing here is impedance-controlled. clearance "
            "0.20 mm = F4 + margin; track 0.25 mm is the routing-density "
            "compromise for escaping the LQFP-64 (0.5 mm pitch, 0.20 mm "
            "intrinsic pad gap) and the SSOP-28. via 0.6/0.3 mm = F7."),
        "nets": ["SCK1", "MOSI1", "CS_DAC", "SCK2", "MOSI2", "MISO2",
                 "CS_ADC", "DRDY", "RESET_ADC", "SWDIO", "SWCLK", "NRST",
                 "BOOT0", "LED_STATUS", "VBUS_SENSE"],
    },
    "USB": {
        "clearance": 0.20,
        "track_width": 0.38,
        "via_diameter": 0.60,
        "via_drill": 0.30,
        "diff_pair_width": 0.38,
        "diff_pair_gap": 0.20,
        "derivation": (
            "USB 2.0 full-speed differential pair, 90 ohm target. On 1.6 mm "
            "FR-4 (F12), 2 layers (F1), 1 oz (F2), the ground pour on the "
            "opposite side puts the reference plane ~1.5 mm away, which cannot "
            "reach 90 ohm at any sane width — an edge-coupled pair at "
            "0.38 mm/0.20 mm is roughly 100-110 ohm here. THIS IS A KNOWN "
            "COMPROMISE of the 2-layer choice (F1), acceptable at full speed "
            "(12 Mbit/s) over the ~30 mm run from J3 to U9. If the design ever "
            "needs high speed, F1 changes to 4 layers and this class is "
            "recomputed. gap 0.20 mm >= F4. Route as a matched pair."),
        "nets": ["USB_DM", "USB_DP", "USB_DM_ESD", "USB_DP_ESD",
                 "USB_DM_CONN", "USB_DP_CONN"],
    },
    "LOCAL_ANALOG": {
        "clearance": 0.25,
        "track_width": 0.25,
        "via_diameter": 0.60,
        "via_drill": 0.30,
        "derivation": (
            "Block-local analog nodes that never leave one sheet: amplifier "
            "summing junctions, gain-set resistor nets, RC filter nodes, ADC "
            "input nets and the crystal nets. Same electrical character as "
            "SENSE (high-impedance, low-current) so the same numbers apply; "
            "kept as a separate class so the Kelvin/reference nets can be "
            "tightened later without touching these."),
        "nets": ["VCAP1", "OSC_IN", "OSC_OUT", "CC1", "CC2", "LED_A",
                 "VREF_HALF_RAW", "DIFF_P", "DIFF_N",
                 "OPA548_ILIM", "OPA548_ES",
                 "U6_RG1", "U6_RG2", "U7_RG1", "U7_RG2",
                 "SENSE_P_AMP", "SENSE_M_AMP",
                 "ADC_XTAL1", "ADC_XTAL2",
                 "ADC_AIN0", "ADC_AIN1", "ADC_AIN2", "ADC_AIN3"],
    },
}

# The KiCad "Default" class still has to exist and must be buildable, but no net
# is assigned to it: classify() raises instead.
DEFAULT_CLASS = {
    "clearance": FAB["min_clearance"],
    "track_width": FAB["min_track"],
    "via_diameter": FAB["min_via_pad"],
    "via_drill": FAB["min_drill"],
    "derivation": "KiCad requires a Default class. No net uses it — every net "
                  "is explicitly classified and classify() raises otherwise.",
}


class UnclassifiedNet(RuntimeError):
    """A net has no netclass. The board must not be generated."""


def _net_to_class():
    out = {}
    for name, spec in NETCLASSES.items():
        for net in spec["nets"]:
            if net in out:
                raise RuntimeError(
                    f"net {net!r} is in two netclasses: {out[net]} and {name}")
            out[net] = name
    return out


NET_TO_CLASS = _net_to_class()


def classify(net_names):
    """Map every net name to its class, or raise naming the offenders.

    ``net_names`` is every net in the built circuit. A net with no class is a
    hard failure: silently defaulting is how a 30 V rail ends up routed at the
    clearance of a 3.3 V signal.
    """
    unknown = sorted(n for n in net_names if n not in NET_TO_CLASS)
    if unknown:
        raise UnclassifiedNet(
            f"{len(unknown)} net(s) have no netclass: {unknown}\n"
            f"Every net must be classified in libraries/netclasses.py with a "
            f"written derivation for its clearance and track width. Decide "
            f"what the net carries; do not let it inherit a default.")
    return {n: NET_TO_CLASS[n] for n in net_names}


def check_against_fab():
    """No netclass may ask for something the fabricator cannot build."""
    problems = []
    for name, spec in list(NETCLASSES.items()) + [("Default", DEFAULT_CLASS)]:
        if spec["track_width"] < FAB["min_track"]:
            problems.append(f"{name}: track {spec['track_width']} < F3 "
                            f"{FAB['min_track']}")
        if spec["clearance"] < FAB["min_clearance"]:
            problems.append(f"{name}: clearance {spec['clearance']} < F4 "
                            f"{FAB['min_clearance']}")
        if spec["via_drill"] < FAB["min_drill"]:
            problems.append(f"{name}: via drill {spec['via_drill']} < F5 "
                            f"{FAB['min_drill']}")
        ring = (spec["via_diameter"] - spec["via_drill"]) / 2
        if ring < FAB["min_annular_ring"] - 1e-9:
            problems.append(f"{name}: annular ring {ring:.3f} < F6 "
                            f"{FAB['min_annular_ring']}")
    if GLOBAL_MIN_CLEARANCE >= INTRINSIC_MIN_PAD_GAP:
        problems.append(
            f"global minimum clearance {GLOBAL_MIN_CLEARANCE} is not below the "
            f"intrinsic pad gap {INTRINSIC_MIN_PAD_GAP}; the VSSOP-8 and "
            f"USB4085 footprints would report unfixable clearance errors")
    return problems
