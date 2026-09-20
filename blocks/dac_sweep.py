"""
dac_sweep.py — SHEET 3: DAC & Sweep Level-Shift
===============================================

DAC8552 (16-bit, channel A used) drives a precision level-shift stage built from
the dual OPA2277:

    U3A : unity-gain buffer of the VREF/2 divider midpoint (low-Z VREF_HALF)
    U3B : 4-resistor difference amplifier

        V_SWEEP = (R4/R3) * (V_DAC - VREF_HALF)
                = (20.0k / 2.49k) * (V_DAC - 1.25V)   ->  +/-10.04 V

V_DAC spans 0..2.500 V, so (V_DAC - 1.25V) spans +/-1.250 V and the gain
20.0k/2.49k = 8.0321 puts V_SWEEP at +/-10.040 V (spec §C.5).

R4 = R6 = 20.0k BRACKETS the +/-10 V target rather than landing on it: an exact
+/-10.000 V needs 8.000 * 2.49k = 19.92k, which is in no standard series, and
the nearest value below it (19.8k, E192) reaches only +/-9.94 V and so could
never get to 10 V at all. 20.0k is E96 at the same 0.1% grade as R3/R5, and
firmware simply leaves the top 0.40% of the DAC's codes unused.

U5 headroom for that range (OPA548 on +/-15VA, Sheet 4). U5's feedback is taken
past R9, so its OUT pin must reach V_SWEEP + I_LIM*R9 = 10.040 + 0.04935*10 =
10.53 V, and its "+IN" sits at V_SWEEP = 10.04 V. SBOS070 guarantees, already
at I_O = 0.6 A (12x this design's 49 mA limit):

    output swing    (V+)-2.4 = +12.6 V  vs +10.53 V needed  ->  2.07 V spare
                    (V-)+1.3 = -13.7 V  vs -10.53 V needed  ->  3.17 V spare
    input com-mode  (V+)-3.0 = +12.0 V  vs +10.04 V on +IN  ->  1.96 V spare

so the rails deliver +/-10.04 V with margin. D5 (SMBJ15CA, 15 V standoff,
Sheet 8) still stands off above it and does not clip it.

Channel B (VOUTB) is reserved/unpopulated.
"""

from libraries.custom_symbols import DAC8552, OPA2277, R, C, TestPoint
from skidl import Net
from .nets import nc


def create_dac_block(n):
    U4 = DAC8552()(ref="U4", tag="U4", value="DAC8552IDGKR")
    C13 = C()(ref="C13", tag="C13", value="100nF")
    U3 = OPA2277()(ref="U3", tag="U3", value="OPA2277UA")
    C11 = C()(ref="C11", tag="C11", value="100nF")
    C12 = C()(ref="C12", tag="C12", value="100nF")
    R1 = R()(ref="R1", tag="R1", value="10.0k 0.1%")
    R2 = R()(ref="R2", tag="R2", value="10.0k 0.1%")
    R3 = R()(ref="R3", tag="R3", value="2.49k 0.1%")
    R5 = R()(ref="R5", tag="R5", value="2.49k 0.1%")
    R4 = R()(ref="R4", tag="R4", value="20.0k 0.1%")
    R6 = R()(ref="R6", tag="R6", value="20.0k 0.1%")
    TP1 = TestPoint()(ref="TP1", tag="TP1", value="V_SWEEP")

    # ---- DAC power + SPI1 (write-only) ----
    n.P5VA += U4["VDD"], C13[1]
    n.AGND += C13[2], U4["GND"]
    n.CS_DAC += U4["SYNC"]
    n.SCK1 += U4["SCLK"]
    n.MOSI1 += U4["DIN"]
    n.VREF_2V5 += U4["VREF"], R1[1]
    n.V_DAC += U4["VOUTA"], R3[1]
    nc(U4["VOUTB"])                      # channel B reserved / unpopulated

    # ---- VREF/2 divider + U3A unity buffer ----
    vref_half_raw = Net("VREF_HALF_RAW")
    vref_half_raw += R1[2], R2[1], U3["+INA"]
    n.AGND += R2[2]
    n.VREF_HALF += U3["OUTA"], U3["-INA"], R5[1]   # buffered, low-Z

    # ---- U3B 4-resistor difference amplifier ----
    node_p = Net("DIFF_P"); node_p += R3[2], R4[1], U3["+INB"]
    n.AGND += R4[2]
    node_n = Net("DIFF_N"); node_n += R5[2], R6[1], U3["-INB"]
    n.V_SWEEP += U3["OUTB"], R6[2], TP1[1]

    # ---- op-amp supplies ----
    n.P15VA += U3["V+"], C11[1]
    n.AGND += C11[2]
    n.N15VA += U3["V-"], C12[1]
    n.AGND += C12[2]

    return {
        "U4": U4, "C13": C13, "U3": U3, "C11": C11, "C12": C12,
        "R1": R1, "R2": R2, "R3": R3, "R4": R4, "R5": R5, "R6": R6, "TP1": TP1,
    }
