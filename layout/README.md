# layout/ — the physical design, as source

* `stage1_iv.layout` — placement, footprint-text positions, every track and
  via, zone definitions, outline, board setup and the as-built design rules of
  the human-routed board in `../unipolar-routed/`, captured by
  `tools/capture_layout.py`. One item per line, sorted.
* `bipolar.eco` — the bipolar-measurement change, as `(add ...)`/`(remove ...)`
  operations on that layout, each with a `(reason ...)`.
* `audit_corrections.eco` — the final hardware audit corrections, applied after
  `bipolar.eco`: the DAC reference gate and heartbeat pump, the channel-B
  midpoint feed, R30 as a 2 W PR02, the DNP snubber pads, the sunk status LED,
  and the J1 / DUT jack silkscreen labels. Its `(add (graphic ...))`
  operations add free board text.
* `final_corrections.eco` — the remaining hardware corrections, applied after
  `audit_corrections.eco`: F1 moved to the bottom edge, R10 as the RS02B
  (DIN0614), the FORCE+ photorelay U12 with its LED string R39/D7, the
  dead-rail clamps D8/D9, the FORCE− PTC F2, and the mounting holes H1–H3.
  Its `(add (board_part "H1" "stage1_iv:MountingHole_3.2mm_M3_Isolated" F x y
  rot))` operations place **board-only** footprints: parts with no symbol
  and no nets, which the layout names by library footprint as well as
  position because the circuit does not know them.
* `policy_clearance.eco` — applied last: every run the human routing drew
  closer than the repository netclass clearances allow
  (`../libraries/netclasses.py`), removed and redrawn between the same
  terminals, plus four AGND stitch vias. After it, KiCad DRC is clean under
  the policy as well as under the as-built rules.

`generators/routed_pcb.py` combines these with the SKiDL circuit into
`../stage1_iv.kicad_pcb` on every `python3 main.py`. Formats, workflow and
verification: `../docs/physical_design.md`; the ECOs' electrical reasoning:
`../docs/bipolar_measurement.md` §5, `../docs/audit_corrections.md` and
`../docs/final_corrections.md`.
