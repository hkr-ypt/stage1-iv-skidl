# Silkscreen and package selection

> **Later changes.** This note is kept as written. Two rows of its
> capacitor table need a correction that the final design audit
> ([`final_audit.md`](final_audit.md) §3.8) made in the parts list: **C17
> and C18** (10 µF / 35 V on the 3528 footprint, "already decided") cannot
> be bought as an MnO2 tantalum, because none of that rating is made in
> that case. They are a **polymer** tantalum by order code,
> TCJB106M035R0200E; value, rating and footprint are as below. U5, which
> they decouple, is now an OPA547T, and C49 is fitted.

Prompt 4: make the board assembly-legible, and fix the packages that were
inherited rather than chosen.

## Capacitor packages

`C_POL()` used to default to `Capacitor_THT:CP_Radial_D8.0mm_P3.50mm`. C5–C8
took that default, so the four physically largest parts on the board got an
8 mm through-hole can **by inheritance rather than by decision**.

**The footprint is now a required argument.** There is no sensible default for
a polarized capacitor — the right package depends on capacitance, working
voltage and derating, all of which differ per instance — so `C_POL(footprint)`
forces the question to be answered every time. No polarized capacitor in the
design can inherit a package again.

| Ref | Value | Rail | Working V | Rated/working | Old footprint | New footprint | Why |
|---|---|---|---:|---:|---|---|---|
| C1 | 10 µF/25 V | +5V_RAW | 5 V | 5.0× | Tantalum 3528 *(explicit)* | unchanged | already decided; ample derating |
| C2 | 10 µF/16 V | +3V3D | 3.3 V | 4.8× | Tantalum 3528 *(explicit)* | unchanged | already decided |
| **C5** | 100 µF/25 V | +15VA | 15 V | **1.67×** (60 % of rating) | **THT radial 8 mm *(default)*** | `Capacitor_SMD:CP_Elec_8x10.5` | SMD aluminium electrolytic, same ratings. 60 % of rating is inside the 80 % ceiling normally applied to aluminium electrolytics. |
| **C6** | 100 µF/25 V | −15VA | 15 V | **1.67×** | **THT radial 8 mm *(default)*** | `Capacitor_SMD:CP_Elec_8x10.5` | as C5 |
| **C7** | 100 µF/16 V | +5VA | 5 V | **3.2×** (31 %) | **THT radial 8 mm *(default)*** | `Capacitor_SMD:CP_Elec_6.3x7.7` | 16 V on a 5 V rail allows a smaller can |
| **C8** | 100 µF/10 V | +3V3D | 3.3 V | **3.0×** (33 %) | **THT radial 8 mm *(default)*** | `Capacitor_SMD:CP_Elec_6.3x5.4` | 10 V on 3.3 V; smallest of the four |
| C17 | 10 µF/35 V | ±15 V (OPA548) | 15 V | 2.3× | Tantalum 3528 *(explicit)* | unchanged | already decided |
| C18 | 10 µF/35 V | ±15 V (OPA548) | 15 V | 2.3× | Tantalum 3528 *(explicit)* | unchanged | already decided |
| C29 | 10 µF/16 V | +5VA | 5 V | 3.2× | Tantalum 3528 *(explicit)* | unchanged | already decided |

**No electrical rating was changed to get a smaller package.** Every value and
voltage rating is exactly what it was; only the package differs. In particular
nothing on a 15 V rail was moved to a 10 V part.

Every footprint above was resolved through `tools/resolve_kicad_libs.py` and is
vendored by `tools/vendor_footprints.py` — none is asserted from memory. The
structural guard caught the change immediately: the first build after editing
the source failed with *"footprint 'Capacitor_SMD:CP_Elec_8x10.5' does not
exist in libraries/footprints/Capacitor_SMD.pretty"* until the new footprints
were vendored.

### Ceramic ratings on the ±15 V rails (final hardware audit)

The ceramic decouplers on the ±15 V rails had no voltage rating or dielectric
in their values. They are now specified explicitly, and the footprints are
unchanged:

| Ref | Rail | Value now |
|---|---|---|
| C11, C12 (U3) | ±15VA | 100 nF 50 V X7R |
| C15, C16 (U5) | ±15VA | 100 nF 50 V X7R |
| C20, C21 (U6) | ±15VA | 100 nF 50 V X7R |
| C22, C23 (U7) | ±15VA | 100 nF 50 V X7R |

The audit asked for at least 25 V. 50 V X7R was chosen because X7R MLCCs lose
much of their capacitance under DC bias near their rated voltage, and a 15 V
rail is 60 % of a 25 V part. Other value changes from the same audit: C9 is
4.7 µF 10 V X7R (REF5025 stability range), C43 is 4.7 µF 10 V X5R (STM32F411
single VCAP), C48 is 470 nF 16 V X7R and C49 (DNP) is 10 nF 50 V X7R. See
`docs/audit_corrections.md`.

## Reference designators: all on F.Fab

**Every one of the 114 designators is on F.Fab. None is on F.SilkS.**

That is not a retreat from legibility, it is what the evidence supports.
KiCad 10's report is unambiguous: of the 161 silkscreen violations,
**every single one involved a Reference field**, and not one was footprint
silk against footprint silk. Moving designators off F.SilkS therefore removes
the entire violation class *by construction* — no geometry, no model, no
estimate, and no dependence on a checker that has now been wrong three times.

`PROMOTE_DESIGNATORS_TO_SILK` in `generators/pcb.py` is the switch. Turned on,
the rule-based promoter keeps about 80 designators on the silkscreen, at four
fixed positions (N/S/E/W of the courtyard at a constant 1.0 mm offset — no
micro-adjustment, no widening search). It is **off**, because turning it on
would stake the result on the same geometric model that produced the last three
wrong answers. Turn it on after a KiCad 10 run has validated the model against
a board this generator produced.

Nothing is hidden and nothing is deleted. Every designator is in the file, on
the assembly-drawing layer, and every part remains identifiable when the fab
drawing is plotted. The bench-legibility cost is real and is stated here rather
than hidden: on the assembled board you will read designators from the assembly
drawing, not from the silkscreen, until promotion is validated.

Board labels — the title, revision, ground-split markers and the J1/DUT terminal
names — are placed in the board margin and require 2.0 mm clearance from
everything. 4 of the 13 cleared that bar and are on F.SilkS; the other 9 went to
F.Fab rather than being squeezed in.

## Reference-designator placement (the promoter, currently disabled)

Generated by `generators/pcb.py::place_references`, not hand-placed. Regenerate
and diff: the board is byte-identical across runs.

The algorithm:

1. **Candidates** are generated on a ring around each part's own courtyard —
   above, below, right, left, then the four diagonals — at `SILK_TEXT_GAP`
   (0.3 mm) from the courtyard edge. The ring widens in 0.5 mm steps out to
   3 mm, so a designator that will not fit tight against its part can still sit
   near it.
2. **A candidate is accepted** when its text box, including clear space,
   overlaps none of: any pad (`silk_over_copper`), any courtyard (text under a
   part body is unreadable once assembled), any footprint's own silkscreen
   (`silk_overlap`), or any designator already placed.
3. **Order**: largest parts first. They have the most neighbours competing for
   space; a small part's designator fits more easily into what is left.
4. **Orientation**: every designator is horizontal. One convention, applied
   uniformly — a board read at the bench should not need turning.
5. **When nothing fits**: the designator moves to **F.Fab**, the assembly
   drawing layer, at the part's centre. It is never hidden and never deleted.

**On this board, nothing fell back.** All 114 designators (110 parts + 4
mounting holes) are on F.SilkS. 0 hidden, 0 deleted, 0 on F.Fab.

Text is 1.0 mm high with 0.15 mm stroke — the stroke equals
`docs/FAB_CONSTRAINTS.md` **F9** (minimum silkscreen line width) and the height
clears KiCad's `min_text_height` of 0.8 mm. Nothing was shrunk below the fab
minimum to make room.

`PART_GUTTER` grew from 1.2 mm to 2.0 mm to leave room for designators between
neighbouring courtyards. The board grew from 129.05 × 108.40 mm to
132.22 × 114.30 mm — the honest cost of legibility.

## Board-level silkscreen

13 board-level texts, all in the margin between the outline and the parts,
which is the one region guaranteed clear of copper and part silk:

* **Identity** — board name, `rev A`, and a note that the board is unrouted.
* **Ground split** — dashed ticks on F.SilkS at the AGND/DGND boundary in the
  top and bottom margins, with `AGND <` and `> DGND` labels. Ticks rather than
  a full line: the split lane holds R31 and U8, and silk across them would be
  both unreadable and a `silk_overlap` violation.
* **J1, the bench supply input** — **correction (final hardware audit,
  F1).** This bullet used to say that all four J1 pins were labelled
  individually and again in a legend, "on this board both did". That was not
  true on either board. The `--autoplace` bootstrap board's hand-written table
  read `+15V IN`, `GND IN`, `-15V IN`, `+5V IN` for pins 1–4, which disagrees
  with the circuit (pin 2 is −15 V, pin 3 is +5 V, pin 4 is GND), and the
  routed board of record had no J1 labels at all. Now:
  - `generators/pcb.py` reads the bootstrap labels and legend from the nets on
    J1's pads (`1 +15V | 2 -15V | 3 +5V | 4 GND`) and stops if a net has no
    label;
  - the board of record (`layout/audit_corrections.eco`) carries a
    `BENCH SUPPLY` heading and `+15V`, `-15V`, `+5V`, `GND` beside pins 1–4
    on F.SilkS, and J1's value is `BENCH SUPPLY 1:+15V 2:-15V 3:+5V 4:GND`.
  Putting +15 V on the −15 V terminal destroys the analog front end, and the
  labels are the only guard: there is no reverse-polarity protection.
* **DUT terminals** — `FORCE+`, `SENSE+`, `SENSE-`, `FORCE-` beside J4–J7 on
  the bootstrap board. The board of record now carries the same four labels
  on F.SilkS (audit F1); it had none before.

## Polarity and pin-1 markers

These come from the KiCad footprints themselves and were **verified present**
rather than duplicated: `CP_Elec_*` and `CP_EIA-*` carry a polarity bar,
diode footprints carry a cathode band, and the SOIC/SSOP/VSSOP/LQFP footprints
carry a pin-1 dot or chamfer. Adding generated markers on top would have
produced a second, possibly contradictory, indicator.

## Courtyards

`validation/drc_check.py::check_courtyards_defined` is new and **enabled**: it
requires every footprint to define a courtyard, and returns **0** problems.
That check should never be in KiCad's ignore list — a footprint without a
courtyard is invisible to the overlap check, so a board can look clean while
parts sit on top of each other.

## Verification, and its limit

`validation/drc_check.py::check_silkscreen` is new and passes: no silk text
overlaps a pad, and no silk text overlaps another part's silkscreen.

**It is not KiCad's silk check and does not claim to be.** It is a conservative
bounding-box test over the emitted file, written because the KiCad in this
build environment (7.x) cannot open a KiCad 9/10 board. KiCad's own
`silk_overlap` and `silk_over_copper` remain the authority. Please re-run DRC.

## The board outline

The `invalid_outline` error survived Prompt 3's fix and had to be worked around
by hand. The outline is now a single `gr_rect` rather than four chained
`gr_line` segments — no endpoints to match, no chaining epsilon to fall foul
of, and it is what KiCad writes for a rectangular board.

**This is a hypothesis, not a verified fix.** The previous explanation — KiCad 8
renumbering `Edge.Cuts` from 44 to 25 — was correct as a fact but evidently not
the whole cause. Please report whether `invalid_outline` is still present.
