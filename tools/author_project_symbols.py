#!/usr/bin/env python3
"""
author_project_symbols.py — generate ``libraries/stage1_iv.kicad_sym``.
=======================================================================

The project symbol library is the **single source of truth for every symbol the
schematic instantiates**. Two kinds of symbol live in it:

1. **Datasheet-authored** — parts with no symbol in the official KiCad
   libraries at all::

       ADS1256   Analog_ADC has ADS1251 only
       OPA548T   no OPA548 of any kind exists upstream

   Their pins are *parsed* out of committed verbatim provenance excerpts under
   ``libraries/provenance/``; no pin number is typed by hand.

       ADS1256 <- ads1256_sbas288k_terminal_functions.txt
                  verbatim ``pdftotext -layout`` extract of the "Terminal
                  Functions" table on page 5 of the TI ADS1255/ADS1256 data
                  sheet, SBAS288K (June 2003, rev. Sept 2013). The
                  "TERMINAL NO. / ADS1256" column is the pin number.
       OPA548T <- opa548t_akl_symbol.txt
                  verbatim excerpt of the OPA548T symbol in the Alternate
                  KiCad Library. The TI data sheet is unreachable from this
                  environment, so these numbers carry weaker provenance and
                  the part is flagged ``VERIFY-DATASHEET``.

2. **Upstream-derived** — every other part. Pin numbers, names and electrical
   types are read from the installed KiCad libraries by
   ``tools/resolve_kicad_libs.py`` at generation time, then frozen here.

WHY THE WHOLE LIBRARY IS GENERATED
----------------------------------
The schematic embeds a copy of each symbol in its ``(lib_symbols)`` block.
KiCad's ``lib_symbol_mismatch`` rule compares that cache against the library the
``lib_id`` names. Generating both from the *same* :class:`SymbolDef` objects
makes them the same bytes, so the rule cannot fire. It also means the design
still builds on a machine with no KiCad: ``libraries/custom_symbols.py`` falls
back to this committed library for pin data, and the build reports which source
it used.

Usage::

    python3 tools/author_project_symbols.py            # regenerate
    python3 tools/author_project_symbols.py --check    # verify it is current
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
LIB_DIR = os.path.join(REPO_ROOT, "libraries")
PROV_DIR = os.path.join(LIB_DIR, "provenance")
OUT_SYM = os.path.join(LIB_DIR, "stage1_iv.kicad_sym")
OUT_JSON = os.path.join(PROV_DIR, "stage1_iv_pin_provenance.json")

ADS_SRC = os.path.join(PROV_DIR, "ads1256_sbas288k_terminal_functions.txt")
OPA_SRC = os.path.join(PROV_DIR, "opa548t_akl_symbol.txt")


# =====================================================================
#  parse: ADS1256 "Terminal Functions" table (TI SBAS288K, page 5)
# =====================================================================

_TERM_RE = re.compile(
    r"^(?P<name>[A-Z0-9/]+)\s+"
    r"(?P<n1255>\d+|—|-)\s+"
    r"(?P<n1256>\d+)\s+"
    r"(?P<io>.+?)\s{2,}"
    r"(?P<desc>\S.*)$"
)

_ADS_ETYPE = {
    "Analog power supply": "power_in",
    "Analog ground": "power_in",
    "Digital power supply": "power_in",
    "Digital ground": "power_in",
}


def _ads_etype(name, io_col, desc):
    if desc in _ADS_ETYPE:
        return _ADS_ETYPE[desc]
    io = io_col.lower()
    if "output" in io and "input" not in io:
        return "output"
    if "io" in io or ("input" in io and "output" in io):
        return "bidirectional"
    if "input" in io:
        return "input"
    if name in ("XTAL2",):
        return "output"
    return "unspecified"


def parse_ads1256(path=ADS_SRC):
    """Read (number, name, etype) for every ADS1256 pin out of the excerpt."""
    pins = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith(("TERMINAL", "NAME", "(")):
                continue
            m = _TERM_RE.match(line.strip())
            if not m:
                continue
            pins.append({
                "number": m.group("n1256"), "name": m.group("name"),
                "etype": _ads_etype(m.group("name"), m.group("io"),
                                    m.group("desc").strip()),
                "description": m.group("desc").strip(),
                "source": f"{os.path.relpath(path, REPO_ROOT)}:{lineno}",
            })
    if len(pins) != 28:
        raise SystemExit(
            f"{path}: parsed {len(pins)} ADS1256 pins, expected 28. "
            "The provenance excerpt or the parser is wrong; refusing to emit a "
            "symbol with an incomplete pin list.")
    numbers = sorted(int(p["number"]) for p in pins)
    if numbers != list(range(1, 29)):
        raise SystemExit(f"{path}: ADS1256 pin numbers are not 1..28: {numbers}")
    return pins


# =====================================================================
#  parse: OPA548T symbol excerpt (Alternate KiCad Library)
# =====================================================================

_PIN_BLOCK_RE = re.compile(
    r"\(pin\s+(?P<etype>\S+)\s+\S+\s*\n"
    r"(?:(?!\(pin\s).)*?"
    r'\(name\s+"(?P<name>(?:[^"\\]|\\.)*)"'
    r"(?:(?!\(pin\s).)*?"
    r'\(number\s+"(?P<number>(?:[^"\\]|\\.)*)"',
    re.S)


def parse_opa548t(path=OPA_SRC):
    """Read (number, name, etype) for every OPA548T pin out of the excerpt."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    body = text.split("BEGIN VERBATIM EXCERPT", 1)[-1]
    offset = text.index(body)
    pins = []
    for m in _PIN_BLOCK_RE.finditer(body):
        lineno = text.count("\n", 0, offset + m.start()) + 1
        pins.append({
            "number": m.group("number"), "name": m.group("name"),
            "etype": m.group("etype"), "description": "",
            "source": f"{os.path.relpath(path, REPO_ROOT)}:{lineno}",
        })
    if len(pins) != 7:
        raise SystemExit(
            f"{path}: parsed {len(pins)} OPA548T pins, expected 7 (TO-220-7).")
    numbers = sorted(int(p["number"]) for p in pins)
    if numbers != list(range(1, 8)):
        raise SystemExit(f"{path}: OPA548T pin numbers are not 1..7: {numbers}")
    return pins


# =====================================================================
#  emit: the project symbol library
# =====================================================================

# KiCad's s-expression reader for .kicad_sym is STRICT. Two things it rejects
# that this generator originally emitted, both found by handing the file to
# kicad-cli (see tools/check_kicad_loads.py, which now runs in CI):
#
#   * a leading ";" comment block — the file must begin with (kicad_symbol_lib
#   * (generator_version ...) — a KiCad 9 field
#
# and a third that only bites on KiCad 9: stamping (version 20241209), the v9
# schema, over symbol bodies written in v6/v7 syntax (bare `hide` rather than
# `(hide yes)`). KiCad 9 then applies v9 parsing rules and rejects the file,
# the library fails to load, and every symbol lookup reports "not found".
#
# So the library is emitted in the KiCad 6/7 symbol schema, which KiCad 7 reads
# natively and KiCad 9 migrates on load, and which can therefore be verified
# with the KiCad available in CI. The schematic's embedded (lib_symbols) uses
# the same bodies, so the two still cannot disagree.
LIB_VERSION = "20211014"        # KiCad 6/7 .kicad_sym schema
LIB_GENERATOR = "stage1_iv_author_project_symbols"


def collect_symbols():
    """Build the circuit and return {symbol_name: SymbolDef}, plus the model."""
    sys.path.insert(0, REPO_ROOT)
    import main as project_main
    from generators.schematic import CircuitModel

    _n, _parts, circuit = project_main.build()
    model = CircuitModel(circuit)
    return model, circuit


def library_text(model):
    """Render the full .kicad_sym from the model's SymbolDefs."""
    out = [f"(kicad_symbol_lib (version {LIB_VERSION}) "
           f"(generator {LIB_GENERATOR})"]
    for lib_id in sorted(model.symbols):
        sym = model.symbols[lib_id]
        lines = sym.sexpr()
        # SymbolDef emits `(symbol "stage1_iv:Name" ...)` for embedding in a
        # sheet's (lib_symbols); a library file names the symbol bare.
        lines[0] = lines[0].replace(f'"{sym.lib_id}"', f'"{sym.name}"', 1)
        out.extend(lines)
    out.append(")")
    return "\n".join(out) + "\n"


def provenance_record(model, circuit):
    """Per-symbol record of where each symbol's pins came from."""
    def digest(path):
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    sys.path.insert(0, REPO_ROOT)
    from libraries.custom_symbols import upstream_state

    # Record WHICH upstream revision, never WHERE it sits on this machine: a
    # path here makes the file differ on every checkout and the --check gate
    # unsatisfiable (it broke CI exactly this way).
    libs_info = {}
    try:
        from resolve_kicad_libs import KicadLibraries
        libs_info = {
            label: {k: v for k, v in info.items() if k != "path"}
            for label, info in KicadLibraries().provenance().items()
        }
    except Exception:                                    # noqa: BLE001
        libs_info = {}

    by_symbol = {}
    for part in circuit.parts:
        name = (part.fields.get("Project_Symbol") or "").split(":")[-1]
        if not name or name in by_symbol:
            continue
        by_symbol[name] = {
            "upstream_lib_id": part.fields.get("KiCad_Lib", ""),
            "symbol_source": part.fields.get("Symbol_Source", ""),
            "pins_verified": part.fields.get("Pins_Verified", ""),
            "pins": [{"number": str(p.num), "name": str(p.name)}
                     for p in sorted(part.pins, key=lambda x: str(x.num))],
        }

    state = upstream_state()
    return {
        "generated_by": "tools/author_project_symbols.py",
        "upstream_library_revision": libs_info,
        "symbol_sources": {
            "upstream": sorted(state["used"]),
            "datasheet_authored": sorted(state["authored_used"]),
            "committed_project_library_fallback": sorted(state["fallback_used"]),
        },
        "authored": {
            "stage1_iv:ADS1256": {
                "provenance_class": "RESOLVED",
                "source_document": "Texas Instruments ADS1255/ADS1256 data sheet",
                "document_number": "SBAS288K",
                "revision": "K — JUNE 2003, revised SEPTEMBER 2013",
                "table": "PIN ASSIGNMENTS / Terminal Functions, page 5, "
                         "column 'TERMINAL NO. / ADS1256'",
                "excerpt_file": os.path.relpath(ADS_SRC, REPO_ROOT),
                "excerpt_sha256": digest(ADS_SRC),
                "pins": parse_ads1256(),
            },
            "stage1_iv:OPA548T": {
                "provenance_class": "VERIFY-DATASHEET",
                "source_document": "Alternate KiCad Library (community), "
                                   "symbols/Amplifier_Operational_AKL.kicad_sym",
                "document_number": "n/a — third-party KiCad library, not a data sheet",
                "revision": "git cf93181d5934eca1b0560f66895f1b06958700f7",
                "table": "symbol OPA548T, pin definitions",
                "excerpt_file": os.path.relpath(OPA_SRC, REPO_ROOT),
                "excerpt_sha256": digest(OPA_SRC),
                "limitation": "The TI OPA548 data sheet (SBOS070) could not be "
                              "retrieved: www.ti.com and every datasheet mirror "
                              "are refused by this environment's network egress "
                              "policy. These pin numbers MUST be re-verified "
                              "against SBOS070 before fabrication.",
                "pins": parse_opa548t(),
            },
        },
        "symbols": dict(sorted(by_symbol.items())),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--check", action="store_true",
                    help="verify the committed library matches its sources")
    args = ap.parse_args(argv)

    model, circuit = collect_symbols()
    text = library_text(model)
    record_text = json.dumps(provenance_record(model, circuit), indent=2) + "\n"

    if args.check:
        problems = []
        for path, expected in ((OUT_SYM, text), (OUT_JSON, record_text)):
            if not os.path.isfile(path):
                problems.append(f"missing: {path}")
            elif open(path, encoding="utf-8").read() != expected:
                problems.append(f"stale (regenerate): {path}")
        if problems:
            for p in problems:
                print("FAIL " + p, file=sys.stderr)
            return 1
        print(f"OK  libraries/stage1_iv.kicad_sym is current "
              f"({len(model.symbols)} symbols)")
        return 0

    os.makedirs(PROV_DIR, exist_ok=True)
    with open(OUT_SYM, "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        fh.write(record_text)
    print(f"wrote {OUT_SYM} ({len(model.symbols)} symbols)")
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
