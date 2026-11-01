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

| Program | Tests | What it proves |
|---|---|---|
| `test_dac8552` | 18 | the frame pair for all 65 536 codes; no B/PD parameter reachable; /SYNC only after BSY clear; timeout leaves the shadow; `dac_zero_safe()` from 0–23 stray bits, with SPI stuck (reset path) and dead (bit-bang), within 200 µs |
| `test_heartbeat` | 15 | fixed 250 µs grid (fundamental exactly 2 kHz), no catch-up bursts, resync counted, gap fault > 1.0 ms checked before toggling, no false fault on enable, thread-mode only, bring-up holds |
| `test_ads1256` | 18 | init/read-back, channel separation, all rates with random pass lengths (5–150 µs), stale and glitch edges rejected, reads only inside the deadline, late samples, a read pushed into t17 by an 8 µs interrupt discarded (never delivered), preemption, /DRDY dead, SPI failure, spontaneous reset caught by the scrub ≤ 1 s, stuck DOUT, no false stuck detection; **zero SBAS288K timing violations** in the model |
| `test_safety` | 14 | the per-point classifier truth table (resistor, diode, 45 mA on the load line, plateau, open SENSE+, open SENSE− only above the documented current, swapped leads, force path open, open DUT with Kelvin connected, clipping), the AFE signature, invariant checks |
| `test_scaling` | 8 | V↔code (±10 V → 131/65404), reject-never-clamp, code→V/I |
| `test_sweep_gen` | 11 | exact grids, divisibility and suggestions, turning points once, identical up/down setpoints, cycles, limits, list mode |
| `test_results` | 9 | ring order, overflow only on never-sent records, rewind/fetch, skip at session start |
| `test_calib` | 11 | record layout, CRC (every single-bit flip detected), statuses, plausibility window edges, A/B alternation, torn erase/program at every position keeps the newest record, silent erase, UID mismatch, fallback to the older copy |
| `test_decimal` | 13 | strict decimal grammar (integers included), exactness, ranges, formatting |
| `test_model_dac8552`, `test_model_board` | 17, 9 | the models themselves (pump arm/disarm times against the pump simulation, V_SWEEP scaling and clamp, operating points, rails missing, open leads) |

### L2 system tests (whole firmware on the simulation port)

| Program | Tests | What it proves |
|---|---|---|
| `test_sys_safety` | 39 | boot ends DISARMED with the zero pair; ARM sequence and timing; preconditions (session, BOR, AFE; a charged cell does not block; the AFE check at 2.5–15 SPS, where one conversion outlasts the monitor period); DISARM zero code at the last edge; DTR/VBUS/unconfigure loss and host timeout disarm with the heartbeat running and never re-arm; slow pass → `LOOP_OVERRUN`; hang → dead-man zero + crash record + no re-arm; IWDG reset when hung disarmed; IWDG kicked in FAULT; SPI1 dead → bit-banged zero; ADC /DRDY dead, spontaneous reset, stuck DOUT; stack canary; FAULT.CLR timing; compliance stop/fault; MCU reset while armed; `RESET`; `v_limit` vs held level; flash only disarmed; bring-up verbs absent in release (and `DAC.RAW` scoped in bring-up); a compliance trip during the disarm ramp (DUT short, or a cell near Voc) completes the disarm; compliance during an `SRC` ramp reported (`SRC_DONE status=compliance`); `SWP.RUN` refused during an `SRC` ramp; a fault mid-sweep goes straight to FAULT; the DAC scrub only every 100 ms; a trip at 0 V reported once; `CRASH STACK` (bring-up) → MemManage record and zero; `DAC.RAW OFF` during an `SRC` ramp → `SRC_DONE status=aborted` (bring-up). **Every test also asserts the DAC model never loaded B ≠ 0xFFFF.** |
| `test_sys_protocol` | 31 | framing and error codes, one response per command in order, exact numbers, CFG all-or-nothing, identity, divisibility rule, a hysteresis loop with identical codes, records equal to the model, centroids, list settles, `v_limit`, abort, host stall without loss, explicit overflow with every pushed record delivered, `DATA.FETCH` during a sweep, re-fetch, event loss reported, MEAS?/ZERO, ZERO armed, calibration save/reload/torn write/round trip, KELVIN, NO_OUTPUT, CLIM, compliance ending a sweep, session loss during a deferred job; with the host not reading, two sweeps back to back still arrive as BEGIN, all records, END each; duplicate keys differing in case; integer keys in exponent form; with the host not reading, ten DISARM/ARM cycles and six back-to-back sweeps lose no event (commands wait instead); `DISARM` and `SWP.ABORT` behind held commands (full ordered queue, full TX ring, a deferred `MEAS?`) act at once and are answered in order |

### Host library (pytest)

| Suite | Tests | What it proves |
|---|---|---|
| `host/tests/test_*_unit.py` | 42 | protocol parsing/formatting (exactness, rejection), PT parsing, gaps, unwrapping, response matching with interleaved events, deferred timeouts, keep-alive PINGs interleaved without corrupting matching, an `SRC` ended by compliance, gap recovery with `DATA.FETCH`, bring-up refusal, session loss, exact sweep grids, calibration fits on known answers, CSV/JSON writers |
| `host/tests/test_sim_integration.py` | 16 | **the real host library against the real firmware in the simulator**, release and bring-up builds: identity, ±10 V resistor sweep equal to the model, diode hysteresis loop with identical codes, ZERO, session loss (port closed) and VBUS unplug mid-sweep, host timeout, ARM preconditions, loop stall → FAULT and zero, hang → dead-man reset and crash report, ADC fault, host stall without loss, explicit overflow, compliance, calibration persistence, bring-up `DAC.RAW` |
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
| H-3 | ADS1256 timing on the analyser: SCLK ≤ 1.92 MHz, t6, t11, t10, read windows (`adc_read_overruns` under USB load); SELFCAL completes after SYNC; register contents after init (CLKOUT off, D1–D3 low) | L3 | §7.4 contract |
| H-4 | Pass duration distribution (`DIAG? pass_max_us`) under USB load; dead-man drill (`CRASH HANG`) zeroes within 2 ms; IWDG drill (`CRASH HANG_IRQOFF`) resets in 1.0–2.8 s; HardFault and `CRASH STACK` (MPU guard) drills zero and record the crash | L3 | SI-9–SI-11 |
| H-5 | `CAL.SAVE` stall time and IWDG margin | L3 | SI-8 |
| H-6 | USB: enumeration on Linux/macOS/Windows, cable unplug mid-sweep, host process kill, hub, suspend; host timeout | L3 | SI-7 |
| H-7 | Gate: DAC_ARM at 2 kHz and 500 Hz, arm time, disarm times (checklist §6, limits ≤ 70 ms PC5 low/floating, ≤ 85 ms PC5 high) | L4 | `T_ARM`, `T_DISARM` |
| H-8 | Output: ±10 V into 1 kΩ, plateau current both polarities → `i_plateau`, PTC soak, snubber decision | L4 | CLIM, compliance |
| H-9 | Kelvin / U12 / AFE signatures: lifted SENSE±, −15 V removed, rails off at ARM | L4 | KELVIN, NO_OUTPUT, AFE |
| H-10 | Zero-input noise per data rate at PGA 2 (and PGA 64 with the bring-up `ADC.PGA`), armed vs disarmed, 1 vs 5 kHz heartbeat | L4 | noise floor, PGA decision |
| H-11 | INA128 offset drift vs temperature (current channel), R10 thermal EMF | L4/L5 | the ±1 µA term and its ±1 °C validity |
| H-12 | Calibration with a 0.01 % reference and a calibrated DMM, both polarities; independent verification points afterwards | L5 | the V and I accuracy targets |
| H-13 | Lowest reliably measurable current (from H-10/H-11 at the chosen settings) | L5 | may then be declared |
| H-14 | Front-end settling (TP1, FORCE+, TP4, TP7 step responses) | L4 | the 100 µs settle floor |

The targets in [`calibration.md` §5](calibration.md#5-accuracy-targets-provisional)
remain provisional until H-10 to H-13 are recorded.
