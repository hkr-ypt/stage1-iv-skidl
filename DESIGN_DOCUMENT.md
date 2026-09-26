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

> **Important context about the PCB.** The repository contains a *generated*
> `stage1_iv.kicad_pcb` that is **placed but unrouted**, and its placement is a
> machine-generated starting point, not a considered layout. The board you
> received was routed by a human, who also changed component placement
> substantially while keeping the schematic unchanged. So this document treats
> the **schematic as the electrical source of truth**. It describes the
> repository board only where its *setup* (netclasses, design rules, ground-pour
> strategy) is likely to have carried into the routed board. PCB-level
> observations are therefore mostly written as **things to check on the routed
> board** (§11). The routed KiCad project is now in the repository under
> `routed/`; it predates the bipolar-measurement change and needs the small
> engineering change listed in `docs/bipolar_measurement.md` §5.

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
| Bipolar sweep to ≈ ±10 V | `README.md`, `blocks/dac_sweep.py` | 16-bit DAC + difference amplifier with gain 8.03 → ±10.04 V |
| Measure DUT voltage without lead or shunt drops | `blocks/voltage_sense.py` | 4-wire Kelvin: separate SENSE± jacks into an INA128 |
| Signed V and I over the whole sweep (hysteresis on a fixed DUT) | `blocks/current_sense.py`, `blocks/voltage_sense.py`, `blocks/adc.py` | INA128 REF pins and ADC AINCOM on VREF_2V5; Kelvin attenuator k = 0.1992; current gain sized to the physical current bound. Checked by `validation/measurement_range.py` |
| Hardware current limit ≈ 50 mA | `blocks/output_buffer.py` | OPA548 programmable current limit, R7 = 1.43 MΩ → ≈ 49.35 mA |
| Protection in depth for DUT and instrument | `blocks/protection.py` | OPA548 ILIM → PTC fuse F1 → 100 Ω R30 → TVS D5; rail clamps on the sense inputs |
| Ratiometric accuracy | `blocks/power.py`, `blocks/adc.py` | One REF5025 (2.500 V) feeds the DAC reference, the sweep mid-point divider, the ADC reference, and the measurement zero (INA128 REF pins, ADC AINCOM) |
| Keep analog and digital apart | `blocks/power.py`, `docs/pcb_generation.md` | Separate AGND/DGND nets and pours, joined only at R31 (0 Ω). Separate SPI buses for DAC and ADC |

The repository states **no numerical accuracy, resolution, noise, speed or
temperature specification** for the instrument. The error-budget notes in this
document are therefore analysis only. They are not requirements.

---

## 2. Design at a glance

| Item | Implemented value | Reference |
|---|---|---|
| MCU | STM32F411RET6 (LQFP-64), 8 MHz HSE crystal | U9, Y2 |
| DAC | DAC8552 16-bit, channel A used, VREF = 2.500 V, powered from +5VA | U4 |
| Sweep amplifier | OPA2277: U3A buffers VREF/2, U3B is a difference amp with gain R4/R3 = 20.0k/2.49k = 8.032 | U3, R1–R6 (0.1 %) |
| Sweep range (V_SWEEP) | −10.040 V … +10.040 V, ≈ 306 µV/LSB | TP1 |
| Output stage | OPA548T power op-amp, unity-gain follower, feedback taken after R9 (10 Ω) | U5, R9, C19 |
| Current limit | R7 = 1.43 MΩ → **≈ 49.35 mA** (from the formula implied in the repository, [DS-check]) | R7, C14 |
| Series elements outside the loop | F1 (PTC, 100 mA hold) + R30 100 Ω + R10 10 Ω | F1, R30, R10 |
| DUT compliance | ±10.04 V at I → 0, about ±4.6 V at 49 mA (less by F1's resistance) | §5.4 |
| Current sense | R10 = 10.0 Ω low-side shunt in FORCE−, INA128 G = 1 + 50k/35.7k = 2.4006 → **24.0 V/A**, REF on VREF_2V5: `I_SENSE_OUT = 2.500 V + 24.0 V/A·I` | R10, U6, R11 |
| Voltage sense | Kelvin SENSE± → rail clamps → 40.2 kΩ/10.0 kΩ dividers to AGND (k = 0.1992) → INA128 **G = 1** (R14 **DNP**), REF on VREF_2V5: `V_SENSE_OUT = 2.500 V + 0.1992·V_K` | U7, R12–R14, R32, R33, D1–D4 |
| ADC | ADS1256 24-bit, 7.68 MHz crystal, **AIN0 − AINCOM = current, AIN2 − AINCOM = voltage, AINCOM on VREF_2V5**, VREF 2.500 V, PGA 2 (±2.500 V), AVDD 5 V, DVDD 3.3 V | U8, Y1, C44 |
| **Measurable window** | **V: ±11.8 V guaranteed (±12.55 V nominal FS). I: ±97.8 mA guaranteed (±104 mA nominal FS). Both signed; both cover everything the source can apply (±10.04 V, ±91.3 mA)** | §6 |
| Supplies | External bench supply: +15 V, −15 V, +5 V, GND through J1. On board: 3.3 V LDO, ferrite-isolated +5VA, 2.5 V reference | §8 |
| Host interface | USB 2.0 full-speed through a USB-C receptacle (data only; VBUS is sensed but does not power the board). SWD header | J3, J2 |
| DUT connection | Four 4 mm banana jacks: J4 FORCE+, J5 SENSE+, J6 SENSE−, J7 FORCE− | §5.4 |
| Grounds | AGND and DGND joined only at R31 (0 Ω, 0805). U8 is the only IC with pins on both | §8.3 |
| Part count | 113 physical parts (plus 8 schematic-only PWR_FLAGs, plus 4 board-only mounting holes on the generated board), 66 nets | `component_manifest.md` |
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
   U9 STM32F411 ──SPI1 (PA4/5/7, write-only)──► U4 DAC8552 ch.A ──V_DAC──┐
                                                  ▲ VREF = VREF_2V5       │
   VREF_2V5 ─► R1/R2 (÷2) ─► U3A buffer ──VREF_HALF──────────────────────►├─► U3B diff-amp, G = 8.032
                                                                          │     V_SWEEP (TP1), ±10.04 V
                                                                          ▼
   U5 OPA548 follower (+IN = V_SWEEP) ─OUT─► R9 10 Ω ─► VO_SENSE (TP2) ──┬──► back to U5 −IN (loop closes here)
     ILIM: R7 1.43 MΩ → ≈49 mA; E/S: R8 → +15VA (always on)              │
                                                                         ▼
   F1 PTC ─► R30 100 Ω ─► FORCE+ ──┬─► J4 FORCE+ ─────────┐
                                   └─ D5 TVS ─► AGND       │
                                                         [ DUT ]   (FORCE and SENSE join only at the DUT)
   AGND ◄─ R10 10 Ω shunt ◄─ FORCE− ◄─ J7 FORCE− ─────────┘
                                  (J5 SENSE+ and J6 SENSE− connect to the DUT terminals)

 MEASUREMENT PATH  (zero at VREF_2V5 = 2.500 V; both channels read against AINCOM)
   FORCE− / AGND (across R10) ──► U6 INA128, G = 2.40, REF = VREF_2V5 ──I_SENSE_OUT (TP4)──► R15/C24 ──► U8 AIN0
   J5 SENSE+ / J6 SENSE− ─► D1–D4 rail clamps ─► R12/R13 40.2 kΩ, R32/R33 10.0 kΩ to AGND (k = 0.1992)
                         ─► U7 INA128, G = 1 (R14 DNP), REF = VREF_2V5 ──V_SENSE_OUT (TP7)──► R17/C25 ──► U8 AIN2
   VREF_2V5 ──► U8 AINCOM (pin 5, beside VREFP pin 4; C44 bypass)     AIN1/AIN3: R16/R18 to AGND, spare self-checks
   U8 ADS1256 (VREFP = VREF_2V5, Y1 7.68 MHz) ──SPI2 (PB12–15) + DRDY (PA8) + RESET (PA9)──► U9

 HOST
   U9 ──USB FS (PA11/PA12, 22 Ω)──► U10 USBLC6 ESD ──► J3 USB-C (data only; VBUS sensed on PA0)
   U9 ──SWD──► J2;  PC13 ──► LED1;  SW1 reset;  Y2 8 MHz HSE
```

### 3.2 Hierarchical sheets

The schematic has one top sheet and eight child sheets. Each child sheet is
generated from one SKiDL block file.

| Sheet | File (KiCad) | SKiDL source | Contents |
|---|---|---|---|
| Top | `stage1_iv.kicad_sch` | `generators/schematic.py` | Sheet symbols and the cross-sheet nets |
| 1 Power | `power.kicad_sch` | `blocks/power.py` | J1, U1 LDO, FB1, U2 REF5025, bulk caps, **R31 star link** |
| 2 MCU | `mcu.kicad_sch` | `blocks/mcu.py` | U9, Y2, reset/boot, SWD J2, USB-C J3 + U10, LED1 |
| 3 DAC/Sweep | `dac_sweep.kicad_sch` | `blocks/dac_sweep.py` | U4 DAC8552, U3 OPA2277, R1–R6 |
| 4 Power buffer | `output_buffer.kicad_sch` | `blocks/output_buffer.py` | U5 OPA548, R7 ILIM, R8 E/S, R9/C19 isolation |
| 5 Current sense | `current_sense.kicad_sch` | `blocks/current_sense.py` | R10 shunt, U6 INA128, R11 |
| 6 Voltage sense | `voltage_sense.kicad_sch` | `blocks/voltage_sense.py` | U7 INA128, R12/R13, R14 (DNP), D1–D4 |
| 7 ADC | `adc.kicad_sch` | `blocks/adc.py` | U8 ADS1256, Y1, input RC filters, pull-ups |
| 8 Protection/DUT | `protection.kicad_sch` | `blocks/protection.py` | F1, R30, D5, J4–J7 |

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
| Digital | **DGND** | U9, U1 LDO, C1–C4/C8, U10, J3 (GND and shield), J2, SW1, LED1, U8 DGND pin, J1 pin 4 (supply return) |
| Bridge | R31 (0 Ω) | The only AGND↔DGND connection. `validation/assertions.py` checks this |

---

## 4. Operating concept

**Inference:** there is no firmware in the repository. The sequence below is
what the hardware allows and requires. It is not taken from existing code.

1. **Set the sweep point.** The MCU writes a 16-bit code to DAC8552 channel A
   over SPI1, which is write-only. U3B converts the code to
   `V_SWEEP = 8.032·(V_DAC − 1.250 V)`. U5 forces V_SWEEP at node VO_SENSE,
   after R9.
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
7. **Current limit.** If the DUT demands more than about 49 mA, U5 enters
   current limit and behaves as a current source. The Kelvin and shunt
   measurements stay valid in that state. **Inference.**

Things the hardware does **not** provide (by design or deferral): a
software-controlled output disable (the OPA548 E/S pin is tied permanently
enabled, and MCU control is "deferred" per `manifest.yaml`), an output
relay/disconnect, and range switching (the ADC's PGA provides digital
ranging for small signals, §6).

---

## 5. Signal chain, stage by stage

### 5.1 MCU → DAC (SPI1, Sheet 2 → Sheet 3)

| Signal | MCU pin | DAC8552 pin | Net |
|---|---|---|---|
| SCLK | PA5 (U9.21) | SCLK (U4.6) | SCK1 |
| DIN | PA7 (U9.23) | DIN (U4.7) | MOSI1 |
| /SYNC | PA4 (U9.20) | /SYNC (U4.5) | CS_DAC |

- The DAC is on its own SPI bus. The ADC uses SPI2. `validation/assertions.py`
  enforces that the two buses stay separate. The repository records this as a
  design requirement (spec §C.1). **Inference:** the likely reason is to keep
  DAC writes and ADC traffic independent, so neither bus's activity couples
  into the other converter.
- **Logic levels.** U4 runs from **+5VA** and its digital inputs are driven by
  a 3.3 V MCU. The DAC's V_IH at V_DD = 5 V must be met by 3.3 V CMOS
  outputs **[DS-check]** → **R7** in §14.
- **Ground boundary.** U4 is referenced to AGND, while its SPI drivers are on
  DGND. The SPI1 return current must therefore cross the split through R31.
  This is a layout question → **R20**.

### 5.2 DAC and sweep level shift (Sheet 3)

**Topology (design fact):**

- U4 (DAC8552) VREF = VREF_2V5, VOUTA drives net V_DAC. VOUTB is not connected
  (channel B is reserved).
- R1 = R2 = 10.0 kΩ 0.1 % divide VREF_2V5 to VREF_HALF_RAW ≈ 1.250 V.
  **U3A** buffers it to a low-impedance **VREF_HALF**.
- **U3B** is a 4-resistor difference amplifier:
  - R3 (2.49 kΩ) from V_DAC to the + input, R4 (20.0 kΩ) from the + input to AGND.
  - R5 (2.49 kΩ) from VREF_HALF to the − input, R6 (20.0 kΩ) from the − input to the output.
  - Output V_SWEEP, test point TP1. U3 runs from ±15VA.

**Transfer function.** With R4/R3 = R6/R5 = 20.0k/2.49k = 8.0321:

```
V_SWEEP = 8.0321 · (V_DAC − VREF_HALF),   V_DAC = VREF · code / 65536,   VREF_HALF = VREF/2
        = 8.0321 · VREF · (code/65536 − 0.5)
```

| DAC code | V_DAC | V_SWEEP |
|---:|---:|---:|
| 0 | 0.000 V | −10.040 V |
| 131 | 0.0050 V | −10.000 V |
| 32768 | 1.2500 V | 0.000 V |
| 65405 | 2.4950 V | +10.000 V |
| 65535 | 2.49996 V | +10.040 V |

- 1 LSB at V_SWEEP = 2.5 V / 65536 × 8.0321 ≈ **306 µV**.
- **Why 20.0 kΩ.** From the source comments: an exact ±10.000 V would need
  R4 = 19.92 kΩ, which is not a standard value. 19.8 kΩ (E192) would only reach
  ±9.94 V. So 20.0 kΩ (E96, same 0.1 % grade as R3/R5) was chosen to *bracket*
  ±10 V, and firmware is expected to leave the end codes unused. The source
  says "the top 0.40 %". In fact about 0.2 % of the codes at *each* end exceed
  ±10.000 V (see the table).
- **History (from git).** The earlier design used R4 = R6 = 10.0 kΩ (gain 4.016,
  ±5.02 V). Commit `66bdab9` changed them to 20.0 kΩ for ±10 V without changing
  any connectivity.
- **Ratiometric zero.** VREF_HALF comes from the same reference as the DAC. So
  mid-scale (code 32768) gives 0 V whatever the exact VREF value, apart from
  resistor and op-amp errors. The span scales with VREF.
- **Set-point error sources (analysis).**
  - R1/R2 ratio tolerance (0.1 % each) moves VREF_HALF by up to about ±0.6 mV,
    which is about ±5 mV at V_SWEEP.
  - The 0.1 % difference-amplifier resistors give up to about ±0.2 % gain error
    and a common-mode error of a few mV at V_CM ≈ 1.25 V.
  - DAC8552 offset and gain errors add to these **[DS-check]**.
  - These errors are small compared with the uncompensated series-resistance
    drop (§5.4). They do not reach the measurement, because the DUT voltage is
    measured independently. **Inference.**

### 5.3 Output power buffer (Sheet 4)

**Topology (design fact):**

| U5 (OPA548T) pin | Net | Connection |
|---|---|---|
| 1 +IN | V_SWEEP | From U3B |
| 2 −IN | VO_SENSE | Feedback from the **far side of R9** |
| 3 ILIM | OPA548_ILIM | R7 1.43 MΩ (1206) ‖ C14 10 nF to −15VA |
| 4 V− | −15VA | C16 100 nF + C18 10 µF/35 V tantalum to AGND |
| 5 V+ | +15VA | C15 100 nF + C17 10 µF/35 V tantalum to AGND |
| 6 OUT | VO_RAW | R9 10 Ω (1206) to VO_SENSE, C19 100 pF across R9 |
| 7 E/S | OPA548_ES | R8 10 kΩ to +15VA (always enabled) |

- **Pin numbering is unverified.** The U5 pin numbers and the TO-220-7 lead
  stagger (footprint `TO-220-7_P2.54x3.7mm_StaggerOdd_Lead3.8mm_Vertical`) come
  from a third-party KiCad library (Alternate KiCad Library at a pinned commit),
  not from the TI data sheet SBOS070. The repository flags U5 as
  `VERIFY-DATASHEET`, and it is the only part so flagged → **R1**.
- **Loop.** U5 is a unity-gain follower of V_SWEEP. The feedback is taken after
  R9, so the loop corrects R9's drop. With C19 across R9 this is the usual
  "isolation resistor inside the loop, with a high-frequency feedback path"
  arrangement for driving capacitive loads such as cable and DUT capacitance
  (**Inference** from topology. The repository only cites spec §C.6).
  Everything *after* VO_SENSE (F1, R30, the leads, R10) is **not** corrected.
  Note that the `output_buffer.py` comment "R9's drop is NOT corrected
  downstream" is easy to misread: R9 *is* inside the loop.
- **Current limit.** The repository's numbers correspond to
  `I_LIM = (15 kΩ × 4.75 V)/(13.75 kΩ + R_CL)`. With R_CL = 1.43 MΩ this gives
  **49.35 mA** **[DS-check: formula and its accuracy at such a low setting]**.
  The design uses the OPA548's internal limit in place of a discrete
  comparator (`manifest.yaml`, "OPA548 native ILIM used instead of a discrete
  comparator"). The data-sheet limit accuracy, and its temperature
  dependence, at ≈ 50 mA (only about 1 % of the device's 5 A range) should be
  checked → **R4**. The repository gives no reason for C14 (10 nF across R7).
  **Inference:** it filters noise on the ILIM node.
- **Headroom (repository numbers, citing SBOS070 at I_O = 0.6 A):** the output
  must reach 10.04 + 0.04935 × 10 = 10.53 V.
  - Guaranteed swing is (V+) − 2.4 = +12.6 V and (V−) + 1.3 = −13.7 V.
  - The input common-mode limit is (V+) − 3.0 = +12.0 V, against 10.04 V on +IN.
  - The worst margin is about 2 V. This holds with the ±15 V rails at nominal.
    It shrinks if the bench supply is set lower **[DS-check]**.
- **E/S pin.** R8 ties it to +15VA through 10 kΩ. The manifest says MCU control
  of E/S is deferred because it "needs level shifting".
  **Inference:** that implies E/S is referenced to V−. Check the pin's allowed
  voltage range with a 10 kΩ pull-up to V+, and check whether its thermal-flag
  output function is affected **[DS-check]** → **R5**.
- **Dissipation (analysis).** For a passive DUT,
  V_OUT ≥ I × (R9 + R_F1 + R30 + R10), which is at least ≈ 5.7 V at 49 mA.
  So the output-stage dissipation is at most about
  (15 − 5.7) V × 49 mA ≈ 0.46 W. Quiescent dissipation, 30 V × I_Q, comes on
  top of that **[DS-check I_Q]**. There is **no heatsink in the BOM**, and U5
  is a vertical TO-220-7 → **R6**. **[DS-check]:** the OPA548 tab is
  electrically connected to V−, so any heatsink or metal enclosure contact is
  at −15 V.

### 5.4 Force path, DUT terminals, compliance (Sheet 8)

**Topology (design fact):**
`VO_SENSE → F1 (PTC, 0ZRM0010FF1E) → F1_MID → R30 100 Ω "1/4 W" (1206) → FORCE+ → J4`.
D5 (SMBJ15CA, bidirectional TVS) is connected from FORCE+ to AGND.
J5 SENSE+, J6 SENSE− and J7 FORCE− are wired straight through to Sheets 6 and 5.
The four jacks are separate 1-pin 4 mm banana footprints (`Connector:Banana_Jack_1Pin`).
FORCE and SENSE therefore meet **only at the DUT**, which is the point of a
Kelvin connection.

**DUT compliance (analysis, consistent with repository comments).**
V_DUT ≈ V_SWEEP − I·(R_F1 + R30 + R10) − lead drops, with R30 + R10 = 110 Ω.

| DUT current | Drop in R30 + R10 | Maximum \|V_DUT\| (V_SWEEP = 10.04 V) |
|---:|---:|---:|
| 1 µA | 0.11 mV | ≈ 10.04 V |
| 1 mA | 0.11 V | ≈ 9.93 V |
| 10 mA | 1.10 V | ≈ 8.94 V |
| 49.35 mA (ILIM) | 5.43 V | ≈ **4.61 V** minus I·R_F1 |

F1's resistance is not recorded in the repository **[DS-check]**.

**Why R30 is there.** `protection.py` describes it as a passive dead-short
limiter, one of four protection layers (§10). It costs compliance at high
current, and the repository says so explicitly.

**Power in R30.** At the current limit (for example a shorted DUT) R30
dissipates 0.049² × 100 ≈ **0.24 W**. That is about 97 % of the stated 1/4 W
rating, continuously, for as long as the short lasts → **R3**.

### 5.5 Current sense (Sheet 5)

**Topology (design fact):**

- R10 is a 10.0 Ω, 1 W axial resistor (DIN0207, 10.16 mm pitch). It sits in
  the **low side**, between FORCE− (J7) and AGND.
- U6 (INA128P, DIP-8): +IN = FORCE− (the shunt high side, also TP3
  "ISENSE_HI"). −IN = AGND. **REF = VREF_2V5.** R11 = 35.7 kΩ 0.1 % across
  RG1/RG2. Supplies are ±15VA with 100 nF each.
- The output I_SENSE_OUT (TP4) goes to ADS1256 AIN0 through R15 100 Ω and is
  converted against AINCOM (VREF_2V5). C24 1 nF runs from AIN0 to AIN1, which
  R16 100 Ω ties to AGND; AIN1 is no longer part of the measurement.

**Scaling:**

```
G_I = 1 + 50 kΩ / 35.7 kΩ = 2.4006
I_SENSE_OUT = VREF_2V5 + G_I · R10 · I = 2.500 V + 24.006 V/A · I
ADC reads AIN0 − AINCOM = 24.006 V/A · I     → ±1.185 V at ±49.35 mA, ±2.192 V at ±91.3 mA
```

- **Why G = 2.40.** The gain is sized to the largest current any passive DUT
  can draw, `V_SWEEP / (R30 + R10) = 10.04 V / 110 Ω = 91.3 mA`, which holds
  whatever the OPA548 limit does. SBOS070 gives the limit tolerance only at a
  2.5 A setting (±100 mA typ, ±250 mA max), so the ~49 mA setting is not a
  bound the measurement may rely on. Measurable: ±97.8 mA guaranteed (ADC pin
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
  - The tolerance and TCR of R10 and R11 are not specified in their values
    ("10.0R 1W", "13.0k").
  - The INA128 gain-equation error at G ≈ 4.85 comes on top of that
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
  - D1 (1N4148) from SENSE+ to +15VA and D2 from −15VA to SENSE+.
  - D3 and D4 do the same on SENSE−.
  - The clamps go to the amplifier rails, **not** to AGND. That way legitimate
    signals within the INA128 common-mode range are never clipped
    (`voltage_sense.py`).
- U7 (INA128P): **REF = VREF_2V5.** **R14 across RG1/RG2 is DNP**, so RG is
  open and **G = 1**. The output `V_SENSE_OUT = 2.500 V + 0.1992·V_K` (TP7)
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
| Physical Kelvin voltage | ±10.04 V as I → 0, about ±4.6 V at ILIM |
| At U7's inputs | k·V: ±2.0 V differential, common mode < 2.2 V — far inside (V−)+2 … (V+)−2 V **[DS]** |
| U7 output | 0.50 … 4.50 V |
| ADS1256, PGA 2, buffer off | ±2.500 V full scale ↔ ±12.55 V; pin window ↔ **±11.8 V guaranteed** (§6) |

**Loading of the DUT.** Each Kelvin input now has 50.2 kΩ to AGND. SENSE+
draws up to 200 µA at 10 V; it is supplied through FORCE+ and returns directly
to AGND, not through R10, so the current reading is unaffected and the Kelvin
error is 200 µA × the sense-lead resistance (≈20 µV for 0.1 Ω). SENSE−
diverts `I·R10/50.2 kΩ` around the shunt: a 0.02 % gain term. D1–D4 leakage
(nA class, rising with temperature **[DS-check]**) is unchanged; on SENSE− it
bypasses the shunt and matters only at nA currents, already below the current
channel's floor (§5.5). With the SENSE leads unplugged, R32/R33 pull U7's
inputs to AGND and the channel reads 0 V instead of saturating.

**Divider matching.** Mismatch between the two dividers converts common mode
into error only through V(SENSE−) ≈ I·R10: ≤ 3 mV at the 92 mA bound for
0.1 % parts, proportional to I. Use one thin-film series (≤ 25 ppm/°C) for
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
| XTAL2 (18), XTAL1/CLKIN (19) | Y1 7.68 MHz, C27/C26 22 pF to **AGND** |
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
  0 … AVDD − 2 V, leaving only +0.5 V above the zero, so it suits only small
  signals.
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
reach is measured, with its sign.**

The ADS1256 runs from a single +5 V analog supply, so each input pin must stay
inside AGND − 0.1 V … AVDD + 0.1 V with the input buffer off, or AGND …
AVDD − 2 V with it on, at any PGA **[DS SBAS288K]**. Its *differential* range,
±2·VREF/PGA, is bipolar. The design therefore puts the measurement zero at the
2.500 V reference: both INA128 REF pins and the ADC's AINCOM are on VREF_2V5,
and each channel is converted as AINx − AINCOM.

| Quantity | Physically applied | Presented to ADC (AINx; AINx − AINCOM) | Measurable (PGA 2, buffer off) |
|---|---|---|---|
| DUT voltage V_K | −10.04 … +10.04 V | 0.50 … 4.50 V; 0.1992·V_K = ±2.000 V | **±11.8 V guaranteed** at AVDD = 4.75 V; ±12.55 V at nominal full scale |
| DUT current I | −91.3 … +91.3 mA (any passive DUT, any ILIM) | 0.31 … 4.69 V; 24.0 V/A·I = ±2.19 V | **±97.8 mA guaranteed**; ±104.1 mA at nominal full scale |
| Worst-case tolerances stacked | V_SWEEP +0.5 %, R30/R10 ±1 %, INA128 gain ±0.5 %, VREF ±0.1 %, INA128 offset | AIN span V 0.48…4.52 V, I 0.25…4.75 V | headroom to the pin limit: V 334 mV, I 97 mV |

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
  - VIN = +5VA (C10 100 nF). VOUT → **VREF_2V5** with C9 1 µF.
  - TRIM/NR, TEMP and the NC/DNC pins are not connected. So there is **no
    noise-reduction capacitor on TRIM/NR**. That is optional per the data
    sheet **[DS-check]** → **R12**.
- Loads on VREF_2V5:
  - the U4 DAC reference input (code-dependent),
  - the R1 + R2 divider (20 kΩ, about 125 µA),
  - the U8 VREFP switched-capacitor input,
  - the U6 and U7 REF pins, 4…56 µA each (`(VREF − A2out)/80 kΩ`), far below
    the REF5025's ±10 mA; the INA128 needs REF driven from a low impedance
    (8 Ω in series costs ≈80 dB CMR at G = 1 **[DS]**), which a reference
    output is,
  - the U8 AINCOM input (the negative input of both conversions), bypassed
    locally by C44 100 nF.
- **What sharing the reference buys (analysis).**
  - V_SWEEP is fully ratiometric: its zero sits at VREF/2 and its span is
    proportional to VREF.
  - Both ADC readings are proportional to 1/VREF, so V and I carry the
    **same** scale error. The measurement zero is VREF_2V5 itself on both
    sides of the conversion (INA128 REF and AINCOM), so VREF's absolute
    value and its noise cancel from the offset. Any **ratio** of them, such as resistance or
    dynamic resistance, is independent of VREF's absolute error. The
    absolute V and I values still depend on VREF's accuracy and drift
    **[DS-check grade A specs]**.
- **Coupling risk (Inference).** The ADC's sampled reference input, its
  AINCOM input, the two INA128 REF pins and the DAC's code-dependent reference
  current share one node. C9 (1 µF) sits at U2 and C44 (100 nF) at U8. The
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
| +5VA | +5V_RAW through FB1 (BLM21PG221SN1D) | U2, U4, U8 AVDD | C7 100 µF (AGND), C10, C13, C28, C29 |
| +3V3D | U1 LD1117S33 from +5V_RAW | U9, U8 DVDD, SYNC/PDWN, pull-ups, J2 pin 4 | C2 10 µF, C4, C8 100 µF, C34–C37, C39, C30 |
| VDDA | +3V3D through FB2 | U9 VDDA | C38 100 nF, C40 1 µF |
| VREF_2V5 | U2 | U4, R1/R2, U8 | C9 1 µF |
| VBUS | USB host via J3 | **Sensing only:** U10 VBUS pin, R28/R29 divider to PA0, C42 | — |

Observations (design facts, with consequences as inference):

- **The board is powered only by the bench supply.** VBUS powers nothing. The
  `mcu.py` comment on the USB shield describes the board as "bus-powered",
  which is inconsistent with the circuit (§13.2).
- There is **no on-board regulation, input fuse, reverse-polarity protection
  or sequencing** on ±15 V or +5 V. Analog performance (INA128 and OPA548
  PSRR) depends directly on the quality of the bench supply → **R11**.
- U1 is fed from +5V_RAW, on the digital side of FB1. So digital load current
  never flows through FB1, as intended in `power.py`.
- Ratings. The electrolytic and tantalum voltage ratings were chosen for
  derating (`docs/silkscreen_and_packages.md`):
  - 25 V parts on the 15 V rails,
  - 35 V tantalums at U5,
  - C1 is 10 µF/25 V on 5 V.
- The "PWR_FLAG" symbols (#FLG1–#FLG8) are ERC annotations only. They mark
  rails that enter from off-board. They do not appear on the PCB.

### 8.2 Power sequencing and abnormal states (Inference; review)

- **Power-up output state.** The DAC8552 powers up at a defined reset code.
  The author understands this to be zero-scale **[DS-check]**. Once VREF is up,
  zero-scale means V_SWEEP ≈ **−10.04 V**, and U5 is always enabled. So until
  firmware writes mid-scale, the DUT could see the full negative sweep voltage
  through 110 Ω. The same applies during an MCU reset or a firmware hang →
  **R8**.
- **Partial supply states.** For example, ±15 V present without +5 V: the
  INA128 outputs then drive an unpowered ADC through 100 Ω. The reverse case
  leaves the DAC and ADC powered with the amplifiers unpowered. Neither case is
  handled in hardware.

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
| Status LED | PC13 | LED_STATUS | R23 330 Ω → LED1 (green) → DGND |
| SWD | PA13 / PA14, NRST | SWDIO / SWCLK | J2: 1 SWDIO, 2 SWCLK, 3 GND, 4 +3V3D, 5 NRST |
| HSE | PH0 / PH1 | OSC_IN / OSC_OUT | Y2 8 MHz, C32/C33 20 pF |
| Reset / boot | NRST, BOOT0 | | R21 10k pull-up + C41 100 nF + SW1. BOOT0 has a 10k pull-down (R22) |
| Core | VCAP1 → C43 2.2 µF. VBAT tied to +3V3D. VSSA on DGND | | |
| Unused | 33 GPIOs declared not connected | | Listed in `blocks/mcu.py` |

Notes and review points:

- **PA9 / USB VBUS sensing.** On STM32F4 parts, PA9 is the OTG_FS VBUS-sense
  pin **[DS-check]**. This design uses PA9 as an ADC reset output and senses
  VBUS on PA0 through a divider. **Inference:** firmware must disable the USB
  peripheral's hardware VBUS sensing and handle VBUS detection itself.
- **PC13 LED drive.** ST documents that PC13–PC15 have very limited current
  capability and must not be used as a current source, for example to drive an
  LED **[DS-check]**. This design sources about 4 mA from PC13 into LED1 →
  **R13**.
- **C43 (VCAP1) = 2.2 µF.** `manifest.yaml` records C43 as an addition that was
  not in the source BOM. Confirm the value the STM32F411 data sheet requires
  for the single-VCAP package **[DS-check]** → **R13**.
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

### 10.1 Protection layers on FORCE+ (as described in `protection.py`)

| Layer | Part | Intended role (repository) | Observations (analysis) |
|---|---|---|---|
| 1 | U5 ILIM (R7) | Fast, primary current limit (≈ 49 mA) | Accuracy at a low setting **[DS-check]** (R4). U5 also has internal thermal shutdown **[DS-check]** |
| 2 | F1 PTC 0ZRM0010FF1E (100 mA hold per `libraries/netclasses.py`) | Slow backstop if ILIM fails | See the fault scenarios below |
| 3 | R30 100 Ω, 1/4 W, 1206 | Passive dead-short limiter | At ILIM it runs at about 97 % of rating (R3) |
| 4 | D5 SMBJ15CA to AGND | Absorbs transients. The 15 V stand-off clears the ±10.04 V sweep | Its leakage returns to AGND without passing through the DUT or R10, so it does **not** corrupt the current reading. **Inference** |

**Fault scenarios (analysis, for review):**

| Scenario | Expected behavior | Point to check |
|---|---|---|
| DUT short, ILIM working | I ≈ 49 mA. R30 about 0.24 W, U5 about 0.46 W plus quiescent | R30 rating and U5 temperature (R3, R6) |
| DUT short, ILIM failed or set wrong | Current is limited only by R9 + F1 + R30 + R10 ≈ 120 Ω+, about 100 mA at 12.6 V swing. R30 would see about 1 W, and F1 is near its **hold** current, so it may not trip | Whether R30 fails before F1 trips, i.e. whether the layer ordering actually works (R3) |
| External voltage applied to FORCE+ (charged capacitor, powered DUT) | D5 clamps and F1 limits sustained current | D5 energy rating against realistic sources. U5 output back-driven through R30/F1 |
| External voltage applied to FORCE− | **No dedicated protection.** R10 (1 W) goes straight to AGND, and U6 +IN is directly on FORCE−. `protection.py` says FORCE− protection "lives on" Sheet 5, but Sheet 5 has none | The INA128 input overvoltage rating **[DS-check]** and R10 (R15) |
| Overvoltage on SENSE± | D1–D4 clamp the jack-side node to ±15VA. R12/R13 (40.2 kΩ) and R32/R33 then divide it: U7 sees at most ≈3.1 V | The clamps sit **before** the series resistors, so fault current goes straight into the ±15 V rails through the 1N4148s. The bench supply may not sink that current (R15) |
| Board unpowered, DUT connected | The sense clamps are referenced to 0 V rails. The U5 output state is undefined | — |
| Power-up / MCU reset | V_SWEEP can reach −10.04 V until firmware writes mid-scale (§8.2) | R8 |

### 10.2 DNP and configuration options

| Ref | Fitted state | Effect | Notes |
|---|---|---|---|
| **R14** (0603 across U7 RG) | **DNP**. Value text is "DNP (G=1)" | U7 G = 1; with the k = 0.1992 attenuator the Kelvin range is ±11.8 V | DNP is expressed **only in the Value field**. The KiCad symbol has `(dnp no)` and `(in_bom yes)`, and the footprint is not marked DNP. A BOM or pick-and-place export will list R14 as a part unless someone intervenes → **R17**. If fitted, G = 1 + 50k/R14 and the measurable range shrinks to ±11.8/G V (useful only if the sweep is restricted to match). The pads connect to the high-impedance RG nodes, so contamination across the empty pads adds a leakage term to RG. For example, 100 MΩ of leakage gives a +0.05 % gain error |
| R11 35.7 kΩ 0.1 % | Fitted | U6 G = 2.4006, 24.0 V/A | Sized so ±91.3 mA (the physical current bound) stays on-scale; `validation/measurement_range.py` fails the build if it is raised past what the ADC window allows. There is no range switching |
| R12/R13 40.2 kΩ, R32/R33 10.0 kΩ, all 0.1 % | Fitted | Kelvin attenuator k = 0.1992 | The two legs must stay equal (checked by the build); k's tolerance is the voltage channel's gain tolerance |
| R4/R6 20.0 kΩ | Fitted | ±10.04 V sweep | 10.0 kΩ gives ±5.02 V, the previous configuration |
| R7 1.43 MΩ | Fitted | ILIM ≈ 49 mA | Sets the protection threshold |
| DAC channel B (U4 VOUTB) | Not connected | Reserved | — |
| ADS1256 AIN4–AIN7, D0–D3 | Not connected | Spare | These could only be used after rework, because the pads are not connected |
| U5 E/S | Tied enabled through R8 | No software shutdown | "Deferred extension point" (`output_buffer.py`) |

There are no jumpers or solder-bridge options in the design.

---

## 11. PCB and layout considerations

### 11.1 What is in the repository versus what you received

| Aspect | Repository `stage1_iv.kicad_pcb` (generated) | Routed board (human) |
|---|---|---|
| Routing | **None** (0 tracks, 253 unrouted connections) | Routed; predates the bipolar-measurement change (`docs/bipolar_measurement.md` §5) |
| Placement | Automatic shelf-packing by sheet. Not optimized | Significantly re-placed |
| Outline | 132.2 × 117.8 mm rectangle, 4 × M3 holes inset 5.5 mm | 107.3 × 62.3 mm (`routed/diodes.kicad_pcb` Edge.Cuts) |
| Layers | 2-layer, 1 oz, 1.6 mm FR-4 (assumed fab, `docs/FAB_CONSTRAINTS.md`) | Check |
| Ground pours | AGND zone (left) and DGND zone (right), **both layers**, 2 mm gap at x ≈ 52.2–54.2 mm. R31 and U8 straddle the gap | Check whether the routed board kept this strategy |
| Netclasses / rules | In `stage1_iv.kicad_pro` and `stage1_iv.kicad_dru` (generated from `libraries/netclasses.py`) | Probably inherited if the same project file was used |
| KiCad version | Board schema 20241229, which carries a `(units …)` block; the repository says **KiCad 10** is required | — |

Because placement changed, ignore the repository board's physical
arrangement. For example, in the generated board the power block — U2 REF5025
and the AGND-returned C5/C6/C7/C9/C10 — and most ADC support parts sit over
the DGND pour. The **netclasses, the custom DRC rule and the split-ground
intent** are the parts that carry over.

### 11.2 Board setup carried by the project file (design fact)

| Netclass | Nets | Clearance / track (mm) | Rationale (repository) |
|---|---|---|---|
| POWER_HV | +15VA, −15VA | 0.30 / 0.50 | 2× the fab minimum, for the 30 V rail-to-rail |
| POWER_LV | +5V_RAW, +5VA, +3V3D, VDDA, VBUS | 0.20 / 0.40 | Covers U1's 800 mA rating |
| GROUND | AGND, DGND | 0.20 / 0.40 | Poured. Width applies to stitching and the R31 link |
| FORCE | FORCE+, FORCE−, VO_RAW, VO_SENSE, F1_MID | 0.25 / 0.50 | Sized for the fault case (F1 100 mA hold) |
| SENSE | SENSE±, VREF_2V5, V_DAC, VREF_HALF, V_SWEEP, I_SENSE_OUT, V_SENSE_OUT | 0.25 / 0.25 | High-impedance precision. Extra clearance for surface leakage |
| DIGITAL | SPI1/SPI2, DRDY, RESET_ADC, SWD, NRST, BOOT0, LED, VBUS_SENSE | 0.20 / 0.25 | Density compromise around the LQFP-64 and SSOP-28 |
| USB | USB_DM/DP and their _ESD/_CONN segments | 0.20 / 0.38, pair gap 0.20 | About 100–110 Ω on 2 layers. A **stated compromise** for full speed |
| LOCAL_ANALOG | 23 block-local analog nodes (RG, filter, crystal, ILIM …) | 0.25 / 0.25 | Same character as SENSE |

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
   U2 to U4, R1, U8 VREFP/AINCOM (C44 at U8) and the U6/U7 REF pins. The REF
   traces are best tapped near U2/C9 or U8, paired with I_SENSE_OUT and
   V_SENSE_OUT respectively. Keep VREF_HALF, V_DAC and the U3B summing nodes
   compact.
6. **High-impedance nodes.** U7's RG pins and the empty R14 pads, the U6 RG
   pins, and the U7 input nodes (8 kΩ Thevenin behind the attenuator): check
   clearance and freedom from flux contamination. R32/R33 should return to
   AGND at one point, beside U7, so AGND noise is common-mode to U7.
7. **U5 thermal and mechanics.**
   - Copper area and heatsink provision for the TO-220-7.
   - Clearance for the tab at −15 V **[DS-check]**.
   - The lead stagger matching the purchased part (R1).
   - R30, R9 and R10 kept clear of heat-sensitive precision parts (U2, U3, R1–R6).
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
11. **Silkscreen for J1: known error in the repository board.** The generated
    silkscreen legend reads
    `J1 (bench supply), pin 1 -> 4:  1 +15V  |  2 GND  |  3 -15V  |  4 +5V`, on
    F.SilkS (`stage1_iv.kicad_pcb` `gr_text`, generated by
    `generators/pcb.py` lines ~1088–1100). The schematic and netlist instead
    have **J1.1 = +15VA, J1.2 = −15VA, J1.3 = +5V_RAW, J1.4 = DGND**, and the
    J1 symbol value is "+15V_RAW/−15V_RAW/+5V_RAW/GND". Only pin 1 agrees.
    Wiring the supply to follow this silkscreen would put −15 V on +5V_RAW and
    +5 V on ground. Check whether the routed board kept this text → **R0**.
12. **Designators and polarity.** The repository board places some reference
    designators on F.Fab rather than silkscreen. For the routed board, confirm
    that assembly-critical markings are present: tantalum and electrolytic
    polarity, diode cathodes, U5/U6/U7 pin 1, and the DUT jack labels.
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
             ──► stage1_iv.kicad_pcb      (generated, placed, unrouted board)
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
  opens without external libraries. The footprints used (31 files, including
  the mounting hole) are vendored in `libraries/footprints/`.
- The schematic uses the KiCad 7 format (version 20230121). Newer KiCad
  versions migrate it on load.
- **Human edits.** The routed PCB (`routed/diodes.kicad_pcb`, KiCad 10.0)
  was changed by hand (placement and routing), and its schematic gained C31,
  C44 and C45. C44 is now in the SKiDL source; C31 and C45 are not. `main.py`
  regenerates only the root `stage1_iv.kicad_pcb` and never touches
  `routed/`. `tools/compare_routed_board.py` lists what separates the routed
  board from the SKiDL design (today: the bipolar-measurement change, plus
  C31/C45).
- **Re-run check (bipolar-measurement revision).**
  - `python3 main.py` with the pinned KiCad 9.0.9.1 libraries: structural
    check PASS (113 parts, 362 pads, 313 netted), SKiDL ERC 0 errors / 0
    warnings, 11/11 connectivity assertions, measurement-range check PASS,
    Update PCB from Schematic a no-op on the generated board.
  - `tools/kicad_roundtrip.py` (kicad-cli 7.0.11) reads 313 connections in
    66 nets across 113 components, matching the netlist.

---

## 13. Verification status and documentation discrepancies

### 13.1 What has and has not been verified

| Evidence | Status in the repository |
|---|---|
| SKiDL ERC, 11 custom connectivity assertions, measurement-range check, schematic ↔ netlist equivalence | Pass, reproduced for this revision (§12) |
| Symbol and footprint identifiers resolve. Pin numbers are real pads. No stray pads | Reported pass when KiCad libraries are present (CI). Not reproduced here, because no KiCad libraries were available |
| KiCad netlist export from the schematic matches | Reported pass (KiCad 7.0.11 `kicad-cli`) |
| KiCad **ERC dialog** | **Not run** (`manifest.yaml` → `unresolved_items`). Only re-implementations of some rules were run |
| KiCad **DRC** on the routed board | Not in the repository. The generated board was DRC-checked only in part, with an older KiCad and in an unrouted state |
| U5 OPA548 pinout and lead stagger | **Unverified against the data sheet** (`VERIFY-DATASHEET`) |
| Circuit simulation (SPICE), thermal analysis | **None in the repository** |
| Measurement-range arithmetic (worst-case ADC pin voltages, full scale) | `validation/measurement_range.py`, on every build |
| Routed board vs the SKiDL design | `tools/compare_routed_board.py`: the bipolar change is **not yet applied** to `routed/` (3 values, 2 new resistors, 3 pad nets); C31/C45 exist only on the routed board |
| Bench testing, prototype measurements, calibration data | **None in the repository** |
| Firmware | **None in the repository** |

So the evidence establishes **netlist and identifier consistency**. It says
nothing on its own about **electrical performance**. Every performance
statement in this document comes from calculation.

### 13.2 Discrepancies found in repository text (do not affect connectivity)

| Where | Statement | Actual design |
|---|---|---|
| `generators/pcb.py` / board silkscreen | J1 legend "1 +15V, 2 GND, 3 -15V, 4 +5V" | J1: 1 +15V, 2 −15V, 3 +5V, 4 GND (**safety-relevant**, R0) |
| `blocks/protection.py` | FORCE− protection "lives on" Sheet 5 | Sheet 5 has no protection on FORCE− |
| `blocks/mcu.py` (shield comment) | "The board is bus-powered" | Powered only from J1. VBUS is only sensed |
| `blocks/output_buffer.py` | "R9's drop is NOT corrected downstream" | R9 is inside the loop. The drops *after* VO_SENSE are the ones not corrected |
| `README.md` / `dac_sweep.py` | "Top 0.40 % of DAC codes unused" | About 0.2 % at each end exceeds ±10.000 V |
| `docs/pcb_generation.md` | "247 connections", "28 footprints" | The generated board now has 253 unrouted connections and 113 footprints. 31 vendored footprint files |
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

- **R0 — J1 supply silkscreen.**
  - Fact: the generated board's J1 legend does not match the schematic pinout
    (§11.3 item 11).
  - Check: the routed board's J1 markings, and any wiring or harness
    documentation.
- **R1 — OPA548 (U5) pinout and footprint.**
  - Fact: the pin numbers (1 +IN, 2 −IN, 3 ILIM, 4 V−, 5 V+, 6 OUT, 7 E/S) and
    the StaggerOdd footprint come from a third-party library.
  - Check: both against TI SBOS070 and the purchased package variant.
- **R2 — Bipolar measurement (§6). Addressed in the schematic; not yet on the
  routed board.**
  - Fact: the measurement is now bipolar over the whole source span, and no
    normal operating point drives an ADC input outside its window.
  - Check: that the routed board receives the change
    (`docs/bipolar_measurement.md` §5, `tools/compare_routed_board.py`), and
    on the bench that a −V → +V → −V sweep of a resistor reads a straight
    line through the origin with no step at 0 V.
- **R3 — R30 rating and protection ordering.**
  - Fact: R30 dissipates about 0.24 W at ILIM on a 1/4 W 1206.
  - Check: the continuous short-circuit case, and the failed-ILIM case, where
    R30 may overheat before the 100 mA-hold PTC trips.
- **R8 — Output state at power-up, reset and firmware fault.**
  - Check: the DAC8552 power-on code, and the resulting V_SWEEP (possibly
    −10.04 V) applied to the DUT before firmware runs.
  - Check: whether this is acceptable without an output disable or relay.

### B. Component and data sheet checks

- **R4 — OPA548 current limit.** The formula, the accuracy and the
  temperature coefficient at a 49 mA setting. The role of C14.
- **R5 — OPA548 E/S** tied to +15 V through 10 kΩ. Check this against the pin's
  ratings and its status-output behavior.
- **R6 — U5 thermal.** Worst-case total dissipation (≈ 0.46 W output stage plus
  quiescent) in a free-standing TO-220 with no heatsink. The tab potential.
- **R7 — DAC8552 logic thresholds** at V_DD = 5 V with 3.3 V drive.
- **R9 — Current-channel low-end capability.** A 10 Ω shunt gives 10 µV per
  µA whatever the gain; at G = 2.40 the INA128P offset is 5 µA typ / 26 µA max
  and its drift 0.10 µA/°C typ, before calibration. The ADC's PGA (up to 64)
  adds resolution but not accuracy. Compare with the currents the application
  needs, such as diode leakage.
- **R10 — Accuracy of the scaling parts.** The tolerance and TCR of R10 are
  not specified (R11, R12/R13 and R32/R33 are now 0.1 %; their TCR tracking
  sets the drift of k). Also the INA128 gain error and the REF5025 grade.
- **R11 — Supply assumptions.**
  - No on-board regulation of ±15 V.
  - No reverse-polarity protection, fusing or sequencing.
  - The ground connection through USB to the host and earth.
- **R12 — Reference decoupling.** C44 (100 nF) now bypasses VREFP/AINCOM at
  the ADS1256 (it was already on the routed board). Still no NR capacitor on
  the REF5025, and one reference node shared by the DAC, the ADC reference,
  the ADC's negative input and both INA128 REF pins.
- **R13 — MCU details.**
  - PC13 sourcing LED current.
  - The C43 VCAP1 value.
  - PA9 being the OTG VBUS-sense pin.
  - BOOT0 hard-tied.
- **R14 — ADS1256 crystal.** C26/C27 return to AGND while the crystal pins are
  on the DGND side of the split.
- **R15 — Input protection.**
  - FORCE− has no TVS or fuse.
  - The SENSE clamps sit ahead of the series resistors and dump fault current
    into the ±15 V rails.
  - The INA128 input overvoltage ratings.
  - The D5 energy rating against realistic DUT stored energy.
- **R17 — R14 DNP representation.** DNP is shown only in the Value field. The
  KiCad DNP and BOM-exclusion attributes are not set. Also mark TP1–TP8 for
  BOM exclusion if wanted (`docs/pcb_generation.md`).
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
  DRC runs clean on the routed board with the project netclasses.

### D. Firmware assumptions implied by the hardware (no firmware in the repository)

- Initialize the DAC to mid-scale (code 32768) as early as possible.
- Clamp DAC codes to about 131…65405 for ±10.00 V. The measurement covers the
  full code range, so no further clamp is needed for it.
- ADS1256: MUX AIN2/AINCOM (voltage) and AIN0/AINCOM (current), PGA 2 for
  both, input buffer off; higher PGA only for small signals. Convert with
  V_K = code·2.5 V/(2²³·0.199203) and I = code·2.5 V/(2²³·24.0056 V/A).
  Keep SCLK within the ADS1256 limit.
- Disable hardware VBUS sensing on the USB OTG peripheral (PA9 is used as
  RESET_ADC).
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
| **PCB (repository version)** | `stage1_iv.kicad_pcb` | Generated, **unrouted**, not the final placement. The routed board is delivered separately |
| Custom DRC rules | `stage1_iv.kicad_dru` | Intra-footprint clearance only |
| Symbols / footprints | `libraries/stage1_iv.kicad_sym`, `libraries/footprints/*.pretty`, `sym-lib-table`, `fp-lib-table` | Project-owned copies |
| **SKiDL design source** | `blocks/*.py` (one file per sheet), `blocks/nets.py` (global nets), `main.py` (build entry) | The best place to read design intent. Docstrings carry the rationale |
| Part factories and pin aliases | `libraries/custom_symbols.py` | Maps design pin names to symbol pins |
| Pin provenance | `libraries/provenance/` (ADS1256 from SBAS288K; OPA548T from a third-party library) | See R1 |
| Netclasses and derivations | `libraries/netclasses.py`, `docs/FAB_CONSTRAINTS.md` | Assumed fab capability (no fab chosen) |
| Netlist | `stage1_iv.net` | Flat. For documentation and validation, not for PCB import |
| BOM-like table | `component_manifest.md` (generated), `manifest.yaml` | `manifest.yaml` also lists assumptions and unresolved items |
| PCB generation notes | `docs/pcb_generation.md`, `docs/silkscreen_and_packages.md` | Describe the generated board. Parts are stale (§13.2) |
| Identifier and library notes | `docs/identifier_resolution.md`, `docs/library_strategy.md`, `docs/LIBRARY_DEPENDENCIES.md`, `docs/power_flags_and_erc.md` | Toolchain history. Low review priority |
| Validation scripts | `validation/assertions.py` (topology checks), `validation/erc.py`, `validation/footprint_check.py`, `validation/schematic_check.py`, `validation/drc_check.py`, `validation/update_pcb_noop.py` | Structural and connectivity checks only |
| Generators and tools | `generators/schematic.py`, `generators/pcb.py`, `tools/*` | `pcb.py` ~L1088 is the source of the J1 silkscreen error |
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
| VREF_2V5 | U2.6 (VOUT), C9.1, R1.1, U4.2 (VREF), U8.4 (VREFP), **U8.5 (AINCOM)**, **U6.5 (REF)**, **U7.5 (REF)**, C44.1, TP8 |
| VREF_HALF_RAW / VREF_HALF | R1.2, R2.1, U3.3 / U3.1, U3.2, R5.1 |
| V_DAC | U4.4 (VOUTA), R3.1 |
| DIFF_P / DIFF_N | R3.2, R4.1, U3.5 / R5.2, R6.1, U3.6 |
| V_SWEEP | U3.7, R6.2, U5.1 (+IN), TP1 |
| VO_RAW | U5.6 (OUT), R9.1, C19.1 |
| VO_SENSE | R9.2, C19.2, U5.2 (−IN), F1.1, TP2 |
| OPA548_ILIM / OPA548_ES | U5.3, R7.1, C14.1 / U5.7, R8.1 |
| F1_MID | F1.2, R30.1 |
| FORCE+ | R30.2, D5.1, J4.1 |
| FORCE− | J7.1, R10.1, U6.3 (+IN), TP3 |
| SENSE+ | J5.1, R12.1, D1.2 (A), D2.1 (K), TP5 |
| SENSE− | J6.1, R13.1, D3.2 (A), D4.1 (K), TP6 |
| SENSE_P_AMP / SENSE_M_AMP | R12.2, R32.1, U7.3 / R13.2, R33.1, U7.2 |
| U6_RG1/2, U7_RG1/2 | U6.1/U6.8 across R11. U7.1/U7.8 across R14 (DNP) |
| I_SENSE_OUT | U6.6, R15.1, TP4 — 2.500 V + 24.0 V/A·I |
| V_SENSE_OUT | U7.6, R17.1, TP7 — 2.500 V + 0.1992·V_K |
| ADC_AIN0…3 | R15.2/U8.6/C24.1, R16.2/U8.7/C24.2, R17.2/U8.8/C25.1, R18.2/U8.9/C25.2 |
| AGND (selected) | R10.2, U6.2 (−IN), R32.2, R33.2, R16.1, R18.1, U8.2/.3, C44.2, U2.4, U4.8, R2.2, R4.2, D5.2, C26.2, C27.2, **R31.1** |
| DGND (selected) | J1.4, U1.1, U8.17, U9 VSS/VSSA, U10.2, J3 GND/S1, J2.3, **R31.2** |
| +15VA / −15VA | J1.1 / J1.2, plus the U3/U5/U6/U7 supplies, the D1/D3 cathodes / the D2/D4 anodes, R8 / R7, C14 |
| +5V_RAW / +5VA | J1.3, U1.3, FB1.1, C1, C3 / FB1.2, U2.2, U4.1, U8.1, C7, C10, C13, C28, C29 |

## Appendix B — Calculations

| Quantity | Expression | Value |
|---|---|---|
| Sweep gain | R4/R3 = 20.0k/2.49k | 8.0321 |
| Sweep span | ±8.0321 × 1.250 V | ±10.040 V |
| Sweep LSB | 2.5 V / 65536 × 8.0321 | 306.4 µV |
| Codes for ±10.000 V | 65536 × (1.25 ∓ 10/8.0321)/2.5 | ≈ 131 … 65405 |
| OPA548 ILIM | 15 kΩ × 4.75 V / (13.75 kΩ + 1.43 MΩ) [DS-check] | 49.35 mA |
| U5 output required | 10.04 V + 49.35 mA × 10 Ω | 10.53 V |
| Out-of-loop resistance | R30 + R10 (+ R_F1) | 110 Ω + R_F1 |
| DUT voltage at ILIM | 10.04 − 0.04935 × 110 | ≈ 4.61 V (− I·R_F1) |
| R30 power at ILIM | 0.04935² × 100 | 0.244 W (1/4 W part) |
| R9, R10 power at ILIM | 0.04935² × 10 | 24 mW each |
| U5 output-stage power, shorted DUT | (15 − 0.04935 × ~115) × 0.04935 | ≈ 0.46 W (+ quiescent) |
| Physical current bound, any passive DUT | 10.04 V / (R30 + R10) | 91.3 mA (92.5 mA worst tolerance) |
| INA128 gain, current channel | 1 + 50k/35.7k | 2.4006 |
| Current transimpedance | 2.4006 × 10 Ω | 24.006 V/A (24.0 mV/mA) |
| I_SENSE_OUT at ±ILIM / ±91.3 mA | 2.500 ± 24.006 × I | 2.500 ± 1.185 V / 2.500 ± 2.192 V |
| Current full scale, PGA 2 / pin-limited | 2.500 V / 24.006; 2.35 V / 24.006 | ±104.1 mA / ±97.9 mA |
| Kelvin attenuator | 10.0k / (40.2k + 10.0k) | k = 0.19920 |
| INA128 gain, voltage channel | 1 + 50k/∞ (R14 DNP) | 1.000 |
| V_SENSE_OUT at ±10.04 V | 2.500 ± 0.19920 × 10.04 | 2.500 ± 2.000 V |
| Voltage full scale, PGA 2 / pin-limited | 2.500 V / 0.1992; 2.35 V / 0.1992 | ±12.55 V / ±11.80 V |
| ADC pin window, buffer off | AGND − 0.1 … AVDD + 0.1 (AVDD ≥ 4.75 V) | −0.10 … 4.85 V |
| ADC input RC corner | 1 / (2π × 200 Ω × 1 nF) | ≈ 796 kHz |
| INA REF current | (2.5 V − A2out) / 80 kΩ | 4 … 56 µA per INA128 |
| VREF divider current | 2.5 V / 20 kΩ | 125 µA |
| VBUS sense | 5 V × 100k / 200k | 2.5 V at PA0 |
| LED current (V_F ≈ 2 V assumed) | (3.3 − 2.0) / 330 Ω | ≈ 4 mA from PC13 |
| USB-C Rd | R24, R25 | 5.1 kΩ each (UFP) |
