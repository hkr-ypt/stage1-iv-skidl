# Firmware bring-up sequence

The order in which the firmware is brought up on a new board, and the
commands each step uses. It complements the hardware checklist
[`../bringup_checklist.md`](../bringup_checklist.md) (supplies, rails, the
analog checks), whose §6–§8 use these commands. Run every step with a
current-limited bench supply and **no DUT** until §7.

Tools: ST-LINK + OpenOCD (or STM32CubeProgrammer), a logic analyser (≥ 8
channels, ≥ 25 MS/s) on PA4 /SYNC, PA5 SCK1, PA7 MOSI1, PC5, PB12 /CS_ADC,
PB13 SCK2, PB14 DOUT, PA8 /DRDY, PA9 /RESET_ADC; a scope on TP1 (V_SWEEP),
DAC_ARM and FORCE+; `iv-diag` from `host/`.

## 1. Program option bytes and the release image

1. Option bytes: BOR level 3, software watchdog (`docs/firmware/build.md` §5).
2. Flash `stage1_iv_fw-release.elf`. `iv-diag` shows `build=release`,
   `usb_pid=0x0001 usb_pid_status=dev-test`, `bor=3`, `state=DISARMED`,
   `afe=ok` (with ±15 V on) or `afe=fail` (±15 V off).
3. `STAT? reset=por crash=0`; `DIAG? adc_state=idle adc_err=none adc_id=0x3`
   (the ID nibble is logged, not trusted).

## 2. L3 logic checks (±15 V off, analyser on)

| Check | How | Expect |
|---|---|---|
| Boot zero | trigger on NRST release (SW1) | the frame pair `00 80 00`, `34 FF FF` on SPI1 within ~2 ms of reset; no PC5 edge |
| No frame with B ≠ FFFF | decode SPI1 over a whole session | every `00 hh ll` is followed by `34 FF FF` |
| /SYNC rises after the last SCLK | zoom on frame ends | ≥ 0 ns after the 24th falling edge, ≥ 33 ns high between frames |
| ADC init | trigger on /RESET_ADC | RESET pulse ≥ 2 µs; WREG STATUS..IO; SYNC, WAKEUP, SELFCAL in one /CS frame (≥ 3.1 µs after each of SYNC and WAKEUP); /DRDY high during the calibration and falling after the Table 21 calibration time (if it never falls, init times out: report it); RREG read-back; SCLK ≤ 1.92 MHz; mode 1 |
| ADC timing | any conversion | t6 ≥ 6.5 µs (RDATA → data), t11 after SYNC/WAKEUP ≥ 3.1 µs, /CS hold ≥ 1 µs, RDATA only while /DRDY low; at 3750 SPS with USB traffic `DIAG? adc_read_overruns` stays at or near 0 (each one is a discarded read) |
| Monitor | DISARMED | an I/V pair about every 100 ms |
| USB device mode | SWD, after boot with the cable unplugged | OTG_FS GINTSTS (0x5000_0014) bit 0 CMOD = 0 and bit 1 MMIS = 0 (no device-register write was ignored); DCFG (0x5000_0800) bits 1:0 = 3 (full speed); DCTL (0x5000_0804) bit 1 SDIS = 1 (no D+ pull-up without VBUS); PA10 (OTG_FS_ID, unconnected) reads high |
| Pass time | `DIAG?` after 10 min with USB traffic | `pass_max_us` ≤ 150 (design), `pass_overruns=0` |

## 3. Heartbeat and the gate (checklist §6)

ARM needs ±15 V for the AFE check; with ±15 V off, `ARM` returns
`ERR PRECOND afe`, which is itself a check. For the gate checks power ±15 V,
1 kΩ across FORCE+/FORCE− with the Kelvin leads on it.

1. `ARM`: `OK`, `EVT STATE ARMING`, `EVT STATE ARMED` ~100 ms later. PC5 is a
   2 kHz square wave on a fixed 250 µs grid (analyser: spacing histogram
   100–400 µs, none > 500 µs). Measure DAC_ARM (simulated 2.70 V nominal,
   ≥ 2.15 V worst case at 2 kHz) and the time to 1.49 V.
2. Bring-up image (`stage1_iv_fw-bringup.elf`, `build=bringup`) for the
   frequency and single-edge tests:
   * `HB.FREQ 1000`, `HB.FREQ 500` (ARMED): DAC_ARM at 1 kHz / 500 Hz;
     `HB.FREQ 2000` restores.
   * `DISARM`, then `HB.EDGE` (DISARMED): one rising edge, PC5 held high;
     DAC_ARM peaks ≈ 0.5 V and decays; DAC_VREF stays 0. `HB.HOLD OFF`.
     Another `HB.EDGE` or `HB.HOLD HIGH` within 100 ms (T_DISARM) of the
     previous edge is refused (`ERR STATE`): repeated edges would pump the
     gate closed while DISARMED.
3. Disarm drills (A at +5 V: `SRC V=5`; repeat at −5 V). Record the time
   until |V_SWEEP| < 0.2 V:

   | Drill | Command | Firmware action | Expect |
   |---|---|---|---|
   | orderly | `DISARM` | ramp at 10 V/s, zero code, PC5 low | ramp ≈ 0.5 s, then the gate opens ≤ 70 ms after the last edge (DAC already at zero) |
   | hang, IRQs on | `CRASH HANG` (bring-up) | dead-man after 2 ms: zero pair, reset | |V_SWEEP| < 0.2 V within ~2 ms; `crash=1`, `DIAG? crash=deadman` |
   | hang, IRQs off | `CRASH HANG_IRQOFF` (bring-up) | none until the IWDG (1.0–2.8 s) | gate decay only: ≤ 70 ms if PC5 was left low, ≤ 85 ms if left high (record which) |
   | HardFault | `CRASH HARDFAULT` (bring-up) | fault handler: zero pair, crash record, reset | < 1 ms; `DIAG? crash=hardfault` |
   | stack overflow | `CRASH STACK` (bring-up) | MPU guard → MemManage: zero pair, crash record, reset | < 1 ms; `DIAG? crash=memmanage` |
   | debugger halt | halt from OpenOCD | none while halted | gate decay: ≤ 70 / ≤ 85 ms; on resume the dead-man (TIM5 kept counting) fires at once: zero pair, crash record `deadman`, reset (if it did not, the heartbeat gap check would latch `LOOP_OVERRUN`). TIM2 is never frozen (`DBG_TIM2_STOP` → `FAULT INVARIANT`) |
   | SW1 reset | press and hold SW1 | pins float (PC5 low/undriven) | gate decay ≤ 70 ms; after release the boot writes the zero pair and stays `DISARMED` |
   | +5 V removed | remove +5 V | DAC unpowered | ≈ immediate |

   After every drill: the instrument is `DISARMED` (never re-armed),
   `disarm_reason=reset` after a reset, and `ARM` is needed again.
4. Positive-bound contract (bring-up image, 1 kΩ only, briefly):
   `ARM`, `DAC.RAW 32768 0 CONFIRM=UNSAFE` → V_SWEEP ≈ +10 V while A is the
   0 V code; then `DAC.RAW OFF` (zero pair, B = 0xFFFF again). This exists
   only in the bring-up build; the release image answers `ERR UNKNOWN`.
5. Re-flash the release image after the bring-up tests. `*IDN? build=release`.

## 4. Session and host link

With the release image, 1 kΩ load, output at +3 V (`SRC V=3`):
* close the terminal / kill the host program → `DISARMING`, ramp, `DISARMED`;
  the next `EVT HELLO` says `disarm_reason=session_lost`;
* unplug USB mid-sweep → the same, D+ pull-up released (host sees the
  device vanish);
* `CFG host_timeout_s=5`, open a raw terminal, `ARM`, wait 5 s → disarmed
  with `host_timeout`.

## 5. Measurement chain (checklist §8)

* `ZERO` with all four jacks open; record `o_v`, `o_i`, `sd_v`, `sd_i` (codes).
* Noise: `MEAS? N=64` at 10, 50, 1000 and 3750 SPS (`CFG adc_rate=...`),
  armed and disarmed; bring-up `ADC.PGA 64` for the PGA comparison (scaling is
  not adjusted: noise only).
* `ADC.RESETPULSE` (bring-up): the scrub must report `FAULT ADC_COMM`
  within 1 s when armed (or re-initialise and latch when disarmed).
* Open-Kelvin and U12 drills: lift SENSE+ during a sweep → `KELVIN` flags and
  `EVT WARN KELVIN`; remove −15 V at +5 V → `NO_OUTPUT`.

## 6. Calibration

`iv-cal --r-ref 100 --note <id>` (0.01 % 100 Ω reference, DMM). Then measure
the plateau (checklist §7; `i_compliance` at its 100 mA default, so the
plateau, not the firmware, limits) and `CAL.SET i_plateau=<A>`,
`CAL.SAVE TS=<unix>`. Saving `i_plateau` before the `iv-cal` calibration is
fine: records stay `CAL_NOMINAL` (with the wide Kelvin tolerance) until
`g_dac`, `c_dac`, `g_v`, `g_i` and `r_s` are set too.
Verify with independent points (verification H-12).

## 7. First DUT

Only after §1–§6: a diode or resistor with known characteristics, small
`v_limit` and `i_compliance` first (`CFG v_limit=2 i_compliance=0.005`).
