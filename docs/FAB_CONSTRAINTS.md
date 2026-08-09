# Fabrication constraints

Every numeric board-setup value in this project cites a line from this file.
`libraries/netclasses.py` names the row it derives each number from, and
`generators/pcb.py` refuses to emit a board whose global minimum clearance is
below `FAB.min_clearance`.

## Target capability

**No fabricator has been chosen yet.** The values below are a deliberately
conservative default that essentially every 2-layer prototype service meets
(JLCPCB, PCBWay, Aisler, OSH Park 2-layer, Eurocircuits standard pooling).
They are an **ASSUMPTION TO BE REVISITED** once a fabricator is selected — at
which point only this table changes and the board regenerates.

| ID | Parameter | Value | Note |
|---|---|---|---|
| **F1** | Layer count | 2 (F.Cu, B.Cu) | Sufficient for this part count; see §"Why 2 layers" |
| **F2** | Copper weight | 1 oz (35 µm) finished, outer | standard prototype default |
| **F3** | Minimum track width | **0.15 mm** (≈ 6 mil) | conservative; most houses do 0.127 mm (5 mil) |
| **F4** | Minimum clearance (copper-copper) | **0.15 mm** (≈ 6 mil) | conservative; most houses do 0.127 mm |
| **F5** | Minimum drill (through) | **0.3 mm** | standard mechanical drill floor |
| **F6** | Minimum annular ring | **0.15 mm** | ⇒ min via pad = drill + 2×0.15 |
| **F7** | Minimum via | 0.3 mm drill / 0.6 mm pad | from F5 + F6 |
| **F8** | Copper to board edge | **0.5 mm** | routing/V-score tolerance |
| **F9** | Silkscreen minimum line width | 0.15 mm | |
| **F10** | Surface finish | ENIG or HASL | not constraining for this design |
| **F11** | Minimum hole-to-hole (edge-edge) | **0.25 mm** | see the correction below |
| **F12** | Board thickness | 1.6 mm | affects the creepage table below |

## Working voltages on this board

| Net group | Max working voltage | Worst-case rail-to-rail |
|---|---|---|
| `POWER_HV` (`+15VA`, `-15VA`) | ±15 V to ground | **30 V** between `+15VA` and `-15VA` |
| `FORCE` path (DUT drive) | ±11 V compliance | 22 V |
| `POWER_LV` (`+5V_RAW`, `+5VA`, `+3V3D`, `VDDA`, `VBUS`) | 5 V | 5 V |
| everything else | ≤ 5 V | 5 V |

### Creepage/clearance derivation for the ±15 V rails

IPC-2221B Table 6-1, internal/external conductors, uncoated, B1 external
(< 3050 m), for 16–30 V DC or AC peak: **0.13 mm minimum**. That is *below*
this board's fabrication floor (F4 = 0.15 mm), so IPC is not the binding
constraint at these voltages — the fab process is.

The `POWER_HV` clearance of **0.30 mm** is therefore chosen for margin rather
than compliance: 2× the fab floor, which keeps the ±15 V nets comfortably clear
of adjacent low-voltage copper and of each other under solder-mask and
assembly-residue variation. It is *not* derived by trial and error against DRC.

## The binding geometric constraint on this board

Two footprints have an **intrinsic pad-to-pad gap of 0.15 mm**, which no
routing decision can change — the next tightest is the LQFP-64 at 0.20 mm:

| Footprint | Used by | Pitch | Pad gap |
|---|---|---|---|
| `Package_SO:VSSOP-8_3x3mm_P0.65mm` | U2 (REF5025ADGK), U4 (DAC8552) | 0.65 mm | 0.15 mm |
| `Connector_USB:USB_C_Receptacle_GCT_USB4085` | J3 | 0.85 mm | 0.15 mm |

Measured, not assumed — `tools/measure_pad_gaps.py` reads the footprint files
and reports the minimum gap for every footprint the design uses.

**Consequence:** the board's global minimum clearance must be **≤ 0.15 mm**, or
these standard footprints are un-buildable as drawn. See
`docs/pcb_generation.md` §"Global clearance" for the chosen value and its
margin.

### The same constraint on drilled holes

J3's `Connector_USB:USB_C_Receptacle_GCT_USB4085` is a 0.85 mm-pitch through-
hole receptacle with 0.40 mm drills, so adjacent holes sit **0.45 mm** apart
edge to edge. Measured by `tools/measure_pad_gaps.py`, same as the pad gaps.

**F11 was originally written as 0.5 mm and that was wrong.** A KiCad DRC run on
a board using this table reported 14 `hole_to_hole` warnings — "min 0.4995 mm;
actual 0.4500 mm" — every one of them between two pins of J3. The corrected
reasoning: a USB-C receptacle is a mainstream, high-volume part that prototype
houses fabricate constantly. If the assumed capability says it cannot be built,
the assumption is what is wrong, not the connector. F11 is therefore 0.25 mm,
which is the usual mechanical-drill registration floor and which the part
clears with margin.

This is a correction to an assumption, not a relaxation to silence a rule: had
the measured 0.45 mm fallen genuinely below a *known* fabricator limit,
`check_against_fab()` would refuse to build and the right answer would have
been a different connector.

## Why 2 layers

118 parts, of which 8 are virtual power flags; 110 physical. The dominant
routing pressure is the LQFP-64 (U9) and the SSOP-28 (U8). A 2-layer board with
poured ground on both sides is adequate at 0.65 mm and 0.5 mm pitch with 0.15 mm
tracks, and keeps the prototype cost at the lowest tier. If routing proves
infeasible during the (not-yet-done) routing phase, moving to 4 layers changes
F1 here and nothing else in the generator.

## Revisiting this file

When a fabricator is chosen:

1. Replace the table above with their published capability.
2. Re-run `python3 main.py` — netclasses, board setup and the outline all
   regenerate.
3. Re-run `python3 validation/drc_check.py`.

`generators/pcb.py` hard-fails if any netclass requests a track, clearance,
drill or annular ring below the values in this file, so a capability downgrade
cannot silently produce an unmanufacturable board.
