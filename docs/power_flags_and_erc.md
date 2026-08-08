# Power flags and the ERC gate

## 1. What was wrong

`blocks/nets.py` stamped nine supply rails with `Net.drive = POWER` and the
module docstring called it "the electrical equivalent of a KiCad PWR_FLAG".

It was not. `Net.drive` is a runtime Python attribute read only by
`skidl.ERC()`. It is never serialised into `stage1_iv.net` and never emitted
into any `.kicad_sch` — the outputs contained **zero power symbols of any
kind**. The line did exactly one thing: stop SKiDL from reporting the undriven
rails. KiCad, which never saw it, reported every one of them.

Reproduced before touching anything, by setting `_POWER_RAILS = []` and
re-running:

```
ERC WARNING: Insufficient drive current on net +5V_RAW for pin POWER-IN pin 3/VIN of LD1117S33TR_SOT223/U1.
ERC WARNING: Insufficient drive current on net DGND    for pin POWER-IN pin 17/DGND of ADS1256/U8.
ERC WARNING: Insufficient drive current on net VDDA    for pin POWER-IN pin 13/VDDA of STM32F411RETx/U9.
ERC WARNING: Insufficient drive current on net -15VA   for pin POWER-IN pin 4/V- of INA128/U6.
ERC WARNING: Insufficient drive current on net +5VA    for pin POWER-IN pin 1/VDD of DAC8552/U4.
ERC WARNING: Insufficient drive current on net +15VA   for pin POWER-IN pin 8/V+ of OPA2277/U3.
ERC WARNING: Insufficient drive current on net AGND    for pin POWER-IN pin 8/GND of DAC8552/U4.
... 20 warnings total, covering 7 distinct nets
ERC INFO: 20 warnings found while running ERC.
```

SKiDL knew. It had been told not to say.

Three further defects in the ERC layer, all of which meant a SKiDL ERC pass was
uninformative regardless:

| location | defect |
|---|---|
| `validation/erc.py:57` | `ERC()` called, result discarded; `erc_logger.error.count` never read |
| `validation/erc.py:76` | `ok = not missing_fp and not dup_refs` — the gate was insensitive to the ERC result |
| `validation/erc.py:82` | printed `ERC structural checks: PASS` unconditionally |
| `main.py:202` | printed `ERC ... PASS` as a literal, with `erc_summary` in scope one line above |
| `README.md:105,131` | claimed "0 ERC errors, 0 ERC warnings" and that the ERC covered "missing power flags" — the exact check that was disabled |

## 2. The eight flags

Each rail's energy comes from outside the board, so nothing on the board can
drive it. Each carries exactly one `power:PWR_FLAG` — a real symbol with a
`power_out` pin, serialised into both the netlist and the schematic —
instantiated at the point where that rail physically enters the design.

| Net | Physical source | Flag | Attached at | Justification |
|---|---|---|---|---|
| `+15VA` | bench supply, J1 pin 1 | `#FLG1` | `blocks/power.py:97` | J1 is a screw terminal; its pins are PASSIVE, so the +15 V rail it brings in has no driver on this board. |
| `-15VA` | bench supply, J1 pin 2 | `#FLG2` | `blocks/power.py:98` | Same connector, same reason, negative rail. |
| `+5V_RAW` | bench supply, J1 pin 3 | `#FLG3` | `blocks/power.py:99` | Same connector; this is the rail U1 regulates down to +3V3D. |
| `DGND` | bench supply return, J1 pin 4 | `#FLG4` | `blocks/power.py:100` | The supply return enters on the digital side; every DGND pin on the board is `power_in` or passive. |
| `+5VA` | `+5V_RAW` through ferrite bead FB1 | `#FLG5` | `blocks/power.py:107` | FB1 is a passive two-terminal part, so `+5VA` is a distinct net with no driver even though `+5V_RAW` is flagged. |
| `AGND` | analog return, referenced at the R31 star point | `#FLG6` | `blocks/power.py:117` | AGND meets DGND only through the passive 0 Ω R31, so KiCad treats it as a separate net needing its own flag. Flagging it does not merge the planes. |
| `VBUS` | USB host, through J3 | `#FLG7` | `blocks/mcu.py:163` | Supplied by the host at the far end of the cable; every VBUS pin on this board (J3's four pads, U10's clamp, R28, C42) is passive or an input. |
| `VDDA` | `+3V3D` through ferrite bead FB2 | `#FLG8` | `blocks/mcu.py:170` | FB2 is passive, so the MCU's analog supply is a distinct net from the flagged-by-driver `+3V3D`. |

### Nets deliberately NOT flagged

| Net | Why not |
|---|---|
| `+3V3D` | U1 (LD1117S33) `VOUT` is a genuine `power_out` driver. A flag here would mask a future regulator wiring error — exactly the failure the rule exists to catch. |
| `VREF_2V5` | U2 (REF5025ADGK) `Vout` is a genuine `power_out` driver. Same reasoning. |
| `VCAP1` | U9's internal-LDO output pin is `power_out`; the net has no `power_in` pin at all, so the rule does not apply. |
| every signal net | No `power_in` pins, so `power_pin_not_driven` cannot fire. |

The distinction is enforced, not just documented:
`validation/erc.py::check_power_flags` hard-fails on (1) a rail declared
externally-sourced in `blocks/nets.py::EXTERNALLY_SOURCED_RAILS` with no flag,
(2) a flag on a net not in that table, (3) two flags on one net, and (4) a flag
on a net that already has a real `power_out` driver.

## 3. The ERC gate

`validation/erc.py::run_erc` now:

* samples `erc_logger`'s error/warning counters either side of `ERC()` **and**
  captures every message text, cross-checking the two so a change in SKiDL's
  counter semantics surfaces as a loud mismatch rather than a silent zero
  (`Circuit.ERC()` resets those counters itself, so the post-call value — not a
  subtraction — is this run's count);
* returns both counts in the summary dict;
* **raises** on any error;
* **raises** on any warning that does not match `WARNING_ALLOWLIST`, which is
  **empty**;
* prints the actual numbers, never a literal "PASS".

`main.py` derives its summary line from `erc_summary` and exits non-zero if
either count is non-zero. It can also be run standalone: `python3 validation/erc.py`.

## 4. Demonstration: the gate failing, and passing again

`tools/test_guard_fails.sh` copies the repo to a scratch directory, injects one
real defect at a time, and asserts that the responsible gate **and** `main.py`
both exit non-zero. It never touches the working tree. Full output:

```
======================================================================
GUARD FAULT-INJECTION TEST
======================================================================
  [CAUGHT] bad symbol lib_id (Amplifier_Instrumentation:INA128P)
           validation/footprint_check.py=1, main.py=1
  [CAUGHT] bad footprint id (Fuse:Fuse_Bourns-MF-RG300)
           validation/footprint_check.py=1, main.py=1
              [ERROR] F1: footprint 'Fuse:Fuse_Bourns-MF-RG300' does not exist ...
  [CAUGHT] Pins_Verified == PLACEHOLDER-REMAP
           validation/footprint_check.py=1, main.py=1
  [CAUGHT] pad left unconnected (ADS1256 AIN4..AIN7, D0..D3)
           validation/footprint_check.py=1, main.py=1
  [CAUGHT] power pin left unconnected (U6.V+ off +15VA)
           validation/erc.py=1, main.py=1
              [ERC WARNING] Unconnected pin: POWER-IN pin 7/V+ of INA128/U6.
            ERC FAILED: 1 un-allow-listed ERC warning(s)
  [CAUGHT] PWR_FLAG removed from +5VA
           validation/erc.py=1, main.py=1
              [ERC WARNING] Insufficient drive current on net +5VA for pin
                            POWER-IN pin 1/AVDD of ADS1256/U8.
  [CAUGHT] PWR_FLAG added to +3V3D, which U1.VOUT already drives
           validation/erc.py=1, main.py=1
            ERC FAILED: 1 SKiDL ERC error(s); PWR_FLAG invariant:
            ["net '+3V3D' carries a PWR_FLAG (['#FLG9']) but is not declared in
              blocks/nets.py::EXTERNALLY_SOURCED_RAILS",
             "net '+3V3D' has a PWR_FLAG *and* a real driver ['U1.VOUT'];
              remove the flag — it would mask a driver conflict"]
  [CAUGHT] pin numbers not a subset of pads (USB-C on an 8-pad footprint)
           validation/footprint_check.py=1, main.py=1
----------------------------------------------------------------------
ALL INJECTED FAULTS CAUGHT — the guard works.
```

Restored (i.e. the committed tree), every gate passes:

```
  python3 main.py                            exit=0
  python3 validation/erc.py                  exit=0
  python3 validation/footprint_check.py      exit=0
  python3 validation/schematic_check.py      exit=0
  python3 tools/kicad_erc_power.py           exit=0
  python3 tools/kicad_roundtrip.py           exit=0
```

## 5. Before / after

### KiCad power-driver rules

Measured by `tools/kicad_erc_power.py`, which evaluates KiCad's rules directly
on the emitted `.kicad_sch` files. **See §6 — this is a re-implementation of
the rule, not a KiCad ERC run.**

| violation type | before | after |
|---|---|---|
| `power_pin_not_driven` | **7** | **0** |
| driver conflicts (>1 `power_out` on a net) | 0 | 0 |
| driven power nets | 2 (`+3V3D`, `VREF_2V5`) | 9 (those two + the eight flagged rails) |
| `lib_symbol_issues` "library not found" | 12 symbol libraries referenced | 13 (`power` added) — **out of scope** |
| `lib_symbol_mismatch` | up to 24 distinct `lib_id`s | up to 25 (`power:PWR_FLAG` added) — **out of scope** |
| `footprint_link_issues` | 0 | 0 |
| "Symbol X not found in symbol library Y" | 0 | 0 |

The seven `power_pin_not_driven` errors before the change, with the pins KiCad
would have named:

```
+15VA    power_in pins ['U6.7', 'U3.8', 'U5.5', 'U7.7']      with no power_out
+5VA     power_in pins ['U8.1', 'U4.1', 'U2.2']              with no power_out
+5V_RAW  power_in pins ['U1.3']                              with no power_out
-15VA    power_in pins ['U6.4', 'U3.4', 'U5.4', 'U7.4']      with no power_out
AGND     power_in pins ['U8.2', 'U4.8', 'U2.4']              with no power_out
DGND     power_in pins ['U8.17', 'U9.12', 'U9.18', 'U1.1']   with no power_out
VDDA     power_in pins ['U9.13']                             with no power_out
```

**Correction to the brief.** The brief lists eight offending rows, including
`J3 pin A1 / DGND` and `J3 pin A4 / VBUS`. That table predates the merged
identifier work: with the official `Connector:USB_C_Receptacle_USB2.0_16P`
symbol, J3's GND and VBUS pads are `passive`, not `power_in`. So

* `VBUS` has **no** `power_in` pin at all and does not trigger the rule today.
  It is still flagged, because it is still externally sourced with no driver and
  a flag there is what keeps the error from appearing the moment anything with a
  `power_in` VBUS pin is added.
* `DGND` still triggers, but via `U8.17 / U9.12 / U9.18 / U1.1`, not `J3.A1`.

So the count was 7, not 8, and 8 flags are still the right answer.

### SKiDL ERC

| | before | after |
|---|---|---|
| suppression | `Net.drive = POWER` on 9 rails | none — `blocks/nets.py` has no `.drive` assignment and no `_POWER_RAILS` |
| errors | 0 (result discarded) | **0 (result read and gated)** |
| warnings | 0 (20 suppressed) | **0 (none suppressed; allow-list empty)** |
| gate sensitivity to ERC | none | error ⇒ fail, un-allow-listed warning ⇒ fail |

### Everything else

| | before | after |
|---|---|---|
| parts | 110 | 118 (+8 virtual `#FLG`) |
| nets | 66 | 66 — **net names unchanged** |
| netlist connections | 307 | 315 |
| `grep -c PWR_FLAG stage1_iv.net` | 0 | **16** |
| power symbols in `.kicad_sch` | 0 | 8 instances, `(power)` attribute on the lib symbol |
| assertions | 9/9 PASS | 9/9 PASS |
| `schematic_check.py` | PASS (307) | PASS (315) |
| KiCad round-trip | PASS | PASS |
| two runs byte-identical | yes | yes |

## 6. ERC severity settings — unchanged, and there are none

No ERC rule was suppressed, downgraded or excluded.

```
$ git ls-files | grep -E "\.kicad_pro|\.kicad_prl|erc.*json|severit"
  (none tracked)
$ ls *.kicad_pro *.kicad_prl
  (none present on disk)
$ git diff origin/main -- '*.kicad_pro' '*.kicad_prl'
  (empty)
```

The repository contains no KiCad project file and therefore no
violation-severity configuration, before or after. The `power_pin_not_driven`
count went to zero because the nets acquired drivers, not because the rule
stopped being enforced.

## 7. What could not be done

`kicad-cli sch erc` first shipped in **KiCad 8**. KiCad **7.0.11** is the newest
version installable in this environment — the KiCad PPA's file host
(`ppa.launchpadcontent.net`, `launchpadlibrarian.net`) is refused by the network
egress policy, as are flathub, snapcraft, opensuse and `downloads.kicad.org`.
**The KiCad ERC dialog was never run, and no GUI session or PCB update was
performed.** Nothing above is reported as a KiCad ERC result.

What was run instead, all reproducible:

* **`tools/kicad_erc_power.py`** — parses the emitted `.kicad_sch` files (the
  artifact KiCad's ERC reads), resolves each pin to its net through the
  schematic's own labels and wire stubs, and applies KiCad's `power_pin_not_driven`
  and driver-conflict rules to the schematic's own pin electrical types. It
  reads the schematic rather than the netlist deliberately: KiCad omits
  `#`-prefixed power symbols from netlist export, so a netlist-based measurement
  would not see the PWR_FLAGs and would report a false positive.
* **`tools/kicad_roundtrip.py`** — has KiCad 7.0.11 itself export a netlist from
  the schematic and compares it against `stage1_iv.net`: 307 non-virtual
  connections in the same 66 nets.
* **`validation/erc.py`** — the SKiDL ERC gate.
* **`tools/test_guard_fails.sh`** — the fault injection above.

`lib_symbol_issues` / `lib_symbol_mismatch` remain and are out of scope per the
brief; counts are in §5.
