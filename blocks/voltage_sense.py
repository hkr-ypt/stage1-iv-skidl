"""
voltage_sense.py — SHEET 6: Voltage Sensing (4-Wire Kelvin)
==========================================================

True 4-wire Kelvin measurement of DUT voltage, excluding lead/shunt drops.
High input impedance -> negligible current drawn from the DUT.

    G = 1 + 50k/RG = 1 + 50k/49.9k ~= 2.00   (spec §C.3)

Clamp diodes (D1-D4) reference the amplifier's OWN supply rails (+/-15VA), not
AGND, so legitimate signal excursions within the INA128 common-mode range are
never clipped (spec §C.6). Clamps sit at the J4-side node, BEFORE the series
protection resistors R12/R13, so protection acts ahead of the series element.
"""

from libraries.custom_symbols import INA128P, R, C, D, TestPoint
from skidl import Net


def create_voltage_sense_block(n):
    U7 = INA128P()(ref="U7", tag="U7", value="INA128P")
    R12 = R()(ref="R12", tag="R12", value="1.0k")
    R13 = R()(ref="R13", tag="R13", value="1.0k")
    R14 = R()(ref="R14", tag="R14", value="49.9k")
    D1 = D()(ref="D1", tag="D1", value="1N4148")
    D2 = D()(ref="D2", tag="D2", value="1N4148")
    D3 = D()(ref="D3", tag="D3", value="1N4148")
    D4 = D()(ref="D4", tag="D4", value="1N4148")
    C22 = C()(ref="C22", tag="C22", value="100nF")
    C23 = C()(ref="C23", tag="C23", value="100nF")
    TP5 = TestPoint()(ref="TP5", tag="TP5", value="SENSE+")
    TP6 = TestPoint()(ref="TP6", tag="TP6", value="SENSE-")
    TP7 = TestPoint()(ref="TP7", tag="TP7", value="V_SENSE_OUT")

    # ---- SENSE+ : clamp at J4-side node, then series R12 into +IN ----
    n.SENSE_PLUS += TP5[1], R12[1], D1["A"], D2["K"]
    n.P15VA += D1["K"]          # D1 clamps SENSE+ to +15VA
    n.N15VA += D2["A"]          # D2 clamps SENSE+ to -15VA
    sp_amp = Net("SENSE_P_AMP"); sp_amp += R12[2], U7["+IN"]

    # ---- SENSE- : same topology ----
    n.SENSE_MINUS += TP6[1], R13[1], D3["A"], D4["K"]
    n.P15VA += D3["K"]
    n.N15VA += D4["A"]
    sm_amp = Net("SENSE_M_AMP"); sm_amp += R13[2], U7["-IN"]

    # ---- reference + gain ----
    n.AGND += U7["REF"]
    rg1 = Net("U7_RG1"); rg1 += U7["RG1"], R14[1]
    rg2 = Net("U7_RG2"); rg2 += U7["RG2"], R14[2]

    # ---- supplies ----
    n.P15VA += U7["V+"], C22[1]
    n.AGND += C22[2]
    n.N15VA += U7["V-"], C23[1]
    n.AGND += C23[2]

    # ---- amplified output to ADC (via Sheet 7 RC filter) ----
    n.V_SENSE_OUT += U7["OUT"], TP7[1]

    return {"U7": U7, "R12": R12, "R13": R13, "R14": R14,
            "D1": D1, "D2": D2, "D3": D3, "D4": D4,
            "C22": C22, "C23": C23, "TP5": TP5, "TP6": TP6, "TP7": TP7}
