"""The instrument client (docs/firmware/protocol.md).

:class:`Instrument` owns a :class:`stage1iv.transport.Transport` and runs two
background threads while open:

* a **dispatcher** that reads every line and routes it: ``OK``/``ERR`` to
  the oldest outstanding command (the device answers exactly one ``OK`` or
  ``ERR`` per command, in command order), ``EVT`` lines to event listeners,
  callbacks and a bounded history, ``PT`` lines to record listeners (a
  running sweep) or a bounded queue of unclaimed records, ``#`` log lines to
  a bounded log;
* a **keep-alive** that sends ``PING`` every 5 s (protocol.md section 1: the
  firmware disarms after ``host_timeout_s`` of silence while armed).

Every command goes through :meth:`Instrument.command`, which holds a lock
from sending the line until its response has been matched, so the
keep-alive can never interleave with another command's response matching.
A command that times out stays in the FIFO of outstanding commands
("abandoned"), so its late response is consumed by it instead of being
attributed to the next command. Responses with a known tag (``OK IDN``,
``OK PONG``, ...) are checked against the command; a mismatch means the
pairing is lost and raises :class:`ProtocolError` until :meth:`resync`.

Session loss - a serial error, the port vanishing, a new ``EVT HELLO``
after the session was already in use, or ``EVT STATE ... reason=
session_lost`` - surfaces as :class:`SessionLost` in every waiting call.
"""

from __future__ import annotations

import collections
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Iterable, Iterator

from . import protocol as P
from .calibration import CAL_KEYS, CalInfo, format_cal_args
from .errors import (BringupBuildRefused, ProtocolError, RecordsLost,
                     SessionLost, StateChangeFailed, SweepFailed,
                     device_error)
from .errors import TimeoutError as S1Timeout
from .records import Flag, PTRecord, SeqTracker, missing_indices, parse_pt, unwrap_record_times
from .sweeps import WaypointSweep, list_add_commands, waypoint_sweep
from .transport import Transport

log = logging.getLogger(__name__)

KEEPALIVE_INTERVAL_S = 5.0
COMMAND_TIMEOUT_S = 3.0
DEFERRED_TIMEOUT_S = 15.0       # deferred commands are bounded to 10 s by the firmware

#: Response tags that identify the command they answer.
EXPECTED_TAG = {
    "*IDN?": "IDN", "PING": "PONG", "STAT?": "STAT", "DIAG?": "DIAG",
    "CFG?": "CFG", "CAL?": "CAL", "MEAS?": "MEAS", "ZERO": "ZERO",
    "SWP.WP": "SWP", "SWP.LIST.USE": "SWP", "SWP?": "SWP", "SELFTEST": "SELFTEST",
}

#: STATE event reasons that mean the host session (or its liveness) was lost.
SESSION_LOSS_REASONS = frozenset({"session_lost", "host_timeout"})


# ---- result types -------------------------------------------------------------------

@dataclass(frozen=True)
class IdnInfo:
    """``*IDN?`` (protocol.md section 5.1)."""

    model: str | None
    fw: str | None
    rev: str | None
    proto: int | None
    hw: str | None
    build: str | None
    serial: str | None
    usb_vid: int | None
    usb_pid: int | None
    usb_pid_status: str | None
    planned_pid: str | None
    fields: dict[str, str]

    @classmethod
    def from_response(cls, r: P.Response) -> "IdnInfo":
        return cls(
            model=r.get("model"), fw=r.get("fw"), rev=r.get("rev"), proto=r.get("proto"),
            hw=r.get("hw"), build=r.get("build"), serial=r.get("serial"),
            usb_vid=r.get("usb_vid"), usb_pid=r.get("usb_pid"),
            usb_pid_status=r.get("usb_pid_status"), planned_pid=r.get("planned_pid"),
            fields=dict(r.fields))

    @property
    def is_bringup(self) -> bool:
        return (self.build or "").lower() == "bringup"


@dataclass(frozen=True)
class Measurement:
    """``OK MEAS`` (protocol.md section 5.3)."""

    n: int
    i_raw: int
    v_raw: int
    i_sd: Decimal | None
    v_sd: Decimal | None
    v_v: Decimal
    i_a: Decimal
    flags: int
    fields: dict[str, str]

    @classmethod
    def from_response(cls, r: P.Response) -> "Measurement":
        try:
            return cls(n=r["n"], i_raw=r["i_raw"], v_raw=r["v_raw"], i_sd=r["i_sd"],
                       v_sd=r["v_sd"], v_v=r["v_v"], i_a=r["i_a"], flags=r["flags"],
                       fields=dict(r.fields))
        except KeyError as exc:
            raise ProtocolError(f"MEAS response lacks {exc}: {r.line!r}") from exc


@dataclass(frozen=True)
class ZeroResult:
    """``OK ZERO`` (protocol.md section 5.3); offsets in ADC codes."""

    o_v: Decimal
    o_i: Decimal
    sd_v: Decimal
    sd_i: Decimal
    pairs: int
    sps: Decimal
    fields: dict[str, str]

    @classmethod
    def from_response(cls, r: P.Response) -> "ZeroResult":
        try:
            return cls(o_v=Decimal(r["o_v"]), o_i=Decimal(r["o_i"]), sd_v=Decimal(r["sd_v"]),
                       sd_i=Decimal(r["sd_i"]), pairs=r["pairs"], sps=Decimal(r["sps"]),
                       fields=dict(r.fields))
        except (KeyError, TypeError) as exc:
            raise ProtocolError(f"malformed ZERO response: {r.line!r}") from exc


@dataclass(frozen=True)
class SweepDefinition:
    """``OK SWP kind=... points=... est_s=...``."""

    kind: str
    points: int
    est_s: Decimal
    fields: dict[str, str]

    @classmethod
    def from_response(cls, r: P.Response) -> "SweepDefinition":
        try:
            return cls(kind=r["kind"], points=r["points"], est_s=Decimal(r["est_s"]),
                       fields=dict(r.fields))
        except (KeyError, TypeError) as exc:
            raise ProtocolError(f"malformed SWP response: {r.line!r}") from exc


@dataclass
class SweepResult:
    """Everything a sweep produced. ``records`` are ordered by ``idx`` and
    de-duplicated; ``begin``/``end`` are the ``SWEEP_BEGIN``/``SWEEP_END``
    events (``None`` if the firmware dropped them, see protocol.md section 7)."""

    sweep_id: int
    points_planned: int
    est_s: Decimal
    status: str
    records: list[PTRecord]
    begin: P.Event | None = None
    end: P.Event | None = None
    status_inferred: bool = False
    missing: list[int] = field(default_factory=list)
    seq_gaps: list[tuple[int, int]] = field(default_factory=list)
    fetches: int = 0
    events: list[P.Event] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.status == "complete" and not self.missing

    @property
    def begin_fields(self) -> dict[str, str]:
        return dict(self.begin.fields) if self.begin else {}

    @property
    def end_fields(self) -> dict[str, str]:
        return dict(self.end.fields) if self.end else {}

    def times_us(self) -> list[tuple[int, int, int]]:
        """Unwrapped (t_set, t_i, t_v) in us from the sweep start, per record."""
        return unwrap_record_times(self.records)


# ---- internal plumbing ------------------------------------------------------------------

class _Slot:
    """One outstanding command in the response FIFO."""

    __slots__ = ("command", "line", "exc", "done", "abandoned", "index")

    def __init__(self, command: str):
        self.command = command
        self.line: str | None = None
        self.exc: BaseException | None = None
        self.done = threading.Event()
        self.abandoned = False
        self.index = -1


class _Listener:
    """Receives events matching ``event_filter`` and (optionally) records,
    in arrival order, as ``(index, item)`` tuples; ``index`` is the number
    of responses dispatched before the item arrived."""

    def __init__(self, event_filter: Callable[[P.Event], bool] | None, records: bool):
        self.event_filter = event_filter
        self.records = records
        self.q: "queue.Queue[tuple[int, Any]]" = queue.Queue()

    def offer_event(self, index: int, ev: P.Event) -> None:
        if self.event_filter is not None and self.event_filter(ev):
            self.q.put((index, ev))

    def offer_record(self, index: int, rec: PTRecord) -> bool:
        if self.records:
            self.q.put((index, rec))
            return True
        return False

    def fail(self, exc: BaseException) -> None:
        self.q.put((-1, exc))

    def get(self, timeout: float | None) -> tuple[int, Any] | None:
        try:
            idx, item = self.q.get(timeout=timeout) if timeout is None or timeout > 0 \
                else self.q.get_nowait()
        except queue.Empty:
            return None
        if isinstance(item, BaseException):
            raise item
        return idx, item


class Instrument:
    """Client for one instrument. Use as a context manager::

        with Instrument.open_serial(serial="0034002B...") as inst:
            print(inst.idn())
    """

    def __init__(self, transport: Transport, *,
                 keepalive_interval: float | None = KEEPALIVE_INTERVAL_S,
                 command_timeout: float = COMMAND_TIMEOUT_S,
                 deferred_timeout: float = DEFERRED_TIMEOUT_S,
                 allow_bringup: bool = False,
                 hello_timeout: float = 0.0,
                 on_event: Callable[[P.Event], None] | None = None,
                 on_record: Callable[[PTRecord], None] | None = None,
                 history: int = 1000, record_backlog: int = 100_000):
        self.transport = transport
        self.keepalive_interval = keepalive_interval
        self.command_timeout = command_timeout
        self.deferred_timeout = deferred_timeout
        self.allow_bringup = allow_bringup
        self.hello_timeout = hello_timeout
        self._cmd_lock = threading.RLock()
        self._slots: collections.deque[_Slot] = collections.deque()
        self._slots_lock = threading.Lock()
        self._listeners: list[_Listener] = []
        self._listeners_lock = threading.Lock()
        self._event_callbacks: list[Callable[[P.Event], None]] = []
        self._record_callbacks: list[Callable[[PTRecord], None]] = []
        if on_event:
            self._event_callbacks.append(on_event)
        if on_record:
            self._record_callbacks.append(on_record)
        #: Recent events (all types), newest last.
        self.events: collections.deque[P.Event] = collections.deque(maxlen=history)
        #: Records no listener claimed (e.g. ``DATA.FETCH`` outside a sweep).
        self.records: collections.deque[PTRecord] = collections.deque(maxlen=record_backlog)
        #: ``#`` log lines (debug builds).
        self.log_lines: collections.deque[str] = collections.deque(maxlen=history)
        #: Protocol violations seen by the dispatcher (text).
        self.protocol_errors: collections.deque[str] = collections.deque(maxlen=history)
        self.last_hello: P.Event | None = None
        self.state: str | None = None
        self._responses = 0
        self._hello_seen = threading.Event()
        self._lost: SessionLost | None = None
        self._desync: str | None = None
        self._resyncing = False
        self._last_unsolicited = 0.0
        self._idn: IdnInfo | None = None
        self._ramp_rate: Decimal | None = None
        self._stop = threading.Event()
        self._dispatcher: threading.Thread | None = None
        self._keepalive: threading.Thread | None = None
        self._opened = False

    # ---- lifecycle ----------------------------------------------------------------

    @classmethod
    def open_serial(cls, port: str | None = None, *, serial: str | None = None,
                    pid: Any = None, **kwargs: Any) -> "Instrument":
        """Open by explicit port name, or discover by VID/PID (and serial)."""
        from .discovery import find_device
        from .transport import SerialTransport
        if port is None:
            port = find_device(serial=serial, pid=pid).device
        kwargs.setdefault("hello_timeout", 1.0)
        inst = cls(SerialTransport(port), **kwargs)
        try:
            inst.open()
        except BaseException:
            inst.close()
            raise
        return inst

    def open(self) -> "Instrument":
        """Start the dispatcher (and keep-alive); optionally wait for ``EVT HELLO``."""
        if self._opened:
            return self
        self._opened = True
        self._stop.clear()
        self._dispatcher = threading.Thread(target=self._dispatch_loop,
                                            name="stage1iv-dispatch", daemon=True)
        self._dispatcher.start()
        if self.hello_timeout > 0:
            if not self._hello_seen.wait(self.hello_timeout):
                log.debug("no EVT HELLO within %.1f s (session may predate the port open)",
                          self.hello_timeout)
        if self.keepalive_interval:
            self._keepalive = threading.Thread(target=self._keepalive_loop,
                                               name="stage1iv-keepalive", daemon=True)
            self._keepalive.start()
        return self

    def close(self) -> None:
        """Stop the threads and close the transport (dropping DTR ends the session)."""
        self._stop.set()
        try:
            self.transport.close()
        finally:
            for t in (self._keepalive, self._dispatcher):
                if t is not None and t is not threading.current_thread():
                    t.join(timeout=3.0)
            self._fail_everything(SessionLost("instrument closed"), sticky=True)

    def __enter__(self) -> "Instrument":
        return self.open()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def session_lost(self) -> SessionLost | None:
        """The sticky session-loss exception, if the session is gone."""
        return self._lost

    # ---- callbacks -------------------------------------------------------------------

    def add_event_callback(self, cb: Callable[[P.Event], None]) -> None:
        """``cb(event)`` runs on the dispatcher thread: keep it short."""
        self._event_callbacks.append(cb)

    def add_record_callback(self, cb: Callable[[PTRecord], None]) -> None:
        """``cb(record)`` runs on the dispatcher thread: keep it short."""
        self._record_callbacks.append(cb)

    # ---- dispatcher --------------------------------------------------------------------

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                line = self.transport.read_line(0.1)
            except SessionLost as exc:
                if not self._stop.is_set():
                    log.warning("session lost: %s", exc)
                self._fail_everything(exc, sticky=True)
                return
            except Exception as exc:  # pragma: no cover - defensive
                self._fail_everything(SessionLost(f"transport error: {exc}"), sticky=True)
                return
            if line is None:
                continue
            try:
                self._handle_line(line)
            except Exception:  # pragma: no cover - never let the dispatcher die
                log.exception("error while handling %r", line)

    def _protocol_error(self, text: str) -> None:
        log.warning("protocol error: %s", text)
        self.protocol_errors.append(text)

    def _handle_line(self, line: str) -> None:
        kind = P.classify(line)
        if kind in (P.Kind.OK, P.Kind.ERR):
            with self._slots_lock:
                slot = self._slots.popleft() if self._slots else None
                self._responses += 1
                if slot is not None:
                    slot.line = line
                    slot.index = self._responses
                    slot.done.set()
            if slot is None:
                self._last_unsolicited = time.monotonic()
                if not self._resyncing:
                    self._protocol_error(f"response without a command: {line!r}")
            elif slot.abandoned:
                log.warning("late response to %r dropped: %r", slot.command, line)
        elif kind is P.Kind.EVT:
            try:
                ev = P.parse_evt(line, time.monotonic())
            except ProtocolError as exc:
                self._protocol_error(str(exc))
                return
            self._handle_event(ev)
        elif kind is P.Kind.PT:
            try:
                rec = parse_pt(line)
            except ProtocolError as exc:
                self._protocol_error(str(exc))
                return
            self._handle_record(rec)
        elif kind is P.Kind.LOG:
            self.log_lines.append(line)
        elif line.strip():
            self._protocol_error(f"unrecognised line {line!r}")

    def _handle_event(self, ev: P.Event) -> None:
        self.events.append(ev)
        index = self._responses
        if ev.type == "STATE" and ev.arg:
            self.state = ev.arg
        elif ev.type == "HELLO":
            self.last_hello = ev
            self.state = ev.get("state", self.state)
            restarted = self._hello_seen.is_set() or self._responses > 0
            self._hello_seen.set()
            if restarted:
                self._fail_everything(SessionLost(
                    f"a new host session started (device reset or link drop): {ev.line}"),
                    sticky=True, keep_listeners_notified=True)
        with self._listeners_lock:
            listeners = list(self._listeners)
        for lst in listeners:
            lst.offer_event(index, ev)
        if ev.type == "STATE" and (ev.get("reason") or "").lower() in SESSION_LOSS_REASONS:
            exc = SessionLost(f"instrument reports {ev.line}")
            for lst in listeners:
                lst.fail(exc)
        for cb in list(self._event_callbacks):
            try:
                cb(ev)
            except Exception:
                log.exception("event callback failed")

    def _handle_record(self, rec: PTRecord) -> None:
        index = self._responses
        claimed = False
        with self._listeners_lock:
            listeners = list(self._listeners)
        for lst in listeners:
            claimed = lst.offer_record(index, rec) or claimed
        if not claimed:
            self.records.append(rec)
        for cb in list(self._record_callbacks):
            try:
                cb(rec)
            except Exception:
                log.exception("record callback failed")

    def _fail_everything(self, exc: SessionLost, sticky: bool,
                         keep_listeners_notified: bool = True) -> None:
        if sticky and self._lost is None:
            self._lost = exc
        with self._slots_lock:
            slots = list(self._slots)
            self._slots.clear()
        for s in slots:
            s.exc = exc
            s.done.set()
        if keep_listeners_notified:
            with self._listeners_lock:
                listeners = list(self._listeners)
            for lst in listeners:
                lst.fail(exc)

    class _Listen:
        def __init__(self, inst: "Instrument", lst: _Listener):
            self.inst, self.lst = inst, lst

        def __enter__(self) -> _Listener:
            with self.inst._listeners_lock:
                self.inst._listeners.append(self.lst)
            return self.lst

        def __exit__(self, *exc: Any) -> None:
            with self.inst._listeners_lock:
                if self.lst in self.inst._listeners:
                    self.inst._listeners.remove(self.lst)

    def listen(self, event_filter: Callable[[P.Event], bool] | None = None,
               records: bool = False) -> "Instrument._Listen":
        """Context manager yielding a listener queue for events matching
        ``event_filter`` (and all records if ``records``), from now on."""
        return Instrument._Listen(self, _Listener(event_filter, records))

    # ---- keep-alive -------------------------------------------------------------------

    def _keepalive_loop(self) -> None:
        assert self.keepalive_interval
        while not self._stop.wait(self.keepalive_interval):
            if self._lost is not None:
                return
            if self._desync is not None:
                continue
            try:
                self.ping()
            except SessionLost:
                return
            except Exception as exc:
                log.warning("keep-alive PING failed: %s", exc)

    # ---- commands -----------------------------------------------------------------------

    def _raise_if_unusable(self) -> None:
        if self._lost is not None:
            raise SessionLost(str(self._lost))
        if self._desync is not None:
            raise ProtocolError(f"command/response pairing lost ({self._desync}); call resync()")
        if not self._opened or self._stop.is_set():
            raise SessionLost("instrument is not open")

    def _command(self, line: str, timeout: float | None = None) -> P.Response:
        P.check_line(line)
        verb = P.verb_of(line)
        if timeout is None:
            timeout = self.deferred_timeout if verb in P.DEFERRED_VERBS else self.command_timeout
        with self._cmd_lock:
            self._raise_if_unusable()
            slot = _Slot(line)
            with self._slots_lock:
                self._slots.append(slot)
            try:
                self.transport.write_line(line)
            except BaseException:
                with self._slots_lock:
                    if slot in self._slots:
                        self._slots.remove(slot)
                raise
            finished = slot.done.wait(timeout)
            if not finished:
                with self._slots_lock:
                    finished = slot.done.is_set()
                    if not finished:
                        slot.abandoned = True
                if not finished:
                    raise S1Timeout(f"no response to {line!r} within {timeout:g} s")
        if slot.exc is not None:
            raise slot.exc
        assert slot.line is not None
        if P.classify(slot.line) is P.Kind.ERR:
            code, message = P.parse_err(slot.line)
            if code not in _KNOWN_CODES:
                self._protocol_error(f"undefined error code in {slot.line!r}")
            raise device_error(code, message, line)
        resp = P.parse_ok(slot.line, line, index=slot.index)
        want = EXPECTED_TAG.get(verb)
        if want is not None and resp.tag != want:
            self._desync = f"{line!r} was answered by {slot.line!r}"
            raise ProtocolError(f"response mismatch: {self._desync}")
        return resp

    def command(self, line: str, timeout: float | None = None) -> P.Response:
        """Send one command line and return its ``OK`` response.

        Raises the :class:`DeviceError` subclass for ``ERR``, the package
        :class:`TimeoutError` when no response arrives within ``timeout``
        (default 3 s; 15 s for the deferred commands MEAS?, ZERO,
        ADC.SELFCAL, SELFTEST), or :class:`SessionLost`.
        """
        return self._command(line, timeout)

    def resync(self, quiet: float = 0.3, timeout: float = 20.0) -> None:
        """Re-establish command/response pairing after a timeout mismatch or
        a session restart on a still-open port: forget outstanding commands,
        wait until no stray responses arrive for ``quiet`` seconds, then PING."""
        if not self.transport.is_open:
            raise SessionLost("transport is closed")
        with self._cmd_lock:
            self._resyncing = True
            try:
                with self._slots_lock:
                    stale = list(self._slots)
                    self._slots.clear()
                for s in stale:
                    s.exc = SessionLost("discarded by resync()")
                    s.done.set()
                start = time.monotonic()
                self._last_unsolicited = start
                while time.monotonic() - self._last_unsolicited < quiet:
                    if time.monotonic() - start > timeout:
                        raise S1Timeout("device keeps sending responses; cannot resync")
                    time.sleep(0.02)
                self._lost = None
                self._desync = None
                self._idn = None
            finally:
                self._resyncing = False
            self.ping()

    # ---- state waits ----------------------------------------------------------------------

    def _wait_state(self, lst: _Listener, after_index: int, target: str,
                    fail: Iterable[str], timeout: float, what: str,
                    poll: float = 0.5) -> P.Event | None:
        """Wait for ``EVT STATE <target>`` dispatched after ``after_index``;
        ``fail`` states raise. Falls back to polling ``STAT?`` (events may be
        dropped when the device's transmit buffer is full)."""
        fail = {f.upper() for f in fail}
        deadline = time.monotonic() + timeout
        next_poll = time.monotonic() + poll
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise S1Timeout(f"{what}: state {target} not reached within {timeout:g} s "
                                f"(last known state {self.state})")
            got = lst.get(min(deadline, next_poll) - now)
            if got is None:
                if time.monotonic() >= next_poll:
                    next_poll = time.monotonic() + poll
                    st = self.stat()
                    s = str(st.get("state", "")).upper()
                    if s == target:
                        return None
                    if s == "FAULT" and "FAULT" in fail:
                        raise StateChangeFailed(f"{what}: instrument in FAULT "
                                                f"(fault={st.get('fault')})")
                continue
            index, ev = got
            if index < after_index or ev.type != "STATE":
                continue
            s = (ev.arg or "").upper()
            if s == target:
                return ev
            if s in fail:
                raise StateChangeFailed(f"{what}: {ev.line}", ev)

    @staticmethod
    def _is_state(ev: P.Event) -> bool:
        return ev.type == "STATE"

    # ---- identity, status, diagnostics -------------------------------------------------------

    def idn(self, refresh: bool = False) -> IdnInfo:
        """``*IDN?`` (cached after the first call)."""
        if self._idn is None or refresh:
            self._idn = IdnInfo.from_response(self.command("*IDN?"))
            if self._idn.proto is not None and self._idn.proto != P.PROTOCOL_VERSION:
                log.warning("instrument speaks protocol %s; this library implements %s",
                            self._idn.proto, P.PROTOCOL_VERSION)
            if self._idn.usb_pid_status == "dev-test":
                log.warning("instrument uses the pid.codes private-testing USB PID "
                            "(not unique; must not ship)")
        return self._idn

    def ping(self) -> int:
        """``PING`` -> device uptime in us."""
        return self.command("PING").get("t_us")

    def stat(self) -> dict[str, Any]:
        """``STAT?`` as converted, ordered fields."""
        r = self.command("STAT?")
        values = r.values()
        if isinstance(values.get("state"), str):
            self.state = values["state"]
        return values

    def diag(self) -> dict[str, Any]:
        """``DIAG?`` as converted, ordered fields."""
        return self.command("DIAG?").values()

    def diag_clr(self) -> None:
        self.command("DIAG.CLR")

    # ---- configuration ------------------------------------------------------------------

    def cfg(self, **kwargs: Any) -> None:
        """``CFG key=value ...``; values are checked for exactness first.

        Example: ``inst.cfg(settle_us=2000, n_avg=2, v_limit=Decimal("5"))``.
        """
        args = P.format_cfg_args(**kwargs)
        self._ramp_rate = None
        self.command(P.build_command("CFG", *args))

    def cfg_get(self) -> dict[str, Any]:
        """``CFG?`` as converted, ordered fields."""
        values = self.command("CFG?").values()
        rr = values.get("ramp_rate")
        if isinstance(rr, (int, Decimal)):
            self._ramp_rate = Decimal(rr)
        return values

    # ---- output control --------------------------------------------------------------------

    def arm(self, wait: bool = True, timeout: float = 5.0) -> P.Event | None:
        """``ARM``; with ``wait`` block until ``EVT STATE ARMED``.

        Raises :class:`ErrPrecond` etc. if refused, :class:`StateChangeFailed`
        if the instrument goes to FAULT/DISARMING/DISARMED instead, the
        package :class:`TimeoutError` after ``timeout`` seconds.
        """
        with self.listen(self._is_state) as lst:
            resp = self.command("ARM")
            if not wait:
                return None
            return self._wait_state(lst, resp.index, "ARMED",
                                    ("FAULT", "DISARMING", "DISARMED"), timeout, "ARM")

    def disarm(self, wait: bool = True, timeout: float = 5.0) -> P.Event | None:
        """``DISARM``; with ``wait`` block until ``EVT STATE DISARMED``."""
        was = (self.state or "").upper()
        with self.listen(self._is_state) as lst:
            resp = self.command("DISARM")
            if not wait or was == "DISARMED":
                return None
            return self._wait_state(lst, resp.index, "DISARMED",
                                    ("FAULT",), timeout, "DISARM")

    def _ramp_rate_v_per_s(self) -> Decimal:
        if self._ramp_rate is None:
            try:
                self.cfg_get()
            except Exception:  # pragma: no cover - fall back to the slowest rate
                pass
        return self._ramp_rate if self._ramp_rate else Decimal("0.01")

    def src(self, volts: Any, wait: bool = True, timeout: float | None = None) -> P.Event | None:
        """``SRC V=<volts>``; with ``wait`` block until ``EVT SRC_DONE``.

        The default timeout is the worst-case 20 V ramp at the configured
        ``ramp_rate`` plus 3 s. A compliance trip ends the ramp at 0 V
        (``SRC_DONE status=compliance``): :class:`StateChangeFailed`.
        """
        v = P.format_volts(volts)

        def flt(ev: P.Event) -> bool:
            return ev.type in ("SRC_DONE", "STATE")

        with self.listen(flt) as lst:
            if wait and timeout is None:
                timeout = float(Decimal(20) / self._ramp_rate_v_per_s()) + 3.0
            resp = self.command(f"SRC V={v}")
            if not wait:
                return None
            after = resp.index
            assert timeout is not None
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise S1Timeout(f"SRC V={v}: no SRC_DONE within {timeout:g} s")
                got = lst.get(remaining)
                if got is None:
                    continue
                index, ev = got
                if index < after:
                    continue
                if ev.type == "SRC_DONE":
                    if str(ev.get("status", "reached")).lower() != "reached":
                        raise StateChangeFailed(f"SRC V={v}: {ev.line}", ev)
                    return ev
                if ev.type == "STATE" and (ev.arg or "").upper() != "ARMED":
                    raise StateChangeFailed(f"SRC V={v}: {ev.line}", ev)

    def fault_clr(self) -> None:
        """``FAULT.CLR`` (``ERR PRECOND <reason>`` if the cause persists)."""
        self.command("FAULT.CLR")

    def reset(self) -> None:
        """``RESET``: software reset. The session ends; reopen afterwards."""
        self.command("RESET")
        self._idn = None

    # ---- measurement ---------------------------------------------------------------------

    def meas(self, n: int | None = None, timeout: float | None = None) -> Measurement:
        """``MEAS? [N=<pairs>]`` (deferred) at the present output."""
        line = "MEAS?" if n is None else f"MEAS? N={P.format_int(n, 'N', minimum=1)}"
        return Measurement.from_response(self.command(line, timeout))

    def zero(self, timeout: float | None = None) -> ZeroResult:
        """``ZERO`` (deferred): all four jacks open, DUT removed."""
        return ZeroResult.from_response(self.command("ZERO", timeout))

    def adc_selfcal(self, timeout: float | None = None) -> None:
        """``ADC.SELFCAL`` (deferred)."""
        self.command("ADC.SELFCAL", timeout)

    def selftest(self, timeout: float | None = None) -> dict[str, Any]:
        """``SELFTEST`` (deferred) -> ``negref``, ``zero``, ``afe``."""
        return self.command("SELFTEST", timeout).values()

    # ---- sweep definition -----------------------------------------------------------------

    def sweep_waypoints(self, waypoints: Iterable[Any] | WaypointSweep, step: Any = None,
                        cycles: int = 1) -> SweepDefinition:
        """``SWP.WP``; the exact-grid rule is checked before sending
        (:class:`stage1iv.sweeps.GridError`)."""
        sw = waypoints if isinstance(waypoints, WaypointSweep) \
            else waypoint_sweep(waypoints, step, cycles)
        d = SweepDefinition.from_response(self.command(sw.command()))
        if d.points != sw.n_points:
            self._protocol_error(f"firmware counts {d.points} points, host expects {sw.n_points}")
        return d

    def sweep_list(self, points: Iterable[Any]) -> SweepDefinition:
        """``SWP.LIST.CLR``, ``SWP.LIST.ADD`` (<= 8 points per line), ``SWP.LIST.USE``.

        ``points``: voltages, ``(volts, settle_us)`` tuples or
        :class:`stage1iv.sweeps.ListPoint`.
        """
        lines = list_add_commands(points)
        with self._cmd_lock:
            self.command("SWP.LIST.CLR")
            expected = 0
            for line in lines:
                expected += len(line.split()) - 1
                r = self.command(line)
                total = r.get("n")
                if total is not None and total != expected:
                    raise ProtocolError(f"list holds {total} points, expected {expected}")
            d = SweepDefinition.from_response(self.command("SWP.LIST.USE"))
        if d.points != expected:
            self._protocol_error(f"firmware counts {d.points} list points, host sent {expected}")
        return d

    def sweep_query(self) -> SweepDefinition:
        """``SWP?``."""
        return SweepDefinition.from_response(self.command("SWP?"))

    def sweep_abort(self) -> None:
        """``SWP.ABORT``."""
        self.command("SWP.ABORT")

    # ---- sweep execution -------------------------------------------------------------------

    def _check_bringup(self, allow: bool | None) -> None:
        allow = self.allow_bringup if allow is None else allow
        info = self.idn()
        if info.is_bringup and not allow:
            raise BringupBuildRefused(
                "the instrument runs a BRING-UP firmware build (unsafe test commands "
                "compiled in); sweeps need allow_bringup=True / --allow-bringup")

    def run_sweep(self, on_record: Callable[[PTRecord], None] | None = None,
                  timeout: float | None = None, *, allow_incomplete: bool = False,
                  allow_bringup: bool | None = None, fetch_retries: int = 3,
                  end_grace: float = 2.0) -> SweepResult:
        """``SWP.RUN`` and collect the sweep.

        * Records are collected in point order; a jump in ``seq`` triggers
          ``DATA.FETCH FROM=<first missing>`` at once (the ring retains only
          1024 records), and the sweep is checked for completeness at the end
          (with up to ``fetch_retries`` further fetches).
        * ``on_record(rec)`` is called for every new record, in this thread.
        * If ``SWEEP_END`` was dropped by the firmware, ``STAT?`` is used to
          detect the end and the status is inferred
          (:attr:`SweepResult.status_inferred`).
        * Raises :class:`SweepFailed` for a status other than ``complete`` and
          :class:`RecordsLost` for unrecoverable gaps, unless
          ``allow_incomplete``; :class:`BringupBuildRefused` on a bring-up
          build without ``allow_bringup``. On timeout or interruption the sweep
          is aborted (``SWP.ABORT``) before the exception propagates.
        """
        self._check_bringup(allow_bringup)

        def flt(ev: P.Event) -> bool:
            return ev.type in ("SWEEP_BEGIN", "SWEEP_END", "STATE", "WARN")

        with self.listen(flt, records=True) as lst:
            resp = self.command("SWP.RUN")
            try:
                sweep_id = int(resp["id"])
                planned = int(resp["points"])
                est_s = Decimal(resp["est_s"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ProtocolError(f"malformed SWP.RUN response {resp.line!r}") from exc
            if timeout is None:
                timeout = float(est_s) * 1.5 + 30.0
            result = SweepResult(sweep_id=sweep_id, points_planned=planned, est_s=est_s,
                                 status="unknown", records=[])
            try:
                self._collect_sweep(lst, result, on_record, timeout, fetch_retries, end_grace)
            except (S1Timeout, KeyboardInterrupt):
                if result.end is None and self._lost is None:
                    try:
                        self.command("SWP.ABORT")
                    except Exception as exc:  # best effort
                        log.warning("SWP.ABORT after interruption failed: %s", exc)
                raise
        if result.missing and not allow_incomplete:
            raise RecordsLost(f"sweep {sweep_id}: {len(result.missing)} record(s) could not be "
                              f"recovered (first idx {result.missing[0]})", result.missing)
        if result.status != "complete" and not allow_incomplete:
            raise SweepFailed(result.status, result)
        return result

    def _collect_sweep(self, lst: _Listener, result: SweepResult,
                       on_record: Callable[[PTRecord], None] | None, timeout: float,
                       fetch_retries: int, end_grace: float) -> None:
        by_idx: dict[int, PTRecord] = {}
        tracker = SeqTracker()
        base_seq: int | None = None
        deadline = time.monotonic() + timeout
        last_activity = time.monotonic()
        evt_lost = False
        sid = result.sweep_id

        def accept(rec: PTRecord) -> None:
            nonlocal base_seq
            if base_seq is None:
                base_seq = rec.seq - rec.idx
            if rec.idx not in by_idx:
                by_idx[rec.idx] = rec
                if on_record is not None:
                    on_record(rec)

        def fetch(from_seq: int) -> tuple[int, int]:
            result.fetches += 1
            r = self.command(f"DATA.FETCH FROM={P.format_int(from_seq, 'FROM')}")
            return int(r.get("first", 0)), int(r.get("last", -1))

        while result.end is None:
            now = time.monotonic()
            if now >= deadline:
                result.records = [by_idx[k] for k in sorted(by_idx)]
                raise S1Timeout(f"sweep {sid}: not finished within {timeout:g} s "
                                f"({len(by_idx)}/{result.points_planned} records)")
            got = lst.get(min(0.25, deadline - now))
            if got is None:
                quiet = time.monotonic() - last_activity
                if (evt_lost or len(by_idx) >= result.points_planned) and quiet >= end_grace:
                    if self._infer_end(result, by_idx):
                        break
                    last_activity = time.monotonic()
                continue
            last_activity = time.monotonic()
            _, item = got
            if isinstance(item, PTRecord):
                if item.sweep_id != sid:
                    continue
                gap = tracker.feed(item.seq)
                accept(item)
                if gap is not None:
                    result.seq_gaps.append(gap)
                    log.warning("sweep %d: records seq %d..%d missing; fetching", sid, *gap)
                    fetch(gap[0])
                continue
            ev: P.Event = item
            result.events.append(ev)
            if ev.type == "SWEEP_BEGIN" and ev.get("id") == sid:
                result.begin = ev
            elif ev.type == "SWEEP_END" and ev.get("id") == sid:
                result.end = ev
                result.status = str(ev.get("status", "unknown"))
            elif ev.type == "WARN" and ev.arg == "EVT_LOST":
                evt_lost = True

        # Completeness: every idx 0..points-1 must be present.
        n_end = result.points_planned
        if result.end is not None and isinstance(result.end.get("points"), int):
            n_end = result.end.get("points")
        missing = missing_indices(by_idx, n_end)
        tries = 0
        while missing and tries < fetch_retries:
            tries += 1
            start = (base_seq + missing[0]) if base_seq is not None else 0
            first, last = fetch(start)
            if first > last:
                break
            wait_until = time.monotonic() + max(2.0, 0.002 * (last - first + 1))
            while missing and time.monotonic() < wait_until:
                got = lst.get(0.1)
                if got is None:
                    continue
                _, item = got
                if isinstance(item, PTRecord) and item.sweep_id == sid:
                    accept(item)
                    missing = missing_indices(by_idx, n_end)
                elif isinstance(item, P.Event):
                    result.events.append(item)
            missing = missing_indices(by_idx, n_end)
        result.missing = missing
        result.records = [by_idx[k] for k in sorted(by_idx)]

    def _infer_end(self, result: SweepResult, by_idx: dict[int, PTRecord]) -> bool:
        """SWEEP_END may have been dropped: ask STAT? whether the sweep is over."""
        st = self.stat()
        state = str(st.get("state", "")).upper()
        if state == "SWEEPING" or st.get("sweep_id") != result.sweep_id:
            return False
        result.status_inferred = True
        reported = st.get("sweep_status")
        if isinstance(reported, str) and reported not in ("none", "running"):
            result.status = reported
            log.warning("sweep %d: SWEEP_END not received; STAT? reports sweep_status=%s",
                        result.sweep_id, reported)
            return True
        compliance = any(r.has(Flag.COMPLIANCE) for r in by_idx.values())
        if (state == "ARMED" and len(by_idx) == result.points_planned and not compliance
                and st.get("sweep_pts") in (None, result.points_planned)):
            result.status = "complete"
        else:
            result.status = "unknown"
        log.warning("sweep %d: SWEEP_END not received; status inferred as %s from STAT?",
                    result.sweep_id, result.status)
        return True

    def data_fetch(self, from_seq: int, wait: bool = True, timeout: float = 5.0) -> list[PTRecord]:
        """``DATA.FETCH FROM=<seq>``; with ``wait`` collect the re-sent records
        ``first..last`` (sorted by ``seq``)."""
        with self.listen(None, records=True) as lst:
            r = self.command(f"DATA.FETCH FROM={P.format_int(from_seq, 'FROM')}")
            first, last = int(r.get("first", 0)), int(r.get("last", -1))
            if not wait or first > last:
                return []
            got_recs: dict[int, PTRecord] = {}
            deadline = time.monotonic() + timeout
            while len(got_recs) < last - first + 1:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise S1Timeout(f"DATA.FETCH: {len(got_recs)}/{last - first + 1} records "
                                    f"within {timeout:g} s")
                item = lst.get(remaining)
                if item is None:
                    continue
                rec = item[1]
                if isinstance(rec, PTRecord) and first <= rec.seq <= last:
                    got_recs[rec.seq] = rec
            return [got_recs[k] for k in sorted(got_recs)]

    # ---- calibration ------------------------------------------------------------------------

    def cal_get(self) -> CalInfo:
        """``CAL?``."""
        return CalInfo.from_fields(self.command("CAL?").fields)

    def cal_set(self, **kwargs: Any) -> str:
        """``CAL.SET key=value ...`` (keys as in ``CAL?``); returns the line sent."""
        unknown = set(kwargs) - set(CAL_KEYS)
        if unknown:
            raise ValueError(f"unknown CAL.SET keys {sorted(unknown)}; known: {list(CAL_KEYS)}")
        line = P.build_command("CAL.SET", *format_cal_args(**kwargs))
        self.command(line)
        return line

    def cal_save(self, ts: int | None = None, note: str | None = None) -> int:
        """``CAL.SAVE TS=<unix seconds> [NOTE=<word>]`` -> the new record ``seq``."""
        if ts is None:
            ts = int(time.time())
        args = [f"TS={P.format_int(ts, 'TS')}"]
        if note is not None:
            if len(note) > 31:
                raise ValueError("NOTE is limited to 31 characters (32-byte field)")
            args.append(f"NOTE={P.format_word(note, 'NOTE')}")
        r = self.command(P.build_command("CAL.SAVE", *args), timeout=max(self.command_timeout, 5.0))
        return int(r.get("seq", -1))

    def cal_default(self) -> None:
        """``CAL.DEFAULT``: RAM coefficients <- nominal (not saved)."""
        self.command("CAL.DEFAULT")

    def cal_reload(self) -> tuple[str, int]:
        """``CAL.RELOAD`` -> ``(source, seq)``."""
        r = self.command("CAL.RELOAD")
        return str(r.get("source")), int(r.get("seq", -1))


_KNOWN_CODES = frozenset({"SYNTAX", "UNKNOWN", "ARG", "RANGE", "STATE", "BUSY", "FAULT",
                          "PRECOND", "FLASH", "INTERNAL"})


def iter_records_by_seq(records: Iterable[PTRecord]) -> Iterator[PTRecord]:
    """De-duplicate by ``seq`` and yield in ``seq`` order."""
    seen: dict[int, PTRecord] = {}
    for r in records:
        seen.setdefault(r.seq, r)
    for k in sorted(seen):
        yield seen[k]


__all__ = ["Instrument", "IdnInfo", "Measurement", "ZeroResult", "SweepDefinition",
           "SweepResult", "EXPECTED_TAG", "KEEPALIVE_INTERVAL_S", "COMMAND_TIMEOUT_S",
           "DEFERRED_TIMEOUT_S", "iter_records_by_seq"]
