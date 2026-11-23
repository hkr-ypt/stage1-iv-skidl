# Stage 1 I–V Characterization System — Hardware Design Document

**Purpose of this document:** design handoff for an independent review of the KiCad
project (schematic + PCB). It describes what the hardware is meant to do, how the
implemented circuit does it, the reasoning the repository records for its main
design decisions, and where a reviewer's attention is most useful.

**Scope and conventions**

- **Design facts** are statements about what the schematic/netlist actually
  implements. They were checked against the committed netlist (`stage1_iv.net`)
  and the SKiDL source (`blocks/*.py`). The SKiDL build was also re-run while
  this document was prepared, and it reproduced the committed connectivity and
  values.
- **Inference:** marks conclusions drawn from the design that the repository
  does not state itself.
- **[DS-check]** marks a statement that depends on a component data sheet
  value that is not stored in the repository. Confirm it against the current
  data sheet.
- **Review item Rn** refers to the numbered list in §14.
- This document does not modify or replace any design file.
- **Revision: bipolar measurement.** The measurement chain was changed from
  unipolar (zero on AGND, 0…+5 V and positive current only) to bipolar (zero
  at the 2.500 V reference, ±11.8 V and ±97.8 mA). Sections 2–7, 10, 11, 13,
  14 and the appendices describe the revised circuit. The reasoning, the
  options that were rejected and the routed-board change list are in
  [`docs/bipolar_measurement.md`](docs/bipolar_measurement.md).
- **Revision: final hardware audit corrections.** The sweep midpoint now
  comes from DAC channel B (`V_SWEEP = 8.0321·(V_A − V_B/2)`), the DAC
  reference is gated by a software-heartbeat charge pump so that the output
  returns to 0 V on MCU reset, halt or crash, R30 is a 2 W Vishay PR02, C43 is
  4.7 µF, and the bench-supply and DUT connectors carry per-terminal labels.
  Smaller part, rating and layout corrections were made too. The full list
  and its validation record are in
  [`docs/audit_corrections.md`](docs/audit_corrections.md). The firmware
  obligations the hardware now creates are in
  [`docs/firmware_handoff.md`](docs/firmware_handoff.md), and the first-power
  procedure is in [`docs/bringup_checklist.md`](docs/bringup_checklist.md).
- **Revision: remaining hardware corrections.** FORCE+ now passes a
  photorelay U12 (TLP176AM) that is guaranteed open unless both ±15 V rails
  are up (LED string R39 + D7, a 24 V zener, across the rails); D8/D9 clamp a dead
  ±15 V rail to AGND; FORCE− has a series PTC F2 ahead of the shunt; R10 is
  the orderable Vishay RS02B10R00BE12 (10.00 Ω 0.1 % 20 ppm/°C 3 W); F1 moved
  away from R30 and U5; three isolated M3 mounting holes; and the board is
  re-routed to the repository netclass clearances (0 policy DRC errors). The
  record, with what is guaranteed, expected or left to the bench, is
  [`docs/final_corrections.md`](docs/final_corrections.md). Sections below
  that predate it are corrected where they state a fact it changed.
- **Revision: final design audit.** U5 is fitted with an **OPA547T** in place
  of the OPA548T (same family, pinout, TO-220-7 package and footprint) and R7
  is 348 kΩ. A 49 mA limit is 1 % of the OPA548's range and lies inside that
  part's ±100 mA typical / ±250 mA maximum limit tolerance, so the plateau
  could have been anywhere from zero upward; on the OPA547 the expected
  plateau at this board's ±15 V rails is 40…65 mA (design centre 49 mA), and
  the worst-case stack of its errors, 8…89 mA, stays clear of zero. The
  U5 output network R38/C49 is now **fitted, with R38 = 100 Ω**: TI's model
  of the OPA547 oscillates on the plateau with an inductive DUT without
  it, and the earlier 10 Ω would have cost this unity-gain stage its phase
  margin. D7 is the ±2 % grade (BZT52B24), which keeps U12 closed at the
  nominal rails once the zener's self-heating is counted. The ADS1256
  crystal load capacitors C26/C27 are 18 pF (the data sheet allows
  5–20 pF), R30's order code is corrected to PR02000201000FA100, and
  C17/C18 have an order code for a part that exists (a 10 µF / 35 V polymer
  tantalum; no MnO2 part of that rating is made in the 3528 case). **No
  track, via, pad, zone or placement changed**: every change is a fitted
  part, a value or an order code on existing footprints.
  The record, with the data-sheet and simulation evidence, is
  [`docs/final_audit.md`](docs/final_audit.md). Sections below are corrected
  where they state a fact it changed.

> **Important context about the PCB.** The board in the repository root,
> `stage1_iv.kicad_pcb`, is now the **human-placed, human-routed board**,
> regenerated from source: its placement and copper are captured from the
> original routed project (`unipolar-routed/`, unipolar, kept unmodified as the
> reference) into `layout/stage1_iv.layout`, and the bipolar-measurement change
> is applied to it as a reasoned ECO (`layout/bipolar.eco`: R32/R33 placed,
> U6/U7 REF and U8 AINCOM re-routed to VREF_2V5, three AGND stub segments
> removed; no other part moved and no other track touched). It is fully
> routed; KiCad 10 DRC reports 0 unconnected items and 0 errors under the
> rules it was routed to. A second ECO, `layout/audit_corrections.eco`,
> places and routes the audit-correction parts and the new silkscreen labels
> (see `docs/audit_corrections.md`); `layout/final_corrections.eco` places
> the parts of the remaining corrections and the mounting holes, and
> `layout/policy_clearance.eco` re-routes the human 0.20 mm runs to the
> repository's netclass clearances, under which KiCad DRC is now clean too
> (`docs/final_corrections.md`). The schematic remains the
> electrical source of truth. Where sections below describe the earlier
> auto-placed board (placement, outline size, mounting holes), they describe
> the bootstrap output of `python3 main.py --autoplace`, not the board of
> record; §11's observations were written as things to check on the routed
> board and still apply to it.

---

## Table of contents

1. [What the instrument is for](#1-what-the-instrument-is-for)
2. [Design at a glance](#2-design-at-a-glance)
3. [System architecture](#3-system-architecture)
4. [Operating concept (how a measurement is made)](#4-operating-concept)
5. [Signal chain, stage by stage](#5-signal-chain-stage-by-stage)
6. [Measurement ranges: what can be applied vs. what can be measured](#6-measurement-ranges)
7. [Shared voltage reference and the ratiometric scheme](#7-shared-voltage-reference-and-the-ratiometric-scheme)
8. [Power architecture and grounding](#8-power-architecture-and-grounding)
9. [MCU, digital interfaces and USB](#9-mcu-digital-interfaces-and-usb)
10. [Protection and fault behavior](#10-protection-and-fault-behavior)
11. [PCB and layout considerations](#11-pcb-and-layout-considerations)
12. [Design source of truth: SKiDL → KiCad](#12-design-source-of-truth-skidl--kicad)
13. [Verification status and documentation discrepancies](#13-verification-status-and-documentation-discrepancies)
14. [Review focus areas](#14-review-focus-areas)
15. [Repository / Review Map](#15-repository--review-map)
16. [Appendix A — Key nets](#appendix-a--key-nets)
17. [Appendix B — Calculations](#appendix-b--calculations)

---

## 1. What the instrument is for

The board is a **programmable DC source-measure front end for I–V curve
tracing**. A microcontroller steps a precision DAC. The DAC is level-shifted
and amplified into a bipolar sweep voltage, and a power op-amp buffers that
voltage and drives it into a device under test (DUT) through a 4-terminal
(Kelvin) connection. The DUT current is measured with a low-side shunt, and the
DUT voltage is measured directly at the DUT with a separate pair of sense leads.
A 24-bit delta-sigma ADC digitizes both. The intended DUTs are diodes and other
nonlinear two-terminal devices, measured in an engineering or experimental
setting.

The repository names the project "Stage 1", but it contains no definition of
later stages. Several source comments cite a design specification
(`Stage1_IV_System_Design.md`, sections "§C.1"–"§C.9"). **That file is not in
the repository.** Where this document gives design rationale, it is the
rationale written in the SKiDL source, `README.md` or `manifest.yaml`.

### Design goals evidenced in the repository

| Goal | Where stated | How it is implemented |
|---|---|---|
| Bipolar sweep to ≈ ±10 V | `README.md`, `blocks/dac_sweep.py` | 16-bit dual DAC + difference amplifier with gain 8.03 → ±10.04 V (channel A sweeps, channel B sets the midpoint) |
| 0 V output whenever firmware is not running | `blocks/dac_sweep.py`, `blocks/mcu.py` | DAC reference gate U11, held on only by a software heartbeat through the D6 charge pump (§5.2) |
| Measure DUT voltage without lead or shunt drops | `blocks/voltage_sense.py` | 4-wire Kelvin: separate SENSE± jacks into an INA128 |
| Signed V and I over the whole sweep (hysteresis on a fixed DUT) | `blocks/current_sense.py`, `blocks/voltage_sense.py`, `blocks/adc.py` | INA128 REF pins and ADC AINCOM on VREF_2V5; Kelvin attenuator k = 0.1992; current gain sized to the physical current bound. Checked by `validation/measurement_range.py` |
| Hardware current limit ≈ 50 mA | `blocks/output_buffer.py` | OPA547 programmable current limit, R7 = 348 kΩ → expected plateau 40…65 mA at ±15 V (design centre 49 mA), depending on polarity and temperature; ±30 mA maximum between units (SBOS056F; §5.3) |
| Protection in depth for DUT and instrument | `blocks/protection.py`, `blocks/power.py` | U5 ILIM (plateau ≈ 49 mA by design; 8…89 mA with its errors stacked, §5.3) → 100 Ω 2 W R30 (the passive bound that holds whatever ILIM does) → photorelay U12 (open unless both ±15 V rails are up) → TVS D5. F1 (FORCE+) and F2 (FORCE−) are series PTCs; F1 is **not** a backstop at the fault current (§10); F2 limits an external source on J7. D8/D9 clamp a dead ±15 V rail. Rail clamps on the sense inputs |
| Ratiometric accuracy | `blocks/power.py`, `blocks/adc.py` | One REF5025 (2.500 V) feeds the DAC reference (through U11), the ADC reference, and the measurement zero (INA128 REF pins, ADC AINCOM). The sweep midpoint is DAC channel B, so it shares the DAC's reference pin |
| Keep analog and digital apart | `blocks/power.py`, `docs/pcb_generation.md` | Separate AGND/DGND nets and pours, joined only at R31 (0 Ω). Separate SPI buses for DAC and ADC |

The repository states **no numerical accuracy, resolution, noise, speed or
temperature specification** for the instrument. The error-budget notes in this
document are therefore analysis only. They are not requirements.

---

## 2. Design at a glance

| Item | Implemented value | Reference |
|---|---|---|
| MCU | STM32F411RET6 (LQFP-64), 8 MHz HSE crystal | U9, Y2 |
| DAC | DAC8552 16-bit; channel A sweeps, channel B sets the midpoint; VREF = 2.500 V through the U11 gate; powered from +5VA | U4 |
| Sweep amplifier | OPA2277: U3A buffers V_B/2, U3B is a difference amp with gain R4/R3 = 20.0k/2.49k = 8.032 | U3, R1–R6 (0.1 %) |
| Sweep range (V_SWEEP) | −10.040 V … +10.040 V, ≈ 306 µV/LSB. The negative limit is set by hardware; the positive limit holds only while firmware keeps channel B at full scale | TP1 |
| Output safe state | U11 (TMUX1101) connects VREF_2V5 to the DAC only while the PC5 heartbeat keeps DAC_ARM high. Without the heartbeat both DAC outputs are 0 V and V_SWEEP ≈ 0, reached ≈ 50 ms after the heartbeat stops | U11, R35, D6, C47, C48, R36, R37 |
| Output stage | OPA547T power op-amp (drawn with the OPA548T symbol and footprint: same pinout and package), unity-gain follower, feedback taken after R9 (10 Ω) | U5, R9, C19 |
| Current limit | R7 = 348 kΩ → **≈ 49 mA plateau** by design at ±15 V; expected 40…65 mA (≈ 47 mA sourcing / 55 mA sinking with U5 warm by TI's typical curves; 62.6 mA by the SBOS056F equation, which holds at ±30 V and 25 °C); ±10 mA typical, ±30 mA maximum between units | R7, C14 |
| Output network | R38 100 Ω + C49 10 nF from U5's output to AGND, fitted: keeps the current-limit plateau stable with an inductive DUT (TI model) without loading the unity-gain loop (§5.3) | R38, C49 |
| Series elements outside the loop | F1 (PTC, 100 mA hold at 23 °C) + R30 100 Ω 2 W (PR02) + U12 (≤ 2 Ω) + F2 (PTC) + R10 10.00 Ω 0.1 % 20 ppm/°C 3 W | F1, R30, U12, F2, R10 |
| DUT compliance | ±10.04 V at I → 0, about ±4.65 V at 49 mA (less by F1's, F2's and U12's resistance) | §5.4 |
| Current sense | R10 = 10.0 Ω low-side shunt (Vishay RS02B10R00BE12) after F2 in FORCE−, INA128 G = 1 + 50k/35.7k = 2.4006 → **24.0 V/A**, REF on VREF_2V5: `I_SENSE_OUT = 2.500 V + 24.0 V/A·I` | R10, U6, R11 |
| Voltage sense | Kelvin SENSE± → rail clamps → 40.2 kΩ/10.0 kΩ dividers to AGND (k = 0.1992) → INA128 **G = 1** (R14 **DNP**), REF on VREF_2V5: `V_SENSE_OUT = 2.500 V + 0.1992·V_K` | U7, R12–R14, R32, R33, D1–D4 |
| ADC | ADS1256 24-bit, 7.68 MHz crystal, **AIN0 − AINCOM = current, AIN2 − AINCOM = voltage, AINCOM on VREF_2V5**, VREF 2.500 V, PGA 2 (±2.500 V), AVDD 5 V, DVDD 3.3 V | U8, Y1, C44 |
| **Measurable window** | **V: ±11.8 V guaranteed (±12.55 V nominal FS). I: ±97.8 mA guaranteed (±104 mA nominal FS). Both signed; both cover everything the source can apply (±10.04 V, ±91.3 mA)**. These take the ADC window at its limits and the channel gain at its nominal value; with the channel gain and offset at their limits as well they are ±11.76 V and ±97.3 mA. Both need the bench +5 V at 4.75 V or more | §6 |
| Supplies | External bench supply: +15 V, −15 V, +5 V, GND through J1. On board: 3.3 V LDO, ferrite-isolated +5VA, 2.5 V reference | §8 |
| Host interface | USB 2.0 full-speed through a USB-C receptacle (data only; VBUS is sensed but does not power the board). SWD header | J3, J2 |
| DUT connection | Four 4 mm banana jacks: J4 FORCE+, J5 SENSE+, J6 SENSE−, J7 FORCE− | §5.4 |
| Grounds | AGND and DGND joined only at R31 (0 Ω, 0805). U8 is the only IC with pins on both | §8.3 |
| Part count | 132 board parts (131 fitted, 1 DNP: R14), plus 9 schematic-only PWR_FLAGs; 77 nets. The routed board also carries three board-only isolated M3 mounting holes H1–H3 (`docs/final_corrections.md` §2.4) | `component_manifest.md` |
| Firmware | **Not in this repository.** Only the MCU pin assignment is defined | §9 |

---

## 3. System architecture

### 3.1 Block diagram

```
 POWER (Sheet 1)
   J1 (bench) ──+15VA / −15VA─────────────────────────────► U3, U5, U6, U7 (analog amplifiers)
             └──+5V_RAW──┬── FB1 ──► +5VA ──► U4 DAC, U8 AVDD, U2 REF5025 ──► VREF_2V5 (2.500 V)
                         └── U1 LD1117 ──► +3V3D ──► U9 MCU, U8 DVDD;  FB2 ──► VDDA (MCU)
   AGND ══ R31 (0 Ω, the only AGND–DGND link) ══ DGND (J1 pin 4, USB ground)

 CONTROL / FORCE PATH
   U9 STM32F411 ──SPI1 (PA4/5/7, write-only; R34 pulls /SYNC high)──► U4 DAC8552 ch.A ──V_DAC──┐
   U9 PC5 heartbeat ─► R36/C47/D6 pump ─► DAC_ARM (C48, R37) ─► U11 SEL      │                   │
   VREF_2V5 ─► U11 TMUX1101 ─► DAC_VREF (R35 1 MΩ to AGND) ─► U4 VREF        │                   │
                                     U4 DAC8552 ch.B ─V_DACB─► R1/R2 (÷2) ─► U3A buffer ─VREF_HALF─►├─► U3B diff-amp, G = 8.032
                                                                                                │     V_SWEEP (TP1), ±10.04 V
                                                                          ▼
   U5 OPA547 follower (+IN = V_SWEEP) ─OUT─► R9 10 Ω ─► VO_SENSE (TP2) ──┬──► back to U5 −IN (loop closes here)
     ILIM: R7 348 kΩ → ≈49 mA; E/S: R8 → +15VA (always on)               │
                                                                         ▼
   F1 PTC ─► R30 100 Ω ─► U12 photorelay ─► FORCE+ ──┬─► J4 FORCE+ ─────────┐
     (U12 LED: +15VA ─ R39 ─ LED ─ D7 24 V zener ─ −15VA) └─ D5 TVS ─► AGND     │
                                                                           [ DUT ]   (FORCE and SENSE join only at the DUT)
   AGND ◄─ R10 10 Ω shunt ◄─ ISENSE_HI ◄─ F2 PTC ◄─ FORCE− ◄─ J7 FORCE− ───┘
                                  (J5 SENSE+ and J6 SENSE− connect to the DUT terminals)

 MEASUREMENT PATH  (zero at VREF_2V5 = 2.500 V; both channels read against AINCOM)
   ISENSE_HI / AGND (across R10) ──► U6 INA128, G = 2.40, REF = VREF_2V5 ──I_SENSE_OUT (TP4)──► R15/C24 ──► U8 AIN0
   J5 SENSE+ / J6 SENSE− ─► D1–D4 rail clamps ─► R12/R13 40.2 kΩ, R32/R33 10.0 kΩ to AGND (k = 0.1992)
                         ─► U7 INA128, G = 1 (R14 DNP), REF = VREF_2V5 ──V_SENSE_OUT (TP7)──► R17/C25 ──► U8 AIN2
   VREF_2V5 ──► U8 AINCOM (pin 5, beside VREFP pin 4; C44 bypass)     AIN1/AIN3: R16/R18 to AGND, spare self-checks
   U8 ADS1256 (VREFP = VREF_2V5, Y1 7.68 MHz) ──SPI2 (PB12–15) + DRDY (PA8) + RESET (PA9)──► U9

 HOST
   U9 ──USB FS (PA11/PA12, 22 Ω)──► U10 USBLC6 ESD ──► J3 USB-C (data only; VBUS sensed on PA0)
   U9 ──SWD──► J2;  LED1 (sunk by PC13 through R23 1 kΩ);  SW1 reset;  Y2 8 MHz HSE
```

### 3.2 Hierarchical sheets

The schematic has one top sheet and eight child sheets. Each child sheet is
generated from one SKiDL block file.

| Sheet | File (KiCad) | SKiDL source | Contents |
|---|---|---|---|
| Top | `stage1_iv.kicad_sch` | `generators/schematic.py` | Sheet symbols and the cross-sheet nets |
| 1 Power | `power.kicad_sch` | `blocks/power.py` | J1, U1 LDO, FB1, U2 REF5025, bulk caps, **R31 star link** |
| 2 MCU | `mcu.kicad_sch` | `blocks/mcu.py` | U9, Y2, reset/boot, SWD J2, USB-C J3 + U10, LED1, the heartbeat charge pump (R36, C47, D6, C48, R37) |
| 3 DAC/Sweep | `dac_sweep.kicad_sch` | `blocks/dac_sweep.py` | U4 DAC8552, U3 OPA2277, R1–R6, the reference gate U11 + R35 + C46, the /SYNC pull-up R34 |
| 4 Power buffer | `output_buffer.kicad_sch` | `blocks/output_buffer.py` | U5 OPA547T (OPA548T symbol), R7 ILIM, R8 E/S, R9/C19 isolation, R38/C49 output R-C network |
| 5 Current sense | `current_sense.kicad_sch` | `blocks/current_sense.py` | R10 shunt, U6 INA128, R11 |
| 6 Voltage sense | `voltage_sense.kicad_sch` | `blocks/voltage_sense.py` | U7 INA128, R12/R13, R14 (DNP), D1–D4 |
| 7 ADC | `adc.kicad_sch` | `blocks/adc.py` | U8 ADS1256, Y1, input RC filters, pull-ups |
| 8 Protection/DUT | `protection.kicad_sch` | `blocks/protection.py` | F1, R30, U12 + R39 + D7, D5, F2, J4–J7 |

**Reading the schematic.** The schematic is machine-generated. Every symbol is
a generic rectangle with its pins split left and right. Connectivity comes
entirely from **labels**: each pin has a short wire stub that ends in a label
carrying the exact net name. Global labels are used for power, hierarchical
labels for nets that cross sheets, and local labels otherwise. No signal wiring
is drawn, so to follow a path you search by net name. Appendix A lists the
important nets with every node on them.

### 3.3 Ground and power domains

| Domain | Return | Members |
|---|---|---|
| Analog precision | **AGND** | U2 REF5025, U3, U4 DAC, U6/U7 INA128 decoupling (their REF pins are on VREF_2V5), U6 −IN (shunt low side), the R32/R33 attenuator returns, ADS1256 analog side (AGND, VREFN, AIN1/AIN3 returns; AINCOM is on VREF_2V5), crystal load caps C26/C27, R10 shunt return, D5 TVS, ±15 V bulk and decoupling caps, C7 (+5VA bulk), C44 |
| Analog power | ±15VA (bench), +5VA (after FB1) | U3, U5, U6, U7 on ±15VA. U2, U4, U8 AVDD on +5VA |
| Digital | **DGND** | U9, U1 LDO, C1–C4/C8, U10, J3 (GND and shield), J2, SW1, U8 DGND pin, the heartbeat pump (D6 anode, C48, R37), J1 pin 4 (supply return). LED1 is now between +3V3D and PC13, with no DGND pin |
| Bridge | R31 (0 Ω) | The only AGND↔DGND connection. `validation/assertions.py` checks this |

---

## 4. Operating concept

**Inference:** there is no firmware in the repository. The sequence below is
what the hardware allows and requires. It is not taken from existing code.

0. **Arm the output (once per boot).** The MCU loads channel B with full
   scale (V_B = 2.500 V, so VREF_HALF = 1.250 V) and channel A with
   mid-scale, then starts the PC5 software heartbeat. The charge pump raises
   DAC_ARM, U11 closes, and the DAC reference comes up with V_SWEEP ≈ 0. If
   the heartbeat ever stops (reset, debugger halt, crash), U11 opens and
   V_SWEEP returns to ≈ 0 V within about 50 ms. The firmware rules are in
   `docs/firmware_handoff.md`.
1. **Set the sweep point.** The MCU writes a 16-bit code to DAC8552 channel A
   over SPI1, which is write-only. U3B converts the code to
   `V_SWEEP = 8.032·(V_A − V_B/2) = 8.032·(V_DAC − 1.250 V)` with channel B at
   full scale. U5 forces V_SWEEP at node VO_SENSE, after R9.
2. **Current flows through the DUT.** It goes FORCE+ (J4) → DUT → FORCE− (J7)
   → R10 → AGND. Between VO_SENSE and the DUT the current passes F1 and R30,
   which are outside the regulation loop. The voltage actually across the DUT
   is therefore lower than V_SWEEP by `I·(R_F1 + R30)`, and by R10's drop on
   the return side.
3. **The DUT voltage is sensed at the DUT.** The Kelvin leads J5/J6 go
   through the k = 0.1992 attenuator to U7 (G = 1), whose output is
   V_SENSE_OUT = 2.500 V + 0.1992·V_K. The sense leads carry at most ≈200 µA
   (the attenuator), so the Kelvin reading still excludes the lead, F1, R30
   and R10 drops to within µV.
4. **The current is sensed.** U6 amplifies the R10 drop onto the reference:
   I_SENSE_OUT = 2.500 V + 24.0 V/A × I.
5. **Both channels are digitized** by U8 over SPI2, in turn through the input
   multiplexer: AIN2 − AINCOM (voltage), then AIN0 − AINCOM (current), both at
   PGA 2. AINCOM is on VREF_2V5, so the codes are signed. DRDY (PA8) signals
   when a conversion is ready.
6. **Repeat** for the next code. The applied voltage is not the programmed
   voltage, because of the series resistance outside the loop. So firmware
   that needs a specific **DUT** voltage has to iterate on the measured Kelvin
   voltage, or accept the load line. **Inference.**
7. **Current limit.** If the DUT demands more than about 49 mA (expected
   40…65 mA on a given board, §5.3), U5 enters
   current limit and behaves as a current source. The Kelvin and shunt
   measurements stay valid in that state. **Inference.**

Things the hardware does **not** provide (by design or deferral): an output
*disconnect* (U5's E/S pin is tied permanently enabled, MCU control is
"deferred" per `manifest.yaml`, and there is no relay), and range switching
(the ADC's PGA provides digital ranging for small signals, §6). What it does
provide since the audit corrections is a forced **0 V setpoint**: with the
heartbeat stopped, U5 still drives the DUT, but at ≈ 0 V and from a
low-impedance source. It is not an open circuit.

---

## 5. Signal chain, stage by stage

### 5.1 MCU → DAC (SPI1, Sheet 2 → Sheet 3)

| Signal | MCU pin | DAC8552 pin | Net |
|---|---|---|---|
| SCLK | PA5 (U9.21) | SCLK (U4.6) | SCK1 |
| DIN | PA7 (U9.23) | DIN (U4.7) | MOSI1 |
| /SYNC | PA4 (U9.20) | /SYNC (U4.5) | CS_DAC, R34 100 kΩ pull-up to +5VA at the DAC |

- The DAC is on its own SPI bus. The ADC uses SPI2. `validation/assertions.py`
  enforces that the two buses stay separate. The repository records this as a
  design requirement (spec §C.1). **Inference:** the likely reason is to keep
  DAC writes and ADC traffic independent, so neither bus's activity couples
  into the other converter.
- **Logic levels.** U4 runs from **+5VA** and its digital inputs are driven by
  a 3.3 V MCU. The DAC's V_IH at V_DD = 5 V must be met by 3.3 V CMOS
  outputs **[DS-check]** → **R7** in §14.
- **/SYNC pull-up (audit F2).** R34 holds /SYNC high while the MCU's pins
  float (reset, boot, MCU unpowered), so no frame can be clocked into the DAC
  while the reference gate is disarming. It goes to +5VA, the DAC's own rail,
  and is 100 kΩ, so the current into PA4 is ≤ 17 µA whether PA4 is an input
  or driven high at 3.3 V.
- **Ground boundary.** U4 is referenced to AGND, while its SPI drivers are on
  DGND. The SPI1 return current must therefore cross the split through R31.
  This is a layout question → **R20**.

### 5.2 DAC and sweep level shift (Sheet 3)

**Topology (design fact):**

- U4 (DAC8552) VREF is net **DAC_VREF**: VREF_2V5 through the U11 gate, with
  R35 1 MΩ to AGND. VOUTA drives net V_DAC. **VOUTB drives net V_DACB**, the
  top of the midpoint divider.
- R1 = R2 = 10.0 kΩ 0.1 % divide V_DACB to VREF_HALF_RAW = V_B/2 (1.250 V with
  channel B at full scale). **U3A** buffers it to a low-impedance
  **VREF_HALF**.
- **U3B** is a 4-resistor difference amplifier:
  - R3 (2.49 kΩ) from V_DAC to the + input, R4 (20.0 kΩ) from the + input to AGND.
  - R5 (2.49 kΩ) from VREF_HALF to the − input, R6 (20.0 kΩ) from the − input to the output.
  - Output V_SWEEP, test point TP1. U3 runs from ±15VA.

**Transfer function.** With R4/R3 = R6/R5 = 20.0k/2.49k = 8.0321:

```
V_SWEEP = 8.0321 · (V_A − V_B/2),   V_A = VREF_DAC · code_A / 65536,   V_B = VREF_DAC · code_B / 65536
        = 8.0321 · VREF_DAC · (code_A − code_B/2) / 65536
VREF_DAC = 2.500 V while U11 is on (DAC_ARM high), 0 V while it is off
```

With channel B at full scale (code_B = 65535, the firmware contract), V_B/2 =
1.24998 V:

| Code A | V_A | V_SWEEP |
|---:|---:|---:|
| 0 | 0.000 V | −10.040 V |
| 131 | 0.0050 V | −10.000 V |
| 32767 | 1.24996 V | −0.15 mV |
| 32768 | 1.25000 V | +0.15 mV |
| 65404 | 2.49497 V | +10.000 V |
| 65535 | 2.49996 V | +10.040 V |

The zero falls between codes 32767 and 32768, because full scale on channel B
is 65535/65536 of VREF. The ±0.15 mV is half an LSB and calibrates out with the
other offsets.

**What bounds the range.**

| Bound | Mechanism | Class |
|---|---|---|
| −10.04 V | V_A ≥ 0 and V_B ≤ VREF, so no pair of codes can go lower | hardware |
| +10.04 V | Only while channel B is at full scale. With B lower the ceiling rises: B = 0 and A = full scale commands +20.1 V. U3B then saturates somewhere between its guaranteed (V+) − 1.2 V = +13.8 V (SBOS079C) and the +15 V rail, past U5's specified input range of (V+) − 3 V = +12 V (SBOS056F) | **firmware contract** (`docs/firmware_handoff.md`) |

**Why the midpoint comes from channel B (audit F2).** With R1 on VREF_2V5 the
midpoint was a fixed 1.25 V. The DAC8552's guaranteed power-on state is zero
scale (SLAS430A), so from power-up until firmware first wrote the DAC the
output was `8.03·(0 − 1.25 V) = −10.04 V`: full negative drive into the DUT.
With the midpoint on VOUTB, both channels power up at zero scale and
`V_SWEEP = 8.03·(0 − 0) = 0`, apart from residual offsets.

**The reference gate: reset, halt and crash (audit F2).** The DAC8552 has no
reset or clear pin, and it **keeps its codes** through an MCU reset, a
debugger halt or a firmware crash. Its zero-scale state is guaranteed only at
DAC power-up, so a safe power-up does not make a reset safe. The hardware
therefore forces zero another way. The DAC8552 is a resistor-string DAC, so
both outputs are `VREF·code/65536`, and both channels share one VREF pin
(SLAS430A, "Theory of operation").

- **U11** (TMUX1101, SPST, on while SEL = 1) connects VREF_2V5 to that pin only
  while **DAC_ARM** is high. Otherwise the pin is open and **R35** (1 MΩ) pulls
  it to AGND. With VREF = 0 both outputs are 0 V **whatever codes the DAC
  holds**, so V_SWEEP = 0. The midpoint follows the same pin, so the zero
  needs no matching.
- **DAC_ARM is not a GPIO level.** It is the rectified output of a diode charge
  pump on Sheet 2: PC5 → R36 1 kΩ → C47 100 nF → D6 BAT54S → DAC_ARM, which
  holds C48 470 nF and R37 100 kΩ to DGND. PC5 must be **toggled by software**
  (the heartbeat). No DC path exists from any supply to DAC_ARM, so only edges
  transfer charge.
- **Simulated behaviour** (transient model of this circuit, scratch
  simulation; to be measured at bring-up, `docs/bringup_checklist.md`):

  | Condition | DAC_ARM | Against U11 at VDD = 5 V |
  |---|---|---|
  | 1 kHz heartbeat, nominal | 2.42 … 2.46 V | VIH = 1.49 V |
  | 1 kHz, worst case (VOH 3.0 V, diode Vf 0.4 V, 10 µA leakage) | 1.87 V | VIH = 1.49 V |
  | 500 Hz, worst case (half the required rate) | 1.61 V | VIH = 1.49 V |
  | One GPIO edge (a stuck pin) | ≈ 0.53 V | VIL = 0.87 V |
  | Heartbeat stops | falls below 0.87 V within ≈ 52 ms (C48·R37 = 47 ms) | off |

- **PC5 has no timer output** on the STM32F411 (only EVENTOUT and an ADC
  input), so no peripheral can keep toggling it once the CPU stops. That is
  why it was chosen. The heartbeat must come from the main loop, never from a
  timer, ISR or DMA (`docs/firmware_handoff.md`).
- **U11 Ron** (1.8 Ω typ, 4.9 Ω max at 125 °C, SCDS410D) in series with the
  DAC's ≈ 62 kΩ reference input scales both channels by the same 30 … 80 ppm.
  The A/B ratio, and so the zero, is unaffected. The full-scale error
  calibrates out with the rest of the source gain. The TS5A3159A was rejected
  for this position because its VIH (2.4 V at 5 V) is above what the pump can
  guarantee.
- **What the gate does not do.** It forces the **setpoint** to 0 V. U5 stays
  enabled and drives the DUT at ≈ 0 V from a low impedance. It is not a
  disconnect.

- 1 LSB at V_SWEEP = 2.5 V / 65536 × 8.0321 ≈ **306 µV**.
- **Why 20.0 kΩ.** From the source comments: an exact ±10.000 V would need
  R4 = 19.92 kΩ, which is not a standard value. 19.8 kΩ (E192) would only reach
  ±9.94 V. So 20.0 kΩ (E96, same 0.1 % grade as R3/R5) was chosen to *bracket*
  ±10 V, and firmware is expected to leave the end codes unused. The source
  says "the top 0.40 %". In fact about 0.2 % of the codes at *each* end exceed
  ±10.000 V (see the table), nominally. The DAC8552's own limits (zero-code
  error ±12 mV, full-scale error ±0.5 % of range, each channel, SLAS430A)
  times the gain of 8, with the 0.1 % resistors and reference, are larger
  than that 40 mV margin: the span that is guaranteed is about ±9.85 V, and
  about ±10.0 V is expected (`docs/final_audit.md` §6). Firmware clamps the
  command to ±10.0 V and should clamp to the measured span
  (`docs/firmware_handoff.md`).
- **History (from git).** The earlier design used R4 = R6 = 10.0 kΩ (gain 4.016,
  ±5.02 V). Commit `66bdab9` changed them to 20.0 kΩ for ±10 V without changing
  any connectivity.
- **Ratiometric zero.** VREF_HALF is DAC channel B halved, and both DAC
  channels use the same (gated) reference pin. So code_A = code_B/2 gives 0 V
  whatever the exact VREF value, apart from resistor, DAC and op-amp errors.
  The span scales with VREF.
- **Set-point error sources (analysis).**
  - R1/R2 ratio tolerance (0.1 % each) moves VREF_HALF by up to about ±0.6 mV,
    which is about ±5 mV at V_SWEEP.
  - Channel B's own offset and gain error now enter the midpoint, halved
    **[DS-check SLAS430A]**. Channel B is static, so this is a fixed offset
    that the zero calibration removes.
  - The 0.1 % difference-amplifier resistors give up to about ±0.2 % gain error
    and a common-mode error of a few mV at V_CM ≈ 1.25 V.
  - DAC8552 offset and gain errors add to these: ±12 mV zero-code and
    ±0.5 % full-scale error maximum (SLAS430A), up to about ±0.19 V at
    V_SWEEP in all.
  - These errors are small compared with the uncompensated series-resistance
    drop (§5.4). They do not reach the measurement, because the DUT voltage is
    measured independently. **Inference.**

### 5.3 Output power buffer (Sheet 4)

**Topology (design fact):**

| U5 (OPA547T) pin | Net | Connection |
|---|---|---|
| 1 +IN | V_SWEEP | From U3B |
| 2 −IN | VO_SENSE | Feedback from the **far side of R9** |
| 3 ILIM | OPA548_ILIM | R7 348 kΩ 1 % (1206) ‖ C14 10 nF to −15VA |
| 4 V− | −15VA | C16 100 nF 50 V X7R + C18 10 µF/35 V polymer tantalum (TCJB106M035R0200E) to AGND |
| 5 V+ | +15VA | C15 100 nF 50 V X7R + C17 10 µF/35 V polymer tantalum (TCJB106M035R0200E) to AGND |
| 6 OUT | VO_RAW | R9 10 Ω 1 % 0.25 W (1206) to VO_SENSE, C19 100 pF across R9; R38 100 Ω + C49 10 nF to AGND (fitted) |
| 7 E/S | OPA548_ES | R8 10 kΩ to +15VA (always enabled) |

- **The fitted part is an OPA547T (final audit).** The symbol
  (`stage1_iv:OPA548T`), the two net names and the footprint keep their
  OPA548 names: the OPA547 and OPA548 are one TI family with the same seven
  pins in the same 7-lead stagger-formed TO-220 (TI package KVT), so nothing
  on the schematic or the board changes except U5's value and R7. Why the
  smaller part is the correct one is under **Current limit** below.
- **Pin numbering is verified.** The U5 symbol still comes from a
  third-party KiCad library (Alternate KiCad Library at a pinned commit), but
  `tools/author_project_symbols.py` cross-checks every pin against the TI
  SBOS070D "Pin Functions" table (OPA548), kept verbatim in
  `libraries/provenance/opa548_sbos070d_pin_functions.txt`, **and** against
  the TI SBOS056F "Pin Configurations" drawing (OPA547, the part fitted),
  kept verbatim in
  `libraries/provenance/opa547_sbos056f_pin_configurations.txt`, and stops
  the build on any mismatch. All seven pins agree in all three, and U5 is
  flagged `RESOLVED`. No part is flagged `VERIFY-DATASHEET` any more.
- **Footprint.** `stage1_iv:TO-220-7_P2.54x3.7mm_StaggerOdd_Lead3.8mm_Vertical_Drill1.2mm`
  is KiCad's TO-220-7 staggered footprint with the drill widened from 1.1 to
  1.2 mm, generated by `tools/vendor_footprints.py`. That gives more lead
  clearance for the formed leads. The OPA547T and the OPA548T are both TI
  package KVT, so one footprint serves either. The final audit compared it
  with TI's recommended TO-220 solder footprint (SBOS070D Fig. 46): 0.050 in
  lead pitch, two rows 0.150 in apart (3.81 mm; the footprint uses 3.7 mm,
  inside the lead clearance of a 1.2 mm hole), the odd-numbered leads in
  the row away from the tab.
- **Loop.** U5 is a unity-gain follower of V_SWEEP. The feedback is taken after
  R9, so the loop corrects R9's drop. With C19 across R9 this is the usual
  "isolation resistor inside the loop, with a high-frequency feedback path"
  arrangement for driving capacitive loads such as cable and DUT capacitance
  (**Inference** from topology. The repository only cites spec §C.6).
  Everything *after* VO_SENSE (F1, R30, the leads, R10) is **not** corrected.
  Note that the `output_buffer.py` comment "R9's drop is NOT corrected
  downstream" is easy to misread: R9 *is* inside the loop.
- **Current limit (final audit, R4).** The OPA547, OPA548 and OPA549 share one
  limit circuit, `I_LIM = K × 4.75 V / (R_int + R_CL)`, and differ in scale:

  | Part | Limit range | K | R_int | Limit tolerance (TI, 25 °C) |
  |---|---|---:|---:|---|
  | OPA547 (SBOS056F) | 0…750 mA | 5000 | 31.6 kΩ | ±10 mA typ, ±30 mA max, at 375 mA |
  | OPA548 (SBOS070D) | 0…5 A | 15000 | 13.75 kΩ | ±100 mA typ, ±250 mA max, at 2.5 A |

  TI states the limit's tolerance as a current, at one setting. For the
  OPA549 its data sheet says that "offset in the current limit circuitry may
  introduce approximately ±0.25 A variation at low current limit values" and
  directs lower-current applications to the OPA547 or OPA548; it says
  nothing of the kind for the two smaller parts, and taking their tolerance
  as an absolute current at a low setting is the conservative reading used
  here. With the OPA548 and R7 = 1.43 MΩ the 49 mA setting was 1 % of the
  range and a fifth of the ±250 mA maximum tolerance, with a +0.8 mA/K drift
  and, at the lowest setting TI plots, 80 mA less limit at ±15 V than at
  ±30 V: the real limit could be zero (U5 unable to drive the DUT in one
  polarity or both) or far above what the series resistors pass (no
  plateau). TI's OPA548 macromodel shows no limiting below ≈ 87 mA with
  that resistor.

  With the **OPA547**, the equation holds at V_S = ±30 V and a 25 °C case;
  this board runs U5 at ±15 V and self-heated. SBOS056F's typical curves
  (Current Limit vs Supply Voltage, vs Temperature; drawn for the 250, 375
  and 500 mA settings, the same shift on all three) give −12 mA on +I_LIM
  and −4 mA on −I_LIM at ±15 V, and −0.19 mA/K. **R7 = 348 kΩ** is chosen
  for those conditions:
  `I_LIM(equation) = 5000 × 4.75 V / (31.6 kΩ + 348 kΩ) = 62.6 mA`, and

  | U5 case | Sourcing | Sinking |
  |---|---:|---:|
  | 25 °C (cold, pulsed) | 50.6 mA | 58.5 mA |
  | 45 °C (idle, warm) | 46.8 mA | 54.7 mA |
  | 70 °C (held in limit) | 42.0 mA | 50.0 mA |

  if those typical shifts carry down to this setting as fixed currents,
  with ±10 mA typical and ±30 mA maximum between units. That is an
  extrapolation: the curves are typical, and 62.6 mA is below anything TI
  tabulates or plots. If the shifts scaled with the setting the plateau
  would be near +61 / −62 mA. So the **expected plateau is 40…65 mA**. With
  everything stacked (R7's 1 %, rails 14.5…15.5 V, a 25…85 °C case, the
  ±30 mA unit tolerance) the range is **8 mA** to **89 mA**: clear of zero
  and inside the measurable ±97.8 mA. A shorted DUT passes only 79…87 mA
  through the series parts, so a unit in the top few mA of that stack shows
  no plateau into a dead short; that is harmless and is reported.
  `validation/measurement_range.py` recomputes the stack and fails the
  build if its low end is under 5 mA or if a typical unit would show no
  plateau; 453 kΩ, which the bare equation gives for 49 mA, fails the first
  test (−5 mA). The plateau is not a precision quantity: bring-up measures
  it in both polarities and firmware uses the measured value; R7 moves it
  by about 1 mA per 6 kΩ. TI's OPA547 macromodel reads 5…12 mA above the
  equation and has no temperature dependence (+74 / −67 mA in this
  circuit); it settles nothing either way.
  The limit still replaces a discrete comparator (`manifest.yaml`). C14
  (10 nF across R7) is the optional noise capacitor of SBOS056F Fig. 3.
- **Headroom (SBOS056F, guaranteed at I_O = 0.1 A):** the output must reach
  10.04 V + 91.3 mA × 10 Ω = 10.95 V at the passive current bound
  (10.53 V at the 49 mA plateau).
  - Guaranteed swing is (V+) − 1.8 = +13.2 V and (V−) + 1.2 = −13.8 V.
  - The input common-mode limit is (V+) − 3.0 = +12.0 V, against 10.04 V on +IN.
  - The worst margin is 1.96 V (the input range), 2.25 V on the output. This
    holds with the ±15 V rails at nominal; it shrinks if the bench supply is
    set lower.
- **E/S pin (R5).** R8 ties it to +15VA through 10 kΩ. The manifest says MCU
  control of E/S is deferred because it "needs level shifting". SBOS056F
  allows it: for normal operation the pin "can be left open or pulled high
  (at least +2.4 V above the negative rail)", the absolute maximum "Input
  Shutdown Voltage" is V+, and "pulling the E/S pin high (output enabled)
  does not disable internal thermal shutdown". The pull-up does defeat the
  thermal-status *output*, which this design does not use. E/S is **not** the
  safe state: a disabled output still leaks (about 0.1 mA, SBOS056F "Output
  Leakage Current vs Applied Output Voltage", typical), which into an open or
  high-impedance DUT is not "off". The safe state is the DAC reference gate
  (§5.2).
- **Dissipation (analysis, R6).** For a shorted DUT the output stage
  dissipates (V_S − I·R_path)·I, which peaks at V_S²/(4·R_path) whatever the
  limit is: ≤ 0.50 W at 15.5 V with the path at its 121 Ω minimum (0.56 W
  with FORCE+ shorted to AGND, 108 Ω), and ≈ 0.44 W at the nominal 49 mA.
  Quiescent dissipation adds 30 V × 10 mA = 0.30 W typical, 0.47 W maximum
  (SBOS056F). The total, ≤ 0.96 W for a shorted DUT and ≤ 1.02 W for FORCE+
  to AGND, is a 62 K or 66 K rise at the 65 °C/W of a TO-220 with no heat
  sink, so a heat sink is not required. (These bounds are for a passive
  DUT; one that sources voltage of the opposite polarity near the rail
  voltage can take U5 into thermal shutdown.) U5 is a vertical TO-220-7.
  Its tab is electrically connected to V− (SBOS056F), so any metal contact
  is at −15 V; keep the tab clear of the enclosure. A heat sink does not
  fit as placed (C18 and C7 are in the way) (`docs/bringup_checklist.md`).
- **Capacitive loads and stability.** At unity gain the OPA547 has less
  margin with a capacitive load than the OPA548 it replaces: SBOS056F shows
  about 10 % small-signal overshoot with no load capacitance and 50 % at
  4.5 nF (the OPA548: none up to 2.5 nF). As built, U5's direct load is tens
  of pF, and the DUT and its cable sit behind F1 + R30 (≥ 102 Ω), a
  resistive load where it matters; TI's open-loop plot (50 Ω load) reads
  about 60° of phase margin and 14 dB of gain margin, and models fitted to
  TI's curves put this stage at 47…66° (`docs/final_audit.md` §4.1). Do not connect
  a capacitance (a coaxial cable, a ×1 probe, a filter capacitor) directly
  to VO_RAW or TP2; use a ×10 probe.
- **Output R-C network (pads: audit V1; fitted: final audit).** TI: "if the
  OPA547 is intended to be driven into current limit, a R/C network may be
  required" (SBOS056F; its example is 3 Ω + 0.01 µF on an inverting
  gain-of-4 stage, Fig. 11). This design sits in current limit by intent.
  **R38 = 100 Ω and C49 = 10 nF, from VO_RAW to AGND, are fitted.** Without
  them TI's OPA547 macromodel oscillates on the plateau when the DUT is
  inductive (roughly 30 µH…10 mH), up to rail to rail in the negative
  polarity; with them the steady plateau is quiet in every case run,
  inductive, resistive and capacitive. What they do not cure in the model
  is a fast fall of the command out of the *negative* limit with an
  inductive DUT (0.3…10 mH), which leaves U5 oscillating with current
  still in the DUT; ramps do not do it, the immediate-zero paths of the
  safe state do. Until bring-up has tested that exit, an inductive DUT is
  not to be taken into current limit; the remedy, if the board needs it,
  is 10 nF C0G on each of R4 and R6 and takes no copper
  (`docs/final_audit.md` §3.7). C49 is a C0G part: 5 nF is too little in
  the model. The resistor is 100 Ω, not TI's 3 Ω or the 10 Ω first put
  on these pads: on a unity-gain OPA547 a 10 nF capacitor behind a few ohms
  is still a capacitive load at the 1 MHz crossover and costs 10…40° of
  phase margin, whereas behind 100 Ω it is a resistive load no heavier
  than the 50 Ω TI characterises the part with. C49 passes no direct
  current, and its alternating current returns to AGND, not through the
  shunt. Model and data-sheet evidence, checked at bring-up
  (`docs/final_audit.md` §3.7, §4.1). Do not lower R38 below about 47 Ω.

### 5.4 Force path, DUT terminals, compliance (Sheet 8)

**Topology (design fact):**
`VO_SENSE → F1 (PTC, 0ZRM0010FF1E) → F1_MID → R30 100 Ω 1 % 2 W (Vishay PR02, PR02000201000FA100, axial DIN0411) → FORCE_SW → U12 (TLP176AM photorelay) → FORCE+ → J4`.
D5 (SMBJ15CA, bidirectional TVS) is connected from FORCE+ to AGND.
U12's LED is in a series string across the rails, `+15VA → R39 620 Ω → LED →
D7 BZT52B24 → −15VA`, so U12 is guaranteed open while either rail is absent
and closed at the nominal ±15 V rails: from ±14.3 V at 25 °C (data-sheet
limits) and about ±14.75 V at 50 °C (expected), with D7 at its upper
tolerance limit and at its own operating temperature. The bench supplies
are to be set to ±15.0 V ±0.2 V at J1: at ±14.5 V closure holds only to
about 38 °C (`docs/final_audit.md` §3.2; D7 was the looser BZT52C24, which
did not hold the relay closed at nominal rails once its self-heating is
counted).
`J7 FORCE− → F2 (PTC, 0ZRM0010FF1E) → ISENSE_HI` goes to the shunt on
Sheet 5. J5 SENSE+ and J6 SENSE− are wired straight through to Sheet 6.
The four jacks are separate 1-pin 4 mm banana footprints (`Connector:Banana_Jack_1Pin`),
and each has its function (FORCE+, FORCE−, SENSE+, SENSE−) printed beside it on
the front silkscreen. Each is a 6.1 mm plated hole in a 10.16 mm pad, with the
AGND pour 0.3 mm beyond the pad on both faces: the jack must have an M6
thread and metal that reaches the pad, and nothing metallic wider than
10 mm may bear on the board without an insulating washer under it
(`docs/final_audit.md` §6).
FORCE and SENSE therefore meet **only at the DUT**, which is the point of a
Kelvin connection.

**DUT compliance (analysis, consistent with repository comments).**
V_DUT ≈ V_SWEEP − I·(R_F1 + R30 + R_U12 + R_F2 + R10) − lead drops, with R30 + R10 = 110 Ω.

| DUT current | Drop in R30 + R10 | Maximum \|V_DUT\| (V_SWEEP = 10.04 V) |
|---:|---:|---:|
| 1 µA | 0.11 mV | ≈ 10.04 V |
| 1 mA | 0.11 V | ≈ 9.93 V |
| 10 mA | 1.10 V | ≈ 8.94 V |
| 49 mA (the intended plateau) | 5.39 V | ≈ **4.65 V** minus I·(R_F1 + R_U12 + R_F2) |

F1 and F2 are 3.0–7.5 Ω each (Bel: Rmin, R1max); U12 is ≤ 2 Ω. At 49 mA they
cost up to 0.83 V more.

**Why R30 is there.** `protection.py` describes it as a passive dead-short
limiter, one of four protection layers (§10). It costs compliance at high
current, and the repository says so explicitly.

**Power in R30 (audit F3; bound revised in `docs/final_corrections.md` §2.7).**
At the nominal current limit (for example a shorted DUT) R30 dissipates
0.049² × 100 ≈ 0.24 W, and 0.79 W at the 89 mA upper end of the plateau's
spread (§5.3). R30 is not sized for the plateau but for the worst credible
fault, which does not depend on ILIM at all: U5's output driven into a
15.5 V rail with the current set only by the series parts at their
guaranteed minima (R9 −1 %, F1/F2 3.0 Ω, R30 −1 % and hot, U12 0 Ω,
R10 −0.1 %):

```
DUT short:         I ≤ 15.5 V / 121.2 Ω = 128 mA
FORCE+ to AGND:    I ≤ 15.5 V / 108.2 Ω = 143 mA    P(R30) ≤ 1.96 W of 2 W (98 %)
                                                    P(R9) ≤ 0.21 W of 0.25 W, P(R10) ≤ 0.16 W of 3 W
```

`validation/measurement_range.py` checks this envelope against the ratings on
every build. The PR02's own hot-spot rise at 2 W is about 150 K (Rth
75 K/W), so it is mounted axially with its body raised about 3 mm off the
board. It lies along the board's Y axis at (87.75, 75.7) mm, 7 mm (courtyards)
from the precision divider (R12/R13/R32/R33). F1 was moved to the bottom
edge, 14.1 mm body to body from R30 and 22.8 mm from U5 (it was 2.6 mm and
0.9 mm). The original 1206 part (1/4 W) would have been overloaded more than
six times in this fault.

### 5.5 Current sense (Sheet 5)

**Topology (design fact):**

- R10 is a Vishay Dale RS02B10R00BE12: 10.00 Ω 0.1 % 20 ppm/°C, 3 W
  wirewound axial (DIN0614 footprint, 20.32 mm pitch, mounted flat). It sits
  in the **low side**, between ISENSE_HI (J7 through F2) and AGND. Its
  DUT-short dissipation is ≤ 0.16 W (§5.4); the 3 W rating (5× for 5 s) is for
  an external source on J7 until F2 trips (`docs/final_corrections.md` §2.2,
  §2.6). The precision grade was set by the audit: R10 is the current-scale
  element and enters every current reading directly.
- U6 (INA128P, DIP-8): +IN = ISENSE_HI (the shunt high side, also TP3). −IN = AGND. **REF = VREF_2V5.** R11 = 35.7 kΩ 0.1 % across
  RG1/RG2. Supplies are ±15VA with 100 nF 50 V X7R each.
- The output I_SENSE_OUT (TP4) goes to ADS1256 AIN0 through R15 100 Ω and is
  converted against AINCOM (VREF_2V5). C24 1 nF runs from AIN0 to AIN1, which
  R16 100 Ω ties to AGND; AIN1 is no longer part of the measurement.

**Scaling:**

```
G_I = 1 + 50 kΩ / 35.7 kΩ = 2.4006
I_SENSE_OUT = VREF_2V5 + G_I · R10 · I = 2.500 V + 24.006 V/A · I
ADC reads AIN0 − AINCOM = 24.006 V/A · I     → ±1.176 V at ±49 mA, ±2.192 V at ±91.3 mA
```

- **Why G = 2.40.** The gain is sized to the largest current any passive DUT
  can draw, `V_SWEEP / (R30 + R10) = 10.04 V / 110 Ω = 91.3 mA`, which holds
  whatever U5's limit does. The limit is a plateau with a spread (expected
  40…65 mA; 8…89 mA with the maximum unit tolerance stacked on the typical
  supply and temperature shifts, an extrapolation, §5.3), not a
  bound the measurement may rely on, and the gain does not rely on it: it
  covers the passive bound. Measurable: ±97.8 mA guaranteed at the nominal
  channel gain, ±97.3 mA with gain and offset at their limits (ADC pin
  window at AVDD = 4.75 V), ±104.1 mA at PGA 2 full scale. The previous
  G = 4.846 would clip at ±48.5 mA once the zero sits at 2.5 V.
- **Polarity.** Both signs are measured: negative current gives
  I_SENSE_OUT below 2.500 V (down to 0.31 V at −91.3 mA) and a negative ADC
  code. The REF pin draws 12…51 µA from VREF_2V5.
- **Low-current floor (analysis).** With a single 10 Ω range, 1 µA gives only
  10 µV across the shunt. INA128P offset at G = 2.4 is 52 µV typ / 258 µV max
  RTI, i.e. 5 µA / 26 µA, and its drift 0.10 µA/°C typ / 0.88 µA/°C max
  **[DS SBOS051G]**. These are removed by a zero calibration, after which
  drift and noise set the floor. There is no range switching, but the ADC's
  PGA can now be raised (up to 64, full scale ±1.6 mA) for small currents,
  because both inputs sit near 2.5 V at any gain. `protection.py` says the instrument characterizes "diode
  reverse leakage and the sub-threshold knee" at "micro- and milliamp
  currents". Whether the current channel really resolves the currents involved
  should be checked → **R9**.
- **Accuracy contributors.**
  - R10 is now 0.1 %, 20 ppm/°C (RS02B). R11 is 35.7 kΩ 0.1 %; its TCR is not
    specified in its value.
  - The INA128 gain-equation error at G ≈ 2.40 comes on top of that
    **[DS-check]**.
  - Self-heating of R10 is small: 24 mW at ILIM.
  - These go into **R10**.
- **Kelvin connection of the shunt.** In the schematic, U6 −IN, U6 REF and the
  R10 low side are all simply "AGND". The **layout** decides whether U6 senses
  the shunt directly at its pads, or senses the shunt plus some AGND copper
  that also carries the DUT return current → **R16**.

### 5.6 Voltage sense, 4-wire Kelvin (Sheet 6)

**Topology (design fact):**

- SENSE+ (J5, TP5) → R12 40.2 kΩ → U7 +IN, with R32 10.0 kΩ from U7 +IN to
  AGND. SENSE− (J6, TP6) → R13 40.2 kΩ → U7 −IN, with R33 10.0 kΩ to AGND.
  Two matched single-ended dividers, k = 10.0k/50.2k = 0.1992 (all four 0.1 %).
- **Clamps on the jack side of R12/R13:**
  - D1 (1N4148W, SOD-123) from SENSE+ to +15VA and D2 from −15VA to SENSE+.
  - D3 and D4 do the same on SENSE−.
  - The clamps go to the amplifier rails, **not** to AGND. That way legitimate
    signals within the INA128 common-mode range are never clipped
    (`voltage_sense.py`).
- U7 (INA128P): **REF = VREF_2V5.** **R14 across RG1/RG2 is DNP**, so RG is
  open and **G = 1**. DNP is now a real attribute: R14 carries `dnp` and
  `exclude_from_bom` in the schematic and on the board, so it no longer
  appears in a fitted BOM. U7's supplies are ±15VA with 100 nF 50 V X7R each. The output `V_SENSE_OUT = 2.500 V + 0.1992·V_K` (TP7)
  goes through R17 100 Ω to ADS1256 AIN2 and is converted against AINCOM
  (VREF_2V5). C25 1 nF runs from AIN2 to AIN3, which R18 ties to AGND.

**Why an attenuator, and G = 1.**

- The ADC window around the 2.5 V zero is about ±2.35 V (AVDD = 4.75 V), and
  G = 1 is the INA128's minimum, so ±10 V needs an overall gain near 0.2
  ahead of or around the INA. Dividing at the input keeps U7's CMRR, keeps
  VREF_2V5 free of signal current, and presents a low-impedance source to the
  ADC. For a fixed overall gain G·k, the lowest G gives the lowest
  input-referred offset (V_OS = 50 µV + 500 µV/G max), so G stays 1.
- The dividers are single-ended to AGND, not one resistor across the inputs:
  a differential divider would pass V_K/R through the DUT's return and the
  R10 shunt and appear as a parallel conductance in every I–V curve.
- The R14 pads stay on the board as a gain option (§10.2).

**Range limits on this path:**

| Limit | Value |
|---|---|
| Physical Kelvin voltage | ±10.04 V as I → 0, about ±4.65 V at ILIM |
| At U7's inputs | k·V: ±2.0 V differential, common mode < 2.2 V — far inside (V−)+2 … (V+)−2 V **[DS]** |
| U7 output | 0.50 … 4.50 V |
| ADS1256, PGA 2, buffer off | ±2.500 V full scale ↔ ±12.55 V; pin window ↔ **±11.8 V guaranteed** at the nominal channel gain, ±11.76 V with gain and offset at their limits (§6) |

**Loading of the DUT.** Each Kelvin input now has 50.2 kΩ to AGND. SENSE+
draws up to 200 µA at 10 V; it is supplied through FORCE+ and returns directly
to AGND, not through R10, so the current reading is unaffected and the Kelvin
error is 200 µA × the sense-lead resistance (≈20 µV for 0.1 Ω). SENSE−
diverts `I·(R10 + R_F2)/50.2 kΩ` around the shunt: a gain term of 0.035 % at most (F2,
3.0…7.5 Ω, lies between FORCE− and the shunt). D1–D4 leakage
(nA class, rising with temperature **[DS-check]**) is unchanged; on SENSE− it
bypasses the shunt and matters only at nA currents, already below the current
channel's floor (§5.5). With the SENSE leads unplugged, R32/R33 pull U7's
inputs to AGND and the channel reads 0 V instead of saturating.

**Divider matching.** Mismatch between the two dividers converts common mode
into error only through V(SENSE−) ≈ I·(R10 + R_F2): ≤ 5.2 mV at the 92.6 mA
bound for 0.1 % parts with F2 at 7.5 Ω, proportional to I and moving a
little with F2's temperature; bring-up measures it. Use one thin-film
series (≤ 25 ppm/°C) for
R12/R13/R32/R33 so k tracks with temperature.

### 5.7 ADC (Sheet 7)

**Topology (design fact):**

| ADS1256 (U8, SSOP-28) | Connection |
|---|---|
| AVDD (1) | +5VA, C28 100 nF + C29 10 µF tantalum to AGND |
| AGND (2), VREFN (3) | AGND |
| VREFP (4), **AINCOM (5)** | VREF_2V5 (TP8), C44 100 nF bypass to AGND. AINCOM is the negative input of both measurements |
| AIN0 (6) | Current channel: I_SENSE_OUT via R15 100 Ω; C24 1 nF to AIN1 |
| AIN2 (8) | Voltage channel: V_SENSE_OUT via R17 100 Ω; C25 1 nF to AIN3 |
| AIN1 (7), AIN3 (9) | R16/R18 100 Ω to AGND. Not part of either measurement; self-checks (AIN1 − AIN3 = ADC zero, AIN1 − AINCOM = −VREF) |
| AIN4–AIN7 (10–13) | Not connected |
| SYNC/PDWN (14) | Tied to +3V3D (always running) |
| /RESET (15) | RESET_ADC → PA9, 10 kΩ pull-up R20 |
| DVDD (16) / DGND (17) | +3V3D, C30 100 nF / DGND |
| XTAL2 (18), XTAL1/CLKIN (19) | Y1 7.68 MHz, C27/C26 18 pF to **AGND** (SBAS288K Fig. 22 specifies 5–20 pF; they were 22 pF) |
| /CS (20), /DRDY (21), DOUT (22), DIN (23), SCLK (24) | PB12, PA8 (10 kΩ pull-up R19), PB14, PB15, PB13 |
| D0/CLKOUT, D1–D3 (25–28) | Not connected |

- **Input filters.** Each signal input has an RC to AGND through the idle
  AIN1/AIN3 leg: `f_c = 1/(2π · (100 + 100) Ω · 1 nF) ≈ 796 kHz`.
  **Inference:** a light anti-alias and charge-kickback filter; the digital
  filter provides the real band-limiting. AINCOM is decoupled by C44.
- **True differential measurement.** Both channels are converted against
  AINCOM, the same VREF_2V5 net the INA128 REF pins sit on, so the 2.5 V
  shift cancels in the conversion and AGND potential differences between the
  INA128s and the ADC no longer enter the result. AGND still matters at the
  amplifier *inputs* (U6 −IN on the shunt, the R32/R33 returns) → **R16**.
- **Reference and range.** VREFP = VREF_2V5 and VREFN = AGND, so full scale is
  ±2·VREF/PGA = ±2.500 V at PGA = 2, matching the ±2.35…2.6 V absolute-input
  window around 2.5 V (buffer off). Buffer on narrows the window to
  0 … AVDD − 2 V, leaving only +0.25 V above the zero at the minimum AVDD
  of 4.75 V, so it suits only small signals. With the buffer off and a PGA
  above 2, the 100 Ω of R15/R17 against the ADC's input impedance adds a
  gain term and a zero term that follows the +5 V supply
  (`docs/final_audit.md` §6, §7).
- **Pin numbers.** The ADS1256 has no upstream KiCad symbol. The project
  symbol was written from TI SBAS288K ("Terminal Functions", page 5), and the
  excerpt is kept in `libraries/provenance/`. It is flagged `RESOLVED`.
- **Crystal capacitor ground.** The crystal pins (18/19) sit on the
  digital-side row of the package, but C26/C27 return to AGND. Check this
  against the data sheet's recommended connection and against the routed
  split → **R14**.
- **Interface timing (firmware, [DS-check]).** The ADS1256 SCLK rate is
  bounded relative to CLKIN (the author understands this as ≤ f_CLKIN/4 ≈
  1.92 MHz). The `DIGITAL` netclass note says "SPI2 … runs at a few MHz", which
  is worth reconciling with that limit.

---

## 6. Measurement ranges

This is the most important system-level property for a reviewer to understand.

**The sweep hardware is bipolar (±10.04 V, up to ±91.3 mA into any passive
DUT), and so is the measurement chain: every operating point the source can
reach is measured, with its sign.** The +10.04 V end assumes firmware keeps
DAC channel B at full scale (§5.2). If that contract is broken, the positive
output can rise toward the +15 V rail, which is outside this table.
`validation/measurement_range.py` reports it as unmeasurable rather than
including it.

The ADS1256 runs from a single +5 V analog supply, so each input pin must stay
inside AGND − 0.1 V … AVDD + 0.1 V with the input buffer off, or AGND …
AVDD − 2 V with it on, at any PGA **[DS SBAS288K]**. Its *differential* range,
±2·VREF/PGA, is bipolar. The design therefore puts the measurement zero at the
2.500 V reference: both INA128 REF pins and the ADC's AINCOM are on VREF_2V5,
and each channel is converted as AINx − AINCOM.

| Quantity | Physically applied | Presented to ADC (AINx; AINx − AINCOM) | Measurable (PGA 2, buffer off) |
|---|---|---|---|
| DUT voltage V_K | −10.04 … +10.04 V | 0.50 … 4.50 V; 0.1992·V_K = ±2.000 V | **±11.8 V guaranteed** at AVDD = 4.75 V (nominal channel gain; ±11.76 V with gain and offset at their limits); ±12.55 V at nominal full scale |
| DUT current I | −91.3 … +91.3 mA (any passive DUT, any ILIM) | 0.31 … 4.69 V; 24.0 V/A·I = ±2.19 V | **±97.8 mA guaranteed** (nominal channel gain; ±97.3 mA with gain and offset at their limits); ±104.1 mA at nominal full scale |
| Worst-case tolerances stacked | V_SWEEP +0.5 %, R30 ±1 %, R10 ±0.1 %, INA128 gain ±0.5 %, VREF ±0.1 %, INA128 offset | AIN span V 0.484…4.516 V, I 0.265…4.735 V | headroom to the pin limit: V 334 mV, I 115 mV |

ADC codes (PGA 2, full scale 2²³ = 8 388 608): V_K = −10.04 / 0 / +10.04 V →
−6 710 886 / 0 / +6 710 886; I = −91.3 / 0 / +91.3 mA → −7 354 163 / 0 /
+7 354 163.

Consequences:

1. **All four quadrants are measurable**, over the whole span the source can
   apply. A diode's reverse and forward branches, and a full
   −V → +V → −V hysteresis trajectory, are recorded with the DUT in one
   orientation. `validation/measurement_range.py` recomputes this from the
   part values on every build and fails it if any reachable point would
   over-range.
2. **No ADC input leaves its absolute window in normal operation**, in either
   polarity. (The previous wiring drove AIN0/AIN2 toward −2.4 V and −10 V
   through 100 Ω during every negative sweep, so the ADC's input protection
   conducted continuously, limited only by 100 Ω and the INA128 output
   current limit.) Fault cases —
   external voltage on the jacks, a failed U5 — can still overdrive the inputs
   through 100 Ω → **R15**.
3. **One PGA for both channels.** PGA 2 fits both; no PGA switch or
   recalibration is needed between the V and I conversions of a sweep point.
   Higher PGA (up to 64) is legal for small signals, since both inputs sit
   near 2.5 V whatever the gain.
4. **Cost of the ±10 V span.** The attenuator makes the INA128 offset and
   drift five times larger referred to the DUT (0.55 mV typ / 2.76 mV max,
   11 µV/°C typ), and the lower current gain raises current drift ≈1.7× (typ).
   A zero calibration at the start of each session removes the offsets; only
   drift within the session remains.

The derivation, the rejected alternatives and the hysteresis argument are in
[`docs/bipolar_measurement.md`](docs/bipolar_measurement.md).

## 7. Shared voltage reference and the ratiometric scheme

- U2 is a REF5025AIDGKT (VSSOP-8).
  - VIN = +5VA (C10 100 nF). VOUT → **VREF_2V5** with C9 4.7 µF 10 V X7R.
    SBOS410O specifies stability for 1 … 50 µF with ESR ≤ 1.5 Ω. The
    original 1 µF sat on the minimum, so tolerance and DC-bias loss could take
    a real MLCC below it (audit).
  - TRIM/NR, TEMP and the NC/DNC pins are not connected. So there is **no
    noise-reduction capacitor on TRIM/NR**. That is optional per the data
    sheet **[DS-check]** → **R12**.
- Loads on VREF_2V5:
  - the U4 DAC reference input (code-dependent), through U11 while armed;
    R35 (1 MΩ, 2.5 µA) sits on the DAC side of U11,
  - the U8 VREFP switched-capacitor input,
  - the U6 and U7 REF pins, 4…56 µA each (`(VREF − A2out)/80 kΩ`), far below
    the REF5025's ±10 mA; the INA128 needs REF driven from a low impedance
    (8 Ω in series costs ≈80 dB CMR at G = 1 **[DS]**), which a reference
    output is,
  - the U8 AINCOM input (the negative input of both conversions), bypassed
    locally by C44 100 nF.
- **What sharing the reference buys (analysis).**
  - V_SWEEP is fully ratiometric: its zero sits at code_A = code_B/2 (both
    channels share the DAC reference pin) and its span is proportional to
    VREF.
  - Both ADC readings are proportional to 1/VREF, so V and I carry the
    **same** scale error. The measurement zero is VREF_2V5 itself on both
    sides of the conversion (INA128 REF and AINCOM), so VREF's absolute
    value and its noise cancel from the offset. Any **ratio** of them, such as resistance or
    dynamic resistance, is independent of VREF's absolute error. The
    absolute V and I values still depend on VREF's accuracy and drift
    **[DS-check grade A specs]**.
- **Coupling risk (Inference).** The ADC's sampled reference input, its
  AINCOM input, the two INA128 REF pins and the DAC's code-dependent reference
  current share one node. C9 (4.7 µF) sits at U2 and C44 (100 nF) at U8. The
  REF-pin traces carry only µA, so their DC drop is µV and calibrates out, but
  they should be routed away from digital and FORCE copper → **R12, R18**.

---

## 8. Power architecture and grounding

### 8.1 Rails

| Net | Source | Loads | Local bulk / decoupling |
|---|---|---|---|
| +15VA | Bench supply via J1 pin 1, **unregulated on board** | U3, U5, U6, U7, D1/D3 clamps, R8 | C5 100 µF/25 V (to AGND). C11, C15, C17, C20, C22 |
| −15VA | Bench supply via J1 pin 2, unregulated | U3, U5 (and R7 ILIM), U6, U7, D2/D4 | C6 100 µF/25 V. C12, C16, C18, C21, C23, C14 |
| +5V_RAW | Bench supply via J1 pin 3 | U1 input, FB1 | C1 10 µF, C3 100 nF (to DGND) |
| +5VA | +5V_RAW through FB1 (BLM21PG221SN1D) | U2, U4, U8 AVDD, U11, R34 (/SYNC pull-up) | C7 100 µF (AGND), C10, C13, C28, C29, C46 |
| +3V3D | U1 LD1117S33 from +5V_RAW | U9, U8 DVDD, SYNC/PDWN, pull-ups, LED1 anode, J2 pin 4 | C2 10 µF, C4, C8 100 µF, C34–C37, C39, C30 |
| VDDA | +3V3D through FB2 | U9 VDDA | C38 100 nF, C40 1 µF |
| VREF_2V5 | U2 | U11 → DAC_VREF → U4; U8 VREFP and AINCOM; U6/U7 REF | C9 4.7 µF (REF5025 needs 1 … 50 µF, SBOS410O) |
| DAC_VREF | VREF_2V5 through U11, only while DAC_ARM is high | U4 VREF | R35 1 MΩ to AGND (pulls it to 0 V when U11 is off) |
| VBUS | USB host via J3 | **Sensing only:** U10 VBUS pin, R28/R29 divider to PA0, C42 | — |

Observations (design facts, with consequences as inference):

- **The board is powered only by the bench supply.** VBUS powers nothing. The
  `mcu.py` comment on the USB shield used to call the board "bus-powered"; it
  has been corrected (§13.2).
- **J1 is labelled per terminal.** The front silkscreen carries a "BENCH
  SUPPLY" heading and +15V / −15V / +5V / GND at the four pins, and J1's value
  reads `BENCH SUPPLY 1:+15V 2:-15V 3:+5V 4:GND`. There is still no
  reverse-polarity or mis-connection protection (below), so the labels are the
  only guard.
- There is **no on-board regulation, input fuse, reverse-polarity protection
  or sequencing** on ±15 V or +5 V. Analog performance (INA128 and U5
  PSRR) depends directly on the quality of the bench supply → **R11**.
- U1 is fed from +5V_RAW, on the digital side of FB1. So digital load current
  never flows through FB1, as intended in `power.py`.
- Ratings. The electrolytic and tantalum voltage ratings were chosen for
  derating (`docs/silkscreen_and_packages.md`):
  - 25 V parts on the 15 V rails,
  - 35 V tantalums at U5,
  - C1 is 10 µF/25 V on 5 V.
- The "PWR_FLAG" symbols (#FLG1–#FLG9) are ERC annotations only. They mark
  rails that enter from off-board, plus DAC_VREF, which is fed through U11's
  passive switch pins. They do not appear on the PCB.

### 8.2 Power sequencing and abnormal states (Inference; review)

- **Power-up, reset, halt and crash output state (resolved, audit F2).**
  The DAC8552 powers up at zero scale (SLAS430A). With the original fixed
  1.25 V midpoint that meant V_SWEEP ≈ −10.04 V into the DUT until firmware
  wrote mid-scale, and the DAC kept whatever code it held through an MCU
  reset, halt or hang. Now:
  - at power-up both channels are at zero scale and the midpoint follows
    channel B, so V_SWEEP ≈ 0;
  - and, independently of the codes, the DAC reference is connected only
    while the software heartbeat runs. Reset, halt, crash, a stuck PC5 or an
    unpowered MCU opens U11 within ≈ 50 ms, and V_SWEEP returns to ≈ 0 V.
  The residual window is the ≈ 50 ms disarm time, during which the last
  commanded voltage decays to zero. U5 stays enabled, so the DUT sees ≈ 0 V
  from a low impedance, not an open circuit.
- **Partial supply states (`docs/final_corrections.md` §2.5).** With one
  ±15 V rail absent, the amplifiers' quiescent current used to drag the dead
  rail toward the live one, forward-biasing the sense clamps into the DUT, and
  U5 ran single-supply. Now D8/D9 hold a dead rail within a Schottky drop
  (≤ 0.51 V) of AGND, and U12 is guaranteed open (FORCE+ disconnected) unless
  both rails are up; it is closed at the nominal ±15 V rails
  (from ±14.3 V at 25 °C, about ±14.75 V at 50 °C; §5.4). ±15 V present
  without +5 V leaves the DAC unpowered (V_SWEEP ≈ 0) and the INA128 outputs
  driving an unpowered ADC through its input filter resistors; +5 V without
  ±15 V has no drive at all. The switching transients themselves are measured
  at bring-up.

### 8.3 Star ground

- AGND and DGND are separate nets. **R31 (0 Ω, 0805) is the only resistive
  link.** `validation/assertions.py` enforces this. U8 is the only IC with
  pins on both nets (AGND pins 2, 3 and 5; DGND pin 17), which is allowed.
- The supply return enters on **DGND** at J1 pin 4. So **all analog return
  current crosses R31**:
  - the DUT current, up to ±49 mA, returning through R10 → AGND,
  - the ±15 V bulk and decoupling currents,
  - the +5VA loads.
- The USB shield and ground are on DGND. The host PC's ground is therefore
  connected, through R31, to the analog ground and to the DUT's FORCE−
  terminal. **Inference:** the DUT is not floating relative to the PC or earth
  ground. Keep this in mind for DUTs that are themselves grounded → **R11**.
- Placing R31 is a layout decision. The repository's generated board puts it
  in a straddle lane beside U8, next to where U8's AGND and DGND pins meet.

---

## 9. MCU, digital interfaces and USB

### 9.1 Pin assignment (the only "firmware interface definition" in the repository)

| Function | U9 pin | Net | Notes |
|---|---|---|---|
| SPI1 SCK / MOSI / NSS → DAC | PA5 / PA7 / PA4 | SCK1 / MOSI1 / CS_DAC | Write-only (no MISO) |
| SPI2 SCK / MISO / MOSI / NSS → ADC | PB13 / PB14 / PB15 / PB12 | SCK2 / MISO2 / MOSI2 / CS_ADC | |
| ADC /DRDY | PA8 | DRDY | 10 kΩ pull-up R19 |
| ADC /RESET | PA9 | RESET_ADC | 10 kΩ pull-up R20. See the note below |
| USB D− / D+ | PA11 / PA12 | USB_DM / USB_DP | 22 Ω series R26/R27 → U10 → J3 |
| VBUS sense | PA0 | VBUS_SENSE | R28/R29 100k/100k → 2.5 V when VBUS = 5 V |
| Status LED | PC13 | LED_STATUS | **Sunk:** +3V3D → LED1 (green) → R23 1 kΩ → PC13. PC13 low = on, ≈ 1.3 mA |
| Safe-state heartbeat | PC5 | SAFE_HB | Software-toggled square wave, ≥ 1 kHz, into the R36/C47/D6 charge pump → DAC_ARM → U11 SEL. Must come from the main loop, never a timer (`docs/firmware_handoff.md`) |
| SWD | PA13 / PA14, NRST | SWDIO / SWCLK | J2: 1 SWDIO, 2 SWCLK, 3 GND, 4 +3V3D, 5 NRST |
| HSE | PH0 / PH1 | OSC_IN / OSC_OUT | Y2 8 MHz, C32/C33 20 pF |
| Reset / boot | NRST, BOOT0 | | R21 10k pull-up + C41 100 nF + SW1. BOOT0 has a 10k pull-down (R22) |
| Core | VCAP1 → C43 4.7 µF 10 V X5R. VBAT tied to +3V3D. VSSA on DGND | | |
| Unused | 32 GPIOs declared not connected | | Listed in `blocks/mcu.py` |

Notes and review points:

- **PA9 / USB VBUS sensing.** On STM32F4 parts, PA9 is the OTG_FS VBUS-sense
  pin **[DS-check]**. This design uses PA9 as an ADC reset output and senses
  VBUS on PA0 through a divider. **Inference:** firmware must disable the USB
  peripheral's hardware VBUS sensing and handle VBUS detection itself.
- **PC13 LED drive (resolved).** DS10314 says PC13–PC15 can sink only 3 mA
  and "must not be used as a current source (e.g. to drive an LED)". LED1 is
  now sunk by PC13 through R23 = 1 kΩ, about 1.3 mA (green V_F ≈ 2 V): visible,
  and well inside the 3 mA sink limit. Firmware drives PC13 low to light it.
- **C43 (VCAP1) = 4.7 µF (resolved, audit F4).** The LQFP64 has a single VCAP
  pin (VCAP_1, pin 30), and DS10314 Table 16 asks for 4.7 µF with ESR < 1 Ω on
  a single VCAP pin (2 × 2.2 µF is the two-pin case). C43 was 2.2 µF.
- **/SYNC pull-up.** R34 (100 kΩ to +5VA) holds the DAC's /SYNC high while
  PA4 floats; see §5.1.
- **Firmware loading.** BOOT0 is hard-wired low (no jumper). Firmware is
  therefore loaded over SWD (J2). The ROM USB DFU bootloader is not reachable
  without rework. **Inference.**
- **USB.**
  - The connector is a USB 2.0 Type-C receptacle (GCT USB4085) with 5.1 kΩ Rd
    on CC1/CC2, so the board acts as a device/UFP.
  - D+ and D− are each tied across both plug orientations, which is checked
    in `validation/assertions.py`.
  - U10 (USBLC6-2SC6) is a flow-through ESD array. The "_ESD" and "_CONN" nets
    on each line are the two ends of one internal path.
  - SBU1/SBU2 are not connected. The shield is tied directly to DGND; the
    rationale is in `mcu.py`.

---

## 10. Protection and fault behavior

### 10.1 Protection layers on FORCE+ and FORCE− (as described in `protection.py`)

| Layer | Part | Intended role (repository) | Observations (analysis) |
|---|---|---|---|
| 1 | U5 ILIM (R7) | Normal current plateau (≈ 49 mA) | OPA547, SBOS056F: expected 40…65 mA with polarity and U5's temperature, ±10 mA typical and ±30 mA maximum between units, 8…89 mA with everything stacked (§5.3). A plateau, not a bound: nothing else relies on its value. U5 also has internal thermal shutdown |
| 2 | F1 PTC 0ZRM0010FF1E | Series PTC. **Not a backstop** | Holds 100 mA and trips at 200 mA at 23 °C. The whole fault envelope (≤ 143 mA) lies between the two, where the data sheet promises neither. Hold current derates to 80 mA at 40 °C, 68 mA at 50 °C, 56 mA at 60 °C and 45 mA at 70 °C. So on the **normal** plateau it can trip above about 65 °C ambient at 49 mA, above about 57 °C at the typical upper end (59 mA) and above about 32 °C at the far upper end of the spread (89 mA). A trip there is benign and self-resetting: the current falls, and V and I are still measured. Contingency if bench tests show nuisance trips: 0ZRM0017FF1E (170 mA hold, same body and footprint), as a conditional swap only |
| 3 | R30 100 Ω 1 % 2 W, Vishay PR02 | **The current bound**, passive | Bounds a DUT short to 128 mA and FORCE+ to AGND to 143 mA, carrying up to 1.96 W (98 % of rating) in that stacked worst case (§5.4) |
| 3a | U12 TLP176AM photorelay | Disconnects FORCE+ unless both ±15 V rails are up | Guaranteed open with either rail absent, closed at the nominal ±15 V rails (from ±14.3 V at 25 °C, about ±14.75 V at 50 °C; rails to be set to ±15.0 V ±0.2 V); ≤ 2 Ω, 0.7 A when on (`docs/final_corrections.md` §2.5, `docs/final_audit.md` §3.2) |
| 4 | D5 SMBJ15CA to AGND | Absorbs transients. The 15 V stand-off clears the ±10.04 V sweep | Its leakage returns to AGND without passing through the DUT or R10, so it does **not** corrupt the current reading. **Inference** |
| 5 | F2 PTC 0ZRM0010FF1E in FORCE− | Limits an external source on J7 ahead of the shunt | ≤ 1.19 A at 15.5 V (Imax 2 A); R10 carries ≤ 14.3 W until it trips, inside its 5 × 3 W short-time rating. Nothing is connected across or around the shunt (`docs/final_corrections.md` §2.6) |

**Fault scenarios (analysis, for review):**

| Scenario | Expected behavior | Point to check |
|---|---|---|
| DUT short, ILIM working | I ≈ 49 mA (8…89 mA over the plateau's whole spread). R30 about 0.24 W (≤ 0.79 W), U5 about 0.44 W (≤ 0.50 W) plus 0.30 W quiescent (0.47 W maximum) | U5 temperature (R6): about 48 K typical, ≤ 62 K rise with no heat sink (≤ 66 K with FORCE+ shorted to AGND) |
| DUT short, ILIM failed or set wrong, or U5 driven into a rail | Current is limited only by the series parts at their minima: 15.5 V / 121.2 Ω = 128 mA (DUT short), 143 mA (FORCE+ to AGND). R30 ≤ 1.96 W (rated 2 W), R9 ≤ 0.21 W, R10 ≤ 0.16 W. F1 may or may not trip | Checked on every build by `validation/measurement_range.py`. The PR02 runs hot, so it is mounted raised and 7 mm from the divider; F1 is 14 mm from it (§5.4) |
| External voltage applied to FORCE+ (charged capacitor, powered DUT) | D5 clamps a transient. **Nothing on the board limits D5's sustained current**: F1, R30 and U12 are on the amplifier side of it. D5 holds about 1.25 W continuously (about 70 mA at its clamp voltage); a stiff source above 16.7…18.5 V destroys it. Outside the envelope (`docs/final_audit.md` §6) | A stored-energy source is absorbed (100 µF at 30 V is about 24 mJ into a 600 W part). U5's output is back-driven only through R30/F1 |
| External voltage applied to FORCE− | F2 limits it: ≤ 1.19 A at 15.5 V until F2 trips (typically < 1 s), R10 ≤ 14.3 W against 15 W for 5 s; the shunt node reaches ≈ 12 V, inside the INA128's ±40 V input rating. Above ≈ 26 V F2's 2 A Imax is exceeded: outside the board's envelope | Recalibrate R10 after such an event (`docs/final_corrections.md` §2.6) |
| Overvoltage on SENSE± | D1–D4 clamp the jack-side node to ±15VA. R12/R13 (40.2 kΩ) and R32/R33 then divide it: U7 sees at most ≈3.1 V | The clamps sit **before** the series resistors, so fault current goes straight into the ±15 V rails through the 1N4148Ws. The bench supply may not sink that current (R15) |
| Board unpowered, or one ±15 V rail absent, DUT connected | U12 is open, so U5 is disconnected from FORCE+. With a **passive** DUT nothing flows. D8/D9 only stop a dead rail being pulled to the wrong polarity: a DUT that **holds voltage** forward-biases D1–D4 from the SENSE jacks, charges the rail capacitors and feeds the analog section, limited only by F2 + R10 and by nothing if it shares ground with the board. Rule: power the board first, disconnect before power-off, DUT floating, never above ±15 V | Bring-up §5; `docs/final_audit.md` §6 |
| +5 V collapsing before ±15 V while the output is armed | U12 interlocks only the ±15 V rails, and the gate stays armed for 17…79 ms after the MCU stops, while the DAC and the reference pass through their unspecified supply region: a positive excursion of a volt or two at the DUT for tens of milliseconds is plausible (bounded by 8 × the falling +5 V) | Rule: `DISARM` before switching off; switch ±15 V off first or all rails together. Measured at bring-up §5–§6 (`docs/final_audit.md` §6) |
| Power-up / MCU reset / halt / crash | V_SWEEP ≈ 0 at power-up (midpoint from channel B). On reset, halt or crash the heartbeat stops and U11 removes the DAC reference within ≈ 50 ms (§5.2, §8.2) | Measured at bring-up (`docs/bringup_checklist.md`) |
| **Inductive DUT (0.3…10 mH) held in negative current limit, and the command falls at once** (firmware cut-off or fault handler writes zero, heartbeat lost, reference gated) | In TI's model U5 is left oscillating (4…14 V p-p at 150…230 kHz at its output) with about 60 mA still in the DUT although the command is 0 V. A ramp out of the limit does not do it, nor does the positive polarity or a resistive or capacitive DUT. Model evidence, not measured; the stated DUTs are not inductive | Rule: no inductive DUT in current limit until `docs/bringup_checklist.md` §7 has tested this exit. Way out of the state: re-enter the limit and ramp out, or switch ±15 V off. Remedy without copper if the board does it: 10 nF C0G on each of R4 and R6 (`docs/final_audit.md` §3.7) |
| Firmware loads channel B below full scale while armed | The positive ceiling rises above +10.04 V, up to U3B's saturation (+13.8 V … +15 V) | Firmware contract, not hardware protection (`docs/firmware_handoff.md`) |

### 10.2 DNP and configuration options

| Ref | Fitted state | Effect | Notes |
|---|---|---|---|
| **R14** (0603 across U7 RG) | **DNP**. Value text is "DNP (G=1)" | U7 G = 1; with the k = 0.1992 attenuator the Kelvin range is ±11.8 V | DNP is a real attribute now: the symbol has `(dnp yes)` and `(in_bom no)`, and the footprint carries `dnp` and `exclude_from_bom`, so BOM and pick-and-place exports leave it out. If fitted, G = 1 + 50k/R14 and the measurable range shrinks to ±11.8/G V (useful only if the sweep is restricted to match). The pads connect to the high-impedance RG nodes, so contamination across the empty pads adds a leakage term to RG. For example, 100 MΩ of leakage gives a +0.05 % gain error |
| R11 35.7 kΩ 0.1 % | Fitted | U6 G = 2.4006, 24.0 V/A | Sized so ±91.3 mA (the physical current bound) stays on-scale; `validation/measurement_range.py` fails the build if it is raised past what the ADC window allows. There is no range switching |
| R12/R13 40.2 kΩ, R32/R33 10.0 kΩ, all 0.1 % | Fitted | Kelvin attenuator k = 0.1992 | The two legs must stay equal (checked by the build); k's tolerance is the voltage channel's gain tolerance |
| R4/R6 20.0 kΩ | Fitted | ±10.04 V sweep | 10.0 kΩ gives ±5.02 V, the previous configuration |
| R7 348 kΩ 1 % | Fitted | Plateau ≈ 49 mA at ±15 V (62.6 mA by the OPA547 equation at ±30 V, 25 °C) | Sets the current plateau; about 6 kΩ per mA if one unit is to be moved. `validation/measurement_range.py` fails the build if the plateau's worst-case stack (R7 tolerance, low rail, hot case, maximum unit tolerance) falls under 5 mA, or if a typical unit would show no plateau |
| DAC channel B (U4 VOUTB) | Fitted, drives V_DACB | Sets the sweep midpoint (V_B/2) | Must be loaded to full scale while armed (firmware contract) |
| **R38 / C49** (0805, U5 OUT to AGND) | Fitted (DNP until the final audit) | Output R-C network, 100 Ω + 10 nF | Keeps the steady current-limit plateau stable with an inductive DUT (TI model); it does not make a fast exit from the negative limit safe for such a DUT (§5.3). Not TI's 3 Ω + 0.01 µF and not the earlier 10 Ω: this is a unity-gain stage. C49 is C0G. Removing R38 returns the stage to the unfitted state |
| ADS1256 AIN4–AIN7, D0–D3 | Not connected | Spare | These could only be used after rework, because the pads are not connected |
| U5 E/S | Tied enabled through R8 | No software shutdown | "Deferred extension point" (`output_buffer.py`). Not a safe state anyway: a disabled output still leaks (about 0.1 mA typical, SBOS056F) |

There are no jumpers or solder-bridge options in the design.

---

## 11. PCB and layout considerations

### 11.1 What is in the repository versus what you received

> **Update.** The table compares the earlier *auto-placed* board (now only
> produced by `python3 main.py --autoplace`) with the human board. The board
> of record, `stage1_iv.kicad_pcb`, is now the human board regenerated from
> `layout/` with the bipolar ECO applied (`docs/physical_design.md`): the
> right-hand column, plus R32/R33 and the three REF/AINCOM routes. The audit
> corrections ECO (`layout/audit_corrections.eco`) is applied on top; see
> `docs/audit_corrections.md`.

| Aspect | Auto-placed board (`--autoplace`, bootstrap only) | Routed board (human; now the board of record) |
|---|---|---|
| Routing | **None** (0 tracks, 253 unrouted connections) | Fully routed: 897 tracks, 113 vias with all four ECOs (849/94 on the unipolar original) |
| Placement | Automatic shelf-packing by sheet. Not optimized | Significantly re-placed |
| Outline | 132.2 × 117.8 mm rectangle, 4 × M3 holes inset 5.5 mm | 107.3 × 62.3 mm (`unipolar-routed/diodes.kicad_pcb` Edge.Cuts) |
| Layers | 2-layer, 1 oz, 1.6 mm FR-4 (assumed fab, `docs/FAB_CONSTRAINTS.md`) | Check |
| Ground pours | AGND zone (left) and DGND zone (right), **both layers**, 2 mm gap at x ≈ 52.2–54.2 mm. R31 and U8 straddle the gap | Check whether the routed board kept this strategy |
| Netclasses / rules | In `stage1_iv.kicad_pro` and `stage1_iv.kicad_dru` (generated from `libraries/netclasses.py`) | Not inherited: one `Default` class, 0.20 mm, and clearance checking switched off in its project. Clean at 0.20 mm with it on. Under the repository policy the original routing had 109 clearance errors at this point; `layout/policy_clearance.eco` re-routes them and the board of record is now clean under the policy too (`docs/final_corrections.md` §2.3) |
| KiCad version | Board schema 20241229, which carries a `(units …)` block; the repository says **KiCad 10** is required | — |

Because placement changed, ignore the repository board's physical
arrangement. For example, in the generated board the power block — U2 REF5025
and the AGND-returned C5/C6/C7/C9/C10 — and most ADC support parts sit over
the DGND pour. The **netclasses, the custom DRC rule and the split-ground
intent** are the parts that carry over.

### 11.2 Board setup carried by the project file (design fact)

| Netclass | Nets | Clearance / track (mm) | Rationale (repository) |
|---|---|---|---|
| POWER_HV | +15VA, −15VA, SSR_LED_A/K (the U12 LED string across them) | 0.30 / 0.50 | 2× the fab minimum, for the 30 V rail-to-rail |
| POWER_LV | +5V_RAW, +5VA, +3V3D, VDDA, VBUS, VCAP1 | 0.20 / 0.40 | Covers U1's 800 mA rating |
| GROUND | AGND, DGND | 0.20 / 0.40 | Poured. Width applies to stitching and the R31 link |
| FORCE | FORCE+, FORCE−, VO_RAW, VO_SENSE, F1_MID, FORCE_SW, ISENSE_HI, VO_SNUB | 0.25 / 0.50 | Sized for the fault case (143 mA envelope; ISENSE_HI up to 1.19 A until F2 trips) |
| SENSE | SENSE±, VREF_2V5, DAC_VREF, V_DAC, V_DACB, VREF_HALF, V_SWEEP, I_SENSE_OUT, V_SENSE_OUT | 0.25 / 0.25 | High-impedance precision. Extra clearance for surface leakage |
| DIGITAL | SPI1/SPI2, DRDY, RESET_ADC, SWD, NRST, BOOT0, LED, VBUS_SENSE, SAFE_HB*, DAC_ARM | 0.20 / 0.25 | Density compromise around the LQFP-64 and SSOP-28 |
| USB | USB_DM/DP and their _ESD/_CONN segments | 0.20 / 0.38, pair gap 0.20 | About 100–110 Ω on 2 layers. A **stated compromise** for full speed |
| LOCAL_ANALOG | 19 block-local analog nodes (RG, filter, crystal, ILIM …) | 0.25 / 0.25 | Same character as SENSE. VCAP1, CC1/CC2 and LED_K were moved out (to POWER_LV / DIGITAL): none is a high-impedance analog node |

`stage1_iv.kicad_dru` narrows pad-to-pad clearance to 0.13 mm **inside** J3,
U2, U4 and U9 only. Their intrinsic pad gaps (0.15–0.20 mm) are below the
netclass values. This is not a global relaxation.

### 11.3 Layout-critical features to verify on the routed board

These follow from the schematic's topology. They are listed as things the
reviewer should look at.

1. **Ground split integrity.**
   - AGND and DGND must connect only through R31. No stray via, pour overlap,
     or ground pin of an analog part on the wrong pour.
   - Check that the parts belong on the pour their grounds connect to: U2,
     C5–C7, C9/C10, the ADC input RC and C28/C29 on AGND; U1, C1–C4 and C8 on
     DGND.
   - Check what reference plane the SPI1 and DRDY/RESET traces have where they
     cross the split, and where SPI1 reaches the AGND-referenced DAC.
2. **Return path of the DUT current.** Up to 49 mA flows from R10 through the
   AGND copper to R31 and on to J1 pin 4. It should not flow under or through
   the ground connections of U8 (VREFN, AGND, AIN1/AIN3 returns), U2, the
   INA128 REF pins or U4. It must also cross the DGND pour to J1, which is
   worth checking.
3. **Shunt Kelvin taps.** U6 +IN (and TP3) should tap R10's FORCE− pad
   directly. U6 −IN and REF should tap R10's AGND pad directly. They should
   not pick up AGND at an arbitrary point on the pour.
4. **The Kelvin pair.** SENSE+/SENSE− run as a pair from J5/J6 to the clamps,
   R12/R13 and U7, away from FORCE traces and digital lines. FORCE+ and SENSE+
   must not connect anywhere on the board, and neither must FORCE− and SENSE−.
5. **Reference distribution.** VREF_2V5 should run as short, quiet traces from
   U2 to U11 (then DAC_VREF to U4), U8 VREFP/AINCOM (C44 at U8) and the U6/U7
   REF pins. The REF
   traces are best tapped near U2/C9 or U8, paired with I_SENSE_OUT and
   V_SENSE_OUT respectively. Keep VREF_HALF, V_DAC and the U3B summing nodes
   compact.
6. **High-impedance nodes.** U7's RG pins and the empty R14 pads, the U6 RG
   pins, the U7 input nodes (8 kΩ Thevenin behind the attenuator), the
   R7 ILIM node (348 kΩ) and DAC_VREF (R35, 1 MΩ): check clearance and
   freedom from flux contamination. The bring-up checklist requires a
   flux-removal wash and a dry bake for these. R32/R33 should return to
   AGND at one point, beside U7, so AGND noise is common-mode to U7.
7. **U5 thermal and mechanics.**
   - Copper area and heatsink provision for the TO-220-7.
   - Clearance for the tab at −15 V (insulate it from any heatsink or
     enclosure).
   - The lead stagger matching the purchased part (R1: the OPA547T, TI
     package KVT, checked against TI's solder footprint), and the widened
     1.2 mm drills.
   - R30, R9 and R10 kept clear of heat-sensitive precision parts (U2, U3, R1–R6).
     R30 (PR02, ≤ 1.96 W in the fault case) is mounted raised, away from the
     Kelvin divider; F1 is 14 mm from it (`docs/final_corrections.md` §2.1).
8. **Decoupling proximity.** Check that these sit at their pins with short
   ground returns to the correct pour: C15/C16 at U5, C20–C23 at U6/U7,
   C11/C12 at U3, C13 at U4, C28/C30 at U8, and C34–C38/C43 at U9.
9. **ADC front end.** R15–R18 and C24/C25 should sit next to U8 pins 6–9, and
   C44 at pins 4/5 (VREFP/AINCOM). Check the crystal Y1 and C26/C27 placement
   against the ground split (R14).
10. **Connectors.**
    - J1 (bench supply) on the DGND side with a clear path to R31.
    - J4–J7 grouped, with FORCE traces sized for the FORCE netclass.
    - J3 USB pair routed as a pair over unbroken DGND.
    - Panel-mount banana jacks and the vertical TO-220 need mechanical fit
      with the enclosure. No enclosure is defined in the repository.
11. **Silkscreen for J1 (resolved, audit F1).** The board of record now has a
    "BENCH SUPPLY" heading and a label at each J1 pin, matching the netlist:
    **J1.1 = +15V, J1.2 = −15V, J1.3 = +5V, J1.4 = GND**. J1's value reads
    `BENCH SUPPLY 1:+15V 2:-15V 3:+5V 4:GND`. The original routed board had no
    J1 labels at all. The `--autoplace` bootstrap board used to print a
    hand-written legend, `1 +15V | 2 GND | 3 -15V | 4 +5V`, that disagreed with
    the circuit. `generators/pcb.py` now reads those labels from J1's pad nets
    and stops if a net has no label.
12. **Designators and polarity.** The repository board places some reference
    designators on F.Fab rather than silkscreen. For the routed board, confirm
    that assembly-critical markings are present: tantalum and electrolytic
    polarity, diode cathodes, U5/U6/U7 pin 1, and the DUT jack labels (now
    FORCE+, FORCE−, SENSE+, SENSE− on the front silkscreen beside J4–J7).
13. **Synchronization.** Run *Update PCB from Schematic* on the routed board.
    The repository treats "no changes" as a contract. Any change reported
    would mean the routed board no longer matches the schematic under review.

---

## 12. Design source of truth: SKiDL → KiCad

```
blocks/*.py  +  libraries/custom_symbols.py  (SKiDL, the electrical source)
        │            └─ pin numbers read at build time from pinned KiCad libraries
        │               (kicad-symbols/footprints 9.0.9.1) or from libraries/stage1_iv.kicad_sym
        ▼
     main.py ──► stage1_iv.net            (flat netlist: documentation and validation only)
             ──► stage1_iv.kicad_sch + 8 sheets   (generated schematic = KiCad source of truth)
             ──► stage1_iv.kicad_pcb      (generated from the circuit + layout/stage1_iv.layout
                                           + layout/bipolar.eco + layout/audit_corrections.eco
                                           + layout/final_corrections.eco
                                           + layout/policy_clearance.eco:
                                           placed and fully routed)
             ──► component_manifest.md
tools/gen_project_config.py ──► stage1_iv.kicad_pro (netclasses), stage1_iv.kicad_dru, sym/fp-lib-table
```

- **Electrical intent** lives in `blocks/*.py`. Each block connects pins
  **by function name** (for example `U8["VREFP"]`), never by pin number. The
  number comes from the symbol library at build time.
- **For KiCad, the schematic is the source of truth.** The PCB is meant to be
  updated with *Update PCB from Schematic*, not from the netlist
  (`docs/library_strategy.md`, `manifest.yaml`).
- The project **owns its symbols**. Every schematic symbol is embedded in its
  sheet, and `libraries/stage1_iv.kicad_sym` holds them all, so the project
  opens without external libraries. The footprints used (37 library files,
  plus two project footprints generated by `tools/vendor_footprints.py`: the
  U5's TO-220-7 with 1.2 mm drills and the isolated M3 mounting hole with
  its copper keepout) are vendored in `libraries/footprints/`.
- The schematic uses the KiCad 7 format (version 20230121). Newer KiCad
  versions migrate it on load.
- **Human edits.** The routed PCB (`unipolar-routed/diodes.kicad_pcb`, KiCad 10.0)
  was changed by hand (placement and routing), and its schematic gained C31,
  C44 and C45; all three are now in the SKiDL source. Its placement and
  copper are captured in `layout/stage1_iv.layout`, from which `main.py`
  regenerates the root `stage1_iv.kicad_pcb` (with `layout/bipolar.eco`
  applied); `unipolar-routed/` itself is never touched and is the reference
  `tools/reproduce_reference.py` proves the capture against.
  `tools/compare_routed_board.py` lists what separates the original routed
  board from the SKiDL design (today: the bipolar-measurement change and the
  audit corrections).
- **Re-run check (bipolar-measurement revision).**
  - `python3 main.py` with the pinned KiCad 9.0.9.1 libraries: structural
    check PASS (113 parts, 362 pads, 313 netted), SKiDL ERC 0 errors / 0
    warnings, 11/11 connectivity assertions, measurement-range check PASS,
    Update PCB from Schematic a no-op on the generated board.
  - `tools/kicad_roundtrip.py` (kicad-cli 7.0.11) reads 313 connections in
    66 nets across 113 components, matching the netlist.
- **Re-run check (audit corrections, 2026-10-26).** With the pinned 9.0.9.1
  libraries and kicad-cli 10.0.7: structural check PASS (135 symbols, 126
  footprints), SKiDL ERC 0 errors / 0 warnings, 15/15 connectivity assertions,
  measurement-range and fault-envelope check PASS, KiCad schematic ERC 0
  errors / 0 warnings, KiCad round-trip PASS, routed-board check 16/16 PASS,
  Update PCB a no-op. The full record, including DRC, is in
  `docs/audit_corrections.md`.

---

## 13. Verification status and documentation discrepancies

### 13.1 What has and has not been verified

| Evidence | Status in the repository |
|---|---|
| SKiDL ERC, 15 custom connectivity assertions, measurement-range and fault-envelope check, schematic ↔ netlist equivalence | Pass, reproduced for this revision (§12) |
| Symbol and footprint identifiers resolve. Pin numbers are real pads. No stray pads | Reported pass when KiCad libraries are present (CI). Not reproduced here, because no KiCad libraries were available |
| KiCad netlist export from the schematic matches | Reported pass (KiCad 7.0.11 `kicad-cli`) |
| KiCad **schematic ERC** | `kicad-cli sch erc` (10.0.7), run in CI: **0 errors, 0 warnings** on the audit-corrections revision (`docs/audit_corrections.md`) |
| KiCad **DRC** on the routed board | KiCad 10.0.7 on every build where it is installed (CI installs it): 0 unconnected, 0 parity, 0 errors under the as-built rules and **0 errors under the repository's netclass policy** (2 pre-existing silk-to-edge warnings at J3 under both); `validation/routed_board_check.py` requires that (`docs/final_corrections.md` §2.3) |
| U5 pinout and lead stagger | **Verified**: every pin is cross-checked against TI SBOS070D (OPA548) and TI SBOS056F (OPA547, the part fitted) on every symbol build (`RESOLVED`); footprint compared with TI's KVT solder footprint |
| Circuit simulation (SPICE), thermal analysis | No simulation files are in the repository. The final audit simulated the output stage (TI OPA547/OPA548 macromodels: limit plateau, stability on the plateau and as a follower) and the heartbeat pump in ngspice, and worked the U5, D7 and R30 thermal cases by hand; results and conditions are in `docs/final_audit.md` §4 |
| Measurement-range arithmetic (worst-case ADC pin voltages, full scale) | `validation/measurement_range.py`, on every build |
| Routed board vs the SKiDL design | The board of record carries the bipolar change (`layout/bipolar.eco`); `validation/routed_board_check.py` proves it matches the circuit and Update PCB is a no-op. `unipolar-routed/` (the unipolar original) still differs by exactly the bipolar change, as `tools/compare_routed_board.py` reports |
| Bench testing, prototype measurements, calibration data | **None in the repository.** The plan is `docs/bringup_checklist.md` |
| Reference-gate charge pump margins | Transient simulation only (scratch model, not in the repository); measured at bring-up |
| Firmware | **None in the repository** |

So the evidence establishes **netlist and identifier consistency**. It says
nothing on its own about **electrical performance**. Every performance
statement in this document comes from calculation.

### 13.2 Discrepancies found in repository text (do not affect connectivity)

| Where | Statement | Actual design |
|---|---|---|
| `generators/pcb.py` / bootstrap silkscreen | J1 legend "1 +15V, 2 GND, 3 -15V, 4 +5V" | **Fixed**: now read from J1's pad nets (1 +15V, 2 −15V, 3 +5V, 4 GND) |
| `blocks/protection.py` | FORCE− protection "lives on" Sheet 5 | Sheet 5 has no protection on FORCE− |
| `blocks/mcu.py` (shield comment) | "The board is bus-powered" | **Fixed**: powered only from J1. VBUS is only sensed |
| `blocks/output_buffer.py` | "R9's drop is NOT corrected downstream" | R9 is inside the loop. The drops *after* VO_SENSE are the ones not corrected |
| `README.md` / `dac_sweep.py` | "Top 0.40 % of DAC codes unused" | About 0.2 % at each end exceeds ±10.000 V |
| `docs/pcb_generation.md` | "247 connections", "28 footprints" | The `--autoplace` board now has 126 footprints and is unrouted. 35 vendored library footprint files plus one project footprint |
| `docs/pcb_generation.md` | Board 129 × 108 mm, split at x = 38.02 mm | Generated board 132.2 × 117.8 mm, pours split at x ≈ 52.2 / 54.2 mm |
| `docs/silkscreen_and_packages.md` | Says both "all designators on F.Fab" and "all on F.SilkS" | Generated board: `PROMOTE_DESIGNATORS_TO_SILK = True`, a mix of both |
| `docs/FAB_CONSTRAINTS.md` | "Everything else ≤ 5 V" | SENSE±, V_SWEEP, V_SENSE_OUT and VO_* reach about ±10–13 V |
| Source comments | Cite `Stage1_IV_System_Design.md` §C.x | That file is not in the repository |

---

## 14. Review focus areas

This is a map of the places where independent checking is most valuable, not
a list of confirmed defects. Items are ordered roughly by consequence.
**Fact** = what the design does. **Check** = what to verify.

### A. Safety and possible functional blockers

- **R0 — J1 supply silkscreen. Resolved (audit F1).**
  - Fact: the board of record now labels each J1 pin, under a BENCH SUPPLY
    heading, matching the netlist (§11.3 item 11).
  - Check: any wiring or harness documentation agrees with it.
- **R1 — U5 (OPA547T) pinout and footprint. Resolved.**
  - Fact: the pin numbers (1 +IN, 2 −IN, 3 ILIM, 4 V−, 5 V+, 6 OUT, 7 E/S) are
    cross-checked against TI SBOS070D and TI SBOS056F on every symbol build.
    The footprint's drills were widened to 1.2 mm, and its hole pattern
    matches TI's KVT solder footprint (§5.3).
  - Check: **order the OPA547T** (TI package KVT, stagger-formed leads). The
    OPA547T-1 (package KC, straight leads) and the OPA547F (DDPAK) do not fit
    this footprint.
- **R2 — Bipolar measurement (§6). Addressed in the schematic; not yet on the
  routed board.**
  - Fact: the measurement is now bipolar over the whole source span, and no
    normal operating point drives an ADC input outside its window.
  - Check: that the routed board receives the change
    (`docs/bipolar_measurement.md` §5, `tools/compare_routed_board.py`), and
    on the bench that a −V → +V → −V sweep of a resistor reads a straight
    line through the origin with no step at 0 V.
- **R3 — R30 rating and protection ordering. Resolved (audit F3).**
  - Fact: R30 is a 2 W PR02, sized for the rail fault (≤ 143 mA, ≤ 1.96 W
    with F2 and U12 in the path). F1 is no longer described as a backstop,
    and it now sits 14 mm from R30.
  - Check: R30's body temperature and its effect on neighbours at bring-up.
- **R8 — Output state at power-up, reset and firmware fault. Resolved in
  hardware (audit F2).**
  - Fact: the midpoint now follows DAC channel B, so power-up gives ≈ 0 V, and
    the U11 reference gate forces V_SWEEP to ≈ 0 V within ≈ 50 ms of the
    software heartbeat stopping (§5.2, §8.2).
  - Check: the bring-up measurements of the pump voltage, the disarm time and
    the reset, halt and crash behaviour (`docs/bringup_checklist.md`).
  - A disconnect now exists only for missing rails (U12); firmware cannot
    open it, so "zero" is still a 0 V setpoint.

### B. Component and data sheet checks

- **R4 — U5 current limit. Resolved (final audit).** On an OPA548 the 49 mA
  setting lay inside the limit circuit's tolerance; U5 is now an OPA547T with
  R7 = 348 kΩ, chosen from SBOS056F's supply-voltage and temperature curves
  so that the expected plateau is 40…65 mA at ±15 V and the stack of its
  errors stays between 8 and 89 mA (§5.3). SBOS056F plots those curves at
  250–500 mA; that the shifts are the same mA at this lower setting is an
  extrapolation, and the design does not depend on it (with no shift at all
  the plateau is 62.6 mA ±30 mA). Nothing relies on the plateau's value.
  C14 is the optional noise capacitor of SBOS056F Fig. 3. The output R-C
  network R38/C49 is fitted (100 Ω + 10 nF, §5.3).
  - Check at bring-up: the plateau in both polarities
    (`docs/bringup_checklist.md` §7), and record it as the firmware's
    `i_plateau`.
- **R5 — U5 E/S** tied to +15 V through 10 kΩ. **Resolved (final audit)**:
  allowed by SBOS056F (§5.3); the pull-up defeats only the unused
  thermal-status output.
- **R6 — U5 thermal. Resolved (final audit).** ≤ 0.50 W in the output stage
  for a shorted DUT at any limit value (0.56 W with FORCE+ shorted to
  AGND), plus 0.30 W typical / 0.47 W maximum quiescent: ≤ 62 K (66 K) rise
  in a free-standing TO-220 (65 °C/W), no heatsink needed (§5.3). The tab
  is at −15 V.
- **R7 — DAC8552 logic thresholds** at V_DD = 5 V with 3.3 V drive.
- **R9 — Current-channel low-end capability.** A 10 Ω shunt gives 10 µV per
  µA whatever the gain; at G = 2.40 the INA128P offset is 5 µA typ / 26 µA max
  and its drift 0.10 µA/°C typ, before calibration. The ADC's PGA (up to 64)
  adds resolution but not accuracy. Compare with the currents the application
  needs, such as diode leakage.
- **R10 — Accuracy of the scaling parts.** R10 is now 0.1 %, 20 ppm/°C
  (R11, R12/R13 and R32/R33 are 0.1 %; their TCR tracking sets the drift of k).
  Also the INA128 gain error and the REF5025 grade.
- **R11 — Supply assumptions.**
  - No on-board regulation of ±15 V.
  - No reverse-polarity protection or fusing. Sequencing: U12 and D8/D9
    cover a missing ±15 V rail (`docs/final_corrections.md` §2.5).
  - The ground connection through USB to the host and earth.
- **R12 — Reference decoupling.** C9 is now 4.7 µF (REF5025 needs 1 … 50 µF,
  SBOS410O). C44 (100 nF) bypasses VREFP/AINCOM at the ADS1256 (it was already
  on the routed board). Still no NR capacitor on the REF5025, and one reference
  node shared by the DAC (through U11), the ADC reference, the ADC's negative
  input and both INA128 REF pins.
- **R13 — MCU details.**
  - PC13 now sinks the LED current (resolved).
  - C43 VCAP1 is now 4.7 µF (resolved, audit F4).
  - PA9 being the OTG VBUS-sense pin (firmware handles it,
    `docs/firmware_handoff.md`).
  - BOOT0 hard-tied.
- **R14 — ADS1256 crystal.** C26/C27 return to AGND while the crystal pins are
  on the DGND side of the split.
- **R15 — Input protection.**
  - FORCE− has a series PTC (F2) and no TVS, by design: a clamp there would
    be a current path around the shunt (`docs/final_corrections.md` §2.6).
  - The SENSE clamps sit ahead of the series resistors and dump fault current
    into the ±15 V rails.
  - The INA128 input overvoltage ratings.
  - The D5 energy rating against realistic DUT stored energy.
- **R17 — R14 DNP representation. Resolved.** R14 carries the KiCad DNP and
  BOM-exclusion attributes in the schematic and on the board. (R38 and C49
  carried them too until the final audit fitted them.)
  Mark TP1–TP8 for BOM exclusion too, if wanted (`docs/pcb_generation.md`).
- F1 footprint: `Fuse_Bourns_MF-RG300` is used for a Bel Fuse
  `0ZRM0010FF1E`. Confirm the lead spacing and body size.

### C. Layout (routed board)

- **R16 — Analog ground current and the shunt taps.** DUT return current in
  the AGND copper no longer reaches the output side of the measurement (INA128
  REF and ADC AINCOM are on VREF_2V5), but it still matters at the inputs: the
  U6 −IN tap on R10, and the common return point of R32/R33 (§11.3 items 2–3).
- **R18 — Kelvin pair and reference routing** (§11.3 items 4–5).
- **R19 — Ground split.** Integrity, parts on the correct pour, and R31 as the
  sole bridge (§11.3 item 1).
- **R20 — Digital crossing the split.** SPI1 to the AGND-referenced DAC, and
  the SPI2/DRDY/RESET return paths.
- **R21 — U5 thermal copper and mechanics.** Heat placement relative to the
  precision parts.
- **R22 — Synchronization.** Update PCB from Schematic reports no changes.
  DRC is clean on the routed board under its as-built rules and under the
  project netclasses (the 109 clearance errors of the human routing were
  fixed, `docs/final_corrections.md` §2.3).

### D. Firmware assumptions implied by the hardware (no firmware in the repository)

- The full list is in `docs/firmware_handoff.md`. In short:
- Load channel B to full scale (0xFFFF) and channel A to mid-scale before
  starting the PC5 heartbeat, and keep B at full scale while armed. Use the
  simultaneous-load sequence (0x00 then 0x34) when both channels change.
- Generate the heartbeat (≥ 1 kHz) from the main loop only, never from a
  timer, ISR or DMA, so that a stall or halt disarms the output.
- Clamp channel A codes to about 131…65404 for ±10.00 V. The measurement
  covers the full code range, so no further clamp is needed for it.
- ADS1256: MUX AIN2/AINCOM (voltage) and AIN0/AINCOM (current), PGA 2 for
  both, input buffer off; higher PGA only for small signals. Convert with
  V_K = code·2.5 V/(2²³·0.199203) and I = code·2.5 V/(2²³·24.0056 V/A).
  Keep SCLK ≤ f_CLKIN/4 = 1.92 MHz. After reset, set ADCON CLK = 00 (CLKOUT
  off) and drive D1–D3 as outputs low, because those pins are not connected.
- Disable hardware VBUS sensing on the USB OTG peripheral (PA9 is used as
  RESET_ADC).
- A software current cut-off set below the hardware plateau acts before U5
  limits. The plateau is expected at 40…65 mA and is different in the two
  polarities, so a 50 mA cut-off sits inside it (`docs/final_audit.md` §7).
- Reactions that write zero at once are the edge that, in TI's model,
  leaves U5 oscillating when an inductive DUT is in negative current limit
  (§10.1). A ramp is safe there.
- Calibrate offset and gain for both channels, including a zero point at the
  start of every session (INA128 offset referred to the DUT is up to 2.8 mV and
  26 µA before calibration). Iterate the set-point on the measured Kelvin
  voltage when a target DUT voltage is needed.

---

## 15. Repository / Review Map

| What | Where | Notes |
|---|---|---|
| **KiCad project** | `stage1_iv.kicad_pro` | Netclasses and board setup (generated by `tools/gen_project_config.py`) |
| **Schematic (source of truth for KiCad)** | `stage1_iv.kicad_sch` (top) + `power`, `mcu`, `dac_sweep`, `output_buffer`, `current_sense`, `voltage_sense`, `adc`, `protection` `.kicad_sch` | Generated from SKiDL. Label-based connectivity. Keep all nine files together |
| **PCB (board of record)** | `stage1_iv.kicad_pcb` | Generated from the circuit + `layout/stage1_iv.layout` + `layout/bipolar.eco` + `layout/audit_corrections.eco`: the human placement and routing with both ECOs, **fully routed** (`docs/physical_design.md`). The unipolar original is `unipolar-routed/diodes.kicad_pcb` |
| Custom DRC rules | `stage1_iv.kicad_dru` | Intra-footprint clearance only |
| Symbols / footprints | `libraries/stage1_iv.kicad_sym`, `libraries/footprints/*.pretty`, `sym-lib-table`, `fp-lib-table` | Project-owned copies |
| **SKiDL design source** | `blocks/*.py` (one file per sheet), `blocks/nets.py` (global nets), `main.py` (build entry) | The best place to read design intent. Docstrings carry the rationale |
| Part factories and pin aliases | `libraries/custom_symbols.py` | Maps design pin names to symbol pins |
| Pin provenance | `libraries/provenance/` (ADS1256 from SBAS288K; the U5 symbol from a third-party library, cross-checked against SBOS070D for the OPA548 and SBOS056F for the fitted OPA547) | See R1 |
| Netclasses and derivations | `libraries/netclasses.py`, `docs/FAB_CONSTRAINTS.md` | Assumed fab capability (no fab chosen) |
| Netlist | `stage1_iv.net` | Flat. For documentation and validation, not for PCB import |
| BOM-like table | `component_manifest.md` (generated), `manifest.yaml` | `manifest.yaml` also lists assumptions and unresolved items |
| PCB generation notes | `docs/pcb_generation.md`, `docs/silkscreen_and_packages.md` | Describe the generated board. Parts are stale (§13.2) |
| Identifier and library notes | `docs/identifier_resolution.md`, `docs/library_strategy.md`, `docs/LIBRARY_DEPENDENCIES.md`, `docs/power_flags_and_erc.md` | Toolchain history. Low review priority |
| Validation scripts | `validation/assertions.py` (topology checks), `validation/erc.py`, `validation/footprint_check.py`, `validation/schematic_check.py`, `validation/drc_check.py`, `validation/routed_board_check.py`, `validation/update_pcb_noop.py`, `tools/reproduce_reference.py` | Structural and connectivity checks, and KiCad 10 DRC of the routed board where KiCad 10 is installed |
| Generators and tools | `generators/schematic.py`, `generators/pcb.py`, `generators/routed_pcb.py`, `tools/*` | `pcb.py` builds only the `--autoplace` bootstrap board |
| Audit corrections, firmware handoff, bring-up | `docs/audit_corrections.md`, `docs/firmware_handoff.md`, `docs/bringup_checklist.md` | What changed and why, what firmware must do, and how to bring up the first board |
| CI | `.github/workflows/build.yml` | Runs the build and checks |
| Firmware, simulations, test data, design spec | **Not present** | `Stage1_IV_System_Design.md` is referenced but absent |

**Suggested reading order for a reviewer:**

1. This document, §2 and §6.
2. `blocks/dac_sweep.py` → `output_buffer.py` → `protection.py` →
   `current_sense.py` → `voltage_sense.py` → `adc.py`. These are short and
   carry the rationale.
3. The KiCad schematic sheets in the same order.
4. The routed PCB, against §11.3.
5. §14.

---

## Appendix A — Key nets

Taken from `stage1_iv.net` (ref.pin).

| Net | Nodes |
|---|---|
| VREF_2V5 | U2.6 (VOUT), C9.1, U11.2 (S), U8.4 (VREFP), **U8.5 (AINCOM)**, **U6.5 (REF)**, **U7.5 (REF)**, C44.1, TP8 |
| DAC_VREF | U11.1 (D), U4.2 (VREF), R35.1, #FLG9 |
| DAC_ARM | D6.2 (K), C48.2, R37.2, U11.4 (SEL) |
| SAFE_HB → SAFE_HB_R → SAFE_HB_AC | U9.25 (PC5), R36.2 → R36.1, C47.2 → C47.1, D6.3 (COM) |
| CS_DAC | U9.20 (PA4), U4.5 (/SYNC), R34.2 (R34.1 on +5VA) |
| LED_STATUS / LED_K | U9.2 (PC13), R23.1 / R23.2, LED1.1 (K); LED1.2 (A) on +3V3D |
| V_DACB | U4.3 (VOUTB), R1.1 |
| VREF_HALF_RAW / VREF_HALF | R1.2, R2.1, U3.3 / U3.1, U3.2, R5.1 |
| V_DAC | U4.4 (VOUTA), R3.1 |
| DIFF_P / DIFF_N | R3.2, R4.1, U3.5 / R5.2, R6.1, U3.6 |
| V_SWEEP | U3.7, R6.2, U5.1 (+IN), TP1 |
| VO_RAW | U5.6 (OUT), R9.1, C19.1, R38.1 |
| VO_SENSE | R9.2, C19.2, U5.2 (−IN), F1.1, TP2 |
| OPA548_ILIM / OPA548_ES | U5.3, R7.1, C14.1 / U5.7, R8.1 |
| F1_MID | F1.2, R30.1 |
| FORCE_SW | R30.2, U12.6 |
| FORCE+ | U12.4, D5.1, J4.1 |
| SSR_LED_A / SSR_LED_K | R39.2, U12.1 / U12.3, D7 (K) |
| FORCE− | J7.1, F2.1 |
| ISENSE_HI | F2.2, R10.1, U6.3 (+IN), TP3 |
| SENSE+ | J5.1, R12.1, D1.2 (A), D2.1 (K), TP5 |
| SENSE− | J6.1, R13.1, D3.2 (A), D4.1 (K), TP6 |
| SENSE_P_AMP / SENSE_M_AMP | R12.2, R32.1, U7.3 / R13.2, R33.1, U7.2 |
| U6_RG1/2, U7_RG1/2 | U6.1/U6.8 across R11. U7.1/U7.8 across R14 (DNP) |
| I_SENSE_OUT | U6.6, R15.1, TP4 — 2.500 V + 24.0 V/A·I |
| V_SENSE_OUT | U7.6, R17.1, TP7 — 2.500 V + 0.1992·V_K |
| ADC_AIN0…3 | R15.2/U8.6/C24.1, R16.2/U8.7/C24.2, R17.2/U8.8/C25.1, R18.2/U8.9/C25.2 |
| AGND (selected) | R10.2, U6.2 (−IN), R32.2, R33.2, R16.1, R18.1, U8.2/.3, C44.2, U2.4, U4.8, R2.2, R4.2, R35.2, U11.3, D5.2, C26.2, C27.2, D8 (A), D9 (K), **R31.1** |
| DGND (selected) | J1.4, U1.1, U8.17, U9 VSS/VSSA, U10.2, J3 GND/S1, J2.3, D6.1 (A), C48.1, R37.1, **R31.2** |
| +15VA / −15VA | J1.1 / J1.2, plus the U3/U5/U6/U7 supplies, the D1/D3 cathodes / the D2/D4 anodes, R8 / R7, C14, R39.1 and D8 (K) / D7 (A) and D9 (A) |
| +5V_RAW / +5VA | J1.3, U1.3, FB1.1, C1, C3 / FB1.2, U2.2, U4.1, U8.1, U11.5, R34.1, C7, C10, C13, C28, C29, C46 |

## Appendix B — Calculations

| Quantity | Expression | Value |
|---|---|---|
| Sweep gain | R4/R3 = 20.0k/2.49k | 8.0321 |
| Sweep transfer | 8.0321 × (V_A − V_B/2), V_B at full scale | |
| Sweep span | ±8.0321 × 1.24998 V | ±10.040 V |
| Sweep LSB | 2.5 V / 65536 × 8.0321 | 306.4 µV |
| Codes for ±10.000 V (channel A) | 65536 × (1.24998 ∓ 10/8.0321)/2.5 | ≈ 131 … 65404 |
| Zero | code_A = code_B/2 = 32767.5 | ±0.15 mV at 32767/32768 |
| U5 ILIM, equation (OPA547, SBOS056F; ±30 V, 25 °C) | 5000 × 4.75 V / (31.6 kΩ + 348 kΩ) | 62.6 mA; ±10 mA typ, ±30 mA max between units |
| U5 plateau at ±15 V, typical | 62.6 − 12 (sourcing) or − 4 (sinking) − 0.19 mA/K × (T_case − 25 °C) | +50.6 / −58.5 mA at 25 °C; +42.0 / −50.0 mA at 70 °C |
| U5 plateau, extrapolated stack (R7 ±1 %, rails 14.5…15.5 V, 25…85 °C case, ±30 mA unit) | 62.0 − 12.4 − 0.19 × 60 − 30; 63.1 − 3.9 + 30 | 8 mA … 89 mA |
| U5 output required | 10.04 V + 49 mA × 10 Ω (91.3 mA × 10 Ω at the passive bound) | 10.53 V (10.95 V) |
| Out-of-loop resistance | R30 + R10 (+ R_F1 + R_U12 + R_F2) | 110 Ω + 6 … 17 Ω |
| DUT voltage at a 49 mA plateau | 10.04 − 0.049 × 110 | ≈ 4.65 V (− I·(R_F1 + R_U12 + R_F2)) |
| R30 power at a 49 mA plateau | 0.049² × 100 (0.089² × 100 at the upper end of the spread) | 0.240 W (0.79 W) |
| Rail fault current, DUT short (U5 driven into a rail) | 15.5 V / (9.9 + 3.0 + 95.3 + 0 + 3.0 + 9.99) Ω, all at their minima | 128 mA |
| Rail fault current, FORCE+ to AGND | 15.5 V / (9.9 + 3.0 + 95.3 + 0) Ω | 143 mA |
| R30 power, rail fault | max over R30's tolerance and heating of V²R/(R + R_rest)² | ≤ 1.96 W (2 W PR02) |
| R9, R10 power, rail fault | as above | ≤ 0.21 W (0.25 W part) / ≤ 0.16 W (3 W part) |
| External 15.5 V on J7 | 15.5 V / (3.0 + 9.99) Ω until F2 trips | 1.19 A, R10 ≤ 14.3 W (15 W for 5 s) |
| R9, R10 power at a 49 mA plateau | 0.049² × 10 | 24 mW each |
| U5 output-stage power, shorted DUT | (15 − 0.049 × 121) × 0.049; at most V_S²/(4 R_path) = 15.5²/(4 × 121.2) for any limit (15.5²/(4 × 108.2) with FORCE+ shorted to AGND) | ≈ 0.44 W; ≤ 0.50 W (0.56 W) (+ 0.30 W typ / 0.47 W max quiescent) |
| Physical current bound, any passive DUT | 10.04 V / (R30 + R10) | 91.3 mA (92.5 mA worst tolerance) |
| INA128 gain, current channel | 1 + 50k/35.7k | 2.4006 |
| Current transimpedance | 2.4006 × 10 Ω | 24.006 V/A (24.0 mV/mA) |
| I_SENSE_OUT at ±49 mA / ±91.3 mA | 2.500 ± 24.006 × I | 2.500 ± 1.176 V / 2.500 ± 2.192 V |
| Current full scale, PGA 2 / pin-limited | 2.500 V / 24.006; 2.35 V / 24.006 | ±104.1 mA / ±97.9 mA |
| Kelvin attenuator | 10.0k / (40.2k + 10.0k) | k = 0.19920 |
| INA128 gain, voltage channel | 1 + 50k/∞ (R14 DNP) | 1.000 |
| V_SENSE_OUT at ±10.04 V | 2.500 ± 0.19920 × 10.04 | 2.500 ± 2.000 V |
| Voltage full scale, PGA 2 / pin-limited | 2.500 V / 0.1992; 2.35 V / 0.1992 | ±12.55 V / ±11.80 V |
| ADC pin window, buffer off | AGND − 0.1 … AVDD + 0.1 (AVDD ≥ 4.75 V) | −0.10 … 4.85 V |
| ADC input RC corner | 1 / (2π × 200 Ω × 1 nF) | ≈ 796 kHz |
| INA REF current | (2.5 V − A2out) / 80 kΩ | 4 … 56 µA per INA128 |
| Midpoint divider current (from DAC channel B) | 2.5 V / 20 kΩ | 125 µA |
| Reference gate pull-down | 2.5 V / 1 MΩ (R35) | 2.5 µA |
| Heartbeat pump (simulated) | 1 kHz into R36/C47/D6/C48/R37 | DAC_ARM 2.42 … 2.46 V nominal, 1.87 V worst case; < 0.87 V within ≈ 52 ms of stopping |
| VBUS sense | 5 V × 100k / 200k | 2.5 V at PA0 |
| LED current (V_F ≈ 2 V assumed) | (3.3 − 2.0) / 1 kΩ | ≈ 1.3 mA, sunk by PC13 (limit 3 mA) |
| USB-C Rd | R24, R25 | 5.1 kΩ each (UFP) |
