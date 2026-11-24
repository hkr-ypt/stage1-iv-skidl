# Final remaining hardware corrections — implementation record

> **Later changes.** This record is kept as written. The final design audit
> ([`final_audit.md`](final_audit.md)) changed these facts stated below:
> U5 is an **OPA547T** with R7 = 348 kΩ (§2.7: on an OPA548 the 49 mA
> setting is not merely uncharacterised, it can come out at zero); D7 is the
> ±2 % **BZT52B24** and U12 is closed from ±14.3 V at 25 °C, not ±14.45 V
> (§2.5: the earlier figure left out the zener's self-heating); R30's order
> code is **PR02000201000FA100**; C26/C27 are 18 pF; the U5 output network
> **R38/C49 is fitted, with R38 = 100 Ω**; and C17/C18 are a polymer
> tantalum, **TCJB106M035R0200E**. The passive fault envelope of §2.7 and
> everything in §2.1–§2.4 and §2.6 stand, with two qualifications to the
> partial-supply and external-source statements (`final_audit.md` §6: an
> energised DUT feeds the rails through the SENSE clamps, and F1 does not
> limit D5's current).

This records the last round of hardware corrections after
[`audit_corrections.md`](audit_corrections.md): what was changed, the
analysis behind each change, how it was validated, and what is still open.
The design itself is the SKiDL source (`blocks/*.py`, reasoning next to the
parts) and the layout ECOs `layout/final_corrections.eco` (placement and new
copper) and `layout/policy_clearance.eco` (clearance re-routing), each
operation with a reason.

- Base: commit `4626a58`.
- Source of truth: SKiDL → `python3 main.py` regenerates the netlist, the
  nine sheets, the component manifest and the routed board. No generated file
  was edited by hand. `tools/author_project_symbols.py`,
  `tools/gen_project_config.py` and `tools/vendor_footprints.py` regenerate the
  project symbol library, the project rules and the vendored footprints.
- Wording used below. **Guaranteed**: follows from data-sheet limits (min/max)
  and the tolerances of the fitted parts. **Expected**: follows from typical
  values or curves. **Bench**: must be measured (`bringup_checklist.md`).

---

## 1. Disposition

| # | Item | Disposition |
|---|---|---|
| 1 | R30 vs F1 spacing (thermal) | **Fixed by relocation.** F1 moved to the bottom edge: body-to-body 14.1 mm from R30 (was 2.6 mm) and 22.8 mm from U5 (was 0.9 mm); courtyards 7.6 mm from the precision divider (was 4.8 mm). Thermal effect is not measured: bring-up soak (checklist) |
| 2 | Orderable R10 | **Fixed.** Vishay Dale **RS02B10R00BE12**: 10.00 Ω ±0.1 %, ±20 ppm/°C, 3 W wirewound, decoded from Vishay doc. 30204 and seen listed by a distributor; KiCad DIN0614 footprint, 20.32 mm pitch, lying flat. Value kept at 10 Ω |
| 3 | Policy clearance violations | **Fixed: 109 → 0.** 87 re-routed at the policy clearances (`layout/policy_clearance.eco`); 22 were on four nets the policy had put in the high-impedance analog class by mistake — reclassified with the evidence in §2.3, and each meets its corrected class. KiCad 10 DRC under the repository rules: 0 clearance errors, 0 unconnected; `routed_board_check.py` now requires that |
| 4 | Mounting holes | **Fixed.** Three isolated M3 holes (H1–H3) with a 4.0 mm-radius copper keepout on both layers. A fourth (top-right) does not fit; §2.4 gives the mechanical alternative |
| 5 | Partial-supply states and sequencing | **Fixed in hardware where hardware was needed:** dead-rail Schottky clamps D8/D9 and a photorelay U12 that disconnects FORCE+ unless both ±15 V rails are up. Guaranteed / expected / bench split in §2.5 |
| 6 | FORCE− protection | **Fixed.** Series PTC F2 between J7 and the shunt node, R10 upgraded to a part that survives F2's trip. Nothing connected across or around the shunt |
| 7 | OPA548 current-limit bound | **Defined.** The bound the protection relies on is passive (128 mA DUT short, 143 mA FORCE+ to AGND), computed on every build from guaranteed minima; ILIM is reported as typical (49.4 mA) and never relied on. R30 is at 98 % of rating in the stacked worst case — stated, not hidden (§2.7) |
| 8 | Stale identifier snapshot | **Fixed.** `docs/audit_identifiers_after.md` regenerated (U5 `RESOLVED`); the generator prints repo-relative and `${KICAD9_*_DIR}`-rooted paths only, and `tools/check_no_abs_paths.py` now checks the file instead of exempting it |

---

## 2. Analysis and changes

### 2.1 R30 vs F1 (thermal separation)

F1 (0ZRM0010FF1E) trips on its own temperature: its hold current falls from
100 mA at 23 °C to 68 mA at 50 °C and 45 mA at 70 °C (Bel derating table).
It used to sit with its courtyard 2.1 mm from R30's and its body 0.9 mm
from U5's body outline: next to both heat sources of the output stage. In normal operation
U5 dissipates ≈ 0.6–1.3 W (supply headroom × the plateau current, plus
quiescent current); R30 dissipates 0.24 W at the 49 mA plateau and up to
1.96 W in the rail fault (§2.7). The courtyard gap was never the issue; the
heat was.

F1 now sits at the bottom edge (`layout/final_corrections.eco`), horizontal,
between the J5 jack and the SENSE+/SENSE− runs:

| F1 to | Before | After |
|---|---|---|
| R30 body centre (PR02 hot spot), from F1's body centre | 6.0 mm | 22.1 mm |
| R30 body, edge to edge | 2.6 mm | 14.1 mm |
| R30 courtyard | 2.1 mm | 12.4 mm |
| U5 body (TO-220 outline), edge to edge | 0.9 mm | 22.8 mm |
| Nearest courtyard of the precision divider R12/R32/R33 | 4.8 mm (R33) | 7.6 mm (R12) |

Measured on the generated boards (commit `4626a58` and this tree) from the
footprints' fabrication outlines and courtyards with KiCad's pcbnew.

R30 stayed where it was: it must be electrically between F1 and the jacks,
and it already sat 7 mm from the divider. VO_SENSE now runs from U5's
feedback node to F1 and F1_MID back up to R30 (B.Cu where they cross the
Kelvin runs); both are outside U5's loop and outside the Kelvin sense, so the
extra length (≈ 30 mΩ) is a series resistance only.

Not measured: the actual temperature rise of F1 in the soak test. That stays
a bring-up item, but it no longer depends on a 2 mm gap to a 2 W part.

### 2.2 R10 — an orderable shunt

| | |
|---|---|
| Part | Vishay Dale **RS02B10R00BE12** |
| Decoded (Vishay doc. 30204, Rev. 27-Apr-2026, global part numbering) | RS02B model, `10R00` = 10.00 Ω, `B` = ±0.1 %, `E12` = lead (Pb)-free, bulk |
| Range check | RS02B at ±0.1 %: 0.499 Ω – 6.5 kΩ, so 10 Ω is a standard value |
| Power | 3.0 W (characteristic U, 25 °C) |
| TCR | ±20 ppm/°C for 10 Ω and above |
| Short-time overload | 5 × rated power for 5 s, ΔR ≤ ±(0.2 % + 0.05 Ω) |
| Body | 14.22 ± 1.57 mm long, 4.75 ± 0.79 mm diameter, 0.813 mm leads |
| Footprint | `Resistor_THT:R_Axial_DIN0614_L14.3mm_D5.7mm_P20.32mm_Horizontal` (14.3 × 5.7 mm body outline, 1.4 mm holes, 20.32 mm pitch: ≥ 2.2 mm of lead per side at the longest body) |
| Availability | Listed by a distributor as "3 W, 10 Ω, 0.1 %" under this exact number; the listing is not an authorised distributor, so confirm stock and lead time with Vishay or an authorised channel before ordering |

Why a 3 W wirewound for a resistor that normally dissipates 24 mW: R10 must
outlive an external source on J7 until F2 trips (§2.6). A 0.5 W metal film
cannot (Vishay CMF, doc. 31018, tests short-time overload at 2.5 × rated
voltage for 5 s, ≈ 3 W). The RS02B also keeps the 0.1 % / 20 ppm/°C accuracy
the measurement needs.

What it costs, stated:

- **Inductance.** RS is a conventional winding. The non-inductive NS02B has
  the same body and footprint, but no listing of `NS02B10R00BE12` was found,
  so it is an alternative, not the default. The sweep is slow (ms steps), and
  R30 (100 Ω) damps the load loop.
- **Thermal EMF.** The element is CuNi or NiCr "depending on resistance
  value" (data sheet); the termination EMF is not specified. The part is
  mounted **flat** in the top band so both terminations sit at the same
  height and temperature, which is what keeps self-heating from turning into
  a differential EMF. Bring-up measures the zero-current offset after a
  high-current dwell.
- **Size.** 23.6 × 6.2 mm courtyard; it now lies in the top band between H1
  and the ±15 V rail runs.

The shunt's Kelvin connection is explicit: F2 feeds R10's ISENSE_HI pad on
F.Cu, and U6 +IN leaves the same pad on its own B.Cu trace through TP3. They
meet only at the pad.

### 2.3 Policy clearance violations

KiCad 10 DRC (zones refilled) of the board at `4626a58` under the
repository's own rules (`stage1_iv.kicad_pro` / `.kicad_dru`, generated from
`libraries/netclasses.py`): **109 clearance errors**, gaps 0.200–0.282 mm —
38 against POWER_HV (0.30 mm), 24 against SENSE (0.25 mm), 47 against
LOCAL_ANALOG (0.25 mm). The human routing was drawn to 0.20 mm throughout;
`routed_board_check.py` accepted that as long as nothing got worse.

**22 of them were a policy error, not a routing error.** All 22 involve four
nets that `netclasses.py` had put in LOCAL_ANALOG (the class for
high-impedance analog nodes: summing junctions, filter nodes, ADC inputs):

| Net | What it is | Corrected class | Violations |
|---|---|---|---|
| `LED_K` (board name `/mcu/LED_A`) | status-LED cathode: +3V3D → LED1 → this node → R23 1 kΩ → PC13, which sinks ~1 mA. A logic-level switched node | DIGITAL (0.20) | 18 |
| `VCAP1` | STM32 internal-regulator output, decoupled to DGND: a supply node | POWER_LV (0.20) | 1 |
| `CC1`, `CC2` | USB-C configuration lines, 5.1 kΩ to DGND, read by the host | DIGITAL (0.20) | 3 |

None is in the measurement chain or high-impedance; each now meets its
corrected class (smallest gap 0.200 mm against 0.20 mm). The reasons are
recorded at the class definitions in `netclasses.py`. No class clearance
was lowered and no rule was added or relaxed; the board-wide minimums are
unchanged.

**The other 87 were re-routed** (`layout/policy_clearance.eco`, 293
operations, each with its reason): an automatic pass with
`tools/eco_route.py`, which redraws a run between the same terminals with
every obstacle inflated by the larger of the two classes' clearances, then
hand re-routes in 21 areas where it found no path (the U5/U6/U7 output and
sense block, U3 and U4 on the DAC sheet, the ADC inputs under U8, the
crystal and SPI vias, and the ±15 V feeds past them). Changes worth knowing
about:

- Widths are unchanged except `ADC_XTAL1` past the CS_ADC/DRDY vias,
  0.40 → 0.25 mm (its class width).
- ADC_AIN0 now leaves U8 to the west and reaches its differential capacitor
  C24 around the north side; the AIN1–AIN3 fan-out vias are staggered.
- One AGND jumper from C7 was in the way of the −15 V feed; it turned out to
  be the only link of two F.Cu AGND pour islands (U2/C7/C10/C11/R2/C49, and
  C9). Four AGND stitch vias to the B.Cu pour replace it; KiCad's
  connectivity confirms 0 unconnected items with zones filled at both the
  as-built and the policy clearances.
- No part was moved for this item.

Result, KiCad 10.0.7 DRC with zones refilled:

| Rules | Clearance errors | Other | Unconnected |
|---|---|---|---|
| Repository policy (`stage1_iv.kicad_pro`) | **0** | 2 silkscreen-to-edge warnings, also on the original | **0** |
| As-built (0.20 mm, the rules the board was routed to) | 0 | the same 2 warnings | 0 |

`validation/routed_board_check.py` previously required only "no policy
violation the original does not have"; it now requires **0 policy errors**
(pre-existing or not), 0 unconnected items under the policy fill, and no
warning the original lacks. `tools/test_guard_fails.sh` injection 32
(dropping `policy_clearance.eco`) proves the gate fails.

### 2.4 Mounting holes

Three M3 holes, board-only footprints (no symbol, `board_only`), each the
project footprint `stage1_iv:MountingHole_3.2mm_M3_Isolated`, derived by
`tools/vendor_footprints.py` from KiCad's `MountingHole_3.2mm_M3`:

- NPTH 3.2 mm, **no annular ring** (no copper of any net at the hole);
- a rule-area **keepout of radius 4.0 mm on F.Cu and B.Cu**: no track, no
  via, no copper pour;
- courtyard radius 4.25 mm on **both** sides, so no part (top or bottom) can
  be placed under the hardware.

An M3 washer (DIN 125, 7.0 mm) or a 5.5 mm hex standoff (6.35 mm across
corners) fits inside the 8.0 mm keepout. Measured on the generated board, the
nearest copper of any net is 4.67 mm from H1's centre (a pad), 7.20 mm from
H2's and 5.81 mm from H3's: ≥ 1.17 mm beyond a 7.0 mm washer's rim, and
under solder mask besides. The hardware therefore cannot touch
copper of any net, and the chassis is **not** bonded to AGND or DGND through
it.

| Hole | Position | Neighbourhood |
|---|---|---|
| H1 | (87.7, 37.5) | between J7 and R10, top edge |
| H2 | (122.2, 88.2) | bottom edge, analog/digital boundary |
| H3 | (157.7, 89.2) | bottom edge, under J1/J3 |

- **Underside test points.** All eight TPs (B.Cu) are ≥ 8.2 mm from the
  nearest hole centre (TP7 to H2), outside the 4.25 mm courtyard; the
  courtyard check covers both sides and passes.
- **OPA548 tab.** U5's tab is at −15 V and stands vertically above the
  board; its outline is 21.5 mm from H1's centre (nearest hole), so mounting
  hardware cannot reach it.
- **No fourth hole.** The top-right quadrant (J1, J3, U9) has at most a
  2.7 mm free radius to the nearest copper, courtyard or board edge
  (measured on a 0.5 mm grid), against the 4.25 mm an isolated M3 needs. The mechanically sound
  alternative: mount on H1–H3 (a stable three-point mount) and carry the
  front edge on the four panel jacks J4–J7, which are bolted through the
  front panel. J1 is a screw terminal: tighten its screws before the board is
  mounted, or support its corner with an insulating card guide or bumper.

### 2.5 Partial-supply states and sequencing

What can happen with one supply missing, before this change:

| State | Mechanism | Hazard |
|---|---|---|
| −15 V absent (floating), +15 V present | U3/U5/U6/U7 quiescent current (U5 alone up to 20 mA) flows out of their V− pins and drags the dead −15VA rail **up** | D2/D4 (anode −15VA, cathode SENSE±) forward-bias and drive the DUT through the Kelvin leads, with nothing but the dead rail's impedance to limit it |
| +15 V absent, −15 V present | Same, mirrored: +15VA dragged below AGND | D1/D3 conduct out of the DUT |
| One ±15 V rail absent (either way) | U5 runs single-supply and its output sits wherever its input stage puts it, inside the live rail | Uncommanded volts on FORCE+ |
| ±15 V present, +5 V absent | DAC, ADC, REF5025 and MCU unpowered; DAC reference gate open | V_SWEEP ≈ 0 (DAC output 0, VREF_HALF 0): the existing F2 safe state |
| +5 V present, ±15 V absent | MCU and DAC powered; no analog output stage | No drive; INA outputs inside rails; nothing to protect |

Hardware added:

- **D8, D9 (MBR0540)**: AGND → +15VA and −15VA → AGND, reverse-biased in
  service. A dead rail is held within VF ≤ 0.51 V (onsemi data sheet, at
  ≤ 0.5 A) of AGND, so whatever reaches the DUT through the sense clamps is
  bounded by that drop, not by the live rail. Reverse leakage ≤ 10 µA at
  20 V, 25 °C (5 mA at a 100 °C junction), rail to AGND, outside every
  measurement path.
- **U12 (Toshiba TLP176AM photorelay)** in FORCE+, between R30 and D5/J4, its
  LED in a series string across the two rails:
  `+15VA → R39 620 Ω → LED → D7 BZT52C24 (reverse) → −15VA`.

| U12 | Condition | Basis |
|---|---|---|
| **Guaranteed open** | either rail absent (clamped to ≤ 0.51 V by D8/D9), the other ≤ 15.5 V: string ≤ 16.0 V < D7's 16.8 V leakage test point, so I ≤ 0.1 µA ≪ IFC (0.1 mA, the guaranteed-off LED current) | Diodes Inc. DS18004 (BZT52C24: IR ≤ 0.1 µA at VR 16.8 V), Toshiba TLP176AM IFC min, at 25 °C. Hot, the zener leakage rises (×64 at 85 °C is still 6 µA ≪ 0.1 mA): expected, not data-sheet |
| **Guaranteed closed** | rail-to-rail ≥ 28.9 V (±14.45 V): IF ≥ (28.9 − 25.6 − 1.4) / 626 Ω = 3.0 mA = IFT max | BZT52C24 VZ ≤ 25.6 V, TLP176AM VF ≤ 1.4 V and IFT ≤ 3 mA, at 25 °C |
| Not guaranteed either way | 16 V … 28.9 V total. Any such state has ≥ 16 V across U5, twice its 8 V minimum, so U5 is powered and regulating; the output is bounded by the passive envelope (§2.7) | |
| **Expected** | closes near 26 V total (VZ typ 24 V, IFT typ 1 mA) | typical values |
| Ratings | ±15.5 V: IF ≤ 11.6 mA (recommended 5–25 mA); D7 ≤ 0.26 W (rated 0.37 W at 25 °C); R39 ≤ 0.08 W; U12 off-state 60 V / ≤ 1 µA; on 0.7 A, RON ≤ 2 Ω | data sheets |

Sequencing: at power-up U12 cannot close until both rails are substantially
up (guaranteed open while either is within its clamp of AGND), and U5 is
powered and in control before it can close; at power-down it opens (tOFF
≤ 0.5 ms) before either rail collapses into a single-rail state. The DUT
therefore never sees U5's single-rail output. What is **not** removed:
D1–D4 can still pass up to ≈ 0.5 V into the Kelvin leads while a rail is
dead (bounded by the Schottky drop), and the U12 transition itself is a
bench measurement (checklist §5).

U12 sits outside U5's loop and outside the Kelvin sense: its ≤ 2 Ω costs
compliance only, and it is taken as 0 Ω in the fault bound. When open, the
DUT is disconnected from U5 but still connected to D5 (its TVS) and to the
Kelvin inputs; firmware sees the disconnected state as "V_meas ≠ V_cmd with
I = 0" (`firmware_handoff.md`).

Also: USB VBUS feeds only the PA0 sense divider and U10's clamp, so a USB
host cannot power any part of the analog chain with the bench supply off.

### 2.6 FORCE− protection

J7 used to land directly on R10's high side: FORCE− was a 10 Ω path to AGND.
The credible fault is an **external** source on J7 (a miswired bench lead,
an energised DUT, a DUT referenced to another instrument's ground). At the
rails' own 15.5 V that is 1.55 A and 24 W in R10, which also returns through
AGND → R31 (0 Ω, 0805) → DGND → the USB ground to the host.

The fix, and why each alternative was rejected:

| Option | Verdict |
|---|---|
| TVS or clamp from J7/ISENSE_HI to AGND | **Rejected.** Any part from the shunt node or from FORCE− to AGND is a current path around R10: unmeasured current at exactly the nA levels this instrument characterises. A TVS also cannot protect R10: it clamps far above R10's survivable voltage |
| A series resistor in FORCE− | **Rejected.** Same compliance cost as R30 again, for a fault R30 already bounds on the FORCE+ side |
| **Series PTC F2 + a shunt that survives the trip** | **Chosen.** F2 = 0ZRM0010FF1E (same part as F1): at 15.5 V the current is ≤ 15.5 / (3.0 + 9.99) = 1.19 A (< Imax 2 A); R10 carries ≤ 14.3 W, inside its 5 × 3 W = 15 W short-time rating. Expected trip ≈ 0.6 s at 1.2 A (Bel curve A, typical); the data sheet bounds trip time only at 0.5 A (≤ 10 s), so the short-time rating's 5 s is covered by the typical curve, not by a guarantee. Recalibrate R10 after any such event (ΔR ≤ 0.2 % + 0.05 Ω) |

Interactions:

- **Leakage around the shunt:** none. ISENSE_HI holds exactly R10, F2, U6
  +IN and TP3; FORCE− holds exactly J7 and F2. `validation/assertions.py`
  (`a_force_minus_chain`) fails the build if anything else is connected.
- **Kelvin sensing:** unchanged. SENSE− is taken at the DUT (J6); F2 and its
  3–7.5 Ω are outside the voltage measurement and in series with the current
  measurement (every ampere through F2 crosses R10).
- **Low-current corruption:** F2 is a series element; it adds no leakage
  path. Its resistance is irrelevant at nA–µA.
- **Grounds and USB:** AGND stays where it was; F2 limits the fault current
  that would otherwise return through R31 and the USB ground.
- **Signed current:** PTCs are symmetric.
- **FORCE+ side:** a DUT short still flows through F1, R30, U12, F2 and R10:
  ≤ 128 mA (§2.7), the same envelope as before.
- **U6 inputs:** at 1.19 A the shunt node reaches ≈ 12 V, inside the INA128's
  ±40 V input protection.
- **Compliance:** F2's ≤ 7.5 Ω costs ≤ 0.7 V at 91 mA.
- Sources above ≈ 26 V on J7 exceed F2's 2 A Imax; that is outside the
  board's ±15 V envelope and is not protected.

### 2.7 OPA548 current limit: what is bounded, and by what

SBOS070D gives `ILIM = 15000 × 4.75 / (13750 + R_CL)` and its accuracy
(±100 mA typ, ±250 mA max) **only at R_CL = 14.8 kΩ (2.5 A)**. At R7 =
1.43 MΩ the equation gives 49.4 mA. Nothing in the data sheet bounds the
error at that setting, so 49.4 mA is a **typical** plateau, not a limit.

The bound the protection relies on is passive. With U5's output driven into a
15.5 V rail (ILIM ineffective, the sweep saturated, or U5 failed), the
current is set by the series parts outside U5's loop at their **guaranteed
minima** — R9 −1 %, F1/F2 at 3.0 Ω (Bel Rmin), R30 −1 % and −250 ppm/K × its
150 K maximum hot-spot rise (= 95.3 Ω), U12 at 0 Ω (no minimum specified),
R10 −0.1 %:

| Fault | Bound (guaranteed) | Worst dissipation |
|---|---|---|
| DUT short (FORCE+ to FORCE−) | 15.5 / 121.2 Ω = **128 mA** | R10 0.16 W of 3 W |
| D5 shorted, or FORCE+ to AGND | 15.5 / 108.2 Ω = **143 mA** | R30 **1.96 W of 2 W (98 %)**, R9 0.21 W of 0.25 W |
| External 15.5 V on J7 | 1.19 A until F2 trips | R10 14.3 W of 15 W short-time |

`validation/measurement_range.py` recomputes all of this from the live part
values on every build, taking each resistor's worst dissipation over its own
tolerance range (V²R/(R + R_rest)², not the low end for the current and the
high end for the power at once), and fails if any part is under-rated.

Coordination and trade-offs:

- **R30 margin.** 98 % of rating needs every worst case at once: a 15.5 V
  rail, U5's output at that rail, FORCE+ shorted to ground, R30 at −1 % and
  at its hottest. 110 Ω would give 91 % at the cost of 0.5 V of compliance at
  the plateau and a smaller current range; 100 Ω was kept, and the margin is
  stated here and in `measurement_range.py` output.
- **F1/F2** hold 100 mA and trip at 200 mA (23 °C). The 128–143 mA envelope
  lies between: they may trip, and the design does not need them to.
- **U12** carries ≤ 143 mA against a 0.7 A rating; its RON (≤ 2 Ω) only
  adds compliance loss.
- **Sequencing.** With a rail absent U12 is guaranteed open, so the current
  limit question does not arise in partial-supply states.
- **What firmware may assume:** the plateau is ≈ 49 mA *typically*; the
  measurement (I channel, ±97.8 mA guaranteed) covers the whole passive
  range of a passive DUT (±91.3 mA), so a plateau anywhere up to that is
  still measured correctly. Bring-up records the actual plateau at both
  polarities.

### 2.8 Identifier snapshot

`tools/audit_identifiers.py` printed the absolute library paths of the
machine that ran it, which is why the "after" snapshot was exempt from
`tools/check_no_abs_paths.py` and was never refreshed. The generator now
prints repo-relative paths for repository files and
`${KICAD9_SYMBOL_DIR}` / `${KICAD9_FOOTPRINT_DIR}`-rooted paths for library
files (the library revision printed in the same report identifies the
content), and counts the schematic-only PWR_FLAGs separately from board
parts instead of as "unresolved footprints". The regenerated report shows U5
as `RESOLVED`, 141 parts (132 board parts, 9 PWR_FLAGs), 0 unresolved
symbols or footprints, 0 unaccounted pads. The file is no longer exempt.
`docs/audit_identifiers_before.md` stays exempt as a dated record.

---

## 3. Validation

Environment: SKiDL 2.2.3, KiCad libraries 9.0.9.1 (pinned), kicad-cli
10.0.7. Every step of `.github/workflows/build.yml` was replayed locally on
the final tree (the "nothing regenerated is dirty" step aside: nothing is
committed; the determinism step stands in for it).

| Check | Result |
|---|---|
| `python3 main.py` | PASS: 141 parts (132 board + 9 PWR_FLAG), 77 nets; board 132 footprints + 3 mounting holes, 897 tracks, 113 vias, 0 unconnected |
| `validation/assertions.py` (18, incl. the four new: FORCE+ chain, FORCE− chain, U12 needs both rails, dead-rail clamps) | PASS |
| `validation/measurement_range.py` | PASS: V ±11.78 V and I ±97.8 mA guaranteed vs ±10.04 V / ±91.3 mA reachable; fault 128 / 143 mA, R30 1.96 W of 2 W, R9 0.21 of 0.25 W, R10 0.16 of 3 W; J7 external 1.19 A, R10 14.3 of 15 W short-time |
| `validation/erc.py` (SKiDL ERC) | PASS |
| `validation/schematic_check.py`, `tools/kicad_roundtrip.py` | PASS: 368 connections (359 without the PWR_FLAG pins), identical in KiCad's own netlist export |
| `tools/kicad_erc_power.py`; `kicad-cli sch erc --exit-code-violations` | PASS; KiCad ERC 0 violations |
| `validation/footprint_check.py`, `tools/vendor_footprints.py --check`, `tools/author_project_symbols.py --check`, `tools/gen_project_config.py --check`, `tools/check_kicad_loads.py` | PASS |
| `validation/routed_board_check.py` | PASS: 16/16 checks, KiCad DRC below |
| `validation/update_pcb_noop.py` (+ `--self-test`), `tools/reproduce_reference.py` | PASS: Update PCB from Schematic changes nothing; the layout source still regenerates the original board |
| Fab conformance, pad gaps | PASS |
| Determinism (two full builds compared) | PASS: byte-identical (netlist timestamp aside) |
| `python3 main.py --autoplace` (bootstrap board) | PASS |
| `tools/audit_identifiers.py` | `docs/audit_identifiers_after.md` equals a fresh run; 141 parts, 0 unresolved, 0 unaccounted pads |
| `tools/check_no_abs_paths.py` | PASS on the tracked files, and the new, still-untracked files scanned with the same rules: clean |
| No-KiCad build (CI job `build-without-kicad`, in a copy) | PASS: every KiCad layer reported SKIPPED / NOT RUN; board untouched |

KiCad DRC with zones refilled (from `routed_board_check.py`):

| Rules | Errors | Warnings | Unconnected | Parity |
|---|---|---|---|---|
| As-built (0.20 mm + scoped intra-footprint rules, every check on) | 0 | 2 `silk_edge_clearance` (J3), also on the original | 0 | — |
| Repository netclass policy | **0** | the same 2 | **0** | 0 |

Smallest copper gap per governing class on the final board: POWER_HV 0.300,
SENSE 0.250, FORCE 0.250, LOCAL_ANALOG 0.250, DIGITAL / POWER_LV / GROUND /
USB 0.200 mm, each exactly at (never below) its class minimum.

Placement heuristics (courtyard, edge, ground split, hole spacing,
silkscreen): 140 findings, every one also on the original; none new.

Fault injection (`tools/test_guard_fails.sh`): six cases added for this
round — F2 bypassed, U12 LED string returned to AGND, D9 reversed, R10 back
to 0.5 W, `policy_clearance.eco` dropped, H1 placed on routed copper — each
caught by its gate and by `main.py`. Full suite: **33 of 33 injected faults
caught**.

---

## 4. Remaining risks and bring-up requirements

Nothing in this round has been built or measured. What follows is what the
first boards must show, in the order the bring-up checklist does it
([`bringup_checklist.md`](bringup_checklist.md)).

**Before energising a board**

- D8, D9, D7 and U12 fitted the right way round (D7 and D9 are on the
  **bottom**). A reversed D8 or D9 is a dead short across its rail: the
  unpowered diode test (checklist §2) catches it.
- Unpowered resistances: J4 to R30 pad 2 open (U12 off); F1 and F2 each
  3.0–7.5 Ω; TP3 to AGND ≈ 10.0 Ω.
- R30 raised ≈ 3 mm; R10 flat with symmetric leads; U5's tab isolated from
  any metal; mounting hardware within the 7.0 mm washer / 6.35 mm standoff
  envelope, J1 corner supported.
- Current-limited bench supplies, no DUT, 1 kΩ dummy load.

**Before connecting a DUT**

- U12's LED current (R39 drop ≈ 4.8 V; below 1.9 V U12 is not guaranteed
  on), and U12 actually closing.
- The partial-supply cases of checklist §5: each ±15 V rail alone (dead rail
  within its Schottky drop of AGND, ≤ 0.5 V across the load), USB only, the
  U12 close/open thresholds (expected ≈ 26 V total; must lie in 16–28.9 V),
  and the switching transients in every supply order.
- The reference-gate arm/disarm tests and the safe-state residual
  (checklist §3, §6), unchanged from the audit round.
- The current-limit plateau at both polarities (49 mA is typical only) and
  the snubber decision.

**Before trusting measurements**

- Zero and gain calibration of both channels (checklist §8).
- R10's thermal EMF (zero offset after a high-current dwell).
- F1/F2 temperature in the 30 min plateau soak: neither may trip.
- Firmware: the open-Kelvin cross-check with F2 and U12 in the force-side
  estimate, and the U12-open state reported as "output disconnected"
  (`firmware_handoff.md` §5).

**Residual design risks (accepted, stated)**

| Risk | Why it stays | Where it is watched |
|---|---|---|
| R30 at 98 % of rating in the stacked worst-case rail fault | Every worst case at once (15.5 V rail, U5 at that rail, FORCE+ on AGND, R30 −1 % and hot). 110 Ω would give 91 % at a compliance cost | `measurement_range.py` fails the build if it exceeds 100 % |
| ILIM is typical only (49.4 mA) | TI specifies accuracy only at 2.5 A | Bring-up records the plateau; nothing relies on it |
| F1/F2 may or may not trip at 128–143 mA | Between hold (100 mA) and trip (200 mA), where the data sheet promises neither | Not relied on; R30 is the bound |
| F2 trip time at ≈ 1.2 A, and R10's 5 s overload | Bel bounds trip time only at 0.5 A (≤ 10 s); 1.2 A ≈ 0.6 s is the typical curve | Recalibrate after any J7 event |
| U12 thresholds and D7 leakage are 25 °C data-sheet limits | Neither part is specified hot for these parameters; the hot zener leakage (≈ 6 µA at 85 °C) is an extrapolation, still ≪ 0.1 mA | Checklist §5 measures the thresholds |
| D1–D4 can still pass ≤ 0.5 V into the Kelvin leads while a rail is dead | Bounded by D8/D9's drop, not removed | Checklist §5 records it |
| U12 is not under firmware control | A firmware-controlled disconnect needs a driver and a pin; out of scope. "Zero" stays a 0 V setpoint | `firmware_handoff.md` |
| No fourth mounting hole | No 4.25 mm free radius in the top-right quadrant | Three-point mount + panel jacks |
| R10 RS02B is wound (inductive) and its thermal EMF is unspecified | The non-inductive NS02B10R00BE12 was not found listed | Flat mounting; checklist §8 measures the EMF |
| R10 availability | Seen at a non-authorised distributor only | Confirm with Vishay or an authorised channel before ordering |
| External sources above ≈ 26 V on J7 exceed F2's 2 A Imax | Outside the board's ±15 V envelope | — |
| 22 of the 109 clearance errors were resolved by correcting four nets' class, at exactly the corrected class minimum (0.200 mm) | Those nets are logic or supply nodes (§2.3) | Recorded at the class definitions in `netclasses.py` |
| DRC and the board checks ran on KiCad 10.0.7 only; the courtyard/silkscreen heuristics are approximate; no fabrication-house DFM | — | Gerber review and DFM before ordering |
