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

`generators/routed_pcb.py` combines these with the SKiDL circuit into
`../stage1_iv.kicad_pcb` on every `python3 main.py`. Formats, workflow and
verification: `../docs/physical_design.md`; the ECOs' electrical reasoning:
`../docs/bipolar_measurement.md` §5 and `../docs/audit_corrections.md`.
