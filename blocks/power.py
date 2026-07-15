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
"""

from libraries.custom_symbols import (
    TerminalBlock_4, LD1117S33, REF5025, L_FerriteBead, R, C, C_POL,
)
from .nets import nc


def create_power_block(n):
    """Instantiate Sheet 1. Returns a dict of the parts for validation/manifest."""

    # ---- components ----
    J1 = TerminalBlock_4()(ref="J1", tag="J1",
                           value="+15V_RAW/-15V_RAW/+5V_RAW/GND")
    U1 = LD1117S33()(ref="U1", tag="U1", value="LD1117S33TR")
    U2 = REF5025()(ref="U2", tag="U2", value="REF5025AIDGKT")
    FB1 = L_FerriteBead()(ref="FB1", tag="FB1", value="BLM21PG221SN1D")
    R31 = R()(ref="R31", tag="R31", value="0R",
              footprint="Resistor_SMD:R_0805_2012Metric")

    C1 = C_POL()(ref="C1", tag="C1", value="10uF",
                 footprint="Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B")
    C2 = C_POL()(ref="C2", tag="C2", value="10uF",
                 footprint="Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B")
    C3 = C()(ref="C3", tag="C3", value="100nF",
             footprint="Capacitor_SMD:C_0805_2012Metric")
    C4 = C()(ref="C4", tag="C4", value="100nF",
             footprint="Capacitor_SMD:C_0805_2012Metric")
    C5 = C_POL()(ref="C5", tag="C5", value="100uF/25V")
    C6 = C_POL()(ref="C6", tag="C6", value="100uF/25V")
    C7 = C_POL()(ref="C7", tag="C7", value="100uF/16V")
    C8 = C_POL()(ref="C8", tag="C8", value="100uF/10V")
    C9 = C()(ref="C9", tag="C9", value="1uF")
    C10 = C()(ref="C10", tag="C10", value="100nF")

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

    return {
        "J1": J1, "U1": U1, "U2": U2, "FB1": FB1, "R31": R31,
        "C1": C1, "C2": C2, "C3": C3, "C4": C4, "C5": C5, "C6": C6,
        "C7": C7, "C8": C8, "C9": C9, "C10": C10,
    }
