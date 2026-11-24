"""Sweep definitions on the firmware's exact 1 uV grid.

Waypoint sweeps (``SWP.WP``, protocol.md section 5.5, firmware_spec.md
section 7.6) follow the **exact-grid rule**: every segment
``|w[i+1] - w[i]|`` is a non-zero integer multiple of the step, and the step
is at least 0.5 mV. The firmware rejects anything else (``ERR ARG not
divisible ... suggest=<V>``) rather than rounding; the builders here check
the same rule before anything is sent and raise :class:`GridError` with the
same suggestion (the largest valid step not above the requested one).

Points of a waypoint sweep are ``w0``, then for each segment the points after
its start up to and including its end, so turning points are measured once
and the up and down branches of a loop land on identical setpoints.
``cycles > 1`` repeats segments 1..n and needs ``w[n] == w[0]``.

List sweeps (``SWP.LIST.*``) take up to 2048 points ``V[:settle_us]``;
:func:`log_list` builds logarithmically spaced ones.

All voltages are returned as exact :class:`decimal.Decimal` values on the
1 uV grid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any, Iterable, Iterator, NamedTuple, Sequence

from .protocol import (VOLT_DECIMALS, build_command, format_us,
                       scaled_to_decimal, scaled_to_str, to_scaled)

UV_PER_V = 10 ** VOLT_DECIMALS
V_MAX_UV = 10_000_000            # V_LIMIT_MAX_UV: nothing beyond 10 V is ever valid
STEP_MIN_UV = 500                # SWEEP_STEP_MIN_UV
WAYPOINTS_MIN = 2
WAYPOINTS_MAX = 16               # SWEEP_WP_MAX
CYCLES_MAX = 1000                # SWEEP_CYCLES_MAX
POINTS_MAX = 1_000_000           # SWEEP_POINTS_MAX
LIST_MAX = 2048                  # SWEEP_LIST_MAX
LIST_ADD_MAX = 8                 # points per SWP.LIST.ADD line
SETTLE_MIN_US = 100
SETTLE_MAX_US = 60_000_000


class GridError(ValueError):
    """A waypoint sweep violates the exact-grid rule.

    ``suggest`` is the largest step (Decimal volts) not above the requested one
    that divides every segment, or ``None`` if there is none >= 0.5 mV.
    """

    def __init__(self, message: str, suggest: Decimal | None = None):
        super().__init__(message if suggest is None else f"{message}; suggest step={suggest}")
        self.suggest = suggest


def to_uv(value: Any, what: str = "voltage") -> int:
    """Exact integer microvolts (ValueError if not on the 1 uV grid)."""
    return to_scaled(value, VOLT_DECIMALS, what)


def uv_to_volts(uv: int) -> Decimal:
    """Exact Decimal volts from integer microvolts (6 decimals)."""
    return scaled_to_decimal(uv, VOLT_DECIMALS)


def _check_v(uv: int, what: str) -> None:
    if abs(uv) > V_MAX_UV:
        raise ValueError(f"{what}: {uv_to_volts(uv)} V is beyond the 10 V ceiling")


def _divisors_upto(g: int, limit: int) -> int | None:
    """Largest divisor of ``g`` that is <= ``limit`` (None if g <= 0)."""
    if g <= 0:
        return None
    best = None
    for i in range(1, math.isqrt(g) + 1):
        if g % i == 0:
            for d in (i, g // i):
                if d <= limit and (best is None or d > best):
                    best = d
    return best


def suggest_step_uv(waypoints_uv: Sequence[int], step_uv: int) -> int | None:
    """The firmware's ``suggest=``: the largest step <= ``step_uv`` dividing
    every segment exactly, or None if none is >= 0.5 mV."""
    segs = [abs(b - a) for a, b in zip(waypoints_uv, waypoints_uv[1:])]
    g = 0
    for s in segs:
        g = math.gcd(g, s)
    if g == 0:
        return None
    d = _divisors_upto(g, max(step_uv, 0))
    if d is None or d < STEP_MIN_UV:
        return None
    return d


class SweepPoint(NamedTuple):
    """A planned point: index, waypoint segment (0 for w0) and setpoint."""

    idx: int
    seg: int
    v: Decimal


@dataclass(frozen=True)
class WaypointSweep:
    """A validated waypoint sweep. Build it with :func:`waypoint_sweep`."""

    waypoints_uv: tuple[int, ...]
    step_uv: int
    cycles: int = 1

    def __post_init__(self) -> None:
        validate_waypoints(self.waypoints_uv, self.step_uv, self.cycles)

    @property
    def waypoints(self) -> tuple[Decimal, ...]:
        return tuple(uv_to_volts(w) for w in self.waypoints_uv)

    @property
    def step(self) -> Decimal:
        return uv_to_volts(self.step_uv)

    @property
    def n_points(self) -> int:
        per_cycle = sum(abs(b - a) // self.step_uv
                        for a, b in zip(self.waypoints_uv, self.waypoints_uv[1:]))
        return 1 + self.cycles * per_cycle

    def iter_points(self) -> Iterator[SweepPoint]:
        """Points in the order the firmware measures them."""
        w = self.waypoints_uv
        idx = 0
        yield SweepPoint(idx, 0, uv_to_volts(w[0]))
        for _ in range(self.cycles):
            for seg in range(1, len(w)):
                a, b = w[seg - 1], w[seg]
                direction = 1 if b > a else -1
                for k in range(1, abs(b - a) // self.step_uv + 1):
                    idx += 1
                    yield SweepPoint(idx, seg, uv_to_volts(a + direction * k * self.step_uv))

    def points(self) -> list[SweepPoint]:
        return list(self.iter_points())

    def command(self) -> str:
        """The ``SWP.WP`` command line."""
        args = [scaled_to_str(w, VOLT_DECIMALS) for w in self.waypoints_uv]
        kwargs = {"STEP": scaled_to_str(self.step_uv, VOLT_DECIMALS)}
        if self.cycles != 1:
            kwargs["CYCLES"] = str(self.cycles)
        return build_command("SWP.WP", *args, **kwargs)


def validate_waypoints(waypoints_uv: Sequence[int], step_uv: int, cycles: int = 1) -> int:
    """Check a waypoint definition like the firmware does; returns the point count."""
    n = len(waypoints_uv)
    if not WAYPOINTS_MIN <= n <= WAYPOINTS_MAX:
        raise ValueError(f"a waypoint sweep needs {WAYPOINTS_MIN}-{WAYPOINTS_MAX} waypoints, got {n}")
    for i, w in enumerate(waypoints_uv):
        _check_v(w, f"waypoint {i}")
    if isinstance(cycles, bool) or not isinstance(cycles, int) or not 1 <= cycles <= CYCLES_MAX:
        raise ValueError(f"cycles must be an integer 1-{CYCLES_MAX}, got {cycles!r}")
    if cycles > 1 and waypoints_uv[-1] != waypoints_uv[0]:
        raise ValueError("cycles > 1 needs the last waypoint equal to the first")
    if step_uv < STEP_MIN_UV:
        raise ValueError(f"step {uv_to_volts(step_uv)} V is below the 0.5 mV minimum")
    total = 0
    for i, (a, b) in enumerate(zip(waypoints_uv, waypoints_uv[1:]), start=1):
        seg = abs(b - a)
        if seg == 0:
            raise ValueError(f"segment {i} has zero length (waypoints {i - 1} and {i} are equal)")
        if seg % step_uv:
            s = suggest_step_uv(waypoints_uv, step_uv)
            raise GridError(
                f"segment {i} ({uv_to_volts(a)} V -> {uv_to_volts(b)} V) is not an integer "
                f"multiple of step {uv_to_volts(step_uv)} V",
                None if s is None else uv_to_volts(s))
        total += seg // step_uv
    points = 1 + cycles * total
    if points > POINTS_MAX:
        raise ValueError(f"{points} points exceed the {POINTS_MAX} maximum")
    return points


def waypoint_sweep(waypoints: Iterable[Any], step: Any, cycles: int = 1) -> WaypointSweep:
    """A waypoint sweep from volts (int, Decimal, exact float or str)."""
    wps = tuple(to_uv(w, f"waypoint {i}") for i, w in enumerate(waypoints))
    step_uv = to_uv(step, "step")
    if isinstance(cycles, float) and cycles.is_integer():
        cycles = int(cycles)
    return WaypointSweep(wps, step_uv, cycles)


def linear(v0: Any, v1: Any, step: Any) -> WaypointSweep:
    """``v0`` -> ``v1`` in steps of ``step`` (both ends measured)."""
    return waypoint_sweep([v0, v1], step)


def dual(v0: Any, v1: Any, step: Any) -> WaypointSweep:
    """``v0`` -> ``v1`` -> ``v0``; the down branch repeats the up branch's setpoints."""
    return waypoint_sweep([v0, v1, v0], step)


def hysteresis_loop(vmax: Any, step: Any, cycles: int = 1) -> WaypointSweep:
    """Bipolar loop 0 -> +vmax -> -vmax -> 0, repeated ``cycles`` times."""
    vmax_uv = to_uv(vmax, "vmax")
    if vmax_uv <= 0:
        raise ValueError("vmax must be positive")
    vm = uv_to_volts(vmax_uv)
    return waypoint_sweep([Decimal(0), vm, -vm, Decimal(0)], step, cycles)


# ---- list sweeps ---------------------------------------------------------------

class ListPoint(NamedTuple):
    """A list-sweep point: setpoint (V) and optional own settle time (us).

    ``settle_us=None`` (omitted) means "use the configuration's ``settle_us``"
    (protocol.md section 5.5): the point is sent as plain ``V``. An explicit
    settle time must be 100-60000000 us; ``0`` is not a way to omit it.
    """

    v: Decimal
    settle_us: int | None = None

    def token(self) -> str:
        """``V`` or ``V:settle_us`` as sent in ``SWP.LIST.ADD``."""
        uv = to_uv(self.v)
        _check_v(uv, "list point")
        s = scaled_to_str(uv, VOLT_DECIMALS)
        if self.settle_us is None:
            return s
        settle = int(format_us(self.settle_us, "settle_us"))
        if not SETTLE_MIN_US <= settle <= SETTLE_MAX_US:
            raise ValueError(f"settle_us {settle} outside {SETTLE_MIN_US}-{SETTLE_MAX_US}")
        return f"{s}:{settle}"


def as_list_point(p: Any) -> ListPoint:
    """Accept ``ListPoint``, ``(v, settle_us)`` / ``[v, settle_us]`` (``settle_us``
    may be ``None``), ``(v,)`` / ``[v]`` or a bare voltage. A missing or
    ``None`` settle time means the configuration's ``settle_us``."""
    if isinstance(p, ListPoint):
        return ListPoint(uv_to_volts(to_uv(p.v)), p.settle_us)
    if isinstance(p, (tuple, list)):
        if not 1 <= len(p) <= 2:
            raise ValueError(f"list point must be (v, settle_us) or (v,), got {p!r}")
        settle = p[1] if len(p) == 2 else None
        return ListPoint(uv_to_volts(to_uv(p[0])),
                         None if settle is None else int(format_us(settle, "settle_us")))
    return ListPoint(uv_to_volts(to_uv(p)))


def list_add_commands(points: Iterable[Any], chunk: int = LIST_ADD_MAX) -> list[str]:
    """``SWP.LIST.ADD`` lines (at most ``chunk`` <= 8 points each)."""
    if not 1 <= chunk <= LIST_ADD_MAX:
        raise ValueError(f"chunk must be 1-{LIST_ADD_MAX}")
    toks = [as_list_point(p).token() for p in points]
    if not toks:
        raise ValueError("a list sweep needs at least one point")
    if len(toks) > LIST_MAX:
        raise ValueError(f"{len(toks)} points exceed the list maximum of {LIST_MAX}")
    return [build_command("SWP.LIST.ADD", *toks[i:i + chunk]) for i in range(0, len(toks), chunk)]


def _parse_polarity(polarity: Any) -> str:
    p = str(polarity).strip().lower()
    if p in ("+", "+1", "1", "pos", "positive"):
        return "+"
    if p in ("-", "-1", "neg", "negative"):
        return "-"
    if p in ("both", "+-", "-+", "bipolar"):
        return "both"
    raise ValueError(f"polarity must be '+', '-' or 'both', got {polarity!r}")


def log_list(v_start: Any, v_stop: Any, points_per_decade: int, polarity: Any = "+") -> list[Decimal]:
    """Logarithmically spaced magnitudes from ``|v_start|`` to ``|v_stop|``.

    ``points_per_decade`` points per factor of 10, both ends included exactly,
    each value rounded (half-even) to the 1 uV grid, and values that coincide
    after rounding kept once. ``polarity``: ``'+'`` (positive values, in the
    order start -> stop), ``'-'`` (the same magnitudes negated), or ``'both'``
    (monotonically increasing voltage: ``-|stop| ... -|start|, +|start| ...
    +|stop|`` for ``|start| < |stop|``).
    """
    a_uv = abs(to_uv(v_start, "v_start"))
    b_uv = abs(to_uv(v_stop, "v_stop"))
    if a_uv == 0 or b_uv == 0:
        raise ValueError("log spacing needs non-zero start and stop voltages")
    if a_uv == b_uv:
        raise ValueError("start and stop magnitudes are equal")
    _check_v(a_uv, "v_start")
    _check_v(b_uv, "v_stop")
    if isinstance(points_per_decade, bool) or not isinstance(points_per_decade, int) \
            or points_per_decade < 1:
        raise ValueError("points_per_decade must be a positive integer")
    pol = _parse_polarity(polarity)
    ascending = b_uv > a_uv
    mags: list[int] = []
    with localcontext() as ctx:
        ctx.prec = 40
        start = Decimal(a_uv)
        k = 0
        while True:
            exp = Decimal(k) / Decimal(points_per_decade)
            factor = Decimal(10) ** (exp if ascending else -exp)
            uv = int((start * factor).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
            if (ascending and uv >= b_uv) or (not ascending and uv <= b_uv):
                break
            if not mags or uv != mags[-1]:
                mags.append(uv)
            k += 1
            if len(mags) > LIST_MAX:
                raise ValueError(f"more than {LIST_MAX} points; use fewer points per decade")
    if mags and mags[-1] == b_uv:
        mags.pop()
    mags.append(b_uv)
    if pol == "+":
        out = mags
    elif pol == "-":
        out = [-m for m in mags]
    else:
        inc = sorted(mags)
        out = [-m for m in reversed(inc)] + inc
    if len(out) > LIST_MAX:
        raise ValueError(f"{len(out)} points exceed the list maximum of {LIST_MAX}")
    return [uv_to_volts(v) for v in out]


__all__ = [
    "GridError", "to_uv", "uv_to_volts", "suggest_step_uv", "SweepPoint",
    "WaypointSweep", "validate_waypoints", "waypoint_sweep", "linear", "dual",
    "hysteresis_loop", "ListPoint", "as_list_point", "list_add_commands",
    "log_list", "STEP_MIN_UV", "WAYPOINTS_MAX", "CYCLES_MAX", "POINTS_MAX",
    "LIST_MAX", "LIST_ADD_MAX", "V_MAX_UV",
]
