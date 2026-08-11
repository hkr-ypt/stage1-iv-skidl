#!/usr/bin/env python3
"""
check_no_abs_paths.py — no committed file may contain a machine-specific path.
==============================================================================

An absolute path in a committed file is a silent portability bug: it works on
the machine it was written on and nowhere else. This walks every tracked file
and fails on any absolute filesystem path, with a small allow-list of forms
that are portable by construction:

  * KiCad path variables — ``${KIPRJMOD}``, ``${KICAD9_SYMBOL_DIR}`` …
  * repo-relative paths and ``$GITHUB_WORKSPACE``-rooted CI paths
  * POSIX install *prefixes* named as defaults inside the resolver, which are
    documented candidates probed at runtime, never baked into output
  * URLs, which are not filesystem paths

Run::

    python3 tools/check_no_abs_paths.py [--verbose]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

# Absolute POSIX paths and Windows drive paths.
# A leading "}" means the path is rooted at a ${VARIABLE} — portable, and the
# only form the lib tables are allowed to use.
ABS_RE = re.compile(r"(?<![\w$.@/}-])(/(?:[A-Za-z0-9_.+-]+/)+[A-Za-z0-9_.+-]*"
                    r"|[A-Za-z]:\\[^\s\"']+)")

# Files whose whole purpose is to name candidate install locations, or which
# record where a value was read from on the machine that read it.
EXEMPT_FILES = {
    # The resolver's job is to know the per-platform default locations. They
    # are candidates probed at runtime, never written into any output.
    "tools/resolve_kicad_libs.py",
    "tools/kicad_env.py",
    # Provenance records: the whole point is to say which file on which
    # machine a value came from. Regenerated, never read back as a path.
    "docs/audit_identifiers_before.md",
    "docs/audit_identifiers_after.md",
    "libraries/provenance/opa548t_akl_symbol.txt",
    # Narrative documents quoting diagnostic transcripts verbatim.
    "docs/identifier_resolution.md",
    "docs/power_flags_and_erc.md",
    "docs/library_strategy.md",
    "tools/check_no_abs_paths.py",
    "tools/install_kicad_libs.sh",
}

# The schematic's sheet names, which are also the block names. Kept as one
# list so the two KiCad-path exemptions below cannot drift apart.
_SHEETS = "|".join([
    "power", "mcu", "dac_sweep", "output_buffer", "current_sense",
    "voltage_sense", "adc", "protection"])

# Path-like strings that are portable by construction.
EXEMPT_PATTERNS = [
    re.compile(r"^/tmp/"),                   # scratch paths created at runtime
    # The conventional POSIX install prefix, named in prose as the place
    # tools/install_kicad_libs.sh puts things BY DEFAULT. It is a documented
    # default, overridable by argument and by every resolution route above it,
    # not a value any code reads back.
    re.compile(r"^/usr/share/kicad/"),
    re.compile(r"^/dev/null$"),
    re.compile(r"^/Applications/KiCad/"),    # macOS default, quoted in docs
    # KiCad net names. On the board and in the project file a net is named by
    # the sheet path it resolves at - "/adc/ADC_AIN0" for a net local to the
    # adc sheet, "/DRDY" for one that crosses sheets. Those are KiCad net
    # names, not filesystem paths, and they are generated: the sheet names come
    # from BLOCK_ORDER and the net names from blocks/nets.py, so nothing here
    # is machine-specific. See generators/schematic.py::board_net_name.
    re.compile(r"^/(" + _SHEETS + r")?/?[A-Za-z_][A-Za-z0-9_+-]*$"),
    # KiCad human-readable SHEET paths: "/adc/" is SCH_SHEET_PATH::
    # PathHumanReadable() for the adc sheet, and is what a board footprint's
    # (sheetname ...) carries. Same generated sheet names as above, with the
    # trailing separator KiCad writes; still not a filesystem path.
    re.compile(r"^/(" + _SHEETS + r")/$"),
    # KiCad hierarchy paths: "/<uuid>/<uuid>" inside .kicad_sch and .net name
    # sheet instances, not the filesystem.
    re.compile(r"^/(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
               r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/?)+$"),
]

# Lines whose leading token means the value is a KiCad hierarchy path, plus
# the POSIX portable shebang, which names an interpreter resolver rather than
# a location on one machine.
CONTEXT_SKIP = re.compile(r"\(\s*(?:path|sheetpath|tstamps)\b|^#!")

TEXT_SUFFIXES = {".py", ".sh", ".md", ".yml", ".yaml", ".json", ".txt", ".cfg",
                 ".toml", ".kicad_pro", ".kicad_sym", ".kicad_sch", ".net", ""}


def tracked_files():
    out = subprocess.run(["git", "-C", REPO_ROOT, "ls-files"],
                         capture_output=True, text=True, check=True)
    return [p for p in out.stdout.splitlines() if p]


def scan(verbose=False):
    findings = []
    for rel in tracked_files():
        if rel in EXEMPT_FILES:
            continue
        _base, ext = os.path.splitext(rel)
        if ext not in TEXT_SUFFIXES and not rel.endswith("-lib-table"):
            continue
        path = os.path.join(REPO_ROOT, rel)
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(lines, 1):
            if CONTEXT_SKIP.search(line):
                continue
            for m in ABS_RE.finditer(line):
                hit = m.group(0)
                if any(p.match(hit) for p in EXEMPT_PATTERNS):
                    continue
                if "://" in line[:m.start()][-8:]:
                    continue                      # part of a URL
                findings.append((rel, n, hit, line.strip()[:100]))
        if verbose:
            print(f"  scanned {rel}")
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    findings = scan(args.verbose)
    files = len(tracked_files())
    if findings:
        print(f"ABSOLUTE PATHS FOUND in {len({f[0] for f in findings})} file(s):")
        for rel, n, hit, line in findings:
            print(f"  {rel}:{n}: {hit}\n      {line}")
        print("\nUse ${KIPRJMOD}, ${KICAD9_SYMBOL_DIR}, ${KICAD9_FOOTPRINT_DIR} "
              "or a repo-relative path instead.")
        return 1
    print(f"no absolute paths in {files} tracked files "
          f"({len(EXEMPT_FILES)} documented exemptions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
