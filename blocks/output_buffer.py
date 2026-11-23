"""
output_buffer.py — SHEET 4: Power Buffer (Voltage Source Output Stage)
=====================================================================

OPA547 power buffer with native current limit (R7 sets the plateau: design
centre ~49 mA, expected 45..65 mA in this board's conditions, spec §C.4), an
in-the-loop output isolation resistor R9 (spec §C.6) and an output R-C
network R38/C49.

WHY U5 IS AN OPA547T, NOT AN OPA548T (final audit)
--------------------------------------------------
The OPA547, OPA548 and OPA549 are one family: same pinout, same 7-lead
stagger-formed TO-220 (TI package KVT), same current-limit scheme
(I_LIM = K * 4.75 V / (R_int + R_CL)), different output-current class:

    part     limit range   K      R_int    limit tolerance (TI)
    OPA547   0..750 mA     5000   31.6 k   +/-10 mA typ, +/-30 mA max at 375 mA
    OPA548   0..5 A        15000  13.75 k  +/-100 mA typ, +/-250 mA max at 2.5 A

TI states that tolerance as a current, at one setting. For the largest part
it says what happens lower down: "offset in the current limit circuitry may
introduce approximately +/-0.25 A variation at low current limit values"
(OPA549, SBOS093E), and it sends lower-current applications to the smaller
parts. It publishes no such statement for the OPA547 or the OPA548; taking
their tolerance as an absolute current at a low setting is the conservative
reading, and it is the one used here. This design needs a ~49 mA plateau.
On an OPA548 that is 1 % of the range (R7 was 1.43 M, I_SET = 3.3 uA of
330 uA) and a fifth of the +/-250 mA maximum tolerance; its typical curves
add about +0.8 mA/K and, at the lowest setting TI plots (1 A), 80 mA less
limit at +/-15 V than at +/-30 V. The real limit could sit anywhere from
zero (U5 unable to drive the DUT at all, in one polarity or both) to far
above what the series resistors pass (no plateau; F1/F2 running near their
hold current). TI's own OPA548 macromodel does not limit at all below
~87 mA with that resistor. No report of an OPA548 used at tens of mA was
found, for or against: this rests on TI's figures, not on an observed
failure.

THE SETTING: R7 = 348 k
-----------------------
The OPA547 equation holds at VS = +/-30 V and a 25 C case. This board runs
U5 from +/-15 V, self-heated. SBOS056F's typical curves (Current Limit vs
Supply Voltage / vs Temperature) are drawn for 250, 375 and 500 mA, and on
each of the three:

    +/-30 V -> +/-15 V:   +ILIM -12 mA,  -ILIM -4 mA
    case temperature:     -0.19 mA/K  (-9 mA from 25 C to 70 C)
    unit to unit:         +/-10 mA typical, +/-30 mA maximum (at 375 mA)

    I_LIM(equation) = 5000 * 4.75 V / (31.6 k + 348 k) = 62.6 mA

    with those shifts applied as currents   sourcing     sinking
      case 25 C (cold, pulsed)               50.6 mA      58.5 mA
      case 45 C (idle, warm)                 46.8 mA      54.7 mA
      case 70 C (held in limit)              42.0 mA      50.0 mA
    everything against it (R7 +1 %, +/-14.5 V, 85 C case, -30 mA):   8 mA
    everything with it    (R7 -1 %, +/-15.5 V, 25 C case, +30 mA):  89 mA

What this is, and is not. Only the +/-30 mA is a data-sheet limit, and it is
stated at 375 mA. The supply and temperature figures are TYPICAL curves, the
three of them one drawn shape, and 62.6 mA is below every setting TI
tabulates or plots (lowest: 100 mA). Carrying them down as fixed currents
is an extrapolation, chosen because it is the pessimistic one for the
question that matters: can the limit come out at zero? If the shifts
instead scaled with the setting, the plateau would be near +61 / -62 mA.
So: EXPECTED plateau 45..65 mA (the table above at its low end, the bare
equation at its high end); with the +/-30 mA unit tolerance a board can
come out anywhere between the two ends of the stack; and the stack stays
8 mA clear of zero. The plateau is not a precision quantity on any part of
this family: bring-up measures it in both polarities and firmware uses the
measured value.

validation/measurement_range.py recomputes the stack on every build and
fails if its low end is under 5 mA, or if a typical unit would limit above
what the series path passes into a shorted DUT (79..87 mA; then there is no
plateau). 453 k, the value the bare equation gives for 49 mA, fails the
first test (-5 mA); values below 301 k fail the second. The upper end of the
maximum-tolerance stack (89 mA) is above 79..87 mA: a unit in the top few
mA shows no plateau into a dead short, which is harmless (R30 bounds the
current) and is reported, not failed. To move one unit's plateau change R7:
about 6 k per mA (larger R7 = less current); 332 k is the next step if a
unit's sourcing plateau is too low. TI's OPA547 macromodel gives +74 / -67
mA with 348 k at +/-15 V (+75 / -73 mA at +/-30 V): it reads 5..12 mA above
the equation and has no temperature dependence, so it is not evidence for
the value either way.

WHAT THE OPA547 COSTS
---------------------
Equal or better than the OPA548 here: output swing (V+) - 1.8 V / (V-) +
1.2 V at 0.1 A (guaranteed, SBOS056F) against the 10.95 V this stage needs;
input range (V+) - 3 V; offset (5 mV max against 10 mV); quiescent current
(15 mA max against 20 mA); bias current, noise, 1 MHz GBW, E/S and thermal
shutdown the same; tab on V-. Lower, and of no consequence in this stage:
slew rate 6 V/us (U3 upstream gives 0.8 V/us), 0.5 A continuous (the fault
envelope is 0.143 A, Sheet 8), specified temperature range -40..+85 C.

Lower, and it matters for what is hung on the output: unity-gain margin
with a capacitive load. SBOS056F "Small-Signal Overshoot vs Load
Capacitance", G = +1: about 10 % with no load capacitance and 50 % at
4.5 nF (the OPA548: none up to 2.5 nF, 50 % at 11.7 nF). This stage is a
unity-gain follower. As built, U5's direct load is tens of pF (TP2, the
-IN pin, tracks), and the DUT and its cable sit behind F1 + R30
(>= 102 ohm), which makes them a resistive load at the frequencies that
decide stability. So: do not connect a capacitance (a coaxial cable, a x1
probe, a filter capacitor) directly to VO_RAW or TP2/VO_SENSE; use a x10
probe. TI's macromodels do not reproduce these curves (the OPA547 model
shows under 1 % overshoot at any load), so they say nothing about this
margin.

The schematic symbol and the nets keep their OPA548 names (the pinout is
identical and tools/author_project_symbols.py cross-checks it against BOTH
data sheets); the fitted part is the value and MPN of U5.

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

OUTPUT R-C NETWORK — R38 100 ohm + C49 10 nF, FITTED (final audit)
----------------------------------------------------------------
TI: "if the OPA547 is intended to be driven into current limit, a R/C
network may be required" (SBOS056F, its example: 3 ohm + 0.01 uF, Fig. 11).
This design is driven into current limit by intent. The pads were added by
the first audit (V1) and left unfitted, with 10 ohm + 10 nF to be fitted if
bring-up saw oscillation. Two things changed that:

  * On the plateau the output is a current source, and the limiter's loop
    gain follows the impedance at the output node. A resistive or capacitive
    DUT leaves it flat or falling; an INDUCTIVE one (a winding, a coil, a
    wire-wound resistor: roughly 30 uH .. 10 mH with a few ohms) makes it
    rise with frequency. In TI's OPA547 macromodel the plateau then
    oscillates, up to rail to rail in the negative polarity, with no network
    fitted. With 10 nF behind 100 ohm from VO_RAW to AGND it is quiet in
    every case run: 10 uH .. 100 mH with 0.3 .. 20 ohm in series, both
    polarities, and resistive and capacitive DUTs as before; 47 and 220 ohm
    did the same where they were run (docs/final_audit.md §4.1).
    The bring-up test that was to decide the matter used capacitors across
    the jacks, which is the one condition that hides it.
  * The values on the pads were wrong for this amplifier. TI's 3 ohm +
    10 nF is shown on an inverting gain-of-4 stage. On a unity-gain OPA547 a
    10 nF capacitor behind 3 or 10 ohm is still a capacitive load at the
    1 MHz crossover (10 nF is 16 ohm there), and TI's own curve puts 4.5 nF
    at 50 % overshoot: an amplifier model fitted to that curve loses 25..35
    degrees of phase margin with either. Behind 100 ohm the network is a
    resistor where it matters: 100 ohm in parallel with the >= 112 ohm DUT
    path is 53 ohm, the load TI characterises the part with (RL = 50 ohm for
    gain-bandwidth, slew rate and the open-loop gain/phase plot). The same
    fitted model puts the phase margin within 2 degrees of the unfitted
    stage, or better, for any DUT and for an output resistance half or
    twice the fitted one.

So R38 is 100 ohm and both parts are fitted. C49 draws no direct current
and its alternating current returns to AGND, not through the shunt: nothing
in the measurement sees it. On a full-scale slew (0.8 V/us from U3) it
takes 8 mA for 25 us. All of this is model and data-sheet evidence, not a
measurement: bring-up checks a small step in the linear region and the
plateau with an inductive load, in both polarities
(docs/bringup_checklist.md §7). Removing R38 returns the stage to the
unfitted state. Do not lower R38 below about 47 ohm, and do not fit TI's
3 ohm here.

C15/C16 sit on the +/-15 V rails and are rated 50 V X7R (audit: >= 25 V).
"""

from libraries.custom_symbols import OPA548, R, C, C_POL, TestPoint
from skidl import Net


def create_output_buffer_block(n):
    U5 = OPA548()(ref="U5", tag="U5", value="OPA547T")
    # R_CL for the OPA547 (header): 62.6 mA by the data-sheet equation;
    # expected 45..65 mA at +/-15 V, less when sourcing and when U5 is warm.
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
    # 10 uF / 35 V in the 3528-21 (case B) package exists only as a POLYMER
    # tantalum. The MnO2 series stop at 4.7 uF / 35 V in case B and put
    # 10 uF / 35 V in case C (6032), which does not fit these pads (KEMET
    # T491 and Kyocera AVX TAJ ratings tables); a buyer told only "10uF/35V
    # tantalum, 3528" ends up with a part that does not fit, or with a 16 V
    # or 25 V one on a 15 V rail. Kyocera AVX TCJ, case B (TCJ data sheet):
    # 10 uF, 35 V, ESR <= 0.2 ohm at 100 kHz, 125 C, recommended up to 80 %
    # of rated voltage. Same pads, same polarity mark.
    for cap in (C17, C18):
        cap.fields["MPN"] = "TCJB106M035R0200E"
    R9 = R()(ref="R9", tag="R9", value="10R 1% 0.25W",
             footprint="Resistor_SMD:R_1206_3216Metric")
    C19 = C()(ref="C19", tag="C19", value="100pF")
    TP2 = TestPoint()(ref="TP2", tag="TP2", value="VO_SENSE")
    # Output snubber, FITTED (header): 100 ohm + 10 nF from VO_RAW to AGND.
    R38 = R()(ref="R38", tag="R38", value="100R",
              footprint="Resistor_SMD:R_0805_2012Metric")
    C49 = C()(ref="C49", tag="C49", value="10nF 50V X7R",
              footprint="Capacitor_SMD:C_0805_2012Metric")

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

    # ---- output snubber (see header) ----
    n.VO_RAW += R38[1]
    snub = Net("VO_SNUB"); snub += R38[2], C49[1]
    n.AGND += C49[2]

    return {
        "U5": U5, "R7": R7, "R8": R8, "C14": C14, "C15": C15, "C16": C16,
        "C17": C17, "C18": C18, "R9": R9, "C19": C19, "TP2": TP2,
        "R38": R38, "C49": C49,
    }
