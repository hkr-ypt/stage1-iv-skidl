"""
current_sense.py — SHEET 5: Current Sensing
===========================================

Ground-referenced, BIPOLAR current measurement. Shunt R10 (10.0 ohm) sits in
the FORCE- return path between the DUT return and AGND; U6 (INA128P)
amplifies the shunt drop and shifts it onto the 2.500 V reference:

    I_SENSE_OUT = VREF_2V5 + G * R10 * I
    G = 1 + 50k/RG = 1 + 50k/35.7k = 2.4006    ->  24.006 V/A (24.0 mV/mA)

The ADS1256 (Sheet 7) reads AIN0 against AINCOM = VREF_2V5, so it sees
G*R10*I = 24.006 V/A * I, signed:

    I = -91.3 mA  ->  AIN0 = 0.309 V,  AIN0 - AINCOM = -2.191 V
    I =   0       ->  AIN0 = 2.500 V,  AIN0 - AINCOM =  0
    I = +49.4 mA  ->  (nominal ILIM)                  +1.185 V
    I = +91.3 mA  ->  AIN0 = 4.691 V,  AIN0 - AINCOM = +2.191 V

WHY G = 2.40 (R11 was 13.0k, G = 4.846)
---------------------------------------
The gain is sized to the largest current the hardware can PHYSICALLY pass,
not to the programmed current limit. For any passive DUT, V and I share a
sign, so |I| <= |V_SWEEP| / (R30 + R10 + R_F1) <= 10.04 V / 110 ohm = 91.3 mA
whatever the OPA548 limit does (Sheet 3 sets V_SWEEP, Sheet 8 holds R30).
The NEGATIVE half of that bound is hardware; the positive half holds while
firmware keeps DAC channel B at full scale (Sheet 3, "firmware contract").
A sweep commanded past it can reach the ~127 mA fault-envelope current and
over-range this channel; validation/measurement_range.py reports both.
That independence matters: SBOS070 specifies the ILIM tolerance only at a
2.5 A setting (+/-100 mA typ, +/-250 mA max), so the ~49 mA setting of R7 is
not a bound anyone can rely on. At the old G = 4.846 the channel would clip at
~48-52 mA, i.e. at or below the nominal limit, and every forward-bias plateau
of a hysteresis sweep would be lost. At G = 2.40:

    ADC full scale, PGA = 2 (+/-2.500 V) ........... +/-104.1 mA
    ADC pin window at AVDD = 4.75 V (+/-2.35 V) .... +/-97.9 mA
    physical maximum, any passive DUT .............. +/-91.3 mA  (92.5 mA
                                                     with 1 % R30/R10 low)

so no operating point of the source can over-range this channel. The cost is
input-referred offset/drift in amperes rising ~1.7x (typ) versus G = 4.846;
the ADC's PGA (up to 64, legal because both inputs now sit near 2.5 V)
recovers resolution for small currents in firmware.

U6 REF is on VREF_2V5, driven directly by the REF5025 (low impedance, as the
INA128 REF pin requires). Its REF current is (VREF - A2out)/80k = 12..51 uA.
The J4 FORCE- node, the shunt high side, and U6 +IN are one physical node
(TP3 monitors it, labeled ISENSE_HI); U6 -IN sits at the shunt's AGND side.
"""

from libraries.custom_symbols import INA128P, R, C, TestPoint
from skidl import Net


def create_current_sense_block(n):
    # R10 IS the current measurement, so it is a precision part: 10.0 ohm
    # +/-0.1 %, TCR <= 25 ppm/K (audit: <= 50 ppm/K), metal film, DIN 0207
    # axial (same footprint). Dissipation: 24 mW at the 49 mA plateau,
    # 0.16 W worst case (127 mA fault envelope, Sheet 8); a 0.5 W-class 0207
    # part runs that at <= 1/3 rating, which also keeps its self-heating drift
    # small. Example: Vishay Dale CMF55 class (verify the ordering code).
    R10 = R()(ref="R10", tag="R10", value="10.0R 0.1% 25ppm",
              footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    R10.fields["MPN"] = "10.0R 0.1% 25ppm/K 0.5W metal film, 0207 axial"
    U6 = INA128P()(ref="U6", tag="U6", value="INA128P")
    R11 = R()(ref="R11", tag="R11", value="35.7k 0.1%")
    C20 = C()(ref="C20", tag="C20", value="100nF 50V X7R")   # +15VA
    C21 = C()(ref="C21", tag="C21", value="100nF 50V X7R")   # -15VA
    TP3 = TestPoint()(ref="TP3", tag="TP3", value="ISENSE_HI")
    TP4 = TestPoint()(ref="TP4", tag="TP4", value="I_SENSE_OUT")

    # ---- shunt in FORCE- return; sense across it ----
    n.FORCE_MINUS += R10[1], TP3[1], U6["+IN"]     # high side (== ISENSE_HI)
    n.AGND += R10[2], U6["-IN"]                     # low side -> AGND

    # ---- output reference: mid-scale of the ADC window ----
    # The ADC reads I_SENSE_OUT against AINCOM on this same net (Sheet 7).
    n.VREF_2V5 += U6["REF"]

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
