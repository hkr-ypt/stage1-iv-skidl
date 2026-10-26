# Assembly and bring-up checklist — first boards

This is the procedure for the first assembled boards after the final hardware
audit corrections (`docs/audit_corrections.md`) and the remaining hardware
corrections (`docs/final_corrections.md`). Some of the corrections rest on
calculation, data-sheet limits or simulation, never on a measurement: the
reference-gate charge pump, the F1 thermal placement, the OPA548 behaviour at
its uncharacterised current-limit setting, the U12 switching thresholds and
the R10 thermal EMF. This checklist is where they get measured. Record every measured value. A **STOP** step means stop: do not go
on to the next section until it is understood.

The firmware requirements referred to here are in
[`firmware_handoff.md`](firmware_handoff.md). Test firmware is needed from
§6 on. This document does not supply it.

Test points (all on the **bottom** side, 1.5 mm pads):

| TP | Net | | TP | Net |
|---|---|---|---|---|
| TP1 | V_SWEEP (U3B out) | | TP5 | SENSE+ |
| TP2 | VO_SENSE (U5 loop node) | | TP6 | SENSE− |
| TP3 | ISENSE_HI (shunt high side, after F2) | | TP7 | V_SENSE_OUT |
| TP4 | I_SENSE_OUT | | TP8 | VREF_2V5 |

DAC_ARM and DAC_VREF have no test point. Probe DAC_ARM at C48 pad 2 or R37,
and DAC_VREF at R35 pad 1. Use a 10 MΩ (×10) probe on DAC_ARM: a 1 MΩ probe
in parallel with R37 (100 kΩ) lowers DAC_ARM by about 9 % and shortens the
decay.

---

## 1. Assembly

- [ ] **Fit / DNP.** Leave **R14, R38 and C49 unfitted** (DNP; the board and
  schematic carry the DNP attribute and the BOM excludes them). Everything
  else in `component_manifest.md` with Fit = `fit` is fitted.
- [ ] **R30, Vishay PR02 2 W (PR02000201000FB100).** Axial, 12.7 mm pitch,
  1.2 mm holes. Form the leads so the **body sits about 3 mm above the board**
  (a lead kink or a spacer). In the worst rail fault it dissipates up to
  1.96 W (98 % of rating, `final_corrections.md` §2.7). F1 is now 14 mm
  away, body to body.
- [ ] **R10, Vishay Dale RS02B10R00BE12 (10.00 Ω 0.1 % 3 W wirewound).**
  DIN0614 footprint, 20.32 mm pitch. Form **both leads identically** and lay
  the body **flat**, parallel to the board, about 1 mm above it, so its two
  terminations sit at the same height and temperature (thermal EMF, §8).
- [ ] **F2, 0ZRM0010FF1E** (same part as F1), next to J7: FORCE− runs
  J7 → F2 → R10.
- [ ] **U12, Toshiba TLP176AM(TPL,E** (SOIC-4 photorelay). Pin 1 (LED anode)
  to the dot on the fab outline. Pins 1 (anode) and 3 (cathode) are the LED,
  4 and 6 the output; the package has no pins 2 and 5.
- [ ] **U5, OPA548T (TO-220-7, staggered).** The footprint is KiCad's
  staggered TO-220-7 with 1.2 mm drills. Check that the purchased part's lead
  form matches the stagger before soldering: the pin functions are verified
  against SBOS070D, but the stagger comes from the package drawing.
- [ ] **U5 tab is at V− (−15 V).** It must not touch any enclosure, bracket or
  grounded metal. If a heatsink is fitted, use an insulating pad and a
  shoulder washer, and check with an ohmmeter that the heatsink is open to the
  tab and to every rail (> 10 MΩ).
- [ ] **Orientation.** Check each of these against the silkscreen and the
  footprint pin 1: electrolytics C5–C8, tantalums C1, C2, C17, C18, C29,
  D1–D4 (1N4148W) and D6 (BAT54S). LED1's cathode is on pad 1, toward R23;
  its anode, on pad 2, is on +3V3D now. D5 is bidirectional. New parts:
  - **D8 (MBR0540, top):** cathode on +15VA, anode on AGND;
  - **D9 (MBR0540, bottom side):** cathode on AGND, anode on −15VA;
  - **D7 (BZT52C24, bottom side):** cathode on SSR_LED_K (U12 pin 3), anode
    on −15VA;
  - **U12:** pin 1 as above.
  A reversed D8 or D9 is a short across its rail at first power (§2 catches
  it).
- [ ] **Flux removal (required).** Wash the board with a cleaner suited to
  the flux used (IPA for rosin; a saponifier and DI rinse for water-soluble),
  then bake it dry (for example 1 h at 60–70 °C). These nodes are high
  impedance, so flux residue shows up directly as error or drift:
  - U7 inputs and the Kelvin attenuator R12/R13/R32/R33 (≈ 8 kΩ Thevenin);
  - the U6 and U7 RG pins, including the **empty R14 pads**;
  - the OPA548 ILIM node (R7 = 1.43 MΩ, C14);
  - DAC_VREF (R35 = 1 MΩ) and DAC_ARM (R37 = 100 kΩ, C48). Leakage here
    shifts the arm and disarm thresholds;
  - VREF_2V5 and the U3 summing nodes.
- [ ] **Mechanical.** Three isolated M3 holes, H1 (top edge, by J7), H2 and
  H3 (bottom edge). They have no copper and a 4.0 mm-radius copper keepout on
  both sides, so metal hardware is fine: washers ≤ 7.0 mm OD (DIN 125 M3) and
  standoffs ≤ 6.35 mm across corners (5.5 mm hex). There is no fourth hole
  (`final_corrections.md` §2.4): carry the front edge on the panel-mounted
  jacks J4–J7, and support the J1 corner with an insulating card guide or
  bumper. Tighten J1's screws before mounting. Bottom-side test points stay
  reachable from below the standoffs.

## 2. Unpowered checks

- [ ] J1's silkscreen reads **+15V, −15V, +5V, GND** for pins 1–4 under a
  BENCH SUPPLY heading. Wire the bench leads to match, and label the harness.
- [ ] Resistance from each J1 pin to J1 pin 4 (GND): none is a short
  (expect kΩ-class, rising as capacitors charge).
- [ ] AGND to DGND ≈ 0 Ω (R31; it is the only link).
- [ ] **Dead-rail clamps (diode test).** Meter + on AGND, − on +15VA: a
  Schottky drop, 0.1–0.4 V (D8). Meter + on −15VA, − on AGND: the same (D9).
  Reversed polarity reads open (capacitors charging). A short either way is a
  reversed or damaged D8/D9: **STOP**.
- [ ] **FORCE+ is open with the board unpowered** (U12 off): J4 (FORCE+) to
  R30 pad 2 (FORCE_SW) > 10 MΩ.
- [ ] VO_SENSE (TP2) to R30 pad 1 (F1_MID) = F1: 3.0–7.5 Ω. R30 pad 1 to
  pad 2 ≈ 100 Ω.
- [ ] J7 (FORCE−) to TP3 (ISENSE_HI) = F2: 3.0–7.5 Ω. TP3 to AGND
  ≈ 10.0 Ω (R10).
- [ ] DAC_ARM to DGND ≈ 100 kΩ (R37). DAC_VREF to AGND ≈ 1 MΩ (R35).
- [ ] U5 tab to V− (−15VA) ≈ 0 Ω, and to everything else open.

## 3. First power — MCU blank or held in reset

Use current-limited bench supplies: ±15 V at 50–100 mA, +5 V at 150 mA.
Bring them up together (§5 covers ordering). Hold the MCU in reset with SW1,
or leave it unprogrammed. **No DUT is connected.** Put a 1 kΩ resistor across
FORCE+/FORCE− with the Kelvin leads on its terminals.

- [ ] Supply currents at power-up, recorded per rail. **STOP** on any rail at
  its limit. Both ±15 V currents include the U12 LED string (next item).
- [ ] **U12 LED string.** Voltage across R39 (620 Ω): expect ≈ 4.8 V
  (≈ 7.7 mA, typical values at ±15 V); the data-sheet bounds give 3.0 mA at
  ±14.45 V and 11.6 mA at ±15.5 V. Below 1.9 V (3 mA) U12 is not guaranteed
  on: **STOP**.
- [ ] **U12 closed:** J4 (FORCE+) follows R30 pad 2. With 1 kΩ load and the
  output at 0 V this is a small-signal check; at 10 mA (§7) the drop across
  U12 (R30 pad 2 to J4) is ≤ 20 mV (RON ≤ 2 Ω).
- [ ] Rails: +15VA, −15VA, +5V_RAW, +5VA, +3V3D (3.3 V ± 2 %), VDDA, and
  **VREF_2V5 at TP8 = 2.500 V** (REF5025A: ±0.05 % initial).
- [ ] **Safe state, MCU not running.** DAC_ARM ≈ 0 V. DAC_VREF ≈ 0 V.
  TP1 (V_SWEEP) and TP2 (VO_SENSE) ≈ 0 V. The firmware handoff estimates the
  residual offset at ≤ 0.1–0.15 V; record it. **STOP if V_SWEEP is near
  −10 V**: that is the pre-correction fault, and means the midpoint or the
  gate is not working.
- [ ] CS_DAC (U4 pin 5) is high while the MCU is held in reset (R34 pull-up).
- [ ] RESET_ADC (U8 pin 15) is high (R20 pull-up).

## 4. Program the MCU

- [ ] SWD on J2 (1 SWDIO, 2 SWCLK, 3 GND, 4 +3V3D, 5 NRST). BOOT0 is tied low,
  so SWD is the only programming path.
- [ ] The LED lights when PC13 is driven **low** (sunk through R23, ≈ 1.3 mA).

## 5. Partial-supply states and sequencing (U12, D8/D9)

`final_corrections.md` §2.5 gives what is guaranteed (U12 open with either
rail absent; closed at ≥ ±14.45 V) and what is only expected (closing near
26 V total). This section measures it. MCU in reset, 1 kΩ load across the
jacks with the Kelvin leads on it.

- [ ] **+15 V only** (−15 V lead disconnected, not shorted): −15VA sits
  between 0 and +0.51 V (clamped by D9). Voltage across the load ≤ 0.5 V
  (expected ≈ 0: U12 is open; only D2/D4 can reach the Kelvin leads).
  Record both.
- [ ] **−15 V only**: mirror image, +15VA between −0.51 V and 0 (D8); load
  voltage ≤ 0.5 V. Record.
- [ ] **USB only** (bench supplies off): nothing on FORCE+, ±15VA and +5VA
  ≈ 0 V.
- [ ] **U12 thresholds.** Ramp ±15 V together slowly (tracking supply, or both
  current-limited supplies turned up together) and record the total
  rail-to-rail voltage at which J4 starts to follow R30 pad 2, and on the way
  down where it stops. Expected ≈ 26 V; it must lie between 16 V and 28.9 V.
- [ ] **Transients.** Scope TP1, TP2 and FORCE+ while switching the supplies
  in different orders: all together; ±15 V first, then +5 V; +5 V first,
  then ±15 V; one ±15 V rail at a time; and the reverse orders at power-down,
  with the sweep at 0 V and again at +5 V. Record the peak voltage and
  duration of any excursion on FORCE+. Anything above about 1 V into the load
  is a finding to report.

## 6. Reference gate and heartbeat (audit F2), with test firmware

Test firmware follows `firmware_handoff.md` §1–§3: B = `0xFFFF`, A = `0x8000`,
then a software heartbeat on PC5.

- [ ] **Arm.** Start the heartbeat at 1 kHz. DAC_ARM: expect ≈ 2.4 V
  (simulated 2.42–2.46 V; worst case 1.87 V; U11 needs 1.49 V). Record it.
  **STOP if it is below 1.7 V.**
- [ ] Time from the first PC5 edge until DAC_ARM crosses 1.49 V. The simulation
  says several edges; firmware assumes ≤ 10 ms.
- [ ] DAC_VREF = VREF_2V5 within about 1 mV (U11's Ron carries only the DAC's
  reference current).
- [ ] **Margin.** Heartbeat at 500 Hz: DAC_ARM ≥ 1.6 V (simulated worst case
  1.61 V). Record it.
- [ ] **Single edge.** Drive PC5 high once and hold it: DAC_ARM peaks near
  0.5 V (simulated 0.53 V), stays below 0.87 V, then decays. DAC_VREF stays
  at 0 V.
- [ ] **Disarm tests.** For each case below, first set A to +5 V (`0xBFFF`),
  then repeat with A at −5 V (`0x3FFF`). Scope TP1 and FORCE+ (1 kΩ load) and
  record the time until |V_SWEEP| < 0.2 V. Expect ≤ 60 ms (simulated
  ≈ 52 ms):
  - [ ] heartbeat stopped with PC5 left low;
  - [ ] heartbeat stopped with PC5 left high;
  - [ ] SW1 reset;
  - [ ] debugger halt;
  - [ ] a deliberate crash (for example a HardFault, or an infinite loop that
    skips the heartbeat);
  - [ ] +5 V supply removed (MCU and DAC unpowered).

  **STOP if any case leaves V_SWEEP at its last value.**
- [ ] **/SYNC during reset.** Scope CS_DAC while pressing SW1. It stays high
  throughout, so no DAC frame is clocked in.
- [ ] **Re-arm after reset.** After an SW1 reset the firmware rewrites B and A
  before arming. V_SWEEP comes back at the commanded value, not at the
  pre-reset one.
- [ ] **Positive-bound contract (negative test, briefly).** With the 1 kΩ load
  only, set B = `0x0000` and A = `0x8000` while armed. V_SWEEP should be
  about +10 V; that it is not 0 V shows the bound depends on B. Then restore
  B. This demonstrates why firmware must never write B below full scale.

## 7. Output stage, current limit and protection

- [ ] **Sweep into 1 kΩ** from −10 V to +10 V and back: the Kelvin and current
  channels track, with signed readings and no step at 0 V.
- [ ] **Current-limit plateau.** Short FORCE+ to FORCE− (Kelvin leads on the
  short) and command +10 V. Record the plateau current (nominally ≈ 49 mA;
  TI does not characterise ILIM at this setting). Repeat at −10 V.
- [ ] **Thermal soak on the plateau.** Hold the short at +10 V for 30 min at
  room temperature. Measure R30's body, F1's and F2's bodies and U5's tab
  with a thermocouple or IR camera. **Neither F1 nor F2 may trip.** Record
  their temperature rise. Bel's 0ZRM0010FF1E hold current is 100 mA at 23 °C
  and 68 mA at 50 °C, against a 49 mA *typical* plateau. F1 is now at the
  bottom edge, 14 mm from R30 and 23 mm from U5 (`final_corrections.md`
  §2.1). If either PTC runs warmer than about 45 °C, or trips, or the plateau
  is above ≈ 60 mA, swap in 0ZRM0017FF1E (170 mA hold, same footprint) and
  report it.
- [ ] **Snubber decision (audit V1).** On the plateau, drive the load through
  a 1 m DUT cable, and then with 10 nF and 100 nF across the jacks. Scope U5
  OUT (VO_RAW, R9's U5-side pad) for oscillation. If it oscillates, fit
  **R38 = 10 Ω, C49 = 10 nF 50 V X7R** (SBOS070D §9.1) and repeat. Record the
  decision on the board's traveller.
- [ ] **Rail-fault envelope (optional; costs a board).** The design bounds a
  DUT short at 128 mA and FORCE+ to AGND at 143 mA, with up to 1.96 W in R30
  (98 % of its 2 W rating) in the stacked worst case. Test it only if a board
  can be spared, for minutes and not hours, with a thermocouple on R30.
  Record whether and when F1 trips. F1 is **not** a guaranteed backstop at
  this current.
- [ ] **FORCE− external source (optional; then recalibrate).** A 12 V supply
  limited to 1.5 A, from J7 to AGND: F2 must trip (expected < 1 s) and R10
  survive. Recalibrate the current channel afterwards (R10's short-time
  overload allows ΔR ≤ 0.2 % + 0.05 Ω).

## 8. Measurement checks and calibration

Follow `firmware_handoff.md` §4–§5.

- [ ] ADS1256: SCLK ≤ 1.92 MHz, CLKOUT off (ADCON CLK = 00), D1–D3 driven low,
  SELFCAL done.
- [ ] Zero with the gate disarmed: jacks open, then jacks shorted. Record the
  offsets of both channels.
- [ ] **R10 thermal EMF.** Run the plateau (or 50 mA into a short) for 5 min,
  set the output to 0 V, and read the current channel at once and again after
  5 min. The difference against the cold zero is the shunt's thermal EMF
  (referred to the input: µV / 10 Ω). Record it; above ≈ 1 µA equivalent,
  check that R10 lies flat with symmetric leads.
- [ ] Gain: a 0.01 % 100 Ω resistor in a 4-wire connection, measured against a
  calibrated DMM at several points of each polarity.
- [ ] **Open-Kelvin detection.** With current flowing, lift SENSE+, then
  SENSE−. The firmware cross-check (Kelvin voltage against
  V_SWEEP − I·(R30 + R10 + R_F1 + R_F2 + R_U12)) must flag it.
- [ ] **U12 open state.** With a 1 kΩ load and +5 V commanded, remove −15 V:
  U12 opens, I → 0 while the commanded V stays; the firmware must report the
  output as disconnected (`firmware_handoff.md`), not as an open DUT.
- [ ] Hysteresis sweep of a known diode, −V → +V → −V: signed and continuous,
  and the plateau region is flagged as current limit, not as DUT behaviour.

## 9. Records to keep

Rail voltages and currents, the R39 voltage (U12 LED current), VREF_2V5,
the safe-state residual, the dead-rail voltages and load voltages of §5, the
U12 close/open thresholds, DAC_ARM at 1 kHz and 500 Hz, the arm time, the six
disarm times, the plateau current at both polarities, the R30 / F1 / F2 / U5
temperatures after the soak, the snubber decision, the R10 thermal-EMF
offset, and the calibration constants. Update `docs/audit_corrections.md`
§"What is calculated, not measured" with the measured values.
