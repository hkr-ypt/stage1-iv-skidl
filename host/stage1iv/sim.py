"""Driver for the whole-firmware simulator ``stage1_iv_sim``
(firmware/sim/sim_main.c, docs/firmware/simulator.md).

The simulator prints exactly two lines on stdout before anything else::

    PTY <slave path>
    CTL <control socket path>

The pseudo-terminal speaks the host protocol exactly like the USB port
(open it with :class:`stage1iv.transport.SerialTransport`); the UNIX control
socket takes one command per line and answers ``OK [key=value ...]`` or
``ERR <text>`` (fault injection, DUT selection, observation).

The executable is found, in order, from the ``executable`` argument, the
``STAGE1IV_SIM`` environment variable, or ``firmware/build/host/stage1_iv_sim``
relative to the repository that contains this package.
"""

from __future__ import annotations

import logging
import os
import queue
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Sequence

from .protocol import parse_payload

log = logging.getLogger(__name__)

SIM_ENV = "STAGE1IV_SIM"
SIM_NAME = "stage1_iv_sim"


class SimulatorError(RuntimeError):
    """The simulator could not be started or answered ``ERR``."""


def repo_root() -> Path:
    """The repository root, found from this file's location (host/stage1iv/sim.py)."""
    return Path(__file__).resolve().parents[2]


def find_simulator(executable: str | os.PathLike | None = None) -> Path | None:
    """The simulator executable, or None if there is none."""
    candidates: list[Path] = []
    if executable:
        candidates.append(Path(executable))
    env = os.environ.get(SIM_ENV)
    if env:
        candidates.append(Path(env))
    root = repo_root()
    candidates.append(root / "firmware" / "build" / "host" / SIM_NAME)
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    return None


def _b(v: Any) -> str:
    return "1" if v else "0"


class SimControl:
    """Client of the simulator's control socket."""

    def __init__(self, path: str, timeout: float = 5.0):
        self.path = path
        self.timeout = timeout
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(path)
        self._buf = b""
        self._lock = threading.Lock()

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:  # pragma: no cover
            pass

    def _readline(self) -> str:
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise SimulatorError("control socket closed")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("ascii", errors="replace").rstrip("\r")

    def send(self, *args: Any) -> str:
        """Send one command; returns the text after ``OK`` (``SimulatorError`` on ``ERR``)."""
        line = " ".join(str(a) for a in args)
        if "\n" in line:
            raise ValueError("control commands are single lines")
        with self._lock:
            try:
                self._sock.sendall(line.encode("ascii") + b"\n")
                reply = self._readline()
            except (OSError, socket.timeout) as exc:
                raise SimulatorError(f"control command {line!r} failed: {exc}") from exc
        if reply == "OK" or reply.startswith("OK "):
            return reply[3:]
        raise SimulatorError(f"control command {line!r}: {reply}")

    # ---- host link
    def vbus(self, mv: int) -> None:
        """VBUS in mV (attach > 3800, detach < 3000)."""
        self.send("vbus", int(mv))

    def dtr(self, on: bool) -> None:
        self.send("dtr", _b(on))

    def configured(self, on: bool) -> None:
        self.send("configured", _b(on))

    def tx_limit(self, nbytes: int | None) -> None:
        """Limit the host-side transmit space (emulates a stalled host); None = no limit."""
        self.send("tx_limit", -1 if nbytes is None else int(nbytes))

    # ---- DUT and board
    def dut_open(self) -> None:
        self.send("dut", "open")

    def dut_short(self) -> None:
        self.send("dut", "short")

    def dut_resistor(self, ohms: float) -> None:
        self.send("dut", "resistor", repr(float(ohms)))

    def dut_diode(self, i_s: float, n: float, r_s: float) -> None:
        self.send("dut", "diode", repr(float(i_s)), repr(float(n)), repr(float(r_s)))

    def dut_cell(self, volts: float, ohms: float) -> None:
        self.send("dut", "cell", repr(float(volts)), repr(float(ohms)))

    def dut_capacitor(self, farads: float) -> None:
        self.send("dut", "capacitor", repr(float(farads)))

    def rails(self, pos: bool, neg: bool) -> None:
        """+15 V / -15 V rails present."""
        self.send("rails", _b(pos), _b(neg))

    def sense_plus_open(self, on: bool) -> None:
        self.send("sense_plus_open", _b(on))

    def sense_minus_open(self, on: bool) -> None:
        self.send("sense_minus_open", _b(on))

    def force_open(self, on: bool) -> None:
        self.send("force_open", _b(on))

    def ilim(self, amps: float) -> None:
        """OPA548 current-limit plateau in A."""
        self.send("ilim", repr(float(amps)))

    def noise(self, codes: float) -> None:
        """ADC noise (rms codes)."""
        self.send("noise", repr(float(codes)))

    # ---- fault injection
    def fail_spi1(self, n: int) -> None:
        self.send("fail_spi1", int(n))

    def fail_spi2(self, n: int) -> None:
        self.send("fail_spi2", int(n))

    def adc_stuck(self, mode: str) -> None:
        """``none``, ``low`` or ``high`` (DOUT stuck)."""
        self.send("adc_stuck", mode)

    def adc_drdy_dead(self, on: bool) -> None:
        self.send("adc_drdy_dead", _b(on))

    def adc_reset(self) -> None:
        """Spontaneous ADS1256 reset."""
        self.send("adc_reset")

    def stall(self, us: int) -> None:
        """Stall the next main-loop pass by ``us`` microseconds of virtual time."""
        self.send("stall", int(us))

    def bor(self, level: int) -> None:
        self.send("bor", int(level))

    def tim2_frozen(self, on: bool) -> None:
        self.send("tim2_frozen", _b(on))

    def mcu_reset(self) -> None:
        self.send("mcu_reset")

    def power_cycle(self) -> None:
        self.send("power_cycle")

    def crash(self, kind: str = "hardfault") -> None:
        """``hardfault``, ``hang`` or ``hang_irqoff``."""
        self.send("crash", kind)

    # ---- observation
    def obs(self) -> dict[str, Any]:
        """``obs?`` as converted key=value pairs (ints, Decimals, strings)."""
        return parse_payload(self.send("obs?")).values()

    def time_us(self) -> int:
        """Virtual time in us (``time?``)."""
        return int(parse_payload(self.send("time?")).get("t_us"))

    def quit(self) -> None:
        self.send("quit")


class SimulatorProcess:
    """Run the simulator as a child process::

        with SimulatorProcess() as sim:
            with sim.instrument() as inst:
                print(inst.idn())
            sim.ctl.dut_resistor(1000)
    """

    def __init__(self, executable: str | os.PathLike | None = None, *,
                 args: Sequence[str] = (), speed: float | None = None,
                 seed: int | None = None, start_timeout: float = 10.0):
        exe = find_simulator(executable)
        if exe is None:
            raise SimulatorError(
                f"simulator not found (argument, ${SIM_ENV}, or firmware/build/host/{SIM_NAME})")
        self.executable = exe
        self.args = list(args)
        if speed is not None:
            self.args += ["--speed", repr(float(speed))]
        if seed is not None:
            self.args += ["--seed", str(int(seed))]
        self.start_timeout = start_timeout
        self.proc: subprocess.Popen[str] | None = None
        self.pty: str | None = None
        self.ctl_path: str | None = None
        self.ctl: SimControl | None = None
        self.output: deque[str] = deque(maxlen=1000)
        self._lines: "queue.Queue[str | None]" = queue.Queue()
        self._tmpdir: str | None = None
        self._threads: list[threading.Thread] = []

    def _drain(self, stream: Any, first_lines: bool) -> None:
        for line in iter(stream.readline, ""):
            line = line.rstrip("\n")
            self.output.append(line)
            if first_lines:
                self._lines.put(line)
        if first_lines:
            self._lines.put(None)

    def start(self) -> "SimulatorProcess":
        self._tmpdir = tempfile.mkdtemp(prefix="s1iv-")
        ctl = os.path.join(self._tmpdir, "ctl")
        cmd = [str(self.executable), "--ctl", ctl, *self.args]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     stdin=subprocess.DEVNULL, text=True, bufsize=1)
        for stream, first in ((self.proc.stdout, True), (self.proc.stderr, False)):
            t = threading.Thread(target=self._drain, args=(stream, first), daemon=True)
            t.start()
            self._threads.append(t)
        deadline = time.monotonic() + self.start_timeout
        got: dict[str, str] = {}
        while len(got) < 2:
            remaining = deadline - time.monotonic()
            try:
                line = self._lines.get(timeout=max(remaining, 0.01))
            except queue.Empty:
                line = None
                remaining = 0
            if line is None or remaining <= 0:
                self.stop()
                raise SimulatorError(f"simulator did not announce PTY/CTL; output: "
                                     f"{list(self.output)[-10:]}")
            head, _, rest = line.partition(" ")
            if head in ("PTY", "CTL") and rest:
                got[head] = rest.strip()
            else:
                self.stop()
                raise SimulatorError(f"unexpected first line from the simulator: {line!r}")
        self.pty, self.ctl_path = got["PTY"], got["CTL"]
        self.ctl = SimControl(self.ctl_path)
        return self

    def transport(self, **kwargs: Any):
        """A :class:`SerialTransport` on the simulator's pseudo-terminal."""
        from .transport import SerialTransport
        if self.pty is None:
            raise SimulatorError("simulator not started")
        return SerialTransport(self.pty, **kwargs)

    def instrument(self, **kwargs: Any):
        """An opened :class:`stage1iv.client.Instrument` on the pseudo-terminal."""
        from .client import Instrument
        kwargs.setdefault("hello_timeout", 2.0)
        inst = Instrument(self.transport(), **kwargs)
        return inst.open()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, timeout: float = 3.0) -> int | None:
        """``quit`` over the control socket, then SIGTERM, then SIGKILL."""
        rc = None
        if self.proc is not None:
            if self.proc.poll() is None and self.ctl is not None:
                try:
                    self.ctl.quit()
                except Exception:
                    pass
            try:
                rc = self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    rc = self.proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    rc = self.proc.wait(timeout=timeout)
        if self.ctl is not None:
            self.ctl.close()
            self.ctl = None
        if self._tmpdir is not None:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None
        return rc

    def __enter__(self) -> "SimulatorProcess":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()


__all__ = ["SIM_ENV", "SIM_NAME", "SimulatorError", "repo_root", "find_simulator",
           "SimControl", "SimulatorProcess"]
