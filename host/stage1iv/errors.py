"""Exception hierarchy of the stage1iv host library.

Every exception raised on purpose by this package derives from
:class:`Stage1IVError`.

* :class:`ProtocolError` - the device sent something that violates
  docs/firmware/protocol.md (malformed line, unsolicited response, lost
  records that could not be recovered).
* :class:`DeviceError` - the device answered ``ERR <CODE> <message>``
  (protocol.md section 4). There is one subclass per error code; use
  :func:`device_error` to build the right one.
* :class:`TimeoutError` - no response / event within the allowed time. It
  also derives from the built-in :class:`TimeoutError`, so ``except
  TimeoutError`` catches it either way.
* :class:`SessionLost` - the host session ended (serial error, port vanished,
  ``EVT STATE ... reason=session_lost``, a new ``EVT HELLO`` mid-operation).
* :class:`SweepFailed` - a sweep ended with a status other than ``complete``.
* :class:`BringupBuildRefused` - a sweep was requested on a ``build=bringup``
  firmware without ``allow_bringup=True``.
"""

from __future__ import annotations

import builtins
from typing import Any


class Stage1IVError(Exception):
    """Base class of every exception raised by this package."""


class ProtocolError(Stage1IVError):
    """The device violated the line protocol (docs/firmware/protocol.md)."""


class RecordsLost(ProtocolError):
    """Measurement records are missing and ``DATA.FETCH`` could not recover them.

    ``missing`` is the sorted list of missing point indices (``idx``).
    """

    def __init__(self, message: str, missing: list[int] | None = None):
        super().__init__(message)
        self.missing = list(missing or [])


class DeviceError(Stage1IVError):
    """``ERR <CODE> <message>`` from the device."""

    code: str = ""

    def __init__(self, code: str, message: str = "", command: str | None = None):
        self.code = code
        self.message = message
        self.command = command
        text = f"ERR {code}"
        if message:
            text += f" {message}"
        if command:
            text += f" (command: {command!r})"
        super().__init__(text)


class ErrSyntax(DeviceError):
    """``ERR SYNTAX``: the line could not be tokenised."""


class ErrUnknown(DeviceError):
    """``ERR UNKNOWN``: unknown verb (also bring-up verbs in other builds)."""


class ErrArg(DeviceError):
    """``ERR ARG``: wrong number/name/format of arguments, inexact number.

    For ``SWP.WP`` the firmware may append ``suggest=<V>``; it is exposed as
    :attr:`suggest` (the raw string) when present.
    """

    @property
    def suggest(self) -> str | None:
        for tok in self.message.split():
            if tok.lower().startswith("suggest="):
                return tok.split("=", 1)[1]
        return None


class ErrRange(DeviceError):
    """``ERR RANGE``: a value outside its allowed range."""


class ErrState(DeviceError):
    """``ERR STATE``: not allowed in the current state."""


class ErrBusy(DeviceError):
    """``ERR BUSY``: a resource is temporarily unavailable."""


class ErrFault(DeviceError):
    """``ERR FAULT``: a latched fault blocks the command."""


class ErrPrecond(DeviceError):
    """``ERR PRECOND <name>``: an ``ARM`` precondition failed."""

    @property
    def precondition(self) -> str:
        parts = self.message.split()
        return parts[0] if parts else ""


class ErrFlash(DeviceError):
    """``ERR FLASH``: calibration flash erase/program/verify failed."""


class ErrInternal(DeviceError):
    """``ERR INTERNAL``: an internal invariant failed."""


DEVICE_ERRORS: dict[str, type[DeviceError]] = {
    "SYNTAX": ErrSyntax,
    "UNKNOWN": ErrUnknown,
    "ARG": ErrArg,
    "RANGE": ErrRange,
    "STATE": ErrState,
    "BUSY": ErrBusy,
    "FAULT": ErrFault,
    "PRECOND": ErrPrecond,
    "FLASH": ErrFlash,
    "INTERNAL": ErrInternal,
}

for _code, _cls in DEVICE_ERRORS.items():
    _cls.code = _code
del _code, _cls


def device_error(code: str, message: str = "", command: str | None = None) -> DeviceError:
    """Build the :class:`DeviceError` subclass for ``code``.

    An error code that protocol v1 does not define yields a plain
    :class:`DeviceError` (the caller may additionally treat it as a protocol
    violation).
    """
    cls = DEVICE_ERRORS.get(code, DeviceError)
    return cls(code, message, command)


class TimeoutError(Stage1IVError, builtins.TimeoutError):  # noqa: A001 - deliberate
    """No response or event arrived in time."""


CommandTimeout = TimeoutError


class SessionLost(Stage1IVError):
    """The host session ended; the instrument has disarmed (or will)."""


class StateChangeFailed(Stage1IVError):
    """A requested state change (ARM, DISARM, SRC) ended in another state.

    ``event`` is the event (``EVT STATE FAULT ...`` etc.) that ended the wait,
    when there was one.
    """

    def __init__(self, message: str, event: Any = None):
        super().__init__(message)
        self.event = event


class SweepFailed(Stage1IVError):
    """A sweep ended with ``status`` other than ``complete``.

    ``result`` is the :class:`stage1iv.client.SweepResult` with whatever was
    measured, so partial data is never thrown away.
    """

    def __init__(self, status: str, result: Any = None, message: str | None = None):
        self.status = status
        self.result = result
        super().__init__(message or f"sweep ended with status={status}")


class BringupBuildRefused(Stage1IVError):
    """The firmware is a bring-up build; sweeps need ``allow_bringup=True``."""


__all__ = [
    "Stage1IVError", "ProtocolError", "RecordsLost", "DeviceError", "ErrSyntax",
    "ErrUnknown", "ErrArg", "ErrRange", "ErrState", "ErrBusy", "ErrFault",
    "ErrPrecond", "ErrFlash", "ErrInternal", "DEVICE_ERRORS", "device_error",
    "TimeoutError", "CommandTimeout", "SessionLost", "StateChangeFailed",
    "SweepFailed", "BringupBuildRefused",
]
