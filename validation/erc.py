"""
erc.py — Electrical Rule Checking for the Stage 1 I–V system.
============================================================

WHAT THIS CHECKS, AND WHAT IT DOES NOT
--------------------------------------
``skidl.ERC()`` and KiCad's ERC are **different checks with different
coverage**. A pass here is evidence about the SKiDL circuit, not about KiCad.

SKiDL's ERC covers:
    * pins connected to nothing
    * pins tied to the no-connect net that also carry a real net
    * nets with zero or one pin
    * pin-type conflicts between two pins on the same net
    * insufficient drive: a net whose maximum pin drive is below what some pin
      on it requires (this is SKiDL's analogue of KiCad's
      ``power_pin_not_driven``, and PWR_FLAG parts satisfy both)

SKiDL's ERC does NOT cover, and KiCad's does:
    * symbol/library resolution (``lib_symbol_issues``, ``lib_symbol_mismatch``,
      "symbol not found in library") — see validation/footprint_check.py and
      tools/audit_identifiers.py
    * footprint link resolution (``footprint_link_issues``) — same
    * anything geometric: wire/label/pin overlap, dangling wire ends, bus
      definition errors, sheet-pin/hierarchical-label mismatch
    * the four-way distinction between error, warning and "exclusion" in a
      KiCad project's severity settings

This module also adds the design-specific structural checks the spec calls for:
missing footprints, duplicate references, pin-number provenance, and the
PWR_FLAG invariant.

THE GATE IS REAL
----------------
``run_erc`` samples ``skidl.erc_logger``'s error and warning counters either
side of the ``ERC()`` call, and independently captures every message text. The
two are cross-checked against each other, so a change in SKiDL's counter
semantics surfaces as a loud mismatch instead of a silent zero. (``ERC()``
resets those counters itself before it runs, which is why the post-call value —
not a subtraction — is this run's count.)

Any error fails the build. Any warning fails unless it matches an entry in
:data:`WARNING_ALLOWLIST`, which is empty and must stay defensible. Nothing
here prints "PASS" unconditionally.

Run standalone::

    python3 validation/erc.py
"""

import os
import re
import sys

# Importable both as `validation.erc` (from main.py) and as a standalone
# script (`python3 validation/erc.py`), so the ERC gate can be exercised on
# its own — see tools/test_guard_fails.sh.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from skidl import ERC, erc_logger  # noqa: E402

from blocks.nets import EXTERNALLY_SOURCED_RAILS  # noqa: E402

# ---------------------------------------------------------------------------
# Allow-list for ERC *warnings*.
# ---------------------------------------------------------------------------
# Each entry is (compiled regex, written justification). A warning that matches
# no entry fails the build. This list starts EMPTY on purpose: every entry
# added to it is a rule this project has chosen to stop enforcing, and that
# choice has to be argued in writing, next to the pattern, by whoever adds it.
WARNING_ALLOWLIST = [
    # (re.compile(r"..."), "why this specific warning is benign here"),
]

PWR_FLAG_LIB_ID = "power:PWR_FLAG"


def _is_virtual(part):
    """Schematic-only symbols (PWR_FLAG) legitimately have no footprint."""
    return bool(part.fields.get("Symbol_Class"))


def check_footprints(circuit):
    """Every *physical* part must carry a footprint. Returns offending refs.

    Virtual symbols are exempted by an explicit positive test on their
    ``Symbol_Class`` field, not by tolerating a blank footprint — a real part
    that lost its footprint is still an error.
    """
    missing = []
    for part in circuit.parts:
        if _is_virtual(part):
            continue
        if not getattr(part, "footprint", None):
            missing.append(part.ref)
    return missing


def check_duplicate_refs(circuit):
    """Return a dict {ref: count} for any reference used more than once."""
    seen = {}
    for part in circuit.parts:
        seen[part.ref] = seen.get(part.ref, 0) + 1
    return {r: c for r, c in seen.items() if c > 1}


def check_pin_provenance(circuit):
    """Group parts by the provenance of their pin numbers (Pins_Verified field)."""
    groups = {}
    for part in circuit.parts:
        status = part.fields.get("Pins_Verified", "UNKNOWN")
        groups.setdefault(status, []).append(part.ref)
    for k in groups:
        groups[k] = sorted(groups[k])
    return groups


def check_power_flags(circuit):
    """The PWR_FLAG invariant. Returns a list of problem strings.

    Three ways to get this wrong, all of them checked:

      1. a rail declared externally-sourced in blocks/nets.py has no flag —
         KiCad would report power_pin_not_driven on it;
      2. a flag sits on a net that is not in that table — the table has drifted,
         or someone flagged a net to silence a finding;
      3. a flagged net ALSO has a real power_out driver — the flag is then
         hiding a genuine driver conflict (two things driving one rail), which
         is a worse error than the one it suppresses.
    """
    from skidl import Pin

    expected = set(EXTERNALLY_SOURCED_RAILS.values())
    flagged = {}
    for part in circuit.parts:
        if part.fields.get("KiCad_Lib") != PWR_FLAG_LIB_ID:
            continue
        for pin in part.pins:
            for net in pin.get_nets():
                if type(net).__name__ == "NCNet":
                    continue
                flagged.setdefault(net.name, []).append(part.ref)

    problems = []
    for name in sorted(expected - set(flagged)):
        problems.append(
            f"net {name!r} is declared externally sourced in "
            f"blocks/nets.py::EXTERNALLY_SOURCED_RAILS but carries no PWR_FLAG")
    for name in sorted(set(flagged) - expected):
        problems.append(
            f"net {name!r} carries a PWR_FLAG ({flagged[name]}) but is not "
            f"declared in blocks/nets.py::EXTERNALLY_SOURCED_RAILS")
    for name, refs in sorted(flagged.items()):
        if len(refs) > 1:
            problems.append(f"net {name!r} carries {len(refs)} PWR_FLAGs: {refs}")

    for net in circuit.get_nets():
        if net.name not in flagged:
            continue
        drivers = [f"{p.part.ref}.{p.name}" for p in net.get_pins()
                   if p.func == Pin.types.PWROUT
                   and p.part.fields.get("KiCad_Lib") != PWR_FLAG_LIB_ID]
        if drivers:
            problems.append(
                f"net {net.name!r} has a PWR_FLAG *and* a real driver "
                f"{drivers}; remove the flag — it would mask a driver conflict")
    return problems, flagged


def _classify(messages):
    """Split ERC warning texts into (allowed, offending) by the allow-list."""
    allowed, offending = [], []
    for msg in messages:
        for pattern, reason in WARNING_ALLOWLIST:
            if pattern.search(msg):
                allowed.append((msg, reason))
                break
        else:
            offending.append(msg)
    return allowed, offending


class _Tee:
    """Callable proxy that records each message and forwards it unchanged.

    Everything else (``.count``, ``.reset()``) is delegated to the real logger
    method object, so SKiDL's own counters keep working — ``Circuit.ERC()``
    calls ``reset()`` on them before it starts.
    """

    def __init__(self, orig, sink):
        object.__setattr__(self, "_orig", orig)
        object.__setattr__(self, "_sink", sink)

    def __call__(self, msg, *args, **kwargs):
        self._sink.append(str(msg))
        return self._orig(msg, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_orig"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_orig"), name, value)


class _Capture:
    """Collect the text of ERC messages so warnings can be triaged."""

    def __init__(self):
        self.warnings = []
        self.errors = []

    def __enter__(self):
        self._orig_w = erc_logger.warning
        self._orig_e = erc_logger.error
        erc_logger.warning = _Tee(self._orig_w, self.warnings)
        erc_logger.error = _Tee(self._orig_e, self.errors)
        return self

    def __exit__(self, *exc):
        erc_logger.warning = self._orig_w
        erc_logger.error = self._orig_e
        return False


def run_erc(circuit):
    """Run SKiDL ERC + structural checks. Returns a summary dict.

    Raises AssertionError on any ERC error, any un-allow-listed ERC warning, or
    any structural failure.
    """
    print("\n" + "=" * 70)
    print("ELECTRICAL RULE CHECK (SKiDL)")
    print("=" * 70)

    # The ERC result is READ, not discarded. Counters are sampled either side
    # of the call; note that skidl.Circuit.ERC() calls reset() on them itself
    # before it starts, so the post-call value is already this run's count and
    # a naive subtraction would understate it. Both numbers are kept, and the
    # captured message list is cross-checked against them below so a future
    # change to SKiDL's counter semantics shows up as a mismatch rather than a
    # silent zero.
    err_before = erc_logger.error.count
    warn_before = erc_logger.warning.count
    with _Capture() as cap:
        ERC()
    err_after = erc_logger.error.count
    warn_after = erc_logger.warning.count
    err_count = err_after if err_after <= err_before else err_after - err_before
    warn_count = warn_after if warn_after <= warn_before else warn_after - warn_before
    if (err_count, warn_count) != (len(cap.errors), len(cap.warnings)):
        raise AssertionError(
            f"ERC accounting mismatch: logger says {err_count} errors / "
            f"{warn_count} warnings, captured {len(cap.errors)} / "
            f"{len(cap.warnings)}. The ERC result cannot be trusted; fix the "
            f"counting before trusting a pass.")

    allowed, offending = _classify(cap.warnings)

    missing_fp = check_footprints(circuit)
    dup_refs = check_duplicate_refs(circuit)
    provenance = check_pin_provenance(circuit)
    flag_problems, flagged_nets = check_power_flags(circuit)

    print(f"\nSKiDL ERC errors ..........: {err_count}")
    print(f"SKiDL ERC warnings ........: {warn_count} "
          f"({len(allowed)} allow-listed, {len(offending)} not)")
    for msg in cap.errors:
        print(f"  [ERC ERROR]   {msg}")
    for msg, reason in allowed:
        print(f"  [ERC ALLOWED] {msg}\n                reason: {reason}")
    for msg in offending:
        print(f"  [ERC WARNING] {msg}")

    print("\n--- Structural checks ---")
    print(f"Parts total................: {len(circuit.parts)}")
    print(f"Nets total.................: {len(circuit.get_nets())}")
    print(f"Missing footprints.........: {missing_fp or 'none'}")
    print(f"Duplicate references.......: {dup_refs or 'none'}")
    print(f"PWR_FLAG nets ({len(flagged_nets):>2}) .......: "
          f"{', '.join(sorted(flagged_nets)) or 'none'}")
    print(f"PWR_FLAG problems..........: {flag_problems or 'none'}")

    print("\n--- Pin-number provenance (see custom_symbols.py policy) ---")
    for status in ("RESOLVED", "VERIFY-DATASHEET", "PLACEHOLDER-REMAP",
                   "STD", "UNKNOWN"):
        if status in provenance:
            print(f"  [{status}] {', '.join(provenance[status])}")

    failures = []
    if err_count:
        failures.append(f"{err_count} SKiDL ERC error(s)")
    if offending:
        failures.append(f"{len(offending)} un-allow-listed ERC warning(s)")
    if missing_fp:
        failures.append(f"missing footprints: {missing_fp}")
    if dup_refs:
        failures.append(f"duplicate refs: {dup_refs}")
    if flag_problems:
        failures.append(f"PWR_FLAG invariant: {flag_problems}")

    summary = {
        "parts": len(circuit.parts),
        "nets": len(circuit.get_nets()),
        "erc_errors": err_count,
        "erc_warnings": warn_count,
        "erc_warnings_allowed": len(allowed),
        "erc_warnings_offending": offending,
        "erc_error_messages": cap.errors,
        "missing_footprints": missing_fp,
        "duplicate_refs": dup_refs,
        "power_flag_nets": sorted(flagged_nets),
        "power_flag_problems": flag_problems,
        "pin_provenance": provenance,
        "passed": not failures,
    }

    if failures:
        raise AssertionError("ERC FAILED: " + "; ".join(failures))

    print(f"\nSKiDL ERC: {err_count} errors, {warn_count} warnings. "
          f"Structural checks OK.")
    return summary


def main():
    """Run the ERC gate standalone. Exit non-zero on any failure."""
    import main as project_main
    _n, _parts, circuit = project_main.build()
    try:
        run_erc(circuit)
    except AssertionError as exc:
        print(f"\nERC GATE: FAIL\n{exc}", file=sys.stderr)
        return 1
    print("ERC GATE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
