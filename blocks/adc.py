"""
adc.py — SHEET 7: ADC
=====================

ADS1256 24-bit delta-sigma ADC digitizing both sense channels, BIPOLAR and
truly differential, against the reference both INA128s are referred to:

    AIN0 - AINCOM : current sense  (I_SENSE_OUT - VREF_2V5 = 24.006 V/A * I)
    AIN2 - AINCOM : voltage sense  (V_SENSE_OUT - VREF_2V5 = 0.19920 * V_K)

AINCOM is on VREF_2V5, the same net as U6 REF and U7 REF (Sheets 5 and 6), so
the 2.5 V level shift cancels inside the conversion and the output code is
signed: negative code = negative current / voltage. It used to be on AGND,
which pinned the measurement zero to the ADC's input floor (AGND - 0.1 V,
SBAS288K) and threw away every negative reading. AINCOM is pin 5, directly
beside VREFP (pin 4), which already carries VREF_2V5.

Recommended configuration: PGA = 2 for BOTH channels (full scale +/-2*VREF/PGA
= +/-2.500 V, matching the +/-2.35..2.6 V absolute-input window around 2.5 V),
input buffer OFF (buffer on narrows the window to AGND .. AVDD - 2 V = 0 ..
3 V, leaving only +0.5 V above the 2.5 V zero; use it only for |signal| <
0.5 V). One PGA for both
channels means no PGA switch between the V and I conversions of a sweep
point. Higher PGA (up to 64) is legal for small signals, because the inputs
sit near 2.5 V whatever the gain.

AIN1/AIN3 keep their R16/R18 connection to AGND and are no longer part of the
two measurements. They are useful self-checks: AIN1 - AIN3 reads the ADC's
own zero, and AIN1 - AINCOM (PGA = 1) reads exactly -VREF, i.e. -1/2 of
positive full scale, whatever VREF's absolute value.

VREFP is the SAME 2.500 V reference (U2) that drives the DAC — the shared
reference that makes the measurement ratiometric (spec §C.2). C44 bypasses
that node at U8, where it is now both the reference input and the negative
signal input; it is the same part the routed board already carries there.
First-order RC filters band-limit noise at AIN0/AIN2: R15 + C24 + R16 and
R17 + C25 + R18, now returned to AGND through the idle AIN1/AIN3 legs.

Uses SPI2 (separate from the DAC's SPI1). PDWN/SYNC tied high (always on).
"""

from libraries.custom_symbols import ADS1256, Crystal, R, C, C_POL, TestPoint
from skidl import Net
from .nets import nc_named


def create_adc_block(n):
    U8 = ADS1256()(ref="U8", tag="U8", value="ADS1256IDBR")
    Y1 = Crystal()(ref="Y1", tag="Y1", value="7.6800MHz",
                   footprint="Crystal:Crystal_SMD_HC49-SD")
    C26 = C()(ref="C26", tag="C26", value="22pF")
    C27 = C()(ref="C27", tag="C27", value="22pF")
    C28 = C()(ref="C28", tag="C28", value="100nF")
    C29 = C_POL("Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B")(
        ref="C29", tag="C29", value="10uF/16V")
    C30 = C()(ref="C30", tag="C30", value="100nF")
    # Second +3V3D decoupler on the ADC side. Added on the routed board by its
    # designer (not in the original source); carried here so the board of
    # record, generated from this file, keeps it.
    C31 = C()(ref="C31", tag="C31", value="100nF")
    R19 = R()(ref="R19", tag="R19", value="10k")
    R20 = R()(ref="R20", tag="R20", value="10k")
    R15 = R()(ref="R15", tag="R15", value="100R")
    R16 = R()(ref="R16", tag="R16", value="100R")
    R17 = R()(ref="R17", tag="R17", value="100R")
    R18 = R()(ref="R18", tag="R18", value="100R")
    C24 = C()(ref="C24", tag="C24", value="1nF")
    C25 = C()(ref="C25", tag="C25", value="1nF")
    TP8 = TestPoint()(ref="TP8", tag="TP8", value="VREF_2V5")
    # Local bypass of VREF_2V5 at VREFP/AINCOM (pins 4/5).
    C44 = C()(ref="C44", tag="C44", value="100nF")

    # ---- analog supply ----
    n.P5VA += U8["AVDD"], C28[1], C29["+"]
    n.AGND += U8["AGND"], C28[2], C29["-"]

    # ---- digital supply + pull-ups ----
    n.P3V3D += U8["DVDD"], C30[1], C31[1], R19[1], R20[1]
    n.DGND += U8["DGND"], C30[2], C31[2]

    # ---- reference (shared with DAC) ----
    n.VREF_2V5 += U8["VREFP"], TP8[1], C44[1]
    n.AGND += U8["VREFN"], C44[2]

    # ---- CLKIN crystal ----
    # On the real ADS1256 the oscillator input and the external-clock input are
    # ONE pad, named XTAL1/CLKIN (pin 19, SBAS288K Terminal Functions p.5).
    # The previous model declared XTAL1 and CLKIN as two separate pins.
    clk1 = Net("ADC_XTAL1"); clk1 += U8["XTAL1/CLKIN"], Y1[1], C26[1]
    clk2 = Net("ADC_XTAL2"); clk2 += U8["XTAL2"], Y1[2], C27[1]
    n.AGND += C26[2], C27[2]

    # ---- current channel: AIN0 (measured against AINCOM) with RC filter ----
    n.I_SENSE_OUT += R15[1]
    ain0 = Net("ADC_AIN0"); ain0 += R15[2], U8["AIN0"], C24[1]
    n.AGND += R16[1]
    ain1 = Net("ADC_AIN1"); ain1 += R16[2], U8["AIN1"], C24[2]

    # ---- voltage channel: AIN2 (measured against AINCOM) with RC filter ----
    n.V_SENSE_OUT += R17[1]
    ain2 = Net("ADC_AIN2"); ain2 += R17[2], U8["AIN2"], C25[1]
    n.AGND += R18[1]
    ain3 = Net("ADC_AIN3"); ain3 += R18[2], U8["AIN3"], C25[2]

    # ---- common negative input: the measurement zero ----
    # Same net as U6 REF and U7 REF, so AIN0/AIN2 - AINCOM is exactly the
    # amplified signal, positive or negative.
    n.VREF_2V5 += U8["AINCOM"]

    # ---- SPI2 (bidirectional) ----
    n.SCK2 += U8["SCLK"]
    n.MOSI2 += U8["DIN"]
    n.MISO2 += U8["DOUT"]
    n.CS_ADC += U8["CS"]
    n.DRDY += U8["DRDY"], R19[2]
    n.RESET_ADC += U8["RESET"], R20[2]

    # ---- SYNC/PDWN tied high (always converting, never powered down) ----
    # Also one pad on the real part (pin 14, SYNC/PDWN), not two.
    n.P3V3D += U8["SYNC/PDWN"]

    # ---- unused ADC pins: explicit no-connects ----
    # The SSOP-28 has 28 pads; 20 are used above. Stage 1 digitises two
    # channels (AIN0 and AIN2, each against AINCOM) and uses no general-purpose
    # digital I/O, so the remaining 8 pads are declared unconnected here rather
    # than being left out of the symbol.
    nc_named(U8,
             "AIN4", "AIN5", "AIN6", "AIN7",   # spare analog inputs
             "D0/CLKOUT", "D1", "D2", "D3")    # general-purpose digital I/O

    return {"U8": U8, "Y1": Y1, "C26": C26, "C27": C27, "C28": C28, "C29": C29,
            "C30": C30, "C31": C31, "R19": R19, "R20": R20, "R15": R15, "R16": R16,
            "R17": R17, "R18": R18, "C24": C24, "C25": C25, "TP8": TP8,
            "C44": C44}
