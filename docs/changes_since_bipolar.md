# Changes since the bipolar design (`bfbb140`)

> **Later changes.** This list is kept as written. Do not order parts from
> it: the final design audit ([`final_audit.md`](final_audit.md)) changed U5
> to an **OPA547T**, R7 to **348 kΩ**, D7 to **BZT52B24** (±2 %), C26/C27 to
> **18 pF** and R30's order code to **PR02000201000FA100**. The current
> parts list is [`component_manifest.md`](../component_manifest.md).

Every change made to the design after commit `bfbb140` (the merge of the
bipolar routed board, PR #12), in one place. It covers two rounds:

| Round | Commits | Record (reasoning, validation) |
|---|---|---|
| 1. Final hardware audit corrections | `376977c`, `4626a58` (PR #13) | [`audit_corrections.md`](audit_corrections.md) |
| 2. Remaining hardware corrections | the commit that adds this file | [`final_corrections.md`](final_corrections.md) |

This file lists *what* changed. The *why*, the analysis and the
guaranteed / expected / bench split are in the two records; the design itself
is the SKiDL source (`blocks/*.py`) and the layout ECOs, each operation with a
reason. To see the raw difference: `git diff bfbb140 -- <path>`.

---

## 1. At a glance

| | `bfbb140` (bipolar) | After round 1 | After round 2 (now) |
|---|---|---|---|
| Board parts (fitted / DNP) | 115 (R14 "DNP" in its value text only) | 126 (123 / 3) | 132 (129 / 3) |
| Schematic-only PWR_FLAGs | 8 | 9 | 9 |
| Board-only mounting holes | 0 | 0 | 3 (H1–H3) |
| Nets | 66 | 73 | 77 |
| Board tracks / vias | 861 / 95 | 889 / 107 | 897 / 113 |
| Layout ECOs on the captured human layout | `bipolar.eco` | + `audit_corrections.eco` | + `final_corrections.eco`, `policy_clearance.eco` |
| KiCad DRC, as-built rules | 0 errors | 0 errors | 0 errors |
| KiCad DRC, repository netclass policy | 114 clearance errors | 109 | **0** |
| Connectivity assertions | 11 | 15 | 18 |
| Guard fault injections (all caught) | 23 | 27 | 33 |

In both DRC rows the board also carries the 2 silkscreen-to-edge warnings at
J3 that the original human-routed board has.

---

## 2. Circuit changes, by function

### Output safe state on power-up, reset, halt and crash (round 1, audit F2)

- The sweep midpoint now comes from **DAC channel B**:
  `V_SWEEP = 8.0321·(V_A − V_B/2)` (R1 on U4 VOUTB instead of VREF_2V5). The
  DAC's zero-scale power-on state now gives ≈ 0 V instead of −10.04 V.
- **DAC reference gate:** U11 (TMUX1101) connects VREF_2V5 to the DAC's
  reference only while DAC_ARM is high; R35 (1 MΩ) pulls DAC_VREF to AGND
  otherwise. C46 decouples U11.
- **Heartbeat charge pump:** PC5 → R36 → C47 → D6 (BAT54S) → DAC_ARM
  (C48, R37 to DGND). Only a toggling PC5 can arm the gate: there is no DC
  path from any rail. Reset, halt, crash or a stuck pin disarms it within
  ≈ 50 ms.
- R34 (100 kΩ) holds the DAC's /SYNC (CS_DAC) high while the MCU's pins float.
- New nets: DAC_ARM, DAC_VREF, SAFE_HB, SAFE_HB_AC, SAFE_HB_R, V_DACB.

### Force path and protection

- **R30** (round 1, F3): 100 Ω 1/4 W 1206 → Vishay **PR02 100 Ω 1 % 2 W**
  (`PR02000201000FB100`), axial, body raised, sized for the rail fault.
  R9: 10 Ω → 10 Ω 1 % 0.25 W.
- **Output snubber pads** (round 1, V1): R38 10 Ω + C49 10 nF 50 V in series
  from VO_RAW to AGND (new net VO_SNUB between them), **DNP**, to fit if
  bring-up sees oscillation.
- **FORCE+ photorelay** (round 2): U12 (Toshiba **TLP176AM(TPL,E**) between
  R30 and D5/J4 (new net FORCE_SW). Its LED is in a series string across
  the rails: +15VA → R39 620 Ω → LED → D7 (BZT52C24, 24 V zener) → −15VA
  (new nets SSR_LED_A, SSR_LED_K). U12 is guaranteed open with either rail
  absent and guaranteed closed at ≥ ±14.45 V.
- **FORCE− protection** (round 2): F2 (**0ZRM0010FF1E** PTC) between J7 and
  the shunt. The shunt node is now ISENSE_HI (F2, R10, U6 +IN, TP3); FORCE−
  holds only J7 and F2. Nothing is connected across or around the shunt.
- **R10 shunt:** 10.0 Ω 1 W → 10.0 Ω 0.1 % 25 ppm/°C 0.5 W (round 1, a
  specification) → Vishay Dale **RS02B10R00BE12**, 10.00 Ω 0.1 % 20 ppm/°C
  3 W wirewound (round 2), sized to survive an external source on J7 until
  F2 trips.
- F1 is no longer described anywhere as a backstop (round 1).

### Power

- **Dead-rail clamps** (round 2): D8, D9 (**MBR0540** Schottky), AGND → +15VA
  and −15VA → AGND, reverse-biased in service. A missing ±15 V rail is held
  within ≤ 0.51 V of AGND instead of being dragged toward the live rail.
- C9 (REF5025 output): 1 µF → 4.7 µF 10 V X7R (round 1).
- C11, C12, C15, C16, C20–C23 (±15 V decoupling): 100 nF → 100 nF 50 V X7R
  (round 1).

### MCU

- C43 (VCAP_1): 2.2 µF → 4.7 µF 10 V X5R (round 1, F4).
- Status LED now **sunk** by PC13: +3V3D → LED1 → R23 → PC13. R23 330 Ω →
  1 kΩ (≈ 1.3 mA). Net LED_A became LED_K (round 1).
- #FLG9 (PWR_FLAG on DAC_VREF), because U11's switch pins are passive (round 1).

### Sense inputs

- D1–D4: 1N4148 → **1N4148W** (the SOD-123 part the footprint is for)
  (round 1).
- R14 is a real DNP part (schematic `dnp` / not in BOM; board
  `exclude_from_bom dnp`), as are R38 and C49 (round 1).

### Connectors

- J1 value is now `BENCH SUPPLY 1:+15V 2:-15V 3:+5V 4:GND` (round 1, F1).

---

## 3. Bill of materials

Generated in `component_manifest.md`; this is the difference from `bfbb140`.

**Added (17 board parts + 1 PWR_FLAG):**

| Ref | Part | Footprint | Round |
|---|---|---|---|
| U11 | TMUX1101DBVR | SOT-23-5 | 1 |
| C46 | 100 nF | 0603 | 1 |
| R35 | 1 MΩ | 0603 | 1 |
| R34 | 100 kΩ | 0603 | 1 |
| R36, C47, D6, C48, R37 | 1 kΩ, 100 nF, BAT54S, 470 nF 16 V X7R, 100 kΩ | 0603 / SOT-23 | 1 |
| R38, C49 | 10 Ω, 10 nF 50 V X7R — **DNP** | 0805 | 1 |
| #FLG9 | PWR_FLAG on DAC_VREF | — | 1 |
| U12 | Toshiba TLP176AM(TPL,E | Toshiba SOIC-4 | 2 |
| R39 | 620 Ω 1 % 0.25 W | 1206 | 2 |
| D7 | BZT52C24 | SOD-123 | 2 |
| D8, D9 | MBR0540 | SOD-123 | 2 |
| F2 | Bel 0ZRM0010FF1E | as F1 | 2 |

Plus three board-only mounting holes H1–H3 (no symbol, not in the BOM).

**Changed:**

| Ref | `bfbb140` | Now | Round |
|---|---|---|---|
| R30 | 100 Ω 1/4 W, 1206 | 100 Ω 1 % 2 W **PR02000201000FB100**, DIN0411 axial | 1 |
| R10 | 10.0 Ω 1 W, DIN0207 | 10.00 Ω 0.1 % 3 W **RS02B10R00BE12**, DIN0614 axial | 1, 2 |
| R9 | 10 Ω | 10 Ω 1 % 0.25 W | 1 |
| R23 | 330 Ω | 1 kΩ | 1 |
| C9 | 1 µF | 4.7 µF 10 V X7R | 1 |
| C43 | 2.2 µF | 4.7 µF 10 V X5R | 1 |
| C11, C12, C15, C16, C20–C23 | 100 nF | 100 nF 50 V X7R | 1 |
| D1–D4 | 1N4148 | 1N4148W | 1 |
| J1 | `+15V_RAW/-15V_RAW/+5V_RAW/GND` | `BENCH SUPPLY 1:+15V 2:-15V 3:+5V 4:GND` | 1 |
| U5 | KiCad TO-220-7 (1.1 mm drills) | project TO-220-7 with 1.2 mm drills | 1 |
| R14 | "DNP" in the value only | real DNP attributes | 1 |

Removed: none.

---

## 4. Nets

66 → 77. Added: DAC_ARM, DAC_VREF, SAFE_HB, SAFE_HB_AC, SAFE_HB_R,
V_DACB, VO_SNUB, LED_K (round 1); FORCE_SW, ISENSE_HI, SSR_LED_A,
SSR_LED_K (round 2). Removed: LED_A (renamed LED_K when the LED moved to
being sunk).

---

## 5. PCB (`stage1_iv.kicad_pcb`, generated)

The board is still the human placement and routing captured in
`layout/stage1_iv.layout`; every change is an ECO operation with a reason.

**Round 1 — `layout/audit_corrections.eco` (140 operations):** places and
routes U11, the charge pump, R34/R35/C46 and the DNP snubber pads; R30
re-placed on its DIN0411 footprint; LED1 re-wired; three AGND stitch vias;
silkscreen `BENCH SUPPLY` / `+15V -15V +5V GND` at J1 and `FORCE+ FORCE-
SENSE+ SENSE-` at J4–J7. U5's footprint has 1.2 mm drills.

**Round 2 — `layout/final_corrections.eco` (72 operations):**
- F1 moved from beside R30 and U5 to the bottom edge: 14.1 mm from R30 and
  22.8 mm from U5, body to body (it was 2.6 mm and 0.9 mm).
- U12 in F1's old place; R39 (top) and D7 (bottom) beside it; D8 (top) and
  D9 (bottom) on the rails; F2 beside J7; R10 on DIN0614, flat in the top
  band, with its Kelvin tap (U6 +IN, TP3) on its own trace from R10's pad.
- Mounting holes **H1 (87.7, 37.5), H2 (122.2, 88.2), H3 (157.7, 89.2)**:
  M3, no copper, 4.0 mm-radius keepout on both copper layers, courtyard on
  both sides (project footprint `stage1_iv:MountingHole_3.2mm_M3_Isolated`).
  The layout format gained a `(board_part ...)` entry for these parts that
  have no schematic symbol.

**Round 2 — `layout/policy_clearance.eco` (293 operations):** every run
closer than the repository netclass clearances (0.25 mm sense/force/local
analog, 0.30 mm ±15 V) re-routed between the same terminals; one ADC_XTAL1
segment narrowed 0.40 → 0.25 mm (its class width); four AGND stitch vias
where re-routing would otherwise have isolated two F.Cu pour islands. No
part moved.

**Design rules:** the board went from 114 policy clearance errors (all
inherited from the human 0.20 mm routing) to 0. Of the 109 left after
round 1, 87 were re-routed and 22 were on four nets misclassified as
high-impedance analog (next section).

---

## 6. Netclasses (`libraries/netclasses.py` → `stage1_iv.kicad_pro` / `.kicad_dru`)

- Round 1: new nets classified (DAC_VREF, V_DACB → SENSE; SAFE_HB*,
  DAC_ARM → DIGITAL; VO_SNUB → FORCE).
- Round 2: SSR_LED_A/K → POWER_HV (the LED string spans the ±15 V rails);
  FORCE_SW, ISENSE_HI → FORCE; **VCAP1 → POWER_LV and LED_K, CC1, CC2 →
  DIGITAL**, out of LOCAL_ANALOG: none of them is a high-impedance analog
  node. The reason is recorded at each class. No class clearance or board
  minimum was lowered.

---

## 7. Validation and tooling

- `validation/assertions.py`: 11 → 18 connectivity assertions. Round 1: the
  channel-B midpoint, the DAC reference gate, the DC-free DAC_ARM pump, the
  /SYNC pull-up. Round 2: the FORCE− chain (J7 → F2 → shunt, nothing across
  it), U12 needs both rails, the dead-rail clamps; the FORCE+ chain now
  includes U12. It can also be run on its own as a gate.
- `validation/measurement_range.py`: round 1 added the rail-fault envelope
  (R9/R30/R10 against their ratings); round 2 computes it from guaranteed
  minima (F1/F2 3.0 Ω, R30 hot, U12 0 Ω), takes each resistor's exact worst
  dissipation, adds the external-source-on-J7 case against R10's short-time
  rating, and reports ILIM as typical only.
- `validation/routed_board_check.py`: round 2 makes the policy DRC a hard
  requirement (0 errors, 0 unconnected, no warning the original lacks)
  instead of "nothing new compared with the original".
- `tools/test_guard_fails.sh`: 23 → 33 injected faults, all caught (round 1:
  midpoint back on VREF, DAC reference bypassing U11, a DC path onto
  DAC_ARM, R30 at 1/4 W; round 2: F2 bypassed, U12 LED string to AGND, D9
  reversed, R10 at 0.5 W, `policy_clearance.eco` dropped, a mounting hole on
  routed copper).
- `generators/`: DNP attributes on schematic and board; J1 silkscreen read
  from J1's pad nets; ECOs can add and remove board graphics (round 1) and
  place board-only parts (round 2).
- `tools/author_project_symbols.py` cross-checks the OPA548 symbol pin by pin
  against TI SBOS070D (round 1). `tools/vendor_footprints.py` generates the
  project footprints (TO-220-7 with 1.2 mm drills; the isolated M3 hole).
  `tools/audit_identifiers.py` prints portable paths, so
  `docs/audit_identifiers_after.md` is regenerated and no longer exempt from
  `tools/check_no_abs_paths.py` (round 2).
- `validation/update_pcb_noop.py` and `validation/drc_check.py` follow the
  DNP attributes and rotated board text; `validation/footprint_check.py`'s
  no-connect allow-list follows the pins now in use (U4 channel B, PC5)
  (round 1).
- `main.py`: prints the policy DRC result as a requirement, counts the
  mounting holes, and puts every new part on its sheet in the component
  manifest.

---

## 8. Documentation

- New: `docs/audit_corrections.md`, `docs/firmware_handoff.md`,
  `docs/bringup_checklist.md` (round 1); `docs/final_corrections.md` and
  this file (round 2).
- Updated throughout both rounds: `README.md`, `DESIGN_DOCUMENT.md`,
  `SCHEMATIC.md` (round 1), `manifest.yaml`, `layout/README.md`,
  `docs/physical_design.md`, `docs/pcb_generation.md`,
  `docs/bipolar_measurement.md` (revision note), `docs/silkscreen_and_packages.md`,
  `docs/identifier_resolution.md`, `docs/LIBRARY_DEPENDENCIES.md`,
  `docs/audit_identifiers_after.md`. Statements found to be false or stale
  were corrected (for example: "bus-powered", F1 as a backstop, J1
  "labelled twice", U5 `VERIFY-DATASHEET`, the board having no mounting
  holes, the policy DRC count).

---

## 9. Still open

Nothing in either round has been built or measured. What the first boards
must show, and the risks accepted in the design (R30 at 98 % of rating in
the stacked worst case, ILIM typical only, F1/F2 neither guaranteed to hold
nor to trip in the fault envelope, U12 thresholds specified at 25 °C, no
fourth mounting hole, R10 sourcing), are in
[`final_corrections.md`](final_corrections.md) §4 and
[`bringup_checklist.md`](bringup_checklist.md).
