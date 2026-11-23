"""
current_sense.py — SHEET 5: Current Sensing
===========================================

Ground-referenced, BIPOLAR current measurement. Shunt R10 (10.0 ohm) sits in
the FORCE- return path between the DUT return (through F2, Sheet 8) and
AGND; U6 (INA128P)
amplifies the shunt drop and shifts it onto the 2.500 V reference:

    I_SENSE_OUT = VREF_2V5 + G * R10 * I
    G = 1 + 50k/RG = 1 + 50k/35.7k = 2.4006    ->  24.006 V/A (24.0 mV/mA)

The ADS1256 (Sheet 7) reads AIN0 against AINCOM = VREF_2V5, so it sees
G*R10*I = 24.006 V/A * I, signed:

    I = -91.3 mA  ->  AIN0 = 0.309 V,  AIN0 - AINCOM = -2.191 V
    I =   0       ->  AIN0 = 2.500 V,  AIN0 - AINCOM =  0
    I = +49 mA    ->  (the intended plateau)          +1.176 V
    I = +91.3 mA  ->  AIN0 = 4.691 V,  AIN0 - AINCOM = +2.191 V

WHY G = 2.40 (R11 was 13.0k, G = 4.846)
---------------------------------------
The gain is sized to the largest current the hardware can PHYSICALLY pass,
not to the programmed current limit. For any passive DUT, V and I share a
sign, so |I| <= |V_SWEEP| / (R30 + R10 + R_F1) <= 10.04 V / 110 ohm = 91.3 mA
whatever U5's current limit does (Sheet 3 sets V_SWEEP, Sheet 8 holds R30).
The NEGATIVE half of that bound is hardware; the positive half holds while
firmware keeps DAC channel B at full scale (Sheet 3, "firmware contract").
A sweep commanded past it can reach the ~128 mA fault-envelope current and
over-range this channel; validation/measurement_range.py reports both.
That independence matters: the current limit is a plateau with a tolerance,
not a bound. U5 is an OPA547 (Sheet 4): its plateau, centred near 49 mA by
R7, shifts with polarity and temperature (expected 45..65 mA) and by up to
+/-30 mA between units (SBOS056F), 8..89 mA in all. At the old G = 4.846 the
channel would clip at ~48-52 mA, i.e. inside that span, and every forward-bias
plateau of a hysteresis sweep would be lost. At G = 2.40:

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
The shunt high side and U6 +IN are one physical node, ISENSE_HI (TP3), fed
from J7 through the series PTC F2; U6 -IN sits at the shunt's AGND pad.
Nothing else connects to ISENSE_HI: every ampere that leaves the DUT through
FORCE- passes through R10 and nothing passes around it.

R10 — THE ORDERED PART
----------------------
Vishay Dale RS02B10R00BE12 (doc. 30204, global part number decoded from
that data sheet: RS02B model, 10R00 = 10.00 ohm, B = +/-0.1 %, E12 =
lead-free bulk): 3 W wirewound (characteristic U, 25 C), TCR +/-20 ppm/C
for >= 10 ohm, body 14.22 +/- 1.57 mm x 4.75 +/- 0.79 mm, 0.813 mm leads.
Footprint: KiCad DIN0614 (14.3 x 5.7 mm) at 20.32 mm pitch, horizontal:
the body lies flat, so its two terminations sit at the same height and
temperature (no lead-length asymmetry to turn self-heating into thermal EMF
across the shunt). Why a 3 W part for a 24 mW job: R10 must survive an
external source on J7 until F2 trips — up to 1.19 A, ~14 W, ~0.6 s typical
(Sheet 8) — and the RS02B's short-time overload test (5 x rated power,
5 s, dR <= 0.2 % + 0.05 ohm) covers that. A 0.5 W metal film does not:
Vishay CMF (doc. 31018) tests 2.5 x rated voltage for 5 s, i.e. ~3 W. Costs, stated: the element is CuNi or
NiCr depending on value (data sheet), so thermal EMF at the terminations is
not specified — bring-up measures zero-current offset after a high-current
dwell (docs/bringup_checklist.md); and the RS winding is inductive (the NS02B
non-inductive variant has the same body and footprint, but no distributor
listing of NS02B10R00BE12 was found, so it is not the default).
"""

from libraries.custom_symbols import INA128P, R, C, TestPoint
from skidl import Net


def create_current_sense_block(n):
    # R10 IS the current measurement: 10.0 ohm +/-0.1 %, 20 ppm/K, and 3 W so
    # it outlives an external-source fault on J7 until F2 trips (header).
    # Normal dissipation: 24 mW at the 49 mA plateau, 0.16 W at the 128 mA
    # fault bound (Sheet 8) — 5 % of rating, so self-heating drift is small.
    R10 = R()(ref="R10", tag="R10", value="10.0R 0.1% 3W",
              footprint="Resistor_THT:"
                        "R_Axial_DIN0614_L14.3mm_D5.7mm_P20.32mm_Horizontal")
    R10.fields["MPN"] = "RS02B10R00BE12"
    U6 = INA128P()(ref="U6", tag="U6", value="INA128P")
    R11 = R()(ref="R11", tag="R11", value="35.7k 0.1%")
    C20 = C()(ref="C20", tag="C20", value="100nF 50V X7R")   # +15VA
    C21 = C()(ref="C21", tag="C21", value="100nF 50V X7R")   # -15VA
    TP3 = TestPoint()(ref="TP3", tag="TP3", value="ISENSE_HI")
    TP4 = TestPoint()(ref="TP4", tag="TP4", value="I_SENSE_OUT")

    # ---- shunt in FORCE- return (after F2); sense across it ----
    n.ISENSE_HI += R10[1], TP3[1], U6["+IN"]       # high side
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
