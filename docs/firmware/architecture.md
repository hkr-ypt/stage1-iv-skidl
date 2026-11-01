# Firmware architecture (as implemented)

This describes the code in `firmware/` as it is. The design rationale is in
[`firmware_spec.md`](firmware_spec.md); where the implementation differs from
the specification, [`firmware_spec.md` §15](firmware_spec.md#15-implementation-notes-and-differences)
lists it.

## 1. Layers

```
host/stage1iv (Python)  ── USB CDC line protocol (protocol.md) ──┐
                                                                  │
firmware/src/app/app.c         init + one super-loop pass          │
firmware/src/core/             portable logic, no registers        │
   proto.c      RX ring, tokeniser, TX ring, event/PT priorities  ◄┘
   commands.c   command table, validation, deferred responses
   session.c    VBUS debounce, soft connect, DTR, host timeout
   instrument.c state machine, arm/disarm/fault sequences, health
   engine.c     output ramps, ADC schedule, monitor, point
                acquisition, sweeps, jobs, calibration in RAM, zero
   safety.c     per-point classifier, AFE check, invariant checks
   config.c     CFG values          calib.c   record, CRC, A/B flash
   scaling.c    V<->code, code->V/I sweep_gen.c exact-grid sweeps
   results.c    1024-record ring    decimal.c strict fixed-point text
firmware/src/drivers/          device logic over port.h
   dac8552.c    frame pair, shadow, scrub, dac_zero_safe()
   ads1256.c    non-blocking init/convert/scrub state machine
   heartbeat.c  the only PC5 edge generator
firmware/src/port/port.h       the platform interface
   ├── firmware/port_stm32/    STM32F411 registers, TinyUSB 0.21.0
   └── firmware/sim/port_sim.c virtual time, DAC/ADC/board models
```

`core/` and `drivers/` never touch a register: they call `port.h` only. The
same objects run on the target, in the simulator and in the unit tests.

## 2. Execution model

Bare-metal cooperative super-loop (`app_pass()`), no RTOS, no dynamic
memory. Every service is a state machine with bounded work per call:

| Order | Call | Work per pass |
|---|---|---|
| 1 | `port_pass_begin()` | extend TIM2 to 64 bits; one ADC1 step (VBUS, temperature) |
| 2 | `port_usb_service()` | `tud_task()`, ≤ 2 events (`CFG_TUD_TASK_EVENTS_PER_RUN`) |
| 3 | `session_service()` | VBUS debounce, soft connect, session edges, host timeout |
| 4 | `proto_service()` | read RX, ≤ 1 command or a deferred completion, ≤ 2 `PT` lines, ≤ 512 TX bytes |
| 5 | `commands_service()` | a pending `RESET` / `CRASH` once its `OK` has left |
| 6 | `ads_service()` | ≤ 1 ADS1256 SPI transaction (≤ ~47 µs) |
| 7 | `engine_service()` | take a sample, jobs, sweep step, one ramp step, monitor start, DAC scrub |
| 8 | `inst_service()` | invariants, ADC errors, staleness, ARMING/DISARMING timing |
| 9 | `hb_service(inst_hb_permitted())` | at most one PC5 edge, gap check |
| 10 | `port_deadman_enable()`, LED | |
| 11 | `port_pass_end(healthy)` | TIM5 dead-man reload; IWDG kick if the pass ≤ 400 µs |

Interrupts (target): `OTG_FS` (TinyUSB queues events), `EXTI9_5` (timestamps
the first /DRDY edge after arming and masks itself), `TIM5` (dead-man, a
fault path), the fault handlers. No ISR writes PC5, SPI1 or SPI2 except the
zero-only fault paths, which may force PC5 low and call `dac_zero_safe()`.

The only operation allowed to exceed the pass budget is the flash
erase/program of `CAL.SAVE`, legal only in `DISARMED` (`app_note_long_op()`
excludes that pass from the overrun count; the IWDG timeout covers it).

## 3. Safety invariants and where they are enforced

| Invariant (spec §3.2) | Enforcement in code | Test |
|---|---|---|
| SI-1 B = 0xFFFF | `dac8552_frames()` has no B parameter; `dac_raw_write()` exists only when `FW_BRINGUP`; `safety_check_invariants()` checks the shadow every pass | `test_dac8552` (all 65 536 codes), `tearDown` of every `test_sys_*` checks the DAC model never loaded B ≠ 0xFFFF; `check_image.py` (no bring-up symbols in release) |
| SI-2 PC5 edges | `heartbeat.c` only (`port_hb_write`), fixed 250 µs grid, gap fault at 1.0 ms, called once per pass from `app.c` | `test_heartbeat`, `test_sys_safety` (spacing ≤ 1 ms, stall → `LOOP_OVERRUN`), `check_image.py` (single references) |
| SI-3 zero before the first edge | `app_init()` → `dac_zero_safe()`; `inst_arm()` → `engine_arm_prepare()` before `hb_enable()` | `test_sys_safety` arm/boot tests |
| SI-4 zero before the heartbeat stops | DISARMING ramps to the zero code, then `hb_disable()`; FAULT entry calls `dac_zero_safe()` first | `test_sys_safety` (DAC code at the last edge) |
| SI-5 codes only from `scaling_v_to_code()` | `engine.c out_load()` is the only `dac_update()` caller besides arm/zero | review; `test_scaling`, sweep validation tests |
| SI-6 never re-arm after reset | boot ends in `BOOT`→`DISARMED`/`FAULT`; nothing but `inst_arm()` leaves DISARMED | `test_sys_safety` (MCU reset, dead-man, watchdog, `RESET`) |
| SI-7 armed only in a session | `session.c` → `inst_disarm(DR_SESSION_LOST / DR_HOST_TIMEOUT)` | `test_sys_safety`, `test_sim_integration.py` (DTR, VBUS, unconfigure, timeout) |
| SI-8 flash only disarmed | `CAL.SAVE` gated to DISARMED and 100 ms after the last edge; runs as a job once the ADC is idle | `test_sys_safety`, `test_sys_protocol` |
| SI-9 health → FAULT | `inst_service()` (invariants, ADC errors, staleness), `hb_service()` gap, `engine` (DAC SPI, clip while armed, compliance=fault) | fault-injection tests in `test_sys_safety` and the simulator suite |
| SI-10 ISR set | fixed vector table (`startup.c`), EXTI ISR touches neither SPI2 nor PC5 | review, `check_image.py` |
| SI-11 IWDG | `port_pass_end(healthy)`; kicked in FAULT too | `test_sys_safety` (hang → IWDG reset; FAULT not lost) |

## 4. Data paths

**Command.** RX bytes → 1 KB ring (any byte refreshes host liveness) →
line assembly (≤ 256 characters, bad bytes and overlong lines answered with
`ERR SYNTAX`) → tokeniser → `commands_execute()` → response into the 4 KB TX
ring. A command is parsed only while the ring has room for its response and
the events it may cause (1792 bytes); events emitted during a command are held
and follow its `OK`.

**Measurement.** `engine.c` owns the ADC: the monitor (one I/V pair per
≥ 100 ms, I only during a settle, never recorded) yields to acquisitions,
which preempt a monitor conversion with `ads_start()`. A point: DAC load →
settle → I,V,V,I,… → means, deci-code standard deviations, centroids →
`safety_classify()` → `results_push()`. The ring holds 1024 records; a push
fails only when the oldest record was never sent, which ends the sweep with
`status=overflow`. Records stream to the TX ring when it has a margin left
for responses and events; `SWEEP_END`/`STATE` events wait until all earlier
records have been formatted.

**Calibration.** `calib.c` keeps a 128-byte CRC-32 record in flash sectors 1
and 2 (alternating, highest valid sequence wins, UID-bound, plausibility
checked); `engine.c` holds the active coefficients (`stored`, `nominal`,
`ram`) and the session zero.

## 5. Build variants

`FW_BUILD_VARIANT` = `release` | `debug` | `bringup` (CMake), visible as
`build=` in `*IDN?`/`EVT HELLO`, in the image file name
(`stage1_iv_fw-<variant>.elf`) and, for bring-up, in the USB product string
(`Stage1 IV BRINGUP`). Only `bringup` compiles `DAC.RAW`, `HB.*`, `ADC.PGA`,
`ADC.RESETPULSE` and `CRASH`; in other builds those verbs are unknown and the
code is absent (checked on the linked image). `debug` differs from `release`
by `-Og` and the IWDG debug freeze. See [`build.md`](build.md).

## 6. Memory (release image)

| Region | Use |
|---|---|
| Flash sector 0 | vector table + reset code (676 B) |
| Flash sectors 1–2 | calibration records A/B (nothing linked) |
| Flash sector 3+ | code + constants, ≈ 70 KB of 464 KB |
| SRAM | thread stack 8 KB at the bottom (128 B MPU no-access guard: overflow → MemManage; canary above it), `.data`/`.bss` ≈ 15 KB, `.noinit` ≈ 60 KB (result ring 44 KB, sweep list 16 KB, crash record), fault stack 0.5 KB, handler stack 1 KB: ≈ 86 KB of 128 KB (budget 90 KB, checked in CI) |
