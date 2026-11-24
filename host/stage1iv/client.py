"""The instrument client (docs/firmware/protocol.md).

:class:`Instrument` owns a :class:`stage1iv.transport.Transport` and runs two
background threads while open:

* a **dispatcher** that reads every line and routes it: ``OK``/``ERR`` to
  the oldest outstanding command (the device answers exactly one ``OK`` or
  ``ERR`` per command, in command order), ``EVT`` lines to event listeners,
  callbacks and a bounded history, ``PT`` lines to record listeners (a
  running sweep) or a bounded queue of unclaimed records, ``#`` log lines to
  a bounded log;
* a **keep-alive** that sends ``PING`` *only while the application uses the
  library*: while one of its calls is in progress (command and deferred
  command waits, ``arm``/``disarm``/``src`` waits, :meth:`Instrument.run_sweep`,
  ``MEAS?``/``ZERO``, ...) and while a ``with inst.hold():`` block runs.
  While armed, the firmware disarms when no byte arrives for
  ``host_timeout_s`` (protocol.md section 1); the timeout exists for a host
  program that hangs with the port open, so the keep-alive must not defeat
  it: **the instrument disarms if the application stops calling the library
  for host_timeout_s.** Use :meth:`Instrument.hold` for a deliberate idle
  hold (e.g. while waiting for an operator) or :meth:`Instrument.keepalive`
  for one ``PING``. The period is ``min(keepalive_interval, host_timeout_s /
  3)`` (5 s with the default 30 s timeout), using the ``host_timeout_s`` the
  client last set or read; no ``PING`` is sent while the timeout is off
  (``host_timeout_s=0``). Application callbacks (``on_record`` of
  :meth:`Instrument.run_sweep`) run outside the keep-alive.

Locking and response matching. Every line is written under a short *send
lock* that covers only reserving the command's place in the FIFO of
outstanding commands and writing the line, so the FIFO order is the wire
order and each response is attributed to its command. Ordinary commands are
additionally serialised by a *command lock* held until their response has
been matched. The urgent verbs ``DISARM`` and ``SWP.ABORT``, which the
firmware acts on as soon as they reach its receive buffer (protocol.md
section 2), and keep-alive ``PING``\\ s bypass the command lock: they are
written at once, also while another thread waits up to 10 s for a deferred
command (``MEAS?``, ``ZERO``, ``SELFTEST``, ``CAL.SAVE``, ...). Their
responses keep their place in the command order; a keep-alive ``PONG`` is
consumed (and checked) by the dispatcher.

A command that times out stays in the FIFO of outstanding commands
("abandoned"), so its late response is consumed by it instead of being
attributed to the next command. Responses with a known tag (``OK IDN``,
``OK PONG``, ...) are checked against the command; a mismatch means the
pairing is lost and raises :class:`ProtocolError` until :meth:`resync`.
Only :meth:`Instrument.disarm` and :meth:`Instrument.sweep_abort` (and the
abort of an interrupted sweep) are still sent then, without checking their
responses.

Session loss - a serial error, the port vanishing, a new ``EVT HELLO``
after the first command was sent, or ``EVT STATE ... reason=
session_lost`` - surfaces as :class:`SessionLost` in every waiting call.
Before the first command, ``HELLO``\\ s, responses and records can be an
earlier session's unread output (protocol.md section 1): responses and
records are dropped and a later ``HELLO`` replaces an earlier one.
"""

from __future__ import annotations

import collections
import contextlib
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Iterable, Iterator

from . import protocol as P
from .calibration import CAL_KEYS, CalInfo, format_cal_args
from .errors import (BringupBuildRefused, DeviceError, ErrPrecond, ErrState, ProtocolError,
                     RecordsLost, SessionLost, StateChangeFailed, SweepFailed, device_error)
from .errors import TimeoutError as S1Timeout
from .records import (Flag, PTRecord, SeqTracker, decode_flags, missing_indices, parse_pt,
                      unwrap_record_times)
from .sweeps import WaypointSweep, list_add_commands, waypoint_sweep
from .transport import Transport

log = logging.getLogger(__name__)

#: Longest keep-alive period; shortened to ``host_timeout_s / 3``.
KEEPALIVE_INTERVAL_S = 5.0
COMMAND_TIMEOUT_S = 3.0
DEFERRED_TIMEOUT_S = 15.0       # deferred commands are bounded to 10 s by the firmware
#: At most this many keep-alive PINGs wait for their PONG at a time (5 bytes
#: each in the device's 1 KB receive buffer, which must keep room for an
#: urgent DISARM) at the keep-alive period; beyond it they continue at the
#: slowest rate that keeps the session, ``host_timeout_s / 3``.
KEEPALIVE_MAX_PENDING = 32
#: The firmware's default ``host_timeout_s``, assumed until it is known.
HOST_TIMEOUT_DEFAULT_S = 30
#: :meth:`Instrument.open` waits (after ``EVT HELLO``, up to ``hello_timeout``)
#: until no line has arrived for this long: an old session's unread output
#: and the new ``HELLO`` arrive back to back (protocol.md section 1).
HELLO_QUIET_S = 0.1
#: :meth:`Instrument.wait_ready` polls ``STAT?`` this often while the
#: instrument is in BOOT (POST).
READY_POLL_S = 0.1
#: :meth:`Instrument.arm` retries ``ERR PRECOND afe`` for this long plus two
#: conversion periods at the configured ADC rate.
ARM_AFE_RETRY_S = 2.0
ARM_RETRY_POLL_S = 0.05
_SLOWEST_SPS = Decimal("2.5")

#: Verbs the firmware acts on as soon as their line is in its receive buffer,
#: even while earlier commands wait (protocol.md section 2), when sent
#: without arguments.
URGENT_VERBS = frozenset({"DISARM", "SWP.ABORT"})

#: Response tags that identify the command they answer.
EXPECTED_TAG = {
    "*IDN?": "IDN", "PING": "PONG", "STAT?": "STAT", "DIAG?": "DIAG",
    "CFG?": "CFG", "CAL?": "CAL", "MEAS?": "MEAS", "ZERO": "ZERO",
    "SWP.WP": "SWP", "SWP.LIST.USE": "SWP", "SWP?": "SWP", "SELFTEST": "SELFTEST",
}

#: STATE event reasons that mean the host session (or its liveness) was lost.
SESSION_LOSS_REASONS = frozenset({"session_lost", "host_timeout"})


def is_urgent(line: str) -> bool:
    """True for ``DISARM`` / ``SWP.ABORT`` without arguments (blanks around the
    verb do not matter): the firmware acts on these on arrival."""
    parts = line.split()
    return len(parts) == 1 and parts[0].upper() in URGENT_VERBS


def _positive_number(v: Any) -> bool:
    return isinstance(v, (int, Decimal)) and not isinstance(v, bool) and v > 0


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
    """``OK MEAS`` (protocol.md section 5.3). ``v_v``/``i_a`` are computed by
    the firmware from the unrounded means (``i_raw``/``v_raw`` are the means
    rounded to whole codes); ``flags`` as in PT records (:class:`Flag`)."""

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

    @property
    def flag_names(self) -> list[str]:
        """Names of the set flag bits (e.g. ``MEAS``, ``SD_SAT``)."""
        return decode_flags(self.flags)


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
    events (``None`` if the firmware dropped them, see protocol.md section 7).

    When :meth:`Instrument.run_sweep` raised, :attr:`Instrument.last_sweep`
    holds the records collected until then (``status`` stays ``"unknown"``
    unless ``SWEEP_END`` arrived; :attr:`ended` is False)."""

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
    def ended(self) -> bool:
        """True once the sweep is known to be over (``SWEEP_END`` received or
        the end inferred from ``STAT?``)."""
        return self.end is not None or self.status_inferred

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

    __slots__ = ("command", "line", "exc", "done", "abandoned", "index", "timeout",
                 "deadline", "background", "ahead", "t_sent", "checked")

    def __init__(self, command: str, timeout: float = 0.0, deadline: float = 0.0,
                 background: bool = False):
        self.command = command
        self.line: str | None = None
        self.exc: BaseException | None = None
        self.done = threading.Event()
        self.abandoned = False
        self.index = -1
        self.timeout = timeout
        #: the response is expected by then (urgent commands: counted from
        #: the deadlines of the commands ahead of them)
        self.deadline = deadline
        #: nobody waits for the response: the dispatcher checks it (keep-alive
        #: PINGs, urgent commands whose OK is no longer awaited)
        self.background = background
        #: unanswered ordinary commands ahead of this one when it was sent
        self.ahead = 0
        #: monotonic time just before the line was written
        self.t_sent = 0.0
        #: the response has been examined (by _finish or the dispatcher)
        self.checked = False


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

    ``keepalive_interval``: longest period of the keep-alive ``PING``\\ s sent
    while a library call is in progress (default 5 s; never more than
    ``host_timeout_s / 3``). ``None`` (or 0) disables that automatic
    keep-alive entirely; :meth:`hold` and :meth:`keepalive`, which the
    application calls explicitly, still send ``PING``\\ s then.
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
        #: serialises ordinary commands (send + response); urgent verbs and
        #: keep-alive PINGs do not take it
        self._cmd_lock = threading.RLock()
        #: held only while reserving a response slot and writing the line
        self._send_lock = threading.Lock()
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
        #: The :class:`SweepResult` of the latest :meth:`run_sweep`, also when
        #: it raised (records collected so far), e.g. to save partial data.
        self.last_sweep: SweepResult | None = None
        self._responses = 0
        self._sent_any = False        # a command line was written (guarded by _slots_lock)
        self._last_rx = 0.0           # when the dispatcher last got a line
        self._ka_sent = 0.0           # when the last keep-alive PING was written
        self._hello_seen = threading.Event()
        self._lost: SessionLost | None = None
        self._desync: str | None = None
        self._resyncing = False
        self._last_unsolicited = 0.0
        self._idn: IdnInfo | None = None
        #: configuration values this client last set or read (CFG, CFG?, STAT?)
        self._cfg_known: dict[str, Any] = {}
        # keep-alive bookkeeping (guarded by _ka_cond)
        self._ka_cond = threading.Condition()
        self._active = 0          # threads inside a library call
        self._holds = 0           # hold() blocks running
        self._ka_last = 0.0       # last keep-alive PING (or start of the demand)
        self._ka_idle = False     # the keep-alive thread waits for demand
        self._tls = threading.local()
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
        """Start the dispatcher and keep-alive threads; optionally wait for ``EVT HELLO``."""
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
            else:
                # Output an earlier session left unread arrives first, then this
                # session's HELLO, back to back; the tail may hold an older HELLO.
                # Let the input pause so the last HELLO is in before the first
                # command (protocol.md section 1).
                limit = time.monotonic() + self.hello_timeout
                while True:
                    now = time.monotonic()
                    quiet = self._last_rx + HELLO_QUIET_S
                    if now >= quiet or now >= limit:
                        break
                    time.sleep(min(quiet, limit) - now)
        # Idle unless a library call or hold() needs it (see the module docstring).
        self._keepalive = threading.Thread(target=self._keepalive_loop,
                                           name="stage1iv-keepalive", daemon=True)
        self._keepalive.start()
        return self

    def close(self) -> None:
        """Stop the threads and close the transport (dropping DTR ends the session)."""
        self._stop.set()
        with self._ka_cond:
            self._ka_cond.notify_all()
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
        self._last_rx = time.monotonic()
        kind = P.classify(line)
        if kind in (P.Kind.OK, P.Kind.ERR):
            background = False
            with self._slots_lock:
                if not self._slots and not self._sent_any:
                    # Nothing was ever sent: an earlier session's unread output
                    # (protocol.md section 1), not a response of this one.
                    log.debug("stale response before the first command dropped: %r", line)
                    return
                slot = self._slots.popleft() if self._slots else None
                self._responses += 1
                if slot is not None:
                    slot.line = line
                    slot.index = self._responses
                    background = slot.background
                    slot.done.set()
            if slot is None:
                self._last_unsolicited = time.monotonic()
                if not self._resyncing:
                    self._protocol_error(f"response without a command: {line!r}")
            elif background:
                self._background_response(slot, line)
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

    def _background_response(self, slot: _Slot, line: str) -> None:
        """Check the response to a command nobody waits for (keep-alive PING,
        an urgent command whose OK was no longer awaited)."""
        slot.checked = True
        verb = P.verb_of(slot.command)
        if P.classify(line) is P.Kind.ERR:
            try:
                code = P.parse_err(line)[0]
            except ProtocolError:
                code = ""
            if verb == "SWP.ABORT" and code == "STATE":
                log.debug("%r answered %r (the sweep had already ended)", slot.command, line)
            else:
                log.warning("%r answered %r", slot.command, line)
            return
        want = EXPECTED_TAG.get(verb)
        if want is None:
            return
        try:
            tag = P.parse_ok(line).tag
        except ProtocolError as exc:
            self._protocol_error(str(exc))
            return
        if tag != want and self._desync is None and not self._resyncing:
            self._desync = f"{slot.command!r} was answered by {line!r}"
            self._protocol_error(f"response mismatch: {self._desync}")

    def _handle_event(self, ev: P.Event) -> None:
        self.events.append(ev)
        index = self._responses
        if ev.type == "STATE" and ev.arg:
            self.state = ev.arg
        elif ev.type == "HELLO":
            self.last_hello = ev
            self.state = ev.get("state", self.state)
            # Before the first command, a later HELLO replaces an earlier one:
            # an old session's unread tail can hold its HELLO (protocol.md
            # section 1). After it, any HELLO means the session restarted.
            with self._slots_lock:
                restarted = self._responses > 0 or (self._sent_any and self._hello_seen.is_set())
            self._hello_seen.set()
            if restarted:
                self._cfg_known.clear()     # the device may have reset
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
        if not self._sent_any:
            # a record of an earlier session's sweep (protocol.md section 1)
            log.debug("stale record before the first command dropped")
            return
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

    @property
    def keepalive_period(self) -> float | None:
        """Seconds between keep-alive ``PING``\\ s: ``min(keepalive_interval,
        host_timeout_s / 3)`` (``keepalive_interval`` counts as 5 s when the
        automatic keep-alive is disabled), with the ``host_timeout_s`` this
        client last set or read (``CFG``, ``CFG?``, ``STAT?``; 30 s assumed
        until then). ``None`` while the instrument's host timeout is off
        (``host_timeout_s=0``): no ``PING`` is needed."""
        base = float(self.keepalive_interval or KEEPALIVE_INTERVAL_S)
        t = self._cfg_known.get("host_timeout_s")
        if isinstance(t, int) and not isinstance(t, bool):
            if t <= 0:
                return None
            return min(base, t / 3.0)
        return base

    def _demand(self) -> int:
        return self._holds + (self._active if self.keepalive_interval else 0)

    def _adjust_demand(self, active: int = 0, holds: int = 0, call_start: bool = False) -> None:
        with self._ka_cond:
            before = self._demand()
            self._active += active
            self._holds += holds
            if before <= 0 < self._demand():
                if call_start:
                    # the call writes its command now: the next PING is a period later
                    self._ka_last = time.monotonic()
                if self._ka_idle:
                    # Only an idle keep-alive needs waking: a waiting one finds
                    # its PING due no earlier than it planned (on_record
                    # callbacks toggle the demand for every record).
                    self._ka_cond.notify_all()

    @contextlib.contextmanager
    def _activity(self) -> Iterator[None]:
        """Mark a library call in progress in this thread (keep-alive on)."""
        tls = self._tls
        depth = getattr(tls, "depth", 0)
        tls.depth = depth + 1
        if depth == 0:
            self._adjust_demand(active=1, call_start=True)
        try:
            yield
        finally:
            tls.depth = depth
            if depth == 0:
                self._adjust_demand(active=-1)

    @contextlib.contextmanager
    def _user_code(self) -> Iterator[None]:
        """Run application code (a callback) outside the keep-alive: if it
        blocks for host_timeout_s the instrument disarms, as for any hung
        application (the callback can use :meth:`hold` for deliberate work)."""
        tls = self._tls
        depth = getattr(tls, "depth", 0)
        if depth:
            tls.depth = 0
            self._adjust_demand(active=-1)
        try:
            yield
        finally:
            if depth:
                tls.depth = depth
                self._adjust_demand(active=1)

    def _keepalive_loop(self) -> None:
        cond = self._ka_cond
        while True:
            with cond:
                while True:
                    if self._stop.is_set():
                        return
                    period = self.keepalive_period
                    if (period is None or self._demand() <= 0 or self._lost is not None
                            or self._desync is not None or self._resyncing):
                        self._ka_idle = True
                        cond.wait(1.0)
                        self._ka_idle = False
                        continue
                    now = time.monotonic()
                    due = self._ka_last + period
                    if now >= due:
                        self._ka_last = now
                        break
                    cond.wait(due - now)
            try:
                self._send_keepalive_ping()
            except (SessionLost, ProtocolError) as exc:
                log.debug("keep-alive PING not sent: %s", exc)
            except Exception as exc:
                log.warning("keep-alive PING failed: %s", exc)

    def _send_keepalive_ping(self) -> bool:
        now = time.monotonic()
        with self._slots_lock:
            pending = sum(1 for s in self._slots if s.background and s.command == "PING")
        if pending >= KEEPALIVE_MAX_PENDING:
            # PINGs go unanswered while a deferred command runs (up to 10 s),
            # yet each still restarts the device's host timeout. Past the cap,
            # send only as often as that needs (host_timeout_s / 3), so a short
            # keepalive_interval cannot let a healthy long command time out.
            t = self._cfg_known.get("host_timeout_s")
            if not isinstance(t, int) or isinstance(t, bool) or t <= 0:
                t = HOST_TIMEOUT_DEFAULT_S
            if now - self._ka_sent < t / 3.0:
                log.debug("keep-alive: %d PINGs unanswered; not sending another yet", pending)
                return False
        self._send("PING", self.command_timeout, background=True)
        self._ka_sent = now
        return True

    def keepalive(self) -> bool:
        """Send one ``PING`` at once and return without waiting for it (the
        dispatcher checks the ``PONG``); it is written even while another
        thread waits for a deferred command. Any byte restarts the
        instrument's host timeout. Returns False if it was not sent because
        earlier keep-alive PINGs are still unanswered. Raises
        :class:`SessionLost` / :class:`ProtocolError` like a command."""
        sent = self._send_keepalive_ping()
        if sent:
            with self._ka_cond:
                self._ka_last = time.monotonic()
        return sent

    @contextlib.contextmanager
    def hold(self) -> Iterator["Instrument"]:
        """Keep the host session alive while the ``with`` block runs::

            inst.src(5)
            with inst.hold():
                reading = input("DMM reading [V]: ")   # the output stays armed

        Outside library calls the client sends nothing, so the instrument
        disarms after ``host_timeout_s`` without traffic (a hung or idle
        application must not hold the output). Inside ``hold()`` the
        keep-alive sends a ``PING`` at once and then every
        :attr:`keepalive_period` for as long as the block runs, whatever the
        application does. Blocks may nest and may run in any thread; this
        works also with ``keepalive_interval=None``. Raises
        :class:`SessionLost` / :class:`ProtocolError` (pairing lost: no
        keep-alive until :meth:`resync`) like a command."""
        self._raise_if_unusable()
        self._learn_host_timeout()
        self.keepalive()            # at once; the keep-alive thread continues
        self._adjust_demand(holds=1)
        try:
            yield self
        finally:
            self._adjust_demand(holds=-1)

    def _learn_host_timeout(self) -> None:
        """Read ``host_timeout_s`` (``CFG?``) if it is not known yet, unless
        another thread's command is in progress."""
        if "host_timeout_s" in self._cfg_known:
            return
        if not self._cmd_lock.acquire(blocking=False):
            return
        try:
            self.cfg_get()
        except (DeviceError, S1Timeout) as exc:
            log.debug("CFG? failed: %s", exc)
        finally:
            self._cmd_lock.release()

    # ---- commands -----------------------------------------------------------------------

    def _raise_if_unusable(self, allow_desync: bool = False) -> None:
        if self._lost is not None:
            raise SessionLost(str(self._lost))
        if self._desync is not None and not allow_desync:
            raise ProtocolError(f"command/response pairing lost ({self._desync}); call resync()")
        if not self._opened or self._stop.is_set():
            raise SessionLost("instrument is not open")

    def _send(self, line: str, timeout: float, *, urgent: bool = False,
              background: bool = False, allow_desync: bool = False) -> _Slot:
        """Reserve the response slot and write the line, atomically under the
        send lock (FIFO order = wire order). An urgent or background
        command's response cannot arrive before those of the commands ahead
        of it, so its deadline is counted from theirs. Keep-alive PINGs ahead
        do not count: each is answered as soon as the command before it, so
        their (themselves chained) deadlines would only add up."""
        with self._send_lock:
            self._raise_if_unusable(allow_desync)
            now = time.monotonic()
            with self._slots_lock:
                start = now
                ahead = 0
                for s in self._slots:
                    if not s.background:
                        ahead += 1
                    if (urgent or background) and not s.background and s.deadline > start:
                        start = s.deadline
                slot = _Slot(line, timeout, start + timeout, background)
                slot.ahead = ahead
                slot.t_sent = now
                self._slots.append(slot)
                self._sent_any = True
            try:
                self.transport.write_line(line)
            except BaseException:
                with self._slots_lock:
                    if slot in self._slots:
                        self._slots.remove(slot)
                raise
        return slot

    def _await(self, slot: _Slot) -> None:
        """Wait for the slot's response until its deadline; abandon it (its
        late response is then dropped) on timeout."""
        if slot.done.wait(max(slot.deadline - time.monotonic(), 0.0)):
            return
        with self._slots_lock:
            finished = slot.done.is_set()
            if not finished:
                slot.abandoned = True
        if not finished:
            raise S1Timeout(f"no response to {slot.command!r} within "
                            f"{slot.deadline - slot.t_sent:g} s")

    def _finish(self, slot: _Slot) -> P.Response:
        """The matched response: ``OK`` -> :class:`Response`, ``ERR`` -> raise."""
        slot.checked = True
        if slot.exc is not None:
            raise slot.exc
        line = slot.command
        verb = P.verb_of(line)
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
        self._learn(verb, line, resp)
        return resp

    def _release_slot(self, slot: _Slot) -> None:
        """Stop waiting for an urgent command's response; the dispatcher checks
        it when it arrives (an ``ERR`` is logged)."""
        with self._slots_lock:
            if not slot.done.is_set():
                slot.background = True
                return
        if not slot.checked and slot.line is not None and P.classify(slot.line) is P.Kind.ERR:
            slot.checked = True
            log.warning("%r answered %r", slot.command, slot.line)

    def _command(self, line: str, timeout: float | None = None) -> P.Response:
        P.check_line(line)
        verb = P.verb_of(line)
        if timeout is None:
            timeout = self.deferred_timeout if verb in P.DEFERRED_VERBS else self.command_timeout
        with self._activity():
            if is_urgent(line):
                # written at once, even while another thread waits for a deferred command
                slot = self._send(line, timeout, urgent=True)
                self._await(slot)
            else:
                with self._cmd_lock:
                    slot = self._send(line, timeout)
                    try:
                        self._await(slot)
                    except BaseException:
                        if verb == "CFG":
                            self._forget_cfg(line)      # applied or not: unknown
                        raise
            return self._finish(slot)

    def command(self, line: str, timeout: float | None = None) -> P.Response:
        """Send one command line and return its ``OK`` response.

        Raises the :class:`DeviceError` subclass for ``ERR``, the package
        :class:`TimeoutError` when no response arrives within ``timeout``
        (default 3 s; 15 s for the deferred commands MEAS?, ZERO,
        ADC.SELFCAL, SELFTEST, CAL.SAVE), or :class:`SessionLost`.
        ``DISARM`` and ``SWP.ABORT`` are written at once even while another
        thread's command is in progress (their ``OK`` keeps its place in the
        command order, so the wait for it can be longer).
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
                with self._send_lock, self._slots_lock:
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
                self._cfg_known.clear()
            finally:
                self._resyncing = False
            self.ping()

    # ---- configuration tracking ---------------------------------------------------------

    def _learn(self, verb: str, line: str, resp: P.Response) -> None:
        """Track the configuration this client set or read: the keep-alive
        period (``host_timeout_s``), the src() timeout (``ramp_rate``) and the
        arm() retry window (``adc_rate``)."""
        old = self._cfg_known.get("host_timeout_s")
        if verb == "CFG?":
            self._cfg_known.update(resp.values())
        elif verb == "STAT?":
            t = resp.get("host_timeout_s")
            if isinstance(t, int):
                self._cfg_known["host_timeout_s"] = t
        elif verb == "CFG":
            for tok in line.split()[1:]:
                key, sep, value = tok.partition("=")
                if sep:
                    key = key.lower()
                    self._cfg_known[key] = P.convert_value(key, value)
        else:
            return
        if self._cfg_known.get("host_timeout_s") != old:
            with self._ka_cond:
                self._ka_cond.notify_all()

    def _forget_cfg(self, line: str) -> None:
        for tok in line.split()[1:]:
            key, sep, _ = tok.partition("=")
            if sep:
                self._cfg_known.pop(key.lower(), None)

    def _try_cfg_get(self) -> None:
        try:
            self.cfg_get()
        except (DeviceError, S1Timeout) as exc:
            log.warning("CFG? failed: %s", exc)

    # ---- state waits ----------------------------------------------------------------------

    def _poll_stat(self) -> dict[str, Any] | None:
        """``STAT?`` unless the pairing is lost or another thread's command is
        in progress (then events will tell; do not wait behind it)."""
        if self._desync is not None:
            return None
        if not self._cmd_lock.acquire(blocking=False):
            return None
        try:
            return self.stat()
        finally:
            self._cmd_lock.release()

    def _wait_state(self, lst: _Listener, target: str, fail: Iterable[str], timeout: float,
                    what: str, *, after_index: int | None = None,
                    after_time: float | None = None, poll: float | None = 0.5,
                    slot: _Slot | None = None) -> P.Event | None:
        """Wait for ``EVT STATE <target>``; ``fail`` states raise.

        Only events dispatched after the command count: ``after_index`` (the
        command's response index; events a command causes follow its ``OK``)
        or ``after_time`` (received after the line was written; for urgent
        commands, whose events can precede their ``OK``). Falls back to
        polling ``STAT?`` every ``poll`` seconds (events may be dropped when
        the device's transmit buffer is full). ``slot``: an urgent command
        whose ``ERR`` response ends the wait."""
        fail = {f.upper() for f in fail}
        deadline = time.monotonic() + timeout
        next_poll = time.monotonic() + poll if poll else None
        while True:
            if slot is not None and slot.done.is_set():
                self._finish(slot)          # raises the ERR
                slot = None
            now = time.monotonic()
            if now >= deadline:
                raise S1Timeout(f"{what}: state {target} not reached within {timeout:g} s "
                                f"(last known state {self.state})")
            wake = deadline if next_poll is None else min(deadline, next_poll)
            if slot is not None:
                wake = min(wake, now + 0.05)
            got = lst.get(wake - now)
            if got is None:
                if next_poll is not None and time.monotonic() >= next_poll:
                    next_poll = time.monotonic() + poll
                    st = self._poll_stat()
                    if st is None:
                        continue
                    s = str(st.get("state", "")).upper()
                    if s == target:
                        return None
                    if s == "FAULT" and "FAULT" in fail:
                        raise StateChangeFailed(f"{what}: instrument in FAULT "
                                                f"(fault={st.get('fault')})")
                continue
            index, ev = got
            if ev.type != "STATE":
                continue
            if after_index is not None and index < after_index:
                continue
            if after_time is not None and ev.received < after_time:
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
        """``PING`` -> device uptime in us (waits for the ``PONG``; see also
        :meth:`keepalive`)."""
        return self.command("PING").get("t_us")

    def stat(self) -> dict[str, Any]:
        """``STAT?`` as converted, ordered fields."""
        r = self.command("STAT?")
        values = r.values()
        if isinstance(values.get("state"), str):
            self.state = values["state"]
        return values

    def wait_ready(self, timeout: float = 10.0) -> str:
        """Wait while the instrument is in ``BOOT`` (its power-on self-test,
        during which ``CFG``, ``ZERO``, ``SWP.*`` etc. answer ``ERR STATE``),
        polling ``STAT?``. Returns the state it reached; raises the package
        :class:`TimeoutError` if it is still ``BOOT`` after ``timeout`` s."""
        with self._activity():
            give_up = time.monotonic() + timeout
            while True:
                state = self.stat().get("state")
                if state != "BOOT":
                    return str(state)
                if time.monotonic() >= give_up:
                    raise S1Timeout(f"instrument still in BOOT after {timeout:g} s")
                time.sleep(READY_POLL_S)

    def diag(self) -> dict[str, Any]:
        """``DIAG?`` as converted, ordered fields."""
        return self.command("DIAG?").values()

    def diag_clr(self) -> None:
        self.command("DIAG.CLR")

    # ---- configuration ------------------------------------------------------------------

    def cfg(self, **kwargs: Any) -> None:
        """``CFG key=value ...``; values are checked for exactness first.

        Example: ``inst.cfg(settle_us=2000, n_avg=2, v_limit=Decimal("5"))``.
        Setting ``host_timeout_s`` also sets the keep-alive period
        (:attr:`keepalive_period`).
        """
        args = P.format_cfg_args(**kwargs)
        self.command(P.build_command("CFG", *args))

    def cfg_get(self) -> dict[str, Any]:
        """``CFG?`` as converted, ordered fields."""
        return self.command("CFG?").values()

    # ---- output control --------------------------------------------------------------------

    def _afe_retry_window(self) -> float:
        rate = self._cfg_known.get("adc_rate")
        if not _positive_number(rate):
            self._try_cfg_get()
            rate = self._cfg_known.get("adc_rate")
        sps = Decimal(rate) if _positive_number(rate) else _SLOWEST_SPS
        return ARM_AFE_RETRY_S + 2.0 / float(sps)

    def arm(self, wait: bool = True, timeout: float = 5.0, *,
            afe_retry: float | None = None) -> P.Event | None:
        """``ARM``; with ``wait`` block until ``EVT STATE ARMED``.

        ``ERR PRECOND afe`` is retried for a bounded time: ARM needs a
        gate-open front-end check younger than 1 s, which the firmware runs
        only in DISARMED, on its next monitor I/V pair, so an ARM right after a
        DISARM is refused for up to about a second (longer at low ADC rates).
        ``afe_retry`` is that retry time in seconds (default 2 s plus two
        conversion periods at the configured ``adc_rate``; 0 = no retry); a
        front-end check that keeps failing raises :class:`ErrPrecond` after it.

        Raises :class:`ErrPrecond` etc. if refused, :class:`StateChangeFailed`
        if the instrument goes to FAULT/DISARMING/DISARMED instead, the
        package :class:`TimeoutError` after ``timeout`` seconds.
        """
        with self._activity():
            if "adc_rate" not in self._cfg_known or "host_timeout_s" not in self._cfg_known:
                self._try_cfg_get()     # keep-alive period and retry window
            give_up: float | None = None
            while True:
                with self.listen(self._is_state) as lst:
                    try:
                        resp = self.command("ARM")
                    except ErrPrecond as exc:
                        if exc.precondition.lower() != "afe":
                            raise
                        now = time.monotonic()
                        if give_up is None:
                            window = (self._afe_retry_window() if afe_retry is None
                                      else max(0.0, float(afe_retry)))
                            give_up = now + window
                        if now >= give_up:
                            raise
                        time.sleep(min(ARM_RETRY_POLL_S, give_up - now))
                        continue
                    if not wait:
                        return None
                    return self._wait_state(lst, "ARMED", ("FAULT", "DISARMING", "DISARMED"),
                                            timeout, "ARM", after_index=resp.index)

    def disarm(self, wait: bool = True, timeout: float = 5.0) -> P.Event | None:
        """``DISARM``, written to the port at once - also while another thread
        waits for a deferred command (``MEAS?`` at 2.5 SPS, ``ZERO``, ...,
        up to 10 s), and also when the command/response pairing is lost
        (it is idempotent; its response is then not checked).

        With ``wait``, block until ``EVT STATE DISARMED`` (received after the
        line was written) or ``STAT?`` reports it; the ``OK``, which keeps its
        place behind earlier commands, is checked if it has arrived and
        consumed in the background otherwise. Without ``wait`` (or when
        already DISARMED) wait only for the ``OK``, unless earlier commands
        are still unanswered. Raises :class:`ErrState` (``DISARM`` in FAULT),
        :class:`StateChangeFailed` on FAULT, the package
        :class:`TimeoutError`, :class:`SessionLost`.
        """
        with self._activity():
            was = (self.state or "").upper()
            with self.listen(self._is_state) as lst:
                desync = self._desync
                slot = self._send("DISARM", self.command_timeout, urgent=True,
                                  background=desync is not None, allow_desync=True)
                if desync is not None:
                    log.warning("DISARM sent although the command/response pairing is lost "
                                "(%s); its response is not checked", desync)
                    if not wait or was == "DISARMED":
                        return None
                    return self._wait_state(lst, "DISARMED", ("FAULT",), timeout, "DISARM",
                                            after_time=slot.t_sent, poll=None)
                try:
                    if not wait or was == "DISARMED":
                        if slot.ahead == 0:
                            self._await(slot)
                            self._finish(slot)
                        return None
                    return self._wait_state(lst, "DISARMED", ("FAULT",), timeout, "DISARM",
                                            after_time=slot.t_sent, slot=slot)
                finally:
                    self._release_slot(slot)

    def _ramp_rate_v_per_s(self) -> Decimal:
        rr = self._cfg_known.get("ramp_rate")
        if not _positive_number(rr):
            try:
                self.cfg_get()
            except Exception:  # pragma: no cover - fall back to the slowest rate
                pass
            rr = self._cfg_known.get("ramp_rate")
        return Decimal(rr) if _positive_number(rr) else Decimal("0.01")

    def src(self, volts: Any, wait: bool = True, timeout: float | None = None) -> P.Event | None:
        """``SRC V=<volts>``; with ``wait`` block until ``EVT SRC_DONE``.

        The default timeout is the worst-case 20 V ramp at the configured
        ``ramp_rate`` plus 3 s. A compliance trip ends the ramp at 0 V
        (``SRC_DONE status=compliance``): :class:`StateChangeFailed`.
        """
        v = P.format_volts(volts)

        def flt(ev: P.Event) -> bool:
            return ev.type in ("SRC_DONE", "STATE")

        with self._activity(), self.listen(flt) as lst:
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
        self._cfg_known.clear()

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
        :class:`stage1iv.sweeps.ListPoint` (``settle_us=None``: the
        configuration's ``settle_us``).
        """
        lines = list_add_commands(points)
        with self._activity(), self._cmd_lock:
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
        """``SWP.ABORT``, written to the port at once - also while another
        thread waits for a deferred command - then wait for its ``OK``
        (:class:`ErrState` if no sweep is running). Also sent when the
        command/response pairing is lost; its response is then not checked."""
        with self._activity():
            desync = self._desync
            slot = self._send("SWP.ABORT", self.command_timeout, urgent=True,
                              background=desync is not None, allow_desync=True)
            if desync is not None:
                log.warning("SWP.ABORT sent although the command/response pairing is lost "
                            "(%s); its response is not checked", desync)
                return
            self._await(slot)
            self._finish(slot)

    # ---- sweep execution -------------------------------------------------------------------

    def _check_bringup(self, allow: bool | None) -> None:
        allow = self.allow_bringup if allow is None else allow
        info = self.idn()
        if info.is_bringup and not allow:
            raise BringupBuildRefused(
                "the instrument runs a BRING-UP firmware build (unsafe test commands "
                "compiled in); sweeps need allow_bringup=True / --allow-bringup")

    def _abort_after_error(self, exc: BaseException) -> None:
        """Best-effort ``SWP.ABORT`` (urgent) for a sweep that is still running
        when collecting it failed; never raises an ordinary exception."""
        if self._lost is not None or not self._opened or self._stop.is_set():
            return
        reason = type(exc).__name__
        try:
            desync = self._desync is not None
            slot = self._send("SWP.ABORT", self.command_timeout, urgent=True,
                              background=desync, allow_desync=True)
        except Exception as e:
            log.warning("SWP.ABORT after %s failed: %s", reason, e)
            return
        if desync:
            log.warning("sweep aborted after %s (SWP.ABORT sent; the command/response "
                        "pairing is lost)", reason)
            return
        try:
            self._await(slot)
            self._finish(slot)
            log.warning("sweep aborted after %s", reason)
        except ErrState:
            log.debug("SWP.ABORT after %s: the sweep had already ended", reason)
        except Exception as e:
            log.warning("SWP.ABORT after %s failed: %s", reason, e)

    def run_sweep(self, on_record: Callable[[PTRecord], None] | None = None,
                  timeout: float | None = None, *, allow_incomplete: bool = False,
                  allow_bringup: bool | None = None, fetch_retries: int = 3,
                  end_grace: float = 2.0) -> SweepResult:
        """``SWP.RUN`` and collect the sweep.

        * Records are collected in point order; a jump in ``seq`` triggers
          ``DATA.FETCH FROM=<first missing>`` at once (the ring retains only
          1024 records), and the sweep is checked for completeness at the end
          (with up to ``fetch_retries`` further fetches).
        * ``on_record(rec)`` is called for every new record, in this thread,
          outside the keep-alive (a callback that blocks for
          ``host_timeout_s`` lets the instrument disarm; use :meth:`hold`
          inside it for deliberate long work).
        * If ``SWEEP_END`` was dropped by the firmware, ``STAT?`` is used to
          detect the end and the status is inferred
          (:attr:`SweepResult.status_inferred`).
        * Raises :class:`SweepFailed` for a status other than ``complete`` and
          :class:`RecordsLost` for unrecoverable gaps, unless
          ``allow_incomplete``; :class:`BringupBuildRefused` on a bring-up
          build without ``allow_bringup``.
        * If anything goes wrong while the sweep runs - a timeout, an
          interruption, an exception from ``on_record``, a protocol error, a
          failing ``DATA.FETCH`` - the sweep is aborted (``SWP.ABORT``, sent
          at once, also when the command/response pairing is lost) before the
          exception propagates, unless the session is lost. The records
          collected so far stay in :attr:`last_sweep`.
        """
        self.last_sweep = None
        with self._activity():
            self._check_bringup(allow_bringup)

            def flt(ev: P.Event) -> bool:
                return ev.type in ("SWEEP_BEGIN", "SWEEP_END", "STATE", "WARN")

            with self.listen(flt, records=True) as lst:
                result: SweepResult | None = None
                refused = False
                self._raise_if_unusable()       # nothing sent, nothing to abort
                try:
                    try:
                        resp = self.command("SWP.RUN")
                    except DeviceError:
                        refused = True          # ERR: no sweep was started
                        raise
                    try:
                        sweep_id = int(resp["id"])
                        planned = int(resp["points"])
                        est_s = Decimal(resp["est_s"])
                    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
                        raise ProtocolError(f"malformed SWP.RUN response {resp.line!r}") from exc
                    if timeout is None:
                        timeout = float(est_s) * 1.5 + 30.0
                    result = SweepResult(sweep_id=sweep_id, points_planned=planned, est_s=est_s,
                                         status="unknown", records=[])
                    self.last_sweep = result
                    self._collect_sweep(lst, result, on_record, timeout, fetch_retries,
                                        end_grace)
                except BaseException as exc:
                    if not refused and not (result is not None and result.ended):
                        self._abort_after_error(exc)
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
        try:
            self._collect_into(by_idx, lst, result, on_record, timeout, fetch_retries,
                               end_grace)
        finally:
            result.records = [by_idx[k] for k in sorted(by_idx)]

    def _collect_into(self, by_idx: dict[int, PTRecord], lst: _Listener, result: SweepResult,
                      on_record: Callable[[PTRecord], None] | None, timeout: float,
                      fetch_retries: int, end_grace: float) -> None:
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
                    with self._user_code():
                        on_record(rec)

        def fetch(from_seq: int) -> tuple[int, int]:
            result.fetches += 1
            r = self.command(f"DATA.FETCH FROM={P.format_int(from_seq, 'FROM')}")
            return int(r.get("first", 0)), int(r.get("last", -1))

        while result.end is None:
            now = time.monotonic()
            if now >= deadline:
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
        with self._activity(), self.listen(None, records=True) as lst:
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
           "SweepResult", "EXPECTED_TAG", "URGENT_VERBS", "KEEPALIVE_INTERVAL_S",
           "COMMAND_TIMEOUT_S", "DEFERRED_TIMEOUT_S", "ARM_AFE_RETRY_S", "is_urgent",
           "iter_records_by_seq"]
