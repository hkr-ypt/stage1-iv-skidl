# Generated KiCad Schematic

Running `python3 main.py` (or `python3 main.py --schematic-only`) emits a
**hierarchical, self-contained KiCad schematic** directly from the same
in-memory SKiDL `Circuit` object that produces `stage1_iv.net` — so the
schematic and the netlist are consistent **by construction**, never by
re-derivation. The emitter is `generators/schematic.py`
(`generate_schematic(circuit, out_dir=".") -> list[Path]`).

`skidl.generate_schematic()` was evaluated and rejected as the mechanism: it
needs installed KiCad symbol libraries to obtain symbol graphics and does not
produce hierarchical KiCad 7 `.kicad_sch` output in this library-less
environment.

## Files

| File | Page | Content |
|------|------|---------|
| `stage1_iv.kicad_sch` | 1 | Top sheet: one `(sheet)` per block with sheet pins |
| `power.kicad_sch` | 2 | Sheet 1 — Power Regulation & Reference |
| `mcu.kicad_sch` | 3 | Sheet 2 — MCU & Digital I/O |
| `dac_sweep.kicad_sch` | 4 | Sheet 3 — DAC & Sweep Level-Shift |
| `output_buffer.kicad_sch` | 5 | Sheet 4 — Power Buffer |
| `current_sense.kicad_sch` | 6 | Sheet 5 — Current Sensing |
| `voltage_sense.kicad_sch` | 7 | Sheet 6 — Voltage Sensing (Kelvin) |
| `adc.kicad_sch` | 8 | Sheet 7 — ADC |
| `protection.kicad_sch` | 9 | Sheet 8 — Protection & DUT Connector |

## Schema version

The files are KiCad 7 s-expression schematics — `(version 20230121)`,
`(generator stage1_iv_skidl)`. KiCad 7, 8 and 9 open them directly (newer
versions migrate the format on load). `kicad-cli` was not present in the
generation environment, so the stable KiCad 7 schema is targeted; the
generator itself never requires KiCad.

## Opening in KiCad

Open `stage1_iv.kicad_sch` in Eeschema (*File → Open…*; standalone mode is
fine). **No symbol libraries are needed**: every symbol used on a sheet is
embedded in that sheet's `(lib_symbols)` block. Child sheets are referenced
by relative filename, so keep the nine files in one directory.

## Symbols

Because the environment has no KiCad libraries, every part is drawn as a
synthesized generic rectangle:

- The embedded symbol's **library ID is the part's intended official
  `KiCad_Lib` identifier** (e.g. `Analog_DAC:DAC8552`), documenting on the
  sheet which real symbol to substitute at layout time (fallbacks, unused in
  this design: MPN, then reference prefix).
- One KiCad pin per SKiDL pin — **same pin name, same pin number** — first
  half down the left side, second half down the right, 2.54 mm pitch.
- Properties on every instance: `Reference`, `Value` (visible),
  `Footprint`, `Datasheet`, `MPN`, `KiCad_Lib`, `Sheet` (owning block) and
  `Pins_Verified`. Virtual `power:PWR_FLAG` symbols are emitted with KiCad's
  `(power)` attribute and a `#`-prefixed reference (`#FLG1`..`#FLG9`), matching
  the upstream symbol; KiCad therefore keeps them out of the netlist export and
  off the board, while ERC still counts their `power_out` pin as a driver.
  The emitter hides this property only when its value is the
  literal `STD`; since every part now carries `RESOLVED` or
  `VERIFY-DATASHEET`, the flag is drawn **visible on every symbol**, stating on
  the sheet itself where that part's pin numbers came from. No part is
  flagged `VERIFY-DATASHEET` any more: U5 (OPA548T symbol, OPA547T fitted),
  the last one, is cross-checked against TI SBOS070D and SBOS056F
  (`RESOLVED`). If a future part needs the
  flag, it will show on the sheet the same way.
- **DNP parts** (R14, R38, C49) are emitted with `(in_bom no)` and
  `(dnp yes)`, so KiCad greys them out and leaves them out of BOM exports.
  Their footprints carry `exclude_from_bom` and `dnp` on the board.

## Connectivity model (label classes)

There is deliberately **no point-to-point routing**. Every connected pin gets
a short 5.08 mm wire stub ending in a label whose text is **exactly the SKiDL
net name** from `stage1_iv.net`; KiCad's connectivity engine joins same-named
labels. Pins tied to SKiDL's no-connect net carry a KiCad `(no_connect)`
marker instead.

| Class | Rule | KiCad construct |
|-------|------|-----------------|
| `GLOBAL_POWER` | `drive == POWER` rails in `blocks/nets.py` plus the `NET_MANIFEST["power"]` group (`+15VA`, `-15VA`, `+5VA`, `+5V_RAW`, `+3V3D`, `VDDA`, `VBUS`, `AGND`, `DGND`, `VREF_2V5`) | `(global_label)` on every sheet where used |
| `HIER_SIGNAL` | Any other net whose pins span ≥ 2 blocks (`SCK1`, `MOSI1`, `CS_DAC`, `SCK2`, `MOSI2`, `MISO2`, `CS_ADC`, `DRDY`, `RESET_ADC`, `V_SWEEP`, `VO_SENSE`, `FORCE-`, `SENSE+`, `SENSE-`, `I_SENSE_OUT`, `V_SENSE_OUT`) | `(hierarchical_label)` in the child sheet + a name-matched `(pin)` on that block's `(sheet)` symbol; on the top sheet each sheet pin is stubbed to a local `(label)`, joining same-named pins across blocks |
| `LOCAL` | All pins within one block (includes `blocks/nets.py` nets that happen to live on one sheet, e.g. `NRST`, `V_DAC`, `FORCE+`, `F1_MID`) | local `(label)` inside the owning sheet |

## Placement & determinism

Placement is a reproducible row-flow: parts in circuit build order, one
rectangular cell per part (symbol body + pins + stubs + label allowances),
cells flowed left→right and wrapped into rows, everything on the 1.27 mm
grid (geometry is computed in exact integer grid units). Per sheet, the
smallest landscape paper of A4/A3/A2/A1/A0 that fits is chosen. All UUIDs
are UUIDv5 in a fixed namespace keyed by stable object identity, so repeated
runs produce **byte-identical** files.

## Validation

`python3 validation/schematic_check.py` re-derives connectivity from the
emitted files alone (embedded symbol pin offsets + wires + labels, via
`simp_sexp`) and compares the `(reference, pin, net)` set against
`stage1_iv.net` (via `kinparse`); it also checks label classes, sheet-pin ↔
hierarchical-label integrity, UUID validity/uniqueness, no-connect markers,
floating pins and duplicate references. It exits non-zero on any mismatch.
