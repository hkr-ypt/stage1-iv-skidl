"""
footprint_check.py — structural guard against unresolvable identifiers.
=======================================================================

This is the check that would have caught the whole class of defect this file
exists because of: symbol and footprint identifiers that do not exist upstream,
pin numbers that do not correspond to any pad, and pads that quietly carry no
net. Every check below is a **hard failure** — ``run_footprint_check`` raises
``FootprintCheckError`` and ``main.py`` exits non-zero.

Checks
------
1. every part's ``KiCad_Lib`` resolves to an existing library AND symbol
2. every part's ``footprint`` resolves to an existing library AND footprint
3. ``set(symbol pin numbers)`` is a SUBSET of ``set(footprint pad names)`` —
   a declared pin with no pad is an ERROR (the net silently vanishes)
4. every footprint pad with no net is listed; a pad may only be unconnected if
   it is an explicit SKiDL no-connect AND its part appears in
   :data:`UNCONNECTED_PAD_ALLOWLIST` with a written reason
5. no part may carry ``Pins_Verified == "PLACEHOLDER-REMAP"``

Run standalone::

    python3 validation/footprint_check.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "tools"))

from resolve_kicad_libs import KicadLibraries, LibraryNotFound  # noqa: E402

NC_NET_NAME = "__NOCONNECT"

# Provenance flags a part is allowed to carry. PLACEHOLDER-REMAP — pin numbers
# invented as sequential placeholders — is deliberately absent.
ALLOWED_PINS_VERIFIED = {"RESOLVED", "VERIFY-DATASHEET"}
FORBIDDEN_PINS_VERIFIED = {"PLACEHOLDER-REMAP"}


# ---------------------------------------------------------------------------
# Explicit allow-list for pads that carry no net.
# ---------------------------------------------------------------------------
# Key: part reference. Value: (set of pad names, written reason).
# A pad may appear here ONLY if the design also marks it no-connect through
# blocks/nets.py::nc() / nc_named(); this table records *why*. Anything not
# listed and not netted is an error.
UNCONNECTED_PAD_ALLOWLIST = {
    "U2": (
        {"1", "3", "5", "7", "8"},
        "REF5025ADGK: pins 1/7/8 are DNC/NC (no internal connection); pin 3 "
        "Temp is the on-chip temperature output, unused in Stage 1; pin 5 "
        "Trim/NR is left open to keep the factory-trimmed 2.500 V and the "
        "internal noise-reduction network undisturbed.",
    ),
    "U4": (
        {"3"},
        "DAC8552 channel B (V_{OUT}B) is reserved/unpopulated in Stage 1; "
        "only channel A drives the sweep.",
    ),
    "U8": (
        {"10", "11", "12", "13", "25", "26", "27", "28"},
        "ADS1256: AIN4..AIN7 are spare analog inputs (Stage 1 digitises two "
        "differential pairs, AIN0/1 and AIN2/3) and D0/CLKOUT, D1, D2, D3 are "
        "the general-purpose digital I/O, unused here.",
    ),
    "U9": (
        {"3", "4", "8", "9", "10", "11", "15", "16", "17", "22", "24", "25",
         "26", "27", "28", "29", "37", "38", "39", "40", "43", "50", "51",
         "52", "53", "54", "55", "56", "57", "58", "59", "61", "62"},
        "STM32F411RETx: GPIOs not used by Stage 1 (no LSE crystal on "
        "PC14/PC15, no USB_ID on PA10, remaining ports A/B/C/D spare). Every "
        "power and ground pad is connected; only signal GPIOs are open.",
    ),
    "J3": (
        {"A8", "B8"},
        "USB-C SBU1/SBU2: sideband use (audio accessory / debug) is not part "
        "of this design; the receptacle is USB 2.0 device-only.",
    ),
}


class FootprintCheckError(AssertionError):
    """One or more structural checks failed."""


NC_TYPES = ("NCNet",)


def _is_nc(pin):
    from skidl import Pin
    for net in pin.get_nets():
        if type(net).__name__ in NC_TYPES or getattr(net, "name", "") == NC_NET_NAME:
            return True
    return pin.func == Pin.types.NOCONNECT


def _net_name(pin):
    for net in pin.get_nets():
        if type(net).__name__ in NC_TYPES or getattr(net, "name", "") == NC_NET_NAME:
            continue
        return getattr(net, "name", None)
    return None


def check(circuit, libs=None):
    """Run every structural check. Returns (errors, warnings, summary)."""
    if libs is None:
        libs = KicadLibraries()

    errors = []
    warnings = []
    summary = {"parts": 0, "virtual_parts": 0, "pads": 0, "netted_pads": 0,
               "nc_pads": 0, "unaccounted_pads": 0}

    for part in sorted(circuit.parts, key=lambda p: p.ref):
        ref = part.ref
        summary["parts"] += 1

        # Virtual, schematic-only symbols (PWR_FLAG) have no footprint by
        # design. They are exempted from the footprint checks by a positive
        # test on their Symbol_Class field — NOT by tolerating an empty
        # footprint string, so a real part that lost its footprint still
        # fails. Their symbol identifier is still checked below.
        virtual = bool(part.fields.get("Symbol_Class"))

        # -- 5. provenance flag ------------------------------------------
        flag = part.fields.get("Pins_Verified", "")
        if flag in FORBIDDEN_PINS_VERIFIED:
            errors.append(
                f"{ref}: Pins_Verified == {flag!r}. Placeholder pin numbers "
                f"may not be committed; resolve them against the real symbol.")
        elif flag not in ALLOWED_PINS_VERIFIED:
            errors.append(
                f"{ref}: Pins_Verified == {flag!r} is not one of "
                f"{sorted(ALLOWED_PINS_VERIFIED)}.")

        # -- 1. symbol identifier ----------------------------------------
        lib_id = (part.fields.get("KiCad_Lib") or "").strip()
        if ":" not in lib_id:
            errors.append(f"{ref}: KiCad_Lib {lib_id!r} is not 'Library:Symbol'.")
        else:
            nick = lib_id.split(":", 1)[0]
            if not libs.has_symbol_library(nick):
                errors.append(
                    f"{ref}: symbol library {nick!r} does not exist "
                    f"(KiCad_Lib={lib_id!r}).")
            elif libs.get_symbol(lib_id) is None:
                errors.append(
                    f"{ref}: symbol {lib_id!r} does not exist in library "
                    f"{nick!r} ({libs.symbol_library_path(nick)}).")

        # -- 2. footprint identifier -------------------------------------
        fp_id = (part.footprint or "").strip()
        fp = None
        if virtual:
            if fp_id:
                errors.append(
                    f"{ref}: virtual symbol (Symbol_Class="
                    f"{part.fields.get('Symbol_Class')!r}) must not carry a "
                    f"footprint, but has {fp_id!r}.")
            summary["virtual_parts"] = summary.get("virtual_parts", 0) + 1
            continue
        if ":" not in fp_id:
            errors.append(f"{ref}: footprint {fp_id!r} is not 'Library:Footprint'.")
        else:
            nick = fp_id.split(":", 1)[0]
            if not libs.has_footprint_library(nick):
                errors.append(
                    f"{ref}: footprint library {nick!r} does not exist "
                    f"(footprint={fp_id!r}).")
            else:
                fp = libs.get_footprint(fp_id)
                if fp is None:
                    errors.append(
                        f"{ref}: footprint {fp_id!r} does not exist in "
                        f"{libs.footprint_library_path(nick)}.")

        if fp is None:
            continue

        pad_names = set(fp.pad_names)
        declared = {str(p.num) for p in part.pins}
        summary["pads"] += len(pad_names)

        # -- 3. pins must be a subset of pads ----------------------------
        orphan_pins = sorted(declared - pad_names)
        if orphan_pins:
            errors.append(
                f"{ref}: pin number(s) {orphan_pins} have no pad in "
                f"{fp_id}. Those connections would be silently dropped. "
                f"Footprint pads: {sorted(pad_names)}")

        # -- 4. pads with no net -----------------------------------------
        netted, nc_pins = set(), set()
        for pin in part.pins:
            num = str(pin.num)
            if _is_nc(pin):
                nc_pins.add(num)
            elif _net_name(pin):
                netted.add(num)

        nc_pads = pad_names & nc_pins
        unaccounted = pad_names - netted - nc_pins
        summary["netted_pads"] += len(pad_names & netted)
        summary["nc_pads"] += len(nc_pads)
        summary["unaccounted_pads"] += len(unaccounted)

        if unaccounted:
            errors.append(
                f"{ref}: pad(s) {sorted(unaccounted)} of {fp_id} carry no net "
                f"and are not marked no-connect. Connect them, or no-connect "
                f"them with blocks/nets.py::nc_named() and add them to "
                f"UNCONNECTED_PAD_ALLOWLIST with a reason.")

        if nc_pads:
            allowed, reason = UNCONNECTED_PAD_ALLOWLIST.get(ref, (set(), ""))
            undeclared = sorted(nc_pads - allowed)
            if undeclared:
                errors.append(
                    f"{ref}: pad(s) {undeclared} are no-connect but are not in "
                    f"UNCONNECTED_PAD_ALLOWLIST['{ref}']. Every deliberately "
                    f"open pad needs a written reason.")
            elif not reason:
                errors.append(
                    f"{ref}: UNCONNECTED_PAD_ALLOWLIST['{ref}'] has no reason "
                    f"text for pads {sorted(nc_pads)}.")
            stale = sorted(allowed - nc_pads)
            if stale:
                warnings.append(
                    f"{ref}: UNCONNECTED_PAD_ALLOWLIST lists pad(s) {stale} "
                    f"that are now connected; prune the allow-list.")

    return errors, warnings, summary


def run_footprint_check(circuit, verbose=True):
    """Run the structural checks and raise on any failure."""
    if verbose:
        print("\n" + "=" * 70)
        print("FOOTPRINT / SYMBOL STRUCTURAL CHECK")
        print("=" * 70)

    try:
        libs = KicadLibraries()
    except LibraryNotFound as exc:
        raise FootprintCheckError(
            "cannot verify identifiers without an installed KiCad library.\n\n"
            + str(exc)) from exc

    errors, warnings, summary = check(circuit, libs)

    if verbose:
        info = libs.describe()
        prov = libs.provenance()
        print(f"  libraries .......... {info['symbol_dir']}")
        print(f"                       {info['footprint_dir']}")
        for label in ("symbols", "footprints"):
            tag = prov.get(label, {}).get("tag")
            if tag:
                print(f"  {label:<18} {tag} @ "
                      f"{prov[label].get('commit', '')[:12]}")
        print(f"  parts checked ...... {summary['parts']} "
              f"({summary['virtual_parts']} virtual, no footprint by design)")
        print(f"  pads total ......... {summary['pads']}")
        print(f"  pads with a net .... {summary['netted_pads']}")
        print(f"  pads no-connect .... {summary['nc_pads']} "
              f"(all allow-listed with a reason)")
        print(f"  pads unaccounted ... {summary['unaccounted_pads']}")
        for w in warnings:
            print(f"  [WARN]  {w}")
        for e in errors:
            print(f"  [ERROR] {e}")

    if errors:
        raise FootprintCheckError(
            f"{len(errors)} structural error(s):\n  - " + "\n  - ".join(errors))

    if verbose:
        print("\nStructural check: PASS")
    return summary


def main():
    sys.path.insert(0, _REPO_ROOT)
    import main as project_main
    _n, _parts, circuit = project_main.build()
    try:
        run_footprint_check(circuit)
    except FootprintCheckError as exc:
        print(f"\nFOOTPRINT CHECK: FAIL\n{exc}", file=sys.stderr)
        return 1
    print("FOOTPRINT CHECK: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
