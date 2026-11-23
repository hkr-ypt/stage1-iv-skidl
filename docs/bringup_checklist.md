# Assembly and bring-up checklist — first boards

This is the procedure for the first assembled boards after the final hardware
audit corrections (`docs/audit_corrections.md`) and the remaining hardware
corrections (`docs/final_corrections.md`). Some of the corrections rest on
calculation, data-sheet limits or simulation, never on a measurement: the
reference-gate charge pump, the F1 thermal placement, the U5 current-limit
plateau, the U12 switching thresholds and the R10 thermal EMF. This checklist
is where they get measured. The final design audit (`docs/final_audit.md`)
then changed fitted parts, values or order codes (U5, R7, D7, C26/C27, R30,
C17/C18) and fitted the U5 output network (R38 100 Ω, C49); they are marked
below. Record every measured value. A **STOP** step means stop: do not go
on to the next section until it is understood.

**Three rules for the whole procedure.** (1) Never connect anything that
holds voltage (a battery, a charged capacitor, a powered circuit) to the
jacks of an unpowered board, and nothing above ±15 V ever: the SENSE clamps
feed the rails directly (`final_audit.md` §6). (2) Probe VO_RAW and TP2 only
with a ×10 probe: capacitance hung directly on U5's output costs it
stability margin. (3) Touch AGND before handling the FORCE− lead.

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

- [ ] **Fit / DNP.** Leave **R14 unfitted** (DNP; the board and schematic
  carry the DNP attribute and the BOM excludes it). Everything else in
  `component_manifest.md` with Fit = `fit` is fitted. That now includes
  **R38 = 100 Ω and C49 = 10 nF 50 V X7R**, the U5 output network, which
  were DNP before the final audit (`final_audit.md` §3.7). R38 must be
  100 Ω: not the 10 Ω of earlier documents, and not TI's 3 Ω.
- [ ] **Parts bought by exact code or specification** (`final_audit.md` §6,
  "Buying the parts"): U5 OPA547T; R30 PR02000201000FA100 (1 %, copper
  leads); D7 BZT52B24 (±2 %); C17/C18 TCJB106M035R0200E; Y1 and Y2
  fundamental-mode, ESR ≤ 80 Ω; C0G for C24–C27, C32, C33; ±10 % X7R for
  C47 and C48; ≤ 25 ppm/°C for the 0.1 % resistors; LED1 a yellow-green
  with V_F ≈ 2 V (an InGaN green stays dark on 3.3 V); R8 a 0.1 W 0603;
  jacks with an M6 thread.
- [ ] **Jack hardware (J4–J7).** The pad is 10.16 mm across and the AGND
  pour starts 0.3 mm beyond it on both faces, under solder mask only. Any
  metal that bears on the board (nut, flange, washer, solder tag) must be
  **10 mm across or less**; under anything wider (an M6 washer is 12 mm)
  fit an insulating washer of 12 mm or more on that face, and make the
  electrical joint by soldering the stud or tag to the pad. §2 checks it.
- [ ] **Order of assembly.** Bottom-side SMD parts first: C19 and R8 lie
  between U5's two rows of leads and R9 and C16 under its body. Then the
  top side, then the through-hole parts; trim U5's leads with care.
- [ ] **C17, C18 (10 µF 35 V polymer tantalum, TCJB106M035R0200E).** An
  MnO2 tantalum of this rating does not exist in this case size; do not
  substitute a 25 V or 16 V part. Moisture-sensitive (MSL 3): from a sealed
  bag, or baked. Polarity bar to the silkscreen mark: C17 + on +15VA, C18 +
  on AGND.
- [ ] **R30, Vishay PR02 2 W (PR02000201000FA100: copper 0.78 mm leads,
  axial, taped).** It must be the **copper-lead** PR02: the FeCu-lead version
  is a 1.3 W part. (The order code used before ended in …FB100, which is not
  a valid PR02 number.) Axial, 12.7 mm pitch, 1.2 mm holes; the body is up to
  10.0 mm long, so the bends start within a fraction of a millimetre of the
  end caps: hold each lead between the cap and the bend with round-nose
  pliers so the cap is not stressed, and reject a part whose lacquer cracks
  at the cap. Form the leads so the **body sits a full 3 mm above the
  board** (a lead kink or a spacer) **and leans away from U12**, which is
  1 mm from it. **1 % is required**: with the 5 % part (…JA100) the fault
  envelope exceeds 2 W. In the worst rail fault it dissipates up to
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
- [ ] **U5, OPA547T (TI package KVT: TO-220-7, stagger-formed leads).** The
  board and BOM say OPA547T; the schematic symbol and the silkscreen
  reference are unchanged from the OPA548T it replaces (same pinout and
  package). **Do not fit an OPA548T**: R7 is sized for the OPA547. Not the
  OPA547T-1 (straight leads) nor the OPA547F (DDPAK). The footprint is
  KiCad's staggered TO-220-7 with 1.2 mm drills; the pin functions are
  verified against SBOS056F and SBOS070D and the hole pattern against TI's
  KVT solder footprint. Check the purchased part's lead form against the
  stagger before soldering.
- [ ] **U5 tab is at V− (−15 V).** It must not touch any enclosure, bracket or
  grounded metal. No heat sink is needed (≤ 66 K rise without one), and one
  does not fit as placed: C18 is about 1 mm behind the tab and C7 beside
  it. If one is improvised all the same, use an insulating pad and a
  shoulder washer, and check with an ohmmeter that it is open to the tab
  and to every rail (> 10 MΩ).
- [ ] **Ground pins in the pours.** Both pours connect to their pads
  solidly, without thermal relief. J1 pin 4, J2 pin 3, SW1, R10 pad 2, U6
  pin 2 and J3's shell need a larger tip and a longer dwell; inspect R10
  and U6 pin 2 in particular.
- [ ] **Orientation.** Check each of these against the silkscreen and the
  footprint pin 1: electrolytics C5–C8, tantalums C1, C2, C17, C18, C29,
  D1–D4 (1N4148W) and D6 (BAT54S). LED1's cathode is on pad 1, toward R23;
  its anode, on pad 2, is on +3V3D now. D5 is bidirectional. New parts:
  - **D8 (MBR0540, top):** cathode on +15VA, anode on AGND;
  - **D9 (MBR0540, bottom side):** cathode on AGND, anode on −15VA;
  - **D7 (BZT52B24, the ±2 % grade, e.g. Vishay BZT52B24-E3-08; bottom
    side):** cathode on SSR_LED_K (U12 pin 3), anode on −15VA. A ±5 % or
    ±6 % BZT52C24 is **not** a substitute (`final_audit.md` §3.2);
  - **U12:** pin 1 as above.
  A reversed D8 or D9 is a short across its rail at first power (§2 catches
  it).
- [ ] **Before the wash.** A plain tact switch is not sealed, and J1, J3 and
  the jacks trap liquid: fit SW1, J1 and the jacks after the wash, or use a
  washable switch.
- [ ] **Flux removal (required).** Wash the board with a cleaner suited to
  the flux used (IPA for rosin; a saponifier and DI rinse for water-soluble),
  then bake it dry (for example 1 h at 60–70 °C). These nodes are high
  impedance, so flux residue shows up directly as error or drift:
  - U7 inputs and the Kelvin attenuator R12/R13/R32/R33 (≈ 8 kΩ Thevenin);
  - the U6 and U7 RG pins, including the **empty R14 pads**;
  - the U5 ILIM node (R7 = 348 kΩ, C14);
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
- [ ] **Measure the harness before it touches the board.** With the harness
  disconnected from J1 and the supplies on, each lead against the GND lead
  at the J1 end: **+15.0 V ±0.2 V, −15.0 V ±0.2 V, +5.0 V (4.75…5.25 V)**.
  The +5 V terminal has no protection and sits next to −15 V: more than 6 V
  on it destroys U4, U8 and U11 whatever the current limit. Use a fixed
  5 V output, or set its over-voltage protection to 5.5 V. Swapped ±15 V
  leads are survived only while the supply's current limit is ≤ 0.5 A
  (D8/D9). The ±15 V setting matters too: U12 is closed from ±14.3 V at
  25 °C but needs ±14.75 V at 50 °C (`final_audit.md` §3.2).
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
- [ ] **Jacks to AGND, after the jack hardware is tightened.** J4 (FORCE+)
  to AGND: open (> 10 MΩ). J5 and J6 (SENSE±) to AGND: 50.2 kΩ each (their
  dividers). J7 (FORCE−) to AGND: 13…17.5 Ω (F2 + R10). Anything lower
  means a nut or washer has cut through the mask onto the ground pour:
  **STOP**.
- [ ] **Sense clamps D1–D4 (diode test, four readings).** Meter + on J5
  (SENSE+), − on +15VA: a diode drop (D1); reversed: open. Meter + on
  −15VA, − on J5: a diode drop (D2). The same from J6 (D3, D4). A reading
  near zero either way is a shorted clamp, which would put a rail on that
  jack at power-up: **STOP**. Repeat this check after any occasion on
  which a source was connected to the jacks of an unpowered board.
- [ ] **R18.** +15VA runs under R18 (bottom side, by U8) between its pads.
  Inspect it under magnification, and measure +15VA to each R18 pad: no
  short (pad 1 is AGND, pad 2 is ADC_AIN3).
- [ ] DAC_ARM to DGND ≈ 100 kΩ (R37). DAC_VREF to AGND ≈ 1 MΩ (R35).
- [ ] U5 tab to V− (−15VA) ≈ 0 Ω, and to everything else open.

## 3. First power — MCU blank or held in reset

Use current-limited bench supplies: ±15 V at 50–100 mA, +5 V at 150 mA
(the ±15 V limits are raised for §7). Bring them up together (§5 covers
ordering). Hold the MCU in reset with SW1,
or leave it unprogrammed. **No DUT is connected.** Put a 1 kΩ resistor across
FORCE+/FORCE− with the Kelvin leads on its terminals.

- [ ] Supply currents at power-up, recorded per rail. **STOP** on any rail at
  its limit. Both ±15 V currents include the U12 LED string (next item).
- [ ] **U12 LED string.** Voltage across R39 (620 Ω): expect ≈ 4.7 V at the
  instant of power-up, settling over one to two minutes to ≈ 3.5 V
  (≈ 5.6 mA, typical values at ±15 V) as D7 warms itself by about 60 K.
  Read it after two minutes. The bounds at ±15.0 V are ≥ 2.9 V (4.6 mA) at
  25 °C and ≥ 2.4 V (3.9 mA) at 50 °C, and ≤ 5.3 V (8.5 mA) at ±15.5 V.
  Below 1.9 V (3 mA) U12 is not guaranteed on: **STOP**. This reading is
  also the check on D7's self-heating; there is no separate temperature
  measurement of D7.
- [ ] **U12 closed:** J4 (FORCE+) follows R30 pad 2. With 1 kΩ load and the
  output at 0 V this is a small-signal check; at 10 mA (§7) the drop across
  U12 (R30 pad 2 to J4) is ≤ 20 mV (RON ≤ 2 Ω).
- [ ] **ADC clock.** With the MCU still in reset the ADS1256 puts its
  clock on D0/CLKOUT (U8 pin 25; on after power-up until the firmware turns
  it off). ×10 probe: 7.68 MHz within a few hundred ppm, present within
  tens of milliseconds of power-up, on several power cycles. This is the
  check on the crystal and on C26/C27 (now 18 pF). No clock: **STOP**.
- [ ] Rails: +15VA and −15VA at J1 = 15.0 V ±0.2 V; +5V_RAW 4.75…5.25 V;
  +5VA, +3V3D (3.3 V ± 2 %), VDDA, and
  **VREF_2V5 at TP8 = 2.500 V** (U2 is REF5025**AI**DGKT, the standard grade:
  ±0.1 % initial = ±2.5 mV, ≤ 8 ppm/°C drift; the high grade's 0.05 % /
  3 ppm/°C does **not** apply to this part).
- [ ] **Safe state, MCU not running.** DAC_ARM ≈ 0 V. DAC_VREF ≈ 0 V.
  TP1 (V_SWEEP) and TP2 (VO_SENSE) ≈ 0 V. The firmware handoff estimates the
  residual offset at ≤ 0.1–0.15 V; record it. **STOP if V_SWEEP is near
  −10 V**: that is the pre-correction fault, and means the midpoint or the
  gate is not working.
- [ ] CS_DAC (U4 pin 5) is high while the MCU is held in reset (R34 pull-up).
- [ ] RESET_ADC (U8 pin 15) is high (R20 pull-up).

## 4. Program the MCU

- [ ] SWD on J2 (1 SWDIO, 2 SWCLK, 3 GND, 4 +3V3D, 5 NRST). BOOT0 is tied low,
  so SWD is the only programming path. **Pin 4 is the 3.3 V rail itself.**
  Connect it only to the probe's target-voltage sense input (VTref); if the
  probe can source 3.3 V on that pin, leave it unconnected. Program only
  with the bench +5 V on: a board powered from the probe drives the DAC's
  logic inputs with the DAC's own supply absent.
- [ ] The LED lights when PC13 is driven **low** (sunk through R23, ≈ 1.3 mA).

## 5. Partial-supply states and sequencing (U12, D8/D9)

`final_corrections.md` §2.5 and `final_audit.md` §3.2 give what is
guaranteed (U12 open with either rail absent; closed at the nominal ±15 V
rails, from ±14.3 V at 25 °C) and what is only expected (closing near
26 V total). This section measures it. MCU in reset, 1 kΩ load across the
jacks with the Kelvin leads on it.

**Keep each single-rail state to a few seconds, and watch the ADC inputs.**
With one ±15 V rail missing the INA128s can drive the ADS1256 inputs
beyond its rails through R15/R17 (100 Ω); the ADC allows 10 mA continuous.
In each state below measure the voltage across R15 and across R17
(1.0 V = 10 mA) and record it. Above 1.0 V, end the state at once and
report it. The state to watch is **−15 V alone with +5 V on**.

**In use:** the two ±15 V rails come up and go down together. `DISARM`
before switching anything off, and switch ±15 V off first or all three
rails together: if +5 V collapses first while the output is armed, the DAC
passes through its unspecified supply region with U12 still closed, and a
positive excursion of a volt or two at the DUT is plausible for tens of
milliseconds (`final_audit.md` §6). The "+5 V supply removed" case of §6
below is where that is measured.

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
  down where it stops. Expected ≈ 26 V; it must lie between 16 V and 28.6 V.
- [ ] **Transients.** Scope TP1, TP2 and FORCE+ while switching the supplies
  in different orders: all together; ±15 V first, then +5 V; +5 V first,
  then ±15 V; one ±15 V rail at a time; and the reverse orders at power-down,
  with the sweep at 0 V and again at +5 V. Record the peak voltage and
  duration of any excursion on FORCE+. Anything above about 1 V into the load
  is a finding to report.

## 6. Reference gate and heartbeat (audit F2), with the firmware

The firmware is in `firmware/` (see `docs/firmware/bringup_firmware.md` for
the full firmware bring-up order, the logic-analyser checks that come first,
and the commands). The production image runs the heartbeat at **2 kHz on a
fixed 250 µs grid** and reports `ARMED` 100 ms after the first edge. The
frequency, single-edge and B = 0 tests need the **bring-up image**
(`stage1_iv_fw-bringup.elf`, `*IDN? build=bringup`); flash the release image
again afterwards.

- [ ] **Arm** (`ARM`, release image, 2 kHz). DAC_ARM: simulated 2.70 V
  nominal, 2.15 V worst case; U11 needs 1.49 V. Record it.
  **STOP if it is below 1.7 V.**
- [ ] At 1 kHz (bring-up `HB.FREQ 1000`): expect ≈ 2.6 V (simulated 2.62 V;
  worst case 2.03 V). Record it.
- [ ] Time from the first PC5 edge until DAC_ARM crosses 1.49 V (simulated
  2.1 ms nominal, 4.0 ms worst case at 2 kHz). The firmware waits 100 ms
  before it reports `ARMED`.
- [ ] DAC_VREF = VREF_2V5 within about 1 mV (U11's Ron carries only the DAC's
  reference current).
- [ ] **Margin.** Heartbeat at 500 Hz (bring-up `HB.FREQ 500`): DAC_ARM
  ≥ 1.76 V (simulated worst case; 2.42 V nominal). Record it.
- [ ] **Arming and disarming transients.** Scope TP1 and FORCE+ (1 kΩ load)
  at `ARM` and at `DISARM` with the zero pair loaded. Closing the gate
  steps the DAC reference from 0 to 2.5 V in under a microsecond, and the
  two DAC channels slew to different targets: a spike of the order of 1 V
  for a few microseconds is expected (estimated, not characterised by TI).
  At disarm, look for chatter while DAC_ARM decays through U11's
  threshold. Record peak and duration; neither can exceed what the held
  code already applied.
- [ ] **Single edge** (bring-up, DISARMED: `HB.EDGE`, then `HB.HOLD OFF`):
  DAC_ARM peaks near 0.5 V (simulated 0.54 V nominal, 0.66 V worst case),
  stays below 0.87 V, then decays. DAC_VREF stays at 0 V.
- [ ] **Disarm tests.** For each case below, first set the output to +5 V
  (`SRC V=5`), then repeat at −5 V (`SRC V=-5`). Scope TP1 and FORCE+ (1 kΩ
  load) and record the time until |V_SWEEP| < 0.2 V.

  **Limits: ≤ 70 ms with PC5 left low or floating, ≤ 85 ms with PC5 left
  high.** Rationale: the pump simulation (`firmware_spec.md` §4.4) gives the
  time for DAC_ARM to fall below V_IL = 0.87 V as 52–54 ms nominal and
  61–63 ms in the slow corner (low diode VF, C48 +10 %, R37 +1 %) with PC5
  low, and 64–66 ms nominal / 75–77 ms worst case with PC5 held high
  (C47 feed-through). The earlier "≤ 60 ms" expectation would fail a correct
  board in the slow corner and in every PC5-high case. The firmware's own
  `T_DISARM` is 100 ms, above both limits. In the cases where the firmware
  is still running it does not rely on the gate: it writes the zero code
  first, so V_SWEEP falls much sooner than the limit.
  - [ ] orderly `DISARM` (ramp at 10 V/s, then the zero code; record the
    ramp and the gate opening after the last edge);
  - [ ] heartbeat stopped, DAC holding the level, PC5 left low or high
    (bring-up `CRASH HANG_IRQOFF`: the PC5 level at the moment of the hang
    is whichever the grid was at — note it from the analyser, repeat until
    both levels are covered; the IWDG resets the MCU 1.0–2.8 s later);
  - [ ] SW1 held in reset (PC5 floats; after release the boot writes the
    zero pair at once);
  - [ ] debugger halt (on resume the dead-man fires and zeroes);
  - [ ] deliberate crashes: `CRASH HARDFAULT` (fault handler zero, < 1 ms)
    and `CRASH HANG` (dead-man zero within 2 ms);
  - [ ] +5 V supply removed (MCU and DAC unpowered).

  **STOP if any case leaves V_SWEEP at its last value, or exceeds its
  limit.**
- [ ] **/SYNC during reset.** Scope CS_DAC while pressing SW1. It stays high
  throughout, so no DAC frame is clocked in.
- [ ] **No re-arm after reset.** After every reset (SW1, watchdog, crash,
  `RESET`) the firmware writes B = `0xFFFF`, A = zero code before any PC5
  edge and stays `DISARMED`; `STAT?` reports `disarm_reason=reset` and the
  reset cause. V_SWEEP stays at 0 V until a new `ARM` and `SRC`.
- [ ] **Positive-bound contract (negative test, briefly; bring-up image
  only).** With the 1 kΩ load only: `ARM`, then
  `DAC.RAW 32768 0 CONFIRM=UNSAFE` (B = `0x0000`, A = `0x8000`). V_SWEEP
  should be about +10 V; that it is not 0 V shows the bound depends on B.
  Then `DAC.RAW OFF` (zero pair, B back at full scale). This command exists
  only in the bring-up build (`ERR UNKNOWN` in release and debug); it shows
  why the production firmware never writes B below full scale.

## 7. Output stage, current limit and protection

- [ ] **Raise the ±15 V current limits first.** Set both to 250 mA (not
  above 0.5 A, the rating of D8/D9). On the plateau the +15 V or −15 V
  supply delivers the plateau current (up to 89 mA) plus about 30 mA for
  U5, the U12 LED string and the other amplifiers. At the 50–100 mA of §3
  the supply itself would limit, and its limit would be read as U5's.
  Throughout this section check on the supplies that neither rail is in
  current limit and both stay at 15.0 V ±0.2 V.
- [ ] **Sweep into 1 kΩ** from −10 V to +10 V and back: the Kelvin and current
  channels track, with signed readings and no step at 0 V.
- [ ] **Sweep span.** Command the two ends and record what V_SWEEP (TP1)
  actually reaches. ±10.04 V is nominal; the DAC's own limits allow as
  little as ±9.85 V, and a few tens of mV short of ±10.000 V at one end is
  not a fault (`final_audit.md` §6). Record both ends for the firmware.
- [ ] **Small step in the linear region (U5 and its output network).** ×10
  probe on TP2. With the 1 kΩ load, and again with the jacks open, step the
  output by 100 mV at 0 V, +5 V and −5 V. The edge settles without ringing
  (U3 upstream limits it to a few microseconds). Ringing near 0.5…1 MHz
  that lasts more than two or three cycles means U5 has lost phase margin:
  **STOP** and check that R38 is 100 Ω and that nothing capacitive is
  connected to VO_RAW or TP2. Only if ringing is seen: lift one end of R38
  and repeat, to tell the network from the amplifier, and report both
  results. Record the waveform.
- [ ] **Current-limit plateau.** Short FORCE+ to FORCE− (Kelvin leads on the
  short) and command +10 V (`SRC V=10`, `MEAS? N=16`). Record the current
  **and the voltage at TP2 (VO_SENSE)**, and repeat at −10 V. **U5 is
  limiting when TP2 falls short of the command**: on the plateau TP2 is
  I × (F1 + R30 + U12 + F2 + R10), about I × 120 Ω (6 V at 50 mA). If TP2
  follows the command (≈ 10 V) the current is set by the series resistors
  (79…87 mA into the short) and U5 is not limiting. Do not judge it by
  whether the current moves between 8 V and 10 V: above about 65 mA the two
  cases look alike.
  The ≈ 49 mA figure is a **design centre**, not a specification. With
  R7 = 348 kΩ the **expected plateau is 45…65 mA**: about +51 / −59 mA
  cold, falling to about +42 / −50 mA as U5 warms, if the OPA547's typical
  supply and temperature curves carry down to this setting, and up to the
  equation's 62.6 mA if they do not. The sinking limit is the larger.
  Between units ±10 mA is typical and ±30 mA the data-sheet maximum, so
  anything from 20 to 90 mA has an explanation. Record the cold and the
  settled value in both polarities.
  **STOP** if either polarity gives no output or limits below 20 mA: check
  that U5 is an OPA547T, that R7 is 348 kΩ, and that the ILIM node (R7,
  C14, U5 pin 3) is clean, since 1 µA of leakage there is 4.6 mA of limit.
  A unit that does not limit at all into the short (TP2 at the command) is
  at the top of the spread: it is safe, R30 bounds it, but it has no
  plateau; correct it with R7.
  The measured value, smaller polarity, becomes the calibration coefficient
  (`CAL.SET i_plateau=<A>`, `CAL.SAVE`), which the firmware's `CLIM`
  classification uses. To move a unit's plateau, change R7: larger R7 gives
  less current, about 6 kΩ per mA near 348 kΩ (412 kΩ for 9 mA less, 300 kΩ
  for 9 mA more; 332 kΩ is the first step if only the sourcing plateau is
  low). The build's limits on R7 (301…365 kΩ) are for an unmeasured unit;
  a measured unit may be trimmed outside them, noted on its traveller.
- [ ] **Plateau at two commands.** With the short still in place compare
  the plateau current at 6 V and at 10 V (TP2 below the command in both).
  They should agree within about 1 mA. More current at 10 V means current
  is entering through U5's inputs, which see up to 9 V between them on the
  plateau and for which TI gives no differential rating. Record it.
- [ ] **Thermal soak on the plateau.** Hold the short at +10 V for 30 min at
  room temperature. Measure R30's body and one of its solder joints, U12's
  body (it is 1 mm from R30), F1's and F2's bodies and U5's tab with a
  thermocouple or IR camera. **Neither F1 nor F2 may trip.** Record
  their temperature rise. Bel's 0ZRM0010FF1E hold current is 100 mA at 23 °C,
  80 mA at 40 °C and 68 mA at 50 °C, against an expected plateau of
  45…65 mA. F1 is now at the bottom edge, 14 mm from R30 and 23 mm from U5
  (`final_corrections.md` §2.1). If either PTC runs warmer than about 45 °C,
  or trips, or the settled plateau is above ≈ 60 mA, first bring the plateau
  down with R7 (previous item). The remaining contingency is 0ZRM0017FF1E
  (170 mA hold, same footprint) **for F1 only**, reported when used: its
  minimum resistance is 2.0 Ω, which takes R30 to 1.99 W of 2 W in the
  FORCE+-to-AGND rail fault. **F2 stays 0ZRM0010FF1E**: with 2.0 Ω there, an
  external 15.5 V on J7 would put 16.7 W into R10, above its 15 W
  short-time rating (`final_corrections.md` §2.6).
- [ ] **Stability on the plateau (output network R38/C49, fitted).** ×10
  probe on U5 OUT (VO_RAW, R9's U5-side pad), second channel on TP3 (the
  shunt). On the plateau, in **both polarities**, with: the short at the
  jacks; the short at the end of a 1 m DUT cable; 10 nF and 100 nF across
  the jacks; and then an **inductive load with no capacitor across the
  jacks**: 1 mH and 10 mH inductors of a few ohms, or a relay coil, in
  place of the short. The inductive case is the one that matters: in TI's
  model the unfitted stage oscillates there, rail to rail in the negative
  polarity, and a capacitor across the jacks hides it
  (`final_audit.md` §3.7). Expected with the network fitted: no sustained
  oscillation on VO_RAW, a steady reading. If it does oscillate: **STOP**
  and report it with the load and polarity. The next step is C49 = 22 nF
  with R38 unchanged. **Do not lower R38 below 47 Ω and do not fit TI's
  3 Ω + 0.01 µF** (that example is an inverting gain-of-4 stage; on this
  unity-gain follower it costs the linear region its phase margin).
  Record the result on the board's traveller.
- [ ] **Rail-fault envelope (optional; costs a board).** The design bounds a
  DUT short at 128 mA and FORCE+ to AGND at 143 mA, with up to 1.96 W in R30
  (98 % of its 2 W rating) in the stacked worst case. Test it only if a board
  can be spared, for a minute or two and not longer, with thermocouples on
  R30 and on U12 beside it (R30's body can reach about 170 °C; U12 is
  rated to 110 °C).
  Record whether and when F1 trips. F1 is **not** a guaranteed backstop at
  this current.
- [ ] **FORCE− external source (optional; then recalibrate).** A 12 V supply
  limited to 1.5 A, from J7 to AGND: F2 must trip (expected < 1 s) and R10
  survive. Recalibrate the current channel afterwards (R10's short-time
  overload allows ΔR ≤ 0.2 % + 0.05 Ω).

## 8. Measurement checks and calibration

Follow `firmware_handoff.md` §4–§5 and `docs/firmware/calibration.md`.

- [ ] ADS1256: SCLK ≤ 1.92 MHz, CLKOUT off (ADCON CLK = 00), D1–D3 driven low,
  SELFCAL done (logic analyser, `docs/firmware/bringup_firmware.md` §2).
- [ ] Zero with the gate disarmed: jacks open (`ZERO`), then jacks shorted
  (`MEAS? N=64`). Record the offsets of both channels.
- [ ] **Kelvin common-mode term.** Put **both** Kelvin leads on the DUT's
  low terminal (the FORCE− side of a 100 Ω load or of the short) and pass
  +80 mA, then −80 mA. The voltage channel should read zero; what it reads,
  divided by I, is the common-mode error of the two dividers (SENSE− sits
  at I × (R10 + F2), about 1.4 V). Expected under 56 mΩ equivalent (5 mV at
  92 mA, worst case with 0.1 % parts); record it. It moves a little with
  F2's temperature.
- [ ] **R10 thermal EMF.** Run the plateau (or 50 mA into a short) for 5 min,
  set the output to 0 V, and read the current channel at once and again after
  5 min. The difference against the cold zero is the shunt's thermal EMF
  (referred to the input: µV / 10 Ω). Record it; above ≈ 1 µA equivalent,
  check that R10 lies flat with symmetric leads.
- [ ] Gain: a 0.01 % 100 Ω resistor in a 4-wire connection, measured against a
  calibrated DMM at several points of each polarity (`iv-cal --r-ref 100`).
- [ ] **Open-Kelvin detection.** With current flowing, lift SENSE+, then
  SENSE−. The firmware cross-check (Kelvin voltage against
  V_SWEEP − I·(R30 + R10 + R_F1 + R_F2 + R_U12)) must flag it: `KELVIN` in
  the records and `EVT WARN KELVIN` after 3 points (open SENSE− only above
  ≈ 2–15 mA, as documented in `firmware_spec.md` §7.7).
- [ ] **U12 open state.** With a 1 kΩ load and +5 V commanded, remove −15 V:
  U12 opens, I → 0 while the commanded V stays; the firmware must report the
  output as disconnected (`NO_OUTPUT` flag, `EVT WARN NO_OUTPUT`), not as an
  open DUT. With ±15 V off at `ARM`, the answer is `ERR PRECOND afe`.
- [ ] Hysteresis sweep of a known diode, −V → +V → −V: signed and continuous,
  and the plateau region is flagged as current limit, not as DUT behaviour.

## 9. Records to keep

Rail voltages and currents, the R39 voltage (U12 LED current), VREF_2V5,
the safe-state residual, the dead-rail voltages and load voltages of §5, the
U12 close/open thresholds, DAC_ARM at 2 kHz, 1 kHz and 500 Hz, the arm time,
every disarm time (with the PC5 level), the arming and disarming transients,
the ADC clock frequency, the R15/R17 voltages in the single-rail states,
the sweep span, the linear-step waveform, the plateau
current and TP2 voltage at both polarities and at 6 V and 10 V, the
R30 / U12 / F1 / F2 / U5 temperatures after the soak, the plateau stability
result with each load, the Kelvin common-mode term, the R10 thermal-EMF
offset, and the calibration constants. Update `docs/audit_corrections.md`
§"What is calculated, not measured" with the measured values.
