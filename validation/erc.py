"""
erc.py — Electrical Rule Checking for the Stage 1 I–V system.
============================================================

Wraps SKiDL's built-in ERC and adds the design-specific structural checks the
spec calls for:

    * unconnected pins / conflicting drivers / power flags  -> SKiDL ERC()
    * missing footprints                                    -> check_footprints()
    * duplicate references                                  -> check_duplicate_refs()
    * unresolved / placeholder pin mappings                 -> check_pin_provenance()

``run_erc(circuit)`` runs everything and returns a summary dict. It never raises
on ERC *warnings* (unconnected reserved pins are expected); it raises only if a
hard structural problem (missing footprint, duplicate ref) is found.
"""

from skidl import ERC


def check_footprints(circuit):
    """Every part must carry a footprint. Returns list of offending refs."""
    missing = []
    for part in circuit.parts:
        fp = getattr(part, "footprint", None)
        if not fp:
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


def run_erc(circuit):
    """Run SKiDL ERC + structural checks. Returns a summary dict."""
    print("\n" + "=" * 70)
    print("ELECTRICAL RULE CHECK (ERC)")
    print("=" * 70)

    # SKiDL built-in ERC (unconnected pins, driver conflicts, power flags).
    ERC()

    missing_fp = check_footprints(circuit)
    dup_refs = check_duplicate_refs(circuit)
    provenance = check_pin_provenance(circuit)

    print("\n--- Structural checks ---")
    print(f"Parts total................: {len(circuit.parts)}")
    print(f"Nets total.................: {len(circuit.get_nets())}")
    print(f"Missing footprints.........: "
          f"{missing_fp if missing_fp else 'none'}")
    print(f"Duplicate references.......: "
          f"{dup_refs if dup_refs else 'none'}")

    print("\n--- Pin-number provenance (see custom_symbols.py policy) ---")
    for status in ("RESOLVED", "VERIFY-DATASHEET", "PLACEHOLDER-REMAP",
                   "STD", "UNKNOWN"):
        if status in provenance:
            print(f"  [{status}] {', '.join(provenance[status])}")

    ok = not missing_fp and not dup_refs
    if not ok:
        raise AssertionError(
            f"ERC structural failure: missing_footprints={missing_fp}, "
            f"duplicate_refs={dup_refs}")

    print("\nERC structural checks: PASS")
    return {
        "parts": len(circuit.parts),
        "nets": len(circuit.get_nets()),
        "missing_footprints": missing_fp,
        "duplicate_refs": dup_refs,
        "pin_provenance": provenance,
    }
