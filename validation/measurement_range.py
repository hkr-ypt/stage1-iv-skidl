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
    V and I share a sign in a passive DUT, and R30 + R10 sit outside the
    OPA548 loop, so this bound holds whatever the OPA548 current limit does;
  * both channels' transfer functions onto the ADS1256 inputs;
  * the FORCE-path fault envelope: U5's output driven into a rail, current
    set only by R9 + F1 + R30 + R10. Every resistor in that path must be
    RATED (a "W" in its value or MPN) for its share of it;

and fails if any normal operating point, at worst-case tolerance, would put
an ADC input outside its absolute window or the signal outside full scale,
or if a FORCE-path resistor is under-rated for the fault envelope.

The measurement check is for the NORMAL operating envelope: a passive DUT and
the source inside its contracted span. External voltage on the jacks is the
protection sheet's business.

Datasheet limits (each is a constant below)
-------------------------------------------
ADS1256, TI SBAS288K, Electrical Characteristics:
    absolute input voltage, buffer off ... AGND - 0.1 V .. AVDD + 0.1 V
    full-scale input (AINP - AINN) ....... +/-2 VREF / PGA
    AVDD ................................. 4.75 .. 5.25 V
REF5025, TI SBOS410H: 2.500 V, initial accuracy +/-0.1 % (standard grade).
INA128, TI SBOS051G: G = 1 + 50 kOhm/RG; RTI offset 50 uV + 500 uV/G max
    (P grade). The 50 kOhm is internal, so G carries its tolerance: a +/-0.5 %
    allowance is used below.

OPA2277, TI SBOS079C: output swing at least (V+) - 1.2 V (RL = 10 kOhm).
Bel 0ZRM0010FF1E: resistance 3.0 .. 7.5 ohm (the low end maximises fault
    current).

Engineering allowances (NOT datasheet values; stated so they can be argued)
--------------------------------------------------------------------------
SWEEP_TOL 0.5 %: 0.1 % R1-R6 (+/-0.2 % on the gain), REF5025 +/-0.1 %, and
    the DAC8552's full-scale error, rounded up.
UNSPECIFIED_R_TOL 1 %: for any resistor whose value carries no tolerance.
RAIL_MAX 15.5 V: the +/-15 V bench supply, set high by ~3 %.
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
F1_R_MIN = 3.0              # 0ZRM0010FF1E minimum resistance, ohm

# ---- engineering allowances --------------------------------------------------
SWEEP_TOL = 0.005
UNSPECIFIED_R_TOL = 0.01
RAIL_MAX = 15.5             # +/-15 V bench rail, high
RAIL_NOM = 15.0
FORCE_PATH = ("R9", "R30", "R10")     # in series with F1 from U5 OUT to AGND


class MeasurementRangeError(AssertionError):
    """A normal operating point would over-range or leave the ADC window."""


_VALUE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([RkKM]?)")
_MULT = {"": 1.0, "R": 1.0, "k": 1e3, "K": 1e3, "M": 1e6}


def ohms(part):
    """'40.2k 0.1%' -> 40200.0; 'DNP (...)' -> None (an open pad pair)."""
    text = str(part.value)
    if text.strip().upper().startswith("DNP"):
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
         ("R1", "R2", "R3", "R4", "R5", "R6", "R9", "R10", "R11", "R12",
          "R13", "R14", "R30", "R32", "R33")}
    problems = []

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
    lo = {r: R[r] * (1 - tolerance(parts[r], UNSPECIFIED_R_TOL))
          for r in FORCE_PATH}
    i_fault = RAIL_MAX / (sum(lo.values()) + F1_R_MIN)
    fault = {}
    for ref in FORCE_PATH:
        p = i_fault ** 2 * R[ref] * (1 + tolerance(parts[ref],
                                                   UNSPECIFIED_R_TOL))
        rating = power_rating(parts[ref])
        fault[ref] = (p, rating)
        if rating is None:
            problems.append(f"{ref} ({parts[ref].value}): no power rating "
                            f"stated in its value or MPN; it carries "
                            f"{p:.2f} W in the FORCE-path fault envelope")
        elif rating < p:
            problems.append(f"{ref} rated {rating:g} W but dissipates "
                            f"{p:.2f} W with U5's output in a rail "
                            f"({i_fault * 1e3:.0f} mA)")

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
    for name, s, gain, unit, reach in (
            ("V", s_v, v_gain, "V", v_sweep),
            ("I", s_i, i_gain, "A", i_max)):
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
            "measurable": swing / gain,            # guaranteed, symmetric
            "fs_limit": (2 * VREF_NOM / PGA) / gain,
            "headroom": swing - s,
        })

    return {
        "problems": problems,
        "v_sweep": v_sweep, "v_sweep_worst": v_sweep_worst,
        "v_neg": v_neg, "v_pos": v_pos,
        "v_pos_uncontracted": v_pos_uncontracted,
        "i_fault": i_fault, "fault": fault,
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
        print(f"  FORCE-path fault ... U5 OUT in a {RAIL_MAX} V rail: "
              f"{r['i_fault'] * 1e3:.0f} mA; "
              + ", ".join(f"{ref} {p:.2f} W of {rating:g} W"
                          if rating else f"{ref} {p:.2f} W (unrated)"
                          for ref, (p, rating) in r["fault"].items()))
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
