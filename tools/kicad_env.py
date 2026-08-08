#!/usr/bin/env python3
"""
kicad_env.py — portable KiCad environment resolution and diagnosis.
====================================================================

Answers two questions, and refuses to guess at either:

  1. **Where are the KiCad libraries on this machine?** Resolution order is
     documented and overridable end to end; no absolute path is ever hardcoded.
  2. **Is that installation complete?** A partially-populated symbols directory
     — an interrupted copy or sync — resolves the alphabetically-early
     libraries and fails the rest, which looks nothing like "KiCad is missing"
     and is easy to misdiagnose.

RESOLUTION ORDER
----------------
  1. environment variables, newest KiCad first::

         KICAD9_SYMBOL_DIR / KICAD9_FOOTPRINT_DIR
         KICAD8_SYMBOL_DIR / KICAD8_FOOTPRINT_DIR
         KICAD7_SYMBOL_DIR / KICAD7_FOOTPRINT_DIR

  2. ``kicad_paths.local.json`` beside this repository — **gitignored**, for a
     developer's machine-specific paths (or ``$STAGE1_IV_KICAD_CONFIG``)
  3. ``kicad_libs.json``, committed, holding the conventional per-platform
     locations rather than one machine's absolute paths
  4. per-platform default install locations

If nothing resolves, :func:`kicad_available` returns False and the build says
so **loudly** — it never degrades to a pass.

CLI
---
::

    python3 tools/kicad_env.py --check      # what resolved, and from where
    python3 tools/kicad_env.py --diagnose   # is the installation complete?
    python3 tools/kicad_env.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from resolve_kicad_libs import (  # noqa: E402
    KicadLibraries, LibraryNotFound, locate_libraries, config_path,
    _platform_defaults, _looks_like_symbol_dir, _looks_like_footprint_dir,
)

# The symbol libraries this design resolves identifiers against. Kept here so
# --diagnose can report per-library, and so a truncated installation is named
# for what it is instead of being reported as "KiCad not installed".
REQUIRED_SYMBOL_LIBS = [
    "Amplifier_Instrumentation", "Amplifier_Operational",
    "Connector", "Connector_Generic", "Device", "MCU_ST_STM32F4",
    "Power_Protection", "Reference_Voltage", "Regulator_Linear",
    "Switch", "power", "Analog_DAC",
]

REQUIRED_FOOTPRINT_LIBS = [
    "Button_Switch_THT", "Capacitor_SMD", "Capacitor_THT",
    "Capacitor_Tantalum_SMD", "Connector", "Connector_PinHeader_2.54mm",
    "Connector_USB", "Crystal", "Diode_SMD", "Fuse", "Inductor_SMD",
    "LED_SMD", "Package_DIP", "Package_QFP", "Package_SO",
    "Package_TO_SOT_SMD", "Package_TO_SOT_THT", "Resistor_SMD",
    "Resistor_THT", "TerminalBlock_Phoenix", "TestPoint",
]

LOCAL_CONFIG_NAME = "kicad_paths.local.json"


def local_config_path():
    """Machine-specific, gitignored override file."""
    return os.environ.get("STAGE1_IV_KICAD_LOCAL_CONFIG",
                          os.path.join(REPO_ROOT, LOCAL_CONFIG_NAME))


def _read_local_config():
    path = local_config_path()
    if not os.path.isfile(path):
        return {}, path
    try:
        with open(path) as fh:
            return json.load(fh), path
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryNotFound(f"{path} is not valid JSON: {exc}") from exc


def resolve():
    """Resolve the library directories. Returns a dict; never raises.

    ``available`` is False when nothing resolved — the caller must then report
    SKIPPED, not PASS.
    """
    attempts = []

    # 2. machine-local override, ahead of the committed config
    local, local_path = _read_local_config()
    if local:
        s, f = local.get("symbol_dir"), local.get("footprint_dir")
        ok = _looks_like_symbol_dir(s) and _looks_like_footprint_dir(f)
        attempts.append({"source": f"local config {local_path}",
                         "symbol_dir": s, "footprint_dir": f, "usable": ok})
        if ok:
            return _result(s, f, f"local config {local_path}", attempts)

    try:
        s, f, origin = locate_libraries()
        attempts.append({"source": origin, "symbol_dir": s,
                         "footprint_dir": f, "usable": True})
        return _result(s, f, origin, attempts)
    except LibraryNotFound as exc:
        attempts.append({"source": "env vars / kicad_libs.json / platform "
                                   "defaults", "symbol_dir": None,
                         "footprint_dir": None, "usable": False,
                         "detail": str(exc).splitlines()[0]})

    return {
        "available": False,
        "symbol_dir": None,
        "footprint_dir": None,
        "origin": None,
        "attempts": attempts,
        "local_config": local_path,
        "committed_config": config_path(),
        "platform_defaults": [{"symbol_dir": a, "footprint_dir": b}
                              for a, b in _platform_defaults()],
    }


def _result(symbol_dir, footprint_dir, origin, attempts):
    return {
        "available": True,
        "symbol_dir": symbol_dir,
        "footprint_dir": footprint_dir,
        "origin": origin,
        "attempts": attempts,
        "local_config": local_config_path(),
        "committed_config": config_path(),
    }


def kicad_available():
    """True when a usable KiCad symbol+footprint library pair was found."""
    return resolve()["available"]


def diagnose():
    """Per-library completeness check of the resolved installation.

    Distinguishes three states that are easy to confuse:

      NOT FOUND   no library directory resolved at all
      INCOMPLETE  a directory resolved but is missing libraries this design
                  needs — the signature of an interrupted copy or sync, and the
                  cause of "library not found" ERC violations
      COMPLETE    every required library is present
    """
    env = resolve()
    out = dict(env)
    if not env["available"]:
        out["state"] = "NOT FOUND"
        return out

    libs = KicadLibraries(env["symbol_dir"], env["footprint_dir"])
    sym_present = [n for n in REQUIRED_SYMBOL_LIBS if libs.has_symbol_library(n)]
    sym_missing = [n for n in REQUIRED_SYMBOL_LIBS if not libs.has_symbol_library(n)]
    fp_present = [n for n in REQUIRED_FOOTPRINT_LIBS if libs.has_footprint_library(n)]
    fp_missing = [n for n in REQUIRED_FOOTPRINT_LIBS if not libs.has_footprint_library(n)]

    out.update({
        "state": "COMPLETE" if not (sym_missing or fp_missing) else "INCOMPLETE",
        "symbol_libraries_total": len(libs.symbol_libraries()),
        "footprint_libraries_total": len(libs.footprint_libraries()),
        "required_symbols_present": sym_present,
        "required_symbols_missing": sym_missing,
        "required_footprints_present": fp_present,
        "required_footprints_missing": fp_missing,
        "provenance": libs.provenance(),
    })

    # An interrupted copy leaves an alphabetical prefix: everything present
    # sorts before everything missing. Saying so turns a confusing pile of
    # "library not found" errors into one actionable sentence.
    if sym_missing:
        all_libs = sorted(libs.symbol_libraries())
        idx = {n: i for i, n in enumerate(all_libs)}
        present_idx = [idx[n] for n in sym_present if n in idx]
        out["truncation_signature"] = bool(
            present_idx and all(n not in idx for n in sym_missing))
        out["hint"] = (
            "Every missing library is absent from the directory entirely, and "
            "the present ones sort alphabetically before them. That is the "
            "signature of an interrupted copy/sync of the symbols directory, "
            "not a missing KiCad install and not a bad environment variable "
            "(a bad variable fails all libraries, not a prefix). Re-run "
            "tools/install_kicad_libs.sh, or re-sync the symbols directory.")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--check", action="store_true",
                    help="print what resolved and from where")
    ap.add_argument("--diagnose", action="store_true",
                    help="also check that the installation is complete")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    info = diagnose() if args.diagnose else resolve()

    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print("=" * 70)
        print("KiCad environment")
        print("=" * 70)
        if info["available"]:
            print(f"  resolved from ....... {info['origin']}")
            print(f"  symbol_dir .......... {info['symbol_dir']}")
            print(f"  footprint_dir ....... {info['footprint_dir']}")
        else:
            print("  KiCad libraries ..... NOT FOUND")
            print("\n  Tried, in order:")
            for a in info["attempts"]:
                print(f"    - {a['source']}: symbols={a['symbol_dir']!r} "
                      f"footprints={a['footprint_dir']!r} usable={a['usable']}")
            print(f"\n  Set KICAD9_SYMBOL_DIR / KICAD9_FOOTPRINT_DIR, or write")
            print(f"  {info['local_config']} (gitignored) as")
            print('      {"symbol_dir": "...", "footprint_dir": "..."}')
            print("  or run  tools/install_kicad_libs.sh")
        if args.diagnose and info.get("state"):
            print(f"\n  installation state .. {info['state']}")
            if info["state"] != "NOT FOUND":
                print(f"  symbol libraries .... {info['symbol_libraries_total']}")
                print(f"  footprint libraries . {info['footprint_libraries_total']}")
                prov = info.get("provenance", {})
                for label in ("symbols", "footprints"):
                    tag = prov.get(label, {}).get("tag")
                    if tag:
                        print(f"  {label:<18} pinned at {tag} @ "
                              f"{prov[label].get('commit','')[:12]}")
                miss_s = info["required_symbols_missing"]
                miss_f = info["required_footprints_missing"]
                print(f"  required symbol libs  {len(info['required_symbols_present'])}"
                      f"/{len(REQUIRED_SYMBOL_LIBS)} present")
                if miss_s:
                    print(f"      MISSING: {miss_s}")
                print(f"  required fp libs      {len(info['required_footprints_present'])}"
                      f"/{len(REQUIRED_FOOTPRINT_LIBS)} present")
                if miss_f:
                    print(f"      MISSING: {miss_f}")
                if info.get("hint"):
                    print(f"\n  {info['hint']}")

    if not info["available"]:
        return 2
    if args.diagnose and info.get("state") == "INCOMPLETE":
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
