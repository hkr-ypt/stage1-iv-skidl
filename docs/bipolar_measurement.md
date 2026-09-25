# Bipolar V/I measurement — design change and routed-board ECO

This note records why the Stage 1 measurement chain was unipolar, what was
changed to make both DUT voltage and DUT current signed over the whole range
the source can apply, and exactly what the human-routed board in `routed/`
needs in order to implement it. The SKiDL blocks (`blocks/current_sense.py`,
`blocks/voltage_sense.py`, `blocks/adc.py`) carry the same reasoning next to
the parts; `validation/measurement_range.py` recomputes every number below
from the live part values on each build.

Labels used below: **[fact]** checked against the SKiDL source, the generated
netlist or the routed board file; **[calc]** arithmetic from part values;
**[DS]** a datasheet value (TI SBAS288K ADS1256, SBOS051G INA128, SBOS070D
OPA548, SBOS410H REF50xx, all read for this change); **[judgement]** an
engineering choice that could be argued.

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

## 5. Engineering change for the routed board (`routed/diodes.kicad_pcb`)

`routed/` is **not modified** by this change: no copper was drawn and no part
moved. `tools/compare_routed_board.py` compares the routed board with the SKiDL
design by reference designator; today it reports exactly this list **[fact]**:

```
NEW    R32   10.0k 0.1%  R_0603  [1=/voltage_sense/SENSE_P_AMP, 2=AGND]
NEW    R33   10.0k 0.1%  R_0603  [1=/voltage_sense/SENSE_M_AMP, 2=AGND]
VALUE  R11   '13.0k' -> '35.7k 0.1%'
VALUE  R12   '1.0k'  -> '40.2k 0.1%'
VALUE  R13   '1.0k'  -> '40.2k 0.1%'
NET    U6.5  'AGND' -> 'VREF_2V5'          (U6 REF)
NET    U7.5  'AGND' -> 'VREF_2V5'          (U7 REF)
NET    U8.5  'AGND' -> 'VREF_2V5'          (ADS1256 AINCOM)
BOARD  C31   100nF [+3V3D, DGND]           (pre-existing: on the board, not in SKiDL)
BOARD  C45   100nF [+3V3D, DGND]           (pre-existing: on the board, not in SKiDL)
```

C44 (100 nF, VREF_2V5–AGND at U8) was already on the routed board; it is now
also in the SKiDL design with the same reference, value, footprint and nets,
because this change makes that node the ADC's negative input.

Coordinates below are read from `routed/diodes.kicad_pcb` (mm, KiCad board
coordinates). Route choices are suggestions for the layout designer; they have
not been drawn or DRC-checked.

**A. BOM only — no copper (3 parts).** R11 (B.Cu, 101.8, 44.5), R12 (F.Cu,
97.2, 82.3), R13 (B.Cu, 104.3, 82.0): same 0603 footprints, new values.

**B. U8 pin 5, AINCOM → VREF_2V5 (tiny).** Pin 5 (123.84, 49.875) is joined to
AGND only by a three-segment F.Cu stub:
`(123.84, 49.875)→(124.708, 49.875)→(125.09, 49.493)→(125.09, 48.09)`.
Delete those three segments; pins 2/3 keep their own route
`(123.84, 47.925)→(124.925, 47.925)→(125.09, 48.09)→(126.34, 48.09)→via (126.5, 48.25)`.
Then join pad 5 to pad 4 (VREFP, 123.84, 49.225, already VREF_2V5): the pads
are adjacent at 0.65 mm pitch, so this is a pad-to-pad jumper. C44 (120.725,
48.5) already decouples the node.

**C. U6 pin 5, REF → VREF_2V5 (one trace, ≈14 mm).** THT pad at
(105.565, 51.06), currently connected only by the AGND pours (no tracks), so
the re-net needs nothing removed; refilling the zones isolates it. One feasible
path: on B.Cu, run alongside the existing I_SENSE_OUT route
`(105.565, 48.52)→(109.295, 52.25)→(118.1, 52.25)` on its south side, east to
x ≈ 117.1, then via onto the F.Cu VREF_2V5 segment U2.6 → C9
`(117.1, 54.975)→(117.1, 57.125)`. Pairing REF with I_SENSE_OUT keeps the
signal and the node it is referred to together. The B.Cu +15VA run at
x = 104.465 lies west of the pad and is not crossed.

**D. U7 pin 5, REF → VREF_2V5 (one trace, ≈15–20 mm).** THT pad at
(108.79, 84.54), zone-connected only. Nearest VREF_2V5 copper: the B.Cu segment
`(121.35, 59.8)→(121.35, 67.65)` and R1 pin 1 (116.575, 72.75). One feasible
path pairs with V_SENSE_OUT's B.Cu diagonal `(108.79, 82.0)→(121.75, 69.04)`
on its south-east side, ending on the x = 121.35 segment. Tapping near U2/C9
or U8 rather than downstream of R1/U4 keeps the DC IR difference between the
REF tap and AINCOM to a few µV (it is a fixed offset either way and
calibrates out).

**E. R32 and R33 (two new 0603s, four short stubs).** They belong at U7's
inputs with their AGND pads side by side, so any AGND noise is common-mode to
U7. The F.Cu area left of U7 pins 2–3, roughly x 97.3–100.0, y 78.9–81.5, holds
only the AGND pour: R33 at about (98.9, 79.46), right pad stubbed to U7.2
(101.17, 79.46); R32 at about (98.9, 80.95), right pad stubbed to the
SENSE_P_AMP track at y = 82.25 or to U7.3 (101.17, 82.0); both left pads into
the F.Cu AGND pour. Keep clear of U7's courtyard (x ≥ ≈100.1).

**Scale of the change.** 3 value changes, 2 new 0603 footprints, 3 re-netted
pads, 3 short track segments removed, 2 new traces of 14–20 mm, 1 pad-to-pad
jumper, 4 stubs. No part moves; no existing route other than the U8 pin-5 stub
is touched. **[judgement]** A minor ECO.

**Netclasses.** VREF_2V5 is in SENSE (0.25 mm / 0.25 mm); SENSE_P_AMP and
SENSE_M_AMP in LOCAL_ANALOG. No netclass changes are needed.

### How to apply it without losing layout work

- Make the schematic change in the **routed project** (`routed/*.kicad_sch`):
  three values, the REF pins of U6/U7 and AINCOM of U8 onto VREF_2V5, R32/R33
  added on the voltage-sense sheet. Then Update PCB from that schematic.
- Do **not** push the generated schematic straight onto the routed board: the
  routed board's C31 and C45 are not in the SKiDL design and would be deleted,
  and its C44 carries a KiCad-assigned UUID, so path matching would replace it
  (relink by reference if you do). Back-porting C31/C45 into SKiDL is a
  separate, unrelated change.
- Afterwards: refill zones, run DRC in KiCad 10, and run
  `python3 tools/compare_routed_board.py` — it should report only C31 and C45.

## 6. Verification performed for this change

- `python3 main.py` with the pinned KiCad 9.0.9.1 libraries: structural check,
  SKiDL ERC (0/0), 11/11 connectivity assertions (two new: measurement zero on
  VREF_2V5; Kelvin attenuator topology), measurement-range check PASS,
  Update-PCB no-op on the generated board, 14 board checks.
- `tools/kicad_roundtrip.py` with kicad-cli 7.0.11: KiCad reads 313
  connections in 66 nets, 113 components, all identifiers resolve.
- `tools/test_guard_fails.sh` including two new injections (AINCOM back on
  AGND; R11 back to 13.0 k), both caught.
- Not verified here: KiCad ERC dialog and DRC (need KiCad 8+/10), any SPICE
  or bench measurement, and the routed-board ECO itself (not drawn).

## 7. Open risks

- The OPA548 current-limit accuracy at a ~49 mA setting is still unknown
  (pre-existing, review item R4). The measurement no longer depends on it.
- 5× larger voltage offset/drift referred to the DUT (typ 0.55 mV, 11 µV/°C):
  needs a zero calibration per session; the attenuator resistors should track.
- The low-current floor is still the 10 Ω shunt and the INA128 drift
  (~0.1 µA/°C typ): nA-level diode leakage is not resolvable (pre-existing, R9).
- Fault cases (external voltage on FORCE−, failed U5) can still drive the ADC
  inputs past their rails through 100 Ω (pre-existing, R15).
- VREF_2V5 now also feeds the two INA REF pins through ~15–20 mm traces; the
  DC effect is µV and calibrated, but keep those traces away from digital and
  FORCE copper.
- The worst-case current-channel headroom (97 mV at AVDD = 4.75 V) assumes
  AVDD within the ADS1256's 4.75–5.25 V range; the +5 V bench supply must be
  set accordingly.
