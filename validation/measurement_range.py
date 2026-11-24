#!/usr/bin/env python3
"""
measurement_range.py — can every point the source can reach be measured, signed?
===============================================================================

``validation/assertions.py`` says WHERE the measurement zero is (U6 REF, U7 REF
and the ADC's AINCOM all on VREF_2V5) and how the sweep midpoint is wired.
This module says whether the NUMBERS work. From the part values in the live
circuit it recomputes

  * the sweep span. The midpoint is DAC channel B halved by R1/R2, so
        V_SWEEP = (R4/R3) * (V_A - V_B * R2/(R1 + R2))
    with V_A, V_B in 0..VREF. Its NEGATIVE end (A = 0, B = full scale) is a
    hardware bound. Its POSITIVE end equals the negative only while firmware
    keeps B at full scale (the firmware contract, docs/firmware_handoff.md),
    so the checked envelope is that contract. What the hardware alone allows
    if the contract is broken is computed and REPORTED, not passed;
  * the largest current a passive DUT can draw, |I| <= |V_SWEEP|/(R30 + R10):
    V and I share a sign in a passive DUT, and R30 + R10 sit outside U5's
    loop, so this bound holds whatever U5's current limit does;
  * both channels' transfer functions onto the ADS1256 inputs;
  * the FORCE-path fault envelope: U5's output driven into a rail (ILIM
    ineffective, sweep saturated, or U5 failed), current set only by the
    series parts outside U5's loop, each at its guaranteed MINIMUM:
        DUT short ...... R9 + F1 + R30 + U12 + F2 + R10
        D5 shorted ..... R9 + F1 + R30 + U12   (FORCE+ straight to AGND)
    R30's minimum includes its TCR at the hottest film temperature its
    rating allows. Every resistor in that path must be RATED (a "W" in its
    value or MPN) for its share of it. This bound is the one the protection
    relies on; U5's current-limit plateau is reported beside it with its
    data-sheet spread and is not a bound;
  * that the ILIM setting is one U5 can actually deliver. The unit-to-unit
    tolerance of the limit is specified as a current, at one setting; the
    shift at this board's +/-15 V rails (the equation holds at +/-30 V) and
    the drift with case temperature are, on the OPA547's typical curves, the
    same number of mA at each of the three settings plotted (250..500 mA).
    Taken as currents at this board's much lower setting (an extrapolation,
    and the conservative reading), a setting no larger than their sum can
    come out at ZERO, i.e. an output stage that cannot drive the DUT at all.
    The build fails unless the plateau stays above ILIM_FLOOR with all of
    them against it: R7 at the high end of its tolerance, the rails at
    RAIL_MIN, the case at U5_CASE_HOT and the maximum unit tolerance. It
    also fails if the setting is so high that a TYPICAL unit would show no
    plateau at all into a shorted DUT (the series path, not U5, would set
    the current; the path is taken with R30, R10 and the sweep at the
    tolerance limits that pass least), if U5's value and order code name
    different parts, or if R7 is not fitted, by a 'DNP' value or by the
    dnp() mark (an open ILIM pin programs zero output current). The high
    end of the stack takes a 25 C case as the coldest; a colder case adds
    0.19 mA/K;
  * an external source on J7 (FORCE-): only F2 + R10 limit it, so R10's
    short-time overload rating must cover that current until F2 trips;

and fails if any normal operating point, at worst-case tolerance, would put
an ADC input outside its absolute window or the signal outside full scale,
if a FORCE-path resistor is under-rated for the fault envelope, or if any
resistor the calculation reads is not fitted (R14, U7's gain resistor, is
open by design and gives a gain of 1).

The measurement check is for the NORMAL operating envelope: a passive DUT and
the source inside its contracted span. External voltage on the jacks is the
protection sheet's business.

Datasheet limits (each is a constant below)
-------------------------------------------
ADS1256, TI SBAS288K, Electrical Characteristics:
    absolute input voltage, buffer off ... AGND - 0.1 V .. AVDD + 0.1 V
    full-scale input (AINP - AINN) ....... +/-2 VREF / PGA
    AVDD ................................. 4.75 .. 5.25 V
REF5025, TI SBOS410O: 2.500 V, initial accuracy +/-0.1 % (standard grade).
INA128, TI SBOS051G: G = 1 + 50 kOhm/RG; RTI offset 50 uV + 500 uV/G max
    (P grade). The 50 kOhm is internal, so G carries its tolerance: a +/-0.5 %
    allowance is used below.

OPA2277, TI SBOS079C: output swing at least (V+) - 1.2 V (RL = 10 kOhm).
OPA547, TI SBOS056F (the part fitted as U5): ILIM = 5000 * 4.75 V /
    (31600 + R_CL) at VS = +/-30 V and a 25 C case; tolerance +/-10 mA typ /
    +/-30 mA max, specified at R_CL = 31.6 k (375 mA). Typical curves
    "Current Limit vs Supply Voltage" and "Current Limit vs Temperature",
    read at R_CL = 15.9 k, 31.6 k and 63.4 k (500, 375, 250 mA): from +/-30 V
    to +/-15 V +ILIM falls 11.9 / 11.9 / 12.0 mA and -ILIM 4.0 / 4.1 / 4.1 mA
    (0.80 and 0.27 mA per volt of rail); from 20 C to 70 C the limit falls
    9.7 / 9.3 / 9.3 mA (0.19 mA/K; 0.14 mA/K above 70 C). The same mA at
    each of the three settings, so they are applied below as offsets. That
    carries TYPICAL curves from 250..500 mA down to a setting near 60 mA,
    below anything TI tabulates or plots (lowest: 100 mA); only the
    +/-30 mA is a data-sheet LIMIT, and it too is stated at 375 mA.
OPA548, TI SBOS070D (same pinout and package): ILIM = 15000 * 4.75 V /
    (13750 + R_CL); +/-100 mA typ / +/-250 mA max at R_CL = 14.8 k (2.5 A).
    Its curves (Fig. 3, 4) are not offsets of one size (at +/-15 V: -80 mA at
    the 1 A setting, 0 at 2.5 A, -50 mA at 4 A; +0.65..0.95 mA/K), so none is
    applied here: the tolerance alone decides.
    TI's OPA549 data sheet says of that part's limit that "offset in the
    current limit circuitry may introduce approximately +/-0.25 A variation
    at low current limit values". TI publishes no such statement for the
    OPA547 or OPA548; applying their tolerance as an absolute current, not
    as a percentage of the setting, is the conservative reading.
Bel 0ZRM0010FF1E (F1, F2): resistance 3.0 .. 7.5 ohm (the low end maximises
    fault current; the high end gives the least current into a short);
    Imax 2 A.
Vishay PR02 (R30, doc. 28729): TCR +/-250 ppm/K, Rth 75 K/W.
Vishay RS02B (R10, doc. 30204): short-time overload 5 x rated power, 5 s.
Toshiba TLP176AM (U12): RON <= 2 ohm, no minimum (taken as 0 ohm); ION
    0.7 A.

Engineering allowances (NOT datasheet values; stated so they can be argued)
--------------------------------------------------------------------------
SWEEP_TOL 0.5 %: 0.1 % R1-R6 (+/-0.2 % on the gain), REF5025 +/-0.1 %, and
    the DAC8552's full-scale error, rounded up.
UNSPECIFIED_R_TOL 1 %: for any resistor whose value carries no tolerance.
RAIL_MAX 15.5 V, RAIL_MIN 14.5 V: the +/-15 V bench supply, set high or low
    by ~3 %.
U5_CASE_HOT 85 C: the top of the OPA547's specified range. A unit at the
    low end of the plateau spread dissipates under 0.6 W in limit and stays
    below it up to about 48 C ambient (65 K/W, no heat sink).
ILIM_FLOOR 5 mA: what the worst-case plateau must still exceed. It covers
    what the stack does not model: 1 uA of leakage at the ILIM node (R7 is
    348 k; flux residue) moves the limit by about 4.6 mA.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---- datasheet limits -------------------------------------------------------
VREF_NOM = 2.500            # REF5025
VREF_TOL = 0.001            # REF5025 standard grade, +/-0.1 %
AVDD_MIN = 4.75             # ADS1256 analog supply, minimum
ABS_MARGIN = 0.1            # ADS1256 buffer off: AGND - 0.1 .. AVDD + 0.1
INA_RG_INTERNAL = 50e3      # INA128 gain equation
INA_G_TOL = 0.005           # allowance on the internal 50 kOhm
INA_VOS_IN, INA_VOS_OUT = 50e-6, 500e-6   # INA128P max RTI: 50 uV + 500 uV/G

# ---- design choices checked here ---------------------------------------------
PGA = 2                     # one PGA for both channels (see blocks/adc.py)

U3_SWING_MIN = 1.2          # OPA2277: output reaches at least (V+) - 1.2 V
PTC_R_MIN = 3.0             # 0ZRM0010FF1E minimum resistance, ohm (F1, F2)
PTC_R_MAX = 7.5             # ... and its maximum (R1max), ohm
PTC_I_MAX = 2.0             # 0ZRM0010FF1E Imax, A
R30_TCR = 250e-6            # PR02, per K
R30_RTH = 75.0              # PR02 hot-spot thermal resistance, K/W
R10_OVERLOAD_X = 5.0        # RS02B short-time overload, x rated power, 5 s
U12_RON_MIN = 0.0           # TLP176AM: no minimum RON is specified
U12_RON_MAX = 2.0           # TLP176AM RON max, ohm
U12_ION_MAX = 0.7           # TLP176AM ON-state current, A
# U5 current limit, by fitted part (read from U5's value, which must be the
# whole orderable name and agree with the MPN field):
#   k, r0  : ILIM = K / (R0 + R_CL), valid at ILIM_EQ_RAIL and ILIM_EQ_CASE;
#   tol    : unit-to-unit tolerance there (typ, max), A;
#   supply : typical change of (+ILIM, -ILIM) per volt each rail is below
#            ILIM_EQ_RAIL, A/V;
#   temp   : typical drift per K of case temperature, A/K (header).
ILIM_PARTS = {
    "OPA547": {"k": 5000 * 4.75, "r0": 31600.0, "tol": (0.010, 0.030),
               "supply": (-0.80e-3, -0.27e-3), "temp": -0.19e-3,
               "doc": "SBOS056F"},
    "OPA548": {"k": 15000 * 4.75, "r0": 13750.0, "tol": (0.100, 0.250),
               "supply": (0.0, 0.0), "temp": 0.0,
               "doc": "SBOS070D"},
}
ILIM_EQ_RAIL = 30.0         # the limit equation's supply condition, +/-V
ILIM_EQ_CASE = 25.0         # ... and case temperature, C
U5_CASE_HOT = 85.0          # hottest U5 case allowed for (its specified range)
U5_NAME = re.compile(r"(OPA54[78])T")   # the stagger-formed TO-220 (KVT) parts

# ---- engineering allowances --------------------------------------------------
SWEEP_TOL = 0.005
UNSPECIFIED_R_TOL = 0.01
RAIL_MAX = 15.5             # +/-15 V bench rail, high
RAIL_NOM = 15.0
RAIL_MIN = 14.5             # ... and low
ILIM_FLOOR = 5e-3           # least acceptable worst-case plateau, A (header)
FORCE_PATH = ("R9", "R30", "R10")     # in series with F1, U12, F2 to AGND


class MeasurementRangeError(AssertionError):
    """A normal operating point would over-range or leave the ADC window."""


_VALUE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([RkKM]?)")
_MULT = {"": 1.0, "R": 1.0, "k": 1e3, "K": 1e3, "M": 1e6}


def ohms(part):
    """'40.2k 0.1%' -> 40200.0; None for an open pad pair: a value that
    starts with 'DNP', or a part marked with libraries.custom_symbols.dnp()
    (which leaves the value text as it was)."""
    text = str(part.value)
    if text.strip().upper().startswith("DNP") or getattr(part, "dnp", False):
        return None
    m = _VALUE.match(text)
    if not m:
        raise MeasurementRangeError(f"{part.ref}: cannot read a resistance "
                                    f"from value {text!r}")
    return float(m.group(1)) * _MULT[m.group(2)]


def tolerance(part, default):
    m = re.search(r"([0-9.]+)\s*%", str(part.value))
    return float(m.group(1)) / 100.0 if m else default


_WATTS = re.compile(r"(?<![0-9.])([0-9]+(?:\.[0-9]+)?|[0-9]+/[0-9]+)\s*W\b")


def power_rating(part):
    """Rated power in W from the value, else the MPN field ('2W', '1/4W',
    '0.5W'); None when neither states one."""
    for text in (str(part.value), part.fields.get("MPN", "")):
        m = _WATTS.search(text or "")
        if m:
            num = m.group(1)
            if "/" in num:
                a, b = num.split("/")
                return float(a) / float(b)
            return float(num)
    return None


def ina_gain(rg, tol=0.0):
    """INA128 gain for RG (None = open), inflated by `tol` on the 50k term."""
    return 1.0 if rg is None else 1.0 + INA_RG_INTERNAL * (1 + tol) / rg


def compute(circuit):
    """Every number the check needs, from the live part values."""
    parts = {p.ref: p for p in circuit.parts}
    R = {ref: ohms(parts[ref]) for ref in
         ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R9", "R10", "R11",
          "R12", "R13", "R14", "R30", "R32", "R33")}
    problems = []
    # R14 (U7's RG) is DNP by design: an open RG is a gain of 1. An open R7
    # is judged below. Every other resistor read here must be fitted,
    # R11 (U6's RG, which sets the current channel's design gain of 2.4)
    # included: the calculation that follows depends on each of them.
    unfitted = sorted(ref for ref, value in R.items()
                      if value is None and ref not in ("R7", "R14"))
    if unfitted:
        raise MeasurementRangeError(
            f"{', '.join(unfitted)} not fitted (DNP): this check's "
            f"calculation of the source span, the fault envelope and the "
            f"measurement channels depends on "
            f"{'it' if len(unfitted) == 1 else 'them'}")

    # ---- source ------------------------------------------------------------
    if (R["R3"], R["R4"]) != (R["R5"], R["R6"]):
        problems.append(f"sweep difference amp unbalanced: R4/R3 = "
                        f"{R['R4']}/{R['R3']}, R6/R5 = {R['R6']}/{R['R5']}")
    g_sweep = R["R4"] / R["R3"]
    k_mid = R["R2"] / (R["R1"] + R["R2"])          # V_B -> VREF_HALF
    # Firmware contract: B at full scale (V_B = VREF). Negative end: A = 0.
    v_neg = g_sweep * (0.0 - VREF_NOM * k_mid)
    v_pos = g_sweep * (VREF_NOM - VREF_NOM * k_mid)
    if abs(v_pos + v_neg) > 1e-9 * max(1.0, abs(v_neg)):
        problems.append(f"sweep span asymmetric with B at full scale: "
                        f"{v_neg:+.3f} .. {v_pos:+.3f} V (R1 = {R['R1']}, "
                        f"R2 = {R['R2']}: the midpoint is not V_B/2)")
    v_sweep = max(abs(v_neg), abs(v_pos))
    v_sweep_worst = v_sweep * (1 + SWEEP_TOL)
    # Out of contract (B below full scale): A = FS, B = 0 commands G * VREF;
    # U3B clips somewhere between its guaranteed swing and the +15 V rail.
    v_pos_uncontracted = min(g_sweep * VREF_NOM, RAIL_NOM)
    r_out = R["R30"] + R["R10"]
    i_max = v_sweep / r_out
    r10_hi = R["R10"] * (1 + tolerance(parts["R10"], UNSPECIFIED_R_TOL))
    r30_lo = R["R30"] * (1 - tolerance(parts["R30"], UNSPECIFIED_R_TOL))
    i_max_worst = v_sweep_worst / (r30_lo + r10_hi)

    # ---- FORCE-path fault envelope: U5 OUT driven into a rail --------------
    rmin = {r: R[r] * (1 - tolerance(parts[r], UNSPECIFIED_R_TOL))
          for r in FORCE_PATH}
    # R30 hot: the film may run up to rating x Rth above ambient, and a
    # negative TCR lowers it by that much again.
    r30_rating = power_rating(parts["R30"]) or 0.0
    rmin["R30"] *= 1 - R30_TCR * r30_rating * R30_RTH
    r30_min = rmin["R30"]
    # U5 OUT -> R9 -> F1 -> R30 -> U12 -> FORCE+
    r_plus = rmin["R9"] + PTC_R_MIN + rmin["R30"] + U12_RON_MIN
    i_fault_dut = RAIL_MAX / (r_plus + PTC_R_MIN + rmin["R10"])   # via F2, R10
    i_fault_gnd = RAIL_MAX / r_plus                              # D5 shorted
    i_fault = i_fault_dut
    i_plus = max(i_fault_dut, i_fault_gnd)
    fault = {}
    for ref in FORCE_PATH:
        # Worst dissipation in `ref` with everything else at its minimum:
        # V^2 R / (R + rest)^2 over R's own range (one value, not the low end
        # for the current and the high end for the power at once). R10 is in
        # the DUT-short path only; R9 and R30 are worst with D5 shorted.
        rest = (r_plus + PTC_R_MIN + rmin["R10"] if ref == "R10"
                else r_plus) - rmin[ref]
        r_hi = R[ref] * (1 + tolerance(parts[ref], UNSPECIFIED_R_TOL))
        r_star = min(max(rest, rmin[ref]), r_hi)
        p = RAIL_MAX ** 2 * r_star / (r_star + rest) ** 2
        i = RAIL_MAX / (r_star + rest)
        rating = power_rating(parts[ref])
        fault[ref] = (p, rating)
        if rating is None:
            problems.append(f"{ref} ({parts[ref].value}): no power rating "
                            f"stated in its value or MPN; it carries "
                            f"{p:.2f} W in the FORCE-path fault envelope")
        elif rating < p:
            problems.append(f"{ref} rated {rating:g} W but dissipates "
                            f"{p:.2f} W with U5's output in a rail "
                            f"({i * 1e3:.0f} mA)")
    if i_plus > U12_ION_MAX:
        problems.append(f"U12 carries {i_plus * 1e3:.0f} mA in the fault "
                        f"envelope, above its {U12_ION_MAX * 1e3:.0f} mA ION")
    # External source of the rails' own magnitude on J7: F2 + R10 only.
    i_ext = RAIL_MAX / (PTC_R_MIN + rmin["R10"])
    p_ext = i_ext ** 2 * R["R10"] * (1 + tolerance(parts["R10"],
                                                   UNSPECIFIED_R_TOL))
    r10_rating = power_rating(parts["R10"]) or 0.0
    if i_ext > PTC_I_MAX:
        problems.append(f"external {RAIL_MAX} V on J7 drives {i_ext:.2f} A "
                        f"through F2, above its {PTC_I_MAX} A Imax")
    if p_ext > R10_OVERLOAD_X * r10_rating:
        problems.append(f"external {RAIL_MAX} V on J7 puts {p_ext:.1f} W in "
                        f"R10 until F2 trips, above its short-time overload "
                        f"rating ({R10_OVERLOAD_X:g} x {r10_rating:g} W)")
    # U5's current limit: a plateau, never relied on as a bound. It must
    # still be a setting the fitted amplifier can deliver (header).
    u5_value = str(parts["U5"].value).strip()
    u5_mpn = str(parts["U5"].fields.get("MPN", "") or "").strip()
    m = U5_NAME.fullmatch(u5_value)
    if m is None:
        raise MeasurementRangeError(
            f"U5 value {u5_value!r} is not the orderable name of an amplifier "
            f"whose current-limit equation is known here "
            f"({', '.join(name + 'T' for name in sorted(ILIM_PARTS))})")
    u5_family = m.group(1)
    if u5_mpn != u5_value:
        problems.append(
            f"U5 value {u5_value!r} and its MPN field {u5_mpn!r} differ: the "
            f"current limit is checked for one part and another is ordered")
    lim = ILIM_PARTS[u5_family]
    ilim_typ, ilim_max = lim["tol"]
    # A dead short across the jacks at full sweep: what the series path
    # (outside U5's loop) passes with U5 NOT limiting. A unit whose limit is
    # above this shows no plateau; that is reported, and is a failure only
    # if a typical unit would do it. i_short is the range with the PTCs and
    # U12 at their limits and the resistors nominal (the figure reported);
    # i_short_floor is its low end with R30, R10 and the sweep at their
    # tolerance limits as well, which is what the typical-unit test uses.
    i_short = (v_sweep / (PTC_R_MAX + R["R30"] + U12_RON_MAX + PTC_R_MAX
                          + R["R10"]),
               v_sweep / (PTC_R_MIN + R["R30"] + U12_RON_MIN + PTC_R_MIN
                          + R["R10"]))
    r30_hi = R["R30"] * (1 + tolerance(parts["R30"], UNSPECIFIED_R_TOL))
    i_short_floor = (v_sweep * (1 - SWEEP_TOL)
                     / (PTC_R_MAX + r30_hi + U12_RON_MAX + PTC_R_MAX + r10_hi))
    if R["R7"] is None:
        problems.append(
            f"R7 is not fitted: an open ILIM pin programs zero output "
            f"current ({lim['doc']}), i.e. U5 unable to drive the DUT")
        i_ilim = 0.0
        ilim_cold = ilim_hot = (0.0, 0.0)
        ilim_lo = ilim_hi = ilim_typ_hi = 0.0
    else:
        r7_tol = tolerance(parts["R7"], UNSPECIFIED_R_TOL)
        eq = lambda r_cl: lim["k"] / (lim["r0"] + r_cl)
        i_ilim = eq(R["R7"])                           # the equation value
        # At this board's nominal rails: (+ILIM, -ILIM), case at 25 C and at
        # its hottest. Reported as the typical expectation.
        shift = lambda rail: tuple(per_volt * (ILIM_EQ_RAIL - rail)
                                   for per_volt in lim["supply"])
        d_hot = lim["temp"] * (U5_CASE_HOT - ILIM_EQ_CASE)
        ilim_cold = tuple(i_ilim + d for d in shift(RAIL_NOM))
        ilim_hot = tuple(i + d_hot for i in ilim_cold)
        # The extremes: every term at the end that hurts. Low: R7 high, the
        # rail (low or high) and the case (25 C or hot) that give less, the
        # weaker polarity, the maximum unit tolerance against. High: the
        # opposite of each. The typical-unit high end takes the typical
        # tolerance instead.
        corners = [eq(r7) + d + dt
                   for r7 in (R["R7"] * (1 + r7_tol), R["R7"] * (1 - r7_tol))
                   for rail in (RAIL_MIN, RAIL_MAX)
                   for d in shift(rail)
                   for dt in (0.0, d_hot)]
        ilim_lo = min(corners) - ilim_max
        ilim_hi = max(corners) + ilim_max
        ilim_typ_hi = max(corners) + ilim_typ
        terms = [f"R7 +{r7_tol * 100:g} %"]
        if any(lim["supply"]):
            terms.append(f"+/-{RAIL_MIN:g} V rails")
        if lim["temp"]:
            terms.append(f"an {U5_CASE_HOT:g} C case")
        terms.append(f"the +/-{ilim_max * 1e3:.0f} mA maximum tolerance")
        if ilim_lo < ILIM_FLOOR:
            problems.append(
                f"U5 ({u5_value}) current limit: R7 sets "
                f"{i_ilim * 1e3:.1f} mA by the {lim['doc']} equation, but "
                f"with {', '.join(terms[:-1])} and {terms[-1]} the plateau "
                f"can be as low as "
                f"{ilim_lo * 1e3:.1f} mA, below the {ILIM_FLOOR * 1e3:g} mA "
                f"floor: the real limit can be zero, i.e. U5 unable to drive "
                f"the DUT")
        if ilim_typ_hi > i_short_floor:
            problems.append(
                f"U5 ({u5_value}) current limit: R7 sets "
                f"{i_ilim * 1e3:.1f} mA by the {lim['doc']} equation, so a "
                f"typical unit can limit as high as "
                f"{ilim_typ_hi * 1e3:.0f} mA, but the series path passes only "
                f"{i_short_floor * 1e3:.0f} mA into a shorted DUT: no "
                f"current-limit plateau")

    # ---- voltage channel: Kelvin attenuator -> U7 (G from R14) -------------
    if (R["R12"], R["R32"]) != (R["R13"], R["R33"]):
        problems.append(f"Kelvin attenuator legs differ: R12/R32 = "
                        f"{R['R12']}/{R['R32']}, R13/R33 = "
                        f"{R['R13']}/{R['R33']}")
    k = R["R32"] / (R["R12"] + R["R32"])
    tol_k = max(tolerance(parts[r], UNSPECIFIED_R_TOL) for r in ("R12", "R32"))
    k_worst = k * (1 + 2 * tol_k * (1 - k))
    g_v = ina_gain(R["R14"])
    g_v_worst = ina_gain(R["R14"], INA_G_TOL)
    v_gain = g_v * k                                   # V at ADC per V at DUT
    s_v = g_v_worst * k_worst * v_sweep_worst + (INA_VOS_IN * g_v + INA_VOS_OUT)

    # ---- current channel: R10 shunt -> U6 (G from R11) ---------------------
    g_i = ina_gain(R["R11"])
    g_i_worst = ina_gain(R["R11"] * (1 - tolerance(parts["R11"], 0.01)),
                         INA_G_TOL)
    i_gain = g_i * R["R10"]                            # V at ADC per A
    # Worst case: sweep high, R10 high, R30 low (more current AND more V/A).
    s_i = (g_i_worst * v_sweep_worst * r10_hi / (r30_lo + r10_hi)
           + (INA_VOS_IN * g_i + INA_VOS_OUT))

    # ---- ADC window ----------------------------------------------------------
    vref_lo = VREF_NOM * (1 - VREF_TOL)
    vref_hi = VREF_NOM * (1 + VREF_TOL)
    full_scale = 2 * vref_lo / PGA
    pin_hi = AVDD_MIN + ABS_MARGIN
    pin_lo = -ABS_MARGIN
    # The signal sits on VREF_2V5 (INA REF) and is read against VREF_2V5
    # (AINCOM), so the pin excursion is VREF +/- s.
    swing = min(pin_hi - vref_hi, vref_lo - pin_lo, full_scale)

    channels = []
    # (gain at its limit, offset at its limit): what the measurable range is
    # when the channel itself, not only the ADC window, is at worst case.
    v_lim = (g_v_worst * k_worst, INA_VOS_IN * g_v + INA_VOS_OUT)
    i_lim = (g_i_worst * r10_hi, INA_VOS_IN * g_i + INA_VOS_OUT)
    for name, s, gain, unit, reach, (gain_hi, offset) in (
            ("V", s_v, v_gain, "V", v_sweep, v_lim),
            ("I", s_i, i_gain, "A", i_max, i_lim)):
        hi, lo = vref_hi + s, vref_lo - s
        if hi > pin_hi:
            problems.append(f"{name} channel: AIN reaches {hi:.3f} V at the "
                            f"positive extreme, above AVDD + 0.1 = "
                            f"{pin_hi:.2f} V")
        if lo < pin_lo:
            problems.append(f"{name} channel: AIN reaches {lo:.3f} V at the "
                            f"negative extreme, below AGND - 0.1 V")
        if s > full_scale:
            problems.append(f"{name} channel: |AIN - AINCOM| reaches {s:.3f} V,"
                            f" beyond PGA = {PGA} full scale "
                            f"+/-{full_scale:.3f} V")
        channels.append({
            "name": name, "unit": unit, "gain": gain,
            "reach": reach,                        # nominal physical extreme
            "adc_at_reach": gain * reach,          # nominal differential
            "worst_signal": s,
            "pin_span": (lo, hi),
            # ADC window at its limits (AVDD_MIN, VREF tolerance), channel
            # gain NOMINAL: the figure the documents quote.
            "measurable": swing / gain,
            # ... and with the channel gain and offset at their limits too.
            "measurable_limits": (swing - offset) / gain_hi,
            "fs_limit": (2 * VREF_NOM / PGA) / gain,
            "headroom": swing - s,
        })

    return {
        "problems": problems,
        "v_sweep": v_sweep, "v_sweep_worst": v_sweep_worst,
        "v_neg": v_neg, "v_pos": v_pos,
        "v_pos_uncontracted": v_pos_uncontracted,
        "i_fault": i_fault, "i_fault_gnd": i_fault_gnd, "fault": fault,
        "r30_lo": r30_min, "i_ext": i_ext, "p_ext": p_ext,
        "r10_overload": R10_OVERLOAD_X * r10_rating, "i_ilim": i_ilim,
        "u5": u5_value, "ilim_doc": lim["doc"], "ilim_typ": ilim_typ,
        "ilim_max": ilim_max, "ilim_cold": ilim_cold, "ilim_hot": ilim_hot,
        "ilim_range": (ilim_lo, ilim_hi), "i_short": i_short,
        "i_short_floor": i_short_floor,
        "i_max": i_max, "i_max_worst": i_max_worst,
        "k": k, "g_v": g_v, "g_i": g_i,
        "full_scale": full_scale, "pin_window": (pin_lo, pin_hi),
        "channels": {c["name"]: c for c in channels},
    }


def _fmt(value, unit):
    if unit == "A":
        return f"{value * 1e3:.1f} mA"
    return f"{value:.2f} V"


def run_measurement_range(circuit, verbose=True):
    """Recompute the ranges and raise if any normal operating point fails."""
    r = compute(circuit)
    v, i = r["channels"]["V"], r["channels"]["I"]
    if verbose:
        print("\n" + "=" * 70)
        print("MEASUREMENT RANGE (bipolar, from live part values)")
        print("=" * 70)
        print(f"  source span ........ V_SWEEP = {r['v_neg']:+.3f} .. "
              f"{r['v_pos']:+.3f} V (+/-{r['v_sweep_worst']:.3f} V worst "
              f"case)")
        print(f"      negative end: hardware bound (A = 0, B = full scale)")
        print(f"      positive end: FIRMWARE CONTRACT (B = full scale). "
              f"Broken, the hardware allows up to "
              f"~{r['v_pos_uncontracted']:+.1f} V (U3B swing >= +"
              f"{RAIL_NOM - U3_SWING_MIN:.1f} V, rail +{RAIL_NOM:.0f} V): "
              f"not measurable, see docs/firmware_handoff.md §3")
        print(f"  FORCE-path fault ... U5 OUT in a {RAIL_MAX} V rail, "
              f"every series part at its minimum (R30 hot: "
              f"{r['r30_lo']:.1f} ohm, U12 0 ohm): DUT short "
              f"{r['i_fault'] * 1e3:.0f} mA, D5 shorted "
              f"{r['i_fault_gnd'] * 1e3:.0f} mA; "
              + ", ".join(f"{ref} {p:.2f} W of {rating:g} W"
                          if rating else f"{ref} {p:.2f} W (unrated)"
                          for ref, (p, rating) in r["fault"].items()))
        lo, hi = r["ilim_range"]
        (cold_p, cold_n), (hot_p, hot_n) = r["ilim_cold"], r["ilim_hot"]
        print(f"  U5 ILIM ............ {r['u5']}, R7: {r['i_ilim'] * 1e3:.1f} "
              f"mA by the {r['ilim_doc']} equation (+/-{ILIM_EQ_RAIL:g} V, "
              f"{ILIM_EQ_CASE:g} C). At +/-{RAIL_NOM:g} V, typical: "
              f"+{cold_p * 1e3:.1f} / -{cold_n * 1e3:.1f} mA cold, "
              f"+{hot_p * 1e3:.1f} / -{hot_n * 1e3:.1f} mA with the case at "
              f"{U5_CASE_HOT:g} C; +/-{r['ilim_typ'] * 1e3:.0f} mA typ, "
              f"+/-{r['ilim_max'] * 1e3:.0f} mA max between units. With R7's "
              f"tolerance and +/-{RAIL_MIN:g} .. {RAIL_MAX:g} V rails as "
              f"well: {lo * 1e3:.1f} .. {hi * 1e3:.1f} mA in all (must stay "
              f"above {ILIM_FLOOR * 1e3:g} mA). The supply and temperature "
              f"terms are typical curves extrapolated to this setting, not "
              f"limits. A plateau, NOT a bound (the passive bound above "
              f"holds with ILIM at any value)")
        print(f"      a shorted DUT at full sweep passes "
              f"{r['i_short'][0] * 1e3:.0f} .. {r['i_short'][1] * 1e3:.0f} "
              f"mA through the series parts alone ({r['i_short_floor'] * 1e3:.1f} "
              f"mA with R30, R10 and the sweep at their limits as well): a "
              f"unit limiting above that shows no plateau into a short "
              f"(reported; a failure only if a typical unit would limit "
              f"above {r['i_short_floor'] * 1e3:.1f} mA)")
        print(f"  external on J7 ..... {RAIL_MAX} V through F2 + R10: "
              f"{r['i_ext']:.2f} A (F2 Imax {PTC_I_MAX:g} A), R10 "
              f"{r['p_ext']:.1f} W until F2 trips (short-time rating "
              f"{r['r10_overload']:g} W for 5 s)")
        print(f"  current bound ...... |I| <= V_SWEEP/(R30+R10) = "
              f"{r['i_max'] * 1e3:.1f} mA ({r['i_max_worst'] * 1e3:.1f} mA "
              f"worst case), any passive DUT, any ILIM")
        print(f"  ADC ................ PGA = {PGA}: full scale "
              f"+/-{r['full_scale']:.3f} V; pins "
              f"{r['pin_window'][0]:.2f} .. {r['pin_window'][1]:.2f} V "
              f"(AVDD = {AVDD_MIN} V)")
        for c, what in ((v, f"k = {r['k']:.5f}, G = {r['g_v']:.4f}"),
                        (i, f"G = {r['g_i']:.4f}")):
            per = "V/V" if c["unit"] == "V" else "V/A"
            print(f"  {c['name']} channel .......... {c['gain']:.5f} {per} "
                  f"({what})")
            print(f"      AIN span at the physical extremes (worst case) "
                  f"{c['pin_span'][0]:.3f} .. {c['pin_span'][1]:.3f} V, "
                  f"headroom {c['headroom'] * 1e3:.0f} mV")
            print(f"      measurable +/-{_fmt(c['measurable'], c['unit'])} "
                  f"guaranteed (+/-{_fmt(c['fs_limit'], c['unit'])} at "
                  f"nominal full scale) vs +/-"
                  f"{_fmt(c['reach'], c['unit'])} reachable")
            print(f"      (that takes the channel gain at its nominal value; "
                  f"with gain and offset at their limits as well: +/-"
                  f"{_fmt(c['measurable_limits'], c['unit'])}. Both need "
                  f"AVDD >= {AVDD_MIN} V at U8)")
    if r["problems"]:
        raise MeasurementRangeError(
            f"{len(r['problems'])} measurement-range problem(s):\n  "
            + "\n  ".join(r["problems"]))
    if verbose:
        print("\nEvery reachable operating point is measurable: PASS")
    r["v_text"] = f"+/-{_fmt(v['measurable'], 'V')}"
    r["i_text"] = f"+/-{_fmt(i['measurable'], 'A')}"
    return r


def main():
    import main as project_main
    from validation.assertions import (
        a_bipolar_measurement_zero, a_kelvin_attenuator,
    )
    n, _parts, circuit = project_main.build()
    # The arithmetic below assumes this wiring; check it first.
    for fn in (a_bipolar_measurement_zero, a_kelvin_attenuator):
        name, ok, detail = fn(n, circuit)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         {detail}")
        if not ok:
            print(f"\nMEASUREMENT RANGE: FAIL — {name}", file=sys.stderr)
            return 1
    try:
        run_measurement_range(circuit)
    except MeasurementRangeError as exc:
        print(f"\nMEASUREMENT RANGE: FAIL\n{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
