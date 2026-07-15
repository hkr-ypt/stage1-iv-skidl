"""
protection.py — SHEET 8: Protection & DUT Connector
===================================================

Defense-in-depth on the FORCE+ path, read in order (spec §C.9):

    VO_SENSE -> F1 (PTC fuse) -> R30 (100R series) -> D5 (TVS to AGND) -> J4.FORCE+

Each stage covers a failure mode the previous one does not: OPA548's active
ILIM (Sheet 4) is fast/primary; F1 is the slow backstop if ILIM fails; R30 is a
passive dead-short limiter; D5 (15 V standoff) absorbs transients without
clipping legitimate +/-5 V (up to ~+/-11 V compliance) excursions.

J4 exposes the four Kelvin wires. FORCE-/SENSE+/SENSE- pass straight through to
Sheets 5/6 (their protection lives on those sheets).
"""

from libraries.custom_symbols import Fuse_PTC, R, D_TVS, Conn_DUT_1x04
from skidl import Net


def create_protection_block(n):
    F1 = Fuse_PTC()(ref="F1", tag="F1", value="0ZRM0010FF1E",
                    footprint="Fuse:Fuse_Bourns-MF-RG300")
    R30 = R()(ref="R30", tag="R30", value="100R 1/4W",
              footprint="Resistor_SMD:R_1206_3216Metric")
    D5 = D_TVS()(ref="D5", tag="D5", value="SMBJ15CA")
    J4 = Conn_DUT_1x04()(ref="J4", tag="J4", value="DUT")

    # ---- FORCE+ protection chain ----
    n.VO_SENSE += F1[1]
    n.F1_MID += F1[2], R30[1]
    n.FORCE_PLUS += R30[2], D5["A1"], J4["FORCE+"]
    n.AGND += D5["A2"]                       # bidirectional TVS to AGND

    # ---- Kelvin wires pass-through to their sense sheets ----
    n.SENSE_PLUS += J4["SENSE+"]
    n.SENSE_MINUS += J4["SENSE-"]
    n.FORCE_MINUS += J4["FORCE-"]

    return {"F1": F1, "R30": R30, "D5": D5, "J4": J4}
