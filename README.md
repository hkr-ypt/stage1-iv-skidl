# Stage 1 I–V Characterization System — SKiDL Project

A complete, **executable** [SKiDL](https://github.com/devbisme/skidl) source
design for a Stage 1 I–V characterization instrument: a precision bipolar
**±5 V** voltage-source with 4-wire **Kelvin** sensing, hardware current
limiting (~49 mA), defense-in-depth protection, and a **shared 2.500 V
reference** for ratiometric measurement.

This project is the electrical source-of-truth. Running it produces a **KiCad
netlist** (`stage1_iv.net`) that you import into KiCad for PCB layout and ERC,
a **hierarchical KiCad schematic** (`stage1_iv.kicad_sch` + one child
`.kicad_sch` per block, generated from the same in-memory circuit — see
[`SCHEMATIC.md`](SCHEMATIC.md)) — plus a generated component manifest.

---

## Why SKiDL-native symbols?

The build environment has **no KiCad symbol/footprint libraries installed**. To
keep the design fully runnable (ERC, netlist, validation) everywhere, every
component is declared as a **SKiDL-native symbol** in
[`libraries/custom_symbols.py`](libraries/custom_symbols.py). Each symbol
records, as component fields carried into the netlist:

- `KiCad_Lib` — the **intended official KiCad library identifier**
  (e.g. `Analog_DAC:DAC8552`, `MCU_ST_STM32F4:STM32F411RETx`)
- `MPN` — manufacturer part number
- `Pins_Verified` — provenance of the pin **numbers** (see below)

**Connectivity is expressed exclusively by pin function name**
(`dac['VREF'] += VREF_2V5`), never by raw pin number.

### Pin-number honesty policy

| Flag | Meaning | Parts |
|------|---------|-------|
| `STD` | Universal / jelly-bean pinout, physically standard. Fabrication-safe. | R, C, D, LED, **INA128P** (DIP-8), **OPA2277** (SOIC-8), **LD1117** (SOT-223), passives, connectors |
| `VERIFY-DATASHEET` | Pin **names** authoritative; pin **numbers** are best-known datasheet mapping — confirm before fabrication. | **REF5025**, **DAC8552**, **OPA548**, **USBLC6-2SC6**, USB-C receptacle |
| `PLACEHOLDER-REMAP` | Fine-pitch part; pin **numbers** are sequential placeholders (per spec §0.7). Pin name/net mapping is complete — pull real pad numbers from the official KiCad symbol at layout. | **STM32F411RET6** (LQFP-64), **ADS1256** (TSSOP-28) |

Nothing is silently substituted; everything flagged is surfaced again in
[`manifest.yaml`](manifest.yaml), `component_manifest.md`, and the ERC report.

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
│   └── schematic.py            # hierarchical .kicad_sch emitter (live circuit)
│
├── validation/
│   ├── erc.py                  # SKiDL ERC + structural checks
│   ├── assertions.py           # custom connectivity/topology assertions
│   └── schematic_check.py      # schematic <-> netlist consistency validator
│
└── libraries/
    └── custom_symbols.py       # SKiDL-native symbols + intended KiCad lib IDs
```

Each block exposes a `create_*_block(nets)` function that instantiates its parts
and connects them to the shared global nets. `main.py` builds all eight sheets.

---

## Install & run

```bash
# SKiDL's optional PCB-placement extras (kinet2pcb, hierplace) may fail to build
# and are NOT needed here, so install the core + import-time deps directly:
pip install skidl --no-deps
pip install graphviz future kinparse simp_sexp rich

# Build: validate + emit netlist + schematic + manifests
python3 main.py

# Just emit the netlist
python3 main.py --netlist-only

# Just emit the hierarchical schematic
python3 main.py --schematic-only

# Verify schematic <-> netlist consistency (exits non-zero on mismatch)
python3 validation/schematic_check.py
```

Expected result: **0 ERC errors, 0 ERC warnings, 8/8 assertions PASS**, and:

- `stage1_iv.net` — KiCad netlist
- `stage1_iv.kicad_sch` + 8 child `.kicad_sch` — hierarchical KiCad schematic
  (self-contained; see below and [`SCHEMATIC.md`](SCHEMATIC.md))
- `component_manifest.md` — generated component table

> In a library-less environment SKiDL prints benign `KICADn_SYMBOL_DIR` /
> `fp-lib-table` warnings on import and one internal no-connect "Missing tag"
> notice during netlist generation. These do not affect the design.

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

**ERC (`validation/erc.py`)** runs SKiDL's built-in ERC (unconnected pins,
driver conflicts, missing power flags) plus structural checks: missing
footprints, duplicate references, and a pin-number-provenance report. Result:
**0 errors, 0 warnings**.

**Custom assertions (`validation/assertions.py`)** — all **PASS**:

- DAC reference (`U4.VREF`) and ADC reference (`U8.VREFP`) share `VREF_2V5`
- FORCE+ path = `OPA548(U5) → R9 → F1(fuse) → R30 → D5(TVS) → J4`
- Kelvin sense (`SENSE±`) is independent from the force path
- ADC uses **SPI2**, DAC uses **SPI1**
- SPI1 and SPI2 are physically **separate buses**
- AGND and DGND join **only** at the `R31` star point
- every required manifest net exists and is populated

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
  libraries installed**. Embedded lib IDs reuse the intended official
  `KiCad_Lib` identifier, and `PLACEHOLDER-REMAP` / `VERIFY-DATASHEET` parts
  show their `Pins_Verified` flag visibly on the sheet.
- **Label-based connectivity** — each pin carries a wire stub and a label with
  the exact SKiDL net name: global labels for power rails, hierarchical
  labels + top-sheet sheet pins for nets crossing blocks, local labels
  otherwise. No rat's-nest routing.
- **Deterministic** — stable UUIDv5 identifiers and grid placement; repeated
  runs are byte-identical.
- **Verified** — `python3 validation/schematic_check.py` reconstructs
  connectivity from the emitted files and asserts the `(reference, pin, net)`
  set equals the netlist's (295 connections, 107 parts, 66 nets).

Details, conventions, and all documented assumptions: [`SCHEMATIC.md`](SCHEMATIC.md)
and `manifest.yaml → schematic_assumptions`.

---

## Importing into KiCad

1. Create a KiCad project and open the schematic/PCB.
2. Import `stage1_iv.net` (Pcbnew: *File → Import → Netlist*), or use it to
   drive footprint assignment.
3. For each `PLACEHOLDER-REMAP` / `VERIFY-DATASHEET` part, place the real symbol
   from the library named in its `KiCad_Lib` field and confirm the pin numbering
   matches the datasheet. The **net/function mapping already carried in the
   netlist is authoritative**; only the physical pad numbers need confirming.
4. Verify footprint identifiers against your installed KiCad version (see
   `manifest.yaml → footprints_to_confirm`).

---

## Key design points (from the source spec, enforced in code)

- **Two separate SPI buses** (§C.1) — SPI1→DAC (write-only), SPI2→ADC.
- **One shared 2.500 V reference** for DAC + ADC (§C.2) — ratiometric accuracy.
- **±5 V sweep** via `V_SWEEP = (R4/R3)(V_DAC − VREF/2)`, Gain ≈ 4.02 (§C.5);
  reach ±10 V later by changing R4/R6 (10k→20k) only.
- **OPA548 native current limit** R7 = 1.43 MΩ → I_LIMIT ≈ 49 mA (§C.4).
- **In-the-loop isolation resistor** R9 with feedback tapped on its far side
  (§C.6) — which is why the independent Kelvin sense still exists.
- **4-wire Kelvin** voltage sense with rail-referenced input clamps (§C.6).
- **Star ground** — AGND/DGND meet only at R31 (§C.7).
- **Defense in depth** — ILIM → PTC fuse → series R → TVS (§C.9).

See the source specification for full derivations. Assumptions carried into this
design are listed in `manifest.yaml → assumptions`.
