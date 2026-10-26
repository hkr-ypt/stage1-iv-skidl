#!/usr/bin/env python3
"""
main.py — Stage 1 I–V Characterization System (SKiDL top level)
===============================================================

Instantiates all eight hierarchical blocks, wires them through the shared global
nets, runs ERC + custom connectivity assertions, prints the component manifest,
and generates the KiCad netlist.

Run:
    python3 main.py                  # validate + emit netlist + schematic + PCB
    python3 main.py --netlist-only   # just emit the netlist
    python3 main.py --schematic-only # just emit the hierarchical schematic
    python3 main.py --pcb-only       # just emit (and check) the board
    python3 main.py --autoplace      # bootstrap: an auto-placed, unrouted
                                     # board in build/autoplace/ (not the
                                     # board of record)

Outputs (written next to this file):
    stage1_iv.net            KiCad netlist (import into a KiCad project)
    stage1_iv.kicad_sch      Top-level KiCad schematic (hierarchical)
    <block>.kicad_sch        One child schematic sheet per block (8 files)
    stage1_iv.kicad_pcb      The board of record: placed and fully routed
    component_manifest.md    Component manifest table

The board is generated from source: the SKiDL circuit (parts, values, nets),
``layout/stage1_iv.layout`` (the placement and copper of the human-routed board
in ``unipolar-routed/``) and ``layout/bipolar.eco`` (the bipolar change, as reasoned
add/remove operations) — see generators/routed_pcb.py and
docs/physical_design.md. It is checked by validation/routed_board_check.py;
KiCad's DRC runs where KiCad 10 is installed and is reported NOT RUN where it
is not. Nothing here reports a DRC pass it did not run.
"""

import sys
import os

from skidl import set_default_tool, KICAD, generate_netlist, reset

from blocks.nets import create_nets, NET_MANIFEST
from blocks.power import create_power_block
from blocks.mcu import create_mcu_block
from blocks.dac_sweep import create_dac_block
from blocks.output_buffer import create_output_buffer_block
from blocks.current_sense import create_current_sense_block
from blocks.voltage_sense import create_voltage_sense_block
from blocks.adc import create_adc_block
from blocks.protection import create_protection_block

from validation.erc import run_erc
from validation.assertions import run_assertions, ALL_ASSERTIONS
from validation.measurement_range import (
    run_measurement_range, MeasurementRangeError,
)
from validation.footprint_check import run_footprint_check, FootprintCheckError
from libraries.custom_symbols import is_dnp, is_virtual

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
import kicad_env  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# Block build order == hierarchical sheet order (sheet 1..8). The block name is
# stamped on every part as a plain Python attribute (`skidl_block`) so the
# schematic generator can recover sheet membership from the live circuit. A
# plain attribute never enters part.fields, so the netlist is unaffected.
BLOCKS = [
    ("power", create_power_block),                # Sheet 1
    ("mcu", create_mcu_block),                    # Sheet 2
    ("dac_sweep", create_dac_block),              # Sheet 3
    ("output_buffer", create_output_buffer_block),# Sheet 4
    ("current_sense", create_current_sense_block),# Sheet 5
    ("voltage_sense", create_voltage_sense_block),# Sheet 6
    ("adc", create_adc_block),                    # Sheet 7
    ("protection", create_protection_block),      # Sheet 8
]


def build():
    """Build the complete circuit. Returns (nets_namespace, parts_dict, circuit)."""
    reset()
    set_default_tool(KICAD)

    n = create_nets()

    parts = {}
    for block_name, create_block in BLOCKS:
        block_parts = create_block(n)
        for part in block_parts.values():
            part.skidl_block = block_name
        parts.update(block_parts)

    circuit = n.AGND.circuit
    return n, parts, circuit


SHEET_OF = {
    # FLG1..FLG6 are PWR_FLAG virtual symbols on the rails that enter the
    # board at J1 / FB1 / the R31 star point — see blocks/power.py.
    "1 Power": ["J1", "U1", "U2", "FB1", "R31", "C1", "C2", "C3", "C4", "C5",
                "C6", "C7", "C8", "C9", "C10",
                "#FLG1", "#FLG2", "#FLG3", "#FLG4", "#FLG5", "#FLG6"],
    "2 MCU": ["U9", "Y2", "C32", "C33", "C34", "C35", "C36", "C37", "C38", "C39",
              "C40", "C43", "FB2", "R21", "C41", "SW1", "R22", "R23", "LED1",
              "J2", "J3", "U10", "R24", "R25", "R26", "R27", "R28", "R29", "C42",
              # C45: +3V3D decoupler added on the routed board, carried here.
              "C45",
              # R36/C47/D6/C48/R37: the safe-state heartbeat charge pump.
              "R36", "C47", "D6", "C48", "R37",
              # FLG7 = VBUS (from the USB host), FLG8 = VDDA (after FB2).
              "#FLG7", "#FLG8"],
    # U11/C46/R35: the DAC reference gate; R34: the CS_DAC pull-up (audit F2);
    # FLG9: DAC_VREF, driven from VREF_2V5 through U11's passive switch.
    "3 DAC/Sweep": ["U4", "C13", "U3", "C11", "C12", "R1", "R2", "R3", "R4",
                    "R5", "R6", "TP1", "U11", "C46", "R35", "R34", "#FLG9"],
    # R38/C49: the output snubber, DNP (audit V1).
    "4 Power Buffer": ["U5", "R7", "R8", "C14", "C15", "C16", "C17", "C18", "R9",
                       "C19", "TP2", "R38", "C49"],
    "5 Current Sense": ["R10", "U6", "R11", "C20", "C21", "TP3", "TP4"],
    # R32/R33 are the bottom arms of the Kelvin attenuator (R12/R13 on top).
    "6 Voltage Sense": ["U7", "R12", "R13", "R14", "R32", "R33", "D1", "D2",
                        "D3", "D4", "C22", "C23", "TP5", "TP6", "TP7"],
    # C44 bypasses VREF_2V5 at U8 VREFP/AINCOM; C31 is a +3V3D decoupler
    # added on the routed board, carried here.
    "7 ADC": ["U8", "Y1", "C26", "C27", "C28", "C29", "C30", "C31", "R19",
              "R20", "R15", "R16", "R17", "R18", "C24", "C25", "TP8", "C44"],
    # J4..J7 are the four separate single-pin DUT banana jacks
    # (FORCE+, SENSE+, SENSE-, FORCE-) — see blocks/protection.py.
    "8 Protection/DUT": ["F1", "R30", "D5", "J4", "J5", "J6", "J7"],
}


def sheet_for(ref):
    for sheet, refs in SHEET_OF.items():
        if ref in refs:
            return sheet
    return "?"


def write_component_manifest(circuit):
    """Emit a Markdown component manifest table."""
    rows = []
    for part in sorted(circuit.parts, key=lambda p: (p.ref[0], _refnum(p.ref))):
        mpn = part.fields.get("MPN", "")
        lib = part.fields.get("KiCad_Lib", "")
        pv = part.fields.get("Pins_Verified", "")
        status = {
            "RESOLVED": "OK — pin numbers read from " + (
                part.fields.get("Symbol_Source", "the symbol library")),
            "VERIFY-DATASHEET": "VERIFY pin#s @ datasheet",
        }.get(pv, pv)
        if is_virtual(part):
            fit = "virtual"
        elif is_dnp(part):
            fit = "DNP"
        else:
            fit = "fit"
        rows.append((part.ref, str(part.value), mpn, lib,
                     part.footprint or "", sheet_for(part.ref), fit, status))

    n_virtual = sum(1 for r in rows if r[6] == "virtual")
    n_dnp = sum(1 for r in rows if r[6] == "DNP")
    lines = [
        "# Component Manifest — Stage 1 I–V Characterization System",
        "",
        f"Total components: **{len(rows)}** — {len(rows) - n_virtual} on the "
        f"board ({len(rows) - n_virtual - n_dnp} fitted, {n_dnp} DNP "
        f"footprints) and {n_virtual} schematic-only PWR_FLAG symbols.",
        "",
        "| Ref | Value | MPN | KiCad Library | Footprint | Sheet | Fit | Status |",
        "|-----|-------|-----|---------------|-----------|-------|-----|--------|",
    ]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")

    text = "\n".join(lines) + "\n"
    out = os.path.join(HERE, "component_manifest.md")
    with open(out, "w") as f:
        f.write(text)
    print(f"\nComponent manifest written to {out}")
    return rows


def _refnum(ref):
    num = "".join(ch for ch in ref if ch.isdigit())
    return int(num) if num else 0


def print_net_manifest(n):
    print("\n" + "=" * 70)
    print("NET MANIFEST")
    print("=" * 70)
    name_to_attr = {
        "+15VA": "P15VA", "-15VA": "N15VA", "+5VA": "P5VA", "+3V3D": "P3V3D",
        "AGND": "AGND", "DGND": "DGND", "VREF_2V5": "VREF_2V5",
        "FORCE+": "FORCE_PLUS", "FORCE-": "FORCE_MINUS",
        "SENSE+": "SENSE_PLUS", "SENSE-": "SENSE_MINUS",
    }
    for group, names in NET_MANIFEST.items():
        print(f"\n  {group.upper()}")
        for name in names:
            attr = name_to_attr.get(name, name)
            net = getattr(n, attr)
            print(f"    {name:<14} {len(net.get_pins()):>2} pins")


def write_schematic(circuit):
    """Emit the hierarchical KiCad schematic from the live circuit."""
    from generators.schematic import generate_schematic
    sch_files = generate_schematic(circuit, out_dir=HERE)
    print("\nKiCad schematic written:")
    for path in sch_files:
        print(f"  {path}")
    return sch_files


def write_pcb(circuit):
    """Emit the board of record and check it.

    Returns (generator report, routed_board_check report), or raises. The board
    needs real footprint files, so this only runs when the KiCad libraries are
    installed; callers report SKIPPED otherwise rather than pretending.
    """
    from generators.routed_pcb import generate_routed_pcb
    from validation.routed_board_check import run_routed_board_check

    path, report = generate_routed_pcb(circuit, out_dir=HERE)
    print(f"\nKiCad board written to {path}")
    print(f"  {report['parts']} footprints, {report['nets']} nets, "
          f"{report['segments']} tracks, {report['vias']} vias, "
          f"{report['zones']} zones")
    print(f"  from {report['layout']} + "
          + ", ".join(f"{e} ({n} operations)" for e, n in report["ecos"]))
    return report, run_routed_board_check(path)


def write_autoplaced_pcb(circuit):
    """The bootstrap auto-placer: a placed, UNROUTED board in build/autoplace/.

    Not the board of record. It is how a new design gets a checkable starting
    point for a person to lay out; this design's layout is in layout/.
    """
    from generators.pcb import generate_pcb
    from validation.drc_check import run_drc_check

    out_dir = os.path.join(HERE, "build", "autoplace")
    os.makedirs(out_dir, exist_ok=True)
    for name in ("stage1_iv.kicad_pro", "stage1_iv.kicad_dru"):
        src = os.path.join(HERE, name)
        if os.path.isfile(src):
            with open(src, encoding="utf-8") as fh, \
                    open(os.path.join(out_dir, name), "w",
                         encoding="utf-8") as out:
                out.write(fh.read())
    path, report = generate_pcb(circuit, out_dir=out_dir)
    print(f"\nAuto-placed board written to {path}")
    print(f"  {report['board_mm'][0]} x {report['board_mm'][1]} mm, "
          f"{report['parts']} footprints, {report['nets']} nets, "
          f"{report['mounting_holes']} mounting holes")
    print("  UNROUTED — a bootstrap placement, not the board of record.")
    return report, run_drc_check(path)


def capability_banner(circuit=None, erc_summary=None, checks=None,
                      drc_report=None):
    """State plainly what this run was able to verify, and what it was not.

    The build deliberately runs on a machine with no KiCad installed — that is
    a genuinely useful property — but a run that could not check anything
    against KiCad must say SKIPPED. It must never print, or return, anything
    that could be read as a KiCad-conformance pass.
    """
    env = kicad_env.diagnose()
    checks = checks or {}
    lines = []
    A = lines.append
    A("=" * 70)
    A("BUILD CAPABILITY")
    A("=" * 70)
    if env["available"]:
        A(f"  KiCad libraries ....... FOUND ({env['state']}) at "
          f"{env['symbol_dir']}")
        prov = env.get("provenance", {}).get("symbols", {})
        if prov.get("tag"):
            A(f"                          pinned {prov['tag']} @ "
              f"{prov.get('commit', '')[:12]}")
        if env["state"] == "INCOMPLETE":
            A(f"                          MISSING: "
              f"{env['required_symbols_missing']}")
    else:
        A("  KiCad libraries ....... NOT FOUND")
        A("                          (run tools/kicad_env.py --diagnose)")

    for label, key in (("Symbol resolution", "symbols"),
                       ("Footprint resolution", "footprints")):
        got = checks.get(key)
        dots = "." * (22 - len(label))
        if got is None:
            A(f"  {label} {dots} SKIPPED (no KiCad libraries to check against)")
        else:
            A(f"  {label} {dots} VERIFIED ({got[0]}/{got[1]})")

    if erc_summary is None:
        A("  SKiDL ERC ............. not run")
    else:
        A(f"  SKiDL ERC ............. {erc_summary['erc_errors']} errors, "
          f"{erc_summary['erc_warnings']} warnings")
    A("  KiCad ERC ............. NOT RUN (open stage1_iv.kicad_sch in KiCad "
      "and run ERC;")
    A("                          see docs/LIBRARY_DEPENDENCIES.md)")

    if drc_report is None:
        A("  Board checks .......... SKIPPED (no board generated this run)")
        A("  KiCad DRC ............. NOT RUN")
    else:
        checks_ = {k: v for k, v in drc_report["checks"].items()
                   if k != "kicad_drc"}
        passed = sum(1 for c in checks_.values() if c["status"] == "PASS")
        A(f"  Board checks .......... {passed}/{len(checks_)} PASS "
          f"(source, parity, netclasses; placement heuristics")
        A("                          relative to the original unipolar-routed/ board)")
        noop = drc_report["checks"].get("update_pcb_noop", {})
        A(f"  Update PCB from Sch ... "
          + ("would change NOTHING — the board and the schematic agree"
             if noop.get("status") == "PASS"
             else "WOULD REWRITE THE BOARD; see validation/update_pcb_noop.py"))
        drc = drc_report["checks"]["kicad_drc"]
        if drc["status"] == "SKIPPED":
            A(f"  KiCad DRC ............. NOT RUN — {drc['reason']}")
            A("                          The board's DRC state is UNKNOWN. "
              "Run it in KiCad 10 — the board carries a\n"
              "                          footprint (units ...) block KiCad 9 cannot parse.")
        else:
            ab, pol = drc["as_built"], drc["policy"]

            def split(counts):
                err = sum(n for k, n in counts.items() if k.endswith("/error"))
                return err, sum(counts.values()) - err
            ab_e, ab_w = split(ab["violations"])
            pol_e, pol_w = split(pol["violations"])
            A(f"  KiCad DRC ............. {drc['ran_with']}: "
              f"{ab['unconnected']} unconnected, "
              f"{pol['parity']} parity problems")
            A(f"    as-built rules ...... {ab_e} errors, {ab_w} warnings "
              f"(the original has the same warnings)")
            A(f"    netclass policy ..... {pol_e} errors, {pol_w} warnings — "
              f"all {pol['pre_existing']} on the original")
            A(f"                          unipolar-routed/ board too, "
              f"{len(pol['new'])} new; NOT clean under the policy classes")
    return "\n".join(lines), env


def main():
    netlist_only = "--netlist-only" in sys.argv
    schematic_only = "--schematic-only" in sys.argv
    pcb_only = "--pcb-only" in sys.argv
    autoplace = "--autoplace" in sys.argv

    n, parts, circuit = build()

    banner, env = capability_banner()
    print(banner)

    # Structural guard FIRST: every symbol and footprint identifier must
    # resolve against the installed KiCad libraries, every declared pin must
    # correspond to a real pad, and every pad must be netted or explicitly
    # no-connect. Emitting a netlist that cannot be laid out is worse than
    # emitting nothing, so this aborts the build.
    # Without KiCad libraries there is nothing to check identifiers against.
    # Skipping is legitimate; pretending to have checked is not.
    checks = {}
    if env["available"]:
        try:
            fp_summary = run_footprint_check(circuit)
        except FootprintCheckError as exc:
            print(f"\nSTRUCTURAL CHECK: FAIL\n{exc}", file=sys.stderr)
            return 1
        n_parts = fp_summary["parts"]
        checks["symbols"] = (n_parts, n_parts)
        checks["footprints"] = (n_parts - fp_summary["virtual_parts"],
                                n_parts - fp_summary["virtual_parts"])
    else:
        print("\n" + "=" * 70)
        print("STRUCTURAL CHECK: SKIPPED — no KiCad libraries on this machine.")
        print("Symbol and footprint identifiers were NOT verified. This build")
        print("has not been shown to be KiCad-conformant.")
        print("=" * 70)

    if pcb_only:
        if not env["available"]:
            print("\nPCB generation needs the KiCad footprint libraries; none "
                  "are installed. Run tools/install_kicad_libs.sh.",
                  file=sys.stderr)
            return 1
        from validation.routed_board_check import RoutedBoardError
        from generators.layout import LayoutError
        try:
            write_pcb(circuit)
        except (RoutedBoardError, LayoutError) as exc:
            print(f"\nBOARD CHECK: FAIL\n{exc}", file=sys.stderr)
            return 1
        return 0

    if autoplace:
        if not env["available"]:
            print("\nThe auto-placer needs the KiCad footprint libraries; none "
                  "are installed.", file=sys.stderr)
            return 1
        from validation.drc_check import DrcCheckError
        try:
            write_autoplaced_pcb(circuit)
        except DrcCheckError as exc:
            print(f"\nAUTO-PLACED BOARD CHECK: FAIL\n{exc}", file=sys.stderr)
            return 1
        return 0

    if schematic_only:
        write_schematic(circuit)
        return 0

    netlist_path = os.path.join(HERE, "stage1_iv.net")
    generate_netlist(tool=KICAD, file_=netlist_path)
    print(f"\nKiCad netlist written to {netlist_path}")

    if netlist_only:
        return 0

    sch_files = write_schematic(circuit)

    # The board needs the real footprint files, so it can only be built where
    # the libraries are installed. Where they are not, say so — do not emit a
    # board from guessed geometry.
    pcb_report = drc_report = None
    if env["available"]:
        from validation.routed_board_check import RoutedBoardError
        from generators.layout import LayoutError
        try:
            pcb_report, drc_report = write_pcb(circuit)
        except (RoutedBoardError, LayoutError) as exc:
            print(f"\nBOARD CHECK: FAIL\n{exc}", file=sys.stderr)
            return 1
    else:
        print("\nPCB generation ....... SKIPPED — no KiCad footprint libraries "
              "on this machine.")

    print_net_manifest(n)
    write_component_manifest(circuit)

    try:
        erc_summary = run_erc(circuit)
    except AssertionError as exc:
        print(f"\nERC: FAIL\n{exc}", file=sys.stderr)
        return 1
    run_assertions(n, circuit)
    n_assert = len(ALL_ASSERTIONS)
    try:
        ranges = run_measurement_range(circuit)
    except MeasurementRangeError as exc:
        print(f"\nMEASUREMENT RANGE: FAIL\n{exc}", file=sys.stderr)
        return 1

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Parts .............. {erc_summary['parts']} "
          f"({len(erc_summary['power_flag_nets'])} PWR_FLAGs)")
    print(f"  Nets ............... {erc_summary['nets']}")
    print(f"  Netlist ............ {os.path.basename(netlist_path)}")
    print(f"  Schematic .......... {len(sch_files)} sheets "
          f"({os.path.basename(sch_files[0])} + 8 child sheets)")
    if pcb_report is None:
        print("  Board .............. SKIPPED — no KiCad footprint libraries")
    else:
        drc = drc_report["checks"]["kicad_drc"]
        routed = ("fully routed, 0 unconnected (KiCad DRC)"
                  if drc["status"] != "SKIPPED"
                  else "routing per layout/; KiCad DRC NOT RUN")
        print(f"  Board .............. {pcb_report['parts']} footprints, "
              f"{pcb_report['segments']} tracks, {pcb_report['vias']} vias, "
              f"{routed}")
    # Derived from the ERC result, never asserted. run_erc() already raises on
    # any error or un-allow-listed warning, so reaching here means both are
    # zero; printing the actual numbers keeps that visible and auditable.
    erc_errors = erc_summary["erc_errors"]
    erc_warnings = erc_summary["erc_warnings"]
    erc_allowed = erc_summary["erc_warnings_allowed"]
    print(f"  SKiDL ERC .......... {erc_errors} errors, {erc_warnings} warnings"
          + (f" ({erc_allowed} allow-listed)" if erc_allowed else ""))
    print(f"  Power flags ........ {len(erc_summary['power_flag_nets'])} on "
          f"{', '.join(erc_summary['power_flag_nets'])}")
    if checks:
        print("  Structural check ... identifiers, pads and provenance "
              "verified against the")
        print("                       installed KiCad libraries")
    else:
        print("  Structural check ... SKIPPED — no KiCad libraries; identifiers")
        print("                       and pads were NOT verified")
    print(f"  Assertions ......... {n_assert}/{n_assert} connectivity "
          f"assertions hold")
    print(f"  Measurement ........ V {ranges['v_text']}, "
          f"I {ranges['i_text']} (bipolar)")
    print("\n  NOTE: SKiDL ERC and KiCad ERC are different checks with "
          "different coverage.\n        See validation/erc.py — a clean SKiDL "
          "run is not evidence about KiCad ERC.")

    print()
    print(capability_banner(circuit, erc_summary, checks, drc_report)[0])

    if erc_errors or erc_summary["erc_warnings_offending"]:
        print("\nDesign build FAILED on ERC.", file=sys.stderr)
        return 1

    print("\nDesign build complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
