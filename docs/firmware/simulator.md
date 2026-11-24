# Whole-firmware simulator

`stage1_iv_sim` runs the **real portable firmware** (`firmware/src`, the same
objects as the target image) on a simulation of the board:

* `sim/port_sim.c` implements `port.h` with virtual, deterministic time: SPI
  transfers take their real duration (SPI1 6 MHz, SPI2 1.5 MHz), each loop
  pass costs `--pass-cost-us` (default 20 µs), and the IWDG (1.02 s) and the
  TIM5 dead-man (2 ms) are emulated;
* `sim/model_dac8552.c` decodes the DAC frames bit by bit (/SYNC framing,
  aborted frames, power-down bits, B ≠ 0xFFFF loads are counted);
* `sim/model_ads1256.c` models the ADS1256 command set, registers,
  conversions, /DRDY, self-calibration and resets, and records every SBAS288K
  timing violation (t6, t10, t11, SCLK, reads while not ready, ...) and every
  command other than WAKEUP or RESET sent while SYNC holds the part
  (`adc_viol_after_sync`: undocumented in the data sheet);
* `sim/model_board.c` models the charge pump and reference gate (driven by
  the firmware's real PC5 edges), V_SWEEP, the output stage with its
  current-limit plateau, R_top/R_bot, the Kelvin attenuators, the INA128
  outputs, the U12 force switch and the DUT (resistor, diode, cell, open,
  short).

**The analog model exists to exercise the firmware's logic.** It is not a
prediction of the instrument's accuracy, noise or settling; those are
measured at bring-up ([`verification.md`](verification.md)).

## Build and run

```sh
cd firmware && cmake --preset host && cmake --build --preset host
./build/host/stage1_iv_sim [--speed X] [--seed N] [--pass-cost-us N] [--ctl PATH] [--pty-link PATH]
```

It prints exactly two lines on stdout, then runs until `quit`, SIGINT or
SIGTERM:

```
PTY <pseudo-terminal slave path>
CTL <control socket path>
```

* The pseudo-terminal speaks the host protocol exactly like the USB port;
  open it with pyserial or `stage1iv.transport.SerialTransport`.
* `--speed X`: virtual time runs at X × wall time (default 1). `0` runs as
  fast as possible (host timeouts then elapse in virtual time: disable
  `host_timeout_s` or keep the host fast).
* DTR: a pty has no modem lines, so the firmware's DTR is "the control flag
  `dtr` (default 1) AND a client has the slave open".
* An emulated MCU reset (watchdog, dead-man, `RESET`, `mcu_reset`) behaves
  like a USB re-enumeration: hosts should close and reopen the port.
* An emulated reset re-runs `app_init()` but cannot clear the firmware's
  static variables as the target's startup code clears `.bss`. Every module's
  init resets its own state; the ADS1256 driver's state is reset by the POST
  that `app_init()` starts, so a conversion the previous run left pending can
  send one transaction to the freshly powered ADC model before that (the
  model counts it as a command while busy). That traffic does not exist on
  the target.
* The device-side TX ring stands for TinyUSB's CDC TX FIFO, 4 KB by default
  (`tx_limit`): what the host has not read stays there when it drops DTR or
  closes the pty, and reaches the next session ahead of its empty line and
  `EVT HELLO`, cut off mid-line, as on the target (protocol.md §1); what the
  departed client's pty queues held is dropped, like a host's tty buffers.
  A re-enumeration clears it, as SET_CONFIGURATION does in TinyUSB: a
  return of the link that the firmware sees (`configured` 0 → 1 across a
  loop pass, or its soft reconnect after it saw VBUS go); a VBUS or
  `configured` glitch no pass sees leaves the session and its output alone.
  While the link is down the host reads nothing. `configured` models a
  re-enumeration only: a suspend/resume, which on the target ends the
  session but keeps the FIFO, is not modelled. The one 64-byte packet the
  target's OTG FIFO can also hold is not modelled either.

From Python: `stage1iv.sim.SimulatorProcess` starts the simulator, opens an
`Instrument` on the pty and gives a control client (`sim.ctl`).

## Control protocol

UNIX stream socket; one command per line; reply `OK [key=value ...]` or
`ERR <text>`.

| Command | Effect |
|---|---|
| `vbus <mV>` | VBUS at PA0 ×2 (attach > 3800 for 20 ms, detach < 3600) |
| `dtr 0\|1`, `configured 0\|1` | host-side session flags |
| `dut open\|short\|resistor <Ω>\|diode <Is> <n> <Rs>\|cell <V> <Ω>\|capacitor <F>` | device under test (capacitor = open at DC) |
| `rails <pos 0\|1> <neg 0\|1>` | ±15 V present (a missing rail opens U12 and gives the AFE signature) |
| `sense_plus_open 0\|1`, `sense_minus_open 0\|1`, `force_open 0\|1` | lifted leads |
| `ilim <A>` | current-limit plateau |
| `noise <codes>` | ADC noise σ |
| `fail_spi1 <n>`, `fail_spi2 <n>` | the next n SPI transfers time out |
| `adc_stuck none\|low\|high`, `adc_drdy_dead 0\|1`, `adc_reset` | ADC faults (DOUT stuck, no /DRDY, spontaneous reset) |
| `stall <µs>` | the next loop pass takes this long (dead-man / gap tests) |
| `bor 0..3`, `tim2_frozen 0\|1` | option-byte BOR level, DBG_TIM2_STOP |
| `tx_limit <bytes>\|-1` | limit the device-side TX FIFO (0 stalls the host link; −1 = the default, 4096 bytes like the target's CDC TX FIFO) |
| `mcu_reset`, `power_cycle` | MCU reset (DAC/pump/ADC/flash/crash record survive) or full power cycle (flash survives) |
| `crash hardfault\|hang\|hang_irqoff` | emulated fault handler, hang (dead-man or IWDG) |
| `obs?` | one line of key=value: `t_us v_sweep vref_dac v_arm gate gate_on_events gate_off_events v_dut i_dut v_hi v_lo i_shunt in_limit dac_a dac_b dac_pd_a dac_pd_b dac_frames dac_aborted dac_reserved_violations dac_b_not_fs_loads hb_edges hb_level hb_last_edge_us led passes resets resets_requested reset_flags crash crash_cause iwdg_kicks iwdg_fires deadman_enabled deadman_fires adc_conversions adc_selfcals adc_viol_*  vbus_mv dtr configured connected client cal_erases cal_programs hung` |
| `time?` | `OK t_us=<virtual time>` |
| `quit` | exit |

## What is and is not modelled

| Modelled | Not modelled |
|---|---|
| frame-level DAC behaviour, pump/gate timing from the pump simulation (arm in ~3 edges, disarm ≈ 52 ms PC5 low / ≈ 62 ms high), V_SWEEP clamp ±13.8 V | DAC INL/DNL, settling, glitches, temperature |
| ADS1256 protocol, timing rules, conversion schedule, ±FS clipping, Gaussian noise | digital-filter dynamics, input-impedance error, reference noise |
| DC operating point of the DUT network, current limit, U12 open, lifted leads | dynamics (capacitive DUTs, oscillation), thermal EMF, offsets drift |
| USB session (VBUS, enumeration, DTR), host stalls | USB timing, enumeration on real hosts |
| flash erase/program with tearing; IWDG, dead-man, fault handler effects | flash bus stalls, exact LSI tolerance |
