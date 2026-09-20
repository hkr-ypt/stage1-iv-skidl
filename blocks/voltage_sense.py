"""
voltage_sense.py — SHEET 6: Voltage Sensing (4-Wire Kelvin)
==========================================================

True 4-wire Kelvin measurement of DUT voltage, excluding lead/shunt drops.
High input impedance -> negligible current drawn from the DUT.

    G = 1 + 50k/RG = 1 + 50k/(open) = 1.00   (R14 depopulated, spec §C.3)

R14 is DNP on purpose. SBOS051's own gain table lists RG = NC for G = 1, and
G = 1 is the INA128's FLOOR: the part amplifies, it can never attenuate. That
floor is what this design wants, because what bounds the measurement sits
downstream of U7, not inside it. Four different ranges live on this signal
path and they are not interchangeable:

  1. physical Kelvin voltage across the DUT, at J5/J6
     +/-10.04 V as I -> 0, shrinking to ~+/-4.6 V at the 49 mA current limit,
     where R30 (100R) + R10 (10R) drop ~5.4 V inside the instrument. Set by
     the sweep source (Sheet 3) and the FORCE-loop series resistance.

  2. differential input U7 can accept and amplify
     ~+/-13 V at G = 1 on the +/-15VA rails (its output swing is the bound).
     D1-D4 clamp to the RAILS, not AGND, so they take nothing off this. Never
     the binding limit in this design.

  3. U7 output after gain and reference
     V_SENSE_OUT = G * V_K + V(REF pin). REF is tied to AGND below, so the
     output is bipolar about 0 V and spans that same ~+/-13 V.

  4. what the ADS1256 can actually represent (Sheet 7)
     AIN3 and VREFN both sit at AGND, and AVDD is +5VA. SBAS288 gives full
     scale = +/-2*VREF/PGA = +/-5.000 V at PGA = 1, and an absolute input
     floor of AGND - 0.1 V. With no negative analog supply to go below that
     floor, the usable window at AIN2 is 0 .. +5.000 V — UNIPOLAR.

The instrument's MEASURABLE Kelvin range is (4) referred back to the DUT, and
intersected with what (1) can actually establish:

    0 .. 5.000/G = 0 .. +5.00 V      (was 0 .. +2.50 V at G = 2.002, R14 49.9k)

It stays unipolar for ANY R14 value, because G cannot go below 1 and the
measurement zero is pinned at AGND. Making it bipolar needs U7's REF pin moved
off AGND to a buffered mid-scale; reaching +/-10 V needs an overall gain of
5.000/20.08 = 0.25 that no INA128 can produce — a sub-unity difference
amplifier, or a buffered front-end divider. Both are changes to parts and
wiring, not to a resistor value. See the REF connection below.

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
    R14 = R()(ref="R14", tag="R14", value="DNP (G=1)")
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
    # REF at AGND makes V_SENSE_OUT bipolar about 0 V — but Sheet 7 puts AIN3
    # on AGND too, on a single +5 V analog supply, so everything below 0 V is
    # thrown away downstream. Recovering the negative half means moving REF to
    # a buffered mid-scale (2.500 V); that is a NET change, not a value change.
    n.AGND += U7["REF"]
    # G = 1 + 50k/R14. R14 is DNP, so RG is open and G = 1 — the INA128's
    # floor, and the widest measurable Kelvin span this ADC wiring allows.
    # The pads stay on the board so a gain resistor can be fitted later.
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
