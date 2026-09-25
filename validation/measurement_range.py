#!/usr/bin/env python3
"""
measurement_range.py — can every point the source can reach be measured, signed?
===============================================================================

``validation/assertions.py`` says WHERE the measurement zero is (U6 REF, U7 REF
and the ADC's AINCOM all on VREF_2V5). This module says whether the NUMBERS
work. From the part values in the live circuit it recomputes

  * the sweep span, V_SWEEP = (R4/R3)(V_DAC - VREF/2);
  * the largest current a passive DUT can draw, |I| <= |V_SWEEP|/(R30 + R10):
    V and I share a sign in a passive DUT, and R30 + R10 sit outside the
    OPA548 loop, so this bound holds whatever the OPA548 current limit does;
  * both channels' transfer functions onto the ADS1256 inputs;

and fails if any of those operating points, at worst-case tolerance, would put
an ADC input outside its absolute window or the signal outside full scale.

It checks the NORMAL operating envelope only: a passive DUT, the source inside
its programmed span. Faults (external voltage on the jacks, a failed U5) are
the protection sheet's business, not this check's.

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

Engineering allowances (NOT datasheet values; stated so they can be argued)
--------------------------------------------------------------------------
SWEEP_TOL 0.5 %: 0.1 % R3-R6 (+/-0.2 % on the gain), REF5025 +/-0.1 %, and the
    DAC8552's full-scale error, rounded up.
UNSPECIFIED_R_TOL 1 %: R30 and R10 carry no tolerance in their values.
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

# ---- engineering allowances --------------------------------------------------
SWEEP_TOL = 0.005
UNSPECIFIED_R_TOL = 0.01


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


def ina_gain(rg, tol=0.0):
    """INA128 gain for RG (None = open), inflated by `tol` on the 50k term."""
    return 1.0 if rg is None else 1.0 + INA_RG_INTERNAL * (1 + tol) / rg


def compute(circuit):
    """Every number the check needs, from the live part values."""
    parts = {p.ref: p for p in circuit.parts}
    R = {ref: ohms(parts[ref]) for ref in
         ("R3", "R4", "R5", "R6", "R10", "R11", "R12", "R13", "R14", "R30",
          "R32", "R33")}
    problems = []

    # ---- source ------------------------------------------------------------
    if (R["R3"], R["R4"]) != (R["R5"], R["R6"]):
        problems.append(f"sweep difference amp unbalanced: R4/R3 = "
                        f"{R['R4']}/{R['R3']}, R6/R5 = {R['R6']}/{R['R5']}")
    g_sweep = R["R4"] / R["R3"]
    v_sweep = g_sweep * VREF_NOM / 2
    v_sweep_worst = v_sweep * (1 + SWEEP_TOL)
    r_out = R["R30"] + R["R10"]
    i_max = v_sweep / r_out
    r10_hi = R["R10"] * (1 + UNSPECIFIED_R_TOL)
    r30_lo = R["R30"] * (1 - UNSPECIFIED_R_TOL)
    i_max_worst = v_sweep_worst / (r30_lo + r10_hi)

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
        print(f"  source span ........ V_SWEEP = +/-{r['v_sweep']:.3f} V "
              f"(+/-{r['v_sweep_worst']:.3f} V worst case)")
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
