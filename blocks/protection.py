"""
protection.py — SHEET 8: Protection & DUT Connector
===================================================

Defense-in-depth on the FORCE+ path, read in order (spec §C.9):

    VO_SENSE -> F1 (PTC fuse) -> R30 (100R series) -> D5 (TVS to AGND) -> J4

Each stage covers a failure mode the previous one does not: OPA548's active
ILIM (Sheet 4) is fast/primary; F1 is the slow backstop if ILIM fails; R30 is a
passive dead-short limiter; D5 (15 V standoff) absorbs transients without
clipping legitimate +/-5 V (up to ~+/-11 V compliance) excursions.

DUT CONNECTOR — FOUR SEPARATE JACKS
-----------------------------------
The four Kelvin wires used to be one 4-position part. There is no 4-position
banana jack in the KiCad libraries (only ``Connector:Banana_Jack_1Pin`` /
``_2Pin`` / ``_3Pin``), and inventing one would have put four nets onto a
footprint that does not exist. Each terminal is therefore its own single-pin
panel jack:

    J4  FORCE+     J5  SENSE+     J6  SENSE-     J7  FORCE-

Each jack's single pin is aliased to its function name, so the connections
below still read by function, never by pad number. FORCE-/SENSE+/SENSE- pass
straight through to Sheets 5/6 (their protection lives on those sheets).
"""

from libraries.custom_symbols import Fuse_PTC, R, D_TVS, BananaJack
from skidl import Net


def create_protection_block(n):
    F1 = Fuse_PTC()(ref="F1", tag="F1", value="0ZRM0010FF1E")
    R30 = R()(ref="R30", tag="R30", value="100R 1/4W",
              footprint="Resistor_SMD:R_1206_3216Metric")
    D5 = D_TVS()(ref="D5", tag="D5", value="SMBJ15CA")

    # One panel-mount 4 mm banana jack per DUT terminal.
    J4 = BananaJack("FORCE+")(ref="J4", tag="J4", value="DUT FORCE+")
    J5 = BananaJack("SENSE+")(ref="J5", tag="J5", value="DUT SENSE+")
    J6 = BananaJack("SENSE-")(ref="J6", tag="J6", value="DUT SENSE-")
    J7 = BananaJack("FORCE-")(ref="J7", tag="J7", value="DUT FORCE-")

    # ---- FORCE+ protection chain ----
    n.VO_SENSE += F1[1]
    n.F1_MID += F1[2], R30[1]
    n.FORCE_PLUS += R30[2], D5["A1"], J4["FORCE+"]
    n.AGND += D5["A2"]                       # bidirectional TVS to AGND

    # ---- Kelvin wires pass-through to their sense sheets ----
    n.SENSE_PLUS += J5["SENSE+"]
    n.SENSE_MINUS += J6["SENSE-"]
    n.FORCE_MINUS += J7["FORCE-"]

    return {"F1": F1, "R30": R30, "D5": D5,
            "J4": J4, "J5": J5, "J6": J6, "J7": J7}
