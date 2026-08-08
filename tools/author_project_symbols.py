#!/usr/bin/env python3
"""
author_project_symbols.py — generate ``libraries/stage1_iv.kicad_sym``.
=======================================================================

Two parts in this design have **no symbol in the official KiCad libraries**:

    ADS1256   (Analog_ADC has ADS1251 only)
    OPA548T   (no OPA548 of any kind exists upstream)

Rather than invent pin numbers, this script *parses* them out of committed,
verbatim provenance excerpts and emits a real KiCad symbol library from the
parse.  No pin number in ``libraries/stage1_iv.kicad_sym`` is typed by hand:
each one is read from a source file under ``libraries/provenance/`` and can be
re-derived by re-running this script.

    ADS1256   <- libraries/provenance/ads1256_sbas288k_terminal_functions.txt
                 verbatim ``pdftotext -layout`` extract of the "Terminal
                 Functions" table on page 5 of the Texas Instruments ADS1255/
                 ADS1256 data sheet, SBAS288K (June 2003, rev. Sept 2013).
                 The "TERMINAL NO. / ADS1256" column is the pin number.

    OPA548T   <- libraries/provenance/opa548t_akl_symbol.txt
                 verbatim excerpt of the OPA548T symbol in the Alternate KiCad
                 Library.  See the header of that file: the TI OPA548 data
                 sheet itself is unreachable from this environment, so these
                 numbers carry a weaker provenance and the part is flagged
                 ``VERIFY-DATASHEET`` rather than ``RESOLVED``.

Usage::

    python3 tools/author_project_symbols.py            # write the library
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

# NAME  <ADS1255 no.>  <ADS1256 no.>  <analog/digital>  <description>
# The ADS1255 column is "—" for pins that exist on the ADS1256 only.
_TERM_RE = re.compile(
    r"^(?P<name>[A-Z0-9/]+)\s+"
    r"(?P<n1255>\d+|—|-)\s+"
    r"(?P<n1256>\d+)\s+"
    r"(?P<io>.+?)\s{2,}"
    r"(?P<desc>\S.*)$"
)

# Electrical type per the table's ANALOG/DIGITAL INPUT/OUTPUT column.
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
            name = m.group("name")
            number = m.group("n1256")
            etype = _ads_etype(name, m.group("io"), m.group("desc").strip())
            pins.append({
                "number": number, "name": name, "etype": etype,
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
#  emit: a real KiCad 9 symbol library
# =====================================================================

GRID = 2.54


def _rect_symbol(name, pins, properties, description, keywords, datasheet):
    """Lay pins out down the left and right edges of a rectangle body."""
    n = len(pins)
    left = pins[: (n + 1) // 2]
    right = pins[(n + 1) // 2:]
    rows = max(len(left), len(right))
    half_h = ((rows + 1) * GRID) / 2.0
    half_w = 5 * GRID

    out = []
    A = out.append
    A(f'\t(symbol "{name}"')
    A("\t\t(pin_names\n\t\t\t(offset 1.016)\n\t\t)")
    A("\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)")
    props = [("Reference", properties["Reference"], half_h + GRID),
             ("Value", name, half_h + GRID * 2),
             ("Footprint", properties.get("Footprint", ""), 0),
             ("Datasheet", datasheet, 0),
             ("Description", description, 0),
             ("ki_keywords", keywords, 0)]
    for idx, (key, value, y) in enumerate(props):
        hide = "\n\t\t\t\t\t(hide yes)" if idx >= 2 else ""
        A(f'\t\t(property "{key}" "{_esc(value)}"')
        A(f"\t\t\t(at 0 {-y if idx == 0 else y:g} 0)")
        A("\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)"
          f"{hide}\n\t\t\t)")
        A("\t\t)")

    A(f'\t\t(symbol "{name}_0_1")')
    out[-1] = f'\t\t(symbol "{name}_0_1"'
    A("\t\t\t(rectangle")
    A(f"\t\t\t\t(start {-half_w:g} {half_h:g})")
    A(f"\t\t\t\t(end {half_w:g} {-half_h:g})")
    A("\t\t\t\t(stroke\n\t\t\t\t\t(width 0.254)\n\t\t\t\t\t(type default)\n\t\t\t\t)")
    A("\t\t\t\t(fill\n\t\t\t\t\t(type background)\n\t\t\t\t)")
    A("\t\t\t)")
    A("\t\t)")

    A(f'\t\t(symbol "{name}_1_1"')
    for side, group in (("left", left), ("right", right)):
        for i, p in enumerate(group):
            y = half_h - GRID * (i + 1)
            if side == "left":
                x, rot = -(half_w + GRID), 0
            else:
                x, rot = (half_w + GRID), 180
            A(f"\t\t\t(pin {p['etype']} line")
            A(f"\t\t\t\t(at {x:g} {y:g} {rot})")
            A("\t\t\t\t(length 2.54)")
            A(f'\t\t\t\t(name "{_esc(p["name"])}"')
            A("\t\t\t\t\t(effects\n\t\t\t\t\t\t(font\n\t\t\t\t\t\t\t"
              "(size 1.27 1.27)\n\t\t\t\t\t\t)\n\t\t\t\t\t)")
            A("\t\t\t\t)")
            A(f'\t\t\t\t(number "{_esc(p["number"])}"')
            A("\t\t\t\t\t(effects\n\t\t\t\t\t\t(font\n\t\t\t\t\t\t\t"
              "(size 1.27 1.27)\n\t\t\t\t\t\t)\n\t\t\t\t\t)")
            A("\t\t\t\t)")
            A("\t\t\t)")
    A("\t\t)")
    A("\t\t(embedded_fonts no)")
    A("\t)")
    return "\n".join(out)


def _esc(text):
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


HEADER_COMMENT = """\
; stage1_iv.kicad_sym — project-local KiCad symbols for the Stage 1 I–V
; Characterization System.
;
; GENERATED FILE — do not edit by hand.
;   regenerate with:  python3 tools/author_project_symbols.py
;
; It holds only the parts that have NO symbol in the official KiCad libraries.
; Every pin number below is parsed out of a committed provenance excerpt under
; libraries/provenance/ (see libraries/provenance/stage1_iv_pin_provenance.json
; for the per-pin source file and line).
"""


def build_library():
    ads_pins = parse_ads1256()
    opa_pins = parse_opa548t()

    ads_pins.sort(key=lambda p: int(p["number"]))
    opa_pins.sort(key=lambda p: int(p["number"]))

    body = [HEADER_COMMENT.rstrip(), "(kicad_symbol_lib",
            "\t(version 20241209)",
            '\t(generator "stage1_iv:tools/author_project_symbols.py")',
            '\t(generator_version "9.0")']
    body.append(_rect_symbol(
        "ADS1256", ads_pins, {"Reference": "U",
                              "Footprint": "Package_SO:SSOP-28_5.3x10.2mm_P0.65mm"},
        "24-bit delta-sigma ADC, 8-channel mux, PGA, SPI, SSOP-28 (TI DB package)",
        "ADC 24-bit delta-sigma SPI ADS1256",
        "https://www.ti.com/lit/ds/symlink/ads1256.pdf"))
    body.append(_rect_symbol(
        "OPA548T", opa_pins,
        {"Reference": "U",
         "Footprint": "Package_TO_SOT_THT:TO-220-7_P2.54x3.7mm_StaggerOdd_Lead3.8mm_Vertical"},
        "High-current power op-amp with adjustable current limit, TO-220-7",
        "opamp power current limit OPA548",
        "https://www.ti.com/lit/ds/symlink/opa548.pdf"))
    body.append(")")
    return "\n".join(body) + "\n", ads_pins, opa_pins


def provenance_record(ads_pins, opa_pins):
    def digest(path):
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    return {
        "generated_by": "tools/author_project_symbols.py",
        "symbols": {
            "stage1_iv:ADS1256": {
                "provenance_class": "RESOLVED",
                "source_document": "Texas Instruments ADS1255/ADS1256 data sheet",
                "document_number": "SBAS288K",
                "revision": "K — JUNE 2003, revised SEPTEMBER 2013",
                "table": "PIN ASSIGNMENTS / Terminal Functions, page 5, "
                         "column 'TERMINAL NO. / ADS1256'",
                "excerpt_file": os.path.relpath(ADS_SRC, REPO_ROOT),
                "excerpt_sha256": digest(ADS_SRC),
                "pins": ads_pins,
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
                "pins": opa_pins,
            },
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--check", action="store_true",
                    help="verify the committed library matches the sources")
    args = ap.parse_args(argv)

    text, ads_pins, opa_pins = build_library()
    record = provenance_record(ads_pins, opa_pins)
    record_text = json.dumps(record, indent=2) + "\n"

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
        print("OK  libraries/stage1_iv.kicad_sym is current with its sources")
        return 0

    os.makedirs(PROV_DIR, exist_ok=True)
    with open(OUT_SYM, "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        fh.write(record_text)
    print(f"wrote {OUT_SYM} ({len(ads_pins)} ADS1256 pins, "
          f"{len(opa_pins)} OPA548T pins)")
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
