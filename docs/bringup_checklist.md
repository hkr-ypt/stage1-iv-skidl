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

**Four rules for the whole procedure.** (1) Never connect anything that
holds voltage (a battery, a charged capacitor, a powered circuit) to the
jacks of an unpowered board: power the board first, disconnect before
power-off, keep the DUT floating, and nothing above ±15 V ever. The SENSE
clamps feed the rails directly (`final_audit.md` §6). (2) Nothing
capacitive on VO_RAW or TP2: a ×10 scope probe, or a meter through a 10 kΩ
resistor held at the test point. Capacitance hung directly on U5's output
costs it stability margin. (3) Touch AGND before handling the FORCE− lead.
(4) With an inductive load, always leave the current limit by `SRC V=0`
(a ramp) before touching the load (§7 says why).

**Preparation.** Solder short wire stubs to TP1–TP8 (bottom side), to R38
pad 1 (VO_RAW, top side) and to C48 pad 2 (DAC_ARM), so that probes and
clips hold themselves. Fit a jumper from J2 pin 5 (NRST) to pin 3 (GND) to
hold the MCU in reset for §3 and §5; SW1 cannot be held for minutes.
Insulate thermocouple tips: U5's tab is at −15 V. Easier probe points
than IC pins: CS_DAC on R34 pad 2 (bottom), RESET_ADC on R20 pad 2 (top),
DRDY on R19 pad 2, PC5 on R36.

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
  **R38 = 100 Ω and C49 = 10 nF 50 V C0G**, the U5 output network, which
  were DNP before the final audit (`final_audit.md` §3.7). R38 must be
  100 Ω: not the 10 Ω of earlier documents, and not TI's 3 Ω.
- [ ] **Parts bought by exact code or specification** (`final_audit.md` §6,
  "Buying the parts"): U5 OPA547T; R30 PR02000201000FA100 (1 %, copper
  leads); D7 BZT52B24 (±2 %); C17/C18 TCJB106M035R0200E; Y1 and Y2
  fundamental-mode, ESR ≤ 80 Ω; C0G for C19, C24–C27, C32, C33 and C49;
  ±10 % X7R for C47 and C48; ≤ 25 ppm/°C for the 0.1 % resistors; LED1 a
  yellow-green with V_F ≈ 2 V (an InGaN green stays dark on 3.3 V); R8 a
  0.1 W 0603; R9 0.25 W at least, a 0.5 W 1206 recommended; crystals with
  12…18 pF load capacitance; the unmarked ceramics X7R, 16 V or more
  (C42 25 V). Jacks with an M6
  thread, **chosen from a drawing** before the board is ordered: write
  the mounting stack for that jack here, including where its metal meets
  the pad.
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
  substitute a 25 V or 16 V part. **Do not solder these two with an iron
  held on them** (the maker: "hand soldering is not recommended"; rework
  by hot air). Use solder paste and hot air, or reflow, preheated, peak
  260 °C or less. Moisture-sensitive (MSL 3): within 168 hours of opening
  the dry pack, or dried at 40 °C for 168 hours first (not a hot bake).
  Polarity bar to the silkscreen mark: C17 + on +15VA, C18 + on AGND.
  **If only an iron is available**, fit a 4.7 µF / 35 V MnO2 tantalum in
  the same case instead (KEMET T491B475K035AT or Kyocera AVX
  TAJB475K035RNJ) and note it on the traveller (`final_audit.md` §3.8).
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
- [ ] Resistance from each J1 pin to J1 pin 4 (GND), meter + on the pin for
  +15 V and +5 V, meter + on GND for −15 V: none is a short (expect
  kΩ-class, rising as capacitors charge). With the leads the other way
  round D8 or D9 conducts and the reading is a few hundred ohms; that is
  not a fault.
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
  to AGND: open (> 10 MΩ). J7 (FORCE−) to AGND: 13…17.5 Ω (F2 + R10). J5
  and J6 (SENSE±) to AGND: 50.2 kΩ nominal (their dividers), but D1–D4
  conduct into the discharged rails at the meter's test voltage, so expect
  **30…50 kΩ, creeping upward**; a meter range with 1 µA or less of test
  current reads 50.2 kΩ. A nut or washer that has cut through the mask
  onto the ground pour reads under 100 Ω. **STOP** only for J4 not open,
  J5 or J6 under 1 kΩ, or J7 under 13 Ω.
- [ ] **Sense clamps D1–D4 (diode test, four readings).** Meter + on J5
  (SENSE+), − on +15VA: a diode drop (D1); reversed: open, or any reading
  above about 1 V. Meter + on −15VA, − on J5: a diode drop (D2). The same
  from J6 (D3, D4). A reading near zero either way is a shorted clamp,
  which would put a rail on that jack at power-up: **STOP**. Repeat this
  check after any occasion on which a source was connected to the jacks
  of an unpowered board.
- [ ] **D7 (diode test).** Meter + on −15VA, − on SSR_LED_K (U12 pin 3, D7's
  cathode pad): a diode drop. Reversed: open. A D7 fitted the wrong way
  round conducts as a plain diode: 45 mA through U12's LED and 1.3 W in
  R39 at first power, and U12 closed when a rail is missing. **STOP** if
  the two readings are the other way round.
- [ ] **R18.** +15VA runs under R18 (bottom side, under U8) between its
  pads. Inspect it under magnification. Meter + on +15VA: pad 1 (AGND)
  reads what J1 pin 1 to pin 4 reads (kΩ-class, rising), pad 2 (ADC_AIN3)
  100 Ω more. **STOP** if either reads under 50 Ω.
- [ ] DAC_ARM to DGND, meter + on DAC_ARM: ≈ 100 kΩ (R37); the other way D6
  conducts. DAC_VREF to AGND: between about 0.3 and 1 MΩ (R35 with the
  unpowered U11 and U4 beside it), not a short.
- [ ] U5 tab to V− (−15VA) ≈ 0 Ω. Tab to AGND reads as the −15 V rail does
  (kΩ-class one way, D9's drop the other); to any other net, open.

## 3. First power — MCU blank or held in reset

Use current-limited bench supplies: ±15 V at 50–100 mA, +5 V at 150 mA
(the ±15 V limits are raised for §7). Bring them up together (§5 covers
ordering). Hold the MCU in reset with SW1,
or leave it unprogrammed. **No DUT is connected.** Put a 1 kΩ resistor across
FORCE+/FORCE− with the Kelvin leads on its terminals.

- [ ] Supply currents at power-up, recorded per rail: expect about
  19…27 mA on each ±15 V rail (U5, the U12 LED string, U3, U6, U7).
  **STOP** on any rail at its limit.
- [ ] **U12 LED string.** Clip the meter across R39 (620 Ω) **before**
  power; its pads are 2–3 mm from U5's pins. Expect ≈ 4.7 V at the instant
  of power-up (up to 5.8 V for the first seconds with a low-voltage D7),
  settling over one to two minutes to 3.2…3.8 V (≈ 5.6 mA, typical values
  at ±15 V) as D7 warms itself by about 60 K. Read it after two minutes:
  at ±15.0 V it is ≥ 2.85 V (4.6 mA) at 25 °C and ≥ 2.4 V (3.9 mA) at
  50 °C, and ≤ 5.3 V (8.5 mA) at ±15.5 V. Below 1.9 V (3 mA) U12 is not
  guaranteed on: **STOP**. **Above 7 V at any time: power off at once**;
  D7 is reversed or the wrong part. This reading is also the check on
  D7's self-heating; there is no separate temperature measurement of D7.
- [ ] **R8.** Voltage across R8 (10 kΩ, U5's E/S pin to +15 V, bottom side
  between U5's lead rows). TI characterises the pin at 60 µA only; record
  what it is. Up to 10 V (10 mW) needs nothing. If R8 drops more than
  about 20 V (40 mW in an 0603 beside a warm U5), fit 100 kΩ instead: the
  pin only has to sit above (V−) + 2.4 V and may even be left open.
- [ ] **U12 closed:** J4 (FORCE+) follows R30 pad 2. With 1 kΩ load and the
  output at 0 V this is a small-signal check; at 10 mA (§7) the drop across
  U12 (R30 pad 2 to J4) is ≤ 20 mV (RON ≤ 2 Ω).
- [ ] **ADC clock.** With the MCU still in reset the ADS1256 free-runs at
  its default data rate, so DRDY (R19 pad 2) is its clock divided by 256:
  **30.000 kHz** within a few hundred ppm, present within tens of
  milliseconds of power-up, on several power cycles. (The clock itself is
  also on D0/CLKOUT, U8 pin 25, 7.68 MHz, until the firmware turns it off;
  DRDY is the easier place to probe.) This is the check on the crystal
  and on C26/C27 (now 18 pF). No DRDY: **STOP**.
- [ ] Rails: +15VA and −15VA at J1 = 15.0 V ±0.2 V; +5V_RAW 4.75…5.25 V;
  +5VA at U8 ≥ 4.75 V; +3V3D (3.3 V ± 2 %), VDDA, and
  **VREF_2V5 at TP8 = 2.500 V** (U2 is REF5025**AI**DGKT, the standard grade:
  ±0.1 % initial = ±2.5 mV, ≤ 8 ppm/°C drift; the high grade's 0.05 % /
  3 ppm/°C does **not** apply to this part).
- [ ] **Safe state, MCU not running.** DAC_ARM ≈ 0 V. DAC_VREF ≈ 0 V.
  TP1 (V_SWEEP) and TP2 (VO_SENSE) ≈ 0 V. The firmware handoff estimates the
  residual offset at ≤ 0.1–0.15 V; record it. **STOP if V_SWEEP is near
  −10 V**: that is the pre-correction fault, and means the midpoint or the
  gate is not working.
- [ ] CS_DAC (R34 pad 2) is high while the MCU is held in reset (R34 pull-up).
- [ ] RESET_ADC (R20 pad 2) is high (R20 pull-up).

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
(1.0 V = 10 mA) and record it: attach the meter across R15 (then R17)
**before** entering the state, one state entry per reading. Above 1.0 V,
end the state at once and report it. Watch **both** single-rail states:
−15 V alone with +5 V on is the likelier one (an INA128 sinks 15 mA
typical), but TI's second fabrication flow of the INA128 sources 18 mA
typical, which would make +15 V alone exceed the limit as well. If a
state does exceed 1.0 V, the remedy is 1.5 kΩ in R15 and R17 (same pads;
it adds a gain term that system calibration removes); do not hold that
state again until it is fitted, §8's "U12 open state" test included.

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
- [ ] **U12 thresholds.** (Do this after §6, with the firmware armed and
  `SRC V=1`, so that there is a voltage for J4 to follow; with the MCU in
  reset the output is 0 V and nothing can be seen.) Ramp ±15 V together
  slowly (tracking supply, or both current-limited supplies turned up
  together) and record the total rail-to-rail voltage at which J4 starts
  to follow R30 pad 2, and on the way down where it stops. Expected
  ≈ 26 V; it must lie between 16 V and 28.6 V.
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
- [ ] **Raise the firmware's own current cut-off as well:
  `CFG i_compliance=0.1`.** Its default is 50 mA, and the firmware writes
  the zero code the moment a reading exceeds it. The expected plateau is
  40…65 mA and the sinking one is typically above 50 mA, so with the
  default every plateau test below ends in a zeroed output and an
  `EVT WARN COMPLIANCE`, which means the *firmware* stopped, not that U5
  limited. 0.1 (100 mA) is the maximum. Restore the setting before §8's
  diode sweep.
- [ ] **Sweep into 1 kΩ** from −10 V to +10 V and back: the Kelvin and current
  channels track, with signed readings and no step at 0 V.
- [ ] **Sweep span.** Command the two ends and record what V_SWEEP (TP1)
  actually reaches. ±10.04 V is nominal; the DAC's own limits allow as
  little as ±9.85 V, and a few tens of mV short of ±10.000 V at one end is
  not a fault (`final_audit.md` §6). Record both ends for the firmware.
- [ ] **U5 stability margin in the linear region (U5 and its output
  network).** (A step commanded through the DAC cannot test this: `SRC`
  moves in 10 mV steps, and any edge reaches U5 through U3, ten times
  slower than U5's own loop. This test drives U5's output node directly
  and needs no firmware command at 0 V.) Output at 0 V (disarmed is
  fine), 1 kΩ load at the jacks. Solder or
  clip a **1 kΩ resistor directly at TP2** and drive its far end from a
  function generator: 1 kHz square wave, 5 V peak to peak, generator
  ground on AGND. (The resistor must be at TP2, so that the generator's
  cable stays on the far side of it.) This pushes ±2.5 mA steps into U5's
  feedback node. ×10 probe on TP2, AC-coupled, 20 mV/div, 1 µs/div,
  triggered on the generator. Expected at each edge: a spike of the order
  of 0.1 V lasting under a microsecond, then **at most one or two cycles
  of ringing** near 0.5…1 MHz. Repeat at 0 V with the jacks open and with
  the jacks shorted, and at +5 V and −5 V (`SRC V=5`, `SRC V=-5`) with the
  1 kΩ load (not shorted: that would be the plateau). Four or more cycles
  means U5 has
  lost phase margin: **STOP** and check that R38 is 100 Ω (not 10 Ω or
  3 Ω) and that nothing capacitive is connected to VO_RAW or TP2. Only
  then, to tell the network from the amplifier, lift one end of R38 and
  repeat, and report both results; refit R38 before any plateau test.
  Record the waveforms.
- [ ] **Current-limit plateau.** Scope on VO_RAW (R38 pad 1, ×10) from the
  first entry into limit, so that an oscillating limiter is seen before
  its average is taken for a plateau. Short FORCE+ to FORCE− (Kelvin leads
  on the short) and command +10 V (`SRC V=10`, `MEAS? N=16`). Record the
  current **and the voltage at TP2 (VO_SENSE)**, and repeat at −10 V. **U5 is
  limiting when TP2 falls short of the command**: on the plateau TP2 is
  I × (F1 + R30 + U12 + F2 + R10), about I × 120 Ω (6 V at 50 mA). If TP2
  follows the command (≈ 10 V) the current is set by the series resistors
  (79…87 mA into the short) and U5 is not limiting. Do not judge it by
  whether the current moves between 8 V and 10 V: above about 65 mA the two
  cases look alike.
  The ≈ 49 mA figure is a **design centre**, not a specification. With
  R7 = 348 kΩ the **expected plateau is 40…65 mA**: about +51 / −59 mA
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
  low). The build's limits on R7 (309…365 kΩ) are for an unmeasured unit;
  a measured unit may be trimmed outside them, noted on its traveller.
- [ ] **Plateau at two commands.** After the plateau has settled (five to
  ten minutes in limit; it sags by several mA as U5 warms), compare the
  current at 10 V with the current at the lowest command that still leaves
  TP2 at least 1 V below the command (8 V for a plateau up to about
  55 mA, otherwise 9 V), alternating 10 V, low, 10 V within a few seconds.
  They should agree within about 1 mA. More current at 10 V means current
  is entering through U5's inputs, which see 2…5 V between them on an
  expected plateau (up to 9 V on a unit with a very low one) and for which
  TI gives no differential rating. Record it.
- [ ] **Entry into the limit, slowly (record, not a STOP).** Still into the
  short, scope on VO_RAW, step the command through the point where the
  limit takes over (about 5…8 V for an expected plateau) in the smallest
  steps `SRC` gives, in both polarities. In TI's model the stage shows a
  small oscillation, about 0.3 V peak to peak near 0.5 MHz, inside a
  window some 20 mV wide at the positive entry, with or without the
  network. If it is there, record its amplitude and the width of the
  window; it does not reach a settled reading and is not a reason to stop
  or to change R38/C49.
- [ ] **Thermal soak on the plateau.** Hold the short at +10 V for 30 min at
  room temperature. Measure R30's body and one of its solder joints, U12's
  body (it is 1 mm from R30), F1's and F2's bodies and U5's tab with an
  insulated thermocouple or IR camera, and the voltage across R39 again.
  **Neither F1 nor F2 may trip.** Record their temperature rise. **U12
  above about 50 °C: report it.** Its trigger current rises with
  temperature and §3's margins were worked at 25 °C and 50 °C; the remedy
  on the same pads is BZT52B22 with R39 = 910 Ω (`final_audit.md` §3.2). Bel's 0ZRM0010FF1E hold current is 100 mA at 23 °C,
  80 mA at 40 °C and 68 mA at 50 °C, against an expected plateau of
  40…65 mA. F1 is now at the bottom edge, 14 mm from R30 and 23 mm from U5
  (`final_corrections.md` §2.1). If either PTC runs warmer than about 45 °C,
  or trips, or the settled plateau is above ≈ 60 mA, first bring the plateau
  down with R7 (previous item). The remaining contingency is 0ZRM0017FF1E
  (170 mA hold, same footprint) **for F1 only**, reported when used: its
  minimum resistance is 2.0 Ω, which takes R30 to 1.99 W of 2 W in the
  FORCE+-to-AGND rail fault. **F2 stays 0ZRM0010FF1E**: with 2.0 Ω there, an
  external 15.5 V on J7 would put 16.7 W into R10, above its 15 W
  short-time rating (`final_corrections.md` §2.6).
- [ ] **Stability on the plateau (output network R38/C49, fitted).** ×10
  probe on VO_RAW (R38 pad 1), second channel on TP3 (the shunt). In
  **both polarities**, at `SRC V=10` and `SRC V=-10`, with each of these
  loads in turn, and for each one **check first that U5 is limiting** (TP2
  at least 1 V below the command), or the test proves nothing:
  1. the short at the jacks, then at the end of a 1 m DUT cable;
  2. 47 Ω, alone, then with 10 nF and with 100 nF across it;
  3. **inductors: 100 µH, 1 mH and 10 mH, each under about 20 Ω in total,
     with no capacitor across the jacks.** (A relay coil only counts if
     its resistance is under about 30 Ω; most are far above that and never
     reach the limit.)

  The inductive case is the one that matters: in TI's model the unfitted
  stage oscillates there, rail to rail in the negative polarity, and a
  capacitor across the jacks hides it (`final_audit.md` §3.7). Expected
  with the network fitted: no sustained oscillation on VO_RAW, a steady
  reading. If it oscillates: `SRC V=0` within seconds (R38 is an 0805 and
  would overheat in a sustained oscillation), **STOP**, and report the
  load and polarity. The next step is C49 = 22 nF with R38 unchanged.
  **Do not lower R38 below 47 Ω and do not fit TI's 3 Ω + 0.01 µF** (that
  example is an inverting gain-of-4 stage; on this unity-gain follower it
  costs the linear region its phase margin). **Always `SRC V=0` before
  changing the load.** Record the result on the board's traveller.
- [ ] **Leaving the negative limit fast, with an inductive load (the
  safe-state test for inductive DUTs).** In TI's model, with the network
  fitted, this is where the stage fails: after a *fast* fall of the
  command out of the negative limit (the zero code written at once, as
  every fault path and the firmware's own current cut-off do) with 0.3 to
  10 mH in the DUT, U5 keeps oscillating, 4 to 14 V peak to peak at 150 to
  230 kHz, with about −60 mA still flowing in the DUT although the
  command is 0 V. A ramp out (`SRC V=0`, `DISARM`, `SWP.ABORT`) does not
  do it, nor does the positive polarity, nor a resistive load
  (`final_audit.md` §3.7). Test it: 1 mH, then 10 mH, each of a few ohms,
  at `SRC V=-10` with U5 limiting; scope on VO_RAW and TP3; then make the
  firmware write the zero code at once. The simplest way is to set
  `CFG i_compliance` just under the measured plateau (e.g. 0.04), which
  makes the firmware's cut-off do it on the next reading; a `CRASH
  HARDFAULT` (bring-up image) does the same through the fault handler,
  and SW1 through the reference gate. **Pass:** VO_RAW and the current go
  to zero and stay there. **If U5 is left oscillating:** `CFG i_compliance=0.1` again
  (otherwise the cut-off answers the next command too), then `SRC V=-10`
  and `SRC V=0` (re-entering the limit and ramping out ends it in the
  model), or switch the ±15 V supplies off, which always ends it; record
  it; and until it is resolved an inductive DUT must not be taken
  into current limit at all. The remedy that needs no copper is to slow
  V_SWEEP: a 10 nF C0G capacitor soldered on top of each of R4 and R6
  (the sweep amplifier's 20 kΩ pair, same value on both), which gives
  V_SWEEP a 200 µs time constant and holds a 10 V fall to 0.05 V/µs
  (`final_audit.md` §3.7). With them fitted, raise `CFG settle_us` to
  2000 or more, and repeat this test.
- [ ] **Rail-fault envelope (not a test for the only board).** The design
  bounds a DUT short at 128 mA and FORCE+ to AGND at 143 mA, with up to
  1.96 W in R30 (98 % of its 2 W rating) in the stacked worst case. A
  healthy board cannot be brought there by command: U5 limits first. It
  would take R7 shorted and the out-of-contract `DAC.RAW` command. Test it
  only if a board can be spared, for a minute or two and not longer, with thermocouples on
  R30 and on U12 beside it (R30's body can reach about 170 °C; U12 is
  rated to 110 °C).
  Record whether and when F1 trips. F1 is **not** a guaranteed backstop at
  this current.
- [ ] **FORCE− external source (optional; then recalibrate).** Board
  **powered**, Kelvin leads **off** J7 (with SENSE− on J7 and the board
  unpowered this would be the back-feed that rule 1 forbids). A 12 V
  supply limited to 1.5 A, from J7 to AGND: F2 must trip within a few
  seconds (Bel guarantees 10 s at 0.5 A; 12 V drives 0.7…0.9 A) and R10
  survive. Remove the source if F2 has not tripped after 5 s. Recalibrate
  the current channel afterwards (R10's short-time overload allows
  ΔR ≤ 0.2 % + 0.05 Ω).

## 8. Measurement checks and calibration

Follow `firmware_handoff.md` §4–§5 and `docs/firmware/calibration.md`.

- [ ] ADS1256: SCLK ≤ 1.92 MHz, CLKOUT off (ADCON CLK = 00), D1–D3 driven low,
  SELFCAL done (logic analyser, `docs/firmware/bringup_firmware.md` §2).
- [ ] Zero with the gate disarmed: jacks open (`ZERO`), then jacks shorted
  (`MEAS? N=64`). Record the offsets of both channels.
- [ ] **Kelvin common-mode term.** (`CFG i_compliance=0.1` again for this
  step.) Put **both** Kelvin leads on the low end of the short at the
  jacks (the FORCE− side) and pass the largest current the unit gives in
  each polarity, i.e. its plateau, at `SRC V=10` and `SRC V=-10`. The
  voltage channel should read zero; what it reads, divided by I, is the
  common-mode error of the two dividers (SENSE− sits at I × (R10 + F2):
  0.65…0.9 V at 50 mA). Expected under 56 mΩ equivalent (2.8 mV at 50 mA,
  worst case with 0.1 % parts); record it. It moves a little with F2's
  temperature.
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
- [ ] **U12 open state.** (Only if §5 showed less than 1.0 V across R15 and
  R17 in the +15 V-only state; and for a few seconds only.) With a 1 kΩ
  load and +5 V commanded, remove −15 V: U12 opens, I → 0 while the
  commanded V stays; the firmware must report the
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
the voltage across R8, the sweep span, the U5 stability waveforms, the
plateau current and TP2 voltage at both polarities and at two commands,
the entry into the limit, the R30 / U12 / F1 / F2 / U5 temperatures after
the soak, the plateau stability result with each load, the fast exit from
the negative limit with each inductor, the Kelvin common-mode term, the
R10 thermal-EMF
offset, and the calibration constants. Update `docs/audit_corrections.md`
§"What is calculated, not measured" with the measured values.
