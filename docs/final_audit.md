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
- This record was then checked by independent reviewers in three passes
  (nine tried to refute its claims; eight swept the design block by block
  for what it had missed; seven then tried to refute the corrections) and
  corrected each time. §9 says what they changed.

Data sheets used (the copies read for this audit): TI OPA547 SBOS056F,
OPA548 SBOS070D, OPA549 SBOS093E, OPA2277 SBOS079C, INA128 SBOS051G,
ADS1256 SBAS288K, DAC8552 SLAS430A, REF50xx SBOS410O, TMUX1101 SCDS410D;
Toshiba TLP176AM Rev. 7.0.A; Vishay BZT52 doc. 86342 (Rev. 1.1), PR02 doc.
28729, RS doc. 30204; Diodes Inc. BZT52C DS18004; Bel 0ZRM (Apr 2023);
onsemi MBR0540; Kyocera AVX TCJ and TAJ; KEMET T491. The STM32F411 pin
assignments were checked against the pinned KiCad symbol (generated from
ST's pin database) and against a mirror copy of DS10314 Rev 8; ST's own
site could not be fetched from the audit environment.

---

## 1. Disposition

| # | Finding | Class | Disposition |
|---|---|---|---|
| 1 | U5's 49 mA current limit cannot be relied on with an OPA548: the setting is 1 % of its range and a fifth of the limit's ±250 mA tolerance. The real limit could be zero (no output) or above what the series resistors pass (no plateau) | **Confirmed error** | **Fixed.** U5 → **OPA547T** (same family, pinout, package, footprint); R7 → **348 kΩ 1 %**. Expected plateau 40…65 mA at ±15 V; the worst-case stack stays 8 mA clear of zero (§3.1) |
| 2 | U12 (FORCE+ photorelay) was not guaranteed closed at the nominal ±15 V rails: the analysis took D7 at its pulsed data-sheet voltage, and D7 runs about 60 K above ambient | **Confirmed error** | **Fixed.** D7 → **BZT52B24** (±2 %). Closed from ±14.3 V at 25 °C (data-sheet limits) and about ±14.75 V at 50 °C (expected). The rails are to be set to ±15.0 V ±0.2 V (§3.2) |
| 3 | ADS1256 crystal load capacitors C26/C27 were 22 pF; SBAS288K Fig. 22 says 5 pF to 20 pF | **Confirmed error** (outside the data sheet's stated range) | **Fixed.** C26, C27 → **18 pF** (§3.3) |
| 4 | R30's order code PR02000201000FB100 names no real part; the nearest real one is the 1.3 W FeCu-lead version at the wrong lead pitch | **Confirmed error** (procurement) | **Fixed.** → **PR02000201000FA100** (copper leads, 2 W, axial, 1 %) (§3.4) |
| 5 | The documented PTC contingency (swap in 0ZRM0017FF1E) would, in F2, let an external source put 16.7 W into R10 against its 15 W short-time rating | **Potential issue** (documents only) | **Fixed in the documents.** The swap is for F1 only (§3.5) |
| 6 | U5 runs in current limit by design. TI says an R-C network "may be required" for that; TI's OPA547 model oscillates on the plateau with an inductive DUT when none is fitted; and the values that were to be fitted "if needed" (10 Ω + 10 nF, or TI's 3 Ω) would cost this unity-gain stage 10…40° of phase margin | **Potential issue**, both parts: the values by TI's overshoot curve and models fitted to it, the plateau by TI's macromodel | **Changed.** R38 → **100 Ω**; R38 and C49 **fitted** (§3.7). The steady plateau is then quiet in the model. **One thing it does not cure in the model stays open:** a fast exit from the negative limit with an inductive DUT (§3.7, §6) |
| 7 | C17/C18 are "10 µF / 35 V tantalum" on a 3528-21 (case B) footprint. No MnO2 tantalum of that rating exists in that case; the part cannot be bought as written | **Confirmed error** (procurement) | **Fixed.** Order code **TCJB106M035R0200E** (Kyocera AVX polymer tantalum, 10 µF, 35 V, case B). Value, rating and pads unchanged (§3.8) |
| 8 | Nothing in the validation would have caught finding 1 | Validation gap | **Closed.** `measurement_range.py` now fails if the plateau's worst-case stack falls under 5 mA, if a typical unit would show no plateau, if U5's value and order code disagree, or if R7 is unfitted; the symbol build cross-checks U5's pins against the OPA547 data sheet too; guard injections 34–40 prove the gate fails (§3.6) |
| 9 | The committed board stores no zone fills, so Gerbers plotted without a refill have no ground pours | Intentional and documented; **fabrication hazard if missed** | No change. Release procedure restated with exact commands and verified (§5) |
| 10 | Statements in the source comments and documents that the review found wrong: the SENSE− common mode leaves out F2; the "guaranteed" measurement ranges use the nominal channel gain; two rows of the fault table (an external source on FORCE+, an energised DUT on an unpowered board); the REF5025 capacitor range | Document errors, no hardware effect | **Corrected** (§2, §6, §7, §9) |
| 11 | Board-level, procurement, assembly and operating observations | Verified adequate, or notes for the buyer, the assembler and the user | No change (§6) |

---

## 2. What was checked and found sound

So that the absence of a finding means something, this is what the audit
covered and found correct:

- **Build.** The committed netlist, schematic sheets, manifest and board are
  byte-for-byte what `main.py` generates from the source (only the netlist's
  date line differs).
- **Sweep source.** `V_SWEEP = 8.0321·(V_A − V_B/2)`, ±10.040 V nominal; the
  OPA2277 difference amplifier's resistor arithmetic, output swing and input
  range; the 0 V power-up state. (The end points are nominal: §6.)
- **Reference gate and heartbeat pump.** TMUX1101 pinout and logic
  thresholds (VIH 1.49 V, VIL 0.87 V at 5 V); pump simulated across
  tolerance (§4.3).
- **DAC8552** logic thresholds at 5 V from a 3.3 V MCU (VIH 2.4 V).
- **Measurement chain.** INA128 gains (2.4006 and 1), input common-mode and
  output ranges at ±15 V with REF at 2.5 V; Kelvin attenuator k = 0.19920;
  the ADS1256 absolute input window with the buffer off, and the data
  sheet's sanction of AINCOM at 2.5 V. The measurable ranges were
  recomputed independently of `measurement_range.py`: ±11.78 V and ±97.8 mA
  with the ADC window at its limits and the channel gain nominal (the
  figure the documents call guaranteed), ±11.76 V and ±97.3 mA with the
  channel gain and offset at their limits as well. The largest signal the
  hardware can produce (10.09 V, 92.6 mA at worst-case tolerance) fits
  either way, with 115 mV of headroom on the current channel. Both figures
  need AVDD ≥ 4.75 V at U8, i.e. the bench +5 V inside 4.75…5.25 V.
- **Reference.** REF5025 pinout, load and output-capacitor range; one
  2.500 V reference for DAC, ADC and both measurement zeros.
- **Protection.** The passive fault envelope (128 mA / 143 mA, R30 ≤ 1.96 W
  of 2 W, R9 ≤ 0.21 W of 0.25 W), the J7 external-source case (R10
  14.3 W of 15 W for 5 s), D5's stand-off against the ±10.04 V sweep, the
  rail clamps D8/D9, U12's guaranteed-open condition.
- **Pin provenance.** U5 against both data sheets (§3.6); ADS1256 against
  SBAS288K; STM32 pins used against the pinned symbol and DS10314 Table 8.
- **Board** (KiCad 10.0.8, zones refilled): 0 DRC errors, 0 unconnected,
  0 schematic-parity problems under both rule sets, the two known
  silk-to-edge warnings at J3; KiCad schematic ERC 0 errors, 0 warnings.
  Force path copper (0.5 mm from F1 to the jacks and from J7 to R10, with
  no vias; about 16 mm of 0.20 mm track around U5 and R9, which carries
  ≤ 0.143 A and is either inside U5's loop or outside the Kelvin sense),
  the shunt's Kelvin tap (the track to TP3 and
  U6 +IN leaves R10 pad 1 itself, on the layer opposite the force track;
  U6 −IN's return is in §4.4), AGND/DGND joined only at R31, the SENSE
  pair kept clear of FORCE, drill table, footprints against the purchased
  parts' drawings (U5, U12, F1/F2, R10, R30, D7, C17/C18) and, for the 4 mm
  jacks, which have no part number, the hole size and spacing (§6).

---

## 3. Findings and changes

### 3.1 U5 current limit: OPA548T → OPA547T, R7 1.43 MΩ → 348 kΩ

**The mechanism.** The OPA547, OPA548 and OPA549 are one family with one
current-limit scheme, `I_LIM = K × 4.75 V / (R_int + R_CL)`:

| Part | Limit range | K | R_int | Limit tolerance (as each data sheet states it) |
|---|---|---:|---:|---|
| OPA547 (SBOS056F) | 0…750 mA | 5000 | 31.6 kΩ | ±10 mA typ, ±30 mA max, at 375 mA (±30 V, 25 °C case) |
| OPA548 (SBOS070D) | 0…5 A | 15000 | 13.75 kΩ | ±100 mA typ, ±250 mA max, at 2.5 A (T_J = 25 °C) |

TI states the tolerance as a current, at one setting. What happens at a
low setting it says only for the largest part. OPA549, SBOS093E: "Offset in
the current limit circuitry may introduce approximately ±0.25A variation at
low current limit values", and "Operation of the OPA549 with current limit
less than 1A results in reduced current limit accuracy. Applications
requiring lower output current may be better suited to the OPA547 or
OPA548." There is no such statement, and no minimum setting, for the two
smaller parts. Taking their tolerance as an absolute current at a low
setting is the conservative reading and is the one used here.

The OPA547's own typical curves support it. Measured off SBOS056F's
"Current Limit vs Supply Voltage" and "Current Limit vs Temperature" at the
three settings plotted (R_CL = 15.9 kΩ, 31.6 kΩ, 63.4 kΩ: 500, 375, 250 mA):

| OPA547, typical | 500 mA | 375 mA | 250 mA |
|---|---:|---:|---:|
| +I_LIM at ±15 V, relative to ±30 V | −11.9 mA | −11.9 mA | −12.0 mA |
| −I_LIM at ±15 V, relative to ±30 V | −4.0 mA | −4.1 mA | −4.1 mA |
| change from 20 °C to 70 °C | −9.7 mA | −9.3 mA | −9.3 mA |

The same number of mA at each setting: 0.80 and 0.27 mA per volt of rail,
and −0.19 mA/K (−0.14 mA/K above 70 °C). The three curves are one drawn
shape at three levels, so this is one observation, not three. The OPA548's
curves (SBOS070D Fig. 3 and 4, read with about ±10 mA resolution) are not
offsets of one size: +0.65…0.95 mA/K, and at ±15 V the limit is 80 mA
lower than at ±30 V at the lowest setting plotted (1 A), unchanged at 2.5 A
and 50 mA lower at 4 A. The OPA549's own typical curves (SBOS093E, read by
a reviewer) do not show an offset either: its +I_LIM at ±15 V is *higher*
than at ±30 V by about 3 % of the setting at 2, 5 and 8 A, and its
temperature coefficient is near zero at 2 and 5 A. So the three parts
share the equation and TI's note names an offset term, but the typical
supply and temperature behaviour differs in sign and character from part
to part and cannot be carried from one to another. "A current, not a
percentage" is shown for the OPA547 only, and there by one drawn shape.

**Why the OPA548 could not do this job.** The design wants a plateau near
49 mA. With R7 = 1.43 MΩ the OPA548 equation gives 49.4 mA: 1 % of its
range, I_SET = 3.3 µA of 330 µA. Against that stand a ±100 mA typical
(±250 mA maximum) unit tolerance, tens of mA of supply shift at the only
low setting TI plots, and +0.8 mA/K. The real limit of a given board could
be anywhere from zero — U5 unable to drive the DUT at all, in one polarity
or both — to far above what the series resistors pass, where there is no
plateau and F1/F2 carry close to their hold current into a shorted DUT.
TI's own OPA548 macromodel (simulated) does not limit below +87 / −94 mA
with that resistor: into the board's ≈ 120 Ω short-circuit path it simply
delivers 83 mA at ±10 V. No report of an OPA548 programmed for tens of mA
was found, for or against; the finding rests on TI's figures, not on an
observed failure. The earlier record (`final_corrections.md` §2.7) had
noted that the setting was uncharacterised and made sure nothing relied on
its *value*; what it did not address is that a limit that can be zero is
an instrument with no output. On a one-shot build that is not a bring-up
item.

**The change.** U5 is fitted with an **OPA547T**. On that part the same
plateau is about 8 % of the range (I_SET = 12.5 µA of 150 µA), and the
limit errors are four to ten times smaller. R7 is chosen for the
conditions the board actually runs in (±15 V, U5 self-heated), not for the
equation's conditions (±30 V, 25 °C):

```
I_LIM(equation) = 5000 × 4.75 V / (31.6 kΩ + 348 kΩ) = 62.6 mA

With the typical shifts applied as currents, ±15 V   sourcing     sinking
  U5 case 25 °C (cold, pulsed)                        50.6 mA      58.5 mA
  U5 case 45 °C (idle, warm)                          46.8 mA      54.7 mA
  U5 case 70 °C (held in limit)                       42.0 mA      50.0 mA
Between units                                         ±10 mA typical, ±30 mA maximum

Everything against it (R7 +1 %, ±14.5 V, 85 °C case, −30 mA unit, sourcing):   8 mA
Everything with it    (R7 −1 %, ±15.5 V, 25 °C case, +30 mA unit, sinking):   89 mA
```

| Requirement | Result |
|---|---|
| The limit cannot come out at zero. The stack: the maximum unit tolerance (a data-sheet limit, stated at 375 mA), R7's tolerance, the low rail, a hot case, with the typical supply and temperature shifts applied as currents | ≥ 8 mA (an extrapolated stack, not a data-sheet guarantee); the build requires ≥ 5 mA |
| Expected plateau | 40…65 mA: about +47 / −55 mA warm, down to about +42 mA sourcing while held in limit (39 mA with the case at 85 °C), if the typical shifts carry down to this setting unchanged; up to the equation's 62.6 mA if they do not. The design centre is 49 mA |
| A typical unit shows a plateau into a shorted DUT, whose series path passes 79…87 mA at full sweep | ≤ 69 mA with the typical tolerance. The maximum-tolerance stack reaches 89 mA: a unit in its top few mA shows no plateau into a dead short. That is harmless (R30 bounds the current) and is reported, not failed |
| It stays inside the measurable range | yes |

453 kΩ, the value the bare equation gives for 49 mA, was considered first
and rejected: at ±15 V it gives about +37 / −45 mA cold and its stack
reaches −5 mA. `measurement_range.py` rejects it. A lower value buys
distance from zero at the price of the plateau's visibility: 332 kΩ is the
next step if bring-up finds a unit's sourcing plateau too low, and
anything below 309 kΩ fails the build because a typical unit would no
longer limit before the series resistors do. An independent derivation in the review
arrived at 332…348 kΩ.

**What the choice rests on, and what it does not.** Only the ±30 mA is a
data-sheet limit, and it is stated at 375 mA. The supply and temperature
figures are typical curves at 250–500 mA, and 62.6 mA is below every
setting TI tabulates or plots (the lowest is 100 mA). Carrying them down
as fixed currents is an extrapolation. It was chosen because it is the
pessimistic one for the question that matters, whether the limit can be
zero. If the shifts scaled with the setting instead, the plateau would be
near +61 / −62 mA. TI's macromodel reads 5…12 mA above the equation and has
no temperature dependence (+74 / −67 mA, simulated), so it settles nothing.
TI's support forum could not be opened from the audit environment; search
excerpts from it, not verified, have TI staff applying the ±30 mA as an
absolute current at 30–50 mA settings ("as low as ±20mA and as high as
±80mA" for a ±50 mA setting), one reply scaling it as ±8 % instead, and
users of 390 kΩ and 750 kΩ at ±15 V reporting most units near the equation
value with some well below it. All of this fits the expected range above
and none of it changes the conclusion: in every reading there is an output
in both polarities, the current is measured, and R30 bounds it. The
plateau is not a precision quantity on any part of this family: bring-up
measures it in both polarities and the firmware's `i_plateau` takes the
measured value. To move one unit, change R7: about 6 kΩ per mA. The ILIM
node is high impedance (12.5 µA sets the limit): 1 µA of leakage there
moves the plateau by about 4.6 mA, which is why the node is on the flux
cleaning list.

**What the change costs.** Same seven pins, same package (TI KVT, both data
sheets), same footprint, same net names, no copper change. Parameter by
parameter:

| Parameter | OPA548 (was) | OPA547 (now) | Need here |
|---|---|---|---|
| Output swing, guaranteed | (V+) − 2.4 V, (V−) + 1.3 V at 0.6 A | (V+) − 1.8 V, (V−) + 1.2 V at 0.1 A | ±10.95 V of ±15 V: 2.25 V spare (was 1.65 V) |
| Input common-mode | (V+) − 3 V | (V+) − 3 V | 10.04 V: 1.96 V spare |
| Offset, max / drift | ±10 mV / ±30 µV/°C | ±5 mV / ±25 µV/°C | outside the measurement (Kelvin sense) |
| Bias current, noise, CMRR | −500 nA, 90 nV/√Hz, 80 dB | same | — |
| Open-loop gain (1 kΩ) | 90 dB min | 100 dB min | — |
| Quiescent current | ±17 mA typ, ±20 mA max | ±10 mA typ, ±15 mA max | 0.30 W instead of 0.51 W in U5 |
| E/S pin to V+ through 10 kΩ | allowed | allowed ("Input Shutdown Voltage: V+") | R8 unchanged |
| Disabled-output leakage (typ) | a few mA | ≈ 0.1 mA | E/S is not the safe state either way |
| Tab; θJA, no heat sink | V−; 65 °C/W | V−; 65 °C/W | unchanged |
| GBW / slew rate | 1 MHz / 10 V/µs | 1 MHz / 6 V/µs | lower; set upstream by U3 (0.8 V/µs) |
| Continuous output current | 3 A | 0.5 A | lower; ≤ 0.143 A in the worst fault |
| Specified temperature range | −40…125 °C | −40…85 °C | lower; a bench instrument |
| **Unity-gain overshoot against load capacitance (typ)** | 0 % up to 2.5 nF, 50 % at 11.7 nF | **≈ 10 % at 0 pF, 50 % at 4.5 nF** | **lower, and it matters: see below and §3.7** |

The last row is the one real cost. This stage is a unity-gain follower,
and by TI's typical curves ("Small-Signal Overshoot vs Load Capacitance",
G = +1) the OPA547 has less margin there than the OPA548. As built it does
not bite: U5's direct load is tens of pF (TP2, the −IN pin, tracks), and
the DUT with its cable sits behind F1 + R30 (≥ 102 Ω), which makes it a
resistive load at the frequencies that decide stability. TI's open-loop
plot, taken with a 50 Ω load, reads about 60° of phase margin and 14 dB of
gain margin (typical), and models fitted to TI's curves put this stage at
47…66° and 9…18 dB for any DUT (§4.1). The fast-edge overshoot that
implies is not excited by U3's 0.8 V/µs
and does not reach a settled reading. The consequences are two rules:
nothing capacitive goes directly on VO_RAW or TP2 (use a ×10 probe), and
the output network must be resistive at 1 MHz (§3.7). An earlier version
of this record claimed the OPA547 was the better damped part, from a
macromodel comparison; the macromodels do not reproduce either data
sheet's curve, and the claim is withdrawn.

U5 dissipation: for a shorted DUT the output stage dissipates
(V_S − I·R_path)·I, which is at most V_S²/(4·R_path) = 15.5²/(4 × 121.2) =
0.50 W whatever the limit is; with FORCE+ shorted to AGND (108.2 Ω) it is
0.56 W. Quiescent adds 0.30 W typical, 0.47 W maximum (31 V × 15 mA). That
is at most 0.96 W and a 62 K rise for the shorted DUT, 1.02 W and 66 K for
FORCE+ to AGND, with no heat sink (0.74 W and 48 K at 49 mA, typical).
These bounds are for a passive DUT. A DUT that sources voltage of the
opposite polarity adds its own voltage across U5: near 15 V that is
1.7…2.3 W, which cycles U5's thermal shutdown. Either amplifier would do
the same; energised DUTs are outside the instrument's envelope (§6).

The other parts of the stage were re-checked for the new values: C14
(10 nF across R7) is the optional noise capacitor of SBOS056F Fig. 3, and
the ILIM node's time constant is 0.3 ms; R30 carries 0.24 W at 49 mA and
0.79 W at 89 mA (2 W part); F1/F2 hold 100 mA at 23 °C, 80 mA at 40 °C,
68 mA at 50 °C, so a plateau at the far upper end of its spread can trip
them above about 32 °C, which is benign and self-resetting.

### 3.2 U12 LED string: D7 BZT52C24 → BZT52B24

U12 (TLP176AM) connects FORCE+ only while both ±15 V rails are up. Its LED
is in the string `+15VA → R39 620 Ω → LED → D7 (24 V zener) → −15VA`.
Guaranteed open with a rail absent is unaffected by this audit: the string
then has ≤ 16.0 V across it, below D7's leakage test voltage (18 V for the
Vishay part, IR ≤ 0.1 µA), by data-sheet limits at 25 °C. That figure
assumes the dead rail stays within 0.51 V of AGND, which holds for a
**passive** DUT. A DUT that holds about 9.5 V or more, of the missing
rail's polarity, lifts that rail through the SENSE clamps and can close
the relay (§6); U5 is then powered and in control, so it is benign, but it
is not "open".

**Closed** needs IF ≥ IFT. Toshiba's limit is 3 mA maximum at 25 °C; it
gives none at other temperatures, so 3.3 mA is taken here at 50 °C, where
its typical curve (Fig. 14.1.6) is about 8 % up. The string leaves only
(rails) − V_Z − V_F ≈ 4.7 V for R39, so D7's voltage decides.
`final_corrections.md` §2.5 took D7 at its data-sheet maximum, 25.6 V, and
concluded ±14.45 V. That voltage is measured with 5 ms pulses. In this
circuit D7 dissipates about 140 mW continuously; at R_thJA = 420 K/W its
junction runs about 60 K above ambient, and with a temperature coefficient
of +8…+10 × 10⁻⁴/K its voltage is about 1.3 V higher than the pulsed value.
With that included, at the limits (D7 at its upper voltage limit and upper
coefficient, no fall of V_Z below the 5 mA test current, LED at 1.4 V,
R39 +1 %):

| | BZT52C24, ≤ 25.6 V (was) | **BZT52B24, ±2 % (now)** |
|---|---|---|
| Expected IF at ±15.0 V | 5.6 mA | 5.6 mA |
| Minimum IF at ±15.0 V, 25 °C | 3.3 mA | **4.6 mA** |
| Minimum IF at ±15.0 V, 50 °C | 2.6 mA (below IFT) | **3.9 mA** |
| Closed from, 25 °C (data-sheet limits) | ±14.9 V | **±14.3 V** |
| Closed from, 50 °C (expected: IFT from the typical curve) | ±15.35 V (not at nominal rails) | **±14.75 V** |
| Same, if the board cools D7 30 % worse than the data sheet's test board | ±15.0 V / ±15.5 V | ±14.4 V / ±14.9 V |
| Closed at ±15.0 V up to an ambient of (expected: IFT from the typical curve, extrapolated) | 32 °C | 65 °C |
| Maximum corner, ±15.5 V, 25 °C ambient: IF / D7 / junction | 8.9 mA / 0.22 W / 116 °C | 7.9 mA / 0.20 W / 108 °C |
| Same at 70 °C ambient: junction (limit 150 °C) | 153 °C | 145 °C |

The ±2 % grade has the same nominal voltage and the same SOD-123
footprint, is better at the minimum corner, carries less current at the
maximum corner, and its leakage test point is higher. (The junction
comparison is like for like, at the Vishay data sheet's 420 K/W for both;
the earlier part named no maker. If the board cools D7 better than that
test board the maximum-corner current is higher and the junction cooler:
≤ 8.5 mA and 0.21 W at 300 K/W, against 9.5 mA and 0.23 W before.) The
order code is Vishay **BZT52B24-E3-08** (23.52…24.48 V at 5 mA, Z_Z ≤ 70 Ω,
300 mW on FR-4).

What this does and does not promise:

- The 25 °C row follows from data-sheet limits, with R_thJA the data
  sheet's single figure for its own test board. The 50 °C row leans on a
  typical curve as well. Neither allows for LED ageing; the typical margin
  is large (5.6 mA against a typical trigger current of 1 mA).
- **The margin is in the rails.** At ±15.0 V the relay stays closed up to
  65 °C ambient; at ±14.8 V up to 54 °C; at ±14.5 V only up to 38 °C
  (expected: these use the trigger current from Toshiba's typical curve,
  extrapolated; its limit is given at 25 °C only). The
  bench supplies are therefore to be **set to ±15.0 V ±0.2 V, measured at
  J1** (`bringup_checklist.md` §3).
- Alternatives considered. Lowering R39 buys the minimum corner with D7's
  dissipation, which is what limits this string. A BZT52B22 with R39 =
  910 Ω would hold 5.4 mA at ±15.0 V and 25 °C, 4.9 mA at 50 °C and
  4.0 mA at ±14.5 V and 50 °C, on the same pads; but it halves the
  margin of the guaranteed-open state (a 17 V leakage test point against
  the 16.0 V string) and closes the relay about 2 V earlier on the way up.
  It trades one protection margin for another, so it was not adopted; it is
  recorded here as the option if the rails cannot be held, or if bring-up
  finds U12 itself running above about 50 °C beside R30.
- Toshiba recommends IF ≥ 5 mA and specifies R_ON at 5 mA; the expected
  5.6 mA meets that and the minimum does not, which matters only for R_ON
  (≤ 2 Ω), and U12 is outside both U5's loop and the Kelvin sense.
- D7 runs warm by design (junction ≈ 85 °C at a 25 °C ambient, expected),
  and takes a minute or two, not seconds, to get there.

### 3.3 ADS1256 crystal load capacitors: 22 pF → 18 pF

SBAS288K, Fig. 22 "Crystal Connection": "C1, C2: 5pF to 20pF", with the
text "external capacitors must be provided to ensure start-up and a stable
clock frequency". (It is an annotation on the figure, not a line of the
electrical table.) C26 and C27 were 22 pF, outside that range. They are
now 18 pF, the value of TI's own connection example (Fig. 25), on the same
0603 pads. Less capacitance lowers both the gain the oscillator needs and
the crystal's drive level, so it cannot make start-up worse. The only
other effect is a clock a few tens of ppm faster, which nothing in the
measurement resolves.

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
footprint, value and rating in the design did not change. The **1 %** is
part of the rating here: with the commonly stocked 5 % part (…JA100) the
same envelope gives 2.02 W, and `measurement_range.py` fails it.

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
  U5's value (the whole orderable name, `OPA547T` or `OPA548T`; anything
  else stops the build) and computes the plateau from that part's
  data-sheet figures. It **fails the build** if
  - the worst-case stack (R7 at the top of its tolerance, ±14.5 V rails, an
    85 °C case, the maximum unit tolerance, the weaker polarity) is under
    **5 mA**. The floor covers what the stack leaves out: 1 µA of leakage
    at the ILIM node is 4.6 mA;
  - a **typical** unit (typical tolerance, everything else in its favour)
    would limit above what the series path passes into a shorted DUT, so
    that no plateau would exist;
  - U5's value and its MPN field name different parts;
  - R7 is not fitted (an open ILIM pin programs zero output current).

  "Not fitted" means either a value of `DNP` or the project's `dnp()`
  mark; any other resistor the arithmetic depends on stops the build the
  same way. With the fitted values it reports 8.2…89.2 mA and passes. On
  the OPA547T it accepts R7 from 309 kΩ to 365 kΩ (E96) and rejects values
  outside; on the OPA548T no value of R7 passes with this series path. The
  path used for the typical-unit test has R30, R10 and the sweep at the
  tolerance limits that pass least (78.0 mA). The upper end of the stack is
  reported, not gated (§3.1). It now also prints the measurable ranges
  with the channel gain and offset at their limits (§2). No existing check
  was removed or loosened; the passive fault envelope is checked as before
  and does not depend on the plateau.
- `tools/author_project_symbols.py` cross-checks every U5 pin against the
  OPA547's own data sheet as well: the "Pin Configurations" drawing of
  SBOS056F, committed verbatim as
  `libraries/provenance/opa547_sbos056f_pin_configurations.txt`, must put
  the same function on every lead as the OPA548 "Pin Functions" table the
  symbol was already checked against. The parser was tested against
  altered excerpts (swapped labels, disagreeing packages, a missing row).
- `tools/test_guard_fails.sh` has seven injections for the gate and
  requires the range check and `main.py` to fail on each: 34, U5 back to an
  OPA548T with 1.43 MΩ; 35, R7 = 412 kΩ (above zero only with a nominal
  resistor and nominal rails); 36, R7 = 200 kΩ (no plateau for a typical
  unit); 37, U5's order code left at OPA548T; 38, R7 = 374 kΩ (above zero
  but under the 5 mA floor); 39, R7 marked `dnp()`; 40, U5 named by family
  only.

### 3.7 U5 output R-C network: R38 10 Ω → 100 Ω, R38 and C49 fitted

**Where it stood.** SBOS056F: "if the OPA547 is intended to be driven into
current limit, a R/C network may be required", with 3 Ω + 0.01 µF as the
example (Fig. 11). The first audit (V1) added pads for R38 + C49 from
VO_RAW to AGND and left them unfitted, 10 Ω + 10 nF to be fitted if
bring-up saw oscillation on the plateau with a capacitive load. This
audit at first left that alone. The review showed two things wrong with
it.

**The plateau with an inductive DUT.** In TI's OPA547 macromodel the
output in current limit has a negative-real, capacitive impedance from
about 25 kHz to 1 MHz (some hundreds of ohms in the positive polarity,
kilohms in the negative). An inductive DUT series-resonates with it, and
where the negative resistance exceeds the 115…130 Ω of the series path the
plateau oscillates. On the board's network with no R-C fitted and only
the 400 pF of D5 and the jacks at the output (simulated, §4.1):

| DUT (in series with 3 Ω) | +10 V command | −10 V command |
|---|---|---|
| 10 µH | quiet | quiet (review run) |
| 1 mH | 2.8 mA p-p; 1.9 V p-p at U5 OUT | 88 mA p-p; 30 V p-p at U5 OUT (rail to rail) |
| 10 mH | 0.8 mA p-p (review run) | 2.6 mA p-p; 12 V p-p |
| 100 mH | quiet | quiet (review run) |

An independent run in the review mapped it as roughly 30 µH to 10 mH. The
readings are then meaningless and, in the negative polarity, the DUT
current swings through zero. The bring-up step that was to decide the
question put capacitors across the jacks; in the model 10 nF there
suppresses the oscillation, so that test would have passed while a winding
or a coil on the same board failed. It is a model result: the limiter's
dynamics in silicon are not characterised by TI, and the instrument's
stated DUTs are diodes and other two-terminal devices, not coils. But it
agrees with TI's advice, a board cannot be asked afterwards, and the cure
costs two fitted parts on pads that are there.

**The values.** TI's 3 Ω + 10 nF is drawn on an inverting gain-of-4 stage.
This stage has unity gain, where the OPA547 has least margin (§3.1): 10 nF
is 16 Ω at the 1 MHz crossover, so behind 3 Ω or 10 Ω it is still a
capacitive load, and TI's own curve puts 4.5 nF at 50 % overshoot. In
every model of the amplifier fitted to TI's curves (§4.1) the 10 Ω + 10 nF
that was on the pads costs 10…27° of phase margin, and 3 Ω + 10 nF costs
20…40°, leaving as little as 2…3 dB of gain margin; in one of the fitted
models the follower oscillates with it. The cure prescribed for the
plateau would have made the linear region, where the instrument does its
work, ring.

**The change.** R38 is **100 Ω** and both parts are **fitted**.

| Question | Answer |
|---|---|
| Does it cure the steady plateau? | In the model, yes: quiet in all 139 cases run with it at ±10 V. Inductive DUT 10 µH…100 mH with 0.3, 3 and 20 Ω in series, 100 pF and 10 nF at the jacks, both polarities (91 cases); resistive and capacitive DUTs, 0.01 Ω…1 kΩ, 100 pF…1 µF, slow and step entry (48 cases). A reviewer's own testbench was quiet in 181 further cases (1 µH…1 H, winding capacitance, cable and coax loads, R38 90…110 Ω, C49 5…15 nF, rails ±14.5…15.5 V, R7 ±1 %), and with the network the impedance seen from the jacks on the plateau is passive at every frequency (least real part +17 Ω; −150 Ω and −3.8 kΩ without). 47 Ω and 220 Ω, and 22 nF with 100 Ω, were quiet where run |
| How much margin does C49 have? | Not much on the low side: with 4.7…5 nF the stage oscillates (0.3 V p-p near 140 kHz) just below the negative entry with a 1…2 mH DUT; 6 nF and above is clean. A ±10 % X7R can be near 7 nF once tolerance, temperature, bias and ageing are stacked, which is closer to that edge than a model of this quality justifies. **C49 is therefore specified as C0G, ±5 %** (10 nF / 50 V C0G is a standard 0805 part; it stays at 9.5 nF or more). Value and pads unchanged |
| **What it does not cure (model)** | (1) *Entry.* In a window about 20 mV wide at the knee where the positive limit takes over, U5's output carries about 0.3 V p-p near 0.5 MHz and the DUT 2 mA p-p, even into a short (0.4 V and 3 mA over 70 mV without the network). It does not reach a settled reading. (2) ***Exit from the negative limit with an inductive DUT.*** If the command falls fast from the negative limit (0.4 V/µs or faster; U3 gives 0.8 V/µs) with 0.3…10 mH in the DUT, U5 is left oscillating, 4…14 V p-p at 150…230 kHz, with about −60 mA mean still in the DUT although the command is at 0 V (rail to rail without the network). No R-C value tried removes it, TI's 3 Ω + 10 nF included. A slow exit (0.1 V/µs or less) does not do it, nor does the positive polarity, a resistive or capacitive DUT, 10 µH or 100 mH. Both reproduced in this audit's own runs (§4.1) |
| Does it hurt the linear region? | Behind 100 Ω the network is a resistor where stability is decided: 100 Ω in parallel with the ≥ 112 Ω DUT path is 53 Ω, the load TI itself characterises the part with (R_L = 50 Ω for gain-bandwidth, slew rate and the open-loop gain/phase plot). That argument does not depend on a model. In every model tried (two independent sets, §4.1) the fitted network moves the phase margin by a few degrees either way at nominal values and never lowers the worst case over DUTs; where it costs more (up to 15° at the extremes of the parameters swept) the unfitted margin was 70° or more and the fitted one stays at 55° or more |
| Does anything in the measurement see it? | No. C49 passes no direct current, and its alternating current returns to AGND, not through the shunt. On a full-scale slew it takes 8 mA for 25 µs. It also takes the edge off the model's overshoot on a fast entry into limit (−88 mA peak instead of −99 mA) |
| What can it cost? | Two more parts that can fail. C49 shorted would load U5 with 100 Ω to AGND (output pulled down, R38 at 0.2…1 W in an 0805): an obvious fault, no hazard to a DUT. A 0.25…0.5 W pulse-rated 0805 for R38 is a free precaution (§6) |

**What the exit result means.** Every commanded move of the output is a
ramp (the firmware moves it at 10 V/s at most), so normal operation never
makes that edge, and in the model a ramp out of the limit is clean. The
paths that write zero at once do make it: the firmware's own current
cut-off, its fault handlers and dead-man, and the hardware safe state
itself (heartbeat lost, reference gated off). For those, with an inductive
DUT held in *negative* current limit, the model says the output is not
brought to zero. That is a failure of the safe state, predicted by a
model whose limiter is an idealisation, for a load the instrument is not
specified for (its stated DUTs are diodes and other two-terminal devices,
and a metre of lead is about 1 µH). Whether the OPA548 would have done the
same nobody can say: its model never limits at ±10 V. It is recorded
here as it stands:

- **Operating restriction** (§6): an inductive DUT is not to be taken into
  current limit until the bring-up test of this exit has passed on the
  board.
- **Bring-up tests it directly** (`bringup_checklist.md` §7), with the way
  out of the latched state (re-enter the limit and ramp out, which
  recovers it in the model, or switch off ±15 V).
- **A remedy that needs no copper exists** if silicon does it: a 10 nF C0G
  capacitor on top of each of R4 and R6 (the sweep amplifier's 20 kΩ
  pair) makes the sweep amplifier a first-order low-pass with a 200 µs
  time constant on both inputs alike, so a 10 V fall of V_SWEEP is no
  faster than 0.05 V/µs, against the 50 ms the safe state takes anyway.
  With the command slowed like that the model leaves the limit cleanly in
  the five cases run (0.3, 1, 3 and 10 mH to 0 V; 1 mH + 20 Ω stepped to
  −5 V). It is not fitted now: it has no footprint, nothing shows that
  silicon needs it, and it is not free. Each sweep point would need its
  settle time raised from the default 1 ms (five of those time constants)
  to 2…3 ms.

This is model and data-sheet evidence, not a measurement. Bring-up checks
all of it: U5's margin in the linear region, the plateau with resistive,
capacitive and inductive loads in both polarities, the entry, and the
fast exit (`bringup_checklist.md` §7). Removing R38 returns the stage to
the unfitted state. **Do not lower R38 below about 47 Ω, and do not fit
TI's 3 Ω here.**

### 3.8 C17/C18: an order code for a part that exists

C17 and C18 are U5's local supply reservoirs: "10 µF / 35 V" tantalum on
`CP_EIA-3528-21_Kemet-B`, order code "generic". No manganese-dioxide
tantalum of that rating is made in that case. KEMET T491: case B at 35 V
stops at 4.7 µF, and 10 µF / 35 V is case C (6032) and larger. Kyocera AVX
TAJ: the same (TAJB475\*035 is the largest B; 10 µF / 35 V is TAJC and
up). A C-case part is 6.0 mm long and does not fit the 4.4 mm pad span.
The package table in `silkscreen_and_packages.md` chose the case from the
voltage derating and was never checked against a maker's case table. A
buyer told "10 µF / 35 V tantalum, 3528" finds nothing, and the nearest
things that do fit are 10 µF at 25 V or 16 V: an MnO2 tantalum at 62 % or
97 % of its rating on a low-impedance 15 V supply rail, well beyond the
50 % its makers recommend there, and the classic way for one to fail
short.

The rating does exist in case B as a **polymer** tantalum. The order code
is now Kyocera AVX **TCJB106M035R0200E** (TCJ data sheet, ratings table:
case B, 3.5 × 2.8 × 1.9 mm, 10 µF, 35 V, ESR ≤ 0.2 Ω at 100 kHz, DCL ≤
35 µA, 125 °C, product category 0: recommended up to 80 % of rated
voltage up to 105 °C for parts of 16 V and above). Value, voltage rating,
pads and polarity mark are unchanged.
KEMET's T521B106M035ATE200 is listed by distributors as the same rating in
the same case; its data sheet could not be fetched, so it is named as an
alternative to check, not as verified.

What changes electrically is the ESR, 0.2 Ω instead of the 2–3 Ω of an
MnO2 part, which is the direction a supply bypass wants. The routed
supply track from the 100 µF bulk capacitor to U5 is 90 mm on +15VA and
102…110 mm on −15VA, all 0.20 mm wide: 0.22…0.27 Ω and about 75…110 nH.
With that and the bulk capacitor behind it, the 10 µF resonates at
160…190 kHz with a characteristic impedance of about 0.1 Ω against at
least 0.5 Ω of series loss: overdamped (Q 0.1…0.3). The impedance at U5's
supply pins from 100 kHz to 5 MHz comes out at 0.15…0.35 Ω, against
0.4…1.2 Ω with a 2–3 Ω MnO2 part; the higher ESR was not doing useful
damping. Hot-plugging a ±15 V supply puts at most 15.5 V / 0.22 Ω through
the part, below the current the maker surge-tests it at. Its leakage
(≤ 35 µA) flows from rail to AGND, outside every measurement. In a
dead-rail state D8/D9 hold the reverse voltage to ≤ 0.51 V, 1.5 % of its
rating against the 15 % (25 °C) to 3 % (125 °C) the maker allows a polymer
part for transients; like any tantalum it is not meant to sit reversed, so
a single-rail state is a fault to end, not a mode to run in.

**How it must be soldered.** The maker's conditions come with the part
(Kyocera AVX, polymer guidelines and technical summary; the review found
them, this record had not):

- **Not with an iron held on it.** "Hand soldering is not recommended due
  to the difficulty to control heat exposure time; for rework, a hot air
  jet is recommended", and the TCJ series is listed as "not dedicated to
  hand soldering". Fit C17 and C18 with solder paste and hot air, or in
  reflow: preheated, peak 260 °C or less.
- **Moisture (MSL 3).** Use within 168 hours of opening the dry pack, or
  dry at 40 °C for 168 hours first. Not a high-temperature bake.

If the board is built with an iron only, the fallback on the same pads is
a **4.7 µF / 35 V MnO2 tantalum in case B** (KEMET T491B475K035AT or
Kyocera AVX TAJB475K035RNJ; both makers' tables list it, and MnO2 parts
may be hand-soldered). It keeps the 35 V rating. It halves the local
reservoir, which at this stage's currents is carried by the 100 µF bulk
capacitor behind 0.22…0.27 Ω in any case. It is the lesser choice, not a
wrong one; a 25 V or 16 V part is wrong.

---

## 4. Simulation and analysis record

### 4.1 Output stage

**TI macromodels** (ngspice 42; OPA547 "Final 1.1", OPA548). The stage as
built: follower with R9 = 10 Ω inside the loop and C19 = 100 pF across it,
R7 ‖ C14 on ILIM, R8 on E/S, ±15 V; load = F1 5 Ω + R30 100 Ω + U12 0.5 Ω
+ D5 300 pF + C_L at the jacks + DUT + F2 5 Ω + R10 10 Ω. Slow transient
ramps were used for the limit (DC sweeps through the limit gave
continuation artefacts).

| Test | Result |
|---|---|
| Limit, OPA548 + 1.43 MΩ, ramp to ±12 V into 120 Ω | No limiting at ±10 V (83 mA, set by the resistors); limit +87 / −94 mA |
| Limit, OPA547 + 348 kΩ, same | Plateau +74.2 / −67.3 mA at ±15 V (+74.9 / −73.0 mA at ±30 V; no change from 25 °C to 85 °C in the model) |
| Plateau, no R-C network, resistive and capacitive DUT: 0.01, 10 and 50 Ω, C_L = 100 pF, 10 nF, 1 µF, both polarities | Quiet |
| Plateau, no R-C network, inductive DUT | Oscillates: §3.7 |
| Plateau with R38 = 100 Ω, C49 = 10 nF: 139 cases (§3.7) | Quiet. Three negative-polarity cases stopped with "timestep too small" at the instant the model's limiter engages and were re-run with a faster entry ramp or looser tolerances; they then settle like the rest. That stiffness is a property of this model's negative limiter, seen with and without the network |
| Held at the positive knee, 8.945 V into a short, with the network | 2.2 mA p-p in the DUT, 0.28 V p-p at U5's output, sustained |
| Held just below the negative entry, −8.25 V, 1.6 mH + 3 Ω: C49 = 4.7 nF; 10 nF | 0.31 V p-p; quiet |
| Exit from the negative plateau at 0.8 V/µs, with the network: 1 mH + 3 Ω to 0 V; 10 mH + 3 Ω to 0 V; 1 mH + 20 Ω to −5 V | Left oscillating: −60 mA mean with 16 mA p-p and 14.3 V p-p at U5's output; −60 mA, 1.7 mA p-p, 14.4 V p-p; −63 mA, 6.5 mA p-p, 8.1 V p-p. Without the network (1 mH): 16.6 V p-p |
| Same exit, positive polarity (1 mH); negative at 0.05 V/µs (1 mH) | Clean: 0 mA, no ripple |
| Same exit with the command passed through a 100 µs or 200 µs low-pass (what capacitors across R4 and R6 would do): 0.3, 1, 3, 10 mH to 0 V | Clean in all four |
| The same low-pass, 1 mH + 20 Ω stepped to −5 V | Clean: −35.6 mA (the resistive value), no ripple. The run did not converge at the standard solver settings and was repeated with two other sets, which agree |
| From the latched state: command back to −10 V, then a 5 ms ramp to 0 V | Back on the steady plateau (−67.2 mA, quiet), then 0 mA, quiet |
| Entry into limit on a 1 µs step into a shorted DUT, with the network | Peaks +83 mA and −88 mA before settling to +74 / −67 mA (−99 mA without the network); none with 1 µF at the jacks. Below the 128 mA passive bound and over in microseconds |

What these models are not good for: the linear region. A plain follower
with a capacitor on its output shows under 1 % overshoot in the OPA547
model at any load, where SBOS056F shows 10 % at 0 pF and 50 % at 4.5 nF;
the OPA548 model shows 38 % at 1 pF where SBOS070D shows none. The
comparison of the two parts' step responses in an earlier version of this
record came from that artefact and is withdrawn. For the same reason the
models' limiter dynamics are not validated either: the inductive-load
result may be milder or worse on silicon.

**Linear region: a model fitted to the data sheet.** Loop gain
`T(s) = ω_u / (s·(1 + s/p₂)) × H_load(s)` with ω_u = 2π × 1 MHz (SBOS056F),
p₂ and the open-loop output resistance R_o fitted to TI's G = +1 overshoot
curve at 0 pF (10 %) and 4.5 nF (50 %): p₂ = 1.40 MHz, R_o = 41 Ω. It
over-reads the middle of the curve (33 % at 2 nF against TI's 22 %), so it
is pessimistic there. H_load is the board's network: R38/C49, R9, 30 pF at
VO_SENSE, F1 + R30, 400 pF at the jacks, the DUT, F2 + R10. Phase margin:

| Network at VO_RAW | DUT open | 1 kΩ | short | 1 µF | 10 nF |
|---|---:|---:|---:|---:|---:|
| none (as built until now) | 53° | 55° | 65° | 65° | 62° |
| 3 Ω + 10 nF (TI Fig. 11) | 18° | 20° | 31° | 31° | 28° |
| 10 Ω + 10 nF (the DNP values) | 27° | 29° | 38° | 38° | 35° |
| 47 Ω + 10 nF | 52° | 54° | 59° | 59° | 56° |
| **100 Ω + 10 nF (fitted)** | **58°** | **59°** | **66°** | **66°** | **62°** |
| 220 Ω + 10 nF | 57° | 58° | 67° | 67° | 63° |

With R_o halved or doubled, or with an amplifier fitted to 20 % overshoot
at 0 pF instead of 10 %, 3 Ω, 10 Ω and 47 Ω stay below the 100 Ω, 220 Ω
and unfitted cases in that order, and the fitted network stays within 2°
of the unfitted stage or better; with R_o four times the
fitted value it costs 7° for a shorted DUT (and gains 14° for an open
one). This is a two-pole model of a typical curve: it ranks the options,
it does not predict the board, and it cannot give a gain margin.

**Checked with other models (review).** A reviewer rebuilt the analysis
from TI's open-loop gain and phase plot (SBOS056F, R_L = 50 Ω, typical,
±30 V), which reads 0 dB at 0.9 MHz with 61° of phase margin and −180° at
4 MHz with 13.8 dB of gain margin, and from both overshoot curves (G = +1
and G = −1), with resistive and inductive output impedances and extra
phase, and swept the uncertain parameters widely (about 1200 draws per
model, ten DUTs):

| | Result across those models |
|---|---|
| Stage with 100 Ω + 10 nF | Phase margin 47…66°, gain margin 9…18 dB. No draw unstable |
| Change against no network, nominal values | Phase margin −2…+6°, gain margin −1…+1.5 dB |
| Largest loss anywhere in the sweeps | 15°, where the unfitted margin is 84° and the fitted one still 69° |
| 10 Ω + 10 nF | Costs 11…27° |
| 3 Ω + 10 nF | Costs 21…39°; gain margin down to 2…3.5 dB; unstable in one fitted model |
| 47 Ω; 220 Ω; 100 Ω with 4.7 nF or 22 nF | Within about −6…+6° of no network |

No model reproduces TI's G = −1 curve closely, and all of this is typical
data at ±30 V. The conclusion that does not rest on any model is the one
in §3.7: with the network fitted, U5 sees at its crossover about the 50 Ω
resistive load that TI's own plot was taken with.

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
1.87 V worst case at 1 kHz). No change. Two qualifications from the
review are in §6: the single-edge corner with ±20 % capacitors and their
temperature coefficient, and the rate below which the gate lets go.

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

The solve also shows where the drop is (review). R31, the DAC's ground pin
and R4's ground end sit on a small island on the R31 side of about 13 of
the 15.5 mΩ, while the reference, the ADC and the measurement amplifiers
sit together on the other side. The measurement is untouched by that: all
its references hang on VREF_2V5 and one ground. The *programmed* voltage
is not: it moves with the DUT current by up to 12.6 mV (at −10 V and
−100 mA), a source resistance of 0.01…0.13 Ω ahead of at least 115 Ω. The
DUT voltage is measured, not assumed, so nothing sees it; firmware should
simply not treat the programmed value as exact to better than about 15 mV
under load.

---

## 5. The board

**Nothing on the board moved.** `git diff` of `stage1_iv.kicad_pcb` against
the base commit is eighteen lines: the `Value`, `MPN` or `Description`
properties of U5, R7, C26, C27, D7, R30 and R38, the `MPN` of C17 and C18,
the `Value` of C47, C48 and C49 (which now states their tolerance and,
for C49, its dielectric), and
the attribute line of R38 and of C49, which loses `exclude_from_bom dnp`
because the two parts are now fitted. All of it is text on the
fabrication layers, hidden fields and assembly flags. Read back with
KiCad's own loader and compared object by object with the board at the
base commit: 897 track segments, 113 vias, 415 pads, 135 footprints
(position, rotation, side), both zones, every footprint graphic and every
board drawing are identical. `layout/*.layout` and `layout/*.eco` are
untouched (the ECO that placed R38 and C49 still calls them DNP in its
`reason` text, and `layout/README.md` calls them "the DNP snubber pads";
that text is a record of why the pads were added and is not read by
anything).

**Fabrication release.** The committed board stores zone *definitions*
without fills (`generators/routed_pcb.py`; a deliberate choice, so that the
file is reproducible). Plotted as it is, the copper layers contain **no
AGND or DGND pour**. Checked on this board with KiCad 10.0.8: a Gerber
export without a refill writes two copper files with no filled region at
all; with `--check-zones` it writes 18 regions on F.Cu and 7 on B.Cu. For
a board made once, the release steps are therefore (each command was run
on this board):

1. On a **copy** of the board (the command rewrites the file):
   `kicad-cli pcb drc --refill-zones --save-board --schematic-parity copy.kicad_pcb`
   and confirm 0 errors, 0 unconnected.
2. Plot, from the committed board, the nine layers a fabricator needs:
   `kicad-cli pcb export gerbers --check-zones -l F.Cu,B.Cu,F.Mask,B.Mask,F.Paste,B.Paste,F.SilkS,B.SilkS,Edge.Cuts -o gerbers/ stage1_iv.kicad_pcb`
   (`--check-zones` refills in memory and leaves the file as it is; in the
   GUI, leave "Check zone fills before plotting" ticked. Without `-l` the
   export writes twenty layers, most of them of no use to a fabricator.)
3. Drill files, plated and non-plated separately:
   `kicad-cli pcb export drill --format excellon --excellon-separate-th --generate-map -o gerbers/ stage1_iv.kicad_pcb`
   The PTH file has 13 tools (0.30…1.40 mm, the four 6.1 mm jack holes and
   four routed 0.6 mm slots at J3); the NPTH file has the three 3.2 mm
   mounting holes and nothing else. Without `--excellon-separate-th` both
   land in one mixed file and the mounting holes may come back plated.
4. **Look at both copper layers in a Gerber viewer and see the pours**
   before sending anything.

`stage1_iv.kicad_pcb` in the repository root is the only board to send.
`unipolar-routed/diodes.kicad_pcb` is the older unipolar board, kept as
the routing reference. A bill of materials and a position file export
cleanly (`kicad-cli sch export bom`, `kicad-cli pcb export pos`); TP1–TP8
appear in both and are bare pads, not parts.

---

## 6. Observations left as they are

None of these is an error in the hardware; each was checked and judged not
to justify a change, least of all a routing change. Where a row says
*corrected*, a comment or a document said something else and now does not.

**The board as routed**

| Observation | Assessment |
|---|---|
| ±15 V rails are routed at 0.20 mm | Adequate: ≤ 0.15 A, tens of mV of drop, negligible heating |
| The AGND returns of the ±15 V bulk capacitors C5 and C6 (60 and 48 mm) and of the crystal capacitors C26/C27 (22 mm) are one 0.20 mm track, and it enters the AGND copper at the small top-side island that carries U8's AGND and VREFN pins; that island joins the main pour through about 6 mΩ (same ground solve as §4.4) | Functional, and no DC error: none of these returns carries direct current. Ripple and clock-rate currents on that track move the ADC's reference return by microvolts, and the ADC's own ≈ 10 mA of analog supply current holds it about 65 µV (26 ppm of the reference) from the reference's ground, a constant that calibration removes. C5/C6 sit 0.12–0.15 Ω further from their loads than they ideally would; every amplifier has its own 10 µF and 100 nF. It is the routed layout; not worth touching |
| R10's return to R31 is 15.5 mΩ of pour (6.1 mΩ on the original routed board, before the ECOs) | No measurable effect (§4.4) |
| REF5025: C9 is a 4.7 µF ceramic (milliohms of ESR) and TRIM/NR is left open | Stable: SBOS410O asks for 1…50 µF with ESR ≤ 1.5 Ω (*corrected*: the design document said 1…100 µF). The 1–1.5 Ω ESR and the NR capacitor it also suggests are optional noise measures that would need new pads |
| R30's body runs about 1 mm from U12 along its length; U12's trigger current rises with temperature | R30 is at +18 K at 49 mA and +59 K at an 89 mA plateau. Assembly: raise R30's body the full 3 mm and lean it away from U12. Bring-up measures U12's temperature on the plateau; above about 50 °C the remedy is the BZT52B22 + 910 Ω option of §3.2 |
| U6 +IN is on the FORCE− jack node with only F2 between, and by design nothing across the shunt | An electrostatic discharge into J7 or its lead develops its current across R10 (10 Ω) at an input rated ±40 V and 2 kV HBM. Handling rule: touch AGND or the chassis before the FORCE− lead. Nothing is to be added across R10 |
| +15VA runs on the bottom layer between the two pads of R18 (0603, the AIN3 filter resistor), 0.33 mm from each | Passes the 0.30 mm rule and is electrically harmless. A solder bridge there would put +15 V on an ADC input. Inspect R18 after soldering and measure +15VA to R18's pads as open before first power |
| The two ground pours connect to pads solidly, through-hole ground pins included | Not a fabrication defect. J1.4, J2.3, SW1.2, R10.2, U6.2 and J3's shell need a larger tip and a longer dwell; R10 and U6 pin 2 are where a cold joint would matter |
| A heat sink cannot be fitted to U5 as placed: C18 is about 1 mm behind the tab and C7 beside it; the tab is at −15 V | None is needed (§3.1). Recorded so that nobody plans on one |
| C19 and R8 lie on the bottom between U5's two rows of leads; R9 and C16 are under its body | Assembly order: bottom-side parts first, then U5, trimming its leads with care |
| U5's leads are 0.66–0.91 × 0.41–0.56 mm (KVT drawing): 1.07 mm across the diagonal at most, in 1.2 mm holes | Fits with 0.13 mm to spare at worst case. TI's own suggested 1.02 mm hole would not |
| `manifest.yaml` does not parse as strict YAML (its `validation` section) | Pre-existing; nothing reads it by machine. Left |

**Buying the parts**

| Observation | Assessment |
|---|---|
| U5 | **OPA547T** (KVT, stagger-formed). Not the OPA547T-1 (straight leads), not the OPA547F (DDPAK), not an OPA548T. Active at TI; TI's own store showed no stock when the review looked. **Confirm distributor stock before ordering the board** |
| Stock in general | Nobody has checked current stock of the parts bought by code: OPA547T, PR02000201000FA100, RS02B10R00BE12 (R10), BZT52B24-E3-08, TCJB106M035R0200E, and a 7.68 MHz HC-49/US SMD crystal. Do it before the board is ordered, not after |
| R30 | PR02000201000FA100: copper leads, 1 %. Not the 5 % …JA100 that distributors mostly stock (§3.4). One distributor listing was found for the 1 % code and it is a 1000-piece pack: confirm supply before ordering the board |
| D7 | BZT52B24, the ±2 % grade (§3.2) |
| C17, C18 | Polymer tantalum, by order code; soldered with paste and hot air or reflow, not with an iron; from a dry pack. Iron-only fallback: 4.7 µF / 35 V MnO2, case B (§3.8) |
| LED1 is "GREEN, generic" | It is fed from 3.3 V through 1 kΩ and PC13 (3 mA at most): buy a yellow-green of about 570 nm with V_F ≈ 2 V. An InGaN "true green" (525 nm, V_F about 3 V) stays dark |
| R9 (10 Ω 1 % 1206, 0.25 W) carries 0.21 W in the worst rail fault, 8.5 mm from U5 | Passes its rating, which is given at 70 °C; at a local 85 °C the usual derating leaves about 0.21 W, i.e. no margin, in a fault that F1 need not end. **A 0.5 W 1206 (high-power thick film) is recommended**; same pads. Left as a recommendation, not a change: the build's rule (rating ≥ fault dissipation) is met |
| C19 (100 pF across R9) | Electrically inert here (it parallels 10 Ω); any 100 pF part, which will be C0G anyway |
| R8 (10 kΩ 0603, E/S to +15 V) | A 0.1 W part: if U5's E/S pin sits near V− (its thermal-shutdown status state) R8 sees the full 30 V, 96 mW at most. SBOS056F does not say what the pin does when pulled to V+ (it gives "typically 3.5 V above the negative rail" for the open pin), so R8 may carry 70…80 mW all the time, between U5's lead rows. Bring-up measures the voltage across it; if it is more than a few volts, fit 100 kΩ (the pin only has to stay above V− + 2.4 V) on the same pads |
| Ceramic capacitors with no voltage in their value | None of them touches ±15 V (those all say 50 V). X7R, 16 V or more (X5R will do on the logic rails), C42 on VBUS 25 V; never Y5V/Z5U |
| R31 (0 Ω 0805) | A real thick-film jumper: it carries the whole analog return |
| R38 (100 Ω 0805) | Dissipates nothing in normal use. A 0.25…0.5 W pulse-rated 0805 costs nothing and survives longer if C49 ever fails short; optional |
| Y1 (7.68 MHz) and Y2 (8 MHz) have no order code | Fundamental-mode AT-cut, HC-49/US SMD, ESR ≤ 80 Ω (100 Ω at most), load capacitance 12…18 pF. The board presents 11…14 pF to each. Not an overtone part |
| C26/C27 (18 pF), C32/C33 (20 pF), C24/C25 (ADC input filter, 1 nF) name no dielectric | C0G/NP0. For C24/C25 an X7R would work at DC but adds voltage-dependent settling |
| C47 (100 nF) and C48 (470 nF) of the heartbeat pump | ±10 % X7R, 16 V or more (their value strings now say so). Only the ratio C47(max)/C48(min) matters: with ±20 % parts, the X7R temperature coefficient and a +2 % rail, a single edge on PC5 lifts DAC_ARM to 0.87–0.89 V, at the gate's 0.87 V guaranteed-off limit; with ±10 % parts it is 0.76 V (review simulation) |
| R1–R6, R11–R13, R32, R33 are 0.1 % with no stated TCR | ≤ 25 ppm/°C; the ratio pairs from one reel. Already review item R10 of the design document |
| The 4 mm jacks have no part number; the footprint is a 6.1 mm plated hole in a 10.16 mm pad | A jack with a ≤ 6 mm stud. **Whatever metal bears on the board must stay inside the pad: 10 mm across at most.** The AGND pour begins 5.38 mm from each jack's centre on both faces, under solder mask only, so a wider nut, flange or washer (an M6 washer is 12 mm, an M6 nut about 11.5 mm across its corners) sits on mask over ground, and mask under a tightened nut is not insulation. Otherwise put an insulating washer of 12 mm or more under it on each face and solder the stud or tag to the pad. (*corrected*: this record first said "under about 12 mm", from the spacing alone.) The thread must be M6 (a 1/4-32 jack does not pass the 6.1 mm plated hole, which must not be opened up), and the jack needs metal that reaches the pad: the footprint's only connection is the plated hole and its land, so an all-insulated jack with a rear solder tag connects to nothing unless the tag is soldered to the pad. Hirschmann's BU 10 / BUG 10 are of the right kind (M6 × 0.75 metal body; distributor data, drawing not checked). **No drawing of an actual jack has been read by anyone, and an ordinary M6 nut is already wider than the pad. Before the board is ordered, take the drawing of the one jack that will be used and settle the mounting stack from it: what bears on each face, how wide it is, and where the electrical joint to the pad is made.** If no jack fits inside the pad, the insulating-washer stack is the answer and the joint is a soldered tag or wire to the pad's rim; that can be decided after the board exists, since it changes nothing on it. A metal front panel under uninsulated jack bodies shorts all four terminals. Spacing: adjacent jacks are 13.75, 15.0 and 17.25 mm apart, H1 is 10.6 mm from J7 and the nearest F1/F2 pads are 8.9–9.1 mm from J5/J7. Not the 19 mm of a dual-banana plug; single leads only. Bring-up measures each jack to AGND before power |
| F1/F2: Bel's leads are offset 2.2 mm front to back, the footprint's holes 1.2 mm | Ø0.51 mm leads in 1.0 mm holes: a slight lead dress at assembly |
| R30: 10.0 mm maximum body on a 12.7 mm pitch | Fits; the bends start within a fraction of a millimetre of the end caps. Hold the lead between cap and bend when forming it |

**What the instrument is and is not built for**

| Observation | Assessment |
|---|---|
| The sweep's end points are ±10.040 V nominal, not guaranteed | The DAC8552's own limits (zero-code error ±12 mV, full-scale error ±0.5 % of range, per channel) times the gain of 8, with the 0.1 % resistors and reference, put the guaranteed reach at about ±9.85 V; about ±10.0 V is expected. A unit with a negative zero-code error also has a dead band of up to 96 mV at the negative end. Data stay correct, since V and I are measured, not assumed. Firmware should clamp to the measured span. No hardware change: it would move the documented range |
| **An energised DUT** (a battery, a charged capacitor, a powered circuit) | The stated DUTs are passive. D1–D4 connect the SENSE jacks straight to the ±15 V rails, ahead of R12/R13. With the board unpowered, a DUT that holds voltage charges the rail capacitors through them and then feeds the analog section; U12 being open does not help, because the path is SENSE, not FORCE+ (*corrected*: the fault table said the clamps were referenced to rails held near 0 V). The current is limited only by F2 + R10, and by nothing if the source shares ground with the board: a stiff 9–12 V source connected to an unpowered board puts amperes through a 1N4148W for hundreds of microseconds, several times its 1 A / 2 A surge ratings (review simulations), and such a diode fails short, which would put a rail on a SENSE jack at the next power-up. With one rail live, a DUT of about 9.5 V or more can also close U12 (§3.2). **Rule: power the board before connecting anything that holds voltage, disconnect it before power-off, keep the DUT floating, never above ±15 V.** Leaving D1–D4 unfitted would remove the path (R12/R13 already keep U7 inside its ratings) at the cost of their shunting of a discharge ahead of the thin-film dividers; not adopted, the stated DUTs being passive. Bring-up checks the four diodes |
| An external source above the rails on any jack | Outside the envelope (±15.5 V). On FORCE+ nothing on the board limits D5's current: F1, R30 and U12 are on the amplifier side of it (*corrected*: the fault table said F1 limited it). D5 holds about 1.25 W continuously. On a SENSE jack the clamps lift the rail; above about 21 V at the jack the ±15 V parts see more than their 36 V. On J7 the limits are 15.9 V for R10's short-time rating and 26 V for F2 |
| **An inductive DUT taken into current limit** | §3.7. In TI's model the steady plateau is quiet with the network fitted, but a fast fall of the command out of the *negative* limit with 0.3…10 mH in the DUT leaves U5 oscillating with about 60 mA still flowing, and the firmware's immediate-zero reactions and the hardware safe state make that edge. **Rule: until the bring-up test of that exit has passed on the board, do not take an inductive DUT (a coil, a relay, a motor, a transformer winding, a long coiled lead) into current limit.** The stated DUTs are not inductive. If silicon does it, the remedy needs no copper (§3.7) |
| A DUT that sources voltage of the opposite polarity near the rail voltage | Cycles U5's thermal shutdown (§3.1) |
| The ADS1256 inputs in partial-supply states | AIN0/AIN2 are rated ±10 mA continuous beyond the rails, and only R15/R17 (100 Ω) and the INA128's own output limit (+6 / −15 mA typical) stand between. With −15 V alone and +5 V on, or with ±15 V on, +5 V off and a DUT driving the inputs negative, the current can reach about 15 mA. TI does not specify what an INA128 does with one rail missing. Already the design document's review item R15. Bring-up measures it and keeps those states short; if it finds more than 10 mA the remedy is 1.5 kΩ in R15 and R17, same pads (it adds to the ADC's source resistance, so not fitted without that cause). In use the two ±15 V rails come up and go down together |
| +5 V collapsing first while the output is armed | U12 interlocks only the ±15 V rails, and the gate stays armed for 17…79 ms after the MCU stops. In that time the DAC and the reference pass through their unspecified supply region with U12 closed; a positive excursion of a volt or two at the DUT for tens of milliseconds is plausible (review reasoning, not simulated; bounded by 8 × the falling +5 V). **Rule: `DISARM` before switching off, and switch ±15 V off first or all three rails together.** The other orders are benign with a passive DUT |
| J1: the +5 V terminal has no protection and is next to −15 V | AVDD of the ADC, the DAC and the gate is 6 V absolute maximum: a ±15 V lead on pin 3 destroys them whatever the current limit. Swapped ±15 V leads are held by D8/D9 only while the supply limit is ≤ 0.5 A. Bring-up measures the harness before it is connected |
| J2 pin 4 is the +3V3D rail itself | Connect it only to a probe's target-voltage *sense* input. A probe that sources 3.3 V there back-powers the board and drives the DAC's logic inputs with its supply absent. Program with the bench +5 V on |
| Arming steps the DAC reference from 0 to 2.5 V in under a microsecond | The two DAC channels slew to different targets, so V_SWEEP can show a spike of about +1 V for a few microseconds at each arming (review estimate, behavioural model), and the slow decay of DAC_ARM through the gate's threshold at disarm may chatter it. Neither exceeds what the held code already applied. Bring-up scopes both. If it matters for a DUT the remedy is in firmware (arm with both channels at zero) |
| The gate detects a stopped heartbeat, not a slow one | It stays armed down to about 110 Hz and is undefined from about 25 to 110 Hz. That follows from the same 47 ms that gives the 50 ms disarm, and the firmware has its own 2 ms dead-man; it should not be described as a rate monitor |
| D7 junction ≈ 85 °C at 25 °C ambient; at ±15.5 V, 70 °C ambient and the lowest zener voltage it reaches 145 °C of 150 °C | Inside ratings (§3.2). Not an instrument for a 70 °C ambient |
| U12's minimum LED current (3.9–4.6 mA) is below Toshiba's recommended 5 mA | §3.2: closure holds; only R_ON's specification point is missed, outside the measurement |
| The plateau differs by polarity and sags as U5 warms | Inherent in this amplifier family (§3.1); measured at bring-up |
| The Kelvin channel's common-mode error | SENSE− sits at I × (R10 + R_F2), not I × R10: F2 (3.0…7.5 Ω) is between FORCE− and the shunt. With 0.1 % dividers the worst case is 5.2 mV at the 92.6 mA bound, not 3 mV, and up to 0.035 % of the current bypasses the shunt through the SENSE− divider (0.026 % with F2 at its minimum), not 0.02 % (*corrected* in `blocks/voltage_sense.py`). F2 is a PTC, so it calibrates out only approximately. Bring-up measures it |
| With the buffer off the ADS1256 input is 150 kΩ/PGA (4.7 kΩ at PGA 32 and 64) behind 100 Ω in one leg only | A gain term per PGA setting, and a zero term that moves with the +5 V supply: up to 96 µV (4 µA) at PGA 2 over AVDD 4.75…5.25 V, 1.6 mV at PGA 64 (§7). U6's own low-frequency noise, about 60 nA rms in 0.1–10 Hz (review estimate from SBOS051G's typical curve), is the current channel's floor, not the ADC |

Sources that could not be reached from the audit environment: ST's site
(STM32F411 and USBLC6-2 data sheets; a mirror copy of DS10314 was used),
the Nexperia BZT52 data sheet (the Vishay one was used and its part ordered
by number), KEMET's T521 data sheet, TI's E2E forum.

Not checked by anyone, and worth knowing: no drawing of an actual 4 mm
jack has been read; the library footprints of the parts this audit did not
change (the USB-C receptacle, the J1 terminal block, SW1, the crystals,
the electrolytic cans) were taken from KiCad's library, not compared with
makers' drawings; the polarity marks of the bottom-side diodes D7 and D9
were not looked at (their pad nets are right, and bring-up tests both with
a meter before power); the LD1117's stability with its mixed output
capacitors rests on ST's "10 µF minimum" alone. For a future board, TI's
OPA593 specifies a programmable limit of ±25…±250 mA, which would cover
49 mA inside its range; it comes in a WSON-12 only (figures from a search
result, data sheet not read).

---

## 7. For the firmware work

Hardware facts only; no firmware file was changed.

- `firmware/src/core/nominal.h`, `firmware/sim/model_board.c`,
  `host/stage1iv/sim.py` and `docs/firmware/firmware_spec.md` (H13) still
  say OPA548 and 1.43 MΩ in comments. The nominal they use, 49 mA, is still
  the design centre; the expected plateau is 40…65 mA.
- The plateau is polarity-dependent (sinking up to about 8 mA above
  sourcing) and falls by ≈ 0.2 mA/K as U5 warms, so it sags by several mA
  during a long hold. A threshold of 0.8 × the smaller measured polarity,
  as now, copes with that; a symmetric, constant plateau should not be
  assumed. On a fast step into limit the first samples can overshoot the
  plateau by 10–20 mA for microseconds.
- The `i_plateau` calibration window is 20…100 mA. A unit below 20 mA is
  possible only at the far end of the tolerance stack and should be
  corrected with R7 rather than accepted. A unit whose limit is above the
  79…87 mA that the series path passes shows no plateau into a short: the
  current then follows the command.
- A current limit is identified by VO_SENSE (the Kelvin-independent
  estimate of it) falling short of the command, not by the current
  failing to rise between two commands: above about 65 mA the two look the
  same at 8 V and 10 V.
- U12 is closed from ±14.3 V (25 °C), not ±14.45 V, and wants the rails at
  ±15.0 V ±0.2 V.
- The sweep's end points are not guaranteed to reach ±10.000 V (§6): clamp
  to the measured span.
- PGA 2 with the buffer off suits every range. Above PGA 2 with the buffer
  off, the 100 Ω filter resistors add a gain term per PGA setting (2.1 % at
  PGA 32 and 64) and a zero term that follows the +5 V supply; take the
  zero at the PGA in use, or use the buffer, which is legal for
  |signal| < 0.25 V at the minimum AVDD (not 0.5 V).
- The measurable ranges with the channel gain and offset at their limits
  are ±11.76 V and ±97.3 mA (§2).
- **The firmware's default current cut-off is now inside the plateau.**
  `i_compliance` defaults to 50 mA, described in the firmware
  specification as "effectively off, under the hardware plateau". That was
  true of a 49 mA plateau. With the expected plateau at 40…65 mA and the
  sinking one typically above 50 mA, the firmware will write the zero code
  before U5 limits. If the hardware plateau is meant to act, the setting
  has to be at its 100 mA maximum (or the default changed).
- **Immediate-zero paths and inductive DUTs.** In TI's model, a fast fall
  of the command out of the *negative* current limit with an inductive DUT
  (0.3…10 mH) leaves U5 oscillating with current still in the DUT (§3.7).
  The current cut-off above, the fault handlers, the dead-man and the
  reference gate all make that edge; ramps do not. Until bring-up shows
  the hardware does not do it, an inductive DUT should not be taken into
  current limit, and a cut-off that ramps down instead of writing zero
  would be the safer reaction for such a load.
- After the USB cable is pulled, U10's diode from D+ to the VBUS pin can
  hold the sensed VBUS node near 2.7…3.0 V while the D+ pull-up is on
  (review estimate; the USBLC6-2 data sheet was not available). The
  firmware's 3.0 V detach threshold has little margin against that; 3.6 V
  would have plenty, since a valid VBUS is at least 4.4 V.
- Arming: the reference step can put a microsecond spike on V_SWEEP (§6).

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
| `tools/test_guard_fails.sh` | 40 of 40 injected faults caught |
| `main.py --autoplace`; build with no KiCad present | PASS; SKIPPED reported, board untouched |

What this does not establish: nothing was measured. The plateau in both
polarities, the stage's step response and its behaviour on the plateau
with a real cable, an inductive load and a DUT, U12's thresholds, the
LED-string current (which is how D7's self-heating shows), R30's, U12's
and U5's temperatures, the ADC clock's start-up, and the partial-supply
currents are bring-up measurements (`bringup_checklist.md` §3, §5, §7,
§8).

---

## 9. What the review of this record changed

The first version of this record (one commit earlier) was put to nine
independent reviewers, each told to refute one part of it from primary
sources, and the design was then swept block by block by eight more
looking for what it had missed. Seven of the first nine confirmed their
part with corrections, one confirmed it outright, and one refuted a claim.
What changed as a result:

- **Withdrawn:** that the OPA547 "equals or betters the OPA548 on every
  figure this stage uses" and is "better damped". It has less unity-gain
  capacitive-load margin (§3.1), and the step-response comparison behind
  the claim was a macromodel artefact (§4.1).
- **New finding, fixed:** the plateau with an inductive DUT, and the wrong
  values of the output network (§3.7).
- **New finding, fixed:** C17/C18 could not be bought as specified (§3.8).
- **Reworded:** the plateau's 9…89 mA was presented as "every data-sheet
  error stacked" and "above zero for every unit". It is an extrapolation
  from typical curves with one data-sheet limit on top; with R7's
  tolerance and the rail range it is 8…89 mA; "centred on 49 mA" is an
  expectation, now given as 40…65 mA; and a plateau is visible only below
  the 79…87 mA of the real short-circuit path, not the 91.3 mA bound
  (§3.1).
- **Strengthened:** the build check (§3.6), which had no floor, left out
  R7's tolerance and the low rail, matched the amplifier's name loosely
  and did not look at the order code.
- **Corrected figures:** U12's minimum current and thresholds were rounded
  in the favourable direction by 0.1 mA and 50 mV, the 50 °C figure was
  called guaranteed although it rests on a typical curve, and the rail
  setting was not stated as a requirement (§3.2); U5's worst passive
  dissipation is 1.02 W, not 0.95 W (§3.1).
- **Corrected statements** that were in the design before this audit and
  that it had not caught: the Kelvin common mode, two rows of the fault
  table, the REF5025 capacitor range (§6), and the "guaranteed" ranges
  (§2, §7).
- **Bring-up checklist:** the ±15 V current limit it prescribed (50–100 mA)
  was below what the plateau test draws, so the supply's own limit could
  have been recorded as U5's plateau; the plateau test could not tell a
  limit from the series resistors above about 65 mA; there was no check of
  the ADC clock, no inductive-load or linear-step test, and its advice to
  "try 3.3 Ω" for the output network pointed the wrong way. All corrected.
- **Release procedure:** exact commands, the layer list and the separate
  plated/non-plated drill files (§5).

The sweep also produced the observations of §6 that concern the jack
hardware, the heat R30 puts into U12, operating rules for energised DUTs
and for power-down, and what the buyer has to be told for the generic
lines of the parts list, and the one on the DAC's ground island in §4.4.
None of them needed copper.

**The third pass.** The corrected record (the second commit) was put to
seven more reviewers: two on the output network (linear region; plateau),
one each on C17/C18, the build check's code, the documents' consistency,
the bring-up procedure as a procedure, and whether every earlier review
item had really been dealt with. Six confirmed their part with
corrections; the one on the plateau refuted part of it. What changed:

- **Refuted in part, and now stated:** "the network cures the plateau".
  It cures the *steady* plateau, and the reviewer's own 181 cases agree.
  It does not cure, in the model, a narrow oscillating window at the
  positive knee, nor a fast exit from the negative limit with an inductive
  DUT, which leaves U5 oscillating with current in the DUT (§3.7). Both
  were reproduced here before being written down. The second touches the
  safe state, so it is now an operating restriction, a bring-up test and a
  note to the firmware work (§6, §7), with a no-copper remedy identified.
  The earlier "4.7 nF is also quiet" was wrong near the negative entry;
  C49 is now specified as a C0G part.
- **Linear region:** the reviewer's two independent models put the fitted
  stage at 47…66° of phase margin and confirm 100 Ω as the right choice,
  but "within 2° of no network" and "25…35° lost with 3 or 10 Ω" were
  figures of one model; the record now gives the spread (§3.7, §4.1).
- **C17/C18:** the part is right; what was missing is that its maker does
  not want it soldered with an iron, and the supply track it damps is
  90…110 mm long, not 60 (§3.8). An iron-only fallback is named.
- **Build check:** it missed an R7 marked not-fitted through the `dnp()`
  helper, took R30 at its nominal value in one comparison, and had no
  injected fault for the 5 mA floor, the unfitted R7 or a part name
  without its package letter. All fixed; 40 of 40 injected faults are
  caught (§3.6).
- **Bring-up checklist:** read as a procedure by someone who would have to
  follow it, a number of steps could not be done or would have stopped a
  good board: the jack-to-ground reading that a correct board fails
  (J5/J6 see the 50 kΩ dividers), a plateau test the firmware's default
  50 mA cut-off prevents, a 100 mV step the firmware cannot make, a
  plateau "check" at a command that is not on the plateau, an inductive
  test that allowed a relay coil too resistive to reach the limit. All
  rewritten, with the missing checks added (D7's orientation, R8, the
  exit from the limit, U12's temperature rule).
- **Triage and consistency:** every major item of the first two passes
  had been dealt with. Of the minor ones, two had been dropped, one acted
  on wrongly, and eight carried a part that was dropped or stated too
  strongly: the expected
  plateau's low end (40, not 45 mA), the BZT52B22 option's figures, the
  class of row 6, the OPA549's curves, the stale comments and document
  lines, the buying notes that the parts list did not point to (it now
  does, and C47/C48/C49 carry their tolerance), R9's recommended rating,
  the stock check, the jack drawing. All in this record now.
- **A hardware fact for the firmware work that nobody had connected:**
  the firmware's default 50 mA cut-off sits inside the new plateau range
  (§7).
