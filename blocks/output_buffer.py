"""
output_buffer.py — SHEET 4: Power Buffer (Voltage Source Output Stage)
=====================================================================

OPA547 power buffer with native current limit (R7 sets the plateau, ~49 mA
in this board's conditions, spec §C.4) and an in-the-loop output isolation
resistor R9 (spec §C.6).

WHY U5 IS AN OPA547T, NOT AN OPA548T (final audit)
--------------------------------------------------
The OPA547, OPA548 and OPA549 are one family: same pinout, same 7-lead
stagger-formed TO-220 (TI package KVT), same current-limit scheme
(I_LIM = K * 4.75 V / (R_int + R_CL)), different output-current class:

    part     limit range   K      R_int    limit tolerance (TI, 25 C)
    OPA547   0..750 mA     5000   31.6 k   +/-10 mA typ, +/-30 mA max at 375 mA
    OPA548   0..5 A        15000  13.75 k  +/-100 mA typ, +/-250 mA max at 2.5 A

The errors of this limit circuit are currents, not percentages. TI's OPA549
data sheet says of it that "offset in the current limit circuitry may
introduce approximately +/-0.25 A variation at low current limit values" and
sends lower-current applications to the smaller parts, and the typical
curves of both smaller parts show the same thing: the change of the limit
with supply voltage and with temperature is the same number of mA at every
setting plotted. This design needs a ~49 mA plateau. On an OPA548 that is
1 % of the range (R7 was 1.43 M, I_SET = 3.3 uA of 330 uA), well inside the
+/-100 mA typical tolerance, with a +0.8 mA/K drift and, at the lowest
setting TI plots, 80 mA less limit at +/-15 V than at +/-30 V: the real
limit could sit anywhere from zero (U5 unable to drive the DUT at all, in
one polarity or both) to far above the passive 91 mA bound (no plateau;
F1/F2 running near their hold current). TI's own OPA548 macromodel does not
limit at all below ~87 mA with that resistor.

THE SETTING: R7 = 348 k
-----------------------
The OPA547 equation holds at VS = +/-30 V and a 25 C case. This board runs
U5 from +/-15 V, self-heated. From SBOS056F's typical curves (Current Limit
vs Supply Voltage / vs Temperature, each read at 250, 375 and 500 mA):

    +/-30 V -> +/-15 V:   +ILIM -12 mA,  -ILIM -4 mA
    case temperature:     -0.19 mA/K  (-9 mA from 25 C to 70 C)
    unit to unit:         +/-10 mA typical, +/-30 mA maximum

    I_LIM(equation) = 5000 * 4.75 V / (31.6 k + 348 k) = 62.6 mA

    expected plateau, +/-15 V       sourcing     sinking
      case 25 C (cold, pulsed)       50.6 mA      58.5 mA
      case 45 C (idle, warm)         46.8 mA      54.7 mA
      case 70 C (held in limit)      42.0 mA      50.0 mA
    all errors against it (85 C case, -30 mA unit):  9 mA
    all errors with it (25 C case, +30 mA unit):    89 mA

So the plateau is centred on the intended 49 mA where the board actually
operates, it is above zero for every unit (the hard requirement: a limit at
zero is an instrument with no output), and it stays below the passive
bound (91 mA) and inside the measurable range (+/-97.8 mA).
validation/measurement_range.py recomputes this on every build and fails if
the low end is not above zero. 453 k, the value the bare equation gives for
49 mA, fails it: its low end is -4 mA. The plateau is not a precision
quantity on any of these parts; bring-up measures it in both polarities
and firmware uses the measured value. To move one unit's plateau, change
R7: about 6 k per mA (larger R7 = less current). TI's OPA547 macromodel
gives +74 / -67 mA with 348 k at +/-15 V (+75 / -73 mA at +/-30 V: it
carries a +10..12 mA offset of its own, which is within the typical
tolerance, and has no temperature dependence); the data sheet's measured
typical curves are taken as the better guide, and the design holds for
either.

Nothing else changes with the part: output swing (V+) - 1.8 V / (V-) + 1.2 V
at 0.1 A (guaranteed, SBOS056F) against the 10.95 V this stage needs; input
range (V+) - 3 V; 1 MHz GBW; unity-gain stable; E/S and thermal shutdown as
before; tab on V-. Offset (5 mV max) and quiescent current (15 mA max) are
lower than the OPA548's (10 mV, 20 mA). Its 0.5 A continuous rating is far
above the 143 mA fault envelope that R30 sets (Sheet 8). The schematic symbol
and the nets keep their OPA548 names (the pinout is identical and
tools/author_project_symbols.py cross-checks it against BOTH data sheets);
the fitted part is the value and MPN of U5.

Feedback (VO_SENSE) is tapped on the FAR side of R9 so the amplifier regulates
the voltage delivered past the isolation resistor. R9's drop is NOT corrected
downstream — which is exactly why the independent Kelvin sense (Sheet 6) exists.

E/S is tied permanently enabled through R8 to +15VA (MCU-driven shutdown is a
deferred extension point, spec §C.6). E/S is NOT a safe-state mechanism: a
disabled output still leaks (SBOS056F "Output leakage current", typical),
which into an open or high-impedance DUT is not "off". The safe state is the
DAC reference gate on Sheet 3. The pin may be taken to V+ (absolute maximum
"Input shutdown voltage: V+").

R9 (10 ohm, in the loop): 1206, +/-1 %, >= 0.25 W. Its worst steady
dissipation is 0.21 W, with U5's output driven into a rail, FORCE+ shorted to
AGND and every series part at its minimum (validation/measurement_range.py
recomputes it on every build against the 0.25 W rating).

SNUBBER (audit V1) — R38 + C49, FITTED ONLY IF NEEDED
----------------------------------------------------
TI recommends an output R-C snubber when these amplifiers are driven into
current limit: 3 ohm + 0.01 uF for the OPA547 (SBOS056F Fig. 11), 3..10 ohm
+ 0.01..0.1 uF for the OPA548 (SBOS070D §9.1, Fig. 38). This design sits in
current limit by intent, but U5 never sees the DUT cable directly:
F1 + R30 (>= 103 ohm) isolate it, so its load is resistive, and TI's OPA547
macromodel stays quiet on the plateau with 100 pF..1 uF at the jacks (final
audit). The footprints are on the board, from U5 OUT (VO_RAW) to AGND, and
the parts are DNP: the bring-up checklist looks for oscillation at the
current-limit plateau with a capacitive load, and fits them if it is seen
(start from TI's 3 ohm + 0.01 uF). A pad pair is cheap; adding a snubber to a
board without one is not.

C15/C16 sit on the +/-15 V rails and are rated 50 V X7R (audit: >= 25 V).
"""

from libraries.custom_symbols import OPA548, R, C, C_POL, TestPoint, dnp
from skidl import Net


def create_output_buffer_block(n):
    U5 = OPA548()(ref="U5", tag="U5", value="OPA547T")
    # R_CL for the OPA547 (header): 62.6 mA by the data-sheet equation, which
    # is ~47 mA sourcing / ~55 mA sinking at +/-15 V with U5 warm.
    R7 = R()(ref="R7", tag="R7", value="348k 1%",
             footprint="Resistor_SMD:R_1206_3216Metric")
    R8 = R()(ref="R8", tag="R8", value="10k")
    C14 = C()(ref="C14", tag="C14", value="0.01uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    C15 = C()(ref="C15", tag="C15", value="100nF 50V X7R",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    C16 = C()(ref="C16", tag="C16", value="100nF 50V X7R",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    C17 = C_POL("Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B")(
        ref="C17", tag="C17", value="10uF/35V")
    C18 = C_POL("Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B")(
        ref="C18", tag="C18", value="10uF/35V")
    R9 = R()(ref="R9", tag="R9", value="10R 1% 0.25W",
             footprint="Resistor_SMD:R_1206_3216Metric")
    C19 = C()(ref="C19", tag="C19", value="100pF")
    TP2 = TestPoint()(ref="TP2", tag="TP2", value="VO_SENSE")
    R38 = dnp(R()(ref="R38", tag="R38", value="10R",
                  footprint="Resistor_SMD:R_0805_2012Metric"))
    C49 = dnp(C()(ref="C49", tag="C49", value="10nF 50V X7R",
                  footprint="Capacitor_SMD:C_0805_2012Metric"))

    # ---- input ----
    n.V_SWEEP += U5["+IN"]

    # ---- supplies + decoupling ----
    n.P15VA += U5["V+"], C15[1], C17["+"]
    n.AGND += C15[2], C17["-"]
    n.N15VA += U5["V-"], C16[1], C18["-"]
    n.AGND += C16[2], C18["+"]

    # ---- current limit (R7 to -15VA, C14 across R7) ----
    ilim = Net("OPA548_ILIM"); ilim += U5["ILIM"], R7[1], C14[1]
    n.N15VA += R7[2], C14[2]

    # ---- enable/shutdown tied permanently enabled ----
    es = Net("OPA548_ES"); es += U5["E/S"], R8[1]
    n.P15VA += R8[2]

    # ---- output with in-the-loop isolation resistor R9 ----
    n.VO_RAW += U5["OUT"], R9[1], C19[1]
    n.VO_SENSE += R9[2], C19[2], TP2[1], U5["-IN"]   # feedback from FAR side of R9

    # ---- output snubber, DNP (see header) ----
    n.VO_RAW += R38[1]
    snub = Net("VO_SNUB"); snub += R38[2], C49[1]
    n.AGND += C49[2]

    return {
        "U5": U5, "R7": R7, "R8": R8, "C14": C14, "C15": C15, "C16": C16,
        "C17": C17, "C18": C18, "R9": R9, "C19": C19, "TP2": TP2,
        "R38": R38, "C49": C49,
    }
