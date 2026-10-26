# Bipolar V/I measurement — design change and routed-board ECO

This note records why the Stage 1 measurement chain was unipolar, what was
changed to make both DUT voltage and DUT current signed over the whole range
the source can apply, and exactly how that change was carried onto the
human-routed board (§5: implemented as `layout/bipolar.eco` on the board of
record `stage1_iv.kicad_pcb`; `unipolar-routed/` stays the unipolar original). The SKiDL blocks (`blocks/current_sense.py`,
`blocks/voltage_sense.py`, `blocks/adc.py`) carry the same reasoning next to
the parts; `validation/measurement_range.py` recomputes every number below
from the live part values on each build.

Labels used below: **[fact]** checked against the SKiDL source, the generated
netlist or the routed board file; **[calc]** arithmetic from part values;
**[DS]** a datasheet value (TI SBAS288K ADS1256, SBOS051G INA128, SBOS070D
OPA548, SBOS410H REF50xx, all read for this change); **[judgement]** an
engineering choice that could be argued.

> **Later revision (final hardware audit corrections).** This note is the
> record of the bipolar change and its numbers are as of that revision. Since
> then the sweep midpoint comes from DAC channel B
> (`V_SWEEP = 8.0321·(V_A − V_B/2)`), the DAC reference is gated for
> reset/halt/crash safety, and R30 is a 2 W PR02. The measurement chain itself
> is unchanged. Two of the statements below now hold under a condition:
> the ±10.04 V span, and so the 91.3 mA passive-DUT bound, assume firmware
> keeps channel B at full scale (`docs/firmware_handoff.md`). If that contract
> is broken the positive output can rise toward the +15 V rail, which is
> outside the measurable range; the hardware bound in that case is R30's
> 127 mA rail-fault envelope. Current counts (15/15 assertions, 109 policy
> clearance errors) are in `docs/audit_corrections.md`.

---

## 1. Why the measurement was unipolar

- **[DS]** The ADS1256 runs from a single +5 V analog supply. Each input pin
  must stay inside `AGND − 0.1 V … AVDD + 0.1 V` (buffer off) or
  `AGND … AVDD − 2 V` (buffer on), whatever the PGA. Its *differential* range,
  `±2·VREF/PGA`, is bipolar — the converter itself was never the problem.
- **[fact]** Both INA128 `REF` pins and the ADC's negative inputs (AIN1, AIN3
  through R16/R18) were on AGND. The measurement zero therefore sat on the
  ADC's input floor: any negative current or voltage drove AIN0/AIN2 below
  `AGND − 0.1 V`.
- **[fact]** U7 already ran at G = 1, the INA128 minimum, so no R14 value could
  help; and even the positive side stopped at +5 V (the pin ceiling), half of
  the ±10.04 V sweep.
- **[calc]** Worse than lost data: in the old wiring a normal negative sweep
  pulled I_SENSE_OUT to −2.39 V and V_SENSE_OUT to −10 V, far below the
  −0.3 V absolute-maximum input voltage **[DS]**, so the ADC's input
  protection conducted continuously, limited only by the 100 Ω filter
  resistors and the INA128's own output current limit — a condition the
  ADS1256 is not specified to operate in. Clamping
  followed by recovery at every zero crossing is exactly the
  polarity-dependent artefact a hysteresis loop cannot tolerate.
- **[calc]** The current gain was sized for a zero on AGND: G = 4.846
  (48.46 V/A) reached PGA-2 full scale at 51.6 mA. Once the zero moves to
  2.5 V, only ±2.35 V of pin window is left on each side (AVDD = 4.75 V), and
  that gain would clip at ±48.5 mA — at or below the ~49 mA nominal current
  limit. Making the channel bipolar therefore also means lowering its gain.

## 2. Options considered

| # | Option | Electrical result | Routed-board disruption | Verdict |
|---|---|---|---|---|
| A | Keep hardware; reverse the DUT between sweeps | Not a hysteresis measurement: the trajectory is broken at 0 V | none | rejected |
| B | Move U6/U7 REF to VREF_2V5 only; ADC still reads against AGND (pseudo-differential, 2.5 V offset subtracted in firmware) | Bipolar, but PGA must stay 1, AGND IR drops (DUT return current in the pour) still add directly to both readings, voltage still only ±2.5 V | 2 REF traces | rejected: no better on the board than C, worse electrically |
| **C** | **U6/U7 REF → VREF_2V5 and ADC AINCOM → VREF_2V5** (true differential against the same node) | 2.5 V cancels inside the conversion; signed codes; one PGA (2) for both channels; PGA up to 64 usable for small signals; AGND drops between INA and ADC no longer enter the result | 2 REF traces + a pad-to-pad jumper at U8 pins 4/5 | **chosen** |
| D | As C but move R16/R18 (AIN1/AIN3 legs) to VREF_2V5 instead of AINCOM | Same as C | R16.1 is the junction that ties U8's AGND pins to the crystal-cap AGND run in the split gap; both pads sit in the tightest area of the board | rejected: same result, far more copper work |
| V1 | **Voltage: two matched single-ended dividers to AGND ahead of U7** (R12/R13 become 40.2 k top arms, new R32/R33 10.0 k bottom arms) | ±10.04 V → ±2.00 V; SENSE+ current returns to AGND, not through R10; SENSE− only diverts I·R10/50.2 k (0.02 % of I) | value change on 2 parts, 2 new 0603s next to U7 | **chosen** |
| V2 | One resistor across U7's inputs (differential divider) | Its current V_K/R flows SENSE+ → SENSE− through the DUT's return and the shunt: a fake parallel conductance (≈200 µA at 10 V for 50 k) in every I–V curve | 1 new part | rejected |
| V3 | Divider after U7 to the 2.5 V node | Loads VREF_2V5 with ±200 µA signal-dependent current; the divider's kΩ Thevenin meets the ADS1256's switched-capacitor input (130 kΩ per pin at PGA 2 **[DS]**) → PGA-dependent gain error of several % | 1 new part near U8 (congested) | rejected |
| V4 | Replace U7 with a sub-unity difference amplifier | Resistive input anyway, different pinout on the RG/REF pads | IC swap + re-net | rejected |
| V5 | Attenuate only to ±5 V | Leaves 5…10 V of the source unmeasurable and able to over-range the ADC mid-sweep | same as V1 | rejected **[judgement]** |
| I1 | Keep R11 = 13.0 k (G 4.846) with the zero at 2.5 V | Clips at ±48.5 mA (pin window) — right at the nominal current limit | none | rejected |
| **I2** | **R11 = 35.7 k (G 2.4006, 24.0 V/A)**, sized to the *physical* current bound | Covers ±97.8 mA guaranteed vs ±91.3 mA reachable (§3) | value change only | **chosen** |

Why the current gain is tied to physics, not to ILIM **[calc/DS]**: for a
passive DUT V and I share a sign, and R30 + R10 = 110 Ω sit outside the OPA548
loop, so `|I| ≤ |V_SWEEP| / (R30 + R10 + R_F1) ≤ 10.04 / 110 = 91.3 mA` no
matter what the current limit does. SBOS070 specifies the OPA548 limit
tolerance only at a 2.5 A setting (±100 mA typ, ±250 mA max), so the ~49 mA
programmed by R7 cannot be relied on as an upper bound. In a forward-biased
hysteresis sweep the current limit is a normal operating state, not a fault,
and its plateau must stay on-scale.

## 3. The chosen circuit and its ranges

```
I_SENSE_OUT = VREF_2V5 + (1 + 50k/35.7k) · 10.0 Ω · I   = 2.500 V + 24.006 V/A · I
V_SENSE_OUT = VREF_2V5 + 1 · (10.0k/50.2k) · V_K         = 2.500 V + 0.19920 · V_K
ADC:  AIN0 − AINCOM,  AIN2 − AINCOM,  AINCOM = VREF_2V5,  PGA = 2 (FS ±2.500 V), buffer off
```

| Quantity | Old | New |
|---|--:|--:|
| Source voltage (V_SWEEP) | ±10.04 V | ±10.04 V (unchanged) |
| DUT voltage physically reachable | ±10.04 V at I→0, ≈±4.6 V at 49 mA | same |
| Measurable DUT voltage | 0 … +5.00 V | **±11.78 V guaranteed** (AVDD ≥ 4.75 V), ±12.55 V at nominal full scale |
| Source current limit (R7) | ≈49.35 mA nominal, tolerance unspecified at this setting | same |
| Physical current bound, any passive DUT | ±91.3 mA (92.5 mA worst tolerance) | same |
| Measurable DUT current | 0 … +51.6 mA (PGA 2) or 0 … +100 mA (PGA 1, pin-limited); no negative current | **±97.8 mA guaranteed**, ±104.1 mA at nominal FS |
| ADC code at negative full scale | V: invalid (pin below AGND); I: invalid | V −10.04 V → AIN2 0.500 V, code −6 710 886; I −91.3 mA → AIN0 0.308 V, code −7 354 163 |
| ADC code at zero | 0 (on the input floor) | 0 (AIN = 2.500 V, mid-window) |
| ADC code at positive full scale | V +5.00 V → +8 388 607 (PGA 1); I 51.6 mA → +8 388 607 (PGA 2) | V +10.04 V → +6 710 886; I +91.3 mA → +7 354 163 (PGA 2, 2²³ = full scale) |
| Worst-case headroom to the ADC pin limit at the physical extremes | — | V 334 mV, I 97 mV (all tolerances stacked, AVDD = 4.75 V) |

The limits on each path, kept separate **[calc/DS]**:

| Limit | Voltage channel | Current channel |
|---|---|---|
| Source can force | ±10.04 V (V_SWEEP) | — |
| Across the DUT under load | ±10.04 V at I→0, shrinking by I·110 Ω | ±91.3 mA physical bound |
| Amplifier input | U7 sees k·V: ±2.0 V differential, CM < 2.2 V, inside (V−)+2…(V+)−2 | U6 sees ±0.92 V on +IN, CM < 0.5 V |
| Amplifier output | 0.50 … 4.50 V (INA128 swings to ≈±13.6 V on ±15 V) | 0.31 … 4.69 V |
| ADC pin window (buffer off) | −0.1 … 4.85 V at AVDD 4.75 V | same |
| ADC full scale (PGA 2) | ±2.500 V ↔ ±12.55 V | ±2.500 V ↔ ±104.1 mA |
| **Recommended validated operating range** | **±10.04 V** (the whole source span) | **±91.3 mA** (whatever the current limit does) |

Resolution and error budget (INA128P, input-referred, before calibration):

| | Voltage (at the DUT) | Current |
|---|---|---|
| ADC LSB, PGA 2 | 1.50 µV | 12.4 nA (0.19 nA at PGA 64, FS ±1.63 mA) |
| ADC noise, 30 SPS, PGA 2 **[DS]** ≈0.34 µV rms | ≈1.7 µV rms | ≈14 nA rms (≈4 nA at PGA 64) |
| INA128 offset typ / max | 0.55 / 2.76 mV | 5.2 / 25.8 µA |
| INA128 offset drift typ / max | 11 / 103 µV/°C | 0.10 / 0.88 µA/°C |
| Old design, same rows | 0.11 / 0.55 mV; 2.2 / 20.5 µV/°C | 3.1 / 15.3 µA; 0.06 / 0.46 µA/°C |

The ±10 V span costs a factor of 5 in voltage offset/drift and ≈1.7 in current
drift. A zero calibration (SENSE shorted / FORCE open) at the start of a run
removes the offsets; only drift within the run remains. **[judgement]** This is the right trade for a 24-bit
converter: resolution is not the constraint, the INA128's offset is, and it
calibrates.

Other consequences **[calc]**:

- **Kelvin loading.** Each sense input now has 50.2 kΩ to AGND. SENSE+ draws
  up to 200 µA from the DUT's + node; that current is supplied through FORCE+
  and returns straight to AGND, never through R10, so the current reading is
  unaffected, and the Kelvin error is only 200 µA × R(sense lead) (≈20 µV for
  0.1 Ω). SENSE− diverts `V(SENSE−)/50.2 k = I·R10/50.2 k`, a 0.02 % gain term.
- **Divider mismatch.** Converts common mode into error only through
  V(SENSE−) ≈ I·R10: ≤ 3 mV at the 92 mA bound with 0.1 % parts, proportional
  to I. Specify R12/R13/R32/R33 as one thin-film series (0.1 %, ≤ 25 ppm/°C)
  so k tracks in temperature.
- **Reference loading.** U6 and U7 REF each draw 4…56 µA from VREF_2V5
  (`(VREF − A2out)/80 kΩ`), ≪ the REF5025's ±10 mA; the variation is tens of
  µA, i.e. µV at the reference.
- **Open sense leads** now read 0 V (R32/R33 pull U7's inputs to AGND) instead
  of rail-saturating on INA128 bias current.
- **Protection.** Unchanged in topology. At the D1–D4 clamp level (±15.7 V)
  U7 sees only 3.1 V. Both ADC inputs now stay inside their absolute window
  for every normal operating point of both polarities (the old design did not).

## 4. Hysteresis: −V → +V → −V on a fixed DUT

1. Firmware writes DAC codes along the trajectory. V_SWEEP crosses 0 V at code
   32 768, where both channels sit at the ADC's mid-window (2.500 V). Nothing
   in the chain changes state at the crossing: no clamp conducts, no amplifier
   leaves its linear region, no range is switched.
2. At every point the ADC reads AIN2 − AINCOM then AIN0 − AINCOM with the
   *same* PGA (2), so V and I are converted back to back without a PGA
   change or recalibration between them.
3. The codes are signed two's-complement with the physical sign: a reverse-
   biased diode reads negative V and negative (leakage) I; the forward branch
   reads positive; the forward current-limit plateau stays on-scale.
4. The chain is resistive and linear apart from C24/C25 (τ ≈ 200 ns) and the
   ADC's digital filter, so there is no polarity-dependent offset, saturation
   or recovery tail to masquerade as hysteresis. The only memory in the loop
   is the DUT's.
5. Up and down branches use the same calibration constants, so any residual
   offset shifts both branches equally and cannot open or close a loop.

Firmware conversion (PGA = 2, VREF = 2.500 V, code c):

```
V_K = c / 2^23 · 2.5 V / 0.199203          (≈ 1.4961 µV per code)
I   = c / 2^23 · 2.5 V / 24.0056 V/A        (≈ 12.415 nA per code)
ADS1256 MUX: AIN2/AINCOM for V (PSEL = 2, NSEL = AINCOM), AIN0/AINCOM for I
```

AIN1 and AIN3 stay tied to AGND through R16/R18 and are useful self-checks:
AIN1 − AIN3 reads the ADC's own zero; AIN1 − AINCOM at PGA 1 reads −VREF,
i.e. exactly half of negative full scale.

## 5. Engineering change on the routed board — as implemented

The change is implemented, drawn and DRC-checked. It lives in
`layout/bipolar.eco`, applied by `generators/routed_pcb.py` to the captured
human layout (`layout/stage1_iv.layout`) to produce the board of record
`stage1_iv.kicad_pcb`. `unipolar-routed/` is **not modified**: it remains the
unipolar original, and `tools/compare_routed_board.py` still reports exactly
the difference below between it and the SKiDL design **[fact]**:

```
NEW    R32   10.0k 0.1%  R_0603  [1=/voltage_sense/SENSE_P_AMP, 2=AGND]
NEW    R33   10.0k 0.1%  R_0603  [1=/voltage_sense/SENSE_M_AMP, 2=AGND]
VALUE  R11   '13.0k' -> '35.7k 0.1%'
VALUE  R12   '1.0k'  -> '40.2k 0.1%'
VALUE  R13   '1.0k'  -> '40.2k 0.1%'
NET    U6.5  'AGND' -> 'VREF_2V5'          (U6 REF)
NET    U7.5  'AGND' -> 'VREF_2V5'          (U7 REF)
NET    U8.5  'AGND' -> 'VREF_2V5'          (ADS1256 AINCOM)
```

C31 and C45 (100 nF +3V3D decouplers the layout designer added by hand) were
on the routed board but not in the SKiDL design; they are now in
`blocks/adc.py` and `blocks/mcu.py` with the board's references, values and
nets, so the regenerated board keeps them. C44 was already carried.

Coordinates are KiCad board millimetres. Every operation below is in
`layout/bipolar.eco` with its reason. New tracks are 0.25 mm (the SENSE and
LOCAL_ANALOG netclass width) and keep the **repository** netclass clearances
(0.25 mm to sense nets, 0.30 mm to ±15 V) — stricter than the 0.20 mm the
board was routed to. They were found with `tools/eco_route.py` (two-layer A*
against the real copper), then snapped to pad centres, 45° runs and existing
track points; KiCad's DRC judged the result.

**A. Values only — no copper (3 parts).** R11 (B.Cu), R12 (F.Cu), R13 (B.Cu):
same 0603 footprints, new values from the circuit.

**B. U8 pin 5, AINCOM → VREF_2V5 — as proposed.** Removed the three-segment
F.Cu AGND stub `(123.84, 49.875)→(124.708, 49.875)→(125.09, 49.493)→(125.09,
48.09)`; pins 2/3 keep their own route into that junction. Added a 0.65 mm
pad-centre-to-pad-centre jumper pin 4 (VREFP) → pin 5 on F.Cu. AINCOM and VREFP
are now the same copper, decoupled by C44 as before.

**C. U6 pin 5, REF → VREF_2V5 — 13.9 mm, 1 via (differs from the proposal).**
F.Cu from the pad centre (105.565, 51.06) east at y = 50.75 over U6's B.Cu
output track (I_SENSE_OUT), a via at (108.95, 50.75), then B.Cu east at
y = 50.75 — 1.5 mm north of I_SENSE_OUT's y = 52.25 run — and a 45° step onto
the existing VREF_2V5 via at (118.75, 49.25), where the U2 → U8 trunk meets the
TP8 branch. *The proposal* went south of I_SENSE_OUT and via'd onto the U2 → C9
segment. The pad's B.Cu side is fenced by I_SENSE_OUT's diagonal from U6
pin 6, so either path needs a via; the implemented one is the shortest the
router found at the policy clearances and ends on an existing trunk via instead
of adding a second one next to U2.

**D. U7 pin 5, REF → VREF_2V5 — 19.7 mm, no via (differs from the proposal).**
B.Cu from the pad centre (108.79, 84.54) up between U7's pad columns, out
between pins 6 and 7, north past pin 7, then a 45° run to the corner
(117.75, 70.25) of the existing B.Cu VREF_2V5 track that feeds the DAC's
reference (U4 pin 2) and R1. *The proposal* paired it with V_SENSE_OUT's B.Cu
diagonal on its south-east side, ending on the x = 121.35 segment near U2.
On the real copper that path does not exist without a via: V_SENSE_OUT's own
B.Cu route (U7 pin 6 → diagonal → x = 121.75 up to its via at y = 59.2)
separates U7 pin 5 from every VREF_2V5 track, and the only B.Cu gap is between
U7 pins 6 and 7, north-west of V_SENSE_OUT. That gap is the tightest point of
the ECO: 0.048 mm over the policy clearance to both pin 7 (+15VA, 0.30 mm) and
V_SENSE_OUT (0.25 mm), i.e. 0.35 and 0.30 mm of actual gap — 0.10–0.15 mm
over the 0.20 mm the board was routed to.

The tap is downstream of the DAC-reference branch, which the proposal wanted
to avoid; the cost, from the board's copper **[calc]** (1 oz, 0.49 mΩ/□ at
25 °C, vias neglected): U7's tap is 51 mΩ of track from U2 pin 6 and U8 AINCOM
28 mΩ, on different branches. The DC current on the tap's branch — the
R1 + R2 divider's 125 µA plus the DAC8552 reference load (not quantified here;
a resistor-string DAC's reference load does not depend on code
**[judgement]**) — shifts U7's zero by about 6 µV plus 5 µV per 100 µA of
DAC load, relative to U2; AINCOM moves by 2.8 µV per 100 µA of ADC reference
load. Both are fixed offsets, about 30 µV referred to the DUT for the divider
term — one-twentieth of U7's typical offset, and removed by the per-session
zero calibration §7 already requires. The REF pins' own, signal-dependent
current ((V_REF − A2 out)/80 kΩ **[calc]** from the 40 kΩ output-stage
resistors **[DS]**) through their routes (U6: 47 mΩ,
U7: 90 mΩ from U2 pin 6) is at most ≈ 8 µV at the ADC, a few ppm of full scale.

**E. R32 and R33 — as proposed, positions refined.** Two 0603s on F.Cu left of
U7's inputs, in the only courtyard-free space there: R33 at (98.4, 79.2), R32
at (98.4, 80.7), both rotated 180° so pad 1 (signal) faces U7 and both pad 2s
(AGND) sit side by side and join the F.Cu AGND pour by thermal relief, keeping
AGND noise common-mode to U7. R33 pad 1 → U7 pin 2 on F.Cu (2.1 mm); R32 pad 1
drops 1.55 mm straight onto the existing R12 → U7 pin 3 track. Their
designators go on F.Fab (no clear spot on silk, as the layout already does for
C23 below them); R12's designator, which R32 now covers, moves beside R12.

**Scale.** 3 value changes, 2 new parts, 3 re-netted pads, 3 segments removed,
15 segments and 1 via added (total new copper 37.9 mm), 1 silk designator
moved. No part moved; no existing track or via other than U8 pin 5's stub was
touched; outline, holes and pour definitions unchanged. Board of record: 115
footprints, 861 tracks, 95 vias.

**Result [fact], KiCad 10.0.6** (`validation/routed_board_check.py`, zones
refilled, all track errors, schematic parity): 0 unconnected items, 0 parity
problems; 0 errors under the rules the board was routed to (with clearance
checking on, which the routed project had off); under the repository netclass
policy, 114 clearance errors — every one present at the same place on the
unipolar original (which has 116; the ECO removed the 2 that involved U8
pin 5's stub) — and none new. See `docs/physical_design.md`.

## 6. Verification performed for this change

- `python3 main.py` with the pinned KiCad 9.0.9.1 libraries and KiCad 10.0.6:
  structural check, SKiDL ERC (0/0), 11/11 connectivity assertions (two new:
  measurement zero on VREF_2V5; Kelvin attenuator topology),
  measurement-range check PASS, and `validation/routed_board_check.py` on the
  routed board (generated from source; parity; Update-PCB no-op; KiCad DRC as
  in §5).
- `tools/reproduce_reference.py`: the layout source regenerates the unipolar
  original — identical copper, placement, connectivity, KiCad DRC and zone
  fill (`docs/physical_design.md`).
- `tools/kicad_roundtrip.py` and `kicad-cli sch erc` (KiCad 10.0.6): KiCad
  reads the netlist's connectivity; KiCad ERC 0 errors, 0 warnings.
- `tools/test_guard_fails.sh`: 23 injections, including AINCOM back on AGND,
  R11 back to 13.0 k, an ECO track dropped (unrouted) and an ECO that no
  longer matches its base.
- Not verified: any SPICE or bench measurement, fabrication-house DFM, and the
  board by a person in KiCad's GUI.

## 7. Open risks

- The OPA548 current-limit accuracy at a ~49 mA setting is still unknown
  (pre-existing, review item R4). The measurement no longer depends on it.
- 5× larger voltage offset/drift referred to the DUT (typ 0.55 mV, 11 µV/°C):
  needs a zero calibration per session; the attenuator resistors should track.
- The low-current floor is still the 10 Ω shunt and the INA128 drift
  (~0.1 µA/°C typ): nA-level diode leakage is not resolvable (pre-existing, R9).
- Fault cases (external voltage on FORCE−, failed U5) can still drive the ADC
  inputs past their rails through 100 Ω (pre-existing, R15).
- VREF_2V5 now also feeds the two INA REF pins through 13.9 mm (U6) and
  19.7 mm (U7) traces, U7's tapped from the DAC-reference branch; the DC
  effect is µV and calibrated (§5 D). Both run over the AGND area, at least
  5 mm from any digital or FORCE copper **[fact]**.
- The worst-case current-channel headroom (97 mV at AVDD = 4.75 V) assumes
  AVDD within the ADS1256's 4.75–5.25 V range; the +5 V bench supply must be
  set accordingly.
