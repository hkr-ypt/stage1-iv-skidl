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

* The client sends `PING` every 5 s (the firmware disarms after
  `host_timeout_s`, default 30 s, without any received byte), matches exactly
  one `OK`/`ERR` per command, routes `EVT`/`PT` lines, detects sequence gaps
  and re-fetches, and raises `SessionLost` when the port goes away.
* Values are checked for exactness before sending (1 µV, 1 nA, 1 µs, 0.1 SPS,
  1 µV/s; calibration resolutions in protocol.md §5.6).
* Sweeps on a `build=bringup` firmware are refused unless
  `allow_bringup=True` (`--allow-bringup`).
* Output: `<base>.csv` (17 protocol fields, decoded flags, unwrapped times)
  and `<base>.json` (IDN, SWEEP_BEGIN/END, CFG, CAL, host metadata).
  Accuracy figures are provisional until the bring-up measurements.

Tests: `python3 -m pytest -q host/tests` (the simulator suite runs when
`firmware/build/host/stage1_iv_sim` exists or `STAGE1IV_SIM` points to it;
`STAGE1IV_REQUIRE_SIM=1` makes its absence a failure).
