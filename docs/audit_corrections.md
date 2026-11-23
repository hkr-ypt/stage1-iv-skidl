# Final hardware audit corrections — implementation record

> **Later changes.** This record is kept as written. Do not order parts from
> it: the final design audit ([`final_audit.md`](final_audit.md)) corrected
> R30's order code to **PR02000201000FA100** (the …FB100 code below names no
> real part) and changed U5 to an **OPA547T** with R7 = 348 kΩ, D7 to
> **BZT52B24** and C26/C27 to 18 pF. The output snubber of item V1 is now
> **fitted, with R38 = 100 Ω** (not the 10 Ω below, which would have cost
> the unity-gain stage its phase margin), and C17/C18 are a polymer
> tantalum, **TCJB106M035R0200E**. The current parts list is
> [`component_manifest.md`](../component_manifest.md).

This records how the corrections from the final adversarial hardware audit
were implemented, what was decided where a correction had options, how the
result was validated, and what remains. It is a record, not the design: the
design is the SKiDL source (`blocks/*.py`), with its reasoning next to the
parts, and the layout ECO `layout/audit_corrections.eco`, with a reason on
every operation.

- Base: commit `bfbb140` (the bipolar routed board).
- **Follow-up:** the items this record leaves open (R10 part number, R30/F1
  spacing, mounting holes, the 109 policy clearance errors, partial-supply
  states, FORCE− protection, the ILIM bound, the identifier snapshot) are
  dispositioned in [`final_corrections.md`](final_corrections.md). This file
  is kept as the record of the earlier round; the notes marked
  *Superseded* below point to where each changed.
- Source of truth: SKiDL → `python3 main.py` regenerates the netlist, the
  nine schematic sheets, the component manifest and the routed board. No
  generated file was edited by hand.
- Related: [`firmware_handoff.md`](firmware_handoff.md) (what firmware must
  do), [`bringup_checklist.md`](bringup_checklist.md) (what to measure on
  the first boards), [`physical_design.md`](physical_design.md) (the ECO and
  DRC), `DESIGN_DOCUMENT.md` (updated throughout).

---

## 1. Corrections checklist

| ID | Finding | What was done | Status |
|---|---|---|---|
| **F1** | J1 (±15 V / +5 V bench input) had no per-terminal marking on the routed board; docs claimed it was "labelled twice"; the bootstrap generator's legend had the wrong pinout | Board: `BENCH SUPPLY` heading and `+15V`, `-15V`, `+5V`, `GND` at pins 1–4 (J1 sits rotated 180°, so pin 1 is the right-most terminal); `FORCE+`, `FORCE-`, `SENSE+`, `SENSE-` beside J4–J7. J1 value: `BENCH SUPPLY 1:+15V 2:-15V 3:+5V 4:GND`. `generators/pcb.py` reads its J1 labels from J1's pad nets. False claims corrected in `docs/silkscreen_and_packages.md`, `DESIGN_DOCUMENT.md` | **Done** |
| **F2** | Output drove −10.04 V from power-up until firmware wrote the DAC; the DAC holds codes through MCU reset/halt/crash | (a) Sweep midpoint from DAC channel B: `V_SWEEP = 8.0321·(V_A − V_B/2)`; power-up gives ≈ 0 V. (b) R34 100 k /SYNC pull-up. (c) **DAC reference gate**: U11 TMUX1101 + R35, armed only by a software heartbeat on PC5 through the D6 charge pump. (d) Four new connectivity assertions; measurement-range models the contract. (e) `docs/firmware_handoff.md` | **Done in hardware** (§2). Margins simulated, to be measured. Positive bound is a firmware contract |
| **F3** | R30 (100 Ω 1/4 W 1206) overloaded in a rail fault; F1 described as a backstop | R30 = Vishay **PR02 100 Ω 1 % 2 W, `PR02000201000FB100`**, DIN0411 axial, body raised. Fault envelope (127 mA; R30 1.63 W of 2 W; R9 and R10 0.16 W) checked on every build. R9 = 10 Ω 1 % 0.25 W, R10 = 10.0 Ω 0.1 % 25 ppm/°C 0.5 W. F1 "backstop" claims removed everywhere | **Done**, except placement: **7 mm from the divider, but only 2.1 mm from F1** (§4). *Superseded:* F1 moved 14 mm from R30, `final_corrections.md` §2.1; the fault envelope now includes F2 and U12 (§2.7 there) |
| **F4** | C43 (VCAP_1) 2.2 µF; LQFP64's single VCAP needs 4.7 µF | C43 = 4.7 µF 10 V X5R (DS10314 Table 16) | **Done** |
| **V1** | OPA548 runs in current limit by design; TI recommends an output snubber for that | R38 10 Ω + C49 10 nF 50 V X7R pads from VO_RAW to AGND, **DNP**; fit if bring-up sees oscillation | **Done** |
| L1 | D1–D4 marked 1N4148 on a SOD-123 footprint | 1N4148W (the SOD-123 part) | Done |
| L2 | ±15 V ceramics had no rating | C11, C12, C15, C16, C20–C23 = 100 nF **50 V X7R** (audit asked ≥ 25 V; 50 V for DC-bias margin) | Done |
| L3 | R10 tolerance/TCR unspecified | 10.0 Ω **0.1 %, 25 ppm/°C**, 0.5 W, 0207 axial. The MPN field carries this specification; no specific part number was selected | Done (spec). Sourcing open. *Superseded:* RS02B10R00BE12, 3 W, 20 ppm/°C (`final_corrections.md` §2.2) |
| L4 | R14 "DNP" only in the value text | Real DNP: schematic `(dnp yes) (in_bom no)`, board `exclude_from_bom dnp`. Same for R38/C49 | Done |
| L5 | PC13 sourced ≈ 4 mA into LED1 (DS10314: must not source) | LED1 now sunk by PC13: +3V3D → LED1 → R23 **1 kΩ** → PC13, ≈ 1.3 mA (limit 3 mA) | Done |
| L6 | C9 (REF5025 output) 1 µF at the bottom of its 1–100 µF stability range | C9 = **4.7 µF 10 V X7R** (SBOS410O) | Done |
| L7 | OPA548 TO-220-7 drills 1.1 mm | Project footprint with **1.2 mm** drills, generated by `tools/vendor_footprints.py` | Done |
| L8 | U5 pinout `VERIFY-DATASHEET` | Cross-checked pin by pin against TI **SBOS070D** at every symbol build (`libraries/provenance/opa548_sbos070d_pin_functions.txt`); `RESOLVED` | Done (stagger remains a mechanical check) |
| L9 | U5 tab is at −15 V | Isolation guidance in `bringup_checklist.md` §1 and `DESIGN_DOCUMENT.md` §5.3 | Done (docs) |
| L10 | High-impedance nodes and flux | Cleaning and bake guidance, node list, in `bringup_checklist.md` §1 | Done (docs) |
| L11 | No mounting holes; test points on the bottom | **Decision: no holes added, bottom TPs kept** (§4) | Decided, documented. *Superseded:* three isolated M3 holes H1–H3 added (`final_corrections.md` §2.4) |
| L12 | New copper and clearances | Every new track designed at the repository netclass clearances; **0 new** DRC violations under either rule set; 109 pre-existing policy clearance errors remain (§5) | Done; **board is not DRC-clean under the policy**. *Superseded:* 0 policy errors (`final_corrections.md` §2.3) |
| D | Stale or false documentation | "Bus-powered", F1 "backstop", "J1 labelled twice", U5 `VERIFY-DATASHEET`, the `V_DAC − VREF/2` transfer function, part counts, C43/R23/R30/LED values: corrected in README, `DESIGN_DOCUMENT.md`, `SCHEMATIC.md`, `manifest.yaml` and `docs/*` (§7) | Done |

## 2. F2 — the safe state on reset, halt and crash

### What the hardware does now

| Condition | Mechanism | Result |
|---|---|---|
| Power-up | DAC8552 powers up at zero scale on both channels (SLAS430A). The midpoint is channel B / 2 | V_SWEEP = 8.03·(0 − 0) ≈ 0 |
| MCU reset, debugger halt, firmware crash, PC5 stuck high or low, MCU unpowered | No PC5 edges → DAC_ARM decays through R37 → U11 (TMUX1101) opens → R35 pulls the DAC's VREF pin to 0 V → both DAC outputs are VREF·code/65536 = 0 whatever codes they hold | V_SWEEP ≈ 0 within ≈ 52 ms (simulated) |
| Armed | Firmware loads B = `0xFFFF`, A = start code, then toggles PC5 ≥ 1 kHz from its main loop | DAC_ARM ≈ 2.4 V, U11 on, normal sweep |

The heartbeat pump is PC5 → R36 1 kΩ → C47 100 nF → D6 BAT54S (clamp diode to
DGND) → DAC_ARM, held by C48 470 nF and bled by R37 100 kΩ. There is no DC
path from any rail or GPIO to DAC_ARM; `validation/assertions.py::a_arm_needs_a_heartbeat`
enforces that on every build. PC5 has no timer output function on the
STM32F411 (DS10314: EVENTOUT and ADC only), so no peripheral can keep the
pump running once the CPU stops.

Simulated (transient model of the pump, the TMUX1101 thresholds at
VDD = 5 V: V_IH 1.49 V, V_IL 0.87 V):

| Case | DAC_ARM |
|---|---|
| 1 kHz, nominal | 2.42 … 2.46 V |
| 1 kHz, worst case (V_OH 3.0 V, diode V_f 0.4 V, 10 µA leakage) | 1.87 V |
| 500 Hz, worst case | 1.61 V |
| a single GPIO edge | ≈ 0.53 V |
| heartbeat stops | < 0.87 V within ≈ 52 ms |

### Alternatives considered and rejected

| Option | Why not |
|---|---|
| OPA548 E/S as the safe state | A disabled OPA548 still leaks a few mA (SBOS070D Fig. 16, typical), which into an open or high-impedance DUT is not "off"; and E/S needs level shifting |
| Gate driven by a GPIO level | A halted or crashed CPU holds its pin levels. It would disarm on reset (pins float) but not on halt or crash |
| Heartbeat from a timer/PWM, DMA or MCO | Peripherals keep running after a crash, and a timer can keep running through a debugger halt. That is why PC5 was chosen: it has no timer function |
| Watchdog reset alone | The DAC keeps its codes through the reset, so a reset alone does not zero the output; the IWDG can also be frozen while debugging |
| TS5A3159A as the switch | V_IH = 2.4 V at 5 V, above what the pump guarantees |
| Output relay / disconnect | A larger architectural change (part, drive, enclosure, contact ratings). Not needed to meet "zero on reset, halt, crash". Remains a possible addition |

### Residual risks (not closed by hardware)

1. **≈ 52 ms disarm window.** The last commanded voltage decays to zero over
   about 50 ms after the heartbeat stops.
2. **Positive bound is a firmware contract.** −10.04 V is a hardware bound.
   +10.04 V holds only while channel B is at full scale. With B lower, the
   output can rise to U3B's saturation (≥ +13.8 V guaranteed, ≤ +15 V rail),
   past U5's input range and the measurable range.
3. **A firmware fault that keeps the main loop toggling PC5** (wrong
   commands from a live loop) is not caught. The heartbeat proves the loop
   runs, not that it is correct. Firmware should toggle only after its health
   checks (`firmware_handoff.md` §2).
4. **"Zero" is a 0 V setpoint, not a disconnect.** U5 stays enabled and
   drives the DUT at ≈ 0 V from a low impedance. The residual offset is
   estimated at ≤ 0.1–0.15 V; bring-up measures it.
5. **Partial supply states** (for example ±15 V with +5 V absent) were not
   modelled. Bring-up §5 characterises them. *Superseded:* analysed, and
   D8/D9 and U12 added (`final_corrections.md` §2.5).
6. The pump numbers are from simulation and have not been measured.

## 3. Component / BOM changes

Counts: 126 board parts (123 fitted, 3 DNP) plus 9 schematic-only PWR_FLAGs
(was 115 + 8); 73 nets (was 66).

| Ref | Before | After | Why |
|---|---|---|---|
| U11 | — | TMUX1101DBVR, SOT-23-5 | DAC reference gate (F2) |
| C46 | — | 100 nF 0603 | U11 VDD decoupling |
| R35 | — | 1 MΩ 0603 | DAC_VREF pull-down |
| R34 | — | 100 kΩ 0603 | /SYNC pull-up (F2) |
| R36, C47, D6, C48, R37 | — | 1 kΩ, 100 nF, BAT54S (SOT-23), 470 nF 16 V X7R, 100 kΩ | Heartbeat charge pump (F2) |
| R38, C49 | — | 10 Ω 0805, 10 nF 50 V X7R 0805, **DNP** | Output snubber pads (V1) |
| #FLG9 | — | PWR_FLAG on DAC_VREF | U11's switch pins are passive (ERC) |
| R30 | 100 Ω 1/4 W 1206 | **100 Ω 1 % 2 W Vishay PR02, `PR02000201000FB100`**, DIN0411 axial 12.7 mm | Rail-fault envelope (F3) |
| R9 | 10 Ω | 10 Ω 1 % 0.25 W 1206 | Fault envelope 0.16 W |
| R10 | 10.0 Ω 1 W | 10.0 Ω 0.1 % 25 ppm/°C 0.5 W, 0207 axial (specification) | Precision shunt (L3) |
| C43 | 2.2 µF | 4.7 µF 10 V X5R | VCAP_1 (F4) |
| C9 | 1 µF | 4.7 µF 10 V X7R | REF5025 stability (L6) |
| C11, C12, C15, C16, C20–C23 | 100 nF | 100 nF 50 V X7R | Rating on ±15 V (L2) |
| D1–D4 | 1N4148 | 1N4148W | SOD-123 part (L1) |
| R23 | 330 Ω | 1 kΩ | LED sunk by PC13 (L5) |
| R14 | "DNP (G=1)" value text | same, plus real DNP attributes | (L4) |
| J1 | value `+15V_RAW/-15V_RAW/+5V_RAW/GND` | `BENCH SUPPLY 1:+15V 2:-15V 3:+5V 4:GND` | (F1) |
| U5 footprint | TO-220-7 staggered, 1.1 mm drills | project copy, 1.2 mm drills | (L7) |

## 4. PCB changes and placement decisions

Applied by `layout/audit_corrections.eco` (140 operations, each with a
reason) on top of the bipolar board. Details are in
`docs/physical_design.md` §"The audit-corrections ECO". Board result: 126
footprints, 889 tracks, 107 vias, 2 zones, fully routed.

- **R30 placement (F3), partially met.** R30 is electrically between F1 and
  D5/J4, so it must sit in the protection area. Its courtyard is now **7 mm**
  from the precision divider (R33; it was 2.8 mm) but only **2.1 mm** from
  F1's (the old 1206 was 2.5 mm below F1). The strip between F1 and the J6
  jack is the only room within reach. R30's heat lowers F1's hold current. In
  a fault that makes F1 trip sooner, which is the protective direction. On the
  normal 49 mA plateau (0.24 W in R30) it eats into F1's hold margin, already
  thin above about 50 °C ambient. Bring-up §7 measures it. **Full separation
  needs the protection area re-laid out** (move F1 or the jacks), which this
  change did not do.
- **Mounting holes: not added.** The four corners are occupied by J7, J5, J1
  and J3. M3 holes with their keep-out need connectors moved and copper
  rerouted, so they belong to the next layout spin. Recommendation for that
  spin: four M3 holes inset ≥ 4 mm, with keep-outs.
- **Bottom-side test points: kept.** TP1–TP8 stay on B.Cu and are probed from
  below. Support the board on its connectors or an edge clamp
  (`bringup_checklist.md` §1).
- **AGND stitching.** Three AGND vias were added where the new tracks isolated
  the U4/C13 fill from the main pour (found by zone-island analysis and
  confirmed by DRC: 0 unconnected).

## 5. Validation record

### Environment

| Item | Version |
|---|---|
| OS | Ubuntu 24.04.5 LTS, Linux 6.18 (cloud container) |
| Python / SKiDL | Python 3.12.3, SKiDL 2.2.3 |
| KiCad libraries | kicad-symbols 9.0.9.1 @ `ad36cd14bcd1`, kicad-footprints 9.0.9.1 @ `2b941bf1d978` (pinned) |
| KiCad (ERC, DRC, round trip, loads) | kicad-cli **10.0.7**; pcbnew 10.0.7 (through the distribution Python 3.12) |
| Date | 2026-10-26 |

### Results

Each CI step from `.github/workflows/build.yml` (KiCad job) was replayed
locally on the final sources, in that order. The replay hit a 30-minute
background limit during the guard test, so the guard test (all 27 cases),
KiCad schematic ERC and the `--autoplace` build were then run on their own,
on the same unchanged sources.

| Step | Result |
|---|---|
| `tools/kicad_env.py --check` | PASS (12/12 symbol libs, 21/21 footprint libs) |
| `python3 main.py` | PASS: structural 135/135 symbols, 126/126 footprints; SKiDL ERC 0/0; **15/15 assertions**; measurement range + fault envelope PASS; board checks 16/16; Update PCB a no-op |
| `author_project_symbols.py --check`, `gen_project_config.py --check`, `vendor_footprints.py --check` | PASS (27 symbols; 35 vendored + 1 project footprint) |
| `check_kicad_loads.py` | PASS (KiCad 10.0.7 loads the library, schematic and board) |
| `footprint_check.py` | PASS: 392 pads, 345 netted, 47 declared no-connect, 0 unaccounted |
| `erc.py` | PASS: 0 errors, 0 warnings, PWR_FLAG set matches `EXTERNALLY_SOURCED_RAILS` |
| `measurement_range.py` | PASS: V ±11.78 V and I ±97.8 mA guaranteed vs ±10.04 V / ±91.3 mA reachable; headroom 334 mV / 115 mV; fault 127 mA, R30 1.63/2 W, R9 0.16/0.25 W, R10 0.16/0.5 W |
| `schematic_check.py` | PASS: 354 connections, 135 references, 8 sheets |
| `kicad_erc_power.py` | PASS (every power_in pin driven; DAC_VREF by #FLG9) |
| `kicad_roundtrip.py` | PASS: KiCad reads 345 connections in 73 nets, 126 components on the right sheets |
| `update_pcb_noop.py --self-test`, `update_pcb_noop.py` | PASS (calibration scaled to 47 open pads) |
| `routed_board_check.py` | PASS 16/16 (below) |
| `tools/reproduce_reference.py` | PASS (the layout source still regenerates `unipolar-routed/`) |
| fab conformance, pad gaps, identifier audit | PASS |
| deterministic (two builds, byte-compare) | PASS |
| `check_no_abs_paths.py` | PASS |
| `tools/test_guard_fails.sh` | PASS: all 27 injected faults caught. Four are new for this work: the midpoint back on VREF_2V5, U4 VREF wired around U11, R37 returned to +3V3D (a DC arming path), and R30 back to 1/4 W. `validation/assertions.py` gained a standalone entry point so the first three have their own gate |
| **KiCad schematic ERC**: `kicad-cli sch erc --exit-code-violations stage1_iv.kicad_sch` | **0 errors, 0 warnings** (ignored by project setting: single-use global label, four-way junction, SPICE model, footprint-filter mismatch) |
| `main.py --autoplace` | PASS (bootstrap board, J1 legend now from the circuit) |
| No-KiCad build (`build-without-kicad` job), in a scratch copy with no libraries and no `kicad-cli` on PATH | PASS: every CI-checked SKIPPED / NOT RUN line present; board untouched; 15/15 assertions; netlist connectivity identical (354 connections). Only provenance fields (`Symbol_Source`, `Datasheet`) differ, as designed |

**KiCad DRC** (`kicad-cli pcb drc --refill-zones --all-track-errors
--schematic-parity`, 10.0.7):

| Rule set | Errors | Warnings | Unconnected | Parity |
|---|---|---|---|---|
| As built (0.20 mm + scoped intra-footprint rules, every check on) | **0** | 2 `silk_edge_clearance` (J3, also on the original) | **0** | — |
| Repository netclass policy | **109** clearance | 2 (same) | **0** | **0** |

All 109 policy errors are on the original human routing at the same place
and measured value (the original has 116); none is new. Placement heuristics
are all pre-existing: courtyard 26, edge 1, ground split 3, hole spacing 1,
silkscreen 117, with 0 new.

**The board is DRC-clean under the rules it was routed to and is not
DRC-clean under the repository netclass policy.** The 109 remaining errors
are human-routed 0.20 mm gaps where the policy asks 0.25–0.30 mm. IPC-2221B
(B2, external, uncoated) asks 0.1 mm up to 30 V, so they are margin
deviations, not safety gaps. Clearing them means re-routing unrelated
signals.

### What is calculated, not measured

- Charge-pump arm level, arm time, disarm time (transient simulation).
- The safe-state residual offset (estimate ≤ 0.1–0.15 V).
- R30 / F1 thermal coupling at the plateau and in the rail fault.
- OPA548 stability at its current-limit plateau (the snubber decision).
- Everything else in `bringup_checklist.md` §9.

## 6. Remaining lower-severity items

As left by this round. Each is dispositioned in
[`final_corrections.md`](final_corrections.md) (§ in brackets).

- **R10 part number.** The specification is set (0.1 %, 25 ppm/°C, ≥ 0.5 W,
  0207 axial). A specific orderable part number still has to be chosen.
  [§2.2: RS02B10R00BE12]
- **R30 / F1 proximity**, §4. [§2.1: F1 relocated]
- **Mounting holes**, next layout spin. [§2.4: H1–H3]
- **109 policy clearance deviations**, §5. [§2.3: 0 remaining]
- **Partial-supply states** and **power-sequencing transients** are not
  handled in hardware (bring-up §5 characterises them). [§2.5: D8/D9, U12;
  transients still measured at bring-up]
- **FORCE− has no TVS or fuse** (pre-existing review item R15). [§2.6: F2]
- **OPA548 ILIM** is uncharacterised at ≈ 49 mA (pre-existing R4). The
  measurement and the protection no longer rely on its value. [§2.7: passive
  bound stated; ILIM still typical only]

## 7. Documentation touched, and dated records left as they were

Updated: `README.md`, `DESIGN_DOCUMENT.md`, `SCHEMATIC.md`, `manifest.yaml`,
`layout/README.md`, `docs/physical_design.md`, `docs/bipolar_measurement.md`
(revision note), `docs/silkscreen_and_packages.md`,
`docs/identifier_resolution.md`. New: this file, `docs/firmware_handoff.md`,
`docs/bringup_checklist.md`.

Left as dated records, not rewritten: `docs/audit_identifiers_before.md` and
`docs/audit_identifiers_after.md`, snapshots of the identifier work. The
"after" file still shows U5 as `VERIFY-DATASHEET` and the old C43/D1–D4/R23
values. Regenerating it here would embed this machine's library path, which
`tools/check_no_abs_paths.py` rejects. *Superseded:* the generator now
prints portable paths and the "after" file has been regenerated
(`final_corrections.md` §2.8). Run `python3 tools/audit_identifiers.py
> docs/audit_identifiers_after.md` on a machine with the libraries at a
standard prefix to refresh it. Also left as recorded: the KiCad 7 transcripts
in `docs/library_strategy.md` and the auto-placer notes in
`docs/pcb_generation.md`.
