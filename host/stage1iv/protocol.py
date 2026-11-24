"""Line protocol v2 (docs/firmware/protocol.md): classification, parsing and
exact argument formatting.

Device -> host lines start with one of ``OK``, ``ERR``, ``EVT``, ``PT`` or
``#`` (protocol.md section 2). Payloads are ``key=value`` tokens, optionally
preceded by positional words (``OK IDN model=...``, ``EVT STATE ARMED
reason=...``). Values are kept as the exact strings received
(:attr:`Payload.fields`) and converted on demand (:meth:`Payload.get`):
integers to ``int``, ``0x``-prefixed values to ``int``, other decimals to
:class:`decimal.Decimal` (never float, so nothing is rounded), ``-`` to
``None``, anything else stays a string.

Host -> device arguments must be exactly representable at the firmware's
resolution (protocol.md section 3): volts 1 uV, amperes 1 nA, times 1 us,
data rate 0.1 SPS, ramp rate 1 uV/s. The firmware rejects inexact values
(``ERR ARG inexact``) instead of rounding; the formatters here raise
:class:`ValueError` *before* anything is sent. Floats are interpreted by
their shortest round-trip ``repr`` (``0.1`` means 0.1, not the binary value
0.1000000000000000055...), so ``0.1 + 0.2`` is rejected while ``0.3`` is
accepted. Output is always plain fixed-point decimal text, never scientific
notation.
"""

from __future__ import annotations

import enum
import math
import numbers
import re
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from typing import Any, Iterable, Mapping

from .errors import ProtocolError

#: ``proto=`` of ``*IDN?``/``EVT HELLO`` this library implements. Version 2
#: added flag bit 11 ``SD_SAT``, ``v_V``/``i_A`` from the unrounded means and
#: the 100 mA ``i_compliance`` default (host-visible), besides firmware-side
#: changes that need no host support (empty line before ``EVT HELLO``,
#: ``CAL? measured=``, warn bit 10, ``ERR PRECOND busy``).
PROTOCOL_VERSION = 2

#: Maximum host -> device line length in characters (without the newline).
LINE_MAX = 256

#: Commands that respond only when their work completes (protocol.md section 2).
DEFERRED_VERBS = frozenset({"MEAS?", "ZERO", "ADC.SELFCAL", "SELFTEST", "CAL.SAVE"})

#: Device decimal grammar (firmware decimal.h): optional sign, digits with an
#: optional fraction (at least one digit on one side of the point), optional
#: exponent.
NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$")
INT_RE = re.compile(r"^[+-]?\d+$")
HEX_RE = re.compile(r"^0x[0-9A-Fa-f]+$")

#: Keys whose values are identifiers and must never be converted to numbers
#: (a serial number or git hash can consist of decimal digits only).
STRING_KEYS = frozenset({
    "model", "fw", "rev", "hw", "build", "serial", "usb_pid_status",
    "planned_pid", "state", "fault", "disarm_reason", "reason", "reset",
    "sweep_status",
    "cal", "cal_source", "source", "zero", "afe", "kind", "status",
    "adc_state", "adc_err", "on_compliance", "end", "detail", "note",
})

# Resolutions, as the number of decimal places of the base unit.
VOLT_DECIMALS = 6        # 1 uV
AMP_DECIMALS = 9         # 1 nA
SECOND_DECIMALS = 6      # 1 us
SPS_DECIMALS = 1         # 0.1 SPS
RAMP_DECIMALS = 6        # 1 uV/s

_MAX_ADJUSTED_EXPONENT = 30   # refuse absurd magnitudes before exact arithmetic


class Kind(str, enum.Enum):
    """Kind of a device -> host line."""

    OK = "OK"
    ERR = "ERR"
    EVT = "EVT"
    PT = "PT"
    LOG = "#"
    UNKNOWN = "?"


def classify(line: str) -> Kind:
    """Classify one received line (without its terminator)."""
    if line.startswith("#"):
        return Kind.LOG
    parts = line.split(None, 1)
    if not parts:
        return Kind.UNKNOWN
    head = parts[0]
    if head == "OK":
        return Kind.OK
    if head == "ERR":
        return Kind.ERR
    if head == "EVT":
        return Kind.EVT
    if head == "PT":
        return Kind.PT
    return Kind.UNKNOWN


def convert_value(key: str | None, raw: str) -> Any:
    """Convert one payload value string (see the module docstring)."""
    if key is not None and key.lower() in STRING_KEYS:
        return raw
    if raw == "-":
        return None
    if HEX_RE.match(raw):
        return int(raw, 16)
    if INT_RE.match(raw):
        return int(raw)
    if NUMBER_RE.match(raw):
        return Decimal(raw)
    return raw


@dataclass(frozen=True)
class Payload:
    """Positional words and ordered ``key=value`` fields of a line."""

    args: tuple[str, ...] = ()
    fields: Mapping[str, str] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """Converted value of ``key`` (``default`` when absent)."""
        if key not in self.fields:
            return default
        return convert_value(key, self.fields[key])

    def __getitem__(self, key: str) -> Any:
        if key not in self.fields:
            raise KeyError(key)
        return convert_value(key, self.fields[key])

    def __contains__(self, key: object) -> bool:
        return key in self.fields

    def values(self) -> dict[str, Any]:
        """All fields converted, in the order received."""
        return {k: convert_value(k, v) for k, v in self.fields.items()}

    def raw(self) -> dict[str, str]:
        """All fields as the exact strings received, in order."""
        return dict(self.fields)


def parse_payload(text: str) -> Payload:
    """Split ``text`` into positional words and ordered ``key=value`` fields.

    Duplicate keys are a protocol violation (:class:`ProtocolError`).
    """
    args: list[str] = []
    fields: dict[str, str] = {}
    for tok in text.split():
        if "=" in tok:
            key, value = tok.split("=", 1)
            if not key:
                raise ProtocolError(f"empty key in {tok!r}")
            if key in fields:
                raise ProtocolError(f"duplicate key {key!r} in {text!r}")
            fields[key] = value
        else:
            args.append(tok)
    return Payload(tuple(args), fields)


@dataclass(frozen=True)
class Response:
    """A successful response (``OK [payload]``)."""

    line: str
    payload: Payload
    command: str | None = None
    #: Number of responses the dispatcher had matched up to and including
    #: this one; events dispatched later carry an index >= this value.
    index: int = field(default=0, compare=False)

    @property
    def tag(self) -> str | None:
        """The leading word (``IDN``, ``PONG``, ``STAT``, ``SWP`` ...), if any."""
        return self.payload.args[0] if self.payload.args else None

    @property
    def fields(self) -> Mapping[str, str]:
        return self.payload.fields

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.payload[key]

    def __contains__(self, key: object) -> bool:
        return key in self.payload

    def values(self) -> dict[str, Any]:
        return self.payload.values()


def parse_ok(line: str, command: str | None = None, index: int = 0) -> Response:
    """Parse an ``OK`` line."""
    if classify(line) is not Kind.OK:
        raise ProtocolError(f"not an OK line: {line!r}")
    return Response(line, parse_payload(line[2:]), command, index)


def parse_err(line: str) -> tuple[str, str]:
    """Parse ``ERR <CODE> <message>`` into ``(code, message)``."""
    if classify(line) is not Kind.ERR:
        raise ProtocolError(f"not an ERR line: {line!r}")
    parts = line.split(None, 2)
    if len(parts) < 2:
        raise ProtocolError(f"ERR line without a code: {line!r}")
    return parts[1], parts[2].strip() if len(parts) > 2 else ""


@dataclass(frozen=True)
class Event:
    """An asynchronous event ``EVT <TYPE> [word ...] key=value ...``."""

    type: str
    args: tuple[str, ...]
    payload: Payload
    line: str
    received: float = 0.0

    @property
    def fields(self) -> Mapping[str, str]:
        return self.payload.fields

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.payload[key]

    def __contains__(self, key: object) -> bool:
        return key in self.payload

    @property
    def arg(self) -> str | None:
        """First positional word after the type (``ARMED`` in ``EVT STATE ARMED``)."""
        return self.args[0] if self.args else None


def parse_evt(line: str, received: float = 0.0) -> Event:
    """Parse an ``EVT`` line."""
    if classify(line) is not Kind.EVT:
        raise ProtocolError(f"not an EVT line: {line!r}")
    parts = line.split(None, 2)
    if len(parts) < 2 or "=" in parts[1]:
        raise ProtocolError(f"EVT line without a type: {line!r}")
    payload = parse_payload(parts[2] if len(parts) > 2 else "")
    return Event(parts[1], payload.args, payload, line, received)


def verb_of(line: str) -> str:
    """Upper-cased verb of a command line ('' for an empty line)."""
    parts = line.split(None, 1)
    return parts[0].upper() if parts else ""


def is_deferred(line: str) -> bool:
    """True for the deferred commands (MEAS?, ZERO, ADC.SELFCAL, SELFTEST)."""
    return verb_of(line) in DEFERRED_VERBS


# --------------------------------------------------------------------------
# Exact number formatting for command arguments.
# --------------------------------------------------------------------------

def _exact(value: Any, what: str) -> Fraction:
    """``value`` as an exact rational number (see the module docstring)."""
    if isinstance(value, bool):
        raise TypeError(f"{what}: a bool is not a number")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"{what}: {value} is not a finite number")
        if value and value.adjusted() > _MAX_ADJUSTED_EXPONENT:
            raise ValueError(f"{what}: {value} is out of range")
        return Fraction(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{what}: {value!r} is not a finite number")
        return _exact(Decimal(repr(value)), what)
    if isinstance(value, Fraction):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not NUMBER_RE.match(s):
            raise ValueError(f"{what}: {value!r} is not a strict decimal number")
        return _exact(Decimal(s), what)
    if isinstance(value, numbers.Integral):
        return Fraction(int(value))
    if isinstance(value, numbers.Rational):
        return Fraction(int(value.numerator), int(value.denominator))
    if isinstance(value, numbers.Real):
        return _exact(float(value), what)
    raise TypeError(f"{what}: unsupported type {type(value).__name__}")


def to_scaled(value: Any, decimals: int, what: str = "value") -> int:
    """``value * 10**decimals`` as an exact integer; ValueError if inexact."""
    q = _exact(value, what) * (10 ** decimals)
    if q.denominator != 1:
        raise ValueError(
            f"{what}: {value!r} is not exactly representable at a resolution of "
            f"1e-{decimals} (the firmware rejects inexact numbers; round it first)")
    return q.numerator


def scaled_to_str(n: int, decimals: int) -> str:
    """Plain decimal text of ``n * 10**-decimals`` (no exponent, no trailing zeros)."""
    if n == 0:
        return "0"
    sign = "-" if n < 0 else ""
    q, r = divmod(abs(n), 10 ** decimals)
    if decimals == 0 or r == 0:
        return f"{sign}{q}"
    frac = str(r).rjust(decimals, "0").rstrip("0")
    return f"{sign}{q}.{frac}"


def scaled_to_decimal(n: int, decimals: int) -> Decimal:
    """Exact :class:`Decimal` of ``n * 10**-decimals``."""
    return Decimal(n).scaleb(-decimals)


def quantize(value: Any, decimals: int, what: str = "value") -> Decimal:
    """``value`` as an exact Decimal on the 10**-decimals grid (ValueError if off-grid)."""
    return scaled_to_decimal(to_scaled(value, decimals, what), decimals)


def format_fixed(value: Any, decimals: int, what: str = "value") -> str:
    return scaled_to_str(to_scaled(value, decimals, what), decimals)


def format_volts(value: Any) -> str:
    """Volts with 1 uV resolution, e.g. ``0.5`` -> ``"0.5"``."""
    return format_fixed(value, VOLT_DECIMALS, "voltage")


def format_amps(value: Any) -> str:
    """Amperes with 1 nA resolution."""
    return format_fixed(value, AMP_DECIMALS, "current")


def format_seconds(value: Any) -> str:
    """Seconds with 1 us resolution."""
    return format_fixed(value, SECOND_DECIMALS, "time")


def format_sps(value: Any) -> str:
    """ADC data rate with 0.1 SPS resolution."""
    return format_fixed(value, SPS_DECIMALS, "data rate")


def format_ramp_rate(value: Any) -> str:
    """Ramp rate in V/s with 1 uV/s resolution."""
    return format_fixed(value, RAMP_DECIMALS, "ramp rate")


def format_int(value: Any, what: str = "integer", minimum: int | None = 0) -> str:
    """An exact integer as plain digits (``2.0`` -> ``"2"``, ``2.5`` -> ValueError)."""
    n = to_scaled(value, 0, what)
    if minimum is not None and n < minimum:
        raise ValueError(f"{what}: {n} is below {minimum}")
    return str(n)


def format_us(value: Any, what: str = "time (us)") -> str:
    """An integer number of microseconds."""
    return format_int(value, what, minimum=0)


_WORD_RE = re.compile(r"^[A-Za-z0-9_.:+-]+$")


def format_word(value: Any, what: str = "word", allowed: Iterable[str] | None = None) -> str:
    """A bare word argument (no spaces, ``=`` or control characters)."""
    if not isinstance(value, str) or not _WORD_RE.match(value):
        raise ValueError(f"{what}: {value!r} is not a valid word")
    if allowed is not None:
        allowed_l = {a.lower() for a in allowed}
        if value.lower() not in allowed_l:
            raise ValueError(f"{what}: {value!r} is not one of {sorted(allowed_l)}")
    return value


def check_line(line: str) -> str:
    """Validate a host -> device command line (printable ASCII, <= 256 chars)."""
    if len(line) > LINE_MAX:
        raise ValueError(f"command line too long ({len(line)} > {LINE_MAX} characters)")
    for ch in line:
        if not ("\x20" <= ch <= "\x7e" or ch == "\t"):
            raise ValueError(f"command line contains a forbidden character {ch!r}")
    if not line.strip():
        raise ValueError("empty command line")
    return line


def build_command(verb: str, *args: str, **kwargs: str) -> str:
    """Join an already-formatted command line and validate it."""
    parts = [verb, *args]
    parts.extend(f"{k}={v}" for k, v in kwargs.items())
    return check_line(" ".join(parts))


# ---- CFG keys (protocol.md section 5.4) ------------------------------------

CFG_ADC_RATES = (Decimal("2.5"), Decimal("5"), Decimal("10"), Decimal("15"),
                 Decimal("25"), Decimal("30"), Decimal("50"), Decimal("60"),
                 Decimal("100"), Decimal("500"), Decimal("1000"), Decimal("2000"),
                 Decimal("3750"))


def _fmt_adc_rate(v: Any) -> str:
    s = format_sps(v)
    if Decimal(s) not in CFG_ADC_RATES:
        raise ValueError(f"adc_rate: {s} SPS is not one of "
                         f"{', '.join(str(r) for r in CFG_ADC_RATES)}")
    return s


CFG_FORMATTERS = {
    "adc_rate": _fmt_adc_rate,
    "n_avg": lambda v: format_int(v, "n_avg", minimum=1),
    "settle_us": lambda v: format_us(v, "settle_us"),
    "first_settle_us": lambda v: format_us(v, "first_settle_us"),
    "ramp_rate": format_ramp_rate,
    "v_limit": format_volts,
    "i_compliance": format_amps,
    "on_compliance": lambda v: format_word(v, "on_compliance", ("stop", "fault")),
    "end": lambda v: format_word(v, "end", ("zero", "hold")),
    "line_hz": lambda v: format_int(v, "line_hz", minimum=0),
    "host_timeout_s": lambda v: format_int(v, "host_timeout_s", minimum=0),
}


def format_cfg_args(**kwargs: Any) -> list[str]:
    """``key=value`` tokens for ``CFG``; unknown keys raise ValueError."""
    if not kwargs:
        raise ValueError("CFG needs at least one key=value")
    out = []
    for key, value in kwargs.items():
        fmt = CFG_FORMATTERS.get(key.lower())
        if fmt is None:
            raise ValueError(f"unknown CFG key {key!r}; known: {sorted(CFG_FORMATTERS)}")
        out.append(f"{key.lower()}={fmt(value)}")
    return out


__all__ = [
    "PROTOCOL_VERSION", "LINE_MAX", "DEFERRED_VERBS", "NUMBER_RE", "Kind",
    "classify", "convert_value", "Payload", "parse_payload", "Response",
    "parse_ok", "parse_err", "Event", "parse_evt", "verb_of", "is_deferred",
    "to_scaled", "scaled_to_str", "scaled_to_decimal", "quantize",
    "format_fixed", "format_volts", "format_amps", "format_seconds",
    "format_sps", "format_ramp_rate", "format_int", "format_us", "format_word",
    "check_line", "build_command", "CFG_FORMATTERS", "CFG_ADC_RATES",
    "format_cfg_args", "VOLT_DECIMALS", "AMP_DECIMALS", "SECOND_DECIMALS",
    "SPS_DECIMALS", "RAMP_DECIMALS",
]
