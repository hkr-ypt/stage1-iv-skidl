"""
mcu.py — SHEET 2: MCU & Digital I/O
===================================

STM32F411RET6 with its own SWD header, HSE crystal, USB OTG-FS front end
(USB-C + ESD array), reset and boot circuitry, and status LED.

Two PHYSICALLY SEPARATE SPI buses (spec §C.1):
    SPI1 (PA5/PA7/PA4)  -> DAC  (write-only)
    SPI2 (PB13/15/14/12)-> ADC  (bidirectional)

NOTE — C43 (VCAP1 decoupling, 2.2uF) is added here for electrical completeness:
the STM32F4 internal LDO requires it and it was absent from the source component
table. All other parts match the source Sheet-2 BOM.
"""

from libraries.custom_symbols import (
    STM32F411RET6, Crystal, R, C, C_POL, L_FerriteBead, LED, SW_Push,
    Header_1x05, USB_C_Receptacle, USBLC6_2SC6,
)
from skidl import Net
from .nets import nc, nc_named


def create_mcu_block(n):
    U9 = STM32F411RET6()(ref="U9", tag="U9", value="STM32F411RET6")
    Y2 = Crystal()(ref="Y2", tag="Y2", value="8MHz",
                   footprint="Crystal:Crystal_SMD_HC49-SD")
    C32 = C()(ref="C32", tag="C32", value="20pF")
    C33 = C()(ref="C33", tag="C33", value="20pF")
    C34 = C()(ref="C34", tag="C34", value="100nF")
    C35 = C()(ref="C35", tag="C35", value="100nF")
    C36 = C()(ref="C36", tag="C36", value="100nF")
    C37 = C()(ref="C37", tag="C37", value="100nF")
    C38 = C()(ref="C38", tag="C38", value="100nF")
    C39 = C()(ref="C39", tag="C39", value="4.7uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    C40 = C()(ref="C40", tag="C40", value="1uF")
    C43 = C()(ref="C43", tag="C43", value="2.2uF")  # VCAP1 (added, see header)
    FB2 = L_FerriteBead()(ref="FB2", tag="FB2", value="BLM21PG221SN1D")
    R21 = R()(ref="R21", tag="R21", value="10k")
    C41 = C()(ref="C41", tag="C41", value="100nF")
    SW1 = SW_Push()(ref="SW1", tag="SW1", value="RESET")
    R22 = R()(ref="R22", tag="R22", value="10k")
    R23 = R()(ref="R23", tag="R23", value="330R")
    LED1 = LED()(ref="LED1", tag="LED1", value="GREEN")
    J2 = Header_1x05()(ref="J2", tag="J2", value="SWD")
    J3 = USB_C_Receptacle()(ref="J3", tag="J3", value="USB-C")
    U10 = USBLC6_2SC6()(ref="U10", tag="U10", value="USBLC6-2SC6")
    R24 = R()(ref="R24", tag="R24", value="5.1k")
    R25 = R()(ref="R25", tag="R25", value="5.1k")
    R26 = R()(ref="R26", tag="R26", value="22R")
    R27 = R()(ref="R27", tag="R27", value="22R")
    R28 = R()(ref="R28", tag="R28", value="100k")
    R29 = R()(ref="R29", tag="R29", value="100k")
    C42 = C()(ref="C42", tag="C42", value="100nF")

    # ---- power ----
    # U9["VDD"] resolves to ALL FOUR VDD pads of the LQFP-64 (19/32/48/64) and
    # U9["VSS"] to all four VSS pads (18/31/47/63); every rail pad is bonded.
    n.P3V3D += (U9["VDD"], U9["VBAT"], C34[1], C35[1], C36[1], C37[1],
                C39[1], FB2[1], R21[1], J2["Pin_4"])
    n.DGND += (U9["VSS"], U9["VSSA"], C34[2], C35[2], C36[2], C37[2], C39[2])
    n.VDDA += FB2[2], U9["VDDA"], C38[1], C40[1]
    n.DGND += C38[2], C40[2]
    vcap = Net("VCAP1"); vcap += U9["VCAP1"], C43[1]
    n.DGND += C43[2]

    # ---- HSE crystal ----
    osc_in = Net("OSC_IN"); osc_in += U9["PH0"], Y2[1], C32[1]
    osc_out = Net("OSC_OUT"); osc_out += U9["PH1"], Y2[2], C33[1]
    n.DGND += C32[2], C33[2]

    # ---- reset / boot ----
    n.NRST += U9["NRST"], R21[2], C41[1], SW1[1], J2["Pin_5"]
    n.DGND += C41[2], SW1[2]
    n.BOOT0 += U9["BOOT0"], R22[1]
    n.DGND += R22[2]

    # ---- SWD ----
    n.SWDIO += U9["PA13"], J2["Pin_1"]
    n.SWCLK += U9["PA14"], J2["Pin_2"]
    n.DGND += J2["Pin_3"]

    # ---- SPI1 (DAC, write-only) ----
    n.SCK1 += U9["PA5"]
    n.MOSI1 += U9["PA7"]
    n.CS_DAC += U9["PA4"]

    # ---- SPI2 (ADC, bidirectional) ----
    n.SCK2 += U9["PB13"]
    n.MOSI2 += U9["PB15"]
    n.MISO2 += U9["PB14"]
    n.CS_ADC += U9["PB12"]
    n.DRDY += U9["PA8"]
    n.RESET_ADC += U9["PA9"]

    # ---- USB OTG-FS with series R + ESD array ----
    n.USB_DM += U9["PA11"], R26[1]
    esd_dm = Net("USB_DM_ESD"); esd_dm += R26[2], U10["IO1b"]
    conn_dm = Net("USB_DM_CONN"); conn_dm += U10["IO1"], J3["DM"]

    n.USB_DP += U9["PA12"], R27[1]
    esd_dp = Net("USB_DP_ESD"); esd_dp += R27[2], U10["IO2b"]
    conn_dp = Net("USB_DP_CONN"); conn_dp += U10["IO2"], J3["DP"]

    # J3["VBUS"] is all four VBUS pads (A4, A9, B4, B9) of the receptacle.
    n.VBUS += J3["VBUS"], U10["VBUS"], R28[1], C42[1]
    n.DGND += C42[2], U10["GND"]
    n.VBUS_SENSE += R28[2], R29[1], U9["PA0"]
    n.DGND += R29[2]

    # USB-C CC pull-downs (UFP identification)
    cc1 = Net("CC1"); cc1 += J3["CC1"], R24[1]
    cc2 = Net("CC2"); cc2 += J3["CC2"], R25[1]
    n.DGND += R24[2], R25[2]

    # USB-C ground: all four GND pads (A1, A12, B1, B12).
    n.DGND += J3["GND"]

    # USB-C shield (pad S1, four physical shell tabs): tied DIRECTLY to DGND.
    # This is a deliberate choice, not an omission. The board is bus-powered
    # and single-enclosure, so there is no chassis/signal-ground potential
    # difference for an RC or ferrite path to manage; a direct tie gives the
    # lowest-impedance return for cable-shield ESD and CM currents and keeps
    # the shell from floating. The analog ground is unaffected: AGND reaches
    # DGND only through the R31 star point (see blocks/power.py).
    n.DGND += J3["SHIELD"]

    # SBU1/SBU2 (pads A8/B8) carry no sideband signalling in this design.
    nc_named(J3, "SBU1", "SBU2")

    # ---- status LED ----
    n.LED_STATUS += U9["PC13"], R23[1]
    led_a = Net("LED_A"); led_a += R23[2], LED1["A"]
    n.DGND += LED1["K"]

    # ---- unused MCU pins: explicit no-connects ----
    # The LQFP-64 has 64 pads. 31 are used above; the remaining 33 GPIOs are
    # deliberately unpopulated in Stage 1 and are declared here so that no pad
    # is silently absent from the netlist (validation/footprint_check.py
    # errors on any pad that is neither netted nor listed here).
    nc_named(
        U9,
        "PC14", "PC15",                                  # LSE oscillator (unused)
        "PC0", "PC1", "PC2", "PC3",                      # port C low
        "PA1", "PA2", "PA3", "PA6",                      # port A spares
        "PC4", "PC5",
        "PB0", "PB1", "PB2", "PB10",                     # port B spares
        "PC6", "PC7", "PC8", "PC9",
        "PA10",                                          # USB_ID (device only)
        "PA15",
        "PC10", "PC11", "PC12", "PD2",
        "PB3", "PB4", "PB5", "PB6", "PB7", "PB8", "PB9",
    )

    return {
        "U9": U9, "Y2": Y2, "C32": C32, "C33": C33, "C34": C34, "C35": C35,
        "C36": C36, "C37": C37, "C38": C38, "C39": C39, "C40": C40, "C43": C43,
        "FB2": FB2, "R21": R21, "C41": C41, "SW1": SW1, "R22": R22, "R23": R23,
        "LED1": LED1, "J2": J2, "J3": J3, "U10": U10, "R24": R24, "R25": R25,
        "R26": R26, "R27": R27, "R28": R28, "R29": R29, "C42": C42,
    }
