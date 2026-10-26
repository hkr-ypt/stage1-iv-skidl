#!/usr/bin/env bash
#
# test_guard_fails.sh — prove the structural guard actually fails.
#
# A check nobody has seen fail is not a check. This script copies the repo to a
# scratch directory, reintroduces one real defect at a time, and asserts that
# the responsible gate (validation/footprint_check.py for structural faults,
# validation/erc.py for electrical ones) AND main.py both exit non-zero. It
# never touches the working tree.
#
# Usage:  tools/test_guard_fails.sh
# Exit:   0 if every injected fault was caught, 1 otherwise.
#
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

FAILURES=0

reset_copy() {
    rm -rf "$WORK/repo"
    mkdir -p "$WORK/repo"
    tar -C "$REPO" --exclude=.git --exclude=__pycache__ -cf - . \
        | tar -C "$WORK/repo" -xf -
}

# expect_fail <gate> <name> <python-patch-script>
#   gate = structural : validation/footprint_check.py must fail
#   gate = erc        : validation/erc.py must fail
#   gate = board      : validation/routed_board_check.py must fail
#   gate = range      : validation/measurement_range.py must fail
#   gate = assert     : validation/assertions.py must fail
# main.py must fail in EVERY case — it is the thing a user actually runs.
#
# `board` injections run against the board that main.py already emitted, so the
# patch runs first, then main.py regenerates and re-checks. A board defect that
# only main.py catches, or only routed_board_check.py catches, counts as MISSED.
# The board is the board of record: circuit + layout/stage1_iv.layout +
# layout/*.eco (generators/routed_pcb.py). Injections 22-23 and 32 need
# KiCad 10's DRC, as the CI runner has; without it they are reported MISSED,
# not passed.
expect_fail() {
    local gate="$1" name="$2" patch="$3"
    reset_copy
    ( cd "$WORK/repo" && python3 -c "$patch" ) || {
        echo "  [SETUP FAIL] $name — could not apply the injection"
        FAILURES=$((FAILURES + 1)); return
    }

    local checker gate_rc main_rc
    case "$gate" in
        structural) checker="validation/footprint_check.py" ;;
        erc)        checker="validation/erc.py" ;;
        board)      checker="validation/routed_board_check.py" ;;
        range)      checker="validation/measurement_range.py" ;;
        assert)     checker="validation/assertions.py" ;;
        *) echo "  [BUG] unknown gate $gate"; FAILURES=$((FAILURES + 1)); return ;;
    esac

    # The board checks read the emitted .kicad_pcb, so it has to exist and be
    # built from the patched source before the gate can see the defect.
    if [ "$gate" = board ]; then
        ( cd "$WORK/repo" && python3 main.py --pcb-only ) \
            >"$WORK/pcb.log" 2>&1 || true
    fi

    ( cd "$WORK/repo" && python3 "$checker" ) >"$WORK/gate.log" 2>&1
    gate_rc=$?
    ( cd "$WORK/repo" && python3 main.py ) >"$WORK/main.log" 2>&1
    main_rc=$?

    if [ "$gate_rc" -ne 0 ] && [ "$main_rc" -ne 0 ]; then
        echo "  [CAUGHT] $name"
        echo "           $checker=$gate_rc, main.py=$main_rc"
        grep -m2 -E "\[ERROR\]|\[ERC WARNING\]|ERC FAILED|PWR_FLAG invariant|\
board defect|finding|netclass|straddle|overlap|MEASUREMENT RANGE|channel:|\
ASSERTIONS: FAIL|\[FAIL\]" \
            "$WORK/gate.log" | sed 's/^/            /' || true
    else
        echo "  [MISSED] $name  ($checker=$gate_rc, main.py=$main_rc)"
        tail -5 "$WORK/gate.log" | sed 's/^/            /'
        FAILURES=$((FAILURES + 1))
    fi
}

echo "======================================================================"
echo "GUARD FAULT-INJECTION TEST"
echo "======================================================================"

# 1. a symbol identifier that does not exist upstream (the INA128P defect)
expect_fail structural "bad symbol lib_id (Amplifier_Instrumentation:INA128P)" '
import pathlib
p = pathlib.Path("libraries/custom_symbols.py"); s = p.read_text()
assert "Amplifier_Instrumentation:INA128" in s
p.write_text(s.replace("Amplifier_Instrumentation:INA128\"",
                       "Amplifier_Instrumentation:INA128P\""))
'

# 2. a footprint identifier that does not exist upstream (the F1 typo)
expect_fail structural "bad footprint id (Fuse:Fuse_Bourns-MF-RG300)" '
import pathlib
p = pathlib.Path("libraries/custom_symbols.py"); s = p.read_text()
assert "Fuse:Fuse_Bourns_MF-RG300" in s
p.write_text(s.replace("Fuse:Fuse_Bourns_MF-RG300", "Fuse:Fuse_Bourns-MF-RG300"))
'

# 3. a placeholder provenance flag creeping back in
expect_fail structural "Pins_Verified == PLACEHOLDER-REMAP" '
import pathlib
p = pathlib.Path("libraries/custom_symbols.py"); s = p.read_text()
assert "\"ADS1256IDBR\", \"RESOLVED\"" in s
p.write_text(s.replace("\"ADS1256IDBR\", \"RESOLVED\"",
                       "\"ADS1256IDBR\", \"PLACEHOLDER-REMAP\""))
'

# 4. a pad silently left with no net and no no-connect
expect_fail structural "pad left unconnected (ADS1256 AIN4..AIN7, D0..D3)" '
import pathlib
p = pathlib.Path("blocks/adc.py"); s = p.read_text()
assert "\"AIN4\", \"AIN5\", \"AIN6\", \"AIN7\"," in s
p.write_text(s.replace("\"AIN4\", \"AIN5\", \"AIN6\", \"AIN7\",", ""))
'

# --- the ERC gate ----------------------------------------------------------

# 5. a power pin disconnected from its rail: SKiDL ERC must see the unconnected
#    pin, and the gate must refuse to pass it
expect_fail erc "power pin left unconnected (U6.V+ off +15VA)" '
import pathlib
p = pathlib.Path("blocks/current_sense.py"); s = p.read_text()
assert "n.P15VA += U6[\"V+\"], C20[1]" in s
p.write_text(s.replace("n.P15VA += U6[\"V+\"], C20[1]", "n.P15VA += C20[1]"))
'

# 6. a PWR_FLAG removed: the rail goes back to having no driver
expect_fail erc "PWR_FLAG removed from +5VA" '
import pathlib
p = pathlib.Path("blocks/power.py"); s = p.read_text()
assert "n.P5VA += FLG5[1]" in s
p.write_text(s.replace("n.P5VA += FLG5[1]", "pass  # flag removed"))
'

# 7. a PWR_FLAG added to a rail that has a REAL driver: this would mask a
#    genuine driver conflict, so it must be rejected too
expect_fail erc "PWR_FLAG added to +3V3D, which U1.VOUT already drives" '
import pathlib
p = pathlib.Path("blocks/power.py"); s = p.read_text()
assert "n.P5VA += FLG5[1]" in s
p.write_text(s.replace(
    "n.P5VA += FLG5[1]",
    "n.P5VA += FLG5[1]\n"
    "    FLG9 = PWR_FLAG()(ref=chr(35) + \"FLG9\", tag=\"FLG9\", value=\"+3V3D\")\n"
    "    n.P3V3D += FLG9[1]"))
'

# 8. a pin number with no corresponding pad (wrong package for the ADC)
expect_fail structural "pin numbers not a subset of pads (USB-C on an 8-pad footprint)" '
import pathlib
p = pathlib.Path("libraries/custom_symbols.py"); s = p.read_text()
assert "Connector_USB:USB_C_Receptacle_GCT_USB4085" in s
p.write_text(s.replace("Connector_USB:USB_C_Receptacle_GCT_USB4085",
                       "Package_SO:VSSOP-8_3x3mm_P0.65mm"))
'

# --- the board gate --------------------------------------------------------

# 9. a net in the design with no netclass: the board must refuse to be
#    generated rather than let the net inherit somebody else's clearance
expect_fail board "net with no netclass (LED_STATUS unclassified)" '
import pathlib
p = pathlib.Path("libraries/netclasses.py"); s = p.read_text()
assert "\"LED_STATUS\"" in s
p.write_text(s.replace("\"BOOT0\", \"LED_STATUS\", \"VBUS_SENSE\"",
                       "\"BOOT0\", \"VBUS_SENSE\""))
'

# 10. the star resistor moved off the plane boundary: R31 no longer straddles
#     the split, so AGND and DGND stop meeting where the schematic says they do.
#     A layout-source edit: this is where placement now lives.
expect_fail board "R31 no longer straddles the AGND/DGND split" '
import pathlib
p = pathlib.Path("layout/stage1_iv.layout"); s = p.read_text()
old = "(footprint \"R31\" F 127.34 59.8 0)"
assert old in s
p.write_text(s.replace(old, "(footprint \"R31\" F 123 59.8 0)"))
'

# 11. a placement that overlaps courtyards — the non-routing DRC violation class
#     that a real fabrication run would reject. The ECO puts R32 on top of R12.
expect_fail board "overlapping courtyards (ECO part placed on another)" '
import pathlib
p = pathlib.Path("layout/bipolar.eco"); s = p.read_text()
old = "(add (footprint \"R32\" F 98.4 80.7 180)"
assert old in s
p.write_text(s.replace(old, "(add (footprint \"R32\" F 97.6 81.9 180)"))
'

# 12. a netclass relaxed below what the fabricator can build: the board must not
#     be emitted at all, however quiet it would make DRC
expect_fail board "track width below the fab minimum (F3)" '
import pathlib
p = pathlib.Path("libraries/netclasses.py"); s = p.read_text()
assert "\"DIGITAL\": {" in s
head, tail = s.split("\"DIGITAL\": {", 1)
tail = tail.replace("\"track_width\": 0.25,", "\"track_width\": 0.10,", 1)
p.write_text(head + "\"DIGITAL\": {" + tail)
'

# 13. a netclass clearance wider than a package's own pad gap, with no scoped
#     rule to cover it: KiCad would report clearance errors no routing can fix.
#     This is the defect a real DRC run found and this repo's own checks missed.
expect_fail board "netclass clearance exceeds a package's pad gap" '
import pathlib
p = pathlib.Path("stage1_iv.kicad_dru")
p.write_text("(version 1)\n;; scoped rules removed\n")
'

# 14. min_hole_to_hole above the drill spacing J3 physically has
expect_fail board "min_hole_to_hole above J3 USB-C drill spacing" '
import pathlib
p = pathlib.Path("libraries/netclasses.py"); s = p.read_text()
assert "HOLE_GAP_MARGIN = 0.02" in s
p.write_text(s.replace("HOLE_GAP_MARGIN = 0.02", "HOLE_GAP_MARGIN = -0.05"))
'

# 15. a footprint whose pad names drifted from the vendored revision - the
#     USB-C S1 -> SH rename that broke "Update PCB from Schematic" on a KiCad 10
#     machine. Owned by the structural gate: pin-number-to-pad-name agreement is
#     footprint_check.py's job, and it fires before a board is ever emitted.
#     (validation/drc_check.py::check_board_parity carries the same check for a
#     board that somehow got written anyway.)
expect_fail structural "vendored footprint drifts from the design (USB-C S1 -> SH)" '
import pathlib
p = pathlib.Path("libraries/footprints/Connector_USB.pretty/"
                 "USB_C_Receptacle_GCT_USB4085.kicad_mod")
s = p.read_text(); assert "\"S1\"" in s
p.write_text(s.replace("(pad \"S1\"", "(pad \"SH\""))
'

# 16. a .kicad_dru KiCad cannot compile: DRC refuses to start entirely, so the
#     user cannot check the board at all. This shipped once - the file used ';'
#     comments, which the DSN lexer does not accept (it wants '#', first
#     non-blank character of a line).
expect_fail board "custom rules file KiCad cannot compile" '
import pathlib
p = pathlib.Path("stage1_iv.kicad_dru"); s = p.read_text()
p.write_text(s.replace("# GENERATED FILE", ";; GENERATED FILE", 1))
'

# 17. the board emitted in a different schema from the footprints on it - the
#     mismatch that cost the Edge.Cuts outline on KiCad 9
expect_fail board "board schema does not match the vendored footprints" '
import pathlib
p = pathlib.Path("generators/pcb.py"); s = p.read_text()
assert "BOARD_VERSION = \"20241229\"" in s
p.write_text(s.replace("BOARD_VERSION = \"20241229\"", "BOARD_VERSION = \"20221018\""))
'

# 18. a board that "Update PCB from Schematic" would rewrite. This is the
#     defect that made four rounds of DRC reports describe a board no
#     generator wrote: KiCad silently replaced the footprints on open, so
#     every measurement afterwards was taken on something else.
expect_fail board "Update PCB from Schematic would change the board" '
import pathlib
p = pathlib.Path("generators/pcb.py"); s = p.read_text()
old = "        node.append(L(A(\"sheetname\"), Q(sheet_name(block))))\n"
assert old in s
p.write_text(s.replace(old, ""))
'

# 19. the no-op checker silently stops seeing what KiCad sees. It is a model of
#     BOARD_NETLIST_UPDATER, not KiCad itself, so it is only worth what its
#     calibration against real KiCad 10.0.5 output is worth. Break the model
#     and the calibration must say so - a checker that reports "0 changes"
#     while KiCad reports 110 is the exact failure this project already
#     shipped once, on silkscreen.
expect_fail board "the Update-PCB model stops matching KiCad output" '
import pathlib
p = pathlib.Path("validation/update_pcb_noop.py"); s = p.read_text()
old = "        if fp.sheetname != comp[\"sheetname\"]:"
assert old in s
p.write_text(s.replace(old, "        if False:"))
'

# --- the bipolar measurement --------------------------------------------------

# 20. the ADC's negative input back on AGND: the 2.5 V level shift no longer
#     cancels, and half of every channel's range is back below the ADC floor
expect_fail range "ADC AINCOM returned to AGND (measurement zero lost)" '
import pathlib
p = pathlib.Path("blocks/adc.py"); s = p.read_text()
assert "n.VREF_2V5 += U8[\"AINCOM\"]" in s
p.write_text(s.replace("n.VREF_2V5 += U8[\"AINCOM\"]", "n.AGND += U8[\"AINCOM\"]"))
'

# 21. the current gain sized to the programmed limit instead of the physical
#     bound: the old R11 = 13.0k over-ranges the ADC well inside the sweep
expect_fail range "current-channel gain too high for the reachable current" '
import pathlib
p = pathlib.Path("blocks/current_sense.py"); s = p.read_text()
assert "value=\"35.7k 0.1%\"" in s
p.write_text(s.replace("value=\"35.7k 0.1%\"", "value=\"13.0k\""))
'

# --- the routed board flow ---------------------------------------------------

# 22. an ECO track dropped: the bipolar board is no longer fully routed. Only
#     KiCad's connectivity sees this; the gate must not call it a pass.
expect_fail board "ECO connection left unrouted (R33 pad 1 to U7 pin 2)" '
import pathlib, re
p = pathlib.Path("layout/bipolar.eco"); s = p.read_text()
s, n = re.subn(r"\(add \(segment \"/voltage_sense/SENSE_M_AMP\"[^\n]*\n[^\n]*\n",
               "", s)
assert n == 2
p.write_text(s)
'

# 23. an ECO that no longer matches the board it was written against: a removed
#     track that is not there. It must stop the build, and the committed board
#     must stop passing as "generated from source".
expect_fail board "ECO no longer applies to its base layout" '
import pathlib
p = pathlib.Path("layout/bipolar.eco"); s = p.read_text()
old = "(remove (segment \"AGND\" \"F.Cu\" 0.2 125.09 49.493456 125.09 48.09)"
assert old in s
p.write_text(s.replace(old, "(remove (segment \"AGND\" \"F.Cu\" 0.2 125.09 49.493456 125.09 48.1)"))
'

# --- the final hardware audit corrections ---------------------------------------

# 24. the sweep midpoint back on the fixed reference (audit F2): with the DAC at
#     its zero-scale power-on state the output is -10.04 V until firmware runs
expect_fail assert "sweep midpoint back on VREF_2V5 (power-up drives -10 V)" '
import pathlib
p = pathlib.Path("blocks/dac_sweep.py"); s = p.read_text()
old = "v_dacb = Net(\"V_DACB\"); v_dacb += U4[\"VOUTB\"], R1[1]"
assert old in s
p.write_text(s.replace(old, "n.VREF_2V5 += R1[1]; "
                            "U4[\"VOUTB\"] += U4[\"VOUTB\"].circuit.NC"))
'

# 25. the DAC reference wired around the gate (audit F2): the DAC would keep
#     its codes, and the output its voltage, through a reset, halt or crash
expect_fail assert "DAC VREF wired straight to VREF_2V5, bypassing U11" '
import pathlib
p = pathlib.Path("blocks/dac_sweep.py"); s = p.read_text()
old = "dac_vref += U4[\"VREF\"], U11[\"D\"], R35[1]"
assert old in s
p.write_text(s.replace(old, "dac_vref += U11[\"D\"], R35[1]; "
                            "n.VREF_2V5 += U4[\"VREF\"]"))
'

# 26. a DC path onto DAC_ARM (audit F2): a static level - a held reset, a
#     halted CPU - could then keep the output armed
expect_fail assert "DAC_ARM bleed resistor returned to +3V3D (DC arming path)" '
import pathlib
p = pathlib.Path("blocks/mcu.py"); s = p.read_text()
old = "n.DGND += C48[1], R37[1]"
assert old in s
p.write_text(s.replace(old, "n.DGND += C48[1]; n.P3V3D += R37[1]"))
'

# 27. R30 back to a quarter watt (audit F3): the 127 mA rail fault puts 1.6 W
#     into it
expect_fail range "R30 rated 1/4 W against the 1.6 W rail-fault envelope" '
import pathlib
p = pathlib.Path("blocks/protection.py"); s = p.read_text()
assert "value=\"100R 1% 2W\"" in s
p.write_text(s.replace("value=\"100R 1% 2W\"", "value=\"100R 1% 1/4W\""))
'

# --- the remaining hardware corrections (docs/final_corrections.md) -------------

# 28. F2 jumpered out: FORCE- reaches the shunt with nothing to limit an
#     external source on J7 (24 W into R10 from a 15.5 V rail)
expect_fail assert "F2 bypassed: J7 FORCE- straight onto the shunt" '
import pathlib
p = pathlib.Path("blocks/protection.py"); s = p.read_text()
old = "n.ISENSE_HI += F2[2]"
assert old in s
p.write_text(s.replace(old, "n.ISENSE_HI += F2[2], J7[\"FORCE-\"]"))
'

# 29. the U12 LED string returned to AGND instead of -15VA: with -15 V absent
#     the relay stays closed, so FORCE+ is no longer disconnected in that state
expect_fail assert "U12 LED string ends on AGND (one rail keeps FORCE+ on)" '
import pathlib
p = pathlib.Path("blocks/protection.py"); s = p.read_text()
old = "n.N15VA += D7[\"A\"]"
assert old in s
p.write_text(s.replace(old, "n.AGND += D7[\"A\"]"))
'

# 30. a dead-rail clamp fitted backwards: D9 would forward-bias across -15VA
expect_fail assert "D9 reversed (-15VA clamp would short the rail)" '
import pathlib
p = pathlib.Path("blocks/power.py"); s = p.read_text()
old = "n.AGND += D9[\"K\"]\n    n.N15VA += D9[\"A\"]"
assert old in s
p.write_text(s.replace(old, "n.AGND += D9[\"A\"]\n    n.N15VA += D9[\"K\"]"))
'

# 31. R10 back to a half-watt part: an external source on J7 puts ~14 W into
#     it until F2 trips, beyond 5 x 0.5 W
expect_fail range "R10 rated 0.5 W against an external source on J7" '
import pathlib
p = pathlib.Path("blocks/current_sense.py"); s = p.read_text()
assert "value=\"10.0R 0.1% 3W\"" in s
p.write_text(s.replace("value=\"10.0R 0.1% 3W\"", "value=\"10.0R 0.1% 0.5W\""))
'

# 32. the policy-clearance re-routes dropped: the board is back at the human
#     0.20 mm spacing, which the repository netclasses reject (KiCad 10 DRC)
expect_fail board "policy_clearance.eco no longer applied" '
import pathlib
p = pathlib.Path("generators/routed_pcb.py"); s = p.read_text()
old = ",\n        os.path.join(LAYOUT_DIR, \"policy_clearance.eco\")]"
assert old in s
p.write_text(s.replace(old, "]"))
'

# 33. a mounting hole dropped onto routed copper: its keepout and courtyard
#     must stop the board, not the first screw
expect_fail board "mounting hole H1 placed on routed tracks" '
import pathlib
p = pathlib.Path("layout/final_corrections.eco"); s = p.read_text()
old = "MountingHole_3.2mm_M3_Isolated\" F 87.7 37.5 0)"
assert old in s
p.write_text(s.replace(old, "MountingHole_3.2mm_M3_Isolated\" F 110 60 0)"))
'

echo "----------------------------------------------------------------------"
if [ "$FAILURES" -eq 0 ]; then
    echo "ALL INJECTED FAULTS CAUGHT — the guard works."
    exit 0
fi
echo "$FAILURES injected fault(s) were NOT caught."
exit 1
