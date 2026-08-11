"""
output_buffer.py — SHEET 4: Power Buffer (Voltage Source Output Stage)
=====================================================================

OPA548 power buffer with native current limit (R7 sets I_LIMIT ~= 49 mA,
spec §C.4) and an in-the-loop output isolation resistor R9 (spec §C.6).

Feedback (VO_SENSE) is tapped on the FAR side of R9 so the amplifier regulates
the voltage delivered past the isolation resistor. R9's drop is NOT corrected
downstream — which is exactly why the independent Kelvin sense (Sheet 6) exists.

E/S is tied permanently enabled through R8 to +15VA (MCU-driven shutdown is a
deferred extension point, spec §C.6).
"""

from libraries.custom_symbols import OPA548, R, C, C_POL, TestPoint
from skidl import Net


def create_output_buffer_block(n):
    U5 = OPA548()(ref="U5", tag="U5", value="OPA548T")
    R7 = R()(ref="R7", tag="R7", value="1.43M",
             footprint="Resistor_SMD:R_1206_3216Metric")
    R8 = R()(ref="R8", tag="R8", value="10k")
    C14 = C()(ref="C14", tag="C14", value="0.01uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    C15 = C()(ref="C15", tag="C15", value="100nF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    C16 = C()(ref="C16", tag="C16", value="100nF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    C17 = C_POL("Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B")(
        ref="C17", tag="C17", value="10uF/35V")
    C18 = C_POL("Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B")(
        ref="C18", tag="C18", value="10uF/35V")
    R9 = R()(ref="R9", tag="R9", value="10R",
             footprint="Resistor_SMD:R_1206_3216Metric")
    C19 = C()(ref="C19", tag="C19", value="100pF")
    TP2 = TestPoint()(ref="TP2", tag="TP2", value="VO_SENSE")

    # ---- input ----
    n.V_SWEEP += U5["+IN"]

    # ---- supplies + decoupling ----
    n.P15VA += U5["V+"], C15[1], C17["+"]
    n.AGND += C15[2], C17["-"]
    n.N15VA += U5["V-"], C16[1], C18["-"]
    n.AGND += C16[2], C18["+"]

    # ---- current limit (R7 to -15VA, C14 across R7) ----
    ilim = Net("OPA548_ILIM"); ilim += U5["ILIM"], R7[1], C14[1]
    n.N15VA += R7[2], C14[2]

    # ---- enable/shutdown tied permanently enabled ----
    es = Net("OPA548_ES"); es += U5["E/S"], R8[1]
    n.P15VA += R8[2]

    # ---- output with in-the-loop isolation resistor R9 ----
    n.VO_RAW += U5["OUT"], R9[1], C19[1]
    n.VO_SENSE += R9[2], C19[2], TP2[1], U5["-IN"]   # feedback from FAR side of R9

    return {
        "U5": U5, "R7": R7, "R8": R8, "C14": C14, "C15": C15, "C16": C16,
        "C17": C17, "C18": C18, "R9": R9, "C19": C19, "TP2": TP2,
    }
