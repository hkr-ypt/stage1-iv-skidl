# Assembly and bring-up checklist — first boards

This is the procedure for the first assembled boards after the final hardware
audit corrections (`docs/audit_corrections.md`). Some of the corrections rest
on calculation or simulation, never on a measurement: the reference-gate
charge pump, the R30 thermal placement and the OPA548 behaviour at its
uncharacterised current-limit setting. This checklist is where they get
measured. Record every measured value. A **STOP** step means stop: do not go
on to the next section until it is understood.

The firmware requirements referred to here are in
[`firmware_handoff.md`](firmware_handoff.md). Test firmware is needed from
§6 on. This document does not supply it.

Test points (all on the **bottom** side, 1.5 mm pads):

| TP | Net | | TP | Net |
|---|---|---|---|---|
| TP1 | V_SWEEP (U3B out) | | TP5 | SENSE+ |
| TP2 | VO_SENSE (U5 loop node) | | TP6 | SENSE− |
| TP3 | ISENSE_HI (FORCE−, shunt high side) | | TP7 | V_SENSE_OUT |
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
  (a lead kink or a spacer), and keep it upright in its footprint outline,
  not leaning toward F1. In the rail-fault case it dissipates about 1.6 W
  (≈ 120 K hot-spot rise). Its courtyard is only 2.1 mm from F1, so the gap
  matters (§7).
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
  its anode, on pad 2, is on +3V3D now. D5 is bidirectional.
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
- [ ] **Mechanical.** The routed board has **no mounting holes** (the corners
  are taken by J7, J5, J1 and J3; see `docs/audit_corrections.md`). Support
  it on its connectors, in an edge clamp, or on an insulating tray, so the
  bottom-side test points stay reachable and no metal touches the bottom
  copper.

## 2. Unpowered checks

- [ ] J1's silkscreen reads **+15V, −15V, +5V, GND** for pins 1–4 under a
  BENCH SUPPLY heading. Wire the bench leads to match, and label the harness.
- [ ] Resistance from each J1 pin to J1 pin 4 (GND): none is a short
  (expect kΩ-class, rising as capacitors charge).
- [ ] AGND to DGND ≈ 0 Ω (R31; it is the only link).
- [ ] FORCE+ (J4) to F1_MID (R30 pad 1) ≈ 100 Ω (R30). VO_SENSE (TP2) to
  FORCE+ ≈ 100 Ω plus F1's resistance.
- [ ] DAC_ARM to DGND ≈ 100 kΩ (R37). DAC_VREF to AGND ≈ 1 MΩ (R35).
- [ ] U5 tab to V− (−15VA) ≈ 0 Ω, and to everything else open.

## 3. First power — MCU blank or held in reset

Use current-limited bench supplies: ±15 V at 50–100 mA, +5 V at 150 mA.
Bring them up together (§5 covers ordering). Hold the MCU in reset with SW1,
or leave it unprogrammed. **No DUT is connected.** Put a 1 kΩ resistor across
FORCE+/FORCE− with the Kelvin leads on its terminals.

- [ ] Supply currents at power-up, recorded per rail. **STOP** on any rail at
  its limit.
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

## 5. Power sequencing transients (characterise; not handled in hardware)

With the MCU in reset and the 1 kΩ load fitted, scope TP1, TP2 and FORCE+
while switching the supplies in different orders: all together; ±15 V first,
then +5 V; +5 V first, then ±15 V; and the reverse orders at power-down.

- [ ] Record the peak voltage and duration of any excursion on FORCE+.
  Anything above about 1 V into the load is a finding to report. Partial-supply
  states are a known open item (`DESIGN_DOCUMENT.md` §8.2).

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
- [ ] **Thermal soak on the plateau (R30 beside F1).** Hold the short at +10 V
  for 30 min at room temperature. Measure R30's body, F1's body and U5's tab
  with a thermocouple or IR camera. **F1 must not trip.** Record F1's
  temperature rise. Bel's 0ZRM0010FF1E hold current is 100 mA at 23 °C and
  68 mA at 50 °C, against a 49 mA plateau. If F1 runs warmer than about 45 °C,
  or trips, raise R30 higher off the board, or swap in 0ZRM0017FF1E (170 mA
  hold, same footprint), and report it. The layout could not put R30 farther
  from F1 (`docs/audit_corrections.md`).
- [ ] **Snubber decision (audit V1).** On the plateau, drive the load through
  a 1 m DUT cable, and then with 10 nF and 100 nF across the jacks. Scope U5
  OUT (VO_RAW, R9's U5-side pad) for oscillation. If it oscillates, fit
  **R38 = 10 Ω, C49 = 10 nF 50 V X7R** (SBOS070D §9.1) and repeat. Record the
  decision on the board's traveller.
- [ ] **Rail-fault envelope (optional; costs a board).** The design bounds a
  rail-to-DUT fault at 127 mA, 1.6 W in R30, which the PR02 is rated to carry.
  Test it only if a board can be spared, for minutes and not hours, with a
  thermocouple on R30. Record whether and when F1 trips. F1 is **not** a
  guaranteed backstop at this current.

## 8. Measurement checks and calibration

Follow `firmware_handoff.md` §4–§5.

- [ ] ADS1256: SCLK ≤ 1.92 MHz, CLKOUT off (ADCON CLK = 00), D1–D3 driven low,
  SELFCAL done.
- [ ] Zero with the gate disarmed: jacks open, then jacks shorted. Record the
  offsets of both channels.
- [ ] Gain: a 0.01 % 100 Ω resistor in a 4-wire connection, measured against a
  calibrated DMM at several points of each polarity.
- [ ] **Open-Kelvin detection.** With current flowing, lift SENSE+, then
  SENSE−. The firmware cross-check (Kelvin voltage against
  V_SWEEP − I·(R30 + R10 + R_F1)) must flag it.
- [ ] Hysteresis sweep of a known diode, −V → +V → −V: signed and continuous,
  and the plateau region is flagged as current limit, not as DUT behaviour.

## 9. Records to keep

Rail voltages and currents, VREF_2V5, the safe-state residual, DAC_ARM at
1 kHz and 500 Hz, the arm time, the six disarm times, the plateau current at
both polarities, the R30 / F1 / U5 temperatures after the soak, the snubber
decision, and the calibration constants. Update `docs/audit_corrections.md`
§"What is calculated, not measured" with the measured values.
