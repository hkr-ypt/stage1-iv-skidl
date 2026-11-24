"""Shared fixtures: a scripted FakeDevice that follows docs/firmware/protocol.md
literally over an in-memory LoopbackTransport (no firmware needed)."""

from __future__ import annotations

import os
import threading
import time
from decimal import Decimal

import pytest

from stage1iv.client import Instrument
from stage1iv.transport import LoopbackTransport

IDN = ("OK IDN model=stage1-iv fw=0.1.0 rev=test proto=2 hw=stage1-iv@c4e7592 build={build} "
       "serial=0034002B3436511233353533 usb_vid=0x1209 usb_pid=0x0001 "
       "usb_pid_status=dev-test planned_pid=500S")


class FakeDevice:
    """One command at a time, one OK/ERR each; events/records interleaved."""

    def __init__(self, transport: LoopbackTransport, build: str = "release"):
        self.t = transport
        self.build = build
        self.state = "DISARMED"
        self.cfg = {"adc_rate": "1000.0", "n_avg": "1", "settle_us": "1000",
                    "first_settle_us": "50000", "ramp_rate": "10.000000",
                    "v_limit": "10.000000", "i_compliance": "0.100000000",
                    "on_compliance": "stop", "end": "zero", "line_hz": "50",
                    "host_timeout_s": "30"}
        self.points = 0
        self.seq = 0
        self.ring: list[str] = []
        self.drop_seqs: set[int] = set()       # PT lines "lost" on first send
        self.meas_delay = 0.0
        self.src_status = "reached"             # "compliance": the ramp ends at 0 V
        self.chatter = False                   # EVT/PT noise before responses
        #: verb -> responses sent instead of the normal one (one per command)
        self.override: dict[str, list[str]] = {}
        #: session zero: STAT? zero=..., SWEEP_BEGIN zero=... o_v=... o_i=...
        self.zero = "none"
        self.zero_o_v = "-"
        self.zero_o_i = "-"
        self.received: list[str] = []
        #: calibration in RAM, as CAL? prints it; measured= follows the
        #: firmware rule (a key set to a value other than its nominal one)
        self.cal_nominal = {"g_dac_uv": "306.401387", "c_dac": "32767.5000",
                            "g_v_uv": "1.498587", "g_i_na": "12.435568", "r_s": "121.0000",
                            "r_top": "106.0000", "i_plateau": "0.049000000"}
        self.cal = dict(self.cal_nominal)
        self.cal_measured = 0
        self._stop = False
        self._thr = threading.Thread(target=self._run, daemon=True)
        self._thr.start()

    def w(self, line: str) -> None:
        self.t.device_write_line(line)

    def stop(self) -> None:
        self._stop = True

    def _run(self) -> None:
        while not self._stop:
            line = self.t.device_read_line(timeout=0.05)
            if line is None:
                if not self.t.is_open:
                    return
                continue
            self.received.append(line)
            try:
                self.handle(line)
            except Exception as exc:  # pragma: no cover - test bug
                self.w(f"ERR INTERNAL {exc}")

    def _pt(self, sweep_id: int, idx: int) -> str:
        self.seq += 1
        v = Decimal(idx) / 10
        line = (f"PT {sweep_id},{self.seq},{idx},{1 if idx else 0},{1000 * idx},{1000 * idx + 300},"
                f"{1000 * idx + 600},{32768 + 326 * idx},{v:.6f},1,{100 * idx},{667 * idx},-,-,"
                f"{v:.6f},{v / 1000:.10f},0x{0x0380 if idx == 0 else 0x0300:04X}")
        self.ring.append(line)
        return line

    def handle(self, line: str) -> None:
        verb, *rest = line.split()
        v = verb.upper()
        if self.chatter:
            self.w("EVT WARN NOISE")
        if self.override.get(v):
            self.w(self.override[v].pop(0))
            return
        if v == "*IDN?":
            self.w(IDN.format(build=self.build))
        elif v == "PING":
            self.w(f"OK PONG t_us={int(time.monotonic() * 1e6)}")
        elif v == "STAT?":
            self.w(f"OK STAT state={self.state} fault=NONE warn=0x0206 disarm_reason=none "
                   "vset_v=0.000000 dac=32768 vk_v=- i_a=- mon_age_ms=- cal=nominal cal_seq=0 "
                   f"zero={self.zero} zero_age_s=- afe=ok bor=3 vbus_mv=5000 session=1 "
                   f"host_timeout_s={self.cfg['host_timeout_s']} "
                   f"reset=por crash=0 sweep_id=0 sweep_pts=0 sweep_status=none hb_edges=0 "
                   "mcu_temp_c=25.00 uptime_s=1")
        elif v == "DIAG?":
            self.w("OK DIAG passes=1 pass_max_us=10 overruns=0")
        elif v == "CFG?":
            self.w("OK CFG " + " ".join(f"{k}={val}" for k, val in self.cfg.items()))
        elif v == "CFG":
            for kv in rest:
                k, _, val = kv.partition("=")
                if k not in self.cfg:
                    self.w(f"ERR ARG unknown key {k}")
                    return
                self.cfg[k] = val
            self.w("OK")
        elif v == "ARM":
            self.w("OK")
            self.state = "ARMING"
            self.w("EVT STATE ARMING")
            self.state = "ARMED"
            self.w("EVT STATE ARMED")
        elif v == "DISARM":
            self.w("OK")
            self.state = "DISARMING"
            self.w("EVT STATE DISARMING reason=command")
            self.state = "DISARMED"
            self.w("EVT STATE DISARMED reason=command")
        elif v == "SRC":
            self.w("OK")
            if self.src_status == "reached":
                self.w(f"EVT SRC_DONE v_v={Decimal(rest[0].split('=')[1]):.6f} status=reached")
            else:
                self.w("EVT WARN COMPLIANCE i_a=0.0100000000")
                self.w(f"EVT SRC_DONE v_v=0.000000 status={self.src_status}")
        elif v == "SWP.WP":
            from stage1iv.sweeps import waypoint_sweep
            step = next(a for a in rest if a.upper().startswith("STEP=")).split("=")[1]
            cyc = [a for a in rest if a.upper().startswith("CYCLES=")]
            wps = [a for a in rest if "=" not in a]
            sw = waypoint_sweep(wps, step, int(cyc[0].split("=")[1]) if cyc else 1)
            self.points = sw.n_points
            self.w(f"OK SWP kind=wp points={self.points} est_s=0.10")
        elif v == "SWP.LIST.CLR":
            self.list_points = []
            self.w("OK")
        elif v == "SWP.LIST.ADD":
            self.list_points = getattr(self, "list_points", []) + list(rest)
            self.w(f"OK n={len(self.list_points)}")
        elif v == "SWP.LIST.USE":
            self.points = len(getattr(self, "list_points", []))
            self.w(f"OK SWP kind=list points={self.points} est_s=0.10")
        elif v == "SWP.RUN":
            if self.state != "ARMED":
                self.w(f"ERR STATE not allowed in {self.state}")
                return
            self.w(f"OK id=1 points={self.points} est_s=0.10")
            self.w(f"EVT SWEEP_BEGIN id=1 kind=wp points={self.points} t0_us=1 "
                   f"zero={self.zero} o_v={self.zero_o_v} o_i={self.zero_o_i}")
            self.w("EVT STATE SWEEPING")
            for i in range(self.points):
                ln = self._pt(1, i)
                if self.seq not in self.drop_seqs:
                    self.w(ln)
            self.w(f"EVT SWEEP_END id=1 status=complete points={self.points}")
            self.w("EVT STATE ARMED")
        elif v == "DATA.FETCH":
            frm = int(rest[0].split("=")[1])
            first = max(frm, 1)
            last = len(self.ring)
            self.w(f"OK first={first} last={last}")
            for ln in self.ring[first - 1:]:
                self.w(ln)
        elif v == "MEAS?":
            time.sleep(self.meas_delay)
            self.w("OK MEAS n=1 i_raw=100 v_raw=-200 i_sd=- v_sd=- v_v=-0.000300 "
                   "i_a=0.0000012436 flags=0x0700")
        elif v == "ZERO":
            time.sleep(self.meas_delay)
            self.w("OK ZERO o_v=-112.40 o_i=387.10 sd_v=2.10 sd_i=2.60 pairs=64 sps=1000.0")
        elif v == "CAL?":
            self.w(f"OK CAL source=nominal seq=0 measured=0x{self.cal_measured:02X} "
                   + " ".join(f"{k}={val}" for k, val in self.cal.items()))
        elif v == "CAL.SET":
            keys = list(self.cal_nominal)
            for kv in rest:
                k, _, val = kv.partition("=")
                bit = 1 << keys.index(k)
                self.cal[k] = val
                if Decimal(val) == Decimal(self.cal_nominal[k]):
                    self.cal_measured &= ~bit
                else:
                    self.cal_measured |= bit
            self.w("OK")
        elif v == "CAL.DEFAULT":
            self.cal = dict(self.cal_nominal)
            self.cal_measured = 0
            self.w("OK")
        elif v in ("SWP.ABORT", "FAULT.CLR", "DIAG.CLR"):
            self.w("OK")
        elif v == "CAL.SAVE":
            self.w("OK seq=1")
        else:
            self.w("ERR UNKNOWN unknown verb")


@pytest.fixture
def device():
    t = LoopbackTransport()
    dev = FakeDevice(t)
    yield dev
    dev.stop()
    t.close()


@pytest.fixture
def inst(device):
    i = Instrument(device.t, keepalive_interval=None, command_timeout=2.0, deferred_timeout=3.0)
    i.open()
    yield i
    i.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """With STAGE1IV_NO_SKIP=1 (CI) a skipped test is a failure: CI must never
    report a check that did not run as passing."""
    outcome = yield
    rep = outcome.get_result()
    if os.environ.get("STAGE1IV_NO_SKIP") == "1" and rep.skipped:
        rep.outcome = "failed"
        rep.longrepr = f"skipped in CI (not allowed): {rep.longrepr}"
