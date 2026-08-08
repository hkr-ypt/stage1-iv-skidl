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
# main.py must fail in BOTH cases — it is the thing a user actually runs.
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
        *) echo "  [BUG] unknown gate $gate"; FAILURES=$((FAILURES + 1)); return ;;
    esac

    ( cd "$WORK/repo" && python3 "$checker" ) >"$WORK/gate.log" 2>&1
    gate_rc=$?
    ( cd "$WORK/repo" && python3 main.py ) >"$WORK/main.log" 2>&1
    main_rc=$?

    if [ "$gate_rc" -ne 0 ] && [ "$main_rc" -ne 0 ]; then
        echo "  [CAUGHT] $name"
        echo "           $checker=$gate_rc, main.py=$main_rc"
        grep -m2 -E "\[ERROR\]|\[ERC WARNING\]|ERC FAILED|PWR_FLAG invariant" \
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

echo "----------------------------------------------------------------------"
if [ "$FAILURES" -eq 0 ]; then
    echo "ALL INJECTED FAULTS CAUGHT — the guard works."
    exit 0
fi
echo "$FAILURES injected fault(s) were NOT caught."
exit 1
