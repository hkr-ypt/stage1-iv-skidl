# Final design audit — record

An independent audit of the hardware before fabrication: the SKiDL source,
the generated schematic and netlist, the routed board, the footprints, the
validation code and the documents were each checked against the others and
against the manufacturers' data sheets, and the critical circuits were
simulated or worked through at their tolerance limits. This file records
what was found, what was changed and why, what was verified, and what is
left as it is.

- Base: commit `e69aa3e`.
- Scope: hardware only. Firmware was read where it states a hardware
  assumption and was not changed (§7).
- Source of truth: SKiDL → `python3 main.py` regenerates the netlist, the
  nine sheets, the component manifest and the routed board. No generated
  file was edited by hand.
- **The board's copper, pads, zones, placement, outline and silkscreen are
  unchanged** (§5). Every change is a fitted part, a value or an order code
  on footprints that were already there.
- Nothing here was measured on hardware. Wording used below. **Guaranteed**:
  follows from data-sheet limits (min/max) and the tolerances of the fitted
  parts. **Expected**: follows from typical values or curves.
  **Simulated**: ngspice 42 with the manufacturer's macromodel.
  **Bench**: must be measured (`bringup_checklist.md`).

Data sheets used (the copies read for this audit): TI OPA547 SBOS056F,
OPA548 SBOS070D, OPA549 SBOS093E, OPA2277 SBOS079C, INA128 SBOS051G,
ADS1256 SBAS288K, DAC8552 SLAS430A, REF50xx SBOS410O, TMUX1101 SCDS410D;
Toshiba TLP176AM Rev. 7.0.A; Vishay BZT52 doc. 86342 (Rev. 1.1), PR02 doc.
28729, RS doc. 30204; Diodes Inc. BZT52C DS18004; Bel 0ZRM (Apr 2023);
onsemi MBR0540. The STM32F411 pin assignments were checked against the
pinned KiCad symbol (generated from ST's pin database), because ST's site
could not be fetched from the audit environment.

---

## 1. Disposition

| # | Finding | Class | Disposition |
|---|---|---|---|
| 1 | U5's 49 mA current limit cannot be set on an OPA548: the setting is 1 % of its range and lies inside the limit circuit's tolerance, supply shift and drift. The real limit could be zero (no output) or above the passive bound (no plateau) | **Confirmed error** | **Fixed.** U5 → **OPA547T** (same family, pinout, package, footprint); R7 → **348 kΩ 1 %**. Plateau centred on 49 mA at ±15 V, above zero and below the passive bound with every data-sheet error stacked (§3.1) |
| 2 | U12 (FORCE+ photorelay) was not guaranteed closed at the nominal ±15 V rails: the analysis took D7 at its pulsed data-sheet voltage, and D7 runs about 60 K above ambient | **Confirmed error** | **Fixed.** D7 → **BZT52B24** (±2 %; it was the ±6 % BZT52C24). Guaranteed closed from ±14.25 V at 25 °C, ±14.7 V at 50 °C (§3.2) |
| 3 | ADS1256 crystal load capacitors C26/C27 were 22 pF; SBAS288K Fig. 22 specifies 5 pF to 20 pF | **Confirmed error** (outside the data sheet) | **Fixed.** C26, C27 → **18 pF** (§3.3) |
| 4 | R30's order code PR02000201000FB100 names no real part; the nearest real one is the 1.3 W FeCu-lead version at the wrong lead pitch | **Confirmed error** (procurement) | **Fixed.** → **PR02000201000FA100** (copper leads, 2 W, axial) (§3.4) |
| 5 | The documented PTC contingency (swap in 0ZRM0017FF1E) would, in F2, let an external source put 16.7 W into R10 against its 15 W short-time rating | **Potential issue** (documents only) | **Fixed in the documents.** The swap is for F1 only (§3.5) |
| 6 | Nothing in the validation would have caught finding 1 | Validation gap | **Closed.** `measurement_range.py` now fails if the plateau can reach zero; the symbol build cross-checks U5's pins against the OPA547 data sheet too; guard injection 34 proves the gate fails (§3.6) |
| 7 | The committed board stores no zone fills, so Gerbers plotted without a refill have no ground pours | Intentional and documented; **fabrication hazard if missed** | No change. Release procedure restated and verified (§5) |
| 8 | Board-level observations: supply-rail track width, the bulk and crystal capacitors' AGND return through the ADC's ground island, shunt return resistance, PTC lead dress, jack hardware | Verified adequate, or procurement/assembly notes | No change (§6) |

---

## 2. What was checked and found sound

So that the absence of a finding means something, this is what the audit
covered and found correct:

- **Build.** The committed netlist, schematic sheets, manifest and board are
  byte-for-byte what `main.py` generates from the source (only the netlist's
  date line differs).
- **Sweep source.** `V_SWEEP = 8.0321·(V_A − V_B/2)`, ±10.040 V; the OPA2277
  difference amplifier's resistor arithmetic, output swing and input range;
  the 0 V power-up state.
- **Reference gate and heartbeat pump.** TMUX1101 pinout and logic
  thresholds (VIH 1.49 V, VIL 0.87 V at 5 V); pump simulated across
  tolerance (§4.3).
- **DAC8552** logic thresholds at 5 V from a 3.3 V MCU (VIH 2.4 V).
- **Measurement chain.** INA128 gains (2.4006 and 1), input common-mode and
  output ranges at ±15 V with REF at 2.5 V; Kelvin attenuator k = 0.19920;
  the ADS1256 absolute input window with the buffer off, and the data
  sheet's sanction of AINCOM at 2.5 V; the ±11.8 V / ±97.8 mA guaranteed
  ranges recomputed independently of `measurement_range.py`.
- **Reference.** REF5025 pinout, load and output-capacitor range; one
  2.500 V reference for DAC, ADC and both measurement zeros.
- **Protection.** The passive fault envelope (128 mA / 143 mA, R30 ≤ 1.96 W
  of 2 W, R9 ≤ 0.21 W of 0.25 W), the J7 external-source case (R10
  14.3 W of 15 W for 5 s), D5's stand-off against the ±10.04 V sweep, the
  rail clamps D8/D9, U12's guaranteed-open condition.
- **Pin provenance.** U5 against both data sheets (§3.6); ADS1256 against
  SBAS288K; STM32 pins used against the pinned symbol.
- **Board** (KiCad 10.0.8, zones refilled): 0 DRC errors, 0 unconnected,
  0 schematic-parity problems under both rule sets, the two known
  silk-to-edge warnings at J3; KiCad schematic ERC 0 errors, 0 warnings.
  Force path copper (0.5 mm), the shunt's Kelvin tap (the track to TP3 and
  U6 +IN leaves R10 pad 1 itself, on the layer opposite the force track;
  U6 −IN's return is in §4.4), AGND/DGND joined only at R31, the SENSE
  pair kept clear of FORCE, drill table, footprints against the purchased
  parts' drawings (U5, U12, F1/F2, R10, R30) and, for the 4 mm jacks, which
  have no part number, the hole size and spacing (§6).

---

## 3. Findings and changes

### 3.1 U5 current limit: OPA548T → OPA547T, R7 1.43 MΩ → 348 kΩ

**The mechanism.** The OPA547, OPA548 and OPA549 are one family with one
current-limit circuit, `I_LIM = K × 4.75 V / (R_int + R_CL)`:

| Part | Limit range | K | R_int | Limit tolerance (TI, 25 °C, ±30 V) |
|---|---|---:|---:|---|
| OPA547 (SBOS056F) | 0…750 mA | 5000 | 31.6 kΩ | ±10 mA typ, ±30 mA max, at 375 mA |
| OPA548 (SBOS070D) | 0…5 A | 15000 | 13.75 kΩ | ±100 mA typ, ±250 mA max, at 2.5 A |

The errors of that circuit are currents, not percentages of the setting:

- TI says so for the largest part. OPA549, SBOS093E: "Offset in the current
  limit circuitry may introduce approximately ±0.25A variation at low
  current limit values", and "Operation of the OPA549 with current limit
  less than 1A results in reduced current limit accuracy. Applications
  requiring lower output current may be better suited to the OPA547 or
  OPA548."
- The typical curves of the two smaller parts show it. Measured off
  SBOS056F's "Current Limit vs Supply Voltage" and "Current Limit vs
  Temperature" at the three settings plotted (R_CL = 15.9 kΩ, 31.6 kΩ,
  63.4 kΩ: 500, 375, 250 mA):

  | OPA547, typical | 500 mA | 375 mA | 250 mA |
  |---|---:|---:|---:|
  | +I_LIM at ±15 V, relative to ±30 V | −11.9 mA | −11.9 mA | −12.0 mA |
  | −I_LIM at ±15 V, relative to ±30 V | −4.0 mA | −4.1 mA | −4.1 mA |
  | change from 20 °C to 70 °C | −9.7 mA | −9.3 mA | −9.3 mA |

  The same number of mA at every setting: 0.80 and 0.27 mA per volt of
  rail, and −0.19 mA/K (−0.14 mA/K above 70 °C).
  On the OPA548 (SBOS070D Fig. 3 and 4, read with about ±10 mA resolution):
  +0.65…0.95 mA/K at all three settings, and at ±15 V the limit is 80 mA
  lower than at ±30 V at the lowest setting plotted (1 A), unchanged at
  2.5 A and 50 mA lower at 4 A.

**Why the OPA548 could not do this job.** The design wants a plateau near
49 mA. With R7 = 1.43 MΩ the OPA548 equation gives 49.4 mA: 1 % of its
range, I_SET = 3.3 µA of 330 µA. Against that stand a ±100 mA typical
(±250 mA maximum) unit tolerance, tens of mA of supply shift and
+0.8 mA/K. The real limit of a given board could be anywhere from zero —
U5 unable to drive the DUT at all, in one polarity or both — to far above
the passive 91 mA bound, where there is no plateau and F1/F2 carry close to
their hold current into a shorted DUT. TI's own OPA548 macromodel
(simulated) does not limit below +87 / −94 mA with that resistor: into the
board's ≈ 120 Ω short-circuit path it simply delivers 83 mA at ±10 V. The
earlier record (`final_corrections.md` §2.7) had noted that the setting was
uncharacterised and made sure nothing relied on its *value*; what it did
not address is that a limit that can be zero is an instrument with no
output. On a one-shot build that is not a bring-up item.

**The change.** U5 is fitted with an **OPA547T**. On that part the same
plateau is about 8 % of the range (I_SET = 12.5 µA of 150 µA; TI tabulates
settings down to 100 mA), and the limit errors are four to ten times
smaller. R7 is chosen for the conditions the board actually runs in (±15 V,
U5 self-heated), not for the equation's conditions (±30 V, 25 °C):

```
I_LIM(equation) = 5000 × 4.75 V / (31.6 kΩ + 348 kΩ) = 62.6 mA

Expected plateau at ±15 V        sourcing     sinking
  U5 case 25 °C (cold, pulsed)    50.6 mA      58.5 mA
  U5 case 45 °C (idle, warm)      46.8 mA      54.7 mA
  U5 case 70 °C (held in limit)   42.0 mA      50.0 mA
Between units                     ±10 mA typical, ±30 mA maximum

Every error against it (85 °C case, −30 mA unit, sourcing):   9 mA
Every error with it    (25 °C case, +30 mA unit, sinking):   89 mA
```

| Requirement | Result |
|---|---|
| The plateau is above zero for every unit: the maximum unit tolerance (a data-sheet limit) and the supply and temperature shifts (typical curves; TI gives them no limits) all taken against it | ≥ 9 mA |
| It is centred on the design value of 49 mA where the board operates (expected) | 42…59 mA with polarity and temperature |
| It stays below the passive bound, 10.04 V / 110 Ω = 91.3 mA, so a plateau exists | ≤ 89 mA |
| It stays inside the measurable range, ±97.8 mA guaranteed | yes |

453 kΩ, the value the bare equation gives for 49 mA, was considered first
and rejected: at ±15 V it gives about +37 / −45 mA cold and can reach
−4 mA with every error against it. `measurement_range.py` now rejects it.
A value below 348 kΩ would buy more distance from zero, but 300 kΩ already
puts the upper end of the spread (98 mA) above the passive bound, and in
the macromodel's reading (below) it puts the plateau at the 78…87 mA that
the series resistors allow into a short anyway, which is no plateau.

**What the choice rests on, and what it does not.** SBOS056F specifies the
unit tolerance at one setting (375 mA) and plots the supply and temperature
curves at 250–500 mA. Applying them unchanged at ≈ 60 mA is an
extrapolation; it is the one the physics and TI's OPA549 note support, and
all three plotted settings agree with it. The design does not depend on
it. If the shifts were absent at this setting the plateau would be 62.6 mA
±30 mA (33…93 mA); TI's macromodel, which carries a +10…12 mA offset of its
own and no temperature dependence, gives +74 / −67 mA (simulated). In every
one of these readings there is an output in both polarities, the current
is measured, and R30 bounds it. The plateau is not a precision quantity on
any part of this family: bring-up measures it in both polarities and the
firmware's `i_plateau` takes the measured value. To move one unit, change
R7: about 6 kΩ per mA.

**Why nothing got worse.** Same seven pins, same package (TI KVT, both data
sheets), same footprint, same net names, no copper change.

| Parameter | OPA548 (was) | OPA547 (now) | Need here |
|---|---|---|---|
| Output swing, guaranteed | (V+) − 2.4 V, (V−) + 1.3 V at 0.6 A | (V+) − 1.8 V, (V−) + 1.2 V at 0.1 A | ±10.95 V of ±15 V: 2.25 V spare (was 1.65 V) |
| Input common-mode | (V+) − 3 V | (V+) − 3 V | 10.04 V: 1.96 V spare |
| Continuous output current | 3 A | 0.5 A | ≤ 0.143 A in the worst fault |
| Offset, max / drift | ±10 mV / ±30 µV/°C | ±5 mV / ±25 µV/°C | outside the measurement (Kelvin sense) |
| Bias current, noise, CMRR | −500 nA, 90 nV/√Hz, 80 dB | same | — |
| Open-loop gain (1 kΩ) | 90 dB min | 100 dB min | — |
| GBW / slew rate | 1 MHz / 10 V/µs | 1 MHz / 6 V/µs | set upstream by U3 (0.8 V/µs) |
| Quiescent current | ±17 mA typ, ±20 mA max | ±10 mA typ, ±15 mA max | 0.30 W instead of 0.51 W in U5 |
| E/S pin to V+ through 10 kΩ | allowed | allowed ("Input Shutdown Voltage: V+") | R8 unchanged |
| Disabled-output leakage (typ) | a few mA | ≈ 0.1 mA | E/S is not the safe state either way |
| Tab | V− | V− | unchanged |
| θJA, no heat sink | 65 °C/W | 65 °C/W | ≤ 62 K rise (below) |

U5 dissipation: for a shorted DUT the output stage dissipates
(V_S − I·R_path)·I, which is at most V_S²/(4·R_path) = 15.5²/(4 × 121.2) =
0.50 W whatever the limit is; quiescent adds 0.30 W typical, 0.45 W
maximum. 0.95 W at 65 °C/W is a 62 K rise with no heat sink.

The other parts of the stage are unchanged and were re-checked for the new
values: C14 (10 nF across R7) is the optional noise capacitor of SBOS056F
Fig. 3, and the ILIM node settles in about 0.3 ms; R30 carries 0.24 W at
49 mA and 0.79 W at 89 mA (2 W part); F1/F2 hold 100 mA at 23 °C, 80 mA at
40 °C, 68 mA at 50 °C, so a plateau at the far upper end of its spread can
trip them above about 32 °C, which is benign and self-resetting.
R38/C49 (snubber, DNP) are unchanged: both data sheets say an R-C network
"may be required" when the amplifier is driven into current limit, U5's
load here is resistive (F1 + R30 ≥ 103 Ω ahead of the cable), the
simulation is stable without it (§4.1), and the pads are on the board.

### 3.2 U12 LED string: D7 BZT52C24 → BZT52B24

U12 (TLP176AM) connects FORCE+ only while both ±15 V rails are up. Its LED
is in the string `+15VA → R39 620 Ω → LED → D7 (24 V zener) → −15VA`.
Guaranteed open with a rail absent is unaffected by this audit: the string
then has ≤ 16.0 V across it, below D7's leakage test voltage (18 V for the
Vishay part, IR ≤ 0.1 µA).

Guaranteed **closed** needs IF ≥ IFT (3 mA maximum at 25 °C; 10 % more is
allowed here at 50 °C, where Toshiba's Fig. 14.1.6 shows +7 % typical). The
string leaves only (rails) − V_Z − V_F ≈ 4.7 V for R39, so D7's voltage
decides. `final_corrections.md` §2.5 took D7 at its data-sheet maximum,
25.6 V, and concluded ±14.45 V. That voltage is measured with 5 ms pulses.
In this circuit D7 dissipates about 140 mW continuously; at R_thJA =
420 K/W its junction runs about 60 K above ambient, and with a temperature
coefficient of +8…+10 × 10⁻⁴/K its voltage is about 1.3 V higher than the
pulsed value. With that included, at the limits (D7 at its upper voltage
limit and upper coefficient, no fall of V_Z below the 5 mA test current,
LED at 1.4 V, R39 +1 %):

| | BZT52C24, ±6 % (was) | **BZT52B24, ±2 % (now)** |
|---|---|---|
| Expected IF at ±15.0 V | 5.6 mA | 5.6 mA |
| Minimum IF at ±15.0 V, 25 °C | 3.4 mA | **4.7 mA** |
| Minimum IF at ±15.0 V, 50 °C | 2.7 mA (below IFT) | **4.0 mA** |
| Guaranteed closed from, 25 °C | ±14.8 V | **±14.25 V** |
| Guaranteed closed from, 50 °C | ±15.3 V (not at nominal rails) | **±14.7 V** |
| Same, if the board cools D7 30 % worse than the data sheet's test board | ±15.0 V / ±15.4 V | ±14.4 V / ±14.8 V |
| Maximum corner, ±15.5 V, 25 °C ambient: IF / D7 / junction | 8.9 mA / 0.22 W / 116 °C | 7.9 mA / 0.20 W / 108 °C |
| Same at 70 °C ambient: junction (limit 150 °C) | 153 °C | 145 °C |

The ±2 % grade has the same nominal voltage and the same SOD-123
footprint, is better at the minimum corner and better at the maximum
corner (less current, a cooler junction), and its leakage test point is
higher. (If the board cools D7 better than the data sheet's test board the
maximum-corner current is higher and the junction cooler: ≤ 8.5 mA and
0.21 W at 300 K/W, against 9.5 mA and 0.23 W before.) Lowering R39 instead
was rejected: it buys the minimum corner with D7's dissipation, which is
what limits this string. The order code is
Vishay **BZT52B24-E3-08** (23.52…24.48 V at 5 mA, Z_Z ≤ 70 Ω, 300 mW on
FR-4).

Two things remain true and are not defects. Toshiba recommends IF ≥ 5 mA
and specifies R_ON at 5 mA; the expected 5.6 mA meets that and the
guaranteed minimum does not, which matters only for R_ON (≤ 2 Ω), and U12
is outside both U5's loop and the Kelvin sense. And D7 runs warm by design
(junction ≈ 85 °C at a 25 °C ambient, expected).

### 3.3 ADS1256 crystal load capacitors: 22 pF → 18 pF

SBAS288K, Fig. 22 "Crystal Connection": "C1, C2: 5pF to 20pF", with the
text "external capacitors must be provided to ensure start-up and a stable
clock frequency". C26 and C27 were 22 pF, outside that range. They are now
18 pF, the value of TI's own connection example (Fig. 25), on the same
0603 pads. The only other effect is a clock a few tens of ppm faster,
which nothing in the measurement resolves.

### 3.4 R30 order code

R30 must be the copper-lead Vishay PR02: 2 W, 75 K/W (doc. 28729); the
FeCu-lead version is 1.3 W, 115 K/W. The fault envelope puts up to 1.96 W
in it. The code in the design was PR02000201000F**B1**00. Decoded with the
data sheet's part-number table: wire type 2 = Cu 0.78 mm, but packaging B1
exists only with FeCu 0.78 mm leads, pre-formed radially ("double kink") at
15 mm pitch. That code names no real part, and the nearest real one is the
wrong rating and the wrong shape for the 12.7 mm axial footprint. The code
is now **PR02000201000FA100**: Cu 0.78 mm, axial, taped (A1), the same
construction as the data sheet's own example PR02000201001JA100. The
footprint, value and rating in the design did not change.

### 3.5 PTC contingency: F1 only

`bringup_checklist.md` offered 0ZRM0017FF1E (170 mA hold) as a contingency
"if either PTC" tripped on the plateau. That part's minimum resistance is
2.0 Ω, not 3.0 Ω. In F1 the rail-fault envelope still holds (R30 ≤ 1.99 W
of 2 W). In F2 it does not: an external 15.5 V on J7 would drive
15.5 / (2.0 + 9.99) = 1.29 A and 16.7 W into R10, above the 15 W short-time
rating that `final_corrections.md` §2.6 relies on. The checklist and the
source comment now say F1 only, and name the better remedy now available
for a high plateau: R7.

### 3.6 Validation

- `validation/measurement_range.py` reads which amplifier is fitted from
  U5's value, computes the plateau at this board's rails from that part's
  data-sheet figures (equation, supply shift, temperature drift, unit
  tolerance) and **fails the build if it can reach zero**. It reports the
  expected plateau in both polarities and its whole spread. No existing
  check was removed or loosened; the passive fault envelope is checked as
  before and does not depend on the plateau.
- `tools/author_project_symbols.py` cross-checks every U5 pin against the
  OPA547's own data sheet as well: the "Pin Configurations" drawing of
  SBOS056F, committed verbatim as
  `libraries/provenance/opa547_sbos056f_pin_configurations.txt`, must put
  the same function on every lead as the OPA548 "Pin Functions" table the
  symbol was already checked against. The parser was tested against
  altered excerpts (swapped labels, disagreeing packages, a missing row).
- `tools/test_guard_fails.sh` injection 34 puts U5 back to an OPA548T with
  1.43 MΩ and requires the range gate and `main.py` to fail.

---

## 4. Simulation and analysis record

### 4.1 Output stage (ngspice 42, TI macromodels OPA547 "Final 1.1", OPA548)

The stage as built: follower with R9 = 10 Ω inside the loop and C19 =
100 pF across it, R7 ‖ C14 on ILIM, R8 on E/S, ±15 V; load = F1 5 Ω + R30
100 Ω + U12 0.5 Ω + D5 300 pF + 1 µH lead + C_L at the jacks + DUT + F2 5 Ω
+ R10 10 Ω. Slow transient ramps were used for the limit (DC sweeps through
the limit gave continuation artefacts).

| Test | Result |
|---|---|
| Limit, OPA548 + 1.43 MΩ, ramp to ±12 V into 120 Ω | No limiting at ±10 V (83 mA, set by the resistors); limit +87 / −94 mA |
| Limit, OPA547 + 348 kΩ, same | Plateau +74.2 / −67.3 mA at ±15 V (+74.9 / −73.0 mA at ±30 V; no change from 25 °C to 85 °C in the model) |
| Stability, OPA547 + 348 kΩ: ±10 V command, DUT 0.01 Ω and 10 Ω (on the model's plateau) and 50 Ω (58.7 mA, below it), C_L = 100 pF, 10 nF, 1 µF, with and without R38/C49 (36 cases) | Every case settles with no ripple on the current or on U5's output. (Five negative-polarity cases needed a second set of solver tolerances to pass the instant the model's limiter engages; with it they settle like the rest) |
| Follower step response, 1.0 → 1.2 V, C_L = 100 pF, 10 nF, 1 µF, DUT 0.01 Ω, 1 kΩ, open | OPA547: overshoot ≤ 2 mV (1 %). OPA548 in the same network: 36–43 mV (18–21 %) |

A macromodel is evidence, not proof, of loop behaviour in current limit;
that is why the snubber pads stay and the bring-up check stays.

### 4.2 Hand analysis at the limits

The U5 plateau (§3.1), U5 dissipation (§3.1), the U12 LED string at both
corners with D7's electro-thermal feedback solved iteratively (§3.2), R30,
R9 and R10 in the plateau and in the rail fault, and the PTC hold current
against the plateau over temperature.

### 4.3 Heartbeat charge pump (ngspice)

R36/C47/D6/C48/R37 driving U11 SEL (VIH 1.49 V, VIL 0.87 V at 5 V),
simulated with a BAT54 model. At nominal (3.3 V drive, 1 kHz) DAC_ARM is
2.77 V, armed 3 ms after the heartbeat starts and disarmed 53 ms after it
stops (17…64 ms across the C47, C48 and R37 tolerance and temperature
corners; 66 ms if PC5 sticks high, 79 ms in the slowest corner). In the
weakest corner simulated (2.9 V drive, 500 Hz, which is half the required
rate, C47 −20 %, 85 °C, 1 µA of leakage) it is 1.92 V on average and 1.82 V
at the ripple minimum. A single edge lifts it to 0.55 V (0.76 V with C47
+20 % and C48 −20 %), below VIL. `audit_corrections.md` reaches the same
conclusions with more pessimistic diode and leakage figures (2.4 V nominal,
1.87 V worst case at 1 kHz). No change.

### 4.4 Ground copper (finite-difference DC solve of the filled AGND pour)

The DUT return current flows from R10 through the AGND pour to R31. Solved
on the board as routed, with the zones filled by KiCad: 15.5 mΩ from R10
pad 2 to R31 pad 1 (1.55 mV at 100 mA, common to every analog part). The
measurement does not see it as an error that matters. At 100 mA the pour
drops 54 µV between R10's low-side pad and U6's −IN tap, which adds 54 ppm
to the current reading (a constant gain term, removed by calibration); the
ADC's reference return sits 16 µV from the reference's own ground pin,
6.5 ppm of 2.5 V; and the two Kelvin divider returns (R32, R33) sit at the
same potential. No routing change is warranted.

---

## 5. The board

**Nothing on the board moved.** `git diff` of `stage1_iv.kicad_pcb` is ten
lines, all `Value`, `MPN` or `Description` properties of U5, R7, C26, C27,
D7 and R30 (text on the fabrication layers and hidden fields). Read back
with KiCad's own loader and compared object by object with the board at
the base commit: 897 track segments, 113 vias, 415 pads, 135 footprints
(position, rotation, side), both zones, every footprint graphic and every
board drawing are identical. `layout/*.layout` and `layout/*.eco` are
untouched.

**Fabrication release.** The committed board stores zone *definitions*
without fills (`generators/routed_pcb.py`; a deliberate choice, so that the
file is reproducible). Plotted as it is, the copper layers contain **no
AGND or DGND pour**. Checked on this board with KiCad 10.0.8:
`kicad-cli pcb export gerbers` without a refill writes two copper Gerbers
with no filled region at all; with `--check-zones` it writes 18 regions on
F.Cu and 7 on B.Cu. The drill export gives 13 plated tools (0.30…1.40 mm,
the four 6.1 mm jack holes, four routed 0.6 mm slots at J3) and the three
3.2 mm non-plated mounting holes. For a board made once, the release steps
are therefore:

1. `kicad-cli pcb drc --refill-zones --save-board` (or open the board and
   press B), and confirm 0 errors, 0 unconnected.
2. Plot with `kicad-cli pcb export gerbers --check-zones` (in the GUI, leave
   "Check zone fills before plotting" ticked), and export the drill files
   (plated, non-plated, and the four slots at J3).
3. **Look at both copper layers in a Gerber viewer and see the pours**
   before sending anything.

---

## 6. Observations left as they are

None of these is an error; each was checked and judged not to justify a
change, least of all a routing change.

| Observation | Assessment |
|---|---|
| ±15 V rails are routed at 0.20 mm | Adequate: ≤ 0.15 A, tens of mV of drop, negligible heating |
| The AGND returns of the ±15 V bulk capacitors C5 and C6 (60 and 48 mm) and of the crystal capacitors C26/C27 (22 mm) are one 0.20 mm track, and it enters the AGND copper at the small top-side island that carries U8's AGND and VREFN pins; that island joins the main pour through about 6 mΩ (ground solve, §4.4) | Functional, and no DC error: none of these returns carries direct current. Ripple and clock-rate currents on that track move the ADC's reference return by microvolts, and the ADC's own ≈ 10 mA of analog supply current holds it about 65 µV (26 ppm of the reference) from the reference's ground, a constant that calibration removes. C5/C6 sit 0.12–0.15 Ω further from their loads than they ideally would; every amplifier has its own 10 µF and 100 nF. The crystal's return is 2 Ω at 7.68 MHz against the load capacitors' 1.2 kΩ, and §3.3 improved the oscillator's margin. It is the routed layout; not worth touching |
| R10's return to R31 is 15.5 mΩ of pour (6.1 mΩ on the original routed board, before the ECOs) | No measurable effect (§4.4) |
| F1/F2: Bel's leads are offset 2.2 mm front to back, the footprint's holes 1.2 mm | Ø0.51 mm leads in 1.0 mm holes: a slight lead dress at assembly |
| R30: 10.0 mm maximum body on a 12.7 mm pitch | Fits; bends start close to the end caps. Assembly note added |
| REF5025: C9 is a 4.7 µF ceramic (milliohms of ESR) and TRIM/NR is left open | Stable: SBOS410O asks for 1…50 µF with ESR ≤ 1.5 Ω. The 1–1.5 Ω ESR and the NR capacitor it also suggests are optional noise measures that would need new pads; not worth a layout change |
| C24/C25 (ADC input filter, 1 nF) do not name a dielectric | Buy C0G/NP0. X7R would work at DC but adds voltage-dependent settling |
| R1–R6, R11–R13, R32, R33 are 0.1 % with no stated TCR | Buy ≤ 25 ppm/°C; the ratio pairs from one reel. Already review item R10 of the design document |
| The 4 mm jacks have no part number; the footprint is a 6.1 mm plated hole | Choose a jack with a ≤ 6 mm stud whose body and nut stay under about 12 mm across: adjacent jacks are 13.75, 15.0 and 17.25 mm apart, H1 is 10.6 mm from J7 and the nearest F1/F2 pads are 8.9–9.1 mm from J5/J7, which leaves 1–2 mm to an M6 nut. The spacing is not the 19 mm of a dual-banana plug; single leads only |
| D7 junction ≈ 85 °C at 25 °C ambient; at ±15.5 V, 70 °C ambient and the lowest zener voltage it reaches 145 °C of 150 °C | Inside ratings, and better than before (§3.2). Not an instrument for a 70 °C ambient |
| U12's guaranteed minimum LED current (4.0–4.7 mA) is below Toshiba's recommended 5 mA | §3.2: closure is guaranteed; only R_ON's specification point is missed, outside the measurement |
| The plateau differs by polarity and sags as U5 warms | Inherent in this amplifier family (§3.1); measured at bring-up |
| With the buffer off the ADS1256 input is 150 kΩ/PGA (4.7 kΩ at PGA 32 and 64) behind 100 Ω | A gain term per PGA setting, removed by system calibration (§7) |
| `manifest.yaml` does not parse as strict YAML (its `validation` section) | Pre-existing; nothing reads it by machine. Left |

Sources that could not be reached from the audit environment: ST's
STM32F411 data sheet (the pinned KiCad symbol was used instead), the
Nexperia BZT52 data sheet (the Vishay one was used and its part ordered by
number), TI's E2E forum.

---

## 7. For the firmware work

Hardware facts only; no firmware file was changed.

- `firmware/src/core/nominal.h`, `firmware/sim/model_board.c`,
  `host/stage1iv/sim.py` and `docs/firmware/firmware_spec.md` (H13) still
  say OPA548 and 1.43 MΩ in comments. The nominal they use, 49 mA, is still
  the design centre.
- The plateau is polarity-dependent (sinking ≈ 8 mA above sourcing) and
  falls by ≈ 0.2 mA/K as U5 warms, so it sags by several mA during a long
  hold. A threshold of 0.8 × the smaller measured polarity, as now, copes
  with that; a symmetric, constant plateau should not be assumed.
- The `i_plateau` calibration window is 20…100 mA. The expected range is
  30…75 mA; the data-sheet extremes are 9 and 89 mA, so a unit at the
  extreme low end would fall outside the window and should be corrected
  with R7 rather than accepted.
- U12 is guaranteed closed from ±14.25 V (25 °C), not ±14.45 V.
- If PGA 32 or 64 is ever used, the 100 Ω filter resistors load the 4.7 kΩ
  input by 2.1 %: gain calibration is per PGA setting.

---

## 8. Validation

Run on the audit machine with KiCad 10.0.8, kicad-symbols/footprints
9.0.9.1 and Python 3.11, every step of `.github/workflows/build.yml`:

| Check | Result |
|---|---|
| `main.py` (structural check, SKiDL ERC, 18 connectivity assertions, measurement range, board checks, KiCad DRC) | PASS; SKiDL ERC 0/0; 18/18; V ±11.78 V, I ±97.8 mA; board 16/16 |
| `author_project_symbols.py --check`, `gen_project_config.py --check`, `vendor_footprints.py --check` | current |
| `check_kicad_loads.py`, `footprint_check.py`, `erc.py`, `schematic_check.py`, `kicad_erc_power.py`, `kicad_roundtrip.py` | PASS |
| `update_pcb_noop.py --self-test` and `update_pcb_noop.py` | PASS: Update PCB from Schematic would change nothing |
| `routed_board_check.py` | PASS. KiCad DRC, zones refilled: 0 errors, 0 unconnected, 0 parity under the as-built rules and under the netclass policy; the 2 silk-to-edge warnings at J3, as before |
| `reproduce_reference.py` | PASS |
| Fab conformance, pad-gap bound, identifier audit (141 parts, 0 unresolved) | PASS |
| Deterministic output (build twice, compare) | byte-identical |
| `kicad-cli sch erc` | 0 errors, 0 warnings |
| `tools/test_guard_fails.sh` | 34 of 34 injected faults caught |
| `main.py --autoplace`; build with no KiCad present | PASS; SKIPPED reported, board untouched |

What this does not establish: nothing was measured. The plateau in both
polarities, U12's thresholds, D7's and R30's temperatures, stability on
the plateau with a real cable and DUT, and the crystal's start-up are
bring-up measurements (`bringup_checklist.md` §3, §5, §7, §8).
