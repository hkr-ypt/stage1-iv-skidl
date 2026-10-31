"""Find instruments by USB identity (docs/firmware/firmware_spec.md section 8.1).

The instrument enumerates as a CDC-ACM port with VID 0x1209 (pid.codes).
Development firmware uses PID 0x0001, the pid.codes PID reserved for
*private in-house testing*: it is **not unique**, other test devices use it
too. So with that PID this module also checks the product string (``Stage1
IV ...``) when the operating system reports one, and logs a warning. A
registered PID, once assigned, is configured with the ``pid`` argument or the
``STAGE1IV_PID`` environment variable (e.g. ``STAGE1IV_PID=0x1234``).

The serial string is the STM32 96-bit UID as 24 hex characters; select an
individual instrument with ``serial=``. Device paths are never hard-coded:
they come from :func:`serial.tools.list_ports.comports`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

VID = 0x1209
#: pid.codes private-testing PID used by development builds (not unique).
PID_DEV_TEST = 0x0001
#: Product string prefix (firmware_spec.md section 8.1: "Stage1 IV" + version).
PRODUCT_PREFIX = "Stage1 IV"
PID_ENV = "STAGE1IV_PID"

DEV_PID_WARNING = (
    "USB PID 0x0001 is the pid.codes private-testing PID: it is not unique and "
    "other devices may use it. Select the instrument by serial number "
    "(--serial) and register a project PID before release.")


@dataclass(frozen=True)
class DeviceInfo:
    """One candidate port."""

    device: str
    vid: int | None
    pid: int | None
    serial_number: str | None
    product: str | None
    description: str | None
    manufacturer: str | None
    location: str | None


def parse_pid(value: Any) -> int:
    """A PID from an int or a hex string (``"0x1234"`` or ``"1234"``; USB IDs are hex)."""
    if isinstance(value, int) and not isinstance(value, bool):
        pid = value
    elif isinstance(value, str):
        pid = int(value.strip(), 16)
    else:
        raise TypeError(f"PID must be an int or a hex string, not {type(value).__name__}")
    if not 0 <= pid <= 0xFFFF:
        raise ValueError(f"PID {value!r} out of range")
    return pid


def configured_pid(pid: Any = None) -> int:
    """The PID to search for: ``pid``, else ``$STAGE1IV_PID``, else 0x0001."""
    if pid is not None:
        return parse_pid(pid)
    env = os.environ.get(PID_ENV)
    if env:
        return parse_pid(env)
    return PID_DEV_TEST


def _from_port(p: Any) -> DeviceInfo:
    return DeviceInfo(
        device=p.device,
        vid=getattr(p, "vid", None),
        pid=getattr(p, "pid", None),
        serial_number=getattr(p, "serial_number", None),
        product=getattr(p, "product", None),
        description=getattr(p, "description", None),
        manufacturer=getattr(p, "manufacturer", None),
        location=getattr(p, "location", None),
    )


def _default_lister() -> Iterable[Any]:
    from serial.tools import list_ports
    return list_ports.comports()


def find_devices(serial: str | None = None, pid: Any = None, vid: int = VID,
                 check_product: bool = True,
                 lister: Callable[[], Iterable[Any]] | None = None) -> list[DeviceInfo]:
    """All ports matching VID/PID (and ``serial`` if given, case-insensitive).

    With the development PID 0x0001 and ``check_product``, ports whose OS
    reports a product string not starting with ``Stage1 IV`` are skipped.
    """
    want_pid = configured_pid(pid)
    if want_pid == PID_DEV_TEST:
        log.warning(DEV_PID_WARNING)
    ports = (lister or _default_lister)()
    out = []
    for p in ports:
        info = _from_port(p)
        if info.vid != vid or info.pid != want_pid:
            continue
        if serial is not None and (info.serial_number or "").lower() != serial.lower():
            continue
        if (want_pid == PID_DEV_TEST and check_product and info.product
                and not info.product.startswith(PRODUCT_PREFIX)):
            log.info("skipping %s: product %r is not a Stage1 IV", info.device, info.product)
            continue
        out.append(info)
    return sorted(out, key=lambda d: d.device)


class DeviceNotFound(LookupError):
    """No (or more than one) matching instrument."""


def find_device(serial: str | None = None, pid: Any = None, vid: int = VID,
                check_product: bool = True,
                lister: Callable[[], Iterable[Any]] | None = None) -> DeviceInfo:
    """Exactly one matching instrument, else :class:`DeviceNotFound`."""
    devs = find_devices(serial=serial, pid=pid, vid=vid, check_product=check_product,
                        lister=lister)
    want = f"VID 0x{vid:04X} PID 0x{configured_pid(pid):04X}"
    if serial:
        want += f" serial {serial}"
    if not devs:
        raise DeviceNotFound(f"no instrument found ({want})")
    if len(devs) > 1:
        listing = ", ".join(f"{d.device} (serial {d.serial_number})" for d in devs)
        raise DeviceNotFound(f"{len(devs)} instruments match ({want}): {listing}; "
                             "select one with a serial number")
    return devs[0]


__all__ = ["VID", "PID_DEV_TEST", "PRODUCT_PREFIX", "PID_ENV", "DEV_PID_WARNING",
           "DeviceInfo", "parse_pid", "configured_pid", "find_devices",
           "find_device", "DeviceNotFound"]
