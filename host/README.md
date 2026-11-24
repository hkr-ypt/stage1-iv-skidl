# stage1iv — host library and tools

Python ≥ 3.11, `pyserial==3.5`. Protocol: `docs/firmware/protocol.md`.

```sh
pip install -e host            # or: PYTHONPATH=host python3 ...
iv-diag                        # identity, status, diagnostics, calibration, configuration
iv-sweep --wp 0 5 -5 0 --step 0.05 --cfg n_avg=2 settle_us=2000 --out run1
iv-cal --r-ref 100 --note bench1
```

Devices are found by USB VID 0x1209 / PID (default 0x0001, the pid.codes
private-testing PID — not unique; set `--pid` or `STAGE1IV_PID` for a
registered one) and optionally `--serial <24 hex>`; `--port` selects a port
by name. A fixed device path is never assumed. `--sim` runs any tool against
the firmware simulator.

```python
from stage1iv import Instrument
from stage1iv.sweeps import hysteresis_loop
from stage1iv.writers import write_sweep

with Instrument.open_serial(serial="0034002B3436511233353533") as inst:
    inst.cfg(n_avg=2, settle_us=2000)
    inst.sweep_waypoints(hysteresis_loop("2", "0.01", cycles=2))
    inst.arm()
    try:
        result = inst.run_sweep()          # gap recovery with DATA.FETCH
    finally:
        inst.disarm()
    write_sweep("loop", result, idn=inst.idn(), cfg=inst.command("CFG?"),
                cal=inst.command("CAL?"))
```

* **Host timeout and keep-alive.** While the output is armed the firmware
  disarms when no byte arrives for `host_timeout_s` (default 30 s, `0` =
  off); this covers a program that hangs with the port open, which DTR
  cannot see. The library therefore sends `PING` only *while one of its calls
  is in progress* (command and deferred-command waits, `arm`/`disarm`/`src`
  waits, `run_sweep`, `meas`/`zero`, calibration steps), every
  `min(5 s, host_timeout_s / 3)` (it tracks the `host_timeout_s` it sets or
  reads; `inst.keepalive_period`). **The device disarms if your program
  stops calling the library for `host_timeout_s`; use `inst.hold()` for
  deliberate idle holds:**

  ```python
  inst.src(5)
  with inst.hold():                  # PINGs for as long as the block runs
      reading = input("DMM reading: ")
  inst.keepalive()                   # or: one PING now, without waiting
  ```

  `run_sweep(on_record=...)` callbacks run outside the keep-alive (a callback
  that blocks for `host_timeout_s` lets the instrument disarm; use `hold()`
  inside it for deliberate long work). `iv-cal` holds while it waits for DMM
  readings. `Instrument(..., keepalive_interval=None)` disables the automatic
  keep-alive entirely; `hold()`/`keepalive()` still ping when called.
* The client matches exactly one `OK`/`ERR` per command, in order, routes
  `EVT`/`PT` lines, detects sequence gaps and re-fetches, and raises
  `SessionLost` when the port goes away. `DISARM` and `SWP.ABORT` are written
  at once, even while another thread waits for a deferred command (`MEAS?`,
  `ZERO`, ... up to 10 s), because the firmware acts on them on arrival;
  `disarm()` returns when the instrument reports DISARMED and also works when
  the command/response pairing is lost (until `resync()`).
* `run_sweep` sends `SWP.ABORT` when anything goes wrong while the sweep runs
  (timeout, Ctrl-C, an exception in `on_record`, a protocol error, a failing
  `DATA.FETCH`), then re-raises; `inst.last_sweep` keeps the records
  collected so far.
* Opening the port can first deliver up to 4 KB that an earlier program left
  unread when it closed the port (protocol.md §1). `open()` (with
  `hello_timeout`, as `open_serial` sets it) waits for `EVT HELLO` and for
  the input to pause; responses and records that arrive before the first
  command are dropped, and a later `HELLO` replaces an earlier one. After
  the first command, a new `HELLO` means the instrument restarted
  (`SessionLost`). `inst.wait_ready()` waits while the instrument is in BOOT
  (POST after power-up); the tools do this, except `iv-diag`.
* `arm()` retries `ERR PRECOND afe` for about 2 s plus two ADC conversions:
  ARM needs a front-end check younger than 1 s, which the firmware runs only
  while DISARMED, so an ARM right after a DISARM is briefly refused.
* Values are checked for exactness before sending (1 µV, 1 nA, 1 µs, 0.1 SPS,
  1 µV/s; calibration resolutions in protocol.md §5.6). A list point without
  its own settle time (`ListPoint(v)`, `volts` alone in a `--list` file, `v`
  or `[v, null]` in a `--config` list) uses the configured `settle_us`.
* Sweeps on a `build=bringup` firmware are refused unless
  `allow_bringup=True` (`--allow-bringup`).
* Output: `<base>.csv` (17 protocol fields exactly as the device printed
  them, never in scientific notation; decoded flags, including `SD_SAT`;
  unwrapped times) and `<base>.json` (IDN, SWEEP_BEGIN/END, CFG, CAL, host
  metadata). The suffixes are appended (`--out diode_0.5V` writes
  `diode_0.5V.csv`); existing files are replaced unless `overwrite=False`
  (`--no-overwrite`). `iv-sweep` checks that the output directory is
  writable before it starts, and if writing still fails afterwards it writes
  the files to the temporary directory instead and says where. It also
  writes what it collected when the sweep,
  the session or the final DISARM fails (sidecar `host.error`; `host.partial`
  and `sweep.status` `unknown` if the sweep did not end) and exits non-zero;
  errors are one-line messages, not tracebacks. `--keep-armed` only skips the final
  DISARM: it has no lasting effect, because the tool closes the port when it
  exits, DTR drops and the instrument disarms (session lost).
  Accuracy figures are provisional until the bring-up measurements.
* `iv-cal` says which of `g_dac`, `c_dac`, `g_v`, `g_i`, `r_s` are still
  nominal after it saves (`CalInfo.measured`, `CalInfo.nominal_core`): until
  all five are calibrated, records keep `CAL_NOMINAL` and the wide Kelvin
  tolerance.
* `v_V`/`i_A` (PT records, `MEAS?`) are computed by the firmware from the
  unrounded means; `i_raw`/`v_raw` are those means rounded to whole codes.
  The firmware's default `i_compliance` is 100 mA.

Tests: `python3 -m pytest -q host/tests` (the simulator suite runs when
`firmware/build/host/stage1_iv_sim` exists or `STAGE1IV_SIM` points to it;
`STAGE1IV_REQUIRE_SIM=1` makes its absence a failure).
