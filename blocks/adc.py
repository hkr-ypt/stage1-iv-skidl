"""
adc.py — SHEET 7: ADC
=====================

ADS1256 24-bit delta-sigma ADC digitizing both sense channels differentially:

    AIN0/AIN1 : current sense  (I_SENSE_OUT vs AGND)
    AIN2/AIN3 : voltage sense  (V_SENSE_OUT vs AGND)

VREFP is the SAME 2.500 V reference (U2) that drives the DAC — the shared
reference that makes the measurement ratiometric (spec §C.2). First-order RC
filters (R15/R16/C24, R17/R18/C25) band-limit noise at the modulator inputs.

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
    R19 = R()(ref="R19", tag="R19", value="10k")
    R20 = R()(ref="R20", tag="R20", value="10k")
    R15 = R()(ref="R15", tag="R15", value="100R")
    R16 = R()(ref="R16", tag="R16", value="100R")
    R17 = R()(ref="R17", tag="R17", value="100R")
    R18 = R()(ref="R18", tag="R18", value="100R")
    C24 = C()(ref="C24", tag="C24", value="1nF")
    C25 = C()(ref="C25", tag="C25", value="1nF")
    TP8 = TestPoint()(ref="TP8", tag="TP8", value="VREF_2V5")

    # ---- analog supply ----
    n.P5VA += U8["AVDD"], C28[1], C29["+"]
    n.AGND += U8["AGND"], C28[2], C29["-"]

    # ---- digital supply + pull-ups ----
    n.P3V3D += U8["DVDD"], C30[1], R19[1], R20[1]
    n.DGND += U8["DGND"], C30[2]

    # ---- reference (shared with DAC) ----
    n.VREF_2V5 += U8["VREFP"], TP8[1]
    n.AGND += U8["VREFN"]

    # ---- CLKIN crystal ----
    # On the real ADS1256 the oscillator input and the external-clock input are
    # ONE pad, named XTAL1/CLKIN (pin 19, SBAS288K Terminal Functions p.5).
    # The previous model declared XTAL1 and CLKIN as two separate pins.
    clk1 = Net("ADC_XTAL1"); clk1 += U8["XTAL1/CLKIN"], Y1[1], C26[1]
    clk2 = Net("ADC_XTAL2"); clk2 += U8["XTAL2"], Y1[2], C27[1]
    n.AGND += C26[2], C27[2]

    # ---- current channel: AIN0/AIN1 with RC filter ----
    n.I_SENSE_OUT += R15[1]
    ain0 = Net("ADC_AIN0"); ain0 += R15[2], U8["AIN0"], C24[1]
    n.AGND += R16[1]
    ain1 = Net("ADC_AIN1"); ain1 += R16[2], U8["AIN1"], C24[2]

    # ---- voltage channel: AIN2/AIN3 with RC filter ----
    n.V_SENSE_OUT += R17[1]
    ain2 = Net("ADC_AIN2"); ain2 += R17[2], U8["AIN2"], C25[1]
    n.AGND += R18[1]
    ain3 = Net("ADC_AIN3"); ain3 += R18[2], U8["AIN3"], C25[2]

    n.AGND += U8["AINCOM"]

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
    # differential channels (AIN0/1 and AIN2/3) and uses no general-purpose
    # digital I/O, so the remaining 8 pads are declared unconnected here rather
    # than being left out of the symbol.
    nc_named(U8,
             "AIN4", "AIN5", "AIN6", "AIN7",   # spare analog inputs
             "D0/CLKOUT", "D1", "D2", "D3")    # general-purpose digital I/O

    return {"U8": U8, "Y1": Y1, "C26": C26, "C27": C27, "C28": C28, "C29": C29,
            "C30": C30, "R19": R19, "R20": R20, "R15": R15, "R16": R16,
            "R17": R17, "R18": R18, "C24": C24, "C25": C25, "TP8": TP8}
