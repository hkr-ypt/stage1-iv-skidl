# routed/ — the original, unipolar, human-routed board (reference)

This is the KiCad 10 project a person placed and routed: `diodes.kicad_pcb`,
its project file and its schematic. It implements the design as it was
**before** the bipolar-measurement change (U6/U7 REF and the ADS1256's AINCOM on
AGND; no R32/R33).

**Do not edit anything here.** It is the reference the reproducible layout is
proven against:

* `layout/stage1_iv.layout` is a capture of `diodes.kicad_pcb`
  (`python3 tools/capture_layout.py routed/diodes.kicad_pcb
  layout/stage1_iv.layout --check` proves it is current);
* `python3 tools/reproduce_reference.py` regenerates this board from that
  layout and shows KiCad sees the same board (DRC, zone fill, re-save);
* `validation/routed_board_check.py` compares the board of record against it,
  so that anything reported on the bipolar board that this board does not
  also have is attributed to the bipolar ECO.

The **board of record** — bipolar, fully routed, generated from source — is
`../stage1_iv.kicad_pcb`. See `../docs/physical_design.md`.

Note: this project's DRC settings switch clearance checking off
(`"clearance": "ignore"` in `diodes.kicad_pro`). The checks above run with it
on; see `../docs/physical_design.md`, "Design rules and what DRC says".
