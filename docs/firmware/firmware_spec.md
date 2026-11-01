# Stage 1 I–V Characterization System — Firmware Specification

**Status:** specification of firmware v0.1 (the implementation in `firmware/`
and `host/`). Written before implementation, then reconciled with the code;
where the two differ the code and [`architecture.md`](architecture.md) are
authoritative and the difference is listed in §15.
**Baseline hardware:** `hkr-ypt/stage1-iv-skidl` at `8a84b55` (SKiDL source,
netlist and routed board unchanged by the firmware work).
**Scope:** the firmware for U9 (STM32F411RET6) on this board, and the host
software needed to use and test it.

The words **must / never** mark normative requirements. Evidence labels
([Src], [DS], [Sim], [Calc], [Bench]) are defined in §4. Performance figures
are **provisional engineering targets**, not measured results (§14).

---

## 1. Repository state established

| Item | Finding |
|---|---|
| Firmware | **None.** No firmware source, toolchain configuration, host software or protocol definition exists. The MCU pin assignment in `blocks/mcu.py` is the only firmware interface definition |
| Hardware source of truth | SKiDL (`blocks/*.py`, `main.py`) generates the netlist, the nine schematic sheets, the manifest and the routed board. CI (`.github/workflows/build.yml`, jobs `build` and `build-without-kicad`) gates ERC, DRC, measurement range, the fault envelope, determinism and the path guard |
| Latest hardware state | `docs/final_corrections.md` (round 2) on top of `docs/audit_corrections.md` (round 1); the summary is `docs/changes_since_bipolar.md`. Relevant additions: reference gate U11 + heartbeat pump on PC5, midpoint from DAC channel B, /SYNC pull-up R34, dead-rail clamps D8/D9, rail-dependent disconnect U12, F2 on FORCE− |
| Firmware requirements already written | `docs/firmware_handoff.md` (safe state, heartbeat, DAC contract, ADS1256 setup, calibration, USB, pins) and `docs/bringup_checklist.md` (§6 onward needs test firmware) |
| Built or measured hardware | **None.** Every gate, pump, plateau, U12 and thermal number is calculated or simulated (`final_corrections.md` §4) |
| Cross-checks done for this plan | (1) Every MCU-connected net in `stage1_iv.net` against `blocks/mcu.py` and the LQFP-64 pinout: PA0 14, PA4 20, PA5 21, PA7 23, PC5 25, PB12–15 33–36, PA8 41, PA9 42, PA11/12 44/45, PC13 2 — all agree. (2) DAC8552 and TMUX1101 package pinouts against SLAS430A and SCDS410D: agree. (3) Every firmware-relevant claim in `firmware_handoff.md` against the datasheets (§4, §11). (4) A new numerical simulation of the heartbeat pump (§4.4) |

How the work was done: four independent investigations (DAC8552/TMUX1101,
ADS1256, STM32F411 + TinyUSB, a pump simulation), reconciled here. One of
them overturned an assumption I had made: that fast toggling starves the
pump (§4.4). Two adversarial reviews of this draft followed (§12).

## 2. Summary of the major decisions

| Area | Decision | Main reason |
|---|---|---|
| Execution model | Bare-metal cooperative super-loop, no RTOS. Every service is non-blocking, ≤ 70 µs per call, ≤ 150 µs per pass | The hardware's safety argument needs "the control loop completed" and "the heartbeat toggled" to be the same fact (§7.1) |
| Heartbeat | One thread-mode edge generator on PC5, gated by the per-pass health predicate. **2 kHz on a fixed 250 µs grid**, never once per pass. Any edge spacing > 1 ms → fault. A 2 ms dead-man timer zeroes the DAC and resets on a hang. `T_ARM` = `T_DISARM` = 100 ms | Pump simulation: an uneven duty, or a sustained slow loop, starves the pump; worst-case disarm is 77 ms (§4.4) |
| DAC | Every update is the atomic pair `00 A` + `34 FFFF`. B is never a parameter. 100 ms scrub in every armed state. /SYNC rises only after BSY = 0. A frame-aligned `dac_zero_safe()` (with SPI-reset and bit-bang fallbacks) serves every fault path | Makes the positive-bound contract structural and self-healing; the DAC has no readback |
| Safe sequences | Zero code before stopping the heartbeat; early-boot, fault-handler and dead-man zero; never re-arm after any reset; armed only in a host session; BOR level 3 required to arm | §3, §6 |
| ADC | Mux-cycled settled conversions, I first. PGA 2 on both channels, buffer off. Data rates ≤ 3750 SPS. Stale-edge rejection and a read deadline outside t17. SELFCAL on every rate change (DISARMED only). Register scrub ≥ 1/s and a stuck-code check. Read-back, not the ID, as the comms test | The INA128, not the ADC, limits the low end (≤ 21 % σ gain from any PGA); SBAS288K t17/t18/Table 21 (§4.5, §7.4) |
| Measurement | Fixed per-point procedure: settle (with a preemptible monitor) → I,V,V,I conversions → checks → record. Integer-µV setpoints on an exact grid. µs timestamps for both channels | Repeatability, drift-unbiased V/I pairing, identical setpoints on up and down branches |
| Sweeps | Waypoint sweeps (linear, dual, hysteresis, cycles) and list sweeps (≤ 2048 points), generated on the fly; pre-ramp and end ramp; validated before running | One small engine covers every v1 shape |
| Data path | 1024-record ring. The sweep never waits for USB. Overflow aborts loudly. `DATA.FETCH` re-sends. Raw codes plus SI values, with the calibration and session zero in `SWEEP_BEGIN` | No silent loss, no silent timing change, reprocessable data |
| Host link | USB CDC-ACM (TinyUSB 0.21.0), ASCII line protocol v1. Session = VBUS measured on PA0 + configured + DTR, plus a 30 s host timeout | Driverless and scriptable; any host death disarms (§8) |
| Checks | Software compliance (post-hoc); plateau detected by current **and** load-line departure; Kelvin consistency with R_top/R_bot; force-path open; AFE signature (not current) before arming; staleness | The handoff's open-Kelvin and U12 requirements made concrete, with stated detection limits (§7.7) |
| Calibration | Linear model per channel, plus the DAC and R_s/R_top. One jacks-open `ZERO` per session (RAM). Gains in flash sectors 1/2 (A/B), written only when disarmed. Nominal constants derived from the component table | §7.10 |
| Watchdog | IWDG 1.0–2.8 s, kicked by every healthy pass including in FAULT. **Not** part of output safety | The gate (52–77 ms), dead-man (2 ms) and fault handlers make the output safe whatever the IWDG does |
| Toolchain | C11, arm-none-eabi-gcc 13.2 (pinned runner), CMake + Ninja, CMSIS device headers (no HAL), TinyUSB, Unity, Python host package | §5 |
| Verification | Unit tests (Unity) and whole-firmware simulation against a board model, driven by the real host library, in CI. Link-level checks of the single-writer rules. Bench levels L3–L5 mapped onto the bring-up checklist | §9 |

## 3. Safety model

### 3.1 Division of responsibility

The hardware forces the **setpoint** to ≈ 0 V within 52–77 ms (simulated,
§4.4) whenever the heartbeat stops. It disconnects FORCE+ when a ±15 V rail is missing. It
bounds current passively (128 mA DUT short, 143 mA FORCE+ to AGND). Everything
else is firmware. In particular, **the positive output bound is a firmware
contract**: channel B must stay at 0xFFFF.

| Hazard | Hardware mitigation | Firmware obligation |
|---|---|---|
| Output holds a voltage after MCU reset, halt, crash or hang | Reference gate opens 52–77 ms after the last heartbeat edge | Early-boot DAC zero (§7.2); fault-handler and 2 ms dead-man zero (§7.9); never re-arm after reset (SI-6) |
| MCU running out of specification (VDD sag) next to a powered DAC and gate (+5VA can outlast +3V3D) | none | BOR level 3 required to `ARM` (§6.2) |
| V_SWEEP above +10.04 V (up to +13.8…15 V) | **none** | SI-1: B = 0xFFFF in every frame, rewritten on every update |
| Main loop hung but an ISR or peripheral still toggling PC5 | PC5 has no timer or peripheral function | SI-2, SI-10: one thread-mode writer, gated by health checks |
| Heartbeat present but the firmware is commanding the wrong thing | none | SI-5, SI-9 and the test suite. **Residual risk:** an in-range wrong setpoint from a live, logically faulty loop is not caught by hardware |
| DUT left biased with no operator | none | SI-7: armed only during a host session |
| Overcurrent into a fragile DUT | ~49 mA plateau (typical), 128 mA passive bound | software compliance, **post-hoc** (acts after a conversion), stated as such |
| One ±15 V rail missing | D8/D9 clamp, U12 opens | `AFE` check blocks `ARM`; `NO_OUTPUT` classification; never adapt calibration to it |
| Flash erase stalls the CPU | (gate disarms) | SI-8: erase only while disarmed |

### 3.2 Safety invariants (normative)

Each invariant names how it is enforced and how it is verified (§9).

| ID | Invariant | Enforcement | Verification |
|---|---|---|---|
| **SI-1** | Every DAC frame a release build can emit has B = 0xFFFF and PD1:PD0 = 00, and every A update is followed by `0x34 FF FF` | `dac8552_frames()` has no B/PD parameter. The bring-up-only `DAC.RAW` is compiled out of release builds | exhaustive unit test over all A codes; ELF symbol check (no bring-up symbols in release); analyser decode (L3) |
| **SI-2** | PC5 makes edges only in thread mode, in `hb_service()`, only while the health predicate holds, on the fixed 250 µs grid, with every edge spacing ≤ 1.0 ms (§4.4). The one exception: the zero-only writers (fault handlers, dead-man ISR) may force PC5 **low**, never high | single edge generator; IPSR assert; grid scheduler; gap fault | unit tests; link check (only `main.o` references `hb_service`; only `heartbeat.o` and `dac_zero_safe` access GPIOC BSRR); analyser over a soak (L3) |
| **SI-3** | Before the first heartbeat edge after any reset, and at every `ARM`, this boot has written A = zero code and B = 0xFFFF | boot step 1; the `ARM` sequence | state-machine tests; analyser (reset → frames before PC5 activity) |
| **SI-4** | Every orderly disarm and every fault entry writes A = zero code **before** the heartbeat stops (unless the DAC transport is the fault) | the FAULT/DISARM entry sequences (§6.3) | unit tests on every state × fault; bench drills (M9) |
| **SI-5** | No DAC code is produced except by `scaling_v_to_code()` (or the zero constant), which rejects \|V\| > `v_limit` ≤ 10.000 V | the code-producing API takes volts, not codes | unit tests; sweep definitions validated before running |
| **SI-6** | After any reset the instrument is `DISARMED`; nothing but an `ARM` command in a live session arms it | no persistent "armed" state; boot always ends in `DISARMED` or `FAULT` | sim reset drills; bench reset/watchdog drills |
| **SI-7** | `ARMING`, `ARMED` and `SWEEPING` need a host session (VBUS, configured, DTR); losing it starts `DISARMING` | session service | sim (VBUS/DTR drop); bench unplug |
| **SI-8** | Flash erase/program only in `DISARMED`, ≥ `T_DISARM` after the last heartbeat edge | `CAL.SAVE` state gate | unit test; timed bench test |
| **SI-9** | When the state requires the output armed, any failing health item (loop timing, heartbeat gap, DAC transport, ADC freshness and integrity, stack canary, invariant cross-checks such as the B shadow and `DBG_TIM2_STOP`) causes a **FAULT entry**, not merely a missing heartbeat. Host-session loss is **not** a health item: it is a state trigger (SI-7) handled by a bounded ramp with the heartbeat still running | `safety_service()` evaluates; `hb_service()` re-checks as a second gate | unit tests; fault injection (sim) |
| **SI-10** | No ISR writes PC5, SPI1 or SPI2, except the zero-only writers (fault handlers and the dead-man ISR): they may force PC5 low and send the zero pair through `dac_zero_safe()`, nothing else (§7.1, §7.9) | ISR set fixed (§7.1) | code review checklist; link check |
| **SI-11** | The IWDG runs in every build and is kicked only at the end of a healthy pass | `iwdg_service()` | injected hang (L3) |

### 3.3 Residual risks accepted for v1

- **Interrupts disabled during a hang.** If interrupts are disabled when the
  loop hangs (dead-man masked), or the core locks up before reaching a
  handler, the last setpoint decays for 52–77 ms. The TMUX1101 may chatter
  in that window (§11, H7). Every other hang is zeroed within 2 ms.
- **Wrong but in-range setpoints** from a logically faulty yet running loop.
  Mitigated by design (one code path, validation before running) and by
  tests, not by hardware.
- **Compliance is post-hoc.** It reacts within settle + one conversion of
  the step, or one monitor conversion during a long settle.
- **A corrupted channel-B frame** persists until the next update or the
  scrub, ≤ 100 ms in every armed state. Current stays bounded by R30
  meanwhile.
- **"Armed" is inferred, not measured** (§4.8).

## 4. Hardware interface the firmware drives (verified)

Evidence labels:
- **[Src]** SKiDL source or generated netlist at `8a84b55`;
- **[DS]** datasheet, with document and revision;
- **[Sim]** this work's numerical simulation;
- **[Calc]** arithmetic;
- **[Bench]** not yet measurable, because no board has been built.

Datasheets read for this plan:
- TI SLAS430A (DAC8552), SCDS410D (TMUX1101), SBAS288K (ADS1256),
  SBOS051G (INA128);
- ST DS10314 Rev 8, RM0383 Rev 4, ES0287 Rev 6. st.com refused downloads, so
  these were read from third-party mirrors and cross-checked against ST's own
  `cmsis-device-f4` and `stm32f4xx-hal-driver` sources on GitHub.

### 4.1 Pin configuration (normative)

All pins are checked against `stage1_iv.net`: U9 pin numbers, LQFP-64.

| Pin (U9) | Net | Configuration | Init value / order | Notes |
|---|---|---|---|---|
| PC5 (25) | SAFE_HB | GPIO output push-pull, low speed, no pull | **low, first action** | Heartbeat. AF0–AF14 empty, AF15 EVENTOUT only [DS10314 Table 9]. Edges only from `hb_service()`; the zero-only writers (fault handlers, dead-man) may force it low. Config locked with LCKR |
| PA4 (20) | CS_DAC | GPIO output push-pull, medium speed, **no pull** | **high, before PA5/PA7 get their AF** | FT pin [DS]. R34 pulls it to +5VA, so **never analog mode and never an internal pull** (FT tolerance is lost in analog mode, and a pull must be off above VDD+0.3 V [DS10314 Table 8 note 4, Table 53]). Excluded from bulk "unused → analog" code |
| PA5 (21) / PA7 (23) | SCK1 / MOSI1 | AF5 (SPI1), medium speed | after PA4 is high | SPI1 master, mode 1 (CPOL 0, CPHA 1), 8-bit, MSB first, software NSS (SSM = SSI = 1), BR = /16 → 6 MHz from APB2 96 MHz (DAC max 30 MHz) |
| PA6 (22) | — | analog | | Not SPI1 MISO. The DAC has no output; RX data are read and discarded |
| PB12 (33) | CS_ADC | GPIO output push-pull, medium speed | high | |
| PB13 / PB14 / PB15 (34/35/36) | SCK2 / MISO2 / MOSI2 | AF5 (SPI2), **≥ medium speed** (errata ES0287 2.11.4); PB14 weak pull-down (DOUT is Hi-Z while /CS is high) | | SPI2 master, mode 1, SCLK idle low (SBAS288K: "hold SCLK low"), BR = /32 → **1.5 MHz** from APB1 48 MHz (≤ 1.92 MHz = f_CLKIN/4) |
| PA8 (41) | DRDY | GPIO input, no pull (R19 external), EXTI8 falling edge | | Never MCO1 |
| PA9 (42) | RESET_ADC | GPIO output push-pull, low speed | **high** early in boot; pulsed low ≥ 2 µs to reset the ADS1256 | FT. **Never AF10 (OTG_FS_VBUS), never input**. Do not copy TinyUSB's `hw/bsp/stm32f4/family.c`, which makes PA9 an input |
| PA0 (14) | VBUS_SENSE | **analog, ADC1_IN0**, 480-cycle sample time | | 2.5 V at 5 V VBUS is only 0.19 V above the tested V_IH (2.31 V at VDD 3.3 V) and VBUS = 4.4 V is not guaranteed high [DS10314 Table 53]. So VBUS is *measured*: attach > 1.9 V at the pin (VBUS > 3.8 V), detach < 1.5 V (VBUS < 3.0 V), 20 ms debounce. Source impedance 50 kΩ is at the R_AIN limit, hence the longest sample time |
| PA11 / PA12 (44/45) | USB_DM / USB_DP | AF10 (OTG_FS), very high speed | | |
| PC13 (2) | LED_STATUS | GPIO output (push-pull or open-drain), **low speed** | high = LED off | LED lit by driving **low**. Sinks ≈ 1.3 mA; ≤ 3 mA, ≤ 2 MHz, never a source [DS10314 Table 8 note 2] |
| PA13 / PA14 (46/49) | SWDIO / SWCLK | untouched (reset AF0 and pulls) | | Bulk GPIO writes must mask these bits |
| PH0 / PH1 (5/6) | OSC_IN/OUT | HSE crystal 8 MHz | | |
| PA1–3, PA10 (OTG_ID), PA15, PB0–2, PB3–10, PC0–4, PC6–12, PC14/15, PD2 | not connected | **analog** | | JTAG pins PA15/PB3/PB4 can be released with SWD kept [RM0383 23.4.4; ES0287 2.2.5] |

No pin has an alternate function that could drive PC5, the DAC or the ADC
autonomously.

### 4.2 Clocks, flash, watchdog, reset

| Item | Value | Source |
|---|---|---|
| PLL | HSE 8 MHz, M = 4 (2 MHz VCO in), N = 192 (384 MHz), P = 4 → **SYSCLK 96 MHz**, Q = 8 → **USB 48 MHz exactly** | RM0383 §6.3.2; DS10314 |
| Voltage scale | **VOS = Scale 1** (reset value is Scale 2, ≤ 84 MHz). PWREN first, VOS written with the PLL off, wait VOSRDY | RM0383 |
| Flash | 3 wait states (90–100 MHz at 2.7–3.6 V), set before the switch; prefetch, I- and D-cache on | RM0383 Table 5 |
| Buses | AHB 96, APB1 48 (/2), APB2 96 (/1); TIM2 kernel clock 96 MHz → PSC = 95 → 1 MHz | RM0383 §6.2 |
| `SystemCoreClock` | updated before `tusb_init()`: TinyUSB derives the USB turnaround time from it | TinyUSB `dcd_dwc2.c` |
| CSS | enabled. The NMI runs the common fault handler (zero, crash record, reset, §7.9). It sets CSSC first, because the NMI repeats otherwise | RM0383 §6.2.7 |
| Flash map | sector 0 (16 KB) vectors + startup; **sectors 1–2 (16 KB each) calibration A/B**; code from sector 3 (0x0800C000) | RM0383 Table 4 |
| Flash timing | 16 KB sector erase **250 ms typ, 500 ms max** (x32); word program 16/100 µs; **any flash read during an erase stalls the bus** | DS10314 Table 45; RM0383 §3.5 |
| IWDG | LSI **17–47 kHz** (32 typ). Prescaler /32, reload 1500 → timeout **1.02 s (LSI max) … 2.82 s (LSI min)**: ≥ 2× the 500 ms worst-case sector erase | DS10314 Table 40; RM0383 |
| IWDG debug | `DBGMCU_APB1_FZ.DBG_IWDG_STOP` (bit 12) in debug builds only | RM0383 |
| Option bytes | WDG_SW = 1 (software start). **BOR level 3** (2.83 V falling / 2.92 V rising, typ). Factory default is BOR **off**: the flashing procedure programs it, and `ARM` is refused (`ERR PRECOND bor`) until it is set (§6.2) | RM0383 §3.6 / OPTCR |
| Reset cause | RCC_CSR LPWR/WWDG/IWDG/SFT/POR/PIN/BOR, cleared by RMVF. An internal reset also pulses NRST, so PINRSTF accompanies every cause: decode the others first | RM0383 §6.1, §6.3.21 |
| Diagnostics | MCU temperature (ADC1_IN18, TS_CAL1/2 at 0x1FFF7A2C/2E) reported in `STAT?` as an ambient proxy for drift | DS10314 |

### 4.3 DAC path (U4 DAC8552 → U3 → V_SWEEP)

- **Transfer.** V_SWEEP = 8.0321·VREF_DAC·(code_A − code_B/2)/65536
  [Src `dac_sweep.py`]. With code_B = 0xFFFF and VREF_DAC = 2.500 V, the zero
  is at code_A = 32767.5. Setpoint resolution is 306.4 µV/code. ±10.000 V
  nominal is codes 131…65404.
- **Frame** [DS SLAS430A Fig. 43, Table 1, Example 1, p.13–16]:
  - 24 bits, MSB first, DIN latched on the **falling** SCLK edge → mode 1.
  - Control byte `0 0 LDB LDA X BufSel PD1 PD0`; DB23 and DB22 must be 0.
  - The update pair is `0x00 Ahi Alo` (write buffer A), then `0x34 FF FF`
    (write buffer B, load DAC A and DAC B simultaneously). 0x34 also loads A
    from buffer A, which is why the pair is atomic at the output.
  - A frame is discarded if /SYNC rises before the 24th falling edge.
  - /SYNC must rise and fall again for the next frame: t8 ≥ 33 ns high,
    t9 ≥ 100 ns from the 24th edge to the next fall.
- **Logic levels.** V_INH ≥ 2.4 V and V_INL ≤ 0.8 V at VDD = 5 V, Schmitt
  inputs. 3.3 V drive is in spec [DS p.3; characterised, not production
  tested].
- **Power-on** [DS p.15]: DAC registers zero, outputs at zero scale. Input
  buffers are not stated to be cleared. There is no reset pin, no software
  reset and **no readback**.
- **Settling** 10 µs max (±0.003 % FSR); slew 1.8 V/µs.
- **Linearity** is specified only for codes 485…64741, which is about
  −9.89 V … +9.80 V of V_SWEEP. Zero-code error ±12 mV max (≈ ±96 mV at
  V_SWEEP). Channel B at 0xFFFF lies outside the linear range, but it is
  static, so its error is a fixed offset the calibration removes.
- **Reference.** 0 V … VDD allowed, so the gated 0 V state is legal. The
  input impedance is 62 kΩ *typ* only; the worst-case reference current
  implies about 46 kΩ. The output at VREF = 0 is not specified, so the
  safe-state residual is a bench item (checklist §3).

### 4.4 Reference gate and heartbeat

PC5 → R36 1 kΩ → C47 100 nF → D6 (BAT54S) → DAC_ARM (C48 470 nF ∥ R37 100 kΩ)
→ TMUX1101 SEL.
- **ON** for SEL ≥ V_IH = 1.49 V.
- **OFF** for SEL ≤ V_IL = 0.87 V.
- No hysteresis and no input-slope requirement are specified; SEL leakage is
  ≤ 0.06 µA [DS SCDS410D p.5, Table 8-1].
- Pinout 1 D, 2 S, 3 GND, 4 SEL, 5 VDD matches the board.

**Pump behaviour.** Numerical simulation [Sim] of this circuit with a
fitted BAT54 model, checked by an independent solver to ±1 mV:

| | Nominal | Worst case (arming: VOH 3.0 V, 50 Ω, VF 0.40 V at 1 mA, 13 µA leakage, C47 −10 %, C48 +10 %) |
|---|---|---|
| DAC_ARM low point at 500 Hz / 1 kHz / 2 kHz / 5 kHz | 2.42 / 2.62 / 2.70 / 2.73 V | 1.76 / 2.03 / 2.15 / 2.20 V |
| Time from the first edge until DAC_ARM stays ≥ 1.49 V, at 2 kHz | 2.1 ms | 4.0 ms |
| 95 % of steady state, at 2 kHz | 9 ms | 10.5 ms |
| Disarm to < 0.87 V, PC5 left **low** or floating | 52–54 ms | 61–63 ms (slow corner: low VF, C48 +10 %, R37 +1 %) |
| Disarm to < 0.87 V, PC5 left **high** | 64–66 ms | 75–77 ms |
| Longest single gap in a 2 kHz heartbeat that keeps ≥ 1.7 V / ≥ 1.49 V | 22 / 28 ms | 7.5 / 11.2 ms |
| Single edge or pin stuck high: peak | 0.54 V | 0.66 V (< V_IL 0.87 V) |
| Two rising edges | 0.93–1.12 V: **may partly turn U11 on** | |

Three findings drive the firmware rules below:
- **Fast, even toggling does not starve the pump.** DAC_ARM rises with
  frequency and flattens above ~3 kHz. The original concern that it would
  fail at high frequency is wrong.
- **What does starve it is an uneven duty cycle.** Toggling once per loop
  pass, when passes alternate short and long, gives worst-case lows of
  0.42 V at 5 µs/500 µs and 1.0 V at 10 µs/500 µs. So edges must be
  scheduled from a time base, never "every pass".
- **The gate can begin conducting by the second rising edge**, so the DAC
  must already hold the zero pair before the first edge (SI-3).

**Firmware heartbeat rules** (constants in `heartbeat.h`):

| Constant | Value | Basis |
|---|---|---|
| `HB_HALF_PERIOD` | **250 µs on a fixed grid** (`next_edge += 250 µs`): a 2 kHz square wave whose fundamental does not wander. Each edge lands in the first pass after its grid time (≤ 150 µs late); if more than one period behind, resynchronise without bursting | 2.15 V worst case at 2 kHz; 2.07 V even at 20/80 % duty. 2 kHz is a sinc notch at 10, 50, 100, 500, 1000 and 2000 SPS |
| Normal edge spacing | 100–400 µs. Above 500 µs it is counted in `DIAG?` | ≥ 2.0 V worst case |
| `HB_GAP_FAULT` | **1.0 ms** since the last edge while permitted → `F_LOOP_OVERRUN` → FAULT. A hang (no pass at all) is caught by the 2 ms dead-man (§7.1) | the worst-case trough falls below 1.49 V for *sustained* half periods above 1.55 ms (below 1.7 V above 1.12 ms). A single-gap limit alone (7.5 ms) would miss a loop running steadily slow, so the limit applies to every edge |
| `T_ARM` | **100 ms** after the first edge before `ARMED` is reported (handoff value) | guaranteed on by 7 ms worst case; 95 % by 17 ms |
| `T_DISARM` | **100 ms** after the last edge, with PC5 driven **low** | 63 ms worst case low, 77 ms if left high |

**Consequence for the bring-up checklist** (§11, H1). Its §6 disarm
expectation of "≤ 60 ms" will fail in the slow corner (61–63 ms) and for
"PC5 left high" (up to 77 ms), even though the hardware is behaving as
designed. Suggested limits: ≤ 70 ms with PC5 low or floating, ≤ 85 ms with
PC5 high. The levels simulated here are about 0.2 V *higher* than the
repository's earlier figures (which used a fixed diode drop), so the
repository's 1.87 V worst case at 1 kHz is conservative.

### 4.5 ADS1256 (U8)

Hardware [Src `adc.py`]:
- f_CLKIN = 7.68 MHz (τ = 130.2 ns);
- VREF = 2.500 V;
- AIN2 − AINCOM = voltage, AIN0 − AINCOM = current, with AINCOM on
  VREF_2V5;
- AIN1/AIN3 are 100 Ω to AGND (self-checks);
- SYNC/PDWN tied high;
- /RESET on PA9 with a 10 kΩ pull-up; /DRDY on PA8 with a 10 kΩ pull-up.

Facts [DS SBAS288K]:
- **Interface:** SPI mode 1 with SCLK idle low.
  - SCLK held **low** for 32 data periods resets the serial interface; this
    is a timeout, not a device reset.
  - A specific SCLK-high pulse pattern resets the device (Fig. 2). With
    mode 1 and no stalls it cannot occur.
  - /CS high resets the interface and puts DOUT in Hi-Z.
- **Delays:**
  - t1 ≥ 4τ: SCLK ≤ 1.92 MHz.
  - t6 ≥ 50τ = **6.51 µs** before clocking out data after RDATA/RREG.
  - t10 ≥ 8τ = **1.04 µs** /CS hold after the last SCLK.
  - t11 = 4τ after RREG/WREG/RDATA; **24τ = 3.13 µs after SYNC**. After
    WAKEUP t11 is unspecified, so 24τ is used.
  - t16 ≥ 4τ RESET pulse.
  - After RESET, STANDBY and the calibration commands, wait for /DRDY.
- **Registers:** STATUS 00h, MUX 01h, ADCON 02h, DRATE 03h, IO 04h,
  OFC 05–07h, FSC 08–0Ah.
- **Reset values:** ADCON 0x20 (CLKOUT on), DRATE 0xF0 (30 kSPS), IO 0xE0
  (D0 output, D1–D3 inputs), MUX 0x01. **The RESET command does not restore
  CLK1:CLK0; the RESET pin and power-up do.** Any reset runs an automatic
  self-calibration.
- **ID nibble:** not documented in SBAS288K. Do not use it as a pass/fail
  check.
- **Mux cycling** (Fig. 19): WREG MUX → SYNC → WAKEUP → RDATA, every result
  fully settled.

  | DR (SPS) | 30k | 7500 | 3750 | 2000 | 1000 | 500 | 100 | 60 | 50 | 10 | 2.5 |
  |---|---|---|---|---|---|---|---|---|---|---|---|
  | t18 settling (ms) | 0.21 | 0.31 | 0.44 | 0.68 | 1.18 | 2.18 | 10.18 | 16.84 | 20.18 | 100.18 | 400.18 |
  | SELFCAL at PGA 2 (ms) | 0.60 | 0.90 | 1.3 | 2.0 | 3.6 | 6.6 | 31.2 | 50.9 | 61.8 | 307.2 | 1227 |
  | noise, PGA 2, buffer off (µV rms) | 6.14 | | | | 1.80 | | 0.53 | 0.42 | 0.39 | 0.20 | |

- **Calibration rule:** recalibrate after any DRATE change (must), and any
  PGA or BUFEN change (should). Issue SYNC after any PGA, DRATE, BUFEN,
  OFC/FSC or SDCS change.
- **Mains notches:** 60 Hz at 60, 30, 15 SPS; 50 Hz at 50, 25 SPS; both at
  10, 5, 2.5 SPS.
- **Data:** 24-bit two's complement, LSB = 2VREF/(PGA·(2²³−1)) =
  298.023 nV at PGA 2. Clip codes 0x7FFFFF / 0x800000.
- **Input impedance, buffer off:** about 150 kΩ/PGA differential. With the
  100 Ω source that is a ≈ 0.17 % gain error at PGA 2, which ADC
  self-calibration does not remove and the system calibration does.
  AINCOM is sampled like any input, from VREF_2V5.
- **Unused D0–D3:** "configure them as outputs", so IO = 0x00 after
  CLK = 00. SDCS must stay 00, and **BUFEN must stay 0**: with the buffer on
  the input range is 0 … AVDD − 2 V, which the ±2 V signals around 2.5 V
  exceed.

### 4.6 Measurement scaling and ranges

Nominal values [Src, Calc]; the calibration model is §7.10.

| Channel | Physical → ADC | ADC code → SI (PGA 2) | Reachable | Guaranteed measurable |
|---|---|---|---|---|
| Voltage (Kelvin) | V_SENSE_OUT − VREF = 0.199203·V_K (U7 G = 1, R14 DNP) | 1.496077 µV/code (×1.00168 input-impedance correction, §7.10) | ±10.04 V | ±11.8 V |
| Current | I_SENSE_OUT − VREF = 24.0056 V/A·I (U6 G = 2.4006, R10 10.00 Ω) | 12.4147 nA/code (same correction) | ±91.3 mA (passive DUT) | ±97.8 mA |

Uncalibrated offsets, referred to the DUT (INA128P [DS SBOS051G]):
- V: ≤ 2.76 mV max;
- I: ≤ 26 µA max, ≈ 5 µA typ; drift ≈ 0.1 µA/°C typ, 0.9 µA/°C max.

These, not the ADC LSB, set the low end. Hence the session zero (§7.10)
and the fixed PGA 2 (§7.5).

### 4.7 USB

OTG_FS core ID 0x1200 [`stm32f411xe.h`].
- **VBUS sensing off:** GCCFG.NOVBUSSENS (bit 21) = 1, VBUSASEN (18) =
  VBUSBSEN (19) = 0, PWRDWN (16) = 1. This "leaves the pad free", so PA9 is
  a GPIO [RM0383].
- **Device mode:** GUSBCFG.FDMOD (bit 30) forces device mode; wait ≥ 25 ms
  after setting it.
- **Connect:** DCTL.SDIS (bit 1) is the soft disconnect.
- **TinyUSB 0.21.0** (latest tag, 2026-06-30) does the GCCFG part itself when
  `CFG_TUD_VBUS_DETECT_HW = 0` (the default).
  - It touches no GPIO, but **its `dcd_init()` connects (clears SDIS)
    unconditionally**, so `tud_disconnect()` is called right after
    `tusb_init()` and connection is gated by the PA0 measurement.
  - Without VBUS sensing, **TinyUSB never sees an unplug**. The session logic
    (§8.2) uses PA0, not `tud_mounted()`, as the truth for attach and detach.
  - Its interrupt handler only moves FIFO data and queues events; class
    callbacks run in `tud_task()` in the main loop.
  - Errata ES0287 2.12.1 (TX FIFO corruption if FIFO writes are interrupted
    by OTG register accesses): no non-USB ISR touches OTG registers, and
    `tud_connect/disconnect` run only from the main loop.
  - The note in `usbd.h` that swaps the connect/disconnect descriptions is a
    documentation error only.

### 4.8 What the firmware cannot observe

These gaps shape the safety design. None blocks v1. Each is in §11.

| Not observable | Consequence | How the firmware copes |
|---|---|---|
| DAC_ARM / DAC_VREF (gate state) | "armed" is inferred from heartbeat time, not measured | `T_ARM`/`T_DISARM` from the pump analysis; the Kelvin/force checks reveal an output that does not follow when a DUT or the Kelvin leads are connected; bring-up measures the gate |
| DAC register contents | B = FFFF cannot be verified | B is rewritten with every update and every 100 ms scrub (§7.4) |
| ±15 V rails, U12 state | a missing rail looks like "no output" | `AFE` signature check before `ARM`; `NO_OUTPUT` classification (§7.7) |
| Actual output current limit | the plateau is typical only | `CLIM` threshold from the bring-up measurement |

## 5. Toolchain, libraries and repository integration

| Item | Choice (pinned) | Why |
|---|---|---|
| Language | C11 (`-std=c11`), no dynamic allocation, no recursion | small, explicit, analysable; what every STM32 reference and tool assumes |
| Compiler | `arm-none-eabi-gcc` from Ubuntu 24.04's `gcc-arm-none-eabi` (13.2) on a **pinned `ubuntu-24.04` runner**; developers may use Arm GNU Toolchain ≥ 13.2. The version is printed and checked in CI. Release images come from CI | reproducible without a custom download step |
| Flags | `-mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard -O2 -g3 -ffunction-sections -fdata-sections -Wall -Wextra -Werror -Wconversion -Wshadow -Wdouble-promotion`, `--gc-sections`, newlib-nano, `-fstack-usage` + a stack report | single-precision FPU; double only where explicit (the per-record SI conversion); no float `printf` |
| Build | CMake ≥ 3.25 + Ninja, `CMakePresets.json` | one tool for target, host tests and the simulator |
| Device support | ST `cmsis-device-f4` (Apache-2.0) + ARM CMSIS 5.9.0 Core (Apache-2.0), submodules at fixed commits | register definitions only |
| ST HAL / LL | **not used** (the ST driver repository is a reference for sequences) | HAL's blocking `HAL_Delay`/timeout idioms and SysTick coupling conflict with the bounded-pass rule; the peripherals used are few and simple |
| USB | TinyUSB **0.21.0** (MIT), submodule; CDC-ACM only; `CFG_TUD_VBUS_DETECT_HW = 0` | ISR queues, `tud_task()` processes in the main loop; maintained dwc2 port for the F411 |
| Unit tests | Unity (MIT), submodule; host gcc with `-fsanitize=address,undefined`; gcovr for coverage | small, C-native |
| Fuzzing | clang libFuzzer on the protocol parser (host) | cheap robustness for the one external input |
| Static analysis | cppcheck (CI), optional clang-tidy | |
| Host software | Python ≥ 3.11, pyserial 3.5, pytest; numpy for the calibration fit | same package is the sim test driver |
| Programming / debug | ST-LINK V2/V3 (or J-Link) on J2, connect-under-reset (NRST on J2 pin 5); OpenOCD 0.12 (`target/stm32f4x.cfg`) or pyOCD; option bytes (BOR level 3, WDG_SW = 1) set by a scripted step in `host/tools` | BOOT0 is tied low, so SWD is the only path; there is no SWO |

**Build variants:**

| Variant | Used for | Differences |
|---|---|---|
| `debug` | development | asserts on, `#` log lines on, `DBG_IWDG_STOP` set |
| `release` | measurements | asserts become `F_INVARIANT`, no log lines, no bring-up commands |
| `bringup` | `bringup_checklist.md` §6–§8 only | release plus the `HB.*`, `DAC.RAW`, `ADC.PGA`, `ADC.RESETPULSE`, `CRASH` commands (§8.4). `*IDN?` reports `build=bringup`, and the host library refuses to run sweeps on it without `--allow-bringup` |

**Repository integration** (nothing in the hardware tree changes):

- **New top-level trees:** `firmware/`, `host/`, `docs/firmware/`.
- **CI.** A **new** workflow `.github/workflows/firmware.yml`, path-filtered
  to those trees and itself. `build.yml` and its hardware gates are left
  alone; the existing "nothing regenerated is dirty" and determinism steps do
  not touch the new trees, and `build/` is already in `.gitignore`.
- **`tools/check_no_abs_paths.py`** scans every tracked `.md .py .txt .yml
  .json .cfg .toml` and suffix-less file. That includes `CMakeLists.txt`,
  OpenOCD `.cfg` files, host Python and firmware docs. Two consequences:
  - **Third-party code must be git submodules**, never copied in. The guard
    skips gitlinks (a directory path raises `OSError`, which it ignores);
    copying TinyUSB would add thousands of scanned files.
  - **No device paths** (e.g. a fixed tty path) in code or docs: port
    discovery is by VID/PID/serial (§8.1).
- **Licensing.** MIT (TinyUSB, Unity) and Apache-2.0 (CMSIS) are compatible
  with the repository's MIT licence. Keep their licence files in the
  submodules.

## 6. Operating-state model

### 6.1 States

| State | Output | Heartbeat (PC5) | Entered by | Leaves to |
|---|---|---|---|---|
| `BOOT` | gate open (reset), DAC zeroed in early init | static low | any reset | `DISARMED` (POST passed) or `FAULT` (POST failed) |
| `DISARMED` | ≈ 0 V setpoint, gate open | static low | boot, `DISARM` completed, `FAULT.CLR` | `ARMING` (`ARM` accepted) |
| `ARMING` | A = zero code, gate closing | toggling | `ARM` | `ARMED` after `T_ARM`, or `DISARMING` / `FAULT` |
| `ARMED` | holds a DC level (default 0 V); `SRC` ramps it | toggling | arming done; sweep ended | `SWEEPING`, `DISARMING`, `FAULT` |
| `SWEEPING` | point engine drives the DAC | toggling | `SWP.RUN` | `ARMED` (end/abort/compliance), `DISARMING` (session lost), `FAULT` |
| `DISARMING` | ramps to 0 V, writes A = zero code, then stops the heartbeat | toggling until the zero code is loaded, then static low | `DISARM`, session lost, host timeout | `DISARMED` once `T_DISARM` has elapsed after the last edge |
| `FAULT` | A = zero code written immediately, gate opening | static low | any fault (§6.4) | `DISARMED` on `FAULT.CLR`, which needs the cause gone and `T_DISARM` elapsed |

The host sees state changes as `EVT STATE <name>` (§8.6). The firmware never
goes from any state back to `ARMING` without a new `ARM` command. In
particular it **never re-arms after a reset of any kind**, and it reports the
reset cause (§7.9).

`T_ARM` and `T_DISARM` come from the charge-pump analysis (§4.4): the state
is reported only once the hardware state can be relied on, not when the
command was accepted.

### 6.2 Arm preconditions

`ARM` is refused unless **all** of these hold. A wrong state gives `ERR STATE`; any other failure gives `ERR PRECOND <name>`:

1. state is `DISARMED` and no fault is latched;
2. the host session is valid (§8.2: VBUS present, USB configured and not
   suspended, DTR asserted);
3. POST passed (§7.2), and the ADC is configured and producing conversions;
4. a front-end check within the last 1 s passed (§7.7, check `AFE`): with
   the gate open, no channel is at a clip code, and the two channels are not
   both below −0.8 × full scale. The second condition is the "±15 V off"
   signature: both INA128 outputs sit near AGND. The bench supplies may come
   up after the MCU, so this runs at `ARM` time, not only at boot.

   It deliberately does **not** test |I|. An active or charged DUT
   (PV cell, battery, a capacitor left charged) legitimately drives current
   into the ≈ 0 V gate-open output;
5. the active acquisition configuration and limits are valid;
6. **BOR is at level 3** (option bytes). Below 2.7 V the 96 MHz / 3-wait-state
   flash setting is out of specification. +5VA can still power the DAC and
   TMUX1101 while a sagging +5V_RAW starves the 3.3 V LDO, so an MCU running
   out of spec next to a powered gate is a real hazard. BOR level 3 holds the
   MCU in reset below 2.83 V instead. Factory parts ship with BOR off, so
   `ARM` reports `ERR PRECOND bor`.

Calibration does not gate arming. With nominal constants every record carries
the `CAL_NOMINAL` flag.

### 6.3 Arm and disarm sequences (normative)

```
ARM:
  target := 0 V
  if !dac_update(zero_code): FAULT(F_DAC_SPI)     // frame 1: 00 A; frame 2: 34 FFFF
  hb_enable()            // sets last_edge := now; first edge on the next pass
  wait T_ARM (from the first edge)                // ADC monitor keeps running
  -> ARMED, EVT STATE ARMED

DISARM (orderly; also session loss and host timeout):
  ramp to 0 V at max(ramp_rate, 10 V/s)           // floor is a firmware constant
  deadline: 1.1 s after DISARMING was entered -> FAULT(F_DISARM_TIMEOUT)
  dac_update(zero_code); target := 0 V
  hb_disable()                                    // PC5 low and left low
  wait T_DISARM (from the last edge)
  -> DISARMED, EVT STATE DISARMED

FAULT entry (any state):
  dac_zero_safe()        // §7.9: frame-aligned zero pair; SPI1 reset and retry,
                         // then a bit-banged fallback on PA5/PA7 if SPI1 fails
  hb_disable(); target := 0 V
  abort sweep, mark the sweep record "aborted: <fault>"
  latch fault, EVT FAULT <code>
```

The order matters. Writing A = zero code *before* the heartbeat stops gives
an immediate 0 V, instead of the last setpoint decaying over 52–77 ms. It also
makes TMUX1101 chatter harmless: SEL has no hysteresis and DAC_ARM crosses
its undefined 0.87–1.49 V band slowly, and with A at the zero code it does
not matter whether the reference toggles (§11, H7).

### 6.4 Faults, aborts and warnings

| Class | Examples | Reaction |
|---|---|---|
| **Fault** (latched) | `F_ADC_TIMEOUT`, `F_ADC_COMM` (register read-back mismatch), `F_DAC_SPI` (BSY/TXE timeout), `F_LOOP_OVERRUN` (pass budget, heartbeat gap), `F_INVARIANT` (heartbeat gate disagrees with state, B shadow ≠ FFFF, DBG_TIM2_STOP set, …), `F_AFE` (a clip code while armed), `F_STALE` (no conversion within `T_STALE` while armed), `F_DISARM_TIMEOUT`, `F_CLOCK` (HSE failed at boot; a CSS event at run time resets via the fault handler, §7.9), `F_STACK` (canary), `F_POST` | FAULT entry sequence; `FAULT.CLR` needed |
| **Abort** (not latched) | software compliance (`on_compliance=stop`), host `SWP.ABORT`, session loss, host timeout | compliance → immediate zero code, target := 0, stay `ARMED`; host `SWP.ABORT` → ramp to 0 V; session loss or timeout → `DISARMING`. Session loss is a **state trigger, not a health item**: the heartbeat keeps running during the bounded ramp, so the ramp never runs on a decaying, chattering gate |
| **Warning** (point flag and event) | ADC clip, current-limit plateau, Kelvin mismatch, force path open, late timing | flag in every affected record; `EVT WARN` the first time and when a run of N consecutive points is reached |

A fault in `DISARMED` is still latched: an ADC that stops responding while
disarmed blocks the next `ARM`.

**Why compliance is not a latched fault by default.** Researchers commonly use
compliance as the normal end of a forward sweep ("sweep until 10 mA").
`on_compliance=fault` is available for unattended runs. Either way the
reaction is an immediate zero code with no ramp. The software compliance is
**not a current limit.** It acts after a conversion, so the DUT carries the
excess for up to one settle time plus one conversion. The only real-time
limit is the hardware: the ~49 mA plateau (typical) and the 128 mA passive
bound.

## 7. Firmware architecture

### 7.1 Execution model

**Bare-metal cooperative super-loop, no RTOS.** Every service is a
non-blocking state machine with a bounded execution time per call. The
heartbeat is toggled by a single function, called once per pass at the end
of the loop, and only if every health check of that pass succeeded:

```c
for (;;) {
    loop_monitor_begin();          // pass timing, overrun detection
    usb_service();                 // tud_task(): drains queued USB events
    session_service();             // VBUS (PA0) debounce, soft-connect, DTR
    proto_service();               // ≤ 1 command parsed, bounded output formatting
    adc_service();                 // ADS1256 transaction state machine
    engine_service();              // point/sweep/monitor engine, DAC writes
    safety_service();              // checks, fault latch, state transitions
    hb_service(safety_hb_permitted()); // the ONLY writer of PC5
    led_service();
    deadman_reload();              // TIM5 dead-man (armed states), §7.9
    iwdg_service();                // kicks if the pass completed within budget
}
```

Why not an RTOS: the hardware's safety argument is that a hung control path
stops the heartbeat. With threads, an idle or heartbeat task keeps running
while a measurement task is blocked or spinning. That is exactly the failure
the handoff forbids, where a healthy ISR keeps toggling while the main loop
is hung. A super-loop makes "every service completed" and "the heartbeat
toggled" the same fact. Nothing in the workload needs preemption: conversions
take 0.2–400 ms, USB is event-driven, and the CPU is mostly idle.

**Interrupt policy (fixed set):**

| ISR | Does | Must not |
|---|---|---|
| `OTG_FS_IRQHandler` | TinyUSB `dcd_int_handler`: queues events only | process class requests (TinyUSB defers them to `tud_task()`) |
| `EXTI9_5_IRQHandler` (PA8 /DRDY falling) | timestamps the edge from TIM2, sets a flag, masks itself | touch SPI2 |
| `TIM5_IRQHandler` — **dead-man** (highest priority; armed states only) | fires if no pass has completed for 2 ms: PC5 low, `dac_zero_safe()`, crash record (cause `DEADMAN`, with the interrupted PC), reset | anything that could keep the output on |
| `NMI` (incl. CSS), `HardFault`, `MemManage`, `BusFault`, `UsageFault` | the fault handler of §7.9 | return |

Priorities (all preemption): the dead-man and the configurable faults 0,
`EXTI9_5` 1, `OTG_FS` 2. /DRDY sits above USB so that a USB FIFO copy never
delays the edge timestamp the read window is measured from; its handler runs
for well under 1 µs.

TIM2 runs free with no interrupt. The main loop extends it to 64 bits each
pass, and all gap arithmetic uses raw 32-bit unsigned differences.

**Zero-only writers.** Only the fault handlers and the dead-man ISR may touch
PC5 or SPI1 outside the main loop. They may force PC5 **low** and send the
zero pair through `dac_zero_safe()`, and nothing else, so they can only move
the output toward the safe state.

No other ISR touches PC5, SPI1 (the DAC) or SPI2 (the ADC).

`hb_service()` asserts `__get_IPSR() == 0` and latches `F_INVARIANT`
otherwise. A link check in CI enforces the single-writer rule: `hb_service`
may be referenced only from `main.o`, and only `heartbeat.o` (plus
`dac_zero_safe`'s force-low) may hold the GPIOC BSRR access.

All GPIO writes use BSRR, never ODR, because GPIOC is shared between PC5 and
the PC13 LED. After early init, the configuration of PC5 and PA4 is frozen
with LCKR. PA5/PA7 are not locked, so the bit-banged zero fallback can
reclaim them.

**Why a dead-man timer in addition to the gate.** A loop that hangs without
faulting leaves the last setpoint decaying for 52–77 ms, and the TMUX1101
may chatter on the way (H7). Known paths to such a hang include the
unbounded `while` spins in TinyUSB's `edpt_disable()`. The dead-man converts
any hang into a frame-aligned zero within 2 ms and a reset. It cannot keep
the output alive: it only ever forces PC5 low.

**Loop budget.**
- **Per service:** ≤ 70 µs. The longest single operation is one ADS1256
  transaction, about 35 µs at 1.5 MHz SCLK including the t6/t11 waits
  (calibrated busy-waits). `ads_service()` performs at most one transaction
  per call.
- **Per pass:** ≤ 150 µs worst case, typically 10–30 µs.
- **Enforcement:** a pass longer than 400 µs is counted in `DIAG?`. The
  heartbeat's edge spacing (§4.4) and the dead-man enforce the hard limits.
- **TinyUSB:** `CFG_TUD_TASK_EVENTS_PER_RUN = 2`, CDC TX buffer 4 KB, RX
  buffer 512 B.

**Output formatting.**
- Integer fixed-point only, from `decimal.c`: no `printf` of floats, no heap.
- CI link check: `_dtoa_r`, `_malloc_r` and `_sbrk` are absent from the
  image.
- At most 2 lines per pass. A long line such as `SWEEP_BEGIN` is emitted
  across passes in ≤ 128-byte chunks from a resumable line builder.

The only operations allowed to exceed the pass budget are:
- boot steps before the loop (HSE start, the 25 ms OTG FDMOD wait);
- `CAL.SAVE`, which erases and programs a 16 KB sector (≤ 500 ms with the
  bus stalled). It is legal only in `DISARMED` (SI-8). The loop monitor
  skips the pass that performs it, and the IWDG timeout (≥ 1.02 s) covers
  it.

Every other long activity (SELFCAL, ADC reset, settle waits, ramps) is a
state machine polled across passes.

### 7.2 Boot sequence (normative order)

| Step | Action | Why first |
|---|---|---|
| 0 | Reset handler: copy `.data`, zero a *small* `.bss`. The large buffers (result ring, sweep list) are in `.noinit` and initialised by their modules, so the DAC is zeroed within ~1 ms of reset | early zero |
| 1 | `early_safe_init()` on HSI 16 MHz, all waits cycle-counted (DWT CYCCNT, since TIM2 and the IWDG are not running yet). Order: <br>• PC5: BSRR low, then MODER output. <br>• PA4: **BSRR high first, then MODER output**, so /SYNC never glitches low. <br>• PA5/PA7 to AF5; SPI1 mode 1 at /4 (4 MHz). <br>• `dac_zero_safe()`: `00 80 00`, `34 FF FF`. <br>• PA9 high, then output (ADC out of reset); PB12 high, then output (CS_ADC idle). <br>• LCKR on PC5 and PA4. | Puts the DAC at a 0 V pair whatever it held. Matters when the gate is still armed after a fast reset (HardFault, dead-man, software reset): the gate decays over 52–77 ms, the write takes ≈ 0.1 ms |
| 2 | Read and clear RCC_CSR reset flags; read the `.noinit` crash record | reported in `STAT?` and `DIAG?` |
| 3 | Start the IWDG (§7.9) | from here, a hang resets |
| 4 | HSE + PLL → 96 MHz: VOS Scale 1, 3 wait states, caches, CSS on (§4.2); SPI1 → /16 (6 MHz). Switch thread mode to **PSP**, with the main stack at the bottom of SRAM; handlers keep MSP in a separate reserved 1 KB, so a thread stack overflow still reaches a working fault handler. If HSE fails: stay on HSI with USB disabled, latch `F_CLOCK`, blink the fault pattern | |
| 5 | TIM2 µs timebase; GPIO configuration of every remaining pin (§4.1, unused pins analog) | |
| 6 | Load the calibration record (A/B selection, CRC) or the nominal constants | |
| 7 | ADC init state machine starts (non-blocking, §7.4): RESET pulse, wait for /DRDY, configure, verify, SELFCAL | POST continues inside the main loop |
| 8 | USB init: device mode, VBUS sensing off, soft-disconnected until PA0 shows VBUS | |
| 9 | Enter the main loop in `BOOT`; `DISARMED` once ADC POST passes (or `FAULT(F_POST)`) | |

**POST** (in the loop, gate open):
- ADS1256 register write and read-back of MUX/ADCON/DRATE/IO. The ID nibble
  is logged, not trusted: SBAS288K does not document its value.
- SELFCAL completes before its timeout.
- Self-check conversions at PGA 1 (ADCON PGA = 1 + SELFCAL, the checks,
  then PGA = 2 + SELFCAL; at PGA 2 the −2.5 V reading would clip):
  - AIN1 − AINCOM must be ≈ −VREF = −2²² codes, within ±0.5 %;
  - AIN1 − AIN3 must be ≈ 0, within ±200 codes.
- The analog front-end check (`AFE`, §7.7).

An `AFE` failure at boot does **not** fail POST, because the ±15 V supplies
may simply be off. It only blocks `ARM`.

### 7.3 Module structure

```
firmware/
  CMakeLists.txt, CMakePresets.json
  cmake/            arm-gcc toolchain file, warnings, target/host options
  linker/           stm32f411re.ld   (vectors in sector 0, cal in sectors 1–2, code from sector 3)
  third_party/      git submodules, pinned: cmsis_core, cmsis_device_f4, tinyusb, unity
  src/
    main.c                  boot sequence + super-loop; sole caller of hb_service()
    core/                   portable, host-testable, no register access
      instrument.[ch]       state machine of §6
      safety.[ch]           health predicate, fault latch, per-point checks (§7.7)
      engine.[ch]           point / sweep / monitor engine (§7.5, §7.6)
      sweep_gen.[ch]        sweep definition → setpoint sequence (pure)
      scaling.[ch]          V↔DAC code, ADC code→V/I, limits (pure)
      calib.[ch]            calibration record, CRC, A/B selection (pure)
      results.[ch]          result ring, record format (pure)
      proto.[ch]            line parser, command table, formatting (pure; I/O via callbacks)
      decimal.[ch]          strict fixed-point decimal parser/printer (pure)
    drivers/                device logic over abstract transports (host-testable with fakes)
      dac8552.[ch]          frame builder (pure) + update over spi_if
      ads1256.[ch]          command layer + non-blocking conversion state machine
      heartbeat.[ch]        PC5 toggle scheduler + gate (pure logic + gpio_if)
    port_stm32/             target-only: clock, gpio, spi, tim2, exti, iwdg, flash, reset,
                            usb glue (TinyUSB dcd + VBUS/PA0 + soft-connect), uid, faults, startup
    port_sim/               host-only: board model, fake transports, pty transport, sim main
  tests/
    unit/                   Unity tests of core/ and drivers/ (host gcc, ASan/UBSan)
    sim/                    pytest suites running the sim binary through host/stage1iv
    target/                 on-target self-test scripts (OpenOCD/pyOCD + host library)
host/
  stage1iv/                 Python package: transport, protocol client, sweeps, calibration, file output
  tools/                    CLI (iv-sweep, iv-cal, iv-diag)
  tests/
docs/firmware/              this specification, protocol.md, bringup_firmware.md, calibration.md
```

**Dependency rule:** `core/` includes nothing from `port_*/` or vendor
headers. `drivers/` talk to hardware only through these small interfaces:

```c
typedef struct { uint64_t (*now_us)(void); void (*delay_us)(uint32_t us); } time_if;
typedef struct {
    void (*cs)(bool asserted);                       // GPIO chip select
    bool (*xfer)(const uint8_t *tx, uint8_t *rx, size_t n, uint32_t timeout_us);
                                                     // returns false on BSY/TXE/RXNE timeout;
                                                     // returns only after BSY == 0
} spi_if;
typedef struct { void (*write)(bool level); } gpio_out_if;
```

The same `core/` and `drivers/` objects are linked into the target image, the
unit tests and the simulator.

### 7.4 Driver contracts

**`dac8552`** — the only code that talks to U4.

```c
#define DAC_CODE_ZERO_NOMINAL 0x8000u
void dac8552_frames(uint16_t code_a, uint8_t out[6]);   // pure: 00 Ah Al 34 FF FF
bool dac_update(uint16_t code_a);                        // two frames; false on SPI fault
uint16_t dac_shadow_a(void);                             // last code successfully sent
```

- **No API can produce B ≠ 0xFFFF or PD ≠ 00.** Channel B is a constant, not
  a parameter. Every update rewrites B and loads both channels with the
  datasheet's simultaneous-update sequence (SLAS430A Example 1). That makes B
  self-healing: one corrupted frame cannot persist past the next update. The
  cost is 6 bytes instead of 3, about 8 µs at 6 MHz.
- `/SYNC` toggles between the two frames: one frame per falling edge, ≥ 33 ns
  high, ≥ 100 ns from the 24th edge to the next fall. `/SYNC` rises only after
  `SPI_SR.BSY == 0`. Raising it on TXE silently discards the frame
  (SLAS430A p.15).
- **Scrub.** In every state where the heartbeat runs (`ARMING`, `ARMED`,
  `SWEEPING`, `DISARMING`), `dac_shadow_a()` and B are re-sent every
  100 ms, whatever the ADC is doing.
  - SPI1 is independent of SPI2.
  - Reloading identical codes changes nothing at the output: 0.15 nV·s
    glitch, 0.15 nV·s digital feedthrough (SLAS430A). Any effect on readings
    is covered by the armed-vs-disarmed noise measurement at M5.
  - The scrub bounds the life of an undetected corruption to ≤ 100 ms in
    every armed state.
  - It sends the **shadow** (the last code commanded), never a recomputed
    target.
- **`dac_zero_safe()`** is the robust zero used by FAULT entry, the fault
  handlers and the dead-man. It is bounded to ≤ 200 µs, with cycle-counted
  waits:
  1. PC5 low.
  2. If SPI1 is clocked, wait for TXE = 1 and BSY = 0. On timeout, reset
     SPI1 through `RCC_APB2RSTR` and reconfigure it. Never clear SPE while
     BSY = 1 (errata ES0287 2.11.1).
  3. PA4 high for ≥ 1 µs. This aborts any half-sent frame: the DAC discards
     frames shorter than 24 bits.
  4. Send `00 80 00` and `34 FF FF`, each framed by /SYNC, waiting BSY = 0
     before each rise.
  5. If step 2 or 4 fails, switch PA5/PA7 to GPIO outputs and **bit-bang**
     the same two frames in mode 1 (data changes while SCLK is high; the DAC
     latches on the falling edge) at ≈ 1 MHz.

  Unit tests drive it against a fake DAC that has received 0–23 bits of a
  previous frame, and check that the DAC ends at A = 0x8000, B = 0xFFFF in
  every case.
- **Settling floor.** `settle_us` may not be set below
  `SETTLE_MIN = 100 µs`. That covers:
  - the DAC's 10 µs max settling;
  - U3B, which slews at 0.8 V/µs (OPA2277, SBOS079C), so a 20 V step needs
    25 µs, then about 15 µs more to settle at its noise gain of 9
    (≈ 110 kHz bandwidth);
  - INA128 settling (12 µs to 0.01 %).

  DUT settling is on top of that and is the user's choice; the default is
  1 ms. The real floor is confirmed at M6 from step responses at TP1,
  FORCE+, TP4 and TP7.

**`ads1256`** — the only code that talks to U8.

```c
typedef enum { ADS_MUX_V = 0x28, ADS_MUX_I = 0x08,
               ADS_MUX_ZERO = 0x13, ADS_MUX_NEGREF = 0x18 } ads_mux_t;
typedef struct { int32_t code; ads_mux_t mux; uint64_t t_wakeup_us, t_drdy_us, t_read_us;
                 bool late, clipped; } ads_sample_t;
void ads_init_begin(const ads_cfg_t *cfg);   // non-blocking: reset, configure, verify, SELFCAL
bool ads_ready(void);                         // initialised, idle, no error
bool ads_reconfigure(const ads_cfg_t *cfg);   // DISARMED only: WREG + SYNC + SELFCAL
bool ads_start(ads_mux_t mux);                // WREG MUX, SYNC, ≥24τ, WAKEUP (preempts a running conversion)
ads_poll_t ads_poll(ads_sample_t *out);       // BUSY / READY / ERROR
void ads_service(void);                       // ≤ 1 SPI transaction per call; timeouts, init/cal, scrub
```

- **Conversion model.** Every conversion is a mux-cycling settled
  conversion: `WREG MUX → SYNC → (≥ 24 τ) → WAKEUP`. It is read with `RDATA`
  after its /DRDY edge. This is the non-pipelined variant of SBAS288K
  Fig. 19: RDATA comes before the next mux change, so the data belong to the
  channel just started.
  - `ads_start()` may be issued while a monitor conversion is in progress.
    SYNC discards it, so the point schedule never waits for the monitor.
  - Cost: RDATA ≈ 29 µs plus the next start ≈ 34 µs, done in separate
    passes. The time estimates use 70 µs per conversion plus one pass.
- **Edge validity.**
  - After WAKEUP, clear EXTI_PR8 and the NVIC pending bit, then unmask.
    Edges from the previous free-running conversion set PR8 even while the
    line is masked (RM0383 §10.3.6).
  - The ISR records the first edge and masks itself.
  - An edge with `t_drdy − t_wakeup < 0.9·t18(DR)` is rejected as stale.
- **Read deadline.** RDATA is issued only if /DRDY reads low **and**
  `now − t_drdy ≤ 1/DR − (t6 + 24 SCLK + t17 + 10 µs)`. Otherwise the
  engine waits for the next edge and marks the sample `late`. This keeps
  reads out of the t17 update window, where SBAS288K says data are invalid.
  The check is repeated when the read ends: an interrupt during the read can
  still push it into t17, and such a read is discarded the same way
  (`DIAG? adc_read_overruns`).
- **Data rates.** v1 accepts 2.5, 5, 10, 15, 25, 30, 50, 60, 100, 500, 1000,
  2000 and 3750 SPS. At 3750 SPS the read deadline (≈ 232 µs) exceeds the
  150 µs pass budget, so `late` occurs only on a budget violation. 7500 SPS
  and above are excluded from v1, because their deadline would be shorter
  than the pass budget.
- **Rate changes need `DISARMED`.** A data-rate change needs a SELFCAL of up
  to 1.23 s (2.5 SPS, SBAS288K Table 21) with no conversions meanwhile.
  `CFG adc_rate` and `ADC.SELFCAL` are accepted only in `DISARMED`, and they
  mark the session zero stale.
- **Timeout.** No valid /DRDY within `t18(DR) × 1.5 + 2 ms` →
  `F_ADC_TIMEOUT`. A calibration times out at Table 21 + 50 %.
- **Timing guards.** Explicit `delay_us` calls, each with a unit test:
  - 6.51 µs (t6);
  - 4 τ after WREG/RREG/RDATA, 24 τ after SYNC, 24 τ after WAKEUP (t11; the
    WAKEUP value is unspecified in SBAS288K);
  - 1.04 µs /CS hold (t10).
- **Configuration after every RESET-pin pulse:**
  - STATUS = 0x00 (MSB first, ACAL off, buffer off);
  - ADCON = 0x01 (CLKOUT off, SDCS off, PGA 2);
  - DRATE as configured;
  - IO = 0x00 (D0–D3 outputs, low).

  Then SYNC, SELFCAL, wait for /DRDY, and read back all five registers. Data
  are accepted only from conversions that `ads_start()` began.
- **Integrity checks while running:**
  - A **register scrub** between points, and at least once per second in
    every state: RREG 0x00–0x04 against the shadow, with STATUS masked to
    bits 3:1.
  - A **stuck-code check**: 8 consecutive conversions of *both* channels
    returning exactly 0x000000, or exactly 0xFFFFFF. That is the signature
    of DOUT stuck low or high. With the ≥ 0.7-code noise at PGA 2, a real
    signal cannot produce it.

    Clip codes are deliberately excluded: with ±15 V off, both channels sit
    at −full scale, which is the AFE signature, not an ADC fault.

  Either failure re-initialises the ADC, and if armed, latches
  `F_ADC_COMM`. Together they catch an ADS1256 brown-out reset (CLKOUT back
  on, DRATE 30 k, PGA 1) and a dead DOUT, which would otherwise read as
  I = 0 and blind compliance.
- **Never used:** RDATAC/SDATAC, SYSOCAL/SYSGCAL, ACAL, BUFEN = 1,
  SDCS ≠ 00, OFC/FSC writes (v1).

**`heartbeat`** — the only edge generator on PC5. The numbers are in §4.4.

```c
void hb_enable(void);              // sets next_edge := now + 250 µs; last_edge := now
void hb_disable(void);             // PC5 low (BSRR), stays low
void hb_service(bool permitted);   // called once per main-loop pass, thread mode only
```

- `permitted == false` → `hb_disable()`.
- **Fixed grid.** Otherwise toggle when `now ≥ next_edge`, then
  `next_edge += 250 µs`.
  - The fundamental stays exactly 2 kHz. That sits in a sinc notch at 10,
    50, 100, 500, 1000 and 2000 SPS, so heartbeat coupling into the ADC is
    rejected.
  - Pass jitter only shifts individual edges, by ≤ 150 µs (duty 40–60 %
    typical).
  - If the loop falls more than one period behind (`now ≥ next_edge + 250`),
    it resynchronises with `next_edge := now + 250` and counts it in `DIAG?`.
    It never bursts to catch up.
- **Never toggle once per pass.** Passes of uneven length give an uneven
  duty cycle, which starves DAC_ARM down to 0.4–1.0 V (§4.4).
- **Gap fault.** The gap is checked before toggling, using raw 32-bit TIM2
  differences: `now − last_edge > HB_GAP_FAULT` (1.0 ms) → `F_LOOP_OVERRUN`.
  - In the worst case the trough falls below 1.49 V only for *sustained*
    half periods above 1.55 ms. A 1.0 ms limit (2.5× the normal 400 µs
    maximum) therefore catches a slow loop before the gate can chatter.
  - Hangs are the dead-man's job.
- **Debug freeze.** `DBGMCU_APB1_FZ.DBG_TIM2_STOP` must be 0, checked every
  pass (`F_INVARIANT`). A frozen TIM2 would hide a debugger halt, and the
  heartbeat would silently re-arm the gate at the old setpoint on resume.
  With TIM2 running, any halt over 1 ms ends in `F_LOOP_OVERRUN` and a
  zero.

### 7.5 Measurement engine (one point)

The point procedure is the measurement contract. Sweeps and `MEAS?` use it;
the background monitor uses the same conversion path.

```
input: setpoint V (µV, int32), settle_us, n_avg (1…256), data rate DR
1. code = scaling_v_to_code(V)    // ERR RANGE if |V| > v_limit or the calibrated code is outside 0…65535
2. if !dac_update(code): FAULT(F_DAC_SPI);  t_set = now()   // after BSY clear of frame 2
3. until now() ≥ t_set + settle_us:              // settle_us in [100 µs, 60 s]
       monitor I conversions may run (compliance, staleness); preempted at the deadline
4. for j in 0 .. n_avg−1:                          // I first: compliance acts on the first conversion
       order = (j even) ? [I, V] : [V, I]          // I,V,V,I,…: balanced for even n_avg
       for ch in order:
           sample = convert(ch)                    // §7.4
           if ch == I and |I(sample)| > i_compliance → compliance action, stop here
           accumulate: sum, offset-shifted sum of squares (int64), clip flags, timestamps
5. means (rounded to int32), standard deviations (raw codes; "-" if n_avg < 2),
   t_i, t_v = centroids of the I and V conversion midpoints
6. per-point checks (§7.7) → flags
7. push the result record (§7.8)
```

- **Ordering.**
  - **I first**, so that compliance needs only the settle time plus one
    conversion.
  - With an even `n_avg` the I,V,V,I pattern gives coincident V and I
    centroids, so a linear drift of the DUT (thermal, capacitive charging)
    does not bias V against I.
  - With `n_avg = 1` the record carries both centroids, so the V–I time
    offset is explicit.
- **Timing.** `T_point ≈ settle + 2·n_avg·(t18(DR) + 70 µs) + pass latency`.
  `SWP.RUN` returns that estimate, and the record holds the actual times.
  Typical:

  | DR | t18 | T_point (n_avg = 1, settle 1 ms) |
  |---|---|---|
  | 1000 SPS | 1.18 ms | ≈ 3.5 ms |
  | 50 SPS | 20.18 ms | ≈ 41.5 ms |
- **Compliance latency.** The worst case is settle + t18 + 0.2 ms from the
  DAC step that crossed the limit, or one monitor conversion during a long
  settle. It is post-hoc (§3.3).
- **Timestamps.** µs from the sweep start, from TIM2 extended to 64 bits.
  The jitter of `t_set` against the schedule is bounded by one pass
  (≤ 150 µs). A realised settle more than 200 µs over the request is
  flagged `LATE`.

**Defaults and ranges.** All are host-settable and stored in the result
metadata.

| Parameter | Default | Range |
|---|---|---|
| `adc_rate` | 1000 SPS | the v1 set above |
| `n_avg` | 1 | 1–256 |
| `settle_us` | 1000 | 100 µs – 60 s |
| `first_settle_us` | 50 000 (after the pre-ramp) | 100 µs – 60 s |
| `ramp_rate` | 10 V/s | 0.01–10 V/s |
| `v_limit` | 10.000 V | 0.001–10.000 V; in `ARMED`, never below \|held level\| |
| `i_compliance` | 50 mA (effectively off, under the hardware plateau) | 1 µA – 100 mA |
| `on_compliance` | `stop` | `stop`, `fault` |
| `line_hz` | 50 | 50, 60 |
| `host_timeout_s` | **30** | 0 (off) – 3600 |

Precision preset: `adc_rate = line_hz` (a mains notch per SBAS288K
Table 12), `n_avg = 2`. Use 10 SPS where both mains frequencies must be
rejected.

**Background monitor.**
- **When:** whenever no sweep point or `MEAS?` holds the ADC, including
  during settle times, ramps, `ARMING` and `DISARMED`.
- **What:** one I/V pair at least every 100 ms (during a settle: I only).
- **Used for:** `STAT?` values, the AFE, compliance and staleness checks.
- **Never** put in the result ring.
- **Preemptible:** a point's conversion starts with SYNC at its deadline,
  discarding any monitor conversion in flight.
- DAC steps may land inside a monitor conversion. That is acceptable,
  because monitor values serve limits only.

**PGA is fixed at 2 for both channels in v1, buffer off.** INA128 noise was
estimated from SBOS051G Fig. 6-11: output stage ≈ 115 nV/√Hz white, 1/f
corner ≈ 40 Hz; input stage 8 nV/√Hz. ADC noise is from SBAS288K Table 4.
At the ADC input:

| DR | INA128 | ADC (PGA 2) | ADC share of variance |
|---|---|---|---|
| 10 SPS | 0.26–1.43 µV | 0.20 µV | 2–37 % |
| 50 SPS | 0.58–1.77 µV | 0.39 µV | 5–31 % |
| 1000 SPS | 2.6–3.3 µV | 1.80 µV | 23–32 % |

So no higher PGA can cut σ by more than ≈ 21 %. Against that, a higher PGA
would bring:
- per-PGA gain calibration, since the source-impedance error grows with PGA;
- a smaller V range (PGA 4 clips beyond ±6.3 V);
- ADCON/SYNC/calibration restores between the V and I conversions.

Referred to the DUT at 10 SPS, σ is about 1.7–7 µV and 14–60 nA per
conversion. The current channel's real floor is the INA128 offset drift:
≈ 0.1 µA/°C typ, 0.88 µA/°C max. M5 measures the zero-input noise at PGA 2
and PGA 64. A per-channel PGA is added later only if those data justify it
(§13).

### 7.6 Sweep engine

A sweep is **a sequence of setpoints generated on the fly** from one of two
definitions, executed by the point procedure.

1. **Waypoint sweep** (`SWP.WP`): waypoints `w0 … wn`, a step `Δ`, and
   `cycles ≥ 1`. All values are integer µV.
   - **Exact grid:** every segment length `|w_{i+1} − w_i|` must be a
     non-zero integer multiple of `Δ`, and `Δ ≥ 0.5 mV`. Otherwise
     `ERR ARG` names the nearest valid step.
   - So the points are exact (`w_i + k·Δ·sign`), with no rounding, and the
     up and down branches of a loop land on **identical setpoints and hence
     identical DAC codes**, which is what a hysteresis comparison needs.
   - Points are `w0`, then each segment's points after its start up to and
     including its end. A turning point is measured once.
   - This one form covers linear (`0 5`), dual (`0 5 0`), bipolar
     hysteresis loops (`0 10 −10 0`, with cycles) and multi-segment
     trajectories.
   - `cycles > 1` repeats `w1 … wn` and needs `wn == w0`.
2. **List sweep** (`SWP.LIST.*`): up to 2048 host-uploaded points, each
   `V[:settle_us]`. Typical uses: log spacing, arbitrary waveforms,
   per-point dwell.

For both kinds:
- **Pre-ramp:** from the present output to the first point at `ramp_rate`,
  unrecorded, then `first_settle_us`.
- **End action:**
  - `end=zero` (default) ramps to 0 V;
  - `end=hold` holds the last point;
  - `SWP.ABORT` always ramps to 0 V.

  The state is `ARMED` afterwards.
- **Validation before running:** every point satisfies `|V| ≤ v_limit` and
  maps to a code in 0…65535 under the current calibration. Total points
  ≤ 1 000 000. The estimated duration is returned. A definition is rejected,
  never clipped.
- **Ramps:** DAC steps of ≤ 10 mV (≈ 33 codes), one per ≥ 1 ms (hence the
  10 V/s maximum). They are not recorded; the monitor watches compliance
  meanwhile.
- **Sweep identity:** each run gets a `sweep_id`. `EVT SWEEP_BEGIN` echoes
  the full configuration, the calibration record **and the session zero**
  (o_v, o_i, its time and data rate), so a data file is self-describing and
  raw codes can be reprocessed.

### 7.7 Per-point and background checks

Symbols:
- `V_set`: calibrated V_SWEEP for the code.
- `V_K`, `I`: the measurements.
- `R_s`: the calibrated series resistance outside U5's loop. It splits into
  - `R_top` = F1 + R30 + U12 (≈ 102–111 Ω): carries both the DUT current
    and the SENSE+ attenuator current;
  - `R_bot` = F2 + R10 (≈ 13–17.5 Ω): carries the DUT current only.
- `R_sd` = R12 + R32 = 50.2 kΩ.

The SENSE+ attenuator draws `V_hi/R_sd` from the DUT's high side through
`R_top` and returns it to AGND, never through R10.

| Check | Condition | Flag / action |
|---|---|---|
| **Clip** | either code ∈ {0x7FFFFF, 0x800000} | `OVR_V` / `OVR_I`; the value is not used by the other checks |
| **Compliance** | \|I\| > `i_compliance`, on each I conversion including monitor conversions | immediate zero code; `on_compliance` action (§6.4) |
| **Kelvin consistency** | `e = V_set − V_K − I·R_s − (V_K/R_sd)·R_top`. Flag if `|e| > tol`, where `tol` = 10 mV + 0.2 %·\|V_set\| + 5 Ω·\|I\| when calibrated, and 50 mV + 1 %·\|V_set\| + 15 Ω·\|I\| while `CAL_NOMINAL` | `KELVIN`; `EVT WARN KELVIN` after 3 consecutive points |
| **Current-limit plateau** | \|I\| ≥ 0.8·`i_plateau` **and** `e·sign(V_set) > tol` (U5 in limit leaves V_K short of the load line) | `CLIM`: the point is a plateau, never DUT data. `i_plateau` is measured at bring-up (§7.10). A DUT legitimately drawing 45–49 mA *on* the load line is not flagged |
| **Force path open** | \|V_set\| ≥ 1 V, \|I\| < max(2 µA, 4·σ_I(zero)), and \|V_K\| < 0.1·\|V_set\|. Before a valid `ZERO` the current threshold is 30 µA | `NO_OUTPUT`: "output disconnected: U12 open (±15 V missing or low), PTC open, or FORCE leads open". Never adapts calibration |
| **AFE**, gate open (before `ARM`) | a clip code on either channel, or **both** channels below −0.8 × full scale (±15 V off: both INA128 outputs near AGND) | blocks `ARM` (`ERR PRECOND afe`) |
| **AFE**, armed | a clip code | `F_AFE` |
| **Staleness** (armed) | no completed conversion for `T_STALE = 2·t18(DR) + 100 ms` | `F_STALE` |

**What the Kelvin check can and cannot see:**
- **Open SENSE+** is unmistakable: V_K reads near 0, so `e ≈ V_set`.
- **Open SENSE−** pulls U7 −IN to AGND. V_K then reads high by `I·R_bot`,
  about 15 Ω × I. That is detectable only once 10 Ω·\|I\| exceeds
  10 mV + 0.2 %·\|V_set\|, i.e. above ≈ 2–15 mA depending on V. The error it
  causes is the same 15 Ω·I, so it is small where it is undetectable.
- **Swapped leads** invert V_K's sign, so `e ≈ 2·V_set`.
- An open DUT with the Kelvin leads on its terminals reads `V_K ≈ V_set`,
  `I ≈ 0`, and passes, as it should.
- **Not separable:** U12 open versus both FORCE leads open; an open DUT
  versus an open FORCE lead when the Kelvin leads are open too.

These are flags for the operator, not proofs. **The Kelvin channel is not in
the output's control loop.** An open sense lead corrupts only the reported
V. It cannot drive the output, which is why it is a warning and not a fault.
A future DUT-voltage servo changes that (§13).

### 7.8 Result data path

- **Record** (binary, RAM, 44 bytes):
  - `seq`, `sweep_id`, `idx`, `seg`;
  - `t_set_us`, `t_i_us`, `t_v_us`: u32 µs relative to the sweep start;
  - `dac_code`;
  - `i_raw`, `v_raw`: mean raw codes, int32, rounded;
  - standard deviations: u16 raw codes, saturating, 0xFFFF = n/a;
  - `n`, `flags`.

  SI values are computed only when a line is formatted.
- **Rounding** the mean to an integer loses ≤ 0.29 code rms, against
  1–5 codes of noise per sample.
- **Time wrap.** u32 µs wraps at 71.6 min. `settle_us ≤ 60 s` and the
  per-point time bound keep consecutive records far less than 71 min apart,
  so the host unwraps monotonically. `SWEEP_BEGIN` carries the 64-bit start
  time.
- **Ring:** ≥ 1024 records (44 KB) in `.noinit` RAM. The engine writes; the
  protocol streams in order. **The sweep never waits for USB:**
  - a sweep of ≤ 1024 points can never lose data;
  - a longer one that the host lets fall more than 1024 records behind
    ends with `EVT SWEEP_END status=overflow`. Data are never silently
    dropped, and timing is never silently stretched.
- **Re-fetch.** `DATA.FETCH from=<seq>` re-sends retained records, for
  example after a host reconnect.
- **Throughput:** worst case at 3750 SPS with the 100 µs settle floor is
  ≈ 900 points/s × ~130 B/line ≈ 120 kB/s, far below USB FS CDC capacity.
- **RAM budget** (128 KB):

  | Item | Size |
  |---|---|
  | Ring | 44 KB |
  | List | 16 KB |
  | TinyUSB with 4 KB CDC TX | ≈ 7 KB |
  | Thread stack | 8 KB |
  | Handler stack | 1 KB |
  | Protocol buffers | 2 KB |
  | Everything else | ≤ 10 KB |
  | **Total** | **≤ 90 KB** |

  The map-file budget check at L0 enforces it.

### 7.9 Watchdog, resets, fault records

- **IWDG**, started at boot step 3:
  - kicked once per pass that completed within budget, **in every state
    including FAULT**, so a latched fault is not lost to a reset loop;
  - timeout 1.02–2.82 s, ≥ 2× the worst-case sector erase (§4.2);
  - not part of output safety: the gate, the dead-man and the fault
    handlers act within 77 ms, 2 ms and µs;
  - debug builds set `DBG_IWDG_STOP`, never `DBG_TIM2_STOP`.
- **Fault handlers.** HardFault, MemManage, BusFault, UsageFault and NMI
  (including **CSS**) share one naked entry. It runs on MSP's reserved
  region, so a thread-stack overflow still reaches it. The lowest 128 B of
  the thread stack are an MPU no-access region (larger than the 104 B
  exception frame with FP context), so an overflow raises MemManage at once;
  the canary is the word above it. In order:
  1. PC5 low;
  2. `dac_zero_safe()` (§7.4), frame-aligned with SPI-reset and bit-bang
     fallbacks;
  3. stacked PC/LR/xPSR, CFSR, HFSR, MMFAR, BFAR, the cause and the RCC
     clock state into the `.noinit` crash record (magic + CRC);
  4. `NVIC_SystemReset()`.

  They **never return**.
  - A CSS event (HSE failure) is handled the same way. On reboot the HSE
    check fails, and the instrument stays in `FAULT(F_CLOCK)` on HSI with
    USB off. Continuing to run after CSS would leave every µs timeout and
    the heartbeat schedule 6× wrong.
  - Boot zeroes the DAC again, ends in `DISARMED` or `FAULT`, and reports
    `crash=1` until `DIAG.CLR`.
- **Dead-man (TIM5).**
  - Reloaded at the end of every pass, active only while the heartbeat is
    permitted.
  - If no pass completes for 2 ms, the ISR runs the same steps 1–4 with
    cause `DEADMAN` and the interrupted PC. That PC is the hang location,
    useful for debugging.
  - It is masked during `CAL.SAVE`, which is legal only while disarmed.
- **Reset cause** is decoded from RCC_CSR, with PIN reported only when
  alone, then cleared with RMVF.

### 7.10 Calibration model and storage

**Applied model.** The nominal constants are computed in `scaling.c` from a
single table of component values. A CI check compares that table with the
values in `blocks/*.py`, so a hardware change cannot leave stale firmware
constants.

| Quantity | Model | Nominal |
|---|---|---|
| Setpoint | `V_set = g_dac·(code − c_dac)` | g_dac = 8.0321·2.5/65536 = 306.40 µV/code; c_dac = 32767.5 |
| DUT voltage | `V_K = g_v·(code − o_v)` | g_v = 2.5/(2²³−1)/0.199203 × (1 + 100 Ω/59.6 kΩ) = 1.496077 µV × 1.00168 = 1.49859 µV/code |
| DUT current | `I = g_i·(code − o_i)` | g_i = 298.0233 nV/24.0056 × 1.00168 = 12.4356 nA/code |
| Series resistance | `R_s`, `R_top` | 122 Ω, 107 Ω |
| Plateau | `i_plateau` | 49 mA (until measured) |

- The ×1.00168 factor is the typical buffer-off input-impedance gain error:
  the 100 Ω source against ZeffA ∥ ZeffB = 59.6 kΩ, SBAS288K Table 10. ADC
  self-calibration does not remove it.
- AVDD's deviation from 5 V adds a common-mode offset of ≈ 0.1 mV at the ADC
  input. The zero removes it.
- **Signs:** positive I is conventional current out of FORCE+, through the
  DUT, into FORCE−. Positive V_K means SENSE+ is above SENSE−. A passive
  DUT reads in quadrants I and III.

**Where each constant comes from:**

| Constant | Procedure | When |
|---|---|---|
| `o_v`, `o_i` | **`ZERO`**: all four jacks open (DUT removed), output at 0 V, armed or disarmed. 64 averaged pairs at the active data rate. With SENSE± open, R32/R33 pull U7's inputs to AGND, so V reads exactly its offset; with no current path, I reads its offset. Rejected if \|o_i\| > 30 µA or \|o_v\| > 3 mV (fixture not open?) | each session, and after any data-rate change or SELFCAL (which mark it stale). Drift is up to 0.88 µA/°C, so a 1 µA zero holds within about 1 °C for a worst-case part; `ZERO_STALE` after 1 h |
| `g_v`, `g_i` | host script (`iv-cal`): a 0.01 % reference resistor, **4-wire**, Kelvin leads on its terminals; a calibrated DMM for its voltage; several points of each polarity; least squares. Fitting g_i 4-wire also absorbs the SENSE− leg's diversion (V(S−)/R_sd ≈ 0.03 % of I) | bring-up, then periodically |
| `g_dac`, `c_dac` | DUT open, Kelvin leads on the FORCE terminals, I = 0: `V_SWEEP = V_K·(1 + R_top/R_sd)`, which corrects the attenuator current's 0.21 % drop across R_top (R_top nominal; its ±5 Ω uncertainty is ±0.01 %). Codes 485…64741 only, the DAC8552's specified linear range | after g_v |
| `R_s` | least squares on `y = a + R_s·I` with `y = V_set − V_K·(1 + R_top/R_sd)`, over ≥ 3 load currents of each polarity with \|I\| ≥ 5 mA. The intercept `a` absorbs the residual DAC/U5 offset | after the above |
| `i_plateau` | the measured plateau, smaller polarity (bring-up §7) | bring-up |

Every result line carries the raw codes, and `SWEEP_BEGIN` carries the
calibration record and the session zero, so the host can reprocess any data.

**Storage.**
- A 128-byte record: magic, version, board UID (96-bit), sequence, a
  host-supplied UTC timestamp, the coefficients above, a 32-character note,
  CRC-32.
- Written to flash **sectors 1 and 2** (16 KB each) alternately. A new record
  goes to the sector *not* holding the newest valid record, so an interrupted
  erase or program leaves the previous one intact. At boot the valid record
  with the highest sequence wins.
- `CAL.SAVE` only in `DISARMED`, ≥ `T_DISARM` after the last edge, with the
  dead-man masked: a sector erase stalls flash fetches for ≤ 500 ms.
- A record whose UID does not match this MCU is ignored, and nominal
  constants are used with a warning.
- The session zero lives in RAM and is not persisted.

## 8. Host interface

### 8.1 Transport

**USB 2.0 full-speed, CDC-ACM (virtual serial port)** implemented with
TinyUSB. It is driverless on Linux, macOS and Windows 10+, scriptable from
any language through a serial library, and fast enough with an order of
magnitude to spare (§7.8).

Rejected alternatives:
- vendor bulk / WinUSB: needs a driver story;
- HID: 64-byte reports at 1 ms are too slow and awkward;
- USBTMC: heavier, and few lab users need VISA here.

| Item | Value |
|---|---|
| VID:PID | **pid.codes** (VID 0x1209). Use the test PID 0x0001 during development; request a project PID before release (owner action, §14) |
| Serial string | STM32 96-bit UID as 24 hex characters. The host associates calibration and data with it |
| Product string | `Stage1 IV` + firmware version |
| Port discovery | the host selects by VID/PID/serial through `serial.tools.list_ports`. Never by a hard-coded device path; the repository's path guard (`tools/check_no_abs_paths.py`) would also reject one |

### 8.2 Session model

A **host session** exists while all of these hold:
- VBUS is present: PA0 measured above the attach threshold with
  hysteresis, debounced 20 ms (§4.1);
- the device is configured and not suspended;
- DTR is asserted (`tud_cdc_connected()`).

Opening the port with pyserial asserts DTR by default. Closing the port, or
the host process exiting, drops it.

| Event | Effect |
|---|---|
| Session starts | `EVT HELLO` with IDN, state, reset cause and crash flag |
| Session lost in `ARMING`/`ARMED`/`SWEEPING` | sweep aborted (`status=session_lost`) → `DISARMING`: ramp to 0 at ≥ 10 V/s with the heartbeat still on, a 1.1 s deadline, zero code, then heartbeat off (§6.3) |
| VBUS absent | `tud_disconnect()`: D+ pull-up off. A self-powered device must not drive D+ without VBUS. Reconnect when VBUS returns |
| No command for `host_timeout_s` (default **30 s**; 0 = off) in `ARMING`, `ARMED` or `SWEEPING` | `DISARMING`. This covers a host program that hangs with the port open, which DTR cannot see. The host library sends `PING` every 5 s from a background thread. Raw-terminal users can set 0 |

Hardware VBUS sensing is disabled (§4.7). PA9 is never given its OTG
alternate function: it is RESET_ADC.

### 8.3 Line protocol (protocol version 1)

- ASCII, one line per message, `\n`-terminated (`\r\n` accepted). Commands
  are ≤ 256 characters.
- **Host → device:** `VERB [arg …]`. Arguments are positional or `key=value`.
  Verbs are case-insensitive. Unknown keys are an error (no silent ignore).
- **Device → host**, four line types distinguished by their first token:
  - `OK [payload]` or `ERR <code> <text>`: **exactly one per command, in
    command order**;
  - `EVT <type> key=value …`: asynchronous events;
  - `PT <fields>`: data records;
  - `#…`: log lines (debug builds only; the host ignores them).
- **Numbers.**
  - Input is strict decimal with an optional exponent (`-2.5`, `1e-3`,
    `0.000125`). It is parsed to scaled integers (µV, nA, µs) by `decimal.c`,
    with no locale dependence and no float parsing.
  - Output uses fixed-point integer formats: V to 1 µV, A to 0.1 nA, times
    in µs.
- **One command at a time.** The device processes commands in order, and a
  command that starts a long activity replies `OK` at once. Completion is
  signalled by `EVT`. The host library matches responses to its single
  outstanding command and routes `EVT`/`PT` lines to handlers.

### 8.4 Command set (v1)

| Command | States | Response / effect |
|---|---|---|
| `*IDN?` | any | `OK IDN model=stage1-iv fw=<semver>+<git> proto=1 hw=<rev> build=<release\|debug\|bringup> serial=<uid>` |
| `STAT?` | any | `OK STAT state=… fault=… warn=… vset=… vk=… i=… cal=<valid\|nominal> zero_age_s=… reset=<cause> crash=<0\|1> session=… t_us=… mcu_temp=…` |
| `DIAG?` / `DIAG.CLR` | any | crash record, counters (loop max µs, ADC timeouts, DAC scrubs, late points), clear |
| `PING` | any | `OK PONG` (also resets the host timeout) |
| `ARM` | DISARMED | §6.2 preconditions; `OK`, then `EVT STATE ARMING` … `EVT STATE ARMED` |
| `DISARM` | ARMING/ARMED/SWEEPING | aborts any sweep; `OK`, then `EVT STATE DISARMING` … `EVT STATE DISARMED` |
| `SRC V=<volts>` | ARMED | ramps to the level; `EVT SRC_DONE` |
| `MEAS? [n=<k>]` | DISARMED/ARMED | one point at the present output (no DAC write); response carries a `PT`-format record |
| `CFG key=value …` / `CFG?` | not SWEEPING (`adc_rate` only in DISARMED) | `adc_rate n_avg settle_us first_settle_us ramp_rate v_limit i_compliance on_compliance end line_hz host_timeout_s kelvin_tol_mv`; validated as a set |
| `SWP.WP <v0> <v1> … step=<V> [cycles=<k>]` | not SWEEPING | defines a waypoint sweep; `OK points=<N> est_s=<t>` |
| `SWP.LIST.CLR` / `SWP.LIST.ADD <v>[:<settle_us>] …` / `SWP.LIST.USE` | not SWEEPING | builds and selects a list sweep (≤ 2048 points; ≤ 8 points per `ADD` line) |
| `SWP.RUN` | ARMED | `OK id=<sweep_id> points=<N> est_s=<t>`; then `EVT SWEEP_BEGIN …`, `PT …` lines, `EVT SWEEP_END status=<complete\|aborted\|compliance\|overflow\|session_lost\|fault> points=<n>` |
| `SWP.ABORT` | SWEEPING | ends at the next point boundary, then always ramps to 0 V (whatever `end` says); state `ARMED` |
| `DATA.FETCH from=<seq>` | any | re-sends retained records; `OK first=<seq> last=<seq>` |
| `ZERO` | DISARMED, or ARMED at 0 V | all four jacks open (DUT removed): measures both offsets (§7.10); `OK o_v=… o_i=… sd_v=… sd_i=…`, or `ERR RANGE` if outside the plausibility limits. Recorded with time, data rate and gate state |
| `CAL?` / `CAL.SET key=value …` / `CAL.SAVE ts=<unix>` / `CAL.DEFAULT` | `SET`/`SAVE`/`DEFAULT` only in DISARMED | read/modify the RAM record; persist (§7.10) |
| `ADC.SELFCAL` / `SELFTEST` | DISARMED | re-run SELFCAL, or POST incl. AFE; the report as payload |
| `FAULT.CLR` | FAULT | clears if the cause is gone and `T_DISARM` has elapsed |
| `RESET` | DISARMED/FAULT | `OK`, then a software reset |

**Bring-up build only** (compiled out of release builds; `*IDN?` reports
`build=bringup`). These support `bringup_checklist.md` §6:
- `HB.FREQ <Hz>`: 500 Hz–5 kHz. The gap fault scales to the half period
  + 0.5 ms. Above ~2 kHz the duty depends on pass length; that is acceptable
  for bring-up.
- `HB.EDGE`: one rising edge, then hold.
- `HB.HOLD high|low`.
- `DAC.RAW <a> <b>`:
  - needs `confirm=UNSAFE`;
  - is the single path that can write B ≠ FFFF, for the §6 negative test;
  - suspends the B-shadow invariant and the scrub until `DAC.RAW off` or any
    other DAC command, which restores both and rewrites the zero pair.
- `ADC.PGA <1…64>`: for the M5 noise table.
- `ADC.RESETPULSE`: a RESET_ADC pulse without the driver's knowledge, to
  test that the scrub detects it.
- `CRASH hardfault|hang|hang_irqoff`.

### 8.5 Data record (`PT`)

```
PT <sweep_id>,<seq>,<idx>,<seg>,<t_set_us>,<t_i_us>,<t_v_us>,<dac_code>,<v_set_V>,<n>,
   <i_raw>,<v_raw>,<i_sd_raw>,<v_sd_raw>,<v_V>,<i_A>,<flags_hex>
```

`*_sd_raw` is `-` when `n < 2`. `v_V` and `i_A` are formatted from the
raw means with the active calibration and session zero, using integer
arithmetic.

Flags:

| Bit | Name | Bit | Name |
|---|---|---|---|
| 0 | `OVR_V` | 6 | `LATE` |
| 1 | `OVR_I` | 7 | `FIRST` (first point after the pre-ramp) |
| 2 | `CLIM` | 8 | `CAL_NOMINAL` |
| 3 | `KELVIN` | 9 | `ZERO_STALE` (no `ZERO` this session, > 1 h old, or a data-rate change or SELFCAL since) |
| 4 | `NO_OUTPUT` | 10 | `MEAS` (record answers a `MEAS?`, not a sweep point) |
| 5 | `COMPLIANCE` | 11–15 | reserved, sent as 0 |

The host library writes CSV plus a JSON sidecar. The sidecar holds:
- `SWEEP_BEGIN` (configuration, calibration record, firmware version, serial);
- `SWEEP_END`;
- the host's own metadata.

### 8.6 Events and error codes

**Events:**
- `HELLO`, `STATE <name>`, `FAULT <code> detail=…`, `WARN <code> …`;
- `SWEEP_BEGIN`, `SWEEP_END`, `SRC_DONE`;
- `VBUS <0|1>` (logged on the next session);
- `EVT_LOST n=<k>`: the event queue overflowed. Records are never lost this
  way: the ring and `DATA.FETCH` cover them.

**Error codes:**
- `SYNTAX`, `UNKNOWN`, `ARG`, `RANGE`, `STATE`, `BUSY`;
- `FAULT` (latched fault blocks the command), `PRECOND` (an arm precondition
  failed; the text names it), `FLASH`, `INTERNAL`.

### 8.7 Host software (in this repository, `host/`)

- **`stage1iv` Python package (≥ 3.11, pyserial):**
  - discovery;
  - session handling (keeps DTR asserted; optional `PING` keep-alive);
  - a command/response client with timeouts;
  - typed sweep builders;
  - a streaming record parser with gap detection on `seq`, and an automatic
    `DATA.FETCH` on gaps;
  - CSV/JSON writers;
  - a calibration workflow.
- **CLI tools:**
  - `iv-diag`: IDN/STAT/DIAG;
  - `iv-sweep`: config file in, data out;
  - `iv-cal`: guided calibration with DMM entry, fit, `CAL.SET`, `CAL.SAVE`.
- The same package is the test driver for the simulator (§9.3), so the host
  library is exercised in CI on every change.

## 9. Test architecture

### 9.1 Levels

| Level | Runs where | Runs when | What it can prove |
|---|---|---|---|
| L0 Static | CI | every push | build (target + host) with `-Wall -Wextra -Werror -Wconversion`, cppcheck, RAM/flash budget from the map file; **link checks**: only `main.o` references `hb_service`; only `heartbeat.o`/`dac_zero_safe` access GPIOC BSRR; no `GPIOx->ODR` writes; `_dtoa_r`, `_malloc_r`, `_sbrk` absent; no bring-up symbols in release; nominal-constant table matches `blocks/*.py`; path guard |
| L1 Unit | CI, host gcc with ASan/UBSan | every push | logic of `core/` and `drivers/` against fakes, exhaustively where the input space allows |
| L2 Simulation | CI (sim binary + pytest via the host library) | every push | the whole application (state machine, engine, protocol, host library) against a board model with fault injection |
| L3 Target, no analog | bench: board powered with ±15 V **off**, logic analyser on SPI1/SPI2/PC5/DRDY/RESET | each firmware milestone | real timing, framing, USB, boot, resets, watchdog, heartbeat schedule |
| L4 Hardware-in-the-loop | bench, `bringup_checklist.md` §6–§8 | bring-up, then each release | gate/pump behaviour, output, measurement chain, safety reactions |
| L5 Metrology | bench, DMM + reference resistors + diodes | release | accuracy after calibration, noise, repeatability, hysteresis-free resistor sweeps |

### 9.2 L1 unit tests (Unity, host)

| Module | Tests that matter |
|---|---|
| `dac8552` | Exhaustive over all 65 536 codes: the frame pair is `00 hi lo` + `34 FF FF`; no other control byte or B value is producible. Fake SPI: /SYNC rises only after the transfer reports BSY clear and toggles between frames; a transport timeout returns false and leaves the shadow unchanged. **`dac_zero_safe()`** against a bit-level fake DAC pre-loaded with 0–23 bits of an earlier frame, with SPI BSY stuck (→ RCC reset path), and with SPI dead (→ bit-bang path): the DAC must end at A = 0x8000, B = 0xFFFF every time, within 200 µs of fake time |
| `ads1256` | Against a **register-level ADS1256 fake** modelling the command set, register map, free-running mux conversions (RDATA returns the last completed conversion; DRDY pulses every period after WAKEUP), the t17 update window, SELFCAL durations and reset defaults (ADCON 0x20, DRATE 0xF0, IO 0xE0). It **asserts timing**: t6 ≥ 6.51 µs; t11 per command class; t10; SCLK ≤ 1.92 MHz; mode 1. Cases: a stale pending edge at unmask (must be rejected, never returning the previous channel's data); a read past the deadline (must wait for the next edge and mark `late`, never read inside t17); no DRDY; register read-back mismatch; a spontaneous device reset (the scrub detects it within 1 s); DOUT stuck low/high (stuck-code check); short reads; preemption of a monitor conversion by `ads_start()`. Sign extension at 0x7FFFFF, 0x800000, 0xFFFFFF |
| `heartbeat` | With fake time and randomised pass lengths (5–150 µs): edges only when permitted; edges on the 250 µs grid (fundamental exactly 2 kHz over 10⁶ edges); never once per pass; resynchronise without bursts after a late pass; any spacing > 1.0 ms raises overrun; `hb_enable` sets `last_edge` (no false fault on the first `ARM`); `DBG_TIM2_STOP` set → `F_INVARIANT`; disable leaves the pin low |
| `scaling` | V↔code round trips with nominal and perturbed calibration; `v_limit` enforcement (reject, never clamp); ±10.000 V → codes 131/65404 nominal; code→V/I constants against hand-computed values; LSB formula |
| `sweep_gen` | Waypoint expansion on the exact grid: steps that do not divide a segment, steps < 0.5 mV and zero-length segments rejected; turning points measured once; up and down branches yield identical setpoints and codes; cycles; integer arithmetic over ±10 V × 10⁶ points; point counts and duration estimates; list mode limits (2048 points, 8 per line) |
| `safety` | A truth table of the per-point classifier over modelled cases, with calibrated and nominal tolerances: normal resistor and diode; a 45–49 mA DUT on the load line (no `CLIM`); the real plateau (`CLIM`); open SENSE+ (always `KELVIN`); open SENSE− (`KELVIN` only above the documented current); swapped Kelvin; U12 open; PTC open; open DUT with Kelvin connected (clean); ADC clip; AFE before arming for ±15 V off (block) and for a 1.5 V cell or battery DUT (no block); compliance on monitor and point conversions |
| `instrument` | Every state × every event, including faults in every state: FAULT entry runs `dac_zero_safe()` **before** heartbeat disable; `target := 0` on every entry to DISARMED, FAULT and ARMING; no transition re-arms without `ARM`; session loss and host timeout → DISARMING with the heartbeat still on, a ramp ≥ 10 V/s, and `F_DISARM_TIMEOUT` at 1.1 s if the ramp stalls; `v_limit` below the held level rejected; arm preconditions including BOR and AFE; `FAULT.CLR` timing; IWDG kicked in FAULT passes |
| `proto`, `decimal` | Grammar, every command's argument validation, state gating, number edge cases (exponents, overflow, `-0`); **libFuzzer** target on the line parser (no crash, bounded time) |
| `results` | Ring order, overflow semantics, fetch-from-seq, formatting of every field and flag |
| `calib` | CRC, version, UID mismatch, A/B selection including torn-write images (erased, half-programmed, stale-but-valid) |

### 9.3 L2 simulation

`port_sim/` replaces the hardware with a **board model** in C:
- `V_SWEEP` from the DAC codes and the gate state. The gate follows the
  pump's simulated DAC_ARM, driven by the real heartbeat scheduler's edges.
  So stopping the loop disarms the sim too, with the same `T_DISARM`.
- R_s; a DUT model (resistor, diode via Shockley with series R, capacitor,
  open, short); the current-limit plateau.
- INA gains, offsets and noise; the ADS1256 fake from L1.
- The U12 state, from simulated rail states.

The sim binary exposes the protocol on a pseudo-terminal. The pytest suites
drive it **through the real host library**. Fault injection uses a
side-channel control socket:
- DRDY stuck, SPI timeout;
- VBUS drop, DTR drop;
- open SENSE± leads, U12 open, ±15 V missing;
- a loop stall (a sleep inserted in a service), a HardFault (the sim exits
  and restarts with the noinit record).

**Acceptance suites:**
- every command;
- a ±10 V bidirectional resistor sweep (data equal to the model within the
  modelled noise);
- a diode hysteresis loop with cycles;
- a 5000-point sweep with the host stalled for 2 s (no loss);
- a 50 000-point sweep with the host stalled long enough to overflow
  (`status=overflow`, no silent loss);
- each injected fault → the documented state, event and output (V_SWEEP = 0
  within the modelled time).

### 9.4 What can be verified without the board, and what needs measurement

| Property | Verifiable without hardware (L0–L2) | Needs physical measurement (L3–L5) |
|---|---|---|
| B is always FFFF; DAC frames correct | ✔ exhaustive unit test | logic-analyser decode on SPI1 (L3) |
| Heartbeat only from the main loop, schedule within the band | ✔ schedule logic, symbol check | PC5 edge timing and jitter over hours (L3); DAC_ARM level, arm and disarm times at the chosen frequency (L4, checklist §6) |
| Arm/disarm/fault sequences and their order | ✔ state-machine tests | V_SWEEP and FORCE+ waveforms at arm, disarm, fault, reset, halt, crash (L4) |
| ADS1256 command sequences and delays | ✔ against the fake | the same timing on the analyser; behaviour of the real part (L3) |
| Scaling, calibration arithmetic | ✔ | the constants themselves (L4/L5) |
| Kelvin/force/AFE classification | ✔ against the model | true signatures on hardware: lifted leads, −15 V removed (checklist §8) |
| Data integrity, no silent loss | ✔ | sustained streaming to real hosts on Linux/macOS/Windows (L3) |
| USB enumeration, VBUS/DTR handling | partially (logic only) | real hosts, hubs, cable unplug mid-sweep (L3) |
| Timing repeatability (settle, t_set/t_i/t_v jitter) | logic only | on target with the analyser (DAC frame → WAKEUP spacing) (L3) |
| Front-end settling, minimum useful settle time | — | step response at TP1, FORCE+, TP4 and TP7 into representative loads (L4) |
| Noise per data rate and PGA, heartbeat-induced noise | — | zero-input conversions armed vs disarmed, 1 vs 5 kHz heartbeat (L4) |
| Current-limit plateau, R_s, U12 signature, thermal EMF | — | checklist §7–§8 |
| Watchdog timeout vs flash erase | arithmetic | timed IWDG reset and a timed `CAL.SAVE` (L3) |

## 10. Implementation milestones

Each milestone ends with its acceptance tests passing in CI (L0–L2) **and**
the listed bench checks, recorded in `docs/firmware/` with the measured
values. M0–M2 need no assembled board. M3–M4 deliver the test firmware that
`bringup_checklist.md` §6 and §8 ask for.

| # | Milestone | Content | Acceptance (measurable) |
|---|---|---|---|
| **M0** | Skeleton and CI | `firmware/` and `host/` trees; pinned submodules; CMake presets (target-debug, target-release, target-bringup, host-test, sim); linker script with the cal sectors; empty super-loop; Unity and pytest wiring; new workflow `.github/workflows/firmware.yml`, path-filtered to `firmware/**`, `host/**` and itself, leaving `build.yml` untouched | CI builds every preset with zero warnings; size report; unit and sim jobs run (trivial tests); `tools/check_no_abs_paths.py` still passes; reproducible ELF (same toolchain, same hash twice) |
| **M1** | Pure core | `scaling`, `decimal`, `sweep_gen`, `results`, `calib`, `dac8552` frames, `heartbeat` scheduler, `safety` classifier, `instrument` state machine, `proto` parser, each with its L1 tests | L1 suite green; branch coverage ≥ 90 % on `core/` and `drivers/`; the parser fuzzer runs 10 min clean in CI-nightly |
| **M2** | Simulator and host library | `port_sim` board model + pty; `ads1256` against the fake; `engine`; the `stage1iv` package and `iv-diag`/`iv-sweep`; protocol document `docs/firmware/protocol.md` | L2 acceptance suites (§9.3) green, including every fault-injection case |
| **M3** | Target platform and safe boot | `port_stm32`: startup (PSP thread stack, reserved MSP), early DAC zero with `dac_zero_safe()`, clocks, TIM2, GPIO table with LCKR, IWDG, reset cause, fault handlers + crash record, CSS → reset, dead-man (TIM5); USB CDC with VBUS measured on PA0 and the DTR session; LED patterns | **L3:** (a) analyser: the first SPI1 activity after NRST release is `00 80 00` then `34 FF FF`, ≤ 2 ms after reset; /SYNC never low during reset or init; PC5 low until armed. (b) SPI1 mode 1 at 6 MHz; /SYNC rises after the 24th falling edge; ≥ 100 ns high between frames. (c) enumerates on Linux, macOS and Windows; unplugging the cable soft-disconnects (D+ released within 50 ms), replug reconnects; PA9 static high throughout USB activity; VBUS attach/detach thresholds verified with a variable 5 V supply. (d) `CRASH hang` with the heartbeat permitted → zero pair on SPI1 within 2 ms (dead-man), reset, `crash=1 cause=DEADMAN`; `CRASH hardfault` → zero pair, correct PC; a deliberate stack overflow still produces a crash record; `CRASH hang_irqoff` → IWDG reset within its window. (e) 96 MHz verified by USB SOF timing and TIM2 against a reference. (f) `ARM` refused with BOR off |
| **M4** | Heartbeat, DAC, arm/disarm on hardware (bring-up firmware) | heartbeat on target, `ARM`/`DISARM`/`SRC`, bring-up commands (`HB.*`, `DAC.RAW`, `CRASH`) | **checklist §6**, every item, with the recorded values: DAC_ARM at the 2 kHz heartbeat ≥ 1.9 V (worst-case simulation 2.15 V); at 500 Hz (`HB.FREQ`) ≥ 1.6 V; time from the first edge to DAC_ARM > 1.49 V ≤ 10 ms (simulation ≤ 4 ms); single edge peak < 0.87 V; each disarm case to \|V_SWEEP\| < 0.2 V within ≤ 70 ms (PC5 low or floating: stop, SW1, halt, crash, +5 V removed) and ≤ 85 ms (PC5 held high) — the corrected limits of §4.4; **/SYNC stays high through SW1 reset**; re-arm after reset returns to the commanded value only after a new `ARM`. Plus: FORCE+ waveform at arm and disarm with A = zero code into 1 kΩ (record peak and duration; see §11 H8); V_SWEEP at codes for −10, −5, 0, +5, +10 V within ±1 % + 0.15 V of nominal (uncalibrated) |
| **M5** | ADC on hardware | ADS1256 init/POST/scrub/stuck-code check; the v1 data-rate set (≤ 3750 SPS); `MEAS?`; `ZERO`; the self-checks; bring-up `ADC.PGA`, `ADC.RESETPULSE` | **checklist §8, first item:** analyser shows SCLK 1.5 MHz mode 1, t6 ≥ 6.51 µs, t10 ≥ 1.04 µs, t11 per command, and no RDATA inside t17 over 10⁵ conversions at 3750 SPS; ADCON reads back 0x01, IO 0x00; SELFCAL done. AIN1−AINCOM at PGA 1 within 0.5 % of −2²²; AIN1−AIN3 within ±200 codes. `ADC.RESETPULSE` → detected by the scrub within 1 s. **Noise table** recorded: zero-input σ for V and I at 10, 50/60, 100, 1000, 3750 SPS, at PGA 2 and PGA 64, gate disarmed and armed (heartbeat on). This decides per-channel PGA (§13). `ZERO` offsets within the plausibility limits; the armed-minus-disarmed offset recorded |
| **M6** | Measurement engine and checks on hardware | point procedure (I-first ordering), monitor, compliance, CLIM, Kelvin, force-path, AFE, staleness | After `ZERO`, nominal gains: 1 kΩ and 100 Ω (0.01 %) loads read V and I within 1 % of the DMM at ±1, ±5, ±10 V, current-limited points excepted. Lifting SENSE+ flags `KELVIN` within 3 points at any current ≥ 10 µA. Lifting SENSE− flags it at \|I\| ≥ 10 mA (100 Ω load, ≥ 1.2 V), the documented detection limit. Removing −15 V at +5 V gives `NO_OUTPUT`, `KELVIN`, `OVR` or `F_AFE`, and **never unflagged data**. `AFE` blocks `ARM` with ±15 V off and does **not** block it with a 1.5 V cell across the jacks. Compliance at 1 mA into 1 kΩ: output at 0 V within settle + t18 + 1 ms of the crossing step, measured at FORCE+. The plateau is flagged `CLIM` at both polarities (checklist §7); a 220 Ω load at 45 mA (on the load line) is not flagged. Step responses at TP1, FORCE+, TP4 and TP7 confirm `SETTLE_MIN` |
| **M7** | Sweeps and data path on hardware | waypoint and list sweeps, cycles, ramps, end actions, streaming, `DATA.FETCH`, abort paths | ±10 V bidirectional sweep, 10 mV steps, of a **low-TCR (foil) 10 kΩ** resistor (≤ 10 mW; a 1 kΩ part self-heats enough to fake 0.9 µA of hysteresis). Linear-fit residuals within the ADS1256 INL budget (0.005 % FS); no step at 0 V; up and down branches at the same setpoints: ≤ 1 % of points beyond 3σ_diff, none beyond 5σ_diff (σ_diff = √2·σ), and a mean difference within 3σ_diff/√N. A diode loop shows the plateau flagged. 20 repeated sweeps: per-point σ consistent with the M5 noise table; t_set jitter ≤ 150 µs. Cable unplug mid-sweep → FORCE+ at 0 V within 1.1 s + `T_DISARM`, `status=session_lost` on reconnect, data before the unplug retrievable with `DATA.FETCH`. Host program killed (DTR drop), and host hung with the port open (30 s timeout) → `DISARMING` |
| **M8** | Calibration and persistence | `ZERO`, `CAL.*`, flash A/B, `iv-cal`; `R_s`, `R_top` and `i_plateau` fits | After `iv-cal`: the 100 Ω reference at ±1 V and ±3 V, and 1 kΩ at ±10 V, read within the release accuracy targets (§14, decision 1). Calibration survives power cycling. A corrupted newest record falls back to the older one. `CAL.SAVE` start-to-finish time is below the IWDG minimum timeout (1.02 s) with ≥ 2× margin. After calibration the Kelvin check uses the calibrated tolerance with no false `KELVIN` across a full ±10 V sweep of 100 Ω, 1 kΩ and a diode |
| **M9** | Hardening and v1.0 release | fault drills on hardware: debugger halt mid-sweep, `CRASH hang`, `CRASH hang_irqoff`, `CRASH hardfault`, −15 V removal, +5 V removal, USB unplug, host kill; 8 h soak of repeated sweeps; documentation; version tag | Every drill: FORCE+ at 0 V within its documented bound (µs for a fault handler, ≤ 2 ms for a hang, 52–77 ms + decay for `hang_irqoff` and a halt). A halt followed by a resume ends in `F_LOOP_OVERRUN` and never re-arms. Correct state, event and record after recovery. Soak: 0 faults, 0 lost records, maximum pass time < 150 µs and maximum edge spacing < 500 µs (from `DIAG?`) |

**Order rationale:**
- The safety-relevant logic (M1) and the end-to-end system (M2) are proven
  where every fault can be injected cheaply, before any hardware is
  energised.
- M3–M4 then produce exactly what bring-up §6 needs, with the most dangerous
  behaviours (DAC framing, B contract, heartbeat, arm order) already covered
  by tests.
- Measurement (M5–M7) builds on a verified safe output.
- Persistence (M8) comes late, because it carries the one operation
  (a flash erase) that stalls the CPU.

## 11. Hardware requirements: missing, contradictory or unverified

Ordered by consequence for the firmware. "Resolved" means resolved for the
firmware with the evidence named. Nothing here requires changing the
hardware before v1. Each item says what evidence or decision would close it.

| # | Item | Status | Firmware handling | Evidence or decision still needed |
|---|---|---|---|---|
| **H1** | **Heartbeat timing.** The handoff gives "≥ 1 kHz, 1–5 kHz recommended" and "~52 ms" disarm. Simulation shows: the pump also works at high frequency; an *uneven duty* is what starves it; sustained half periods above 1.55 ms lose the gate (worst case); disarm takes 61–63 ms worst case with PC5 low and up to 77 ms with PC5 left high. `bringup_checklist.md` §6 expects ≤ 60 ms | **contradiction in the bring-up limits** | 2 kHz on a fixed grid; any edge spacing > 1 ms faults; PC5 always left low; `T_DISARM` 100 ms (§4.4, §7.4) | bench §6 with the corrected limits (≤ 70 ms low / ≤ 85 ms high); **done:** `bringup_checklist.md` §6 now states these limits with this rationale |
| **H2** | **VBUS sensing on PA0.** The 100k/100k divider gives 2.5 V against a tested V_IH of 2.31 V; at VBUS = 4.4 V a digital read is not guaranteed | resolved in firmware | ADC1_IN0 measurement with hysteresis (§4.1) | none for v1; a later spin could use 100k/150k (3.0 V) for margin |
| **H3** | **The gate state cannot be observed.** DAC_ARM/DAC_VREF reach no ADC input | accepted for v1 | inferred arm state; Kelvin/force cross-checks; bench verification | optional next spin: DAC_VREF through ~10 kΩ to the spare ADS1256 AIN4 (reads 0 vs −2.5 V at PGA 1) |
| **H4** | **±15 V rails and U12 cannot be observed** | accepted | `AFE` signature before `ARM`; `NO_OUTPUT` classification; bench confirms both signatures (checklist §8) | optional next spin: rail dividers to spare AIN5/AIN6 |
| **H5** | **CS_ADC and SCK2 have no pull-ups or pull-downs**, so they float while the MCU is in reset and the ADS1256 may latch garbage | resolved in firmware | RESET-pin pulse at every boot, full register write and read-back, periodic scrub | none (measurement only, no safety effect) |
| **H6** | **REF5025 grade.** `bringup_checklist.md` §3 says "REF5025A: ±0.05 % initial". `validation/measurement_range.py` uses ±0.1 % ("standard grade"). TI's REF50 product page: "standard grade (REF50xxAI)", 0.1 % max and 8 ppm/°C; "high grade (REF50xxI)", 0.05 % and 3 ppm/°C. The fitted part is REF5025**AI**DGKT, the standard grade | **resolved: the checklist is wrong**, `measurement_range.py` is right | none: the calibration absorbs the initial error. The 8 ppm/°C drift scales the DAC span and both ADC channels; it is one of the inputs to the v1 accuracy target (§14) | correct the checklist: TP8 = 2.5000 V ± 2.5 mV; **done** (`bringup_checklist.md` §3) |
| **H7** | **TMUX1101 SEL has no hysteresis** and DAC_ARM decays slowly through the undefined 0.87–1.49 V band. The switch may chatter, and its supply current peaks at ~430 µA near 1.2 V | mitigated | A = zero code before stopping the heartbeat, and in fault handlers (SI-4), so chatter cannot move the output | bench: scope DAC_VREF and TP1 during the "infinite loop" disarm drill (no fault handler runs) and record any chatter at the last setpoint |
| **H8** | **Arm/disarm transient** [Calc, estimate]. When VREF steps, V_A (→ 1.25 V) and V_B (→ 2.5 V) slew at the same 1.8 V/µs. A arrives first, so V_A − V_B/2 peaks near 0.6 V for ~1.4 µs; ×8 at U3B, limited by OPA2277 slew, it may reach a few hundred mV for µs at FORCE+, even with A at the zero code | unverified | arm only at the zero pair (the best case firmware can choose). Not avoidable without breaking the B contract | bench: FORCE+ into 1 kΩ and open, at arm and disarm. If significant for target DUTs, a future spin could slow DAC_VREF with an RC (e.g. 100 Ω / 10 µF; ~0.16 % common gain change, calibrated) |
| **H9** | DAC8552 output at VREF = 0 is not specified | bench | none | checklist §3 safe-state residual (already listed) |
| **H10** | DAC8552 linearity is specified only for codes 485–64741 (≈ −9.89 … +9.80 V). Channel B at 0xFFFF is outside it | accepted | calibrate the DAC transfer inside that range; setpoints beyond it are less accurate but still measured | none |
| **H11** | ADS1256 ID value is undocumented in SBAS288K (community drivers assume 3) | resolved in firmware | register read-back is the comms test; the ID is logged | none |
| **H12** | PA4 sees R34's 5 V pull-up | resolved: PA4 is FT [DS10314] | never analog mode, never an internal pull | none |
| **H13** | OPA548 current-limit plateau is typical only (49.4 mA) | accepted (hardware record) | `CLIM` threshold from the bench measurement | checklist §7 plateau at both polarities |
| **H14** | **No numerical performance requirements exist** (DESIGN_DOCUMENT §1: "no numerical accuracy, resolution, noise, speed or temperature specification") | **missing requirement** | v1 targets proposed in §14 | owner decision (§14, item 1) |
| **H15** | BOOT0 tied low and no SWO: updates need an SWD probe | accepted | SWD flashing; future USB update via a jump to the ROM DFU bootloader (§13) | AN2606 check for the F411 DFU's use of PA9/HSE before implementing that |
| **H16** | `bringup_checklist.md` §6 needs firmware the plan must provide: frequency override, single edge, held-high pin, the B-negative test | resolved by the `bringup` build | §8.4 bring-up commands | owner decision on the unsafe B test (§14, item 3) |
| **H17** | `firmware_handoff.md` §4 says D1–D3 reset as inputs. True, but D0 is an output (CLKOUT) at reset; and only the RESET pin *or power-up* restore CLK1:CLK0 | clarified | IO = 0x00 after CLK = 00 (§7.4) | doc note only |
| **H18** | Bring-up limits that need firmware support but are not in the checklist: arm/disarm transient (H8), chatter (H7), heartbeat-induced ADC noise, loop latency | proposed additions | M4/M5 acceptance includes them | add to the checklist when hardware docs are next edited |
| **H19** | The design specification the SKiDL comments cite (`Stage1_IV_System_Design.md`, §C.1–§C.9) is not in the repository | missing | none: the repository text was sufficient for every firmware decision here | none for firmware |
| **H20** | **Brown-out reset is off from the factory**, and +5VA (DAC, TMUX1101) can outlast a sagging +3V3D (LD1117 dropout). The MCU could then run out of specification next to a powered gate | resolved in firmware and procedure | the flashing step sets BOR level 3; `ARM` refuses otherwise (§6.2) | none |

## 12. Independent review and how it changed the design

The plan was challenged in three rounds:
1. **Independent investigations** of four areas, each against primary
   sources: DAC8552/TMUX1101, ADS1256, STM32F411 + TinyUSB, and a numerical
   simulation of the heartbeat pump.
2. **A self-review** of the assembled draft.
3. **Two adversarial reviews** of the draft, checked against the SKiDL
   source and the datasheets: one on safety and failure modes, one on
   measurement correctness and implementability.

Each finding below was verified before it was accepted.

### 12.1 Investigations that overturned or sharpened assumptions

| Finding | Source | Effect on the design |
|---|---|---|
| The pump does **not** fail at high toggle rates. It fails on an *uneven duty* (0.4–1.0 V worst case) and on *sustained* half periods > 1.55 ms. Disarm worst case is 77 ms, not ~52 ms | pump simulation, two independent solvers | grid-scheduled 2 kHz heartbeat; 1 ms edge-spacing fault; `T_DISARM` 100 ms; corrected bring-up limits (H1). My initial assumption (fast toggling starves the pump) was wrong |
| 2.5 V on PA0 is only 0.19 V above the tested V_IH | DS10314 Table 53 | VBUS measured with ADC1 (H2) |
| TinyUSB connects at init regardless of VBUS, never sees an unplug without VBUS sensing, and its F4 board file reconfigures PA9 | TinyUSB 0.21.0 source | `tud_disconnect()` right after init; PA0 is the attach truth; do not reuse `family.c` |
| ADS1256 ID undocumented; CLK1:CLK0 survive the RESET *command*; t11 after WAKEUP unspecified | SBAS288K | read-back as the comms test; RESET *pin* at boot; 24 τ after WAKEUP |
| /SYNC raised on TXE discards the frame; no readback; linearity only for codes 485–64741 | SLAS430A | BSY-gated /SYNC; B rewritten every update and scrub; DAC calibration within the linear range |
| The REF5025**A** is the standard grade (0.1 %, 8 ppm/°C) | TI REF50 product page | the bring-up checklist's ±0.05 % is wrong (H6) |
| OPA2277 slews at 0.8 V/µs | SBOS079C | `SETTLE_MIN` 100 µs (not 20 µs) |

### 12.2 Review findings and dispositions

**Safety review:** 1 critical, 9 significant, 8 minor findings.
**Measurement review:** 4 major, 9 moderate, 4 minor findings.

All were verified. Those that changed the specification:

| # | Finding | Disposition |
|---|---|---|
| S1 (critical) | A fault-path DAC write could land mid-frame: `34` already shifted + `00 80 00` executes as `34 00 80`, loading B = 0x0080 while armed. The CSS NMI path re-entered the driver and returned | **Adopted.** `dac_zero_safe()` (§7.4): BSY-bounded wait, SPI1 RCC reset, /SYNC high ≥ 1 µs to abort any partial frame, then the pair, with a bit-bang fallback; unit-tested against 0–23 pre-loaded bits. CSS now resets through the common handler |
| S2 | A 2 ms gap limit misses a loop running steadily at 1.4–2 ms per pass, leaving the gate chattering at the live setpoint | **Adopted** as a 1.0 ms limit on every edge. The suggested extra windowed counter was not needed: a sustained 1 ms half period still gives 1.76 V worst case |
| S3 | Session loss was both a health fault and a ramp trigger; the ramp had no floor or deadline; a hung-open host was undetected | **Adopted:** a state trigger only, a ≥ 10 V/s ramp with a 1.1 s deadline, and a 30 s host timeout with library keep-alives (§14, item 2) |
| S4 / M4 | `F_STALE` would trip during long settles and SELFCAL; the SELFCAL time for 10 SPS was wrong (307 ms, not 202 ms) | **Adopted:** a preemptible monitor during settles; data-rate changes only in DISARMED; table corrected |
| S5 | The scrubs did not run during sweeps; a stuck DOUT reads I = 0 and blinds compliance | **Adopted:** a timed DAC scrub in every armed state; a register scrub ≥ 1/s; a stuck-code check |
| S6 | `DBG_TIM2_STOP` would let a resumed debugger silently re-arm the gate | **Adopted:** checked every pass; raw 32-bit gap arithmetic. A DWT cross-check was not adopted, because CYCCNT also stops in a halt |
| S7 | BOR is off from the factory; +5VA can outlast +3V3D | **Strengthened** to BOR level 3 as an `ARM` precondition: 3 wait states at 96 MHz need ≥ 2.7 V |
| S8 | No evidence for the pass budget: TinyUSB's unbounded spins, float `printf` via heap | **Adopted:** 2 events per `tud_task`; integer formatting and link checks; a 2 ms **dead-man** timer that zeroes and resets on any hang. SI-10 now names it as a "zero-only writer" |
| S9 | The AFE current test would block PV cells, batteries and charged capacitors | **Adopted:** a clip / both-channels-low signature instead |
| S10 | Skipping the zero on a DAC transport fault | **Adopted:** SPI reset, then bit-bang |
| S11–S18 | Stack overflow lockup; the R_s formula; disarm-time consistency; `HB.FREQ` range; `DAC.RAW` versus the invariants; held-level reset; ODR writes and LCKR; early-init timeouts | **Adopted:** PSP/MSP split; formula fixed; 52–77 ms everywhere; 500 Hz–5 kHz with a scaled gap; suspension rules; `target := 0`; BSRR-only, LCKR on PC5/PA4 (not PA5/PA7, which the bit-bang fallback needs); cycle-counted waits |
| M1 / M6 | The R_s formula; the attenuator current flows through R_top only; the open-circuit DAC calibration is 0.21 % low unless corrected | **Adopted** (§7.7, §7.10) |
| M2 | An open SENSE− is invisible at the draft's tolerance | **Adopted:** a calibrated tolerance; the detection limit (≈ 2–15 mA) is documented; the M6 criterion changed |
| M3 | A stale DRDY edge returns the previous channel's data; reads at 7500 SPS can fall inside t17 | **Adopted:** clear PR8 and NVIC pending after WAKEUP; an edge-age check; a read deadline; v1 capped at 3750 SPS |
| M5 | M7's statistics would always fail; resistor self-heating fakes hysteresis | **Adopted:** distribution-based criteria; a 10 kΩ foil resistor |
| M7–M13 | Compliance latency (V converted first); uncalibrated offsets; missing record fields; ZERO procedure; the undefined monitor; the NO_OUTPUT σ; crude CLIM | **Adopted:** I,V,V,I ordering; the M6 criterion "after ZERO"; `t_i`/`t_v` and the zero in `SWEEP_BEGIN`; one jacks-open `ZERO` with plausibility limits; the monitor defined and kept out of the ring; σ from ZERO; CLIM requires a load-line departure |
| M14–M17 | Quantitative PGA case; overhead; unspecified ranges; cuts | **Adopted:** the noise table; 70 µs per conversion; the ranges table; the exact-grid sweep rule; the 2 kHz fixed grid (a sinc notch). **Not adopted:** "raw codes only, no SI fields". With float `printf` banned, SI formatting is cheap integer arithmetic, and it keeps the protocol usable from a terminal |

### 12.3 What review could not settle

Every number from the pump simulation, the ±15 V-off signatures, the
arm/disarm transient (H8), TMUX1101 chatter (H7), and the INA128 noise
estimate are still **unmeasured**. The plan routes each one to a specific
bench acceptance (M4–M6) rather than treating it as settled.

## 13. Future extensions, and what the architecture already allows

None of these is in v1. Each is listed with what it needs, so that v1 does
not foreclose it.

| Extension | Needs | Notes |
|---|---|---|
| **Pulsed I–V** (return to a base level between points; reduces self-heating) | a per-point "off" phase in the engine (base V, t_off) | Minimum pulse ≈ settle + 2·(t18 + 70 µs) (≈ 1 ms at 3750 SPS). List mode can approximate it already |
| **DUT-voltage servo** (iterate the setpoint until V_K hits a target) | closed loop on V_K | **Safety constraint:** an open SENSE lead reads ≈ 0 V and would drive the setpoint to the limit. The correction must be bounded: \|V_set − V_target\| ≤ \|I\|·R_s + margin. A Kelvin warning must abort. Only then can it ship |
| **Constant-bias logging / time series** | a "monitor stream" mode | the ring, record format and timestamps already support it; it needs a 64-bit time origin and periodic re-zero guidance |
| **Per-channel PGA / current autorange** | ADCON + SYNC between conversions; per-(PGA, DR) gain calibration; OFC/FSC caching (readable/writable per SBAS288K) | **only if** the M5 noise table shows the ADC, not the INA128, limits the current floor |
| **7500 / 15 k / 30 k SPS** | RDATA inside the DRDY ISR, with the ISR owning SPI2 while a conversion is pending (monitor preemption would then go through the ISR too) | needed only for fast pulsed work |
| **USB firmware update** | `DFU` command (DISARMED only) → jump to the ROM bootloader | the heartbeat stops during the jump, so the gate opens. Needs AN2606 verification for the F411 (PA9, HSE detection) |
| **Hardware observability** (H3, H4) and a firmware-controlled disconnect (U12 drive, OPA548 E/S) | board revision | would turn "inferred armed" into "measured armed" and give a true output-off |
| **SCPI / VISA front end** | a translation layer in the host package | the device protocol stays as is |
| **Temperature compensation** | MCU temperature already reported; an external sensor near R10/U6 would be better | |

## 14. Settled decisions (owner review, 2026-10-27)

1. **Measurement performance — provisional targets, not guarantees.**
   After calibration: V ±(0.05 % of reading + 1 mV); I ±(0.1 % of reading +
   1 µA), the current-offset term valid only within about ±1 °C of the
   session-zero temperature. The lowest reliably measurable current is
   **not established**; it is determined by the bring-up noise and drift
   measurements (M5) and must not be claimed before them. Unit tests verify
   arithmetic and algorithms only; they say nothing about analog accuracy.
   The hardware tests that must pass before any target can be claimed are
   listed in [`verification.md`](verification.md).
2. **Host-connected operation only.** v1 never holds a bias without a live
   host session. Losing VBUS, enumeration, DTR, or 30 s of host silence
   (`host_timeout_s`, default 30, any received byte counts) disarms with a
   bounded ramp, reports the reason (`disarm_reason=`, `EVT WARN`, and in the
   next session's `EVT HELLO`), and requires an explicit `ARM`.
3. **DAC channel-B test.** The deliberate B = 0x0000 test exists only in the
   `bringup` build (`FW_BUILD_VARIANT=bringup`), behind `DAC.RAW ...
   CONFIRM=UNSAFE`. In every other build the command does not exist
   (`ERR UNKNOWN`) and the code is not compiled; a CI link check verifies the
   release image contains no bring-up symbols. Channel B is held at 0xFFFF
   whenever the output is armed in normal operation.
4. **USB identity.** VID 0x1209 (pid.codes). The project's planned PID
   designation is **500S — not registered, not assigned**, and not a valid
   hexadecimal PID, so it is never placed in a descriptor. Development builds
   use 0x1209:0x0001, the pid.codes PID reserved for private in-house testing
   (not unique; must not ship). Replacing it is a one-line build option
   (`FW_USB_PID`, `FW_USB_PID_REGISTERED`); see [`usb_identity.md`](usb_identity.md).
5. **Repository.** Firmware, host software, documentation and CI live in this
   repository (`firmware/`, `host/`, `docs/firmware/`,
   `.github/workflows/firmware.yml`).

Owner actions outside the software: register the PID; schedule the optional
observability improvements (H3, H4) for a future board spin.

## 15. Implementation notes and differences

The implementation (v0.1) follows this specification except where listed
here. Each item says what the code does and why; the code and
[`architecture.md`](architecture.md) are authoritative.

**Structure and build**

1. *Single PC5 writer.* `hb_service()` is called once per pass from
   `app_pass()` (`src/app/app.c`); the target `main.c` only boots and calls
   `app_init()`/`app_pass()`, so the simulator runs the identical loop. The
   link check therefore allows `hb_service` in `app.c.obj` only (§7.1 said
   `main.o`).
2. *Third-party sources* are fetched by commit hash with
   `firmware/tools/fetch_deps.sh` from `firmware/third_party.lock` instead of
   git submodules (§5, §7.3): submodules would put gitlinks into the
   repository index; the pinned fetch is equally reproducible and verified.
3. *TinyUSB 0.21.0* has no `usbd_control.c` (merged into `usbd.c`);
   `CFG_TUD_TASK_EVENTS_PER_RUN = 2` exists and is used. `SystemCoreClock` and
   `tusb_time_millis_api()` are provided by the port.
4. *Start-up.* Thread mode switches to PSP in the reset handler, before
   `main()` (§7.2 step 4 does it after the clock setup; earlier is strictly
   safer). The fault path switches to a separate 512-byte fault stack below
   the 1 KB handler stack. `crt0` is not linked (`-nostartfiles`): its
   `exit()` path would pull in stdio and the heap.
5. *Driver API.* The ADS1256 driver exposes `ads_start()` / `ads_take()` /
   `ads_idle()` instead of `ads_ready()` / `ads_poll()` (§7.4); the stuck-DOUT
   detector needs **32** consecutive identical all-zero or all-one codes
   covering both channels (§7.4 said 8), to keep the false-alarm probability
   negligible at the 0.7-code noise floor.

**Behaviour**

6. *ADC errors latch a fault in every state*, `DISARMED` included (§6.4 "a
   fault in DISARMED is still latched"; §7.4 had "if armed"). The ADC is
   re-initialised on entry; `FAULT.CLR` succeeds once it is healthy again
   (ADC faults) or after a passing POST re-run (`F_POST`).
7. *`SWP.ABORT` ends the sweep at once*; the point in progress is discarded
   (no partial record) instead of finishing it (§7.6 / §8.4 "next point
   boundary"): with settles up to 60 s an abort must not wait.
8. *Compliance in a sweep* zeroes the output immediately and produces **no
   record for the tripping point** (it has no complete I/V pair);
   `SWEEP_END` carries `trip_idx` and `trip_i_a`. Outside a sweep the trip is
   reported as `EVT WARN COMPLIANCE i_a=`. `MEAS?` sets the `COMPLIANCE` flag.
9. *`CAL.SAVE` is a deferred command.* It waits until no ADC conversion is
   in flight (the monitor is held off), then erases and programs: a
   conversion caught by the ≤ 500 ms erase stall would otherwise time out and
   latch `F_ADC_TIMEOUT` (found by the system tests).
10. *Calibration source* has a third value, `ram` (changed by `CAL.SET`, not
    saved). `CAL.SET`/`CAL?` resolutions are fixed (protocol.md §5.6) so that
    `CAL?` output round-trips exactly.
11. *Output ordering.* Events a command causes are held and sent after its
    `OK`. `SWEEP_BEGIN`, `SWEEP_END` and `STATE` events each carry their own
    watermark in a 2 KB ordered queue: each waits until every `PT` record
    pushed before it has been sent, and records pushed after it wait for it.
    A command is parsed only while the TX ring has room for its response and
    events and the ordered queue has room for everything the command can lead
    to (`PROTO_AFTER_RESERVE`); otherwise it waits in the RX ring. So a
    stalled host still reads every sweep between its `SWEEP_BEGIN` and
    `SWEEP_END` and loses no event its own commands cause (spec §7.1 did not
    define the priorities). Every other event (`FAULT`, `WARN`, `SRC_DONE`,
    ...) is not ordered: it goes out at once.
12. *Ramps* step on a 1 ms **grid** (`next += 1 ms`, resynchronised when more
    than a period late) so that the 10 V/s disarm ramp finishes ±10 V in
    1.0 s, inside the 1.1 s deadline whatever the pass jitter. An individual
    step may therefore follow a late one by less than 1 ms (§7.6 "one per
    ≥ 1 ms"); the average rate is exact and no step exceeds 10 mV.
13. *`RESET` and `CRASH`* run 50–200 ms after their `OK` is queued, so the
    response reaches the host before the USB device disappears.
14. *Monitor.* In `DISARMED` every monitor pair runs the gate-open front-end
    check (the ARM precondition needs one within 1 s). The pair is two
    consecutive conversions, I then V, at most t18 + 100 ms apart, so the
    check also runs at 2.5–10 SPS, where one conversion outlasts the 100 ms
    monitor period. During a settle the
    monitor starts an I conversion only if the remaining settle exceeds
    t18 + 1 ms (it would be preempted otherwise).
15. *Session zero.* A stale zero (older than 1 h, taken in an earlier
    session, or before a rate change/SELFCAL) is still applied and flagged
    `ZERO_STALE`; `ZERO` plausibility uses the active gains.
16. *Protocol surface.* `STAT?` adds `sweep_status=`; `DIAG?` adds ADC, DAC,
    point and session counters; `MEAS?` answers with key=value fields
    (protocol.md §5.3) rather than a PT-format record (§8.4); `PT`
    standard deviations have 0.1-code resolution; `CFG kelvin_tol_mv` (§8.4)
    is not implemented (the Kelvin tolerance is the fixed §7.7 formula);
    `CFG` is accepted in `DISARMED` and `ARMED` only; `DISARM` is also a
    no-op in `DISARMING`.
17. *LED* (PC13): off in BOOT, 50 ms blink every 2 s in DISARMED, on while
    the heartbeat runs, 4 Hz in FAULT.
18. *`CRASH HARDFAULT`* executes an undefined instruction with UsageFault
    disabled, so it escalates to HardFault; unused vectors also enter the
    HardFault path. A CSS NMI is recorded with cause `css`. `CRASH STACK`
    (bring-up) overflows the thread stack into the MPU guard (item 21).
19. *Compliance during a ramp.* A trip during the disarm ramp loads the zero
    code, which completes the disarm (it is not left to `DISARM_TIMEOUT`); a
    trip during an `SRC` ramp ends it with `SRC_DONE status=compliance`
    (bring-up `DAC.RAW OFF`: `status=aborted`). `SWP.RUN` is refused
    (`ERR BUSY`) while an `SRC` ramp runs, instead of silently replacing it.
    So `SRC_DONE` comes when a ramp reaches its target or is cut short at 0 V;
    a new `SRC` replaces the target (one `SRC_DONE`, for the last one), and
    `DISARM`, a fault or a session loss end a ramp with `EVT STATE` instead.
    With the output already at 0 V, the first trip is reported
    (`WARN COMPLIANCE`) and later ones are not, until the output is driven
    again.
20. *A fault during a sweep* goes from `SWEEPING` straight to `FAULT`, with
    no intermediate `STATE ARMED`. `EVT FAULT` is sent at once, so it can
    precede the sweep's last records when the host is behind;
    `SWEEP_END status=fault` and `EVT STATE FAULT` follow those records.
21. *Stack guard.* The lowest 128 B of the thread stack are an MPU no-access
    region (region 0, `PRIVDEFENA`; larger than the 104 B exception frame with
    FP context), so an overflow raises MemManage at once;
    the canary is the word above it, and the fault handler does not read a
    stacked frame that lies in the guard.
22. *ADC read window* is checked again when the read ends (§7.4); an
    overrun read is discarded like a missed deadline
    (`DIAG? adc_read_overruns`). `EXTI9_5` has priority over `OTG_FS`.
23. *Numbers and keys.* Integer keys accept the whole number grammar of
    protocol.md §3 (`1e3`, `+4`, `30.0`; `2.5` is inexact). Duplicate keys
    are detected case-insensitively, as keys are matched.
24. *Clocks and VBUS.* Any clock-initialisation failure ends on HSI with all
    buses at /1 and the PLL and HSE off, so the recorded clock state is the
    real one. A VBUS conversion without EOC within 5 ms reads as VBUS absent.
    The configuration descriptor is self-powered, 2 mA.
25. *`DISARM` and `SWP.ABORT` act on arrival.* While command parsing is held
    back (a deferred command, or no room for responses and ordered events
    because the host is not reading), a `DISARM` or `SWP.ABORT` line without
    arguments takes effect as soon as it is in the RX ring; it is answered
    in its place in the command order (`DISARM` as a no-op, an early
    `SWP.ABORT` with `OK`). Without this, a host that writes but does not
    read could leave the output armed behind held commands.

**Hardware documentation updated with this work:** `bringup_checklist.md`
(§3 REF5025AI tolerance; §6 disarm limits 70/85 ms with rationale, the
firmware commands, never re-arm after reset; §7 plateau as a nominal value
replaced by the measured `i_plateau`; §8 firmware procedures) and
`firmware_handoff.md` (current timing figures and where the firmware is
stricter than a requirement).

**Verification status.** Everything in §9.2 and §9.3 is implemented and
passing (inventory and results in [`verification.md`](verification.md)),
except the libFuzzer target for the line parser, which is not implemented;
the tokeniser is exercised by the protocol system tests instead. The L3–L5
levels need the board ([`limitations.md`](limitations.md)).
