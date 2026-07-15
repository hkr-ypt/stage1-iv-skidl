"""
current_sense.py — SHEET 5: Current Sensing
===========================================

Ground-referenced current measurement. Shunt R10 (10 ohm, 1 W) sits in the
FORCE- return path between the DUT return and AGND; U6 (INA128P) amplifies the
shunt drop.

    G = 1 + 50k/RG = 1 + 50k/13.0k ~= 4.85   (spec §C.3)
    Full scale at 50 mA: 0.500 V * 4.85 ~= 2.42 V  (~3% below the 2.5 V VREF)

The J4 FORCE- node, the shunt high side, and U6 +IN are one physical node
(TP3 monitors it, labeled ISENSE_HI); U6 -IN and REF sit at the AGND side.
"""

from libraries.custom_symbols import INA128P, R, C, TestPoint
from skidl import Net


def create_current_sense_block(n):
    R10 = R()(ref="R10", tag="R10", value="10.0R 1W",
              footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    U6 = INA128P()(ref="U6", tag="U6", value="INA128P")
    R11 = R()(ref="R11", tag="R11", value="13.0k")
    C20 = C()(ref="C20", tag="C20", value="100nF")
    C21 = C()(ref="C21", tag="C21", value="100nF")
    TP3 = TestPoint()(ref="TP3", tag="TP3", value="ISENSE_HI")
    TP4 = TestPoint()(ref="TP4", tag="TP4", value="I_SENSE_OUT")

    # ---- shunt in FORCE- return; sense across it ----
    n.FORCE_MINUS += R10[1], TP3[1], U6["+IN"]     # high side (== ISENSE_HI)
    n.AGND += R10[2], U6["-IN"], U6["REF"]          # low side -> AGND

    # ---- gain-set resistor across RG pins ----
    rg1 = Net("U6_RG1"); rg1 += U6["RG1"], R11[1]
    rg2 = Net("U6_RG2"); rg2 += U6["RG2"], R11[2]

    # ---- supplies ----
    n.P15VA += U6["V+"], C20[1]
    n.AGND += C20[2]
    n.N15VA += U6["V-"], C21[1]
    n.AGND += C21[2]

    # ---- amplified output to ADC (via Sheet 7 RC filter) ----
    n.I_SENSE_OUT += U6["OUT"], TP4[1]

    return {"R10": R10, "U6": U6, "R11": R11, "C20": C20, "C21": C21,
            "TP3": TP3, "TP4": TP4}
