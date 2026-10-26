"""
dac_sweep.py — SHEET 3: DAC & Sweep Level-Shift
===============================================

DAC8552 (16-bit, both channels used) drives a precision level-shift stage built
from the dual OPA2277:

    U3A : unity-gain buffer of the R1/R2 midpoint (low-Z VREF_HALF)
    U3B : 4-resistor difference amplifier

        VREF_HALF = V_DACB * R2/(R1 + R2) = V_DACB / 2
        V_SWEEP   = (R4/R3) * (V_DAC - VREF_HALF)
                  = 8.0321 * (V_A - V_B/2)          V_A, V_B = DAC A/B outputs

Firmware writes channel B to full scale once per boot (V_B = 2.500 V, so
VREF_HALF = 1.250 V) and sweeps channel A over 0..2.500 V; then
(V_A - 1.25 V) spans +/-1.250 V and the gain 20.0k/2.49k = 8.0321 puts V_SWEEP
at +/-10.040 V (spec §C.5).

WHY THE MIDPOINT COMES FROM CHANNEL B, NOT FROM VREF_2V5 (audit F2)
-------------------------------------------------------------------
With R1 on VREF_2V5, VREF_HALF was a fixed 1.25 V and the DAC's guaranteed
power-on state (zero scale, SLAS430A) meant V_SWEEP = 8.03 * (0 - 1.25 V)
= -10.04 V: full negative output into the DUT from power-up until firmware
first wrote the DAC. With the midpoint on VOUTB, both channels power up at
zero scale and V_SWEEP = 8.03 * (0 - 0) = 0, residual offsets only.

    Bound            Mechanism                              Class
    -10.04 V min     V_A >= 0, V_B <= VREF: no code can     hardware
                     push V_SWEEP lower
    +10.04 V max     ONLY while channel B = full scale.     FIRMWARE CONTRACT
                     B below full scale raises the ceiling:
                     B = 0, A = FS commands +20.1 V. U3B is
                     guaranteed to swing to at least
                     (V+) - 1.2 V = +13.8 V (SBOS079C), so
                     the real ceiling is 13.8 V..the +15 V
                     rail, past U5's specified input range
                     ((V+) - 3 V = +12 V, SBOS070D).

The firmware requirements this creates are listed in docs/firmware_handoff.md
and are not hardware protection.

THE DAC REFERENCE GATE (U11) — RESET, HALT AND CRASH
----------------------------------------------------
The DAC8552 has no reset or clear pin and HOLDS its codes through an MCU reset,
a debugger halt or a firmware crash: the zero-scale power-on state is
guaranteed only at DAC power-up. So the hardware forces the output to zero by
another route. The DAC8552 is a resistor-string DAC whose outputs are
VREF * code / 65536 for both channels (SLAS430A, "Theory of operation"), and
both channels share one VREF pin. U11 (TMUX1101, SPST) connects that pin to
VREF_2V5 only while DAC_ARM is high; otherwise the pin is open and R35 pulls
it to AGND. With VREF = 0 both outputs are 0 V WHATEVER CODES THE DAC HOLDS,
so V_SWEEP = 8.03 * (0 - 0/2) = 0. Because the midpoint is ratiometric to the
same pin, the zero holds with no matching requirement.

DAC_ARM is not a GPIO level. It is the rectified output of a diode charge
pump (Sheet 2, D6/C47/C48/R37) fed by a SOFTWARE-toggled heartbeat on PC5:

    * no DC path from any supply to DAC_ARM exists; only edges transfer charge
    * a single GPIO transition reaches ~0.53 V, below U11's VIL (0.87 V)
    * >= 1 kHz heartbeat: DAC_ARM 2.4 V nominal, 1.9 V worst case
      (VOH 3.0 V, diodes 0.4 V, 10 uA leakage) vs VIH 1.49 V
    * heartbeat stops (reset, halt, crash, a stuck pin, MCU unpowered):
      DAC_ARM < 0.87 V, U11 off, within ~52 ms (C48 * R37 = 47 ms)

PC5 has no timer or peripheral output function on the STM32F411 (only
EVENTOUT), so no peripheral can keep toggling it once the CPU stops. The
pump's numbers are from a transient simulation of this circuit; the bring-up
plan measures them (docs/bringup_checklist.md).

U11's on-resistance (1.8 ohm typ, 4.9 ohm max at 125 C, SCDS410D) in series with
the DAC's 62 kohm reference input scales both channels by the same 30..80 ppm:
the A/B ratio and therefore
the zero point are unaffected, and the full-scale error calibrates out with
the rest of the source gain (it is a setpoint, not a measurement — V and I are
measured independently by the ADC).

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

C11/C12 sit on the +/-15 V rails and are rated 50 V X7R (audit: >= 25 V).
"""

from libraries.custom_symbols import (
    DAC8552, OPA2277, TMUX1101, R, C, TestPoint, PWR_FLAG,
)
from skidl import Net


def create_dac_block(n):
    U4 = DAC8552()(ref="U4", tag="U4", value="DAC8552IDGKR")
    C13 = C()(ref="C13", tag="C13", value="100nF")
    U3 = OPA2277()(ref="U3", tag="U3", value="OPA2277UA")
    C11 = C()(ref="C11", tag="C11", value="100nF 50V X7R")
    C12 = C()(ref="C12", tag="C12", value="100nF 50V X7R")
    R1 = R()(ref="R1", tag="R1", value="10.0k 0.1%")
    R2 = R()(ref="R2", tag="R2", value="10.0k 0.1%")
    R3 = R()(ref="R3", tag="R3", value="2.49k 0.1%")
    R5 = R()(ref="R5", tag="R5", value="2.49k 0.1%")
    R4 = R()(ref="R4", tag="R4", value="20.0k 0.1%")
    R6 = R()(ref="R6", tag="R6", value="20.0k 0.1%")
    TP1 = TestPoint()(ref="TP1", tag="TP1", value="V_SWEEP")
    U11 = TMUX1101()(ref="U11", tag="U11", value="TMUX1101DBVR")
    C46 = C()(ref="C46", tag="C46", value="100nF")
    R35 = R()(ref="R35", tag="R35", value="1M")
    # CS_DAC (SYNC) pull-up, at the DAC end: holds SYNC high while the MCU's
    # pins float (reset, boot, unpowered MCU), so no frame can be clocked in
    # during U11's ~50 ms disarm. To +5VA, the DAC's own rail, because only
    # that rail is on this side of the AGND/DGND split here; 100 k keeps the
    # current into PA4 when it is an input or driven to 3.3 V at <= 17 uA
    # (DS10314 allows +/-5 mA injection on TC pins; FT pins take 5 V). SYNC's
    # VINH is 2.4 V, so the MCU's 3.3 V drive still reads high.
    R34 = R()(ref="R34", tag="R34", value="100k")

    # ---- DAC power + SPI1 (write-only) ----
    n.P5VA += U4["VDD"], C13[1]
    n.AGND += C13[2], U4["GND"]
    n.CS_DAC += U4["SYNC"], R34[2]
    n.P5VA += R34[1]
    n.SCK1 += U4["SCLK"]
    n.MOSI1 += U4["DIN"]
    n.V_DAC += U4["VOUTA"], R3[1]
    v_dacb = Net("V_DACB"); v_dacb += U4["VOUTB"], R1[1]   # midpoint source

    # ---- DAC reference gate: VREF reaches the DAC only while armed ----
    dac_vref = Net("DAC_VREF"); dac_vref += U4["VREF"], U11["D"], R35[1]
    # U4's VREF pin is power_in. Its source is the REF5025 THROUGH U11, whose
    # D/S pins are passive, so ERC sees no driver on DAC_VREF — the same
    # situation as +5VA after FB1 and VDDA after FB2, and stated the same way.
    FLG9 = PWR_FLAG()(ref="#FLG9", tag="FLG9", value="DAC_VREF")
    dac_vref += FLG9[1]
    n.VREF_2V5 += U11["S"]
    n.DAC_ARM += U11["SEL"]                # charge-pump output, Sheet 2
    n.AGND += R35[2], U11["GND"]
    n.P5VA += U11["VDD"], C46[1]
    n.AGND += C46[2]

    # ---- V_DACB/2 divider + U3A unity buffer ----
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
        "U11": U11, "C46": C46, "R35": R35, "R34": R34, "FLG9": FLG9,
    }
