# Stage 1 I–V Characterization System — SKiDL Project

A complete, **executable** [SKiDL](https://github.com/devbisme/skidl) source
design for a Stage 1 I–V characterization instrument: a precision bipolar
**±10 V** voltage-source with 4-wire **Kelvin** sensing, **bipolar voltage and
current measurement over the whole sweep** (±11.8 V, ±97.8 mA guaranteed, for a
source that can reach ±10.04 V and ±91.3 mA), hardware current limiting
(~49 mA), defense-in-depth protection, and a **shared 2.500 V reference** for
ratiometric measurement.

This project is the source of truth for the electrical design **and** the
board. Running it produces a **KiCad netlist** (`stage1_iv.net`), a
**hierarchical KiCad schematic** (`stage1_iv.kicad_sch` + one child
`.kicad_sch` per block, generated from the same in-memory circuit — see
[`SCHEMATIC.md`](SCHEMATIC.md)), the **placed and fully routed bipolar board**
(`stage1_iv.kicad_pcb`), plus a generated component manifest.

> **The board is generated from source and fully routed.** Its placement and
> copper are the human-routed board's (`unipolar-routed/`), captured into
> [`layout/stage1_iv.layout`](layout/stage1_iv.layout); the bipolar
> measurement change is applied to it by
> [`layout/bipolar.eco`](layout/bipolar.eco), the final hardware audit
> corrections by [`layout/audit_corrections.eco`](layout/audit_corrections.eco),
> the remaining corrections (FORCE+ photorelay, dead-rail clamps, FORCE−
> PTC, R10, F1 placement, mounting holes) by
> [`layout/final_corrections.eco`](layout/final_corrections.eco), and the
> re-routing to the repository clearances by
> [`layout/policy_clearance.eco`](layout/policy_clearance.eco): lists of
> add/remove operations, each with its reason. See
> [`docs/physical_design.md`](docs/physical_design.md),
> [`docs/audit_corrections.md`](docs/audit_corrections.md) and
> [`docs/final_corrections.md`](docs/final_corrections.md).
>
> **KiCad 10 DRC** (zones refilled, schematic parity on): 0 unconnected, 0
> parity problems, and **0 errors** both under the rules the board was routed
> to (its 0.20 mm netclass plus the scoped intra-footprint rules, every check
> on) and under the repository's stricter netclass policy (0.25–0.30 mm
> around sense and ±15 V nets). Two silkscreen-to-edge warnings at J3 are
> also on the original.
>
> **`unipolar-routed/` is the original, unipolar, human-routed reference** and is never
> modified. `python3 tools/reproduce_reference.py` proves the layout source
> regenerates it (identical copper, placement and connectivity; identical
> KiCad DRC and zone fill).

---

## Where the pin numbers come from

Every component is a **SKiDL-native part** (`tool=SKIDL`) declared in
[`libraries/custom_symbols.py`](libraries/custom_symbols.py), so the design runs
ERC, builds the netlist and validates connectivity as plain Python. But the
pins are **not written there**: each factory names an official KiCad `lib_id`,
and the pin numbers, pin names and electrical types are *read out of the
installed KiCad symbol library at build time* by
[`tools/resolve_kicad_libs.py`](tools/resolve_kicad_libs.py).

That is the whole point. A pin number in this repository cannot be stale,
invented, or remembered — it is whatever the symbol file on disk says, and
`tools/audit_identifiers.py` prints the file and line every value came from. A
`lib_id` that does not exist upstream stops the build instead of quietly
emitting a netlist that maps nets onto pads that are not there.

Each part still records, as component fields carried into the netlist:

- `KiCad_Lib` — the official KiCad library identifier the pins were read from
- `MPN` — manufacturer part number
- `Symbol_Source` — the symbol file and line the pins came from
- `Pins_Verified` — provenance of the pin **numbers** (see below)

**Connectivity is expressed exclusively by pin function name**
(`dac['VREF'] += VREF_2V5`), never by raw pin number. Where the official symbol
spells a pin differently from the design (`V_{REF}` vs `VREF`), an alias layer
keeps both spellings addressing the same pin; every alias is documented in
`ALIAS_DOCUMENTATION` and in
[`docs/identifier_resolution.md`](docs/identifier_resolution.md).

### Pin-number provenance

| Flag | Meaning | Parts |
|------|---------|-------|
| `RESOLVED` | Pin numbers read from an official KiCad symbol library, or from a project symbol authored from (or cross-checked pin by pin against) the manufacturer data sheet. Fabrication-safe. | every part |
| `VERIFY-DATASHEET` | Pin numbers read from a real, version-pinned library file that is **not** the manufacturer data sheet. Confirm before fabrication. | none (U5 was the last; resolved against TI SBOS070D) |

`PLACEHOLDER-REMAP` no longer exists anywhere in this project;
[`validation/footprint_check.py`](validation/footprint_check.py) hard-fails if
it reappears.

Two parts have no symbol in the official KiCad libraries and live in the
project-local library [`libraries/stage1_iv.kicad_sym`](libraries/stage1_iv.kicad_sym),
generated by `tools/author_project_symbols.py` from committed provenance
excerpts under `libraries/provenance/`:

- **ADS1256** — authored from TI **SBAS288K** (rev. K, Sept 2013), "Terminal
  Functions" table, page 5. `RESOLVED`.
- **OPA548T** — its symbol comes from a pinned commit of a published
  third-party KiCad library, and every pin is cross-checked against the TI
  **SBOS070D** "Pin Functions" table, committed verbatim as
  `libraries/provenance/opa548_sbos070d_pin_functions.txt`. The build stops on
  any disagreement. `RESOLVED`. Its footprint is a project copy of KiCad's
  staggered TO-220-7 with 1.2 mm drills (`stage1_iv.pretty`, generated by
  `tools/vendor_footprints.py`).

---

## Project structure

```
stage1-iv-skidl/
├── main.py                     # instantiate all blocks, validate, emit outputs
├── requirements.txt
├── manifest.yaml               # machine-readable design summary + unresolved items
├── README.md
├── SCHEMATIC.md                # how the .kicad_sch output is generated
│
├── blocks/
│   ├── nets.py                 # global cross-sheet nets (the net manifest)
│   ├── power.py                # Sheet 1 — power regulation & reference
│   ├── mcu.py                  # Sheet 2 — STM32F411 + USB/SWD/clock
│   ├── dac_sweep.py            # Sheet 3 — DAC8552 + OPA2277 level-shift
│   ├── output_buffer.py        # Sheet 4 — OPA548 power buffer
│   ├── current_sense.py        # Sheet 5 — shunt + INA128 current sense
│   ├── voltage_sense.py        # Sheet 6 — INA128 Kelvin voltage sense
│   ├── adc.py                  # Sheet 7 — ADS1256 + RC filters
│   └── protection.py           # Sheet 8 — fuse/TVS + DUT connector
│
├── generators/
│   ├── schematic.py            # hierarchical .kicad_sch emitter (live circuit)
│   ├── routed_pcb.py           # THE board: circuit + layout/ + ECOs -> .kicad_pcb
│   ├── layout.py               # layout source + ECO model (parse, apply, write)
│   ├── footprint_xform.py      # place a library footprint as KiCad stores it
│   └── pcb.py                  # footprint prep; bootstrap auto-placer (--autoplace)
│
├── layout/
│   ├── stage1_iv.layout        # placement + copper, captured from unipolar-routed/
│   ├── bipolar.eco             # the bipolar change: reasoned add/remove ops
│   ├── audit_corrections.eco   # the final hardware audit corrections (F1–F4, V1, …)
│   ├── final_corrections.eco   # U12/D7/R39, D8/D9, F2, R10, F1 move, H1–H3
│   └── policy_clearance.eco    # re-routing to the repository netclass clearances
│
├── tools/
│   ├── resolve_kicad_libs.py   # THE resolver: reads installed KiCad libraries
│   ├── install_kicad_libs.sh   # install a pinned upstream library revision
│   ├── audit_identifiers.py    # per-part identifier / pad audit -> markdown
│   ├── author_project_symbols.py # generate libraries/stage1_iv.kicad_sym
│   ├── kicad_roundtrip.py      # verify the schematic through kicad-cli
│   ├── check_kicad_loads.py    # KiCad itself reads the library/schematic/board
│   ├── gen_project_config.py   # lib tables + board setup from netclasses
│   ├── vendor_footprints.py    # pin the used footprints into the repository
│   ├── measure_pad_gaps.py     # measure intrinsic pad gaps from the footprints
│   ├── compare_routed_board.py # unipolar-routed/ board vs the SKiDL design (ECO list)
│   ├── capture_layout.py       # routed board -> layout source (--check)
│   ├── reproduce_reference.py  # prove the layout regenerates unipolar-routed/
│   ├── compare_boards.py       # semantic board-vs-board comparison
│   ├── board_geometry.py       # real-copper geometry (shapely)
│   ├── eco_route.py            # design an ECO track against real copper
│   └── test_guard_fails.sh     # fault-injection: prove the guard fails
│
├── validation/
│   ├── erc.py                  # SKiDL ERC + structural checks
│   ├── assertions.py           # custom connectivity/topology assertions
│   ├── footprint_check.py      # identifiers/pads guard (aborts the build)
│   ├── routed_board_check.py   # board of record: source, parity, KiCad DRC
│   ├── drc_check.py            # board checks (shared; auto-placed board)
│   ├── measurement_range.py    # every reachable V/I point fits the ADC, signed
│   └── schematic_check.py      # schematic <-> netlist consistency validator
│
├── docs/
│   ├── changes_since_bipolar.md      # every change since the bipolar design (bfbb140)
│   ├── final_corrections.md          # remaining hardware corrections: analysis, validation
│   ├── audit_corrections.md          # final hardware audit: what changed, validation record
│   ├── firmware_handoff.md           # what the hardware requires of the firmware
│   ├── bringup_checklist.md          # assembly + first-power procedure
│   ├── bipolar_measurement.md        # bipolar V/I design + routed-board ECO
│   ├── physical_design.md            # layout source, ECO, reproduction, DRC
│   ├── identifier_resolution.md      # what was wrong, what it became, sources
│   ├── FAB_CONSTRAINTS.md            # assumed fabricator capability (F1..F12)
│   ├── pcb_generation.md             # netclasses, rules, the auto-placer
│   ├── audit_identifiers_before.md   # pre-fix baseline
│   └── audit_identifiers_after.md    # identifier audit of the current design (generated)
│
├── unipolar-routed/            # ORIGINAL unipolar human-routed KiCad project:
│                               # the reference, never modified (its README.md)
├── kicad_libs.json             # explicit KiCad library locations (optional)
├── sym-lib-table               # registers libraries/stage1_iv.kicad_sym
│
└── libraries/
    ├── custom_symbols.py       # part factories; pins READ from the libraries
    ├── netclasses.py           # netclasses; every value cites FAB_CONSTRAINTS
    ├── stage1_iv.kicad_sym     # generated: parts with no upstream symbol
    └── provenance/             # verbatim sources every project pin came from
```

Each block exposes a `create_*_block(nets)` function that instantiates its parts
and connects them to the shared global nets. `main.py` builds all eight sheets.

---

## Install & run

```bash
# 1. SKiDL and its import-time dependencies. The optional PCB-placement extras
#    (kinet2pcb, hierplace) are not needed and may fail to build, so skip them.
pip install --no-deps skidl==2.2.3
pip install graphviz future simp_sexp rich ply pyyaml
#    kinparse's setup.py predates modern setuptools:
SETUPTOOLS_USE_DISTUTILS=stdlib pip install --no-build-isolation kinparse

# 2. Check what KiCad libraries this machine has, if any. ALWAYS START HERE:
#    a partially-copied symbols directory looks like a working install until
#    ERC reports a hundred "library not found" errors.
python3 tools/kicad_env.py --check --diagnose
#      exit 0 = complete, 2 = none found, 3 = INCOMPLETE (and it names what is
#      missing). KiCad is OPTIONAL — see "Building without KiCad" below.

# 3. Optional: install exactly the pinned library revision this design was
#    resolved against (see docs/LIBRARY_DEPENDENCIES.md).
tools/install_kicad_libs.sh "$HOME/.local/share/kicad" 9.0.9.1
export KICAD9_SYMBOL_DIR="$HOME/.local/share/kicad/symbols"
export KICAD9_FOOTPRINT_DIR="$HOME/.local/share/kicad/footprints"
#    or write your paths into kicad_paths.local.json (gitignored).

# Build: validate + emit netlist + schematic + manifests
python3 main.py

# Just emit the netlist
python3 main.py --netlist-only

# Just emit the hierarchical schematic
python3 main.py --schematic-only

# Verify schematic <-> netlist consistency (exits non-zero on mismatch)
python3 validation/schematic_check.py

# Structural guard: identifiers resolve, pins map to pads, no undeclared
# floating pads, no placeholder provenance (also run from main.py)
python3 validation/footprint_check.py

# ERC gate standalone: hard-fails on any error, any un-allow-listed warning,
# or any PWR_FLAG invariant violation
python3 validation/erc.py

# KiCad's power-driver ERC rules, evaluated on the emitted .kicad_sch files
python3 tools/kicad_erc_power.py

# Audit every part against the installed libraries
python3 tools/audit_identifiers.py > docs/audit_identifiers_after.md

# Verify the emitted schematic through KiCad's own parser
python3 tools/kicad_roundtrip.py

# Prove the guard fails when a bad identifier is reintroduced
tools/test_guard_fails.sh

# Regenerated files are current (project symbol library, lib tables, deps doc)
python3 tools/author_project_symbols.py --check
python3 tools/gen_project_config.py --check

# No committed file contains a machine-specific absolute path
python3 tools/check_no_abs_paths.py
```

### Building without KiCad

`python3 main.py` runs with no KiCad installed: pin data falls back to the
committed project symbol library `libraries/stage1_iv.kicad_sym`. The build then
reports **SKIPPED** for every KiCad-conformance check and never anything
resembling a pass:

```
  KiCad libraries ....... NOT FOUND
  Symbol resolution ..... SKIPPED (no KiCad libraries to check against)
  Footprint resolution .. SKIPPED (no KiCad libraries to check against)
  SKiDL ERC ............. 0 errors, 0 warnings
  KiCad ERC ............. NOT RUN
```

### Opening in KiCad

`stage1_iv.kicad_pro`, `sym-lib-table` and `fp-lib-table` are committed and
contain **no absolute paths** — only `${KIPRJMOD}` and KiCad's own
`${KICAD9_SYMBOL_DIR}` / `${KICAD9_FOOTPRINT_DIR}`. Open `stage1_iv.kicad_pro`
and every symbol resolves from `libraries/stage1_iv.kicad_sym` inside the
repository, so the schematic opens correctly even on a machine whose stock
libraries are missing or incomplete.

**Update the PCB with "Update PCB from Schematic", never by importing
`stage1_iv.net`** — the schematic is the single source of truth for KiCad and
the netlist is intentionally flat. See
[`docs/library_strategy.md`](docs/library_strategy.md) §4 for the decision and
its consequences for an existing board.

Expected result: **0 SKiDL ERC errors, 0 SKiDL ERC warnings, 15/15 assertions
PASS, measurement-range check PASS, 0 unresolved identifiers, 0 unaccounted pads**, and:

> **SKiDL ERC is not KiCad ERC.** They are different checks with different
> coverage — see [Validation](#validation) below for exactly what each one does
> and does not cover. A clean SKiDL ERC run is evidence about the SKiDL
> circuit; it says nothing about the KiCad ERC dialog.


- `stage1_iv.net` — KiCad netlist
- `stage1_iv.kicad_sch` + 8 child `.kicad_sch` — hierarchical KiCad schematic
  (self-contained; see below and [`SCHEMATIC.md`](SCHEMATIC.md))
- `stage1_iv.kicad_pcb` — the bipolar board, placed and **fully routed**
  (KiCad 9/10 schema, **opens in KiCad 10**; zones are stored unfilled — press
  B, or plot with `--check-zones`). Generated from the circuit,
  `layout/stage1_iv.layout` and the four ECOs in `layout/`; checked by
  `validation/routed_board_check.py` (see
  [`docs/physical_design.md`](docs/physical_design.md)). Opening it and
  pressing *Update PCB from Schematic* changes **nothing** — that is a checked
  property, not a hope (`validation/update_pcb_noop.py`)
- `component_manifest.md` — generated component table

> SKiDL prints benign `KICADn_SYMBOL_DIR` / `fp-lib-table` warnings on import
> and one internal no-connect "Missing tag" notice during netlist generation.
> These come from SKiDL's own library loader, which this project does not use
> (it reads the libraries through `tools/resolve_kicad_libs.py`), and do not
> affect the design.

---

## Generation stages (as required by the spec)

1. **Manifest & library identification** — every component, its intended KiCad
   library, and every unresolved/placeholder symbol are enumerated in
   `manifest.yaml` and regenerated into `component_manifest.md`.
2. **Individual SKiDL blocks** — `blocks/*.py`, one per hierarchical sheet.
3. **Integration** — `main.py` wires blocks through `blocks/nets.py`.
4. **ERC + connectivity validation** — `validation/erc.py` and
   `validation/assertions.py`.
5. **Final project files** — this repository.

## Validation

**ERC (`validation/erc.py`)** — and the first thing to know is that **SKiDL ERC
and KiCad ERC are different checks with different coverage. A pass here is not
evidence about KiCad ERC.**

SKiDL's ERC covers: pins connected to nothing; pins on the no-connect net that
also carry a real net; nets with fewer than two pins; pin-type conflicts; and
insufficient drive — a net whose maximum pin drive is below what some pin on it
requires. That last one is SKiDL's analogue of KiCad's `power_pin_not_driven`,
and the `PWR_FLAG` parts on the eight externally-sourced rails satisfy both.

SKiDL's ERC does **not** cover, and KiCad's does: symbol and library resolution
(`lib_symbol_issues`, `lib_symbol_mismatch`, "symbol not found in library");
footprint link resolution (`footprint_link_issues`); and everything geometric —
wire/label/pin overlap, dangling wire ends, bus definition errors, sheet-pin
mismatch. The first two are covered instead by
[`validation/footprint_check.py`](validation/footprint_check.py) and
[`tools/audit_identifiers.py`](tools/audit_identifiers.py); the geometric rules
are not checked anywhere in this repository.

The gate is real: `run_erc` reads `skidl.erc_logger`'s counters around the
`ERC()` call, cross-checks them against the captured message texts, and
**raises** on any error, on any warning not in an explicitly justified
allow-list (currently empty), and on any violation of the `PWR_FLAG` invariant.
`main.py` prints the actual counts and exits non-zero. Current result:
**0 errors, 0 warnings**.

**Power flags.** Eight rails have no on-board driver because their energy comes
from outside the board, and each carries one `power:PWR_FLAG` instantiated at
its physical point of entry:

| Net | Physical source | Flag | Attached in |
|---|---|---|---|
| `+15VA` | bench supply, J1 pin 1 | `#FLG1` | `blocks/power.py` |
| `-15VA` | bench supply, J1 pin 2 | `#FLG2` | `blocks/power.py` |
| `+5V_RAW` | bench supply, J1 pin 3 | `#FLG3` | `blocks/power.py` |
| `DGND` | bench supply return, J1 pin 4 | `#FLG4` | `blocks/power.py` |
| `+5VA` | `+5V_RAW` through ferrite bead FB1 | `#FLG5` | `blocks/power.py` |
| `AGND` | analog return; separate net from DGND (R31 star) | `#FLG6` | `blocks/power.py` |
| `VBUS` | USB host, through J3 | `#FLG7` | `blocks/mcu.py` |
| `VDDA` | `+3V3D` through ferrite bead FB2 | `#FLG8` | `blocks/mcu.py` |

`+3V3D` and `VREF_2V5` are deliberately **not** flagged: U1.VOUT and U2.Vout
genuinely drive them, and a flag there would mask a future regulator wiring
error. `validation/erc.py::check_power_flags` enforces all of this — a missing
flag, a flag on an undeclared net, and a flag on a net that already has a real
driver are each a hard failure.

**Structural guard (`validation/footprint_check.py`)** — wired into `main.py`,
so the build **aborts** on any failure. Five hard checks:

1. every `KiCad_Lib` resolves to a real library **and** symbol
2. every footprint resolves to a real library **and** footprint
3. every declared pin number is a real pad name (a pin with no pad silently
   drops its net on the PCB)
4. every pad is either netted or an explicit no-connect listed in
   `UNCONNECTED_PAD_ALLOWLIST` with a written reason
5. no part carries `Pins_Verified == "PLACEHOLDER-REMAP"`

Current result: 135 parts (9 virtual PWR_FLAGs), 392 pads, **345 netted, 47
declared no-connect, 0 unaccounted**. `tools/test_guard_fails.sh` reintroduces real defects one
at a time (27 cases, among them the five structural ones above and, since the
audit corrections, the midpoint back on VREF_2V5, the DAC reference wired
around U11, a DC path onto DAC_ARM and R30 back to 1/4 W) and confirms every
one is caught. `python3 validation/assertions.py` runs the connectivity
assertions on their own.

**Custom assertions (`validation/assertions.py`)** — all **15 PASS**:

- ADC reference (`U8.VREFP`) is on `VREF_2V5`, and the DAC reference
  (`U4.VREF`) reaches it only through the U11 gate (never directly)
- the sweep midpoint divider is fed from DAC channel B (`VOUTB`), not from a
  fixed reference
- the DAC reference gate: U11 between `VREF_2V5` and `U4.VREF`, a pull-down on
  the DAC side, SEL on `DAC_ARM`
- `DAC_ARM` has no DC path from any rail or GPIO: it is driven only through
  the charge-pump diode from a capacitively coupled heartbeat
- the DAC's `/SYNC` (CS_DAC) has a pull-up
- FORCE+ path = `OPA548(U5) → R9 → F1(fuse) → R30 → D5(TVS) → J4`
- Kelvin sense (`SENSE±`) is independent from the force path
- ADC uses **SPI2**, DAC uses **SPI1**
- SPI1 and SPI2 are physically **separate buses**
- AGND and DGND join **only** at the `R31` star point
- every required manifest net exists and is populated
- **USB-C (J3): all four GND pads on DGND, all four VBUS pads on VBUS, both
  D+ pads tied and both D- pads tied** (the receptacle is symmetric — wiring
  one of each pair gives a port that works in one cable orientation only)
- **the measurement zero**: `U6.REF`, `U7.REF` and the ADC's `AINCOM` are all
  on `VREF_2V5` (none left on AGND)
- **the Kelvin attenuator**: each U7 input is fed by one series resistor from
  its SENSE jack and one shunt resistor to AGND, with nothing across the inputs

**Measurement range (`validation/measurement_range.py`)** — from the part
values in the live circuit: the sweep span, the largest current any passive DUT
can draw (`V_SWEEP/(R30 + R10)`, independent of the OPA548 limit), both
channels' transfer functions, and the voltage on each ADS1256 input at those
extremes with worst-case tolerances. Fails the build if any normal operating
point leaves the ADC's absolute input window (`AGND − 0.1 … AVDD + 0.1 V`) or
its PGA = 2 full scale. It also bounds the FORCE-path fault (U5 driven into a
15.5 V rail, every series part at its guaranteed minimum: 128 mA into a DUT
short, 143 mA with FORCE+ on AGND) and an external source on J7 (F2 + R10:
1.19 A), and checks R9, R30 and R10 against their ratings. Current result:
worst-case headroom 334 mV (V) and 115 mV (I); fault dissipation R30 1.96 W
of 2 W, R9 0.21 W of 0.25 W, R10 0.16 W of 3 W and 14.3 W of its 15 W
short-time rating ([`docs/final_corrections.md`](docs/final_corrections.md)
§2.7).

**Identifier audit (`tools/audit_identifiers.py`)** — per part, whether the
symbol and footprint resolve, symbol pins vs footprint pads, unaccounted pads,
and the file:line every value was read from. Before/after snapshots:
[`docs/audit_identifiers_before.md`](docs/audit_identifiers_before.md),
[`docs/audit_identifiers_after.md`](docs/audit_identifiers_after.md); the full
write-up is [`docs/identifier_resolution.md`](docs/identifier_resolution.md).

---

## Generated hierarchical schematic

`python3 main.py` also emits a **valid, human-readable KiCad schematic** from
the *same live SKiDL circuit* that produced the netlist (module
`generators/schematic.py`) — consistent with `stage1_iv.net` by construction:

- **Hierarchy** — `stage1_iv.kicad_sch` (top) contains one `(sheet)` per
  block; each block becomes its own child sheet (`power.kicad_sch`,
  `mcu.kicad_sch`, `dac_sweep.kicad_sch`, `output_buffer.kicad_sch`,
  `current_sense.kicad_sch`, `voltage_sense.kicad_sch`, `adc.kicad_sch`,
  `protection.kicad_sch`).
- **Self-contained** — every symbol is a synthesized rectangle embedded in the
  sheet's `(lib_symbols)`; the files open in KiCad 7/8/9 with **zero symbol
  libraries installed**. Embedded lib IDs reuse the resolved official
  `KiCad_Lib` identifier, and every symbol shows its `Pins_Verified` flag on
  the sheet — `RESOLVED` for every part.
- **Label-based connectivity** — each pin carries a wire stub and a label with
  the exact SKiDL net name: global labels for power rails, hierarchical
  labels + top-sheet sheet pins for nets crossing blocks, local labels
  otherwise. No rat's-nest routing.
- **Deterministic** — stable UUIDv5 identifiers and grid placement; repeated
  runs are byte-identical.
- **Verified** — `python3 validation/schematic_check.py` reconstructs
  connectivity from the emitted files and asserts the `(reference, pin, net)`
  set equals the netlist's (368 connections, 141 references, 77 nets), and
  `python3 tools/kicad_roundtrip.py` re-checks the same thing through **KiCad's
  own parser** (`kicad-cli sch export netlist`): KiCad reads the identical 359
  connections (the 9 PWR_FLAG pins excluded) partitioned into the identical
  77 nets.

Details, conventions, and all documented assumptions: [`SCHEMATIC.md`](SCHEMATIC.md)
and `manifest.yaml → schematic_assumptions`.

---

## Importing into KiCad

1. Create a KiCad project and open the schematic/PCB.
2. Import `stage1_iv.net` (Pcbnew: *File → Import → Netlist*), or use it to
   drive footprint assignment.
3. Every `KiCad_Lib` and every footprint in the netlist resolves against
   kicad-symbols/kicad-footprints **9.0.9.1**; `validation/footprint_check.py`
   asserts this on every build. Add `sym-lib-table` (in the repository root) to
   your project so `stage1_iv:ADS1256` and `stage1_iv:OPA548T` resolve.
4. **No part is flagged `VERIFY-DATASHEET`.** U5 (OPA548T), the last one, is
   now cross-checked pin by pin against TI SBOS070D on every symbol build.
   Confirm the purchased part's lead form against the staggered footprint.
   See [`docs/identifier_resolution.md`](docs/identifier_resolution.md) §4b.
   Add `fp-lib-table` too, so the project footprint `stage1_iv:TO-220-7_…_Drill1.2mm`
   resolves.

---

## Key design points (from the source spec, enforced in code)

- **Two separate SPI buses** (§C.1) — SPI1→DAC (write-only), SPI2→ADC.
- **One shared 2.500 V reference** for DAC + ADC (§C.2) — ratiometric accuracy.
  The DAC gets it through the U11 reference gate (below).
- **±10.04 V sweep** via `V_SWEEP = (R4/R3)(V_A − V_B/2)`: DAC channel A
  sweeps, channel B (held at full scale by firmware) sets the midpoint. Gain =
  20.0k/2.49k ≈ 8.03 on a ±1.250 V DAC bracket (§C.5). The −10.04 V floor is
  hardware; the +10.04 V ceiling holds only while channel B is at full scale,
  a firmware contract ([`docs/firmware_handoff.md`](docs/firmware_handoff.md)).
  R4/R6 = 20.0k
  *brackets* the ±10 V target — an exact ±10.000 V would need a non-standard
  19.92 kΩ — so firmware leaves the top 0.40% of DAC codes unused. What
  reaches the **DUT** is less at high current: R30 (100 Ω) + R10 (10 Ω) drop
  ~5.4 V at the 49 mA limit (plus up to 0.8 V in F1, F2 and U12), leaving
  ~±4.6 V there; the full ±10.04 V arrives only at low current.
- **0 V output whenever firmware is not running** (audit F2). Both DAC
  channels power up at zero scale, so the midpoint from channel B gives
  V_SWEEP ≈ 0 at power-up. The DAC keeps its codes through an MCU reset, halt
  or crash, so the DAC reference itself is gated: U11 (TMUX1101) connects
  VREF_2V5 to the DAC only while a charge pump (D6, C47, C48) is fed by a
  software-toggled heartbeat on PC5. When the heartbeat stops, U11 opens, R35
  pulls the DAC reference to 0 V, and V_SWEEP returns to ≈ 0 V within about
  50 ms, whatever codes the DAC holds. R34 holds the DAC's /SYNC high while
  the MCU's pins float.
- **OPA548 native current limit** R7 = 1.43 MΩ → I_LIMIT ≈ 49 mA (§C.4),
  uncharacterised at this setting; nothing relies on its exact value.
- **In-the-loop isolation resistor** R9 with feedback tapped on its far side
  (§C.6) — which is why the independent Kelvin sense still exists.
- **Bipolar measurement, zero at mid-scale.** Both INA128 `REF` pins and the
  ADS1256 `AINCOM` sit on `VREF_2V5`, and the ADC reads `AIN0 − AINCOM` (I)
  and `AIN2 − AINCOM` (V): the 2.5 V shift cancels in the conversion and the
  codes are signed. One PGA (2, full scale ±2.500 V) serves both channels.
  The ADC's inputs must stay inside `AGND − 0.1 … AVDD + 0.1 V`; with the zero
  on AGND, as before, every negative reading fell below that floor.
- **4-wire Kelvin** voltage sense with rail-referenced input clamps (§C.6),
  then two matched dividers to AGND (R12/R32 and R13/R33, k = 10.0k/50.2k =
  0.1992) into U7 at **G = 1** (R14 depopulated). Ranges kept distinct:
  - *physical* Kelvin voltage at J5/J6 — ±10.04 V, falling to ~±4.6 V at 49 mA;
  - *at U7* — ±2.0 V differential, far inside the INA128's range;
  - *measurable* — `V_SENSE_OUT = 2.500 V + 0.1992·V_K`: **±11.8 V
    guaranteed** (±12.55 V at nominal full scale), so no sweep point can
    over-range it.
- **Current sense** — R10 = 10 Ω shunt, U6 at G = 2.4006 (R11 = 35.7 k),
  `I_SENSE_OUT = 2.500 V + 24.0 V/A·I`: **±97.8 mA guaranteed**. The gain is
  sized to the largest current *any passive DUT can draw*,
  `10.04 V/(R30 + R10) = 91.3 mA`, not to the ~49 mA OPA548 limit, whose
  tolerance at that setting is not specified — so the current-limit plateau of
  a forward sweep stays on-scale. See
  [`docs/bipolar_measurement.md`](docs/bipolar_measurement.md).
- **Star ground** — AGND/DGND meet only at R31 (§C.7).
- **Defense in depth** — ILIM → PTC fuse → series R → photorelay → TVS
  (§C.9). R30 (100 Ω, 2 W Vishay PR02) is the passive bound: up to 143 mA and
  1.96 W in the stacked worst-case rail fault. The PTC F1 is **not** a
  backstop: the fault current lies between its 100 mA hold and 200 mA trip,
  and it derates with temperature (`blocks/protection.py`). U12 (TLP176AM)
  disconnects FORCE+ unless both ±15 V rails are up, D8/D9 clamp a dead rail
  to AGND, and F2 (PTC) limits an external source on FORCE− ahead of the
  shunt ([`docs/final_corrections.md`](docs/final_corrections.md)).

See the source specification for full derivations. Assumptions carried into this
design are listed in `manifest.yaml → assumptions`.
