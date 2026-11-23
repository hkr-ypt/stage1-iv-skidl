"""
voltage_sense.py — SHEET 6: Voltage Sensing (4-Wire Kelvin)
==========================================================

True 4-wire Kelvin measurement of DUT voltage, excluding lead/shunt drops,
BIPOLAR over the whole +/-10.04 V the sweep source can apply.

    V_SENSE_OUT = VREF_2V5 + G * k * (V(SENSE+) - V(SENSE-))
    G = 1 + 50k/RG = 1 + 50k/(open) = 1.00   (R14 depopulated)
    k = R32/(R12 + R32) = R33/(R13 + R33) = 10.0k/50.2k = 0.19920

so the ADS1256 (Sheet 7), which reads AIN2 against AINCOM = VREF_2V5, sees
exactly G*k*V_K = 0.19920 * V_K, centred on its own reference:

    V_K = -10.04 V  ->  AIN2 = 0.500 V,  AIN2 - AINCOM = -2.000 V
    V_K =   0       ->  AIN2 = 2.500 V,  AIN2 - AINCOM =  0
    V_K = +10.04 V  ->  AIN2 = 4.500 V,  AIN2 - AINCOM = +2.000 V

WHY THE MEASUREMENT USED TO BE UNIPOLAR, AND WHAT CHANGED
---------------------------------------------------------
U7 REF and the ADC's negative input were both on AGND, and the ADS1256 has no
negative supply: each input pin must stay inside AGND - 0.1 V .. AVDD + 0.1 V
(SBAS288K, "Absolute input voltage", buffer off). Any negative V_K therefore
drove AIN2 below the floor, and G = 1 is the INA128's minimum, so no R14 value
could fix it. Two changes, both needed:

  1. Level shift. U7 REF moves from AGND to VREF_2V5. The INA128 output is
     referred to REF (SBOS051G), so zero volts at the DUT now lands at
     2.500 V, mid-way through the ADC's 0..5 V window. VREF_2V5 is a REF5025
     output: a low-impedance source, which is what the REF pin requires
     (8 ohm in series already costs ~80 dB CMR at G = 1). U7's REF current is
     (VREF - V(+IN))/80k = 4..56 uA, well inside the REF5025's +/-10 mA.

  2. Attenuation. The ADC window around 2.5 V is +/-2.35 V (AVDD = 4.75 V
     worst case) and the INA128 cannot go below G = 1, so the Kelvin pair is
     divided by k = 0.1992 AHEAD of U7. R12/R13 (formerly 1.0k series
     protection) become the top arms and keep that role; R32/R33 are the new
     bottom arms to AGND. The attenuator is two matched single-ended dividers,
     not one resistor across the inputs: a differential divider would pass
     its current (V_K / R_total) from SENSE+ to SENSE- through the DUT's
     return, i.e. through the R10 shunt, and appear as a fake parallel
     conductance in every I-V curve. Here the SENSE+ leg returns to AGND
     directly, and the SENSE- leg only diverts V(SENSE-)/50.2k =
     I*(R10 + R_F2)/50.2k around the shunt (0.03 % of I; proportional, but
     F2 is a PTC, so it calibrates out only as far as F2 holds still).

Divider mismatch converts common mode into error only through V(SENSE-), which
sits at I*(R10 + R_F2): F2 (3.0..7.5 ohm) lies between FORCE- and the shunt.
With 0.1 % parts the worst case is ~0.32 % * 1.62 V = 5 mV at the 92 mA
physical current bound (7.5 ohm in F2), and it scales with I. Bring-up
measures it: both Kelvin leads on the DUT's low terminal, current flowing.

RANGES ON THIS PATH, KEPT DISTINCT
----------------------------------
  1. physical Kelvin voltage across the DUT, at J5/J6
     +/-10.04 V as I -> 0, shrinking to ~+/-4.6 V at the 49 mA current limit,
     where R30 (100R) + R10 (10R) drop ~5.4 V inside the instrument. For a
     passive DUT |V_K| <= |V_SWEEP| always (Sheet 3).
  2. U7 input: k * V(SENSE+/-) = +/-2.0 V differential, common mode < 2.2 V
     — far inside the INA128's (V-)+2 .. (V+)-2 range on +/-15VA.
  3. U7 output: 2.500 +/- 2.000 V (0.50 .. 4.50 V).
  4. ADS1256, PGA = 2: full scale +/-2*VREF/PGA = +/-2.500 V differential;
     absolute pin window -0.1 .. 4.85 V at AVDD = 4.75 V.
     Measurable V_K: +/-12.55 V (PGA-limited), +/-11.80 V guaranteed by the
     pin window at the lowest legal AVDD. Both exceed the +/-10.04 V the
     source can apply, so NO sweep point can over-range this channel.
     validation/measurement_range.py recomputes all of this from the part
     values on every build.

With SENSE leads disconnected, R32/R33 pull both INA128 inputs to AGND and the
channel reads 0 V instead of rail-saturating on its bias current (SBOS051G,
"Input Bias Current Return Path", requires one anyway).

Clamp diodes (D1-D4) reference the amplifier's OWN supply rails (+/-15VA), not
AGND, so legitimate signal excursions within the INA128 common-mode range are
never clipped (spec §C.6). Clamps sit at the J4-side node, BEFORE the series
protection resistors R12/R13, so protection acts ahead of the series element;
at the +/-15.7 V clamp level U7 sees only k * 15.7 = 3.1 V.
"""

from libraries.custom_symbols import INA128P, R, C, D, TestPoint, dnp
from skidl import Net


def create_voltage_sense_block(n):
    U7 = INA128P()(ref="U7", tag="U7", value="INA128P")
    # Attenuator top arms (and the series protection resistors after the
    # clamps). 0.1 % thin film: k's tolerance is the channel's gain tolerance.
    R12 = R()(ref="R12", tag="R12", value="40.2k 0.1%")
    R13 = R()(ref="R13", tag="R13", value="40.2k 0.1%")
    # A real DNP (KiCad dnp + excluded from the BOM), not just a value string.
    R14 = dnp(R()(ref="R14", tag="R14", value="DNP (G=1)"))
    # Attenuator bottom arms to AGND: k = 10.0k / (40.2k + 10.0k) = 0.1992.
    R32 = R()(ref="R32", tag="R32", value="10.0k 0.1%")
    R33 = R()(ref="R33", tag="R33", value="10.0k 0.1%")
    # 1N4148W: the SOD-123 version of the 1N4148 (the 1N4148 is DO-35).
    D1 = D()(ref="D1", tag="D1", value="1N4148W")
    D2 = D()(ref="D2", tag="D2", value="1N4148W")
    D3 = D()(ref="D3", tag="D3", value="1N4148W")
    D4 = D()(ref="D4", tag="D4", value="1N4148W")
    C22 = C()(ref="C22", tag="C22", value="100nF 50V X7R")   # +15VA
    C23 = C()(ref="C23", tag="C23", value="100nF 50V X7R")   # -15VA
    TP5 = TestPoint()(ref="TP5", tag="TP5", value="SENSE+")
    TP6 = TestPoint()(ref="TP6", tag="TP6", value="SENSE-")
    TP7 = TestPoint()(ref="TP7", tag="TP7", value="V_SENSE_OUT")

    # ---- SENSE+ : clamp at J4-side node, then divider R12/R32 into +IN ----
    n.SENSE_PLUS += TP5[1], R12[1], D1["A"], D2["K"]
    n.P15VA += D1["K"]          # D1 clamps SENSE+ to +15VA
    n.N15VA += D2["A"]          # D2 clamps SENSE+ to -15VA
    sp_amp = Net("SENSE_P_AMP"); sp_amp += R12[2], R32[1], U7["+IN"]
    n.AGND += R32[2]

    # ---- SENSE- : same topology, matched divider R13/R33 ----
    n.SENSE_MINUS += TP6[1], R13[1], D3["A"], D4["K"]
    n.P15VA += D3["K"]
    n.N15VA += D4["A"]
    sm_amp = Net("SENSE_M_AMP"); sm_amp += R13[2], R33[1], U7["-IN"]
    n.AGND += R33[2]

    # ---- reference + gain ----
    # REF on VREF_2V5 puts 0 V at the DUT at mid-scale of the ADC. The ADC
    # reads this channel against AINCOM, which is on the same VREF_2V5 net
    # (Sheet 7), so the 2.5 V cancels in the conversion itself.
    n.VREF_2V5 += U7["REF"]
    # G = 1 + 50k/R14. R14 is DNP, so RG is open and G = 1: for a fixed
    # overall gain G*k, the lowest G gives the lowest input-referred offset
    # (INA128 V_OS = 50 uV + 500 uV/G, max). The pads stay on the board.
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
            "R32": R32, "R33": R33,
            "D1": D1, "D2": D2, "D3": D3, "D4": D4,
            "C22": C22, "C23": C23, "TP5": TP5, "TP6": TP6, "TP7": TP7}
