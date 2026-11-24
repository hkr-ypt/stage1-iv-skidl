# Verification: tests, CI, and what still needs hardware

Levels as in [`firmware_spec.md` §9.1](firmware_spec.md#91-levels). L0–L2 run
without the board and pass in CI; L3–L5 need the board and are listed in §4
as the measurements still required.

## 1. What runs, and how

```sh
firmware/tools/fetch_deps.sh
cd firmware
cmake --preset host && cmake --build --preset host && ctest --preset host
cmake --preset host-bringup && cmake --build --preset host-bringup && ctest --preset host-bringup
for p in fw-release fw-debug fw-bringup; do cmake --preset $p && cmake --build --preset $p; done
python3 tools/check_image.py build/fw-release build/fw-debug build/fw-bringup
python3 tools/check_nominal.py && python3 -m pytest -q tools/tests
cd ../host
STAGE1IV_REQUIRE_SIM=1 STAGE1IV_NO_SKIP=1 python3 -m pytest -q tests
STAGE1IV_REQUIRE_SIM=1 STAGE1IV_NO_SKIP=1 STAGE1IV_SIM=../firmware/build/host-bringup/stage1_iv_sim \
    python3 -m pytest -q tests/test_sim_integration.py
```

`.github/workflows/firmware.yml` runs all of it (plus coverage, cppcheck,
the reproducibility comparison and the registered-PID guard). A check that
cannot run fails: the simulator suite is required (`STAGE1IV_REQUIRE_SIM`),
a skipped pytest is an error (`STAGE1IV_NO_SKIP`), and `ctest` runs with
`--no-tests=error`.

## 2. Test inventory (all passing at the time of writing)

### L1 unit tests (Unity, host gcc with ASan/UBSan)

Counts are per program in the `host` (release variant) preset; where the
`host-bringup` preset runs a different number, both are given as
release / bring-up.

| Program | Tests | What it proves |
|---|---|---|
| `test_dac8552` | 19 | the frame pair for all 65 536 codes; no B/PD parameter reachable; /SYNC only after BSY clear; timeout leaves the shadow; `dac_zero_safe()` from 0–23 stray bits, with SPI stuck (reset path) and dead (bit-bang), within 200 µs; `dac_zero_safe()` raises /SYNC (≥ 1 µs) before it waits on or resets SPI1, with a partial frame in the DAC |
| `test_heartbeat` | 15 / 17 | fixed 250 µs grid (fundamental exactly 2 kHz), no catch-up bursts, resync counted, gap fault > 1.0 ms checked before toggling, no false fault on enable, thread-mode only, bring-up holds |
| `test_ads1256` | 21 | init/read-back, channel separation, all rates with random pass lengths (5–150 µs), stale and glitch edges rejected, reads only inside the deadline, late samples, a read pushed into t17 by an 8 µs interrupt discarded (never delivered), preemption, /DRDY dead, SPI failure, spontaneous reset caught by the scrub ≤ 1 s, stuck DOUT, no false stuck detection; **zero SBAS288K timing violations** in the model; a main-loop stall of one to two data periods after an edge gives one late sample, not a timeout, at all 13 v1 rates, and a /DRDY that dies during the stall still times out within 3/2 P + 2 ms of its end; the model counts any command but WAKEUP after SYNC (none sent); a SELFCAL the part ignores (model fault) is detected in init and in reconfiguration, after one retry |
| `test_safety` | 16 | the per-point classifier truth table (resistor, diode, 45 mA on the load line, plateau, open SENSE+, open SENSE− only above the documented current, swapped leads, force path open, open DUT with Kelvin connected, clipping), the AFE signature, invariant checks; a lifted SENSE+ at plateau-level current is `CLIM` and `KELVIN`, an active DUT on the plateau at the opposite polarity `CLIM` alone; the 100 µA pre-zero `NO_OUTPUT` floor |
| `test_scaling` | 8 | V↔code (±10 V → 131/65404), reject-never-clamp, code→V/I |
| `test_sweep_gen` | 11 | exact grids, divisibility and suggestions, turning points once, identical up/down setpoints, cycles, limits, list mode |
| `test_results` | 9 | ring order, overflow only on never-sent records, rewind/fetch, skip at session start |
| `test_calib` | 14 | record layout, CRC (every single-bit flip detected), statuses, plausibility window edges, A/B alternation, torn erase/program at every position keeps the newest record, silent erase, UID mismatch, fallback to the older copy; the `c_dac` window holds the DAC8552 worst case (≈ 615 codes) and refuses dropped or extra digits; the measured-coefficient bits round-trip, and in a record without them only coefficients that differ from nominal at `CAL.SET` resolution count as measured (the bring-up `i_plateau` record and round-tripped nominal gains stay nominal); that rule key by key, one step either side |
| `test_decimal` | 13 | strict decimal grammar (integers included), exactness, ranges, formatting |
| `test_model_dac8552`, `test_model_board` | 17, 9 | the models themselves (pump arm/disarm times against the pump simulation, V_SWEEP scaling and clamp, operating points, rails missing, open leads) |

### L2 system tests (whole firmware on the simulation port)

| Program | Tests | What it proves |
|---|---|---|
| `test_sys_safety` | 48 (1 ignored) / 49 | boot ends DISARMED with the zero pair; ARM sequence and timing; preconditions (session, BOR, AFE; a charged cell does not block; the AFE check at 2.5–15 SPS, where one conversion outlasts the monitor period); DISARM zero code at the last edge; DTR/VBUS/unconfigure loss and host timeout disarm with the heartbeat running and never re-arm; slow pass → `LOOP_OVERRUN`; hang → dead-man zero + crash record + no re-arm; IWDG reset when hung disarmed; IWDG kicked in FAULT; SPI1 dead → bit-banged zero; ADC /DRDY dead, spontaneous reset, stuck DOUT; stack canary; FAULT.CLR timing; compliance stop/fault; MCU reset while armed; `RESET`; `v_limit` vs held level; flash only disarmed; bring-up verbs absent in release (and `DAC.RAW` scoped in bring-up); a compliance trip during the disarm ramp (DUT short, or a cell near Voc) completes the disarm; compliance during an `SRC` ramp reported (`SRC_DONE status=compliance`); `SWP.RUN` refused during an `SRC` ramp; a fault mid-sweep goes straight to FAULT; the DAC scrub only every 100 ms; a trip at 0 V reported once; `CRASH STACK` (bring-up) → MemManage record and zero; `DAC.RAW OFF` during an `SRC` ramp → `SRC_DONE status=aborted` (bring-up). **Every test also asserts the DAC model never loaded B ≠ 0xFFFF.**; a `CAL.SAVE` pending across a session switch never erases flash while armed (`ARM` refused `busy`) and saves the record its command found, not what the next session put in RAM, with `seq` naming it and `source` saying whether RAM still holds it (also after a `CAL.RELOAD` of the older record); `ARM` refused while a `RESET` is pending; `ARM` answers `ERR FAULT` when its zero write fails; a fault during `SELFTEST` makes `FAULT.CLR` re-run POST; the conversion in flight at `SWP.ABORT` never lands in the next sweep; bring-up `HB.HOLD`/`HB.EDGE` cannot pump the gate while DISARMED; `RESET` writes the zero pair before the reset (simulation port, as on the target); (bring-up) a `CAL.SAVE` that outlives its session re-checks SI-8 at the erase and gives up after an `HB.EDGE` |
| `test_sys_protocol` | 55 | framing and error codes, one response per command in order, exact numbers, CFG all-or-nothing, identity, divisibility rule, a hysteresis loop with identical codes, records equal to the model, centroids, list settles, `v_limit`, abort, host stall without loss, explicit overflow with every pushed record delivered, `DATA.FETCH` during a sweep, re-fetch, event loss reported, MEAS?/ZERO, ZERO armed, calibration save/reload/torn write/round trip (values and mask), KELVIN, NO_OUTPUT, CLIM, compliance ending a sweep, session loss during a deferred job; with the host not reading, two sweeps back to back still arrive as BEGIN, all records, END each; duplicate keys differing in case; integer keys in exponent form; with the host not reading, ten DISARM/ARM cycles and six back-to-back sweeps lose no event (commands wait instead); `DISARM` and `SWP.ABORT` behind held commands (full ordered queue, full TX ring, a deferred `MEAS?`) act at once and are answered in order; an early abort never leaves a sweep started by a held `SWP.RUN` running; a waiting `DISARM` still acts after a held `ARM`, and undoes an `ARM` ahead of it in a 300-line backlog before the gate turns on; `SWP.ABORT` + `DISARM` in one packet both `OK`; blank-padded `DISARM`; a writer-only host that fills the receive buffer is disarmed by the host timeout; `ZERO` accepts the INA128P worst-case offsets and still refuses a connected DUT; an open SENSE+ on a low-impedance DUT is `KELVIN` on every point (`CLIM` as well at plateau current, never `CLIM` alone) with `EVT WARN KELVIN`, an active DUT (cell) on the plateau at the opposite polarity keeps `CLIM` without `KELVIN`, a plateau on a short still `CLIM`; the default compliance lets the plateau act; a ±15 V rail lost while armed latches `F_AFE`; compliance during a ramp acts within a conversion; no `LATE` at the 150 µs pass budget; `SD_SAT`; averaged means keep sub-code resolution in `MEAS?` and in sweep records; the whole firmware's ADS1256 traffic obeys the data sheet (SELFTEST, rate changes, `MEAS?`, a sweep); a late sample's time is its conversion's centre; on a reopened port the old session's cut-off tail arrives first and `HELLO` still starts a line; the VBUS back-feed releases the pull-up; a partial calibration stays `CAL_NOMINAL`, so does one with any of the five core coefficients nominal, and a coefficient set to its nominal value counts as nominal (a `CAL?` round trip keeps the mask); a damaged newest record is reported (`CAL_DAMAGED`) |

Target-only changes are not compiled by these host suites: in
`port_stm32/`, the forced and confirmed USB device mode with PA10 pulled
up (and the OTG interrupt masked if the mode check after `tusb_init()`
fails), the SPI2 stale-byte drain in `port_adc_xfer()`, the fault entry's
/SYNC-before-retune order, `port_system_reset()`'s zero write and the
`.bin` gap fill. They are verified by building all three images with
`-Werror`, `tools/check_image.py`, inspection of the disassembly (USB init)
and of the image (gap fill), and review. A fake-SPI harness outside the
repository showed the old `port_adc_xfer()` skewing every later byte after
one timeout and the new one staying aligned. The simulator mirrors
`port_system_reset()`. Their hardware checks are in §4 (H-3) and
`bringup_firmware.md` §2.

### Host library (pytest)

| Suite | Tests | What it proves |
|---|---|---|
| `host/tests/test_*_unit.py` | 110 | protocol parsing/formatting (exactness, rejection), PT parsing, gaps, unwrapping, response matching with interleaved events, deferred timeouts, keep-alive PINGs interleaved without corrupting matching, an `SRC` ended by compliance, gap recovery with `DATA.FETCH`, bring-up refusal, session loss, exact sweep grids, calibration fits on known answers, CSV/JSON writers; the keep-alive only during library calls and `hold()`, at `host_timeout_s / 3`, and past 32 unanswered PINGs at the slowest rate that keeps the session; `DISARM`/`SWP.ABORT` written at once behind a deferred command and not delayed by unanswered PINGs; a sweep aborted on any error; the bounded `ARM` retry of a stale front-end check and of an ADC still reconfiguring; `iv-cal` offsets never assumed zero; an old session's unread output before `HELLO` skipped on open (responses, records, fragments and events; a `HELLO` that follows an old one shortly after), a `HELLO` after the first command a restart; `wait_ready()` through BOOT and DISARMING, the tools waiting (not `iv-diag`); file naming, fixed-point numbers, both result files checked before a sweep and a rescue into a new temporary directory after it; `measured=` and the still-nominal report after `iv-cal` (also when `CAL?` cannot be read back), a fit equal to nominal sent one step away |
| `host/tests/test_sim_integration.py` | 28 | **the real host library against the real firmware in the simulator**, release and bring-up builds: identity, ±10 V resistor sweep equal to the model, diode hysteresis loop with identical codes, ZERO, session loss (port closed) and VBUS unplug mid-sweep, host timeout, ARM preconditions, loop stall → FAULT and zero, hang → dead-man reset and crash report, ADC fault, host stall without loss, explicit overflow, compliance, calibration persistence, bring-up `DAC.RAW`; an idle program disarmed by the host timeout while `hold()` and long calls keep it; `DISARM` from another thread during a long `MEAS?`; a sweep aborted by a failing callback; re-`ARM` right after `DISARM` at 2.5 SPS and right after an `adc_rate` change; the `iv-cal` gains step holding the output during a slow DMM reading and the dac step using the firmware's session zero; `iv-sweep` writing partial results on a lost session and waiting out BOOT; a reopened port's unread 4 KB tail delivered before `HELLO` and skipped by the client |
| `firmware/tools/tests` | 14 | the nominal-constant checker |

### L0 static checks (CI)

* `-Werror` with `-Wconversion -Wsign-conversion -Wshadow -Wdouble-promotion …`
  on host and ARM, all three variants;
* `cppcheck` (warning, performance, portability) on `src`, `sim`,
  `port_stm32`, both variants;
* `check_image.py`: no heap, no float printf, no bring-up symbols in
  release/debug (all present in bring-up), `hb_service` referenced only by
  `app.c`, `port_hb_write` only by `heartbeat.c`, `dac_raw_write` only by
  `commands.c` (bring-up), vector table at 0x08000000, nothing in the
  calibration sectors, `.noinit` not loaded, SRAM ≤ 90 KB (≈ 86 KB used);
* reproducible: two builds in different directories are byte-identical;
* `check_nominal.py`: every `nominal.h` value equals its part in `blocks/*.py`;
* the repository path guard.

Coverage (gcov, all host tests): about 87 % of lines and 76 % of branches in
`firmware/src`.

## 3. What these tests do **not** show

* Anything about analog accuracy, noise, drift, settling or the real
  current-limit plateau: the board model is a logic fixture.
* Real timing on the MCU: pass durations, the 250 µs grid's jitter, SPI
  waveforms, EXTI latency, flash stall times, LSI tolerance.
* USB on real hosts, hubs, cables, suspend/resume.
* The STM32 port's register code beyond compiling, linking, static analysis
  and the link checks (it has not run on hardware yet).

## 4. Hardware tests required before any claim

Nothing about performance or safety timing may be claimed until these pass
on a real board (records go into `docs/audit_corrections.md` §"What is
calculated, not measured" and the bring-up traveller):

| # | Test | Level | Needed for |
|---|---|---|---|
| H-1 | Boot and every reset kind: logic analyser on SPI1, PC5, /DRDY, /RESET — the zero pair precedes any PC5 edge; no frame with B ≠ 0xFFFF | L3 | SI-1, SI-3 |
| H-2 | PC5: 2 kHz grid, spacing histogram over ≥ 1 h, no edge in exception context, gap fault on a forced stall | L3 | SI-2 |
| H-3 | ADS1256 timing on the analyser: SCLK ≤ 1.92 MHz, t6, t11, t10, read windows (`adc_read_overruns` under USB load); SELFCAL completes after SYNC, WAKEUP (/DRDY high during the calibration); register contents after init (CLKOUT off, D1–D3 low) | L3 | §7.4 contract |
| H-4 | Pass duration distribution (`DIAG? pass_max_us`) under USB load; dead-man drill (`CRASH HANG`) zeroes within 2 ms; IWDG drill (`CRASH HANG_IRQOFF`) resets in 1.0–2.8 s; HardFault and `CRASH STACK` (MPU guard) drills zero and record the crash | L3 | SI-9–SI-11 |
| H-5 | `CAL.SAVE` stall time and IWDG margin | L3 | SI-8 |
| H-6 | USB: enumeration on Linux/macOS/Windows, cable unplug mid-sweep, host process kill, hub, suspend; host timeout | L3 | SI-7 |
| H-7 | Gate: DAC_ARM at 2 kHz and 500 Hz, arm time, disarm times (checklist §6, limits ≤ 70 ms PC5 low/floating, ≤ 85 ms PC5 high) | L4 | `T_ARM`, `T_DISARM` |
| H-8 | Output: ±10 V into 1 kΩ, plateau current both polarities → `i_plateau` (with `i_compliance` at its 100 mA default), PTC soak, stability on the plateau with the fitted R38/C49 (checklist §7) | L4 | CLIM, compliance |
| H-9 | Kelvin / U12 / AFE signatures: lifted SENSE±, −15 V removed, rails off at ARM | L4 | KELVIN, NO_OUTPUT, AFE |
| H-10 | Zero-input noise per data rate at PGA 2 (and PGA 64 with the bring-up `ADC.PGA`), armed vs disarmed, 1 vs 5 kHz heartbeat | L4 | noise floor, PGA decision |
| H-11 | INA128 offset drift vs temperature (current channel), R10 thermal EMF | L4/L5 | the ±1 µA term and its ±1 °C validity |
| H-12 | Calibration with a 0.01 % reference and a calibrated DMM, both polarities; independent verification points afterwards | L5 | the V and I accuracy targets |
| H-13 | Lowest reliably measurable current (from H-10/H-11 at the chosen settings) | L5 | may then be declared |
| H-14 | Front-end settling (TP1, FORCE+, TP4, TP7 step responses) | L4 | the 100 µs settle floor |

The targets in [`calibration.md` §5](calibration.md#5-accuracy-targets-provisional)
remain provisional until H-10 to H-13 are recorded.
