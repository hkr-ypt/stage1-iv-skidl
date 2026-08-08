#!/usr/bin/env bash
#
# test_guard_fails.sh — prove the structural guard actually fails.
#
# A check nobody has seen fail is not a check. This script copies the repo to a
# scratch directory, reintroduces one defect at a time, and asserts that both
# validation/footprint_check.py AND main.py exit non-zero. It never touches the
# working tree.
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

# expect_fail <name> <python-patch-script>
expect_fail() {
    local name="$1" patch="$2"
    reset_copy
    ( cd "$WORK/repo" && python3 -c "$patch" ) || {
        echo "  [SETUP FAIL] $name — could not apply the injection"
        FAILURES=$((FAILURES + 1)); return
    }

    local fc_rc main_rc
    ( cd "$WORK/repo" && python3 validation/footprint_check.py ) \
        >"$WORK/fc.log" 2>&1
    fc_rc=$?
    ( cd "$WORK/repo" && python3 main.py ) >"$WORK/main.log" 2>&1
    main_rc=$?

    if [ "$fc_rc" -ne 0 ] && [ "$main_rc" -ne 0 ]; then
        echo "  [CAUGHT] $name  (footprint_check=$fc_rc, main.py=$main_rc)"
        grep -m2 -E "\[ERROR\]|Error|error:" "$WORK/fc.log" \
            | sed 's/^/            /' || true
    else
        echo "  [MISSED] $name  (footprint_check=$fc_rc, main.py=$main_rc)"
        tail -5 "$WORK/fc.log" | sed 's/^/            /'
        FAILURES=$((FAILURES + 1))
    fi
}

echo "======================================================================"
echo "GUARD FAULT-INJECTION TEST"
echo "======================================================================"

# 1. a symbol identifier that does not exist upstream (the INA128P defect)
expect_fail "bad symbol lib_id (Amplifier_Instrumentation:INA128P)" '
import pathlib
p = pathlib.Path("libraries/custom_symbols.py"); s = p.read_text()
assert "Amplifier_Instrumentation:INA128" in s
p.write_text(s.replace("Amplifier_Instrumentation:INA128\"",
                       "Amplifier_Instrumentation:INA128P\""))
'

# 2. a footprint identifier that does not exist upstream (the F1 typo)
expect_fail "bad footprint id (Fuse:Fuse_Bourns-MF-RG300)" '
import pathlib
p = pathlib.Path("libraries/custom_symbols.py"); s = p.read_text()
assert "Fuse:Fuse_Bourns_MF-RG300" in s
p.write_text(s.replace("Fuse:Fuse_Bourns_MF-RG300", "Fuse:Fuse_Bourns-MF-RG300"))
'

# 3. a placeholder provenance flag creeping back in
expect_fail "Pins_Verified == PLACEHOLDER-REMAP" '
import pathlib
p = pathlib.Path("libraries/custom_symbols.py"); s = p.read_text()
assert "\"ADS1256IDBR\", \"RESOLVED\"" in s
p.write_text(s.replace("\"ADS1256IDBR\", \"RESOLVED\"",
                       "\"ADS1256IDBR\", \"PLACEHOLDER-REMAP\""))
'

# 4. a pad silently left with no net and no no-connect
expect_fail "pad left unconnected (ADS1256 AIN4..AIN7, D0..D3)" '
import pathlib
p = pathlib.Path("blocks/adc.py"); s = p.read_text()
assert "\"AIN4\", \"AIN5\", \"AIN6\", \"AIN7\"," in s
p.write_text(s.replace("\"AIN4\", \"AIN5\", \"AIN6\", \"AIN7\",", ""))
'

# 5. a pin number with no corresponding pad (wrong package for the ADC)
expect_fail "pin numbers not a subset of pads (USB-C on an 8-pad footprint)" '
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
