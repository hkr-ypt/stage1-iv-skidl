#!/usr/bin/env python3
"""
audit_identifiers.py — audit every part in the live SKiDL circuit against the
installed KiCad libraries.
=============================================================================

Imports ``main.build()`` (the *live* circuit, not a parsed netlist) and, for
every part, reports:

  * ``KiCad_Lib``  -> does that symbol library exist? does that symbol exist?
  * ``footprint``  -> does that footprint library exist? does that footprint
    exist?
  * ``set(symbol pin numbers)`` vs ``set(footprint pad names)``: missing
    (declared pins with no pad — these silently vanish from the PCB), extra
    (pads with no pin), matched
  * pads carrying no net (per-pad, the fabrication-relevant number)
  * the ``Pins_Verified`` provenance flag
  * when the ``KiCad_Lib`` symbol resolves, whether the SKiDL part's
    (number -> name) map agrees with the official symbol's

Usage::

    python3 tools/audit_identifiers.py            # markdown to stdout
    python3 tools/audit_identifiers.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, HERE)

from resolve_kicad_libs import KicadLibraries, LibraryNotFound  # noqa: E402


def _natural(ref):
    num = "".join(ch for ch in ref if ch.isdigit())
    return (ref[0] if ref else "", int(num) if num else 0)


def _pin_net_name(pin):
    """Return the net name a pin sits on, or None when it carries no net."""
    nets = [n for n in pin.get_nets()]
    if not nets:
        return None
    net = nets[0]
    if getattr(net, "is_implicit", lambda: False)() and len(net.get_pins()) < 2:
        return None
    name = getattr(net, "name", None)
    return name


_NAME_STRIP_RE = re.compile(r"[_{}~\s]")


def _norm_pin_name(name):
    """Normalise a pin name for comparison.

    KiCad symbol libraries mark up pin names (``V_{DD}``, ``~{SYNC}``,
    ``D_{IN}``); the SKiDL factories spell the same pins plainly. Strip the
    markup characters and case so the two can be compared meaningfully.
    """
    return _NAME_STRIP_RE.sub("", str(name)).upper()


def _is_nc(pin):
    from skidl import Pin
    for net in pin.get_nets():
        if getattr(net, "netclass", None) == "NC":
            return True
    circuit = getattr(pin, "circuit", None)
    if circuit is not None and pin.is_attached(circuit.NC):
        return True
    return pin.func == Pin.types.NOCONNECT


def audit(libs, circuit):
    """Return a list of per-part audit records."""
    rows = []
    for part in sorted(circuit.parts, key=lambda p: _natural(p.ref)):
        lib_id = part.fields.get("KiCad_Lib", "") or ""
        fp_id = part.footprint or ""
        pins_verified = part.fields.get("Pins_Verified", "")

        sym_lib_ok = sym_ok = False
        sym_source = ""
        sym_pin_map = {}
        if ":" in lib_id:
            nick = lib_id.split(":", 1)[0]
            sym_lib_ok = libs.has_symbol_library(nick)
            sym = libs.get_symbol(lib_id) if sym_lib_ok else None
            if sym is not None:
                sym_ok = True
                sym_source = f"{sym.file}:{sym.line}"
                for p in sym.pins:
                    sym_pin_map.setdefault(p.number, p.name)

        fp_lib_ok = fp_ok = False
        fp_source = ""
        pad_names = set()
        pad_total = 0
        if ":" in fp_id:
            nick = fp_id.split(":", 1)[0]
            fp_lib_ok = libs.has_footprint_library(nick)
            fp = libs.get_footprint(fp_id) if fp_lib_ok else None
            if fp is not None:
                fp_ok = True
                fp_source = f"{fp.file}:{fp.line}"
                pad_names = set(fp.pad_names)
                pad_total = fp.pad_count

        declared = {str(p.num) for p in part.pins}
        missing = sorted(declared - pad_names) if fp_ok else sorted(declared)
        extra = sorted(pad_names - declared) if fp_ok else []
        matched = sorted(declared & pad_names) if fp_ok else []

        # A pad is "netted" when a declared pin with that number carries a real
        # net that is not the no-connect net.
        netted = set()
        nc_pins = set()
        for p in part.pins:
            num = str(p.num)
            if _is_nc(p):
                nc_pins.add(num)
                continue
            if _pin_net_name(p):
                netted.add(num)
        unnetted_pads = sorted(pad_names - netted) if fp_ok else []

        # Does the SKiDL numbering agree with the official symbol's?
        numbering = "n/a"
        name_conflicts = []
        if sym_ok:
            official = set(sym_pin_map)
            if declared == official:
                numbering = "same pin numbers"
            elif declared <= official:
                numbering = f"subset ({len(declared)}/{len(official)})"
            else:
                numbering = f"DIVERGES ({len(declared - official)} not in symbol)"
            # Same numbers is not the same as the same *mapping*: compare the
            # per-number pin names too, normalising KiCad's name markup.
            for p in part.pins:
                num = str(p.num)
                if num not in sym_pin_map:
                    continue
                ours, theirs = _norm_pin_name(p.name), _norm_pin_name(sym_pin_map[num])
                if ours != theirs:
                    name_conflicts.append(f"{num}:{p.name}!={sym_pin_map[num]}")

        rows.append({
            "ref": part.ref,
            "value": str(part.value),
            "mpn": part.fields.get("MPN", ""),
            "pins_verified": pins_verified,
            "kicad_lib": lib_id,
            "sym_lib_ok": sym_lib_ok,
            "sym_ok": sym_ok,
            "sym_source": sym_source,
            "footprint": fp_id,
            "fp_lib_ok": fp_lib_ok,
            "fp_ok": fp_ok,
            "fp_source": fp_source,
            "declared_pins": sorted(declared),
            "pad_names": sorted(pad_names),
            "pad_total": pad_total,
            "missing": missing,
            "extra": extra,
            "matched_count": len(matched),
            "nc_pins": sorted(nc_pins),
            "unnetted_pads": unnetted_pads,
            "symbol_numbering": numbering,
            "pin_name_conflicts": name_conflicts,
        })
    return rows


def _fmt_set(values, limit=8):
    if not values:
        return "—"
    shown = values[:limit]
    tail = f" +{len(values) - limit}" if len(values) > limit else ""
    return "`" + ", ".join(shown) + "`" + tail


def to_markdown(rows, libs):
    info = libs.describe()
    prov = libs.provenance()
    unresolved_sym = [r for r in rows if not r["sym_ok"]]
    unresolved_fp = [r for r in rows if not r["fp_ok"]]
    placeholder = [r for r in rows if r["pins_verified"] == "PLACEHOLDER-REMAP"]
    with_missing = [r for r in rows if r["missing"]]
    with_unnetted = [r for r in rows if r["unnetted_pads"]]
    with_name_conflicts = [r for r in rows if r.get("pin_name_conflicts")]

    out = []
    A = out.append
    A("# Identifier audit — Stage 1 I–V Characterization System")
    A("")
    A("Generated by `tools/audit_identifiers.py` from the **live SKiDL circuit** "
      "(`main.build()`), resolved against the installed KiCad libraries.")
    A("")
    A("## Library provenance")
    A("")
    A("| | value |")
    A("|---|---|")
    A(f"| resolved from | {info['origin']} |")
    A(f"| symbol_dir | `{info['symbol_dir']}` |")
    A(f"| footprint_dir | `{info['footprint_dir']}` |")
    for label in ("symbols", "footprints"):
        p = prov.get(label, {})
        if p.get("tag"):
            A(f"| {label} revision | `{p.get('tag')}` @ `{p.get('commit','')[:12]}` "
              f"({p.get('repository','')}) |")
    A(f"| symbol libraries indexed | {info['symbol_libraries']} |")
    A(f"| footprint libraries indexed | {info['footprint_libraries']} |")
    A("")
    A("## Summary")
    A("")
    A("| metric | count |")
    A("|---|---|")
    A(f"| parts | {len(rows)} |")
    A(f"| **unresolved symbols** (KiCad_Lib does not exist) | **{len(unresolved_sym)}** |")
    A(f"| **unresolved footprints** | **{len(unresolved_fp)}** |")
    A(f"| parts with `Pins_Verified == PLACEHOLDER-REMAP` | {len(placeholder)} |")
    A(f"| parts declaring a pin with no matching pad | {len(with_missing)} |")
    A(f"| parts with at least one unnetted pad | {len(with_unnetted)} |")
    A(f"| total unnetted pads | {sum(len(r['unnetted_pads']) for r in rows)} |")
    A(f"| parts whose pin *names* disagree with the official symbol at the "
      f"same pin number | {len(with_name_conflicts)} |")
    A("")
    if unresolved_sym:
        A("**Unresolved symbols:** " + ", ".join(
            f"{r['ref']} (`{r['kicad_lib'] or '—'}`)" for r in unresolved_sym))
        A("")
    if unresolved_fp:
        A("**Unresolved footprints:** " + ", ".join(
            f"{r['ref']} (`{r['footprint'] or '—'}`)" for r in unresolved_fp))
        A("")

    if with_name_conflicts:
        A("**Pin number -> name mismatches against the official symbol** "
          "(same pin numbers, different function on that number — a silent "
          "mis-wiring):")
        A("")
        for r in with_name_conflicts:
            A(f"  * `{r['ref']}` vs `{r['kicad_lib']}`: "
              + "; ".join(r["pin_name_conflicts"]))
        A("")

    A("## Per-part detail")
    A("")
    A("`sym` / `fp` columns: `OK` = library and item both found; `NO SYM` / "
      "`NO FP` = library found but the item is not in it; `NO LIB` = the "
      "library nickname itself does not exist.")
    A("")
    A("| Ref | Value | Pins_Verified | KiCad_Lib | sym | footprint | fp | "
      "pins | pads | pins with no pad | unnetted pads | vs official symbol |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        sym_state = ("OK" if r["sym_ok"]
                     else ("NO SYM" if r["sym_lib_ok"] else "NO LIB"))
        fp_state = ("OK" if r["fp_ok"]
                    else ("NO FP" if r["fp_lib_ok"] else "NO LIB"))
        A("| {ref} | {value} | {pv} | `{lib}` | {sym} | `{fp}` | {fps} | "
          "{npins} | {npads} | {missing} | {unnetted} | {numbering} |".format(
              ref=r["ref"], value=r["value"] or "—",
              pv=r["pins_verified"] or "—",
              lib=r["kicad_lib"] or "—", sym=sym_state,
              fp=r["footprint"] or "—", fps=fp_state,
              npins=len(r["declared_pins"]),
              npads=(r["pad_total"] if r["fp_ok"] else "?"),
              missing=_fmt_set(r["missing"]),
              unnetted=_fmt_set(r["unnetted_pads"]),
              numbering=r["symbol_numbering"]))
    A("")
    A("## Source paths for every resolved identifier")
    A("")
    A("| Ref | symbol read from | footprint read from |")
    A("|---|---|---|")
    for r in rows:
        A(f"| {r['ref']} | `{r['sym_source'] or 'UNRESOLVED'}` | "
          f"`{r['fp_source'] or 'UNRESOLVED'}` |")
    A("")
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    try:
        libs = KicadLibraries()
    except LibraryNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 2

    import main as project_main
    _n, _parts, circuit = project_main.build()

    rows = audit(libs, circuit)
    if args.json:
        print(json.dumps({"resolver": libs.describe(),
                          "provenance": libs.provenance(),
                          "parts": rows}, indent=2))
    else:
        sys.stdout.write(to_markdown(rows, libs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
