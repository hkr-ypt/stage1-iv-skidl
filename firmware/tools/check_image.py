#!/usr/bin/env python3
"""
check_image.py — link-level checks of a firmware image (firmware_spec.md §9.1
L0) using the GNU Arm binutils.

For an image built by a firmware preset (build/<preset>/stage1_iv_fw-<variant>.elf):

  * no heap and no float formatting: _sbrk, _malloc_r, malloc, _dtoa_r,
    printf-family symbols absent;
  * bring-up-only code (DAC.RAW, HB.*, ADC.RESETPULSE, CRASH) absent from
    release and debug images, present in the bring-up image;
  * single writers (SI-2, SI-10): hb_service is referenced only by app.c,
    port_hb_write only by heartbeat.c, dac_raw_write only by commands.c;
  * memory map: vector table at 0x08000000, nothing allocated in the
    calibration sectors 0x08004000-0x0800BFFF, code from 0x0800C000,
    .noinit is NOBITS (not cleared, not loaded);
  * RAM budget: everything in SRAM (stacks included) <= 90 KiB (spec §7.8).

Usage::

    python3 firmware/tools/check_image.py build/fw-release   # one build tree
    python3 firmware/tools/check_image.py build/fw-*          # several

Exit status 0 when every check passes; each failure is printed.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

BRINGUP_SYMBOLS = ("dac_raw_write", "dac_raw_active", "hb_set_frequency_hz", "hb_bringup_hold",
                   "hb_bringup_holding", "ads_pulse_reset_unannounced")
FORBIDDEN = ("_sbrk", "_sbrk_r", "_malloc_r", "malloc", "free", "_dtoa_r", "printf", "sprintf",
             "snprintf", "vfprintf", "_vfprintf_r", "_svfprintf_r", "puts", "_printf_i")
# symbol -> the only object files allowed to reference it
SINGLE_REFERENCE = {
    "hb_service": {"app.c.obj"},
    "port_hb_write": {"heartbeat.c.obj"},
    "dac_raw_write": {"commands.c.obj"},
}
CAL_LO, CAL_HI = 0x08004000, 0x0800C000
RAM_BUDGET = 90 * 1024


def tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"{name} not found (install the GNU Arm toolchain)")
    return path


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def defined_symbols(elf: Path) -> set[str]:
    out = run(tool("arm-none-eabi-nm"), "--defined-only", str(elf))
    return {line.split()[-1] for line in out.splitlines() if line.strip()}


def sections(elf: Path) -> list[dict]:
    out = run(tool("arm-none-eabi-readelf"), "-S", "-W", str(elf))
    secs = []
    for line in out.splitlines():
        m = re.match(r"\s*\[\s*\d+\]\s+(\S+)\s+(\S+)\s+([0-9a-f]+)\s+[0-9a-f]+\s+([0-9a-f]+)\s+\S+\s+(\S*)",
                     line)
        if m:
            secs.append({"name": m.group(1), "type": m.group(2), "addr": int(m.group(3), 16),
                         "size": int(m.group(4), 16), "flags": m.group(5)})
    return secs


def referencing_objects(build: Path, symbol: str) -> set[str]:
    objs = set()
    for obj in build.rglob("*.obj"):
        out = run(tool("arm-none-eabi-nm"), "-u", str(obj))
        if any(line.split()[-1] == symbol for line in out.splitlines() if line.strip()):
            objs.add(obj.name)
    return objs


def check_build(build: Path) -> list[str]:
    errors: list[str] = []
    elfs = sorted(build.glob("stage1_iv_fw-*.elf"))
    if len(elfs) != 1:
        return [f"{build}: expected one stage1_iv_fw-<variant>.elf, found {len(elfs)}"]
    elf = elfs[0]
    variant = elf.stem.split("-", 1)[1]
    syms = defined_symbols(elf)

    for s in FORBIDDEN:
        if s in syms:
            errors.append(f"{elf.name}: forbidden symbol {s} linked")
    present = [s for s in BRINGUP_SYMBOLS if s in syms]
    if variant == "bringup":
        missing = sorted(set(BRINGUP_SYMBOLS) - set(present))
        if missing:
            errors.append(f"{elf.name}: bring-up symbols missing: {missing}")
    elif present:
        errors.append(f"{elf.name}: bring-up symbols in a {variant} image: {present}")

    for sym, allowed in SINGLE_REFERENCE.items():
        refs = referencing_objects(build, sym)
        if variant != "bringup" and sym == "dac_raw_write":
            allowed = set()
        extra = refs - allowed
        if extra:
            errors.append(f"{elf.name}: {sym} referenced by {sorted(extra)} (allowed: {sorted(allowed)})")

    secs = sections(elf)
    by_name = {s["name"]: s for s in secs}
    vec = by_name.get(".isr_vector")
    if vec is None or vec["addr"] != 0x08000000:
        errors.append(f"{elf.name}: .isr_vector not at 0x08000000")
    text = by_name.get(".text")
    if text is None or text["addr"] < 0x0800C000:
        errors.append(f"{elf.name}: .text below flash sector 3")
    for s in secs:
        if "A" in s["flags"] and s["size"] and s["addr"] < CAL_HI and s["addr"] + s["size"] > CAL_LO:
            errors.append(f"{elf.name}: section {s['name']} overlaps the calibration sectors")
    noinit = by_name.get(".noinit")
    if noinit is None or noinit["type"] != "NOBITS":
        errors.append(f"{elf.name}: .noinit missing or not NOBITS")
    ram = sum(s["size"] for s in secs if "A" in s["flags"] and 0x20000000 <= s["addr"] < 0x20020000)
    ram += 1024 + 512    # handler and fault stacks (not sections)
    if ram > RAM_BUDGET:
        errors.append(f"{elf.name}: SRAM use {ram} B exceeds the {RAM_BUDGET} B budget")
    print(f"{elf.name}: {len(errors)} problem(s); SRAM {ram} B; bring-up symbols {len(present)}")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("builds", nargs="+", type=Path)
    args = ap.parse_args(argv)
    errors: list[str] = []
    for b in args.builds:
        errors += check_build(b)
    for e in errors:
        print("ERROR:", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
