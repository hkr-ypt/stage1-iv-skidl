"""Calibration: coefficient handling, least-squares fits and the guided
workflow used by ``iv-cal`` (docs/firmware/firmware_spec.md section 7.10).

Applied model (firmware):

=================  ===========================  =========================
Quantity           Model                        Nominal
=================  ===========================  =========================
Setpoint           V_set = g_dac (code - c_dac)  306.40 uV/code, 32767.5
DUT voltage        V_K = g_v (code - o_v)        1.49859 uV/code
DUT current        I = g_i (code - o_i)          12.4356 nA/code
Series resistance  R_s, R_top                    121 ohm, 106 ohm
Plateau            i_plateau                     0.049 A (until measured)
=================  ===========================  =========================

(``o_v``/``o_i`` are the session zero from ``ZERO``, not part of the stored
record.) The fits are plain-Python ordinary least squares (no numpy): data
are centred and summed with :func:`math.fsum`.

``CAL.SET`` values are exact decimals at fixed resolutions
(:data:`CAL_DECIMALS`); the firmware rejects finer values as inexact, so fit
results are rounded explicitly (:func:`round_cal`) before they are sent.

The host-side plausibility check (:func:`check_plausible`) mirrors the
firmware window in calib.h; the firmware remains authoritative
(``ERR RANGE``).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping, Sequence

from .protocol import convert_value, scaled_to_str, to_scaled

if TYPE_CHECKING:  # pragma: no cover
    from .client import Instrument, ZeroResult

# ---- component values (mirror of firmware/src/core/nominal.h) -----------------------

R3_OHM, R4_OHM = 2490.0, 20000.0           # sweep level shift, blocks/dac_sweep.py
R12_OHM, R32_OHM = 40200.0, 10000.0        # Kelvin attenuator, blocks/voltage_sense.py
R10_OHM, R11_OHM = 10.0, 35700.0           # shunt and INA128 gain resistor
INA_RG_INTERNAL_OHM = 50000.0
R15_OHM = 100.0                            # ADC input filter
ADS_ZEFF_PGA2_OHM = 59600.0                # SBAS288K Table 10
R30_OHM, PTC_OHM, U12_RON_OHM = 100.0, 5.0, 1.0
VREF_V = 2.5
ADS_FULLSCALE_CODE = 8388607.0
DAC_CODES = 65536.0

#: R_sd = R12 + R32, the SENSE+ attenuator (firmware_spec.md section 7.7).
R_SD_OHM = R12_OHM + R32_OHM

_ADC_LSB_V = VREF_V / ADS_FULLSCALE_CODE
_BUF_OFF_GAIN = 1.0 + R15_OHM / ADS_ZEFF_PGA2_OHM

#: Nominal coefficients in CAL? units, computed like the firmware does.
NOMINAL: dict[str, float] = {
    "g_dac_uv": R4_OHM / R3_OHM * VREF_V / DAC_CODES * 1e6,                  # 306.40
    "c_dac": (DAC_CODES - 1.0) / 2.0,                                       # 32767.5
    "g_v_uv": _ADC_LSB_V / (R32_OHM / (R12_OHM + R32_OHM)) * _BUF_OFF_GAIN * 1e6,  # 1.49859
    "g_i_na": _ADC_LSB_V / (R10_OHM * (1.0 + INA_RG_INTERNAL_OHM / R11_OHM))
    * _BUF_OFF_GAIN * 1e9,                                                    # 12.4356
    "r_s": (PTC_OHM + R30_OHM + U12_RON_OHM) + (PTC_OHM + R10_OHM),           # 121
    "r_top": PTC_OHM + R30_OHM + U12_RON_OHM,                                # 106
    "i_plateau": 0.049,
}

#: Keys of CAL? / CAL.SET, in protocol order, and their units.
CAL_KEYS = ("g_dac_uv", "c_dac", "g_v_uv", "g_i_na", "r_s", "r_top", "i_plateau")
CAL_UNITS = {"g_dac_uv": "uV/code", "c_dac": "code", "g_v_uv": "uV/code",
             "g_i_na": "nA/code", "r_s": "ohm", "r_top": "ohm", "i_plateau": "A"}
#: Decimal places at which CAL.SET values are exact (finer is rejected).
CAL_DECIMALS = {"g_dac_uv": 6, "c_dac": 4, "g_v_uv": 6, "g_i_na": 6,
                "r_s": 4, "r_top": 4, "i_plateau": 9}

#: DAC8552 specified linear range (codes) for the DAC fit.
DAC_LINEAR_MIN, DAC_LINEAR_MAX = 485, 64741

#: Series-resistance fit: >= 3 load currents of each polarity with |I| >= 5 mA.
RS_MIN_CURRENT_A = 0.005
RS_MIN_POINTS_PER_POLARITY = 3


# ---- coefficient formatting ------------------------------------------------------------

def format_cal_value(key: str, value: Any) -> str:
    """Exact ``CAL.SET`` text of one coefficient (ValueError if inexact)."""
    if key not in CAL_DECIMALS:
        raise ValueError(f"unknown calibration key {key!r}")
    d = CAL_DECIMALS[key]
    return scaled_to_str(to_scaled(value, d, key), d)


def format_cal_args(**kwargs: Any) -> list[str]:
    """``key=value`` tokens for ``CAL.SET`` in protocol order."""
    if not kwargs:
        raise ValueError("CAL.SET needs at least one key=value")
    unknown = set(kwargs) - set(CAL_KEYS)
    if unknown:
        raise ValueError(f"unknown calibration keys {sorted(unknown)}")
    return [f"{k}={format_cal_value(k, kwargs[k])}" for k in CAL_KEYS if k in kwargs]


def round_cal(**kwargs: Any) -> dict[str, Decimal]:
    """Round fit results (float/Decimal) half-even to :data:`CAL_DECIMALS`."""
    out = {}
    for k, v in kwargs.items():
        if k not in CAL_DECIMALS:
            raise ValueError(f"unknown calibration key {k!r}")
        if isinstance(v, float):
            if not math.isfinite(v):
                raise ValueError(f"{k}: {v!r} is not finite")
            v = Decimal(repr(v))
        out[k] = Decimal(v).quantize(Decimal(1).scaleb(-CAL_DECIMALS[k]),
                                     rounding=ROUND_HALF_EVEN)
    return out


@dataclass(frozen=True)
class CalInfo:
    """``CAL?``: source, record sequence and the coefficients (Decimal)."""

    source: str | None
    seq: int | None
    coeffs: dict[str, Decimal]
    fields: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_fields(cls, fields: Mapping[str, str]) -> "CalInfo":
        coeffs = {}
        for k in CAL_KEYS:
            if k in fields:
                v = convert_value(k, fields[k])
                coeffs[k] = Decimal(v) if isinstance(v, (int, Decimal)) else v
        seq = convert_value("seq", fields["seq"]) if "seq" in fields else None
        return cls(fields.get("source"), seq if isinstance(seq, int) else None, coeffs,
                   dict(fields))


# ---- plausibility (calib.h) ------------------------------------------------------------

#: Half-width of the firmware's c_dac window (calib.c C_DAC_WINDOW_CODES):
#: the DAC8552 zero-code, gain and channel-B full-scale errors move the zero
#: by up to about 580 codes on an in-spec board.
C_DAC_WINDOW_CODES = 1000.0


def check_plausible(coeffs: Mapping[str, Any]) -> list[str]:
    """Problems with the given coefficients (CAL? units); empty if plausible.

    Window (firmware calib.h): g_dac +-2 %, c_dac +-1000 codes (the DAC8552
    worst case is about 580), g_v and g_i +-5 %, r_s 80..200 ohm, r_top
    70..180 ohm, r_top < r_s, i_plateau 20..100 mA. Keys that are absent are
    not checked.
    """
    c = {k: float(v) for k, v in coeffs.items() if k in CAL_KEYS}
    problems = []

    def rel(key: str, tol: float) -> None:
        if key in c:
            nom = NOMINAL[key]
            if not abs(c[key] - nom) <= tol * nom:
                problems.append(f"{key}={c[key]:g} is outside nominal {nom:.6g} +-{tol:.0%}")

    def window(key: str, lo: float, hi: float) -> None:
        if key in c and not lo <= c[key] <= hi:
            problems.append(f"{key}={c[key]:g} is outside {lo:g}..{hi:g}")

    rel("g_dac_uv", 0.02)
    if "c_dac" in c and not abs(c["c_dac"] - NOMINAL["c_dac"]) <= C_DAC_WINDOW_CODES:
        problems.append(f"c_dac={c['c_dac']:g} is outside {NOMINAL['c_dac']:g} "
                        f"+-{C_DAC_WINDOW_CODES:g} codes")
    rel("g_v_uv", 0.05)
    rel("g_i_na", 0.05)
    window("r_s", 80.0, 200.0)
    window("r_top", 70.0, 180.0)
    if "r_s" in c and "r_top" in c and not c["r_top"] < c["r_s"]:
        problems.append(f"r_top={c['r_top']:g} must be below r_s={c['r_s']:g}")
    window("i_plateau", 0.020, 0.100)
    return problems


def is_plausible(coeffs: Mapping[str, Any]) -> bool:
    return not check_plausible(coeffs)


# ---- least squares -------------------------------------------------------------------------

@dataclass(frozen=True)
class LinearFit:
    """``y = slope * x + intercept`` by ordinary least squares."""

    slope: float
    intercept: float
    residuals: tuple[float, ...]
    n: int

    @property
    def rms(self) -> float:
        return math.sqrt(math.fsum(r * r for r in self.residuals) / self.n) if self.n else 0.0

    @property
    def max_abs_residual(self) -> float:
        return max((abs(r) for r in self.residuals), default=0.0)


def linear_fit(x: Sequence[Any], y: Sequence[Any]) -> LinearFit:
    """Ordinary least squares on centred data (numerically stable, no numpy)."""
    xs = [float(v) for v in x]
    ys = [float(v) for v in y]
    if len(xs) != len(ys):
        raise ValueError(f"x has {len(xs)} values, y has {len(ys)}")
    n = len(xs)
    if n < 2:
        raise ValueError("a line fit needs at least 2 points")
    if not all(math.isfinite(v) for v in xs + ys):
        raise ValueError("non-finite value in fit data")
    mx = math.fsum(xs) / n
    my = math.fsum(ys) / n
    sxx = math.fsum((a - mx) ** 2 for a in xs)
    if sxx == 0.0:
        raise ValueError("all x values are equal; the slope is undefined")
    sxy = math.fsum((a - mx) * (b - my) for a, b in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    res = tuple(b - (slope * a + intercept) for a, b in zip(xs, ys))
    return LinearFit(slope, intercept, res, n)


@dataclass(frozen=True)
class GainOffsetFit:
    """``reference = gain * (code - offset)``; gain in SI units per code."""

    gain: float
    offset: float
    fit: LinearFit

    @property
    def rms(self) -> float:
        return self.fit.rms

    @property
    def gain_uv(self) -> float:
        """Gain in uV/code (the V channel's ``g_v_uv``)."""
        return self.gain * 1e6

    @property
    def gain_na(self) -> float:
        """Gain in nA/code (the I channel's ``g_i_na``)."""
        return self.gain * 1e9

    def __iter__(self) -> Iterator[float]:
        yield self.gain
        yield self.offset


def fit_gain_offset(raw_codes: Sequence[Any], reference_values: Sequence[Any]) -> GainOffsetFit:
    """Fit an ADC channel: ``reference = gain * (code - offset)``.

    ``raw_codes`` are mean raw ADC codes; ``reference_values`` the true
    values in volts (V channel, from the DMM) or amperes (I channel, DMM
    voltage / reference resistance). Use several points of each polarity.
    """
    lf = linear_fit(raw_codes, reference_values)
    if lf.slope == 0.0:
        raise ValueError("zero gain")
    return GainOffsetFit(lf.slope, -lf.intercept / lf.slope, lf)


@dataclass(frozen=True)
class DacFit:
    """``V_SWEEP = g_dac * (code - c_dac)``; ``g_dac`` in V/code."""

    g_dac: float
    c_dac: float
    fit: LinearFit

    @property
    def g_dac_uv(self) -> float:
        return self.g_dac * 1e6

    @property
    def rms(self) -> float:
        return self.fit.rms

    def __iter__(self) -> Iterator[float]:
        yield self.g_dac
        yield self.c_dac


def vsweep_from_vk(v_k: float, r_top: float = NOMINAL["r_top"], r_sd: float = R_SD_OHM) -> float:
    """V_SWEEP from the Kelvin reading with the DUT open and the Kelvin leads
    on the FORCE terminals: ``V_K * (1 + R_top/R_sd)`` (the attenuator
    current drops 0.21 % across R_top)."""
    return float(v_k) * (1.0 + float(r_top) / float(r_sd))


def fit_dac(codes: Sequence[int], measured_vsweep: Sequence[Any],
            check_range: bool = True) -> DacFit:
    """Fit the setpoint model from DAC codes and measured V_SWEEP (volts).

    Only codes in the DAC8552's specified linear range (485..64741) are
    allowed when ``check_range``.
    """
    if check_range:
        bad = [c for c in codes if not DAC_LINEAR_MIN <= int(c) <= DAC_LINEAR_MAX]
        if bad:
            raise ValueError(f"codes outside the linear range {DAC_LINEAR_MIN}..{DAC_LINEAR_MAX}: "
                             f"{bad[:5]}")
    lf = linear_fit(codes, measured_vsweep)
    if lf.slope == 0.0:
        raise ValueError("zero DAC gain")
    return DacFit(lf.slope, -lf.intercept / lf.slope, lf)


@dataclass(frozen=True)
class SeriesResistanceFit:
    """``y = a + R_s * I`` with ``y = V_set - V_K (1 + R_top/R_sd)``."""

    r_s: float
    a: float
    fit: LinearFit

    @property
    def rms(self) -> float:
        return self.fit.rms

    def __iter__(self) -> Iterator[float]:
        yield self.r_s
        yield self.a


def fit_series_resistance(v_set: Sequence[Any], v_k: Sequence[Any], i: Sequence[Any],
                          r_top: float = NOMINAL["r_top"], r_sd: float = R_SD_OHM,
                          require_spread: bool = True) -> SeriesResistanceFit:
    """Least squares for R_s (firmware_spec.md section 7.10).

    With ``require_spread`` the data must contain at least 3 distinct load
    currents of each polarity with ``|I| >= 5 mA``.
    """
    vs = [float(v) for v in v_set]
    vk = [float(v) for v in v_k]
    cur = [float(v) for v in i]
    if not len(vs) == len(vk) == len(cur):
        raise ValueError("v_set, v_k and i must have the same length")
    if require_spread:
        pos = {round(c, 9) for c in cur if c >= RS_MIN_CURRENT_A}
        neg = {round(c, 9) for c in cur if c <= -RS_MIN_CURRENT_A}
        if len(pos) < RS_MIN_POINTS_PER_POLARITY or len(neg) < RS_MIN_POINTS_PER_POLARITY:
            raise ValueError(
                f"R_s fit needs >= {RS_MIN_POINTS_PER_POLARITY} load currents of each polarity "
                f"with |I| >= {RS_MIN_CURRENT_A * 1e3:g} mA (have {len(pos)} positive, "
                f"{len(neg)} negative)")
    k = 1.0 + float(r_top) / float(r_sd)
    y = [a - b * k for a, b in zip(vs, vk)]
    lf = linear_fit(cur, y)
    return SeriesResistanceFit(lf.slope, lf.intercept, lf)


# ---- guided workflow (iv-cal) -----------------------------------------------------------------

Ask = Callable[[str], str]
Say = Callable[[str], None]


def ask_float(ask: Ask, prompt: str, *, allow_empty: bool = False) -> float | None:
    """Prompt until the answer parses as a finite number."""
    while True:
        text = ask(prompt).strip()
        if not text and allow_empty:
            return None
        try:
            v = float(text)
        except ValueError:
            continue
        if math.isfinite(v):
            return v


def ask_yes(ask: Ask, prompt: str) -> bool:
    return ask(prompt).strip().lower() in ("y", "yes")


def session_zero(begin_fields: Mapping[str, str] | None) -> tuple[float, float] | None:
    """``(o_v, o_i)`` in codes of the session zero the firmware applied to a
    sweep, from its ``EVT SWEEP_BEGIN`` fields, if ``zero=valid``; else None
    (``stale``: older than the firmware's limit, from an earlier session or
    another ADC rate; ``none``: never measured)."""
    if not begin_fields or begin_fields.get("zero") != "valid":
        return None
    try:
        return float(Decimal(begin_fields["o_v"])), float(Decimal(begin_fields["o_i"]))
    except (KeyError, ArithmeticError, ValueError):
        return None


@dataclass
class CalibrationWorkflow:
    """Guided calibration (``iv-cal``): ZERO, ADC gains with a reference
    resistor and a DMM, the DAC with the DUT open, R_s with a load, then
    CAL.SET (after confirmation) and CAL.SAVE.

    ``ask``/``say`` default to :func:`input`/:func:`print`; tests inject
    their own. Prompts answered while the output is armed (the DMM readings
    of the gains step) run inside :meth:`Instrument.hold`, so a slow
    operator is not disarmed by the host timeout.

    The dac and rs steps reprocess raw codes and need the ADC offsets: from
    the zero step of this run, else from the gains fit, else the session zero
    the firmware reports in the sweep's ``SWEEP_BEGIN`` (``zero=valid``,
    e.g. after :meth:`Instrument.zero` in this session). Without any of them
    the step refuses to run (offset 0 would bias ``c_dac`` by the V offset).
    """

    inst: "Instrument"
    ask: Ask = input
    say: Say = print
    n_avg: int = 16
    zero_result: "ZeroResult | None" = None
    v_fit: GainOffsetFit | None = None
    i_fit: GainOffsetFit | None = None
    dac_fit: DacFit | None = None
    rs_fit: SeriesResistanceFit | None = None
    current: dict[str, Decimal] = field(default_factory=dict)

    def load_current(self) -> dict[str, Decimal]:
        self.current = dict(self.inst.cal_get().coeffs)
        return self.current

    def _r_top(self) -> float:
        return float(self.current.get("r_top", NOMINAL["r_top"]))

    def _have_offsets(self) -> bool:
        return self.zero_result is not None or (self.v_fit is not None and self.i_fit is not None)

    def _require_offsets(self) -> None:
        """Before measuring: refuse early if no ADC offsets will be available."""
        if self._have_offsets():
            return
        zero = self.inst.stat().get("zero")
        if zero != "valid":
            raise ValueError(
                f"no ADC offsets: run the zero step first (e.g. --steps zero,dac) or the gains "
                f"step in the same run; the instrument's session zero is {zero!r}, not 'valid'")

    def _offsets(self, begin_fields: Mapping[str, str] | None = None) -> tuple[float, float]:
        """``(o_v, o_i)`` in codes: the zero step, else the gains fits, else the
        session zero of ``begin_fields`` (a sweep's SWEEP_BEGIN); ValueError if
        none is available (never a silent 0)."""
        if self.zero_result is not None:
            return float(self.zero_result.o_v), float(self.zero_result.o_i)
        o_v = self.v_fit.offset if self.v_fit else None
        o_i = self.i_fit.offset if self.i_fit else None
        if o_v is None or o_i is None:
            sz = session_zero(begin_fields)
            if sz is None:
                zero = (begin_fields or {}).get("zero", "not reported")
                raise ValueError(
                    f"no ADC offsets: run the zero step first; the sweep's session zero is "
                    f"{zero!r}, not 'valid'")
            o_v = sz[0] if o_v is None else o_v
            o_i = sz[1] if o_i is None else o_i
        return o_v, o_i

    def _g_v(self) -> float:
        if self.v_fit:
            return self.v_fit.gain
        return float(self.current.get("g_v_uv", NOMINAL["g_v_uv"])) * 1e-6

    def _g_i(self) -> float:
        if self.i_fit:
            return self.i_fit.gain
        return float(self.current.get("g_i_na", NOMINAL["g_i_na"])) * 1e-9

    def step_zero(self) -> "ZeroResult":
        self.say("ZERO: remove the DUT; all four jacks must be open.")
        self.ask("Press Enter when ready ")
        self.zero_result = self.inst.zero()
        self.say(f"  o_v={self.zero_result.o_v} o_i={self.zero_result.o_i} codes "
                 f"(sd {self.zero_result.sd_v}/{self.zero_result.sd_i})")
        return self.zero_result

    def step_gains(self, r_ref: float, setpoints: Sequence[Any] = (-8, -4, -2, 2, 4, 8),
                   i_max: float = 0.040) -> tuple[GainOffsetFit, GainOffsetFit]:
        """Reference resistor ``r_ref`` (0.01 %), 4-wire, Kelvin leads on its
        terminals; the DMM reads the voltage across it at each setpoint."""
        self.say(f"GAINS: connect the {r_ref:g} ohm reference resistor 4-wire "
                 "(Kelvin leads on its terminals) and the DMM across it.")
        self.ask("Press Enter when ready ")
        usable = [v for v in setpoints if abs(float(v)) / r_ref <= i_max]
        if len(usable) < 4:
            raise ValueError("too few setpoints keep the current below "
                             f"{i_max * 1e3:g} mA with this resistor")
        v_codes, v_ref, i_codes, i_ref = [], [], [], []
        self.inst.arm()
        try:
            for v in usable:
                self.inst.src(v)
                m = self.inst.meas(self.n_avg)
                with self.inst.hold():      # armed while the operator reads the DMM
                    dmm = ask_float(self.ask,
                                    f"  setpoint {v} V: DMM voltage across the resistor [V]: ")
                assert dmm is not None
                v_codes.append(m.v_raw)
                v_ref.append(dmm)
                i_codes.append(m.i_raw)
                i_ref.append(dmm / r_ref)
        finally:
            self.inst.disarm()
        self.v_fit = fit_gain_offset(v_codes, v_ref)
        self.i_fit = fit_gain_offset(i_codes, i_ref)
        self.say(f"  g_v = {self.v_fit.gain_uv:.6f} uV/code (rms {self.v_fit.rms * 1e6:.1f} uV)")
        self.say(f"  g_i = {self.i_fit.gain_na:.6f} nA/code (rms {self.i_fit.rms * 1e9:.1f} nA)")
        return self.v_fit, self.i_fit

    def _sweep(self, v_max: Any, step: Any):
        """A dual sweep -v_max..+v_max..-v_max -> :class:`SweepResult`."""
        from .sweeps import dual
        self.inst.sweep_waypoints(dual(-abs(Decimal(str(v_max))), abs(Decimal(str(v_max))), step))
        self.inst.arm()
        try:
            res = self.inst.run_sweep()
        finally:
            self.inst.disarm()
        return res

    def _sweep_records(self, v_max: Any, step: Any):
        return self._sweep(v_max, step).records

    def step_dac(self, v_max: Any = 9, step: Any = 1) -> DacFit:
        """DUT open, Kelvin leads on the FORCE terminals (I = 0)."""
        self._require_offsets()
        self.say("DAC: remove the DUT and clip the Kelvin leads onto the FORCE terminals.")
        self.ask("Press Enter when ready ")
        res = self._sweep(v_max, step)
        recs = res.records
        o_v, _ = self._offsets(res.begin_fields)
        g_v = self._g_v()
        codes = [r.dac_code for r in recs]
        vsw = [vsweep_from_vk(g_v * (r.v_raw - o_v), self._r_top()) for r in recs]
        self.dac_fit = fit_dac(codes, vsw)
        self.say(f"  g_dac = {self.dac_fit.g_dac_uv:.6f} uV/code, c_dac = {self.dac_fit.c_dac:.4f}")
        return self.dac_fit

    def step_series_resistance(self, v_max: Any = 9, step: Any = 1) -> SeriesResistanceFit:
        """A load drawing >= 5 mA of each polarity at the higher setpoints."""
        self._require_offsets()
        self.say("R_s: connect a load resistor (e.g. 470 ohm) with Kelvin leads on it.")
        self.ask("Press Enter when ready ")
        res = self._sweep(v_max, step)
        recs = res.records
        o_v, o_i = self._offsets(res.begin_fields)
        g_v, g_i = self._g_v(), self._g_i()
        if self.dac_fit is not None:
            g_dac, c_dac = self.dac_fit.g_dac, self.dac_fit.c_dac
        else:
            g_dac = float(self.current.get("g_dac_uv", NOMINAL["g_dac_uv"])) * 1e-6
            c_dac = float(self.current.get("c_dac", NOMINAL["c_dac"]))
        v_set, v_k, cur = [], [], []
        for r in recs:
            i = g_i * (r.i_raw - o_i)
            if abs(i) < RS_MIN_CURRENT_A or not r.valid_data:
                continue
            v_set.append(g_dac * (r.dac_code - c_dac))
            v_k.append(g_v * (r.v_raw - o_v))
            cur.append(i)
        self.rs_fit = fit_series_resistance(v_set, v_k, cur, self._r_top())
        self.say(f"  R_s = {self.rs_fit.r_s:.4f} ohm (intercept {self.rs_fit.a * 1e3:.3f} mV)")
        return self.rs_fit

    def proposal(self) -> dict[str, Decimal]:
        """The new coefficients, rounded to the CAL.SET resolutions."""
        new: dict[str, Any] = {}
        if self.dac_fit:
            new["g_dac_uv"] = self.dac_fit.g_dac_uv
            new["c_dac"] = self.dac_fit.c_dac
        if self.v_fit:
            new["g_v_uv"] = self.v_fit.gain_uv
        if self.i_fit:
            new["g_i_na"] = self.i_fit.gain_na
        if self.rs_fit:
            new["r_s"] = self.rs_fit.r_s
        return round_cal(**new)

    def apply(self, coeffs: Mapping[str, Any] | None = None, note: str | None = None,
              ts: int | None = None) -> int | None:
        """Print the CAL.SET line, ask for confirmation, send it, then CAL.SAVE.
        Returns the saved record's seq, or None if declined."""
        from .protocol import build_command
        coeffs = dict(self.proposal() if coeffs is None else coeffs)
        if not coeffs:
            raise ValueError("nothing to apply")
        merged = {**{k: v for k, v in self.current.items() if k in CAL_KEYS}, **coeffs}
        problems = check_plausible(merged)
        if problems:
            self.say("NOT plausible (the firmware would reject it):")
            for p in problems:
                self.say(f"  - {p}")
            return None
        line = build_command("CAL.SET", *format_cal_args(**coeffs))
        self.say("Command to send:")
        self.say(f"  {line}")
        if not ask_yes(self.ask, "Send CAL.SET and save to flash? [y/N] "):
            self.say("Not applied.")
            return None
        self.inst.cal_set(**coeffs)
        seq = self.inst.cal_save(ts=int(time.time()) if ts is None else ts, note=note)
        self.say(f"Saved calibration record seq={seq}.")
        return seq


__all__ = [
    "NOMINAL", "CAL_KEYS", "CAL_UNITS", "CAL_DECIMALS", "R_SD_OHM", "format_cal_value",
    "format_cal_args", "round_cal", "CalInfo", "check_plausible", "is_plausible",
    "LinearFit", "linear_fit", "GainOffsetFit", "fit_gain_offset", "DacFit",
    "vsweep_from_vk", "fit_dac", "SeriesResistanceFit", "fit_series_resistance",
    "CalibrationWorkflow", "session_zero", "DAC_LINEAR_MIN", "DAC_LINEAR_MAX",
]
