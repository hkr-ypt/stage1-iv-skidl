"""Line transports: the byte pipe under :class:`stage1iv.client.Instrument`.

* :class:`SerialTransport` - the instrument's USB CDC-ACM port (or the
  simulator's pseudo-terminal) through pyserial. It opens the port with
  ``dsrdtr=False`` and explicitly asserts DTR: DTR is one of the three
  host-session conditions (protocol.md section 1), so the instrument refuses
  to arm, and disarms, without it. A background thread reads the port and
  queues complete lines.
* :class:`LoopbackTransport` - an in-memory pair of queues for tests; the
  "device" side is driven by the test (see host/tests/fakedevice.py).

Reading after the port has failed or vanished raises
:class:`stage1iv.errors.SessionLost`.
"""

from __future__ import annotations

import abc
import errno
import logging
import queue
import threading
import time
from typing import Any

from .errors import SessionLost
from .errors import TimeoutError as S1Timeout

log = logging.getLogger(__name__)


class _Closed:
    """Queue sentinel: the line source ended (``exc`` says why)."""

    def __init__(self, exc: BaseException | None = None):
        self.exc = exc


class Transport(abc.ABC):
    """A bidirectional line channel (lines without terminators)."""

    @abc.abstractmethod
    def write_line(self, line: str) -> None:
        """Send one line; the transport appends ``\\n``."""

    @abc.abstractmethod
    def read_line(self, timeout: float | None) -> str | None:
        """Next received line, or ``None`` after ``timeout`` seconds.

        Raises :class:`SessionLost` once the channel has failed or closed.
        """

    @abc.abstractmethod
    def close(self) -> None:
        """Release the channel (idempotent)."""

    @property
    @abc.abstractmethod
    def is_open(self) -> bool:
        """True until closed or failed."""

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class _QueueReader:
    """Shared helper: a queue of lines terminated by a :class:`_Closed`."""

    def __init__(self) -> None:
        self._rx: "queue.Queue[str | _Closed]" = queue.Queue()
        self._closed: _Closed | None = None

    def _get(self, timeout: float | None) -> str | None:
        if self._closed is not None and self._rx.empty():
            raise SessionLost(f"transport closed: {self._closed.exc or 'closed'}")
        try:
            item = self._rx.get(timeout=timeout) if timeout is None or timeout > 0 \
                else self._rx.get_nowait()
        except queue.Empty:
            return None
        if isinstance(item, _Closed):
            self._closed = item
            # Keep the sentinel visible to later readers.
            self._rx.put(item)
            raise SessionLost(f"transport closed: {item.exc or 'closed'}")
        return item


class SerialTransport(Transport, _QueueReader):
    """pyserial transport with a background line reader.

    ``port`` is a device name from discovery (:mod:`stage1iv.discovery`) or a
    simulator pseudo-terminal - never a hard-coded path.
    """

    #: Baud rate, parity and stop bits are ignored by a CDC-ACM device.
    BAUDRATE = 115200

    def __init__(self, port: str, *, write_timeout: float = 5.0,
                 read_poll: float = 0.05, max_line: int = 65536):
        _QueueReader.__init__(self)
        import serial  # pyserial; imported here so the rest works without it

        self._serial_mod = serial
        self.port = port
        self._max_line = max_line
        ser = serial.Serial()
        ser.port = port
        ser.baudrate = self.BAUDRATE
        ser.bytesize = serial.EIGHTBITS
        ser.parity = serial.PARITY_NONE
        ser.stopbits = serial.STOPBITS_ONE
        ser.xonxoff = False
        ser.rtscts = False
        ser.dsrdtr = False          # DTR is driven by us, not by flow control
        ser.timeout = read_poll
        ser.write_timeout = write_timeout
        ser.dtr = True              # applied when the port opens
        ser.open()
        self._ser = ser
        self.assert_dtr(True)
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._reader, name=f"stage1iv-rx-{port}",
                                        daemon=True)
        self._thread.start()

    def assert_dtr(self, state: bool = True) -> None:
        """Set DTR explicitly. A pseudo-terminal (simulator) has no modem
        lines; that case is logged and ignored."""
        try:
            self._ser.dtr = state
        except (OSError, self._serial_mod.SerialException) as exc:
            err = getattr(exc, "errno", None)
            if err in (errno.EINVAL, errno.ENOTTY) or "ioctl" in str(exc).lower():
                log.debug("DTR not supported on %s (pseudo-terminal?): %s", self.port, exc)
            else:
                raise

    def _reader(self) -> None:
        buf = bytearray()
        ser = self._ser
        try:
            while not self._stop.is_set():
                try:
                    waiting = ser.in_waiting
                    chunk = ser.read(waiting if waiting > 0 else 1)
                except (OSError, self._serial_mod.SerialException, TypeError, AttributeError) as exc:
                    if self._stop.is_set():
                        break
                    raise SessionLost(f"serial port {self.port} failed: {exc}") from exc
                if not chunk:
                    continue
                buf.extend(chunk)
                while True:
                    nl = buf.find(b"\n")
                    if nl < 0:
                        break
                    raw = bytes(buf[:nl])
                    del buf[:nl + 1]
                    if raw.endswith(b"\r"):
                        raw = raw[:-1]
                    self._rx.put(raw.decode("ascii", errors="replace"))
                if len(buf) > self._max_line:
                    log.warning("discarding %d bytes without a line terminator", len(buf))
                    buf.clear()
        except SessionLost as exc:
            self._rx.put(_Closed(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            self._rx.put(_Closed(exc))
            return
        self._rx.put(_Closed(None))

    def write_line(self, line: str) -> None:
        if self._closed is not None or self._stop.is_set():
            raise SessionLost(f"serial port {self.port} is closed")
        data = line.encode("ascii") + b"\n"
        with self._write_lock:
            try:
                # No flush(): tcdrain() on a pseudo-terminal blocks until the
                # other side reads, without any timeout.
                self._ser.write(data)
            except self._serial_mod.SerialTimeoutException as exc:
                raise S1Timeout(f"write to {self.port} timed out (device not reading?)") from exc
            except (OSError, self._serial_mod.SerialException) as exc:
                raise SessionLost(f"serial port {self.port} failed on write: {exc}") from exc

    def read_line(self, timeout: float | None) -> str | None:
        return self._get(timeout)

    @property
    def is_open(self) -> bool:
        return self._closed is None and not self._stop.is_set()

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self._ser.close()
        except Exception:  # pragma: no cover - best effort
            pass
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)
        self._rx.put(_Closed(None))


class LoopbackTransport(Transport, _QueueReader):
    """In-memory transport for tests.

    Host side: the :class:`Transport` interface. Device side:
    :meth:`device_read_line`, :meth:`device_write_line`,
    :meth:`device_disconnect`. ``sent`` records every host line in order.
    """

    def __init__(self) -> None:
        _QueueReader.__init__(self)
        self._tx: "queue.Queue[str | _Closed]" = queue.Queue()
        self._open = True
        self.sent: list[str] = []
        self._sent_lock = threading.Lock()

    # ---- host side
    def write_line(self, line: str) -> None:
        if not self._open or self._closed is not None:
            raise SessionLost("loopback transport is closed")
        if "\n" in line or "\r" in line:
            raise ValueError("line terminators are added by the transport")
        with self._sent_lock:
            self.sent.append(line)
        self._tx.put(line)

    def read_line(self, timeout: float | None) -> str | None:
        return self._get(timeout)

    @property
    def is_open(self) -> bool:
        return self._open and self._closed is None

    def close(self) -> None:
        if not self._open:
            return
        self._open = False
        self._rx.put(_Closed(None))
        self._tx.put(_Closed(None))

    # ---- device side
    def device_read_line(self, timeout: float | None = None) -> str | None:
        """Next host line; ``None`` on timeout or once the host closed."""
        try:
            item = self._tx.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(item, _Closed):
            self._tx.put(item)
            return None
        return item

    def device_write_line(self, line: str) -> None:
        """Deliver one line to the host."""
        if self._open:
            self._rx.put(line)

    def device_disconnect(self, reason: str = "device vanished") -> None:
        """Simulate the port disappearing (USB unplug)."""
        self._rx.put(_Closed(OSError(reason)))

    def sent_lines(self) -> list[str]:
        with self._sent_lock:
            return list(self.sent)

    def wait_sent(self, predicate, timeout: float = 2.0) -> bool:
        """Wait until ``predicate(sent_lines)`` holds (test helper)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(self.sent_lines()):
                return True
            time.sleep(0.005)
        return predicate(self.sent_lines())


__all__ = ["Transport", "SerialTransport", "LoopbackTransport"]
