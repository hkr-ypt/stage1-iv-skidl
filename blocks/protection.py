"""
protection.py — SHEET 8: Protection & DUT Connector
===================================================

Defense-in-depth on the FORCE+ path, read in order (spec §C.9):

    VO_SENSE -> F1 (PTC fuse) -> R30 (100R series) -> D5 (TVS to AGND) -> J4

What each stage does, and what it does NOT do:

  * OPA548 ILIM (Sheet 4) sets the normal current plateau, ~49 mA nominal.
    It is not characterised at this setting (SBOS070D specifies ILIM accuracy
    only at 2.5 A), so nothing below relies on its value.
  * R30 (100 ohm) is the element that BOUNDS the current, passively. With U5's
    output driven hard into a rail (the worst credible fault: ILIM
    ineffective, the sweep saturated, or U5 itself failed) the current is set
    only by R9 + F1 + R30 + R10:
        15.5 V / (9.9 + 3.0 + 99 + 9.9) ohm = 127 mA,  1.60 W in R30
    so R30 must carry that continuously. It is a Vishay PR02 (2 W at 70 C,
    hot spot <= 220 C, Rth 75 K/W -> ~120 K rise at 1.6 W), mounted with the
    body raised off the board (docs/physical_design.md). It is placed 7 mm
    (courtyards) from the precision divider (R12/R13/R32/R33). It is NOT far
    from F1: R30 sits electrically between F1 and D5/J4, and the only room
    within reach is the strip beside F1, so the courtyards are 2.1 mm apart.
    R30's heat lowers F1's hold current. In a fault that makes F1 trip sooner;
    on the normal 49 mA plateau (0.24 W in R30) it eats into the hold margin,
    which bring-up measures (docs/bringup_checklist.md). Real separation needs
    the protection area re-laid out.
  * F1 is NOT a backstop. 0ZRM0010FF1E holds 100 mA and trips at 200 mA at
    23 C (Bel 0ZRM data sheet); the whole fault envelope above (<= 127 mA)
    lies between the two, where the data sheet promises neither. It may trip
    on a sustained fault, especially warm, and that is all. It also derates:
    hold current is 68 mA at 50 C, 56 mA at 60 C and 45 mA at 70 C, so above
    ~65 C ambient it can trip on the NORMAL 49 mA plateau. If bench testing
    shows nuisance trips, the contingency is 0ZRM0017FF1E (170 mA hold,
    same body and lead pitch, same footprint) — a conditional swap, not a
    default.
  * D5 (SMBJ15CA, 15 V standoff) clamps transients into J4 without clipping
    the legitimate +/-10.04 V sweep (Sheet 3).

R30 also costs DUT compliance, and more so now that the sweep reaches
+/-10.04 V: at the 49 mA ILIM it drops 4.94 V and R10 (Sheet 5) another
0.49 V, so what stands across the DUT at full current is only ~+/-4.6 V (less
again by F1's own resistance). At the micro- and milliamp currents where diode
reverse leakage and the sub-threshold knee are characterised, that drop is
millivolts and essentially the whole +/-10.04 V reaches the DUT.

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
    # Vishay PR02, 100 ohm +/-1 %, Cu 0.78 mm leads, loose in bulk
    # (PR02 part-number scheme, Vishay doc. 28729). DIN 0411 body: the
    # footprint is KiCad's DIN0411 at 12.7 mm pitch with 1.2 mm holes for the
    # 0.78 mm leads. Mount with the body raised ~3 mm (lead kink or spacer).
    R30 = R()(ref="R30", tag="R30", value="100R 1% 2W",
              footprint="Resistor_THT:"
                        "R_Axial_DIN0411_L9.9mm_D3.6mm_P12.70mm_Horizontal")
    R30.fields["MPN"] = "PR02000201000FB100"

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
