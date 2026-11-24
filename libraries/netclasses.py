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
    "min_hole_to_hole": 0.25,   # F11

    "copper_oz": 1,             # F2
    "layers": 2,                # F1
}

# See the module docstring. Measured by tools/measure_pad_gaps.py.
INTRINSIC_MIN_PAD_GAP = 0.15
GLOBAL_CLEARANCE_MARGIN = 0.02
GLOBAL_MIN_CLEARANCE = round(INTRINSIC_MIN_PAD_GAP - GLOBAL_CLEARANCE_MARGIN, 3)

# The same argument, for drilled holes. J3's USB4085 is a 0.85 mm-pitch THT
# receptacle with 0.40 mm drills, leaving 0.45 mm between adjacent holes.
# That is a property of the connector; `min_hole_to_hole` above it produces
# hole_to_hole warnings on every adjacent pair of J3's pins that no layout
# decision can clear. Measured, not assumed.
INTRINSIC_MIN_HOLE_GAP = 0.45
HOLE_GAP_MARGIN = 0.02
GLOBAL_MIN_HOLE_TO_HOLE = round(INTRINSIC_MIN_HOLE_GAP - HOLE_GAP_MARGIN, 3)

# THE INTRA-FOOTPRINT CLEARANCE RULE
# ----------------------------------
# A global minimum below the intrinsic pad gap is NOT sufficient on its own,
# and assuming otherwise was a real defect in this project's first PCB pass.
# KiCad's global minimum is a floor on what a *rule* may ask for; the clearance
# actually enforced between two pads is the LARGER of the two nets' netclass
# clearances. Two adjacent VSSOP-8 pads on +5VA (POWER_LV, 0.20 mm) and
# VREF_2V5 (SENSE, 0.25 mm) are therefore checked at 0.25 mm against a 0.15 mm
# gap the package cannot change.
#
# The escape hatch is a scoped custom rule, generated into stage1_iv.kicad_dru
# by tools/gen_project_config.py: between two pads OF THE SAME FOOTPRINT, the
# required clearance is this value rather than the netclass value.
#
# This is not a suppression. Nothing is excluded and no severity is downgraded:
# the clearance rule still runs on those pads and still enforces a real number.
# It is narrowed only where the geometry is fixed by the part and no routing
# decision exists to satisfy the wider one. Pad-to-track, track-to-track and
# pad-to-pad *between different footprints* are untouched, and every one of
# those is a case where the layout genuinely can comply.
INTRA_FOOTPRINT_CLEARANCE = GLOBAL_MIN_CLEARANCE


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
            "track 0.50 mm: the U5 supply feed at I_LIMIT 49 mA is trivial for "
            "this width; 0.50 mm is chosen for low impedance on the buffer's "
            "supply rather than for current. via 0.8/0.4 mm >= F7."),
        "nets": ["+15VA", "-15VA",
                 # U12's LED string (blocks/protection.py) runs from +15VA to
                 # -15VA; its two inner nodes sit between the rails (about
                 # +10 V and +8.6 V), so they keep the rails' spacing.
                 "SSR_LED_A", "SSR_LED_K"],
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
        "nets": ["+5V_RAW", "+5VA", "+3V3D", "VDDA", "VBUS",
                 # STM32 VCAP_1: the 1.2 V core-regulator output and its
                 # 4.7 uF capacitor (C43, blocks/mcu.py). A low-impedance
                 # supply node, not a high-impedance analog one: it was in
                 # LOCAL_ANALOG by first classification only.
                 "VCAP1"],
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
            "The DUT drive path: U5 -> R9 -> F1 -> R30 -> U12 -> D5 -> "
            "J4, and the return J7 -> F2 -> R10. "
            "clearance 0.25 mm for +/-11 V compliance with margin over F4. "
            "track 0.50 mm: U5's ILIM is set to ~49 mA (R7), but "
            "the fuse F1 holds 100 mA and the TVS D5 conducts fault current, "
            "so the track is sized for the FAULT case, not the operating one; "
            "0.50 mm at 1 oz carries ~1.4 A at a 10 C rise."),
        "nets": ["FORCE+", "FORCE-", "VO_RAW", "VO_SENSE", "F1_MID",
                 # R30 -> U12 (photorelay) and F2 -> R10 (shunt high side):
                 # the same current and the same +/-11 V as FORCE+/FORCE-.
                 "FORCE_SW", "ISENSE_HI",
                 # U5 output snubber midpoint (R38/C49): sits on the
                 # +/-11 V output node, so it keeps the output's clearance.
                 "VO_SNUB"],
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
                 "V_SWEEP", "I_SENSE_OUT", "V_SENSE_OUT",
                 # The DAC's gated reference and its channel-B output (the
                 # sweep midpoint): the same precision chain as V_DAC.
                 "DAC_VREF", "V_DACB"],
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
                 "BOOT0", "LED_STATUS", "VBUS_SENSE",
                 # The status LED's cathode node, LED1 K to R23 (1 k) to
                 # PC13, which sinks it (blocks/mcu.py). 0..3.3 V switching,
                 # the same signal as LED_STATUS; "/mcu/LED_A" is its
                 # pre-audit board name (see LOCAL_ANALOG's old note), kept
                 # so the original board in unipolar-routed/ is classified
                 # the same way under this policy.
                 "LED_K", "/mcu/LED_A",
                 # USB-C CC1/CC2: 5.1 k Rd to DGND at the connector (sink
                 # role). 0..5 V configuration lines, not precision analog.
                 "CC1", "CC2",
                 # Safe-state heartbeat (PC5) through the charge pump to the
                 # DAC reference gate: 3.3 V logic levels, kHz edges.
                 "SAFE_HB", "SAFE_HB_R", "SAFE_HB_AC", "DAC_ARM"],
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
        # VCAP1, CC1/CC2, LED_K (and its pre-audit board name /mcu/LED_A)
        # were here by first classification. None is a high-impedance analog
        # node: VCAP1 is a regulator output (POWER_LV), CC1/CC2 are 5.1 k
        # USB-C configuration lines and LED_K is the status-LED logic sink
        # (both DIGITAL). Moved, with the reason at each new home.
        "nets": ["OSC_IN", "OSC_OUT",
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


# KiCad's own name for a net with exactly one pad on it, created for every
# schematic pin that drives nothing (see generators/schematic.py,
# unconnected_net_name). These are not a design decision to be justified: they
# carry no current between two points, they can never be routed, and KiCad
# itself leaves them in Default. Exempting them is not "letting a net inherit a
# default" — the rule below still applies to every net the design names.
UNCONNECTED_PREFIX = "unconnected-("


def is_unconnected_net(name):
    return name.startswith(UNCONNECTED_PREFIX) and name.endswith(")")


def classify(net_names):
    """Map every net name to its class, or raise naming the offenders.

    ``net_names`` is every net in the built circuit. A net with no class is a
    hard failure: silently defaulting is how a 30 V rail ends up routed at the
    clearance of a 3.3 V signal.
    """
    unknown = sorted(n for n in net_names
                     if n not in NET_TO_CLASS and not is_unconnected_net(n))
    if unknown:
        raise UnclassifiedNet(
            f"{len(unknown)} net(s) have no netclass: {unknown}\n"
            f"Every net must be classified in libraries/netclasses.py with a "
            f"written derivation for its clearance and track width. Decide "
            f"what the net carries; do not let it inherit a default.")
    return {n: NET_TO_CLASS.get(n, "Default") for n in net_names}


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
    if INTRA_FOOTPRINT_CLEARANCE >= INTRINSIC_MIN_PAD_GAP:
        problems.append(
            f"intra-footprint clearance {INTRA_FOOTPRINT_CLEARANCE} is not "
            f"below the intrinsic pad gap {INTRINSIC_MIN_PAD_GAP}; the custom "
            f"rule would not make those pad pairs satisfiable")
    if GLOBAL_MIN_HOLE_TO_HOLE >= INTRINSIC_MIN_HOLE_GAP:
        problems.append(
            f"min_hole_to_hole {GLOBAL_MIN_HOLE_TO_HOLE} is not below the "
            f"intrinsic hole gap {INTRINSIC_MIN_HOLE_GAP}; J3's USB4085 would "
            f"report unfixable hole_to_hole warnings")
    if INTRINSIC_MIN_HOLE_GAP < FAB["min_hole_to_hole"]:
        problems.append(
            f"a footprint on this board drills holes {INTRINSIC_MIN_HOLE_GAP} "
            f"mm apart, below the fabricator's F11 minimum "
            f"{FAB['min_hole_to_hole']}. That part cannot be built by this "
            f"process — change the part or the fabricator, not the rule.")
    return problems
