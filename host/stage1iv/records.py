"""Measurement records (``PT`` lines, docs/firmware/protocol.md section 6).

::

    PT <sweep_id>,<seq>,<idx>,<seg>,<t_set_us>,<t_i_us>,<t_v_us>,<dac_code>,
       <v_set_V>,<n>,<i_raw>,<v_raw>,<i_sd>,<v_sd>,<v_V>,<i_A>,<flags>

* ``seq`` is global and monotonic per boot: a gap means lost lines, which
  ``DATA.FETCH FROM=<seq>`` recovers (:class:`SeqTracker`, :func:`find_gaps`).
* The times are u32 microseconds from the sweep start and wrap after
  2**32 us = 71.6 min; consecutive records are far closer than that, so they
  are unwrapped monotonically (:class:`U32Unwrapper`,
  :func:`unwrap_record_times`).
* ``i_sd``/``v_sd`` are ``-`` when ``n < 2`` and become ``None`` here.
* Decimal fields are kept as :class:`decimal.Decimal`, which reproduces the
  received text exactly (``str(rec.v_set_v)`` is what the device printed).
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Sequence

from .errors import ProtocolError
from .protocol import NUMBER_RE

#: CSV/protocol names of the 17 fields, in wire order.
PT_FIELDS = (
    "sweep_id", "seq", "idx", "seg", "t_set_us", "t_i_us", "t_v_us", "dac_code",
    "v_set_V", "n", "i_raw", "v_raw", "i_sd", "v_sd", "v_V", "i_A", "flags",
)

U32_WRAP = 1 << 32


class Flag(enum.IntFlag):
    """Per-point flag bits (protocol.md section 6)."""

    OVR_V = 1 << 0        #: voltage conversion clipped
    OVR_I = 1 << 1        #: current conversion clipped
    CLIM = 1 << 2         #: current-limit plateau: not DUT data
    KELVIN = 1 << 3       #: Kelvin consistency check failed
    NO_OUTPUT = 1 << 4    #: force path open
    COMPLIANCE = 1 << 5   #: software compliance tripped at this point
    LATE = 1 << 6         #: settle or read timing missed its deadline
    FIRST = 1 << 7        #: first point after the pre-ramp
    CAL_NOMINAL = 1 << 8  #: nominal (uncalibrated) constants in use
    ZERO_STALE = 1 << 9   #: no valid session zero
    MEAS = 1 << 10        #: (MEAS? only)


#: Flags that mean the point is not valid DUT data.
INVALID_DATA_FLAGS = Flag.OVR_V | Flag.OVR_I | Flag.CLIM | Flag.NO_OUTPUT


def decode_flags(flags: int) -> list[str]:
    """Names of the set bits, in bit order; undefined bits as ``BIT<n>``."""
    names = []
    for bit in range(16):
        mask = 1 << bit
        if flags & mask:
            try:
                names.append(Flag(mask).name or f"BIT{bit}")
            except ValueError:
                names.append(f"BIT{bit}")
    return names


def format_flags(flags: int) -> str:
    """``0x`` + 4 upper-case hex digits, as the firmware prints them."""
    return f"0x{flags & 0xFFFF:04X}"


@dataclass(frozen=True)
class PTRecord:
    """One measurement record. Attribute names are the protocol names in
    lower case (``v_set_V`` -> ``v_set_v``, ``v_V`` -> ``v_v``, ``i_A`` -> ``i_a``)."""

    sweep_id: int
    seq: int
    idx: int
    seg: int
    t_set_us: int
    t_i_us: int
    t_v_us: int
    dac_code: int
    v_set_v: Decimal
    n: int
    i_raw: int
    v_raw: int
    i_sd: Decimal | None
    v_sd: Decimal | None
    v_v: Decimal
    i_a: Decimal
    flags: int
    line: str = field(default="", compare=False, repr=False)

    @property
    def flag_names(self) -> list[str]:
        return decode_flags(self.flags)

    def has(self, flag: Flag | int) -> bool:
        return bool(self.flags & int(flag))

    @property
    def valid_data(self) -> bool:
        """False when a flag marks the point as not DUT data (clip, CLIM, NO_OUTPUT)."""
        return not (self.flags & int(INVALID_DATA_FLAGS))

    def wire_values(self) -> list[str]:
        """The 17 fields as text, in wire order (``-`` for a missing sd)."""
        def sd(x: Decimal | None) -> str:
            return "-" if x is None else str(x)
        return [
            str(self.sweep_id), str(self.seq), str(self.idx), str(self.seg),
            str(self.t_set_us), str(self.t_i_us), str(self.t_v_us), str(self.dac_code),
            str(self.v_set_v), str(self.n), str(self.i_raw), str(self.v_raw),
            sd(self.i_sd), sd(self.v_sd), str(self.v_v), str(self.i_a),
            format_flags(self.flags),
        ]

    def to_line(self) -> str:
        """Re-create the ``PT`` line."""
        return "PT " + ",".join(self.wire_values())


_UINT_RE = re.compile(r"^\d+$")
_SINT_RE = re.compile(r"^[+-]?\d+$")
_FLAGS_RE = re.compile(r"^0x[0-9A-Fa-f]{4}$")


def _uint(name: str, s: str, maximum: int = U32_WRAP - 1) -> int:
    if not _UINT_RE.match(s):
        raise ProtocolError(f"PT field {name}: {s!r} is not an unsigned integer")
    v = int(s)
    if v > maximum:
        raise ProtocolError(f"PT field {name}: {v} exceeds {maximum}")
    return v


def _sint(name: str, s: str) -> int:
    if not _SINT_RE.match(s):
        raise ProtocolError(f"PT field {name}: {s!r} is not an integer")
    return int(s)


def _dec(name: str, s: str) -> Decimal:
    if not NUMBER_RE.match(s):
        raise ProtocolError(f"PT field {name}: {s!r} is not a decimal number")
    return Decimal(s)


def _sd(name: str, s: str) -> Decimal | None:
    if s == "-":
        return None
    d = _dec(name, s)
    if d < 0:
        raise ProtocolError(f"PT field {name}: negative standard deviation {s!r}")
    return d


def parse_pt(line: str) -> PTRecord:
    """Parse one ``PT`` line; :class:`ProtocolError` if it is malformed."""
    parts = line.split(None, 1)
    if not parts or parts[0] != "PT" or len(parts) != 2:
        raise ProtocolError(f"not a PT line: {line!r}")
    body = parts[1].strip()
    f = body.split(",")
    if len(f) != len(PT_FIELDS):
        raise ProtocolError(f"PT line has {len(f)} fields, expected {len(PT_FIELDS)}: {line!r}")
    if not _FLAGS_RE.match(f[16]):
        raise ProtocolError(f"PT field flags: {f[16]!r} is not 0x + 4 hex digits")
    return PTRecord(
        sweep_id=_uint("sweep_id", f[0]),
        seq=_uint("seq", f[1]),
        idx=_uint("idx", f[2]),
        seg=_uint("seg", f[3]),
        t_set_us=_uint("t_set_us", f[4]),
        t_i_us=_uint("t_i_us", f[5]),
        t_v_us=_uint("t_v_us", f[6]),
        dac_code=_uint("dac_code", f[7], 65535),
        v_set_v=_dec("v_set_V", f[8]),
        n=_uint("n", f[9]),
        i_raw=_sint("i_raw", f[10]),
        v_raw=_sint("v_raw", f[11]),
        i_sd=_sd("i_sd", f[12]),
        v_sd=_sd("v_sd", f[13]),
        v_v=_dec("v_V", f[14]),
        i_a=_dec("i_A", f[15]),
        flags=int(f[16], 16),
        line=line,
    )


# ---- sequence gaps -----------------------------------------------------------

def find_gaps(seqs: Iterable[int]) -> list[tuple[int, int]]:
    """Missing inclusive ``(first, last)`` ranges between the given sequence
    numbers (order and duplicates do not matter)."""
    s = sorted(set(seqs))
    gaps = []
    for a, b in zip(s, s[1:]):
        if b > a + 1:
            gaps.append((a + 1, b - 1))
    return gaps


def missing_indices(indices: Iterable[int], count: int) -> list[int]:
    """Point indices in ``0..count-1`` that are absent from ``indices``."""
    have = set(indices)
    return [i for i in range(count) if i not in have]


class SeqTracker:
    """Streaming check of the global ``seq`` numbers.

    :meth:`feed` returns the missing inclusive range ``(first, last)`` when a
    record skips ahead, otherwise ``None``. Records at or below the last seen
    sequence number (replays after ``DATA.FETCH``) are counted in
    :attr:`replays` and do not move the expectation backwards.
    """

    def __init__(self, expected: int | None = None):
        self.expected = expected
        self.replays = 0
        self.gaps: list[tuple[int, int]] = []

    def feed(self, seq: int) -> tuple[int, int] | None:
        if self.expected is None:
            self.expected = seq + 1
            return None
        if seq == self.expected:
            self.expected += 1
            return None
        if seq > self.expected:
            gap = (self.expected, seq - 1)
            self.gaps.append(gap)
            self.expected = seq + 1
            return gap
        self.replays += 1
        return None


# ---- timestamp unwrapping ------------------------------------------------------

class U32Unwrapper:
    """Monotonic unwrapping of a u32 microsecond counter.

    Each value is assumed to be no earlier than the previous one and less than
    2**32 us later (the firmware guarantees consecutive records are far closer
    than 71.6 min). ``start`` is the raw value the sequence starts after
    (0 = the sweep start).
    """

    def __init__(self, start: int = 0):
        self._last_raw = start % U32_WRAP
        self._offset = (start // U32_WRAP) * U32_WRAP

    def __call__(self, raw: int) -> int:
        if not 0 <= raw < U32_WRAP:
            raise ValueError(f"{raw} is not a u32 value")
        if raw < self._last_raw:
            self._offset += U32_WRAP
        self._last_raw = raw
        return self._offset + raw


def unwrap_u32(values: Iterable[int], start: int = 0) -> list[int]:
    """Unwrap a sequence of u32 microsecond values (see :class:`U32Unwrapper`)."""
    u = U32Unwrapper(start)
    return [u(v) for v in values]


def unwrap_record_times(records: Sequence[PTRecord]) -> list[tuple[int, int, int]]:
    """64-bit ``(t_set, t_i, t_v)`` in us from the sweep start, for records
    given in point order (sort by ``seq``/``idx`` and de-duplicate first)."""
    us, ui, uv = U32Unwrapper(), U32Unwrapper(), U32Unwrapper()
    return [(us(r.t_set_us), ui(r.t_i_us), uv(r.t_v_us)) for r in records]


__all__ = [
    "PT_FIELDS", "U32_WRAP", "Flag", "INVALID_DATA_FLAGS", "decode_flags",
    "format_flags", "PTRecord", "parse_pt", "find_gaps", "missing_indices",
    "SeqTracker", "U32Unwrapper", "unwrap_u32", "unwrap_record_times",
]
