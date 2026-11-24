"""
power.py — SHEET 1: Power Regulation & Reference
================================================

Distributes clean, separated analog/digital rails from a bench supply
(+15V/-15V/+5V) and produces the 2.500 V precision reference shared by BOTH the
DAC and the ADC (ratiometric-accuracy choice, spec §C.2).

Key topology points enforced here:
  * Ferrite bead FB1 isolates the analog +5VA from digital +5V_RAW switching
    noise.
  * R31 (0 ohm) is the single-point AGND/DGND star-ground join — the ONLY place
    analog and digital grounds meet (checked in validation/assertions.py).
  * U1 (3.3V LDO) is fed from +5V_RAW (digital side) so digital load current
    never flows through FB1 into the analog rail.
  * D8/D9 (MBR0540 Schottky) clamp each +/-15 V rail to within one Schottky
    drop of AGND in the WRONG direction. See "PARTIAL SUPPLY" below.

PARTIAL SUPPLY — WHY D8/D9 EXIST
--------------------------------
With one +/-15 V rail absent (bench output off or unplugged) the other one
still powers U3, U5, U6 and U7 through their supply pins, and their
quiescent current (U5 alone is up to 15 mA, SBOS056F) flows out of the
DEAD rail pin and pulls that rail TOWARDS the live one. A floating -15VA
can rise above AGND. The sense clamps D2/D4 (anode on -15VA, Sheet 6) then
conduct INTO SENSE+/SENSE-, i.e. into the DUT, with nothing but the dead
rail's impedance to limit it; +15VA dragged below AGND does the same
through D1/D3. D9 (anode -15VA, cathode AGND) and D8 (anode AGND, cathode
+15VA) carry that quiescent current instead and hold the dead rail within
VF <= 0.51 V (MBR0540, at <= 0.5 A) of AGND, so the voltage the sense
clamps can put on the DUT is at most that drop, less their own forward
voltage, instead of most of the live rail. Reverse
leakage: <= 10 uA at 20 V and 25 C (5 mA at 100 C junction), rail to AGND,
outside every measurement path. The same 0.51 V bound is what keeps the
U12 disconnect (Sheet 8) guaranteed OPEN when a rail is missing.

+5V_RAW gets no clamp: nothing on the +5 V side connects to -15VA, so it
cannot be dragged negative; with +5 V absent, the DAC reference gate
(Sheet 3) holds the setpoint at 0 V.
"""

from libraries.custom_symbols import (
    TerminalBlock_4, LD1117S33, REF5025, L_FerriteBead, R, C, C_POL, PWR_FLAG,
    D_Schottky,
)
from .nets import nc


def create_power_block(n):
    """Instantiate Sheet 1. Returns a dict of the parts for validation/manifest."""

    # ---- components ----
    # The value used to read "+15V_RAW/-15V_RAW/+5V_RAW/GND", pin 1 first.
    # J1 is rotated 180 on the board, so pin 1 is the RIGHT-most terminal and
    # that text read backwards against the hardware. The value now names the
    # part; which terminal is which is printed on the silkscreen next to each
    # one (layout/audit_corrections.eco) and stated by pin number here.
    J1 = TerminalBlock_4()(ref="J1", tag="J1",
                           value="BENCH SUPPLY 1:+15V 2:-15V 3:+5V 4:GND")
    U1 = LD1117S33()(ref="U1", tag="U1", value="LD1117S33TR")
    U2 = REF5025()(ref="U2", tag="U2", value="REF5025AIDGKT")
    FB1 = L_FerriteBead()(ref="FB1", tag="FB1", value="BLM21PG221SN1D")
    R31 = R()(ref="R31", tag="R31", value="0R",
              footprint="Resistor_SMD:R_0805_2012Metric")

    C1 = C_POL("Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B")(
        ref="C1", tag="C1", value="10uF/25V")
    C2 = C_POL("Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B")(
        ref="C2", tag="C2", value="10uF/16V")
    C3 = C()(ref="C3", tag="C3", value="100nF",
             footprint="Capacitor_SMD:C_0805_2012Metric")
    C4 = C()(ref="C4", tag="C4", value="100nF",
             footprint="Capacitor_SMD:C_0805_2012Metric")
    # Bulk reservoir capacitors. Package is chosen per instance, from the
    # working voltage and the derating - see docs/silkscreen_and_packages.md.
    # These four previously inherited an 8 mm through-hole radial default and
    # were the largest parts on the board by accident rather than by choice.
    #
    # +/-15 V rails: 25 V rated on 15 V working is 60% of rating, comfortably
    # inside the 80% ceiling normally applied to aluminium electrolytics.
    C5 = C_POL("Capacitor_SMD:CP_Elec_8x10.5")(
        ref="C5", tag="C5", value="100uF/25V")
    C6 = C_POL("Capacitor_SMD:CP_Elec_8x10.5")(
        ref="C6", tag="C6", value="100uF/25V")
    # +5 V rail: 16 V rated on 5 V working is 31% of rating.
    C7 = C_POL("Capacitor_SMD:CP_Elec_6.3x7.7")(
        ref="C7", tag="C7", value="100uF/16V")
    # +3V3 rail: 10 V rated on 3.3 V working is 33% of rating.
    C8 = C_POL("Capacitor_SMD:CP_Elec_6.3x5.4")(
        ref="C8", tag="C8", value="100uF/10V")
    # REF5025 output capacitor. SBOS410O, for the REF50xxAI: decouple the
    # output with 1..50 uF, ESR <= 1.5 ohm. A 1 uF 0603 sat at the floor of
    # that range before DC-bias loss; 4.7 uF X7R (~3 uF or more at 2.5 V) is
    # inside it with margin.
    C9 = C()(ref="C9", tag="C9", value="4.7uF 10V X7R")
    C10 = C()(ref="C10", tag="C10", value="100nF")
    # Dead-rail clamps (header, PARTIAL SUPPLY).
    D8 = D_Schottky()(ref="D8", tag="D8", value="MBR0540")
    D9 = D_Schottky()(ref="D9", tag="D9", value="MBR0540")

    # ---- raw supply inputs from bench supply (J1) ----
    n.P15VA += J1["Pin_1"]      # +15V_RAW == +15VA (same net downstream)
    n.N15VA += J1["Pin_2"]      # -15V_RAW == -15VA
    n.P5V_RAW += J1["Pin_3"]
    n.DGND += J1["Pin_4"]       # bench GND enters on digital side

    # +/-15V analog-rail bulk caps, returned to AGND
    n.P15VA += C5["+"]
    n.AGND += C5["-"]
    n.AGND += C6["+"]
    n.N15VA += C6["-"]
    # Dead-rail clamps: reverse-biased while both rails are up.
    n.P15VA += D8["K"]
    n.AGND += D8["A"]
    n.AGND += D9["K"]
    n.N15VA += D9["A"]

    # ---- +3V3 digital LDO (U1), fed from +5V_RAW ----
    n.P5V_RAW += U1["VIN"], C1["+"], C3[1]
    n.DGND += U1["GND"], C1["-"], C3[2]
    n.P3V3D += U1["VOUT"], C2["+"], C4[1], C8["+"]
    n.DGND += C2["-"], C4[2], C8["-"]

    # ---- analog +5VA via ferrite bead FB1 ----
    n.P5V_RAW += FB1[1]
    n.P5VA += FB1[2], C7["+"]
    n.AGND += C7["-"]

    # ---- 2.500V precision reference (U2) ----
    n.P5VA += U2["VIN"], C10[1]
    n.AGND += U2["GND"], C10[2]
    n.VREF_2V5 += U2["VOUT"], C9[1]
    n.AGND += C9[2]
    nc(U2["TEMP"], U2["TRIM/NR"], U2["NC1"], U2["NC2"], U2["NC3"])

    # ---- STAR GROUND single-point join (the only AGND<->DGND connection) ----
    n.AGND += R31[1]
    n.DGND += R31[2]

    # ---- POWER-SOURCE FLAGS -------------------------------------------
    # Nothing on this board drives these rails; the energy enters from
    # outside it. A PWR_FLAG at the point of entry is how that fact is
    # stated to ERC — see blocks/nets.py for why Net.drive was not.
    #
    # J1 is the 4-position screw terminal fed by the bench supply. Its pins
    # are PASSIVE (it is a connector, not a regulator), so each of the three
    # rails and the ground return it brings in needs its own flag.
    FLG1 = PWR_FLAG()(ref="#FLG1", tag="FLG1", value="+15VA")
    FLG2 = PWR_FLAG()(ref="#FLG2", tag="FLG2", value="-15VA")
    FLG3 = PWR_FLAG()(ref="#FLG3", tag="FLG3", value="+5V_RAW")
    FLG4 = PWR_FLAG()(ref="#FLG4", tag="FLG4", value="DGND")
    n.P15VA += FLG1[1]        # source: bench supply +15 V, enters at J1 pin 1
    n.N15VA += FLG2[1]        # source: bench supply -15 V, enters at J1 pin 2
    n.P5V_RAW += FLG3[1]      # source: bench supply +5 V,  enters at J1 pin 3
    n.DGND += FLG4[1]         # source: bench supply return, enters at J1 pin 4

    # +5VA is +5V_RAW after ferrite bead FB1. FB1 is a passive two-terminal
    # part, so KiCad treats +5VA as a net with no driver of its own even
    # though +5V_RAW is flagged. Its source is still the bench supply, reached
    # through FB1.
    FLG5 = PWR_FLAG()(ref="#FLG5", tag="FLG5", value="+5VA")
    n.P5VA += FLG5[1]

    # AGND is galvanically a SEPARATE NET from DGND: the two planes meet only
    # at the R31 0-ohm star point, and R31 is a passive resistor, so KiCad
    # (correctly) does not propagate DGND's flag across it. The analog return
    # therefore needs its own flag, placed here at the star point — the single
    # physical location where the analog ground is referenced to the supply
    # return. Flagging it does NOT merge the planes; the star topology and its
    # assertion in validation/assertions.py are unchanged.
    FLG6 = PWR_FLAG()(ref="#FLG6", tag="FLG6", value="AGND")
    n.AGND += FLG6[1]

    return {
        "J1": J1, "U1": U1, "U2": U2, "FB1": FB1, "R31": R31,
        "C1": C1, "C2": C2, "C3": C3, "C4": C4, "C5": C5, "C6": C6,
        "C7": C7, "C8": C8, "C9": C9, "C10": C10, "D8": D8, "D9": D9,
        "FLG1": FLG1, "FLG2": FLG2, "FLG3": FLG3, "FLG4": FLG4,
        "FLG5": FLG5, "FLG6": FLG6,
    }
