"""
protection.py — SHEET 8: Protection & DUT Connector
===================================================

Defense-in-depth on the FORCE+ path, read in order (spec §C.9):

    VO_SENSE -> F1 (PTC) -> R30 (100R) -> U12 (photorelay) -> D5 (TVS) -> J4

and on the FORCE- return:

    J7 -> F2 (PTC) -> ISENSE_HI (R10 shunt high side, U6 +IN) -> R10 -> AGND

What each stage does, and what it does NOT do:

  * U5's ILIM (OPA547, Sheet 4) sets the normal current plateau: centred
    near 49 mA, typically 42..59 mA depending on polarity and U5's
    temperature, and 9..89 mA with the data sheet's +/-30 mA maximum
    unit-to-unit tolerance on top (SBOS056F). It is a plateau, not a
    protection bound: nothing below relies on its value.
  * R30 (100 ohm) is the element that BOUNDS the current, passively. With U5's
    output driven hard into a rail (the worst credible fault: ILIM
    ineffective, the sweep saturated, or U5 itself failed) the current is set
    only by the series resistance outside U5's loop. The guaranteed minimum
    of each part (R9 1 %, F1 and F2 at their 3.0 ohm data-sheet minimum, R30
    1 % and its TCR at the hot spot, R10 0.1 %, U12 taken as 0 ohm) gives

        DUT short, FORCE+ to FORCE- .... 15.5 V / 121 ohm  ~ 128 mA
        D5 shorted (FORCE+ to AGND) .... 15.5 V / 108 ohm  ~ 143 mA

    validation/measurement_range.py recomputes both from the live values and
    fails if any resistor in the path is under-rated. R30 is a Vishay PR02
    (2 W at 70 C, hot spot <= 220 C), mounted with the body raised off the
    board (docs/physical_design.md); it carries <= 1.95 W in the D5-short case.
  * F1 and F2 are NOT backstops for that envelope. 0ZRM0010FF1E holds 100 mA
    and trips at 200 mA at 23 C (Bel 0ZRM data sheet); 128..143 mA lies
    between the two, where the data sheet promises neither. They may trip on
    a sustained fault, especially warm, and that is all. They derate: hold
    current is 68 mA at 50 C, 56 mA at 60 C and 45 mA at 70 C. F1 therefore
    sits away from U5's heat sink and R30 (docs/physical_design.md); if
    bench testing still shows nuisance trips, the contingency is 0ZRM0017FF1E
    (170 mA hold, same body, lead pitch and footprint) — conditional, not a
    default, and for F1 ONLY: its 2.0 ohm minimum resistance still leaves
    R30 inside its rating (1.99 W of 2 W), but in F2 it would let an external
    source on J7 put 16.7 W into R10, above the 15 W short-time rating the
    next bullet relies on.
  * F2 is the FORCE- protection, against what F1/R30 cannot see: an EXTERNAL
    source on J7 (a miswired bench lead, an energised DUT). J7 otherwise sees
    10 ohm to AGND: 15.5 V there is 1.5 A and 24 W in R10. With F2 in series
    the current is <= 15.5 V / (3.0 + 9.99) ohm = 1.19 A, inside F2's 2 A
    Imax, and R10 is a 3 W wirewound (Sheet 5) whose short-time-overload
    rating (5 x 3 W for 5 s) covers the ~14 W it carries until F2 trips
    (~0.6 s typical at 1.2 A, Bel curve A; the data sheet bounds it only at
    0.5 A, <= 10 s). Recalibrate R10 after any such event. F2 is in SERIES:
    nothing is connected across the shunt, so it adds no leakage path, does
    not touch the Kelvin pair (J5/J6), is symmetric for signed current and
    leaves AGND where it was. Its 3..7.5 ohm sits outside both measurements;
    it costs compliance only (<= 0.7 V at 91 mA). A clamp at J7 was rejected:
    anything from FORCE- to AGND is a path AROUND R10, i.e. unmeasured
    current at the nA levels this instrument characterises.
  * U12 (TLP176AM photorelay) DISCONNECTS FORCE+ unless BOTH +/-15 V rails are
    up. Its LED is driven by a string across the two rails:

        +15VA -> R39 (620R) -> U12 LED -> D7 (BZT52B24) -> -15VA

    With either rail absent (held within 0.51 V of AGND by the rail clamps
    D8/D9, Sheet 1) the string has <= 15.5 + 0.51 = 16.0 V across it, below
    D7's 18 V leakage test point (IR <= 0.1 uA, Vishay doc. 86342), so
    IF <= 0.1 uA, a thousandth of U12's 0.1 mA guaranteed-off (IFC) current:
    U12 is OPEN, guaranteed by data-sheet limits at 25 C.

    CLOSED needs IF >= IFT (3 mA max at 25 C; 10 % more is allowed here at
    50 C, the typical curve, Toshiba Fig. 14.1.6, rises 7 %), and the string
    only has (rails) - VZ - VF = ~4.7 V of headroom for R39, so D7's voltage
    decides it. The first analysis took D7 at its pulsed data-sheet voltage.
    In service D7 heats itself (~140 mW at the operating point, RthJA
    420 K/W, up to +10e-4/K), which puts its voltage ~1.3 V higher. With
    that in (final audit; minimum = D7 at its upper limit and upper tempco
    with no fall of VZ below the 5 mA test current, LED at 1.4 V, R39 +1 %):

                              BZT52C24 (+/-6 %, was)   BZT52B24 (+/-2 %, now)
        typical IF, +/-15.0 V        5.6 mA                  5.6 mA
        minimum IF, +/-15.0 V, 25 C  3.4 mA                  4.7 mA
        minimum IF, +/-15.0 V, 50 C  2.7 mA (< IFT)          4.0 mA
        closed guaranteed from       +/-14.8 V (25 C)        +/-14.25 V (25 C)
                                     +/-15.3 V (50 C)        +/-14.7 V (50 C)

    The +/-6 % part did not guarantee a closed relay at the nominal rails
    once warm; the +/-2 % part does (and still does, from +/-14.8 V at 50 C,
    if the board cools D7 30 % worse than the data sheet's test board), with
    the same nominal voltage, the same footprint and a better leakage test
    point. Lowering R39 instead would raise D7's dissipation, which is what
    limits this string. Toshiba's recommended IF is >= 5 mA; the typical
    5.6 mA meets it, the guaranteed minimum does not, and RON is specified
    at IF = 5 mA: the guarantee above is "closed" (IF >= IFT), which is what
    protection needs, and U12's resistance is outside both U5's loop and the
    Kelvin sense in any case. Between open and closed U12 is not guaranteed
    either way, but any state in that band has >= 16 V across U5, twice its
    8 V minimum supply, so U5 is operating. Typical: closes near 26 V
    total. At +/-15.5 V with D7 at its lowest voltage the string carries
    7.9 mA (D7 0.20 W of 0.30 W on FR-4, junction 108 C at 25 C ambient and
    145 C of 150 C at 70 C; with the +/-6 % part it was 8.9 mA, 0.22 W,
    116 C and 153 C), and <= 8.5 mA if the board cools D7 better than the
    data sheet's 420 K/W; R39 <= 0.05 W. U12 adds <= 2 ohm (data sheet) to
    FORCE+, outside U5's loop and outside the Kelvin sense; taken as 0 ohm
    for the fault bound. Off, it blocks 60 V and leaks <= 1 uA.
  * D5 (SMBJ15CA, 15 V standoff) clamps transients into J4 without clipping
    the legitimate +/-10.04 V sweep (Sheet 3), and shields U12's output from
    anything above its 60 V rating.

R30 also costs DUT compliance, and more so now that the sweep reaches
+/-10.04 V: at the 49 mA ILIM it drops 4.94 V and R10 (Sheet 5) another
0.49 V, so what stands across the DUT at full current is only ~+/-4.6 V (less
again by F1, F2 and U12). At the micro- and milliamp currents where diode
reverse leakage and the sub-threshold knee are characterised, those drops
are millivolts and essentially the whole +/-10.04 V reaches the DUT.

DUT CONNECTOR — FOUR SEPARATE JACKS
-----------------------------------
The four Kelvin wires used to be one 4-position part. There is no 4-position
banana jack in the KiCad libraries (only ``Connector:Banana_Jack_1Pin`` /
``_2Pin`` / ``_3Pin``), and inventing one would have put four nets onto a
footprint that does not exist. Each terminal is therefore its own single-pin
panel jack:

    J4  FORCE+     J5  SENSE+     J6  SENSE-     J7  FORCE-

Each jack's single pin is aliased to its function name, so the connections
below still read by function, never by pad number. SENSE+/SENSE- pass
straight through to Sheet 6 (their protection lives there); FORCE- reaches
Sheet 5 as ISENSE_HI, through F2.
"""

from libraries.custom_symbols import (
    Fuse_PTC, R, D_TVS, D_Zener, BananaJack, PhotoRelay_TLP176AM,
)
from skidl import Net


def create_protection_block(n):
    F1 = Fuse_PTC()(ref="F1", tag="F1", value="0ZRM0010FF1E")
    # Vishay PR02, 100 ohm +/-1 %, COPPER 0.78 mm leads, straight (axial),
    # taped in an ammo pack: PR0200 0 2 0 1000 F A1 00 (Vishay doc. 28729,
    # "Part number" and "Termination wire types"). The lead material is part
    # of the rating: the Cu-lead PR02 is the 2 W / 75 K/W part this design
    # needs, the FeCu-lead one is 1.3 W / 115 K/W. The number used before
    # ended in ...FB100: packaging B1 exists only with FeCu 0.78 mm leads,
    # pre-formed ("double kink") at 15 mm pitch, so that number named no real
    # part, and the nearest real one is the 1.3 W version at the wrong pitch.
    # DIN 0411 body (10.0 x 3.9 mm max): the footprint is KiCad's DIN0411 at
    # 12.7 mm pitch with 1.2 mm holes for the 0.78 mm leads. Form the leads
    # by hand and mount with the body raised ~3 mm (lead kink or spacer).
    R30 = R()(ref="R30", tag="R30", value="100R 1% 2W",
              footprint="Resistor_THT:"
                        "R_Axial_DIN0411_L9.9mm_D3.6mm_P12.70mm_Horizontal")
    R30.fields["MPN"] = "PR02000201000FA100"

    D5 = D_TVS()(ref="D5", tag="D5", value="SMBJ15CA")

    # FORCE- series PTC: same part as F1 (see header).
    F2 = Fuse_PTC()(ref="F2", tag="F2", value="0ZRM0010FF1E")

    # Both-rails-present DUT disconnect (see header).
    U12 = PhotoRelay_TLP176AM()(ref="U12", tag="U12", value="TLP176AM")
    R39 = R()(ref="R39", tag="R39", value="620R 1% 0.25W",
              footprint="Resistor_SMD:R_1206_3216Metric")
    D7 = D_Zener()(ref="D7", tag="D7", value="BZT52B24")

    # One panel-mount 4 mm banana jack per DUT terminal.
    J4 = BananaJack("FORCE+")(ref="J4", tag="J4", value="DUT FORCE+")
    J5 = BananaJack("SENSE+")(ref="J5", tag="J5", value="DUT SENSE+")
    J6 = BananaJack("SENSE-")(ref="J6", tag="J6", value="DUT SENSE-")
    J7 = BananaJack("FORCE-")(ref="J7", tag="J7", value="DUT FORCE-")

    # ---- FORCE+ protection chain ----
    n.VO_SENSE += F1[1]
    n.F1_MID += F1[2], R30[1]
    # U12's output is bidirectional (AC/DC), so OUT1/OUT2 are assigned to
    # suit the board: OUT2 (pin 6) faces R30, OUT1 (pin 4) faces D5.
    force_sw = Net("FORCE_SW")               # R30 -> photorelay
    force_sw += R30[2], U12["OUT2"]
    n.FORCE_PLUS += U12["OUT1"], D5["A1"], J4["FORCE+"]
    n.AGND += D5["A2"]                       # bidirectional TVS to AGND

    # ---- photorelay LED string across both rails ----
    led_a = Net("SSR_LED_A"); led_a += R39[2], U12["A"]
    led_k = Net("SSR_LED_K"); led_k += U12["K"], D7["K"]
    n.P15VA += R39[1]
    n.N15VA += D7["A"]

    # ---- FORCE- return: series PTC, then the shunt (Sheet 5) ----
    n.FORCE_MINUS += J7["FORCE-"], F2[1]
    n.ISENSE_HI += F2[2]

    # ---- Kelvin wires pass-through to their sense sheet ----
    n.SENSE_PLUS += J5["SENSE+"]
    n.SENSE_MINUS += J6["SENSE-"]

    return {"F1": F1, "R30": R30, "D5": D5, "F2": F2, "U12": U12,
            "R39": R39, "D7": D7,
            "J4": J4, "J5": J5, "J6": J6, "J7": J7}
