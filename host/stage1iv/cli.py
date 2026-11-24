"""Command-line tools: ``iv-diag``, ``iv-sweep``, ``iv-cal``.

Device selection (all tools): ``--port <name>`` or discovery by USB VID/PID
(``--pid``, default the development PID 0x0001 or ``$STAGE1IV_PID``) and
optionally ``--serial <24 hex>``. A fixed device path is never assumed.
``--sim [EXE]`` runs against the firmware simulator instead (testing).

Errors (no instrument found, a bad sweep definition or configuration value,
a refused command, a lost session, ...) are reported in one line on stderr,
never as a traceback. Exit status: 0 success, 1 error, 2 sweep not complete,
3 calibration not saved, 130 interrupted. ``iv-sweep`` writes whatever it
collected (``status=unknown`` if the sweep did not end) also when the sweep
or the final DISARM fails.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import tempfile
import tomllib
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .calibration import CalibrationWorkflow
from .client import Instrument, SweepResult
from .discovery import DeviceNotFound
from .errors import Stage1IVError
from .records import decimal_text
from .sim import SimulatorError
from .sweeps import ListPoint, as_list_point, list_add_commands, waypoint_sweep
from .writers import sweep_paths, write_sweep

log = logging.getLogger(__name__)

#: Errors the tools report in one line (exit status 1) instead of a traceback:
#: library errors, no/ambiguous instrument, simulator problems, invalid values
#: (ValueError includes GridError and JSON/TOML syntax errors), decimal
#: syntax, and OS errors (serial port, files).
CLI_ERRORS = (Stage1IVError, DeviceNotFound, SimulatorError, ValueError, ArithmeticError,
              OSError)


def _add_device_args(ap: argparse.ArgumentParser) -> None:
    g = ap.add_argument_group("device")
    g.add_argument("--port", help="serial port name (otherwise discovered by VID/PID)")
    g.add_argument("--serial", help="instrument serial (24 hex characters)")
    g.add_argument("--pid", help="USB PID (default 0x0001 or $STAGE1IV_PID)")
    g.add_argument("--sim", nargs="?", const="", metavar="EXE",
                   help="use the firmware simulator (optionally its path)")
    g.add_argument("-v", "--verbose", action="store_true")


def _check_writable(targets: Iterable[Path]) -> None:
    """Raise OSError unless each file can be written: an existing one must be
    a writable file, a new one is created and removed again (this also
    catches a missing directory and a name the file system refuses)."""
    for target in targets:
        if target.exists():
            if not target.is_file():
                raise IsADirectoryError(f"{target} is a directory, not a file")
            if not os.access(target, os.W_OK):
                raise PermissionError(f"cannot write {target}")
        else:
            with open(target, "x"):
                pass
            target.unlink()


@contextlib.contextmanager
def _open(args: argparse.Namespace, ready: bool = True, **kwargs: Any) -> Iterator[Instrument]:
    """Open the instrument; with ``ready`` wait until it has left BOOT."""
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if args.sim is not None:
        from .sim import SimulatorProcess
        with SimulatorProcess(args.sim or None) as sim:
            with sim.instrument(**kwargs) as inst:
                if ready:
                    inst.wait_ready()       # the simulator starts in BOOT
                yield inst
        return
    with Instrument.open_serial(args.port, serial=args.serial, pid=args.pid, **kwargs) as inst:
        if ready:
            inst.wait_ready()               # POST after power-up
        yield inst


def _print_fields(title: str, fields: dict[str, Any]) -> None:
    print(f"[{title}]")
    for k, v in fields.items():
        print(f"  {k:20s} {v}")


def _text(value: Any) -> str:
    """A converted value as text; Decimals keep their fixed decimals (never
    scientific notation)."""
    return decimal_text(value) if isinstance(value, Decimal) else str(value)


# ---- iv-diag ------------------------------------------------------------------------

def diag_main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="iv-diag", description="Print identity, status, "
                                 "diagnostics, calibration and configuration.")
    _add_device_args(ap)
    ap.add_argument("--json", action="store_true", help="one JSON object instead of text")
    args = ap.parse_args(argv)
    try:
        with _open(args, ready=False) as inst:    # also a board stuck in BOOT
            out = {
                "idn": inst.idn().fields,
                "stat": {k: _text(v) for k, v in inst.stat().items()},
                "diag": {k: _text(v) for k, v in inst.diag().items()},
                "cal": inst.command("CAL?").fields,
                "cfg": inst.command("CFG?").fields,
            }
    except KeyboardInterrupt:
        print("iv-diag: interrupted", file=sys.stderr)
        return 130
    except CLI_ERRORS as exc:
        print(f"iv-diag: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        for title, fields in out.items():
            _print_fields(title.upper(), dict(fields))
    return 0


# ---- iv-sweep -----------------------------------------------------------------------

def _load_config(path: str) -> dict[str, Any]:
    p = Path(path)
    if p.suffix == ".toml":
        return tomllib.loads(p.read_text(encoding="utf-8"))
    return json.loads(p.read_text(encoding="utf-8"))


def _parse_kv(items: Sequence[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items:
        k, sep, v = it.partition("=")
        if not sep or not k or not v:
            raise SystemExit(f"bad --cfg item {it!r} (expected key=value)")
        out[k] = v
    return out


def _decimal(value: Any, what: str) -> Decimal:
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError(f"{what}: {value!r} is not a decimal number") from None


def _list_points(path: str) -> list[ListPoint]:
    """A list file: one ``volts[,settle_us]`` per line (``;`` also separates,
    ``#`` starts a comment). An omitted settle time means the configuration's
    ``settle_us`` (``ListPoint.settle_us=None``)."""
    pts = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [x.strip() for x in line.replace(";", ",").split(",")]
        try:
            v = Decimal(parts[0])
            settle = int(parts[1]) if len(parts) > 1 and parts[1] else None
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"{path}:{n}: cannot parse {line!r}: {exc}") from exc
        pts.append(ListPoint(v, settle))
    return pts


def _config_list_points(items: Iterable[Any]) -> list[ListPoint]:
    """``list`` of a --config file: entries ``v``, ``[v]`` or ``[v, settle_us]``;
    a missing or null ``settle_us`` means the configuration's ``settle_us``."""
    out = []
    for p in items:
        if isinstance(p, (list, tuple)):
            if not 1 <= len(p) <= 2:
                raise ValueError(f"list entry {p!r}: expected v, [v] or [v, settle_us]")
            settle = p[1] if len(p) == 2 else None
            out.append(as_list_point((_decimal(p[0], "list voltage"), settle)))
        else:
            out.append(as_list_point(_decimal(p, "list voltage")))
    return out


def sweep_main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="iv-sweep",
        description="Run one I-V sweep and write <out>.csv + <out>.json. The definition "
                    "comes from --wp/--step, --list, or a JSON/TOML --config file with keys "
                    "waypoints, step, cycles | list, cfg, note.")
    _add_device_args(ap)
    ap.add_argument("--config", help="JSON or TOML sweep description")
    ap.add_argument("--wp", nargs="+", metavar="V", help="waypoints in volts")
    ap.add_argument("--step", help="step in volts (exact grid)")
    ap.add_argument("--cycles", type=int, default=None)
    ap.add_argument("--list", metavar="FILE",
                    help="list sweep: one 'volts[,settle_us]' per line (no settle_us: the "
                         "configured settle_us)")
    ap.add_argument("--cfg", nargs="*", default=[], metavar="KEY=VALUE",
                    help="instrument configuration (CFG keys)")
    ap.add_argument("--out", required=True,
                    help="output base name: <out>.csv and <out>.json are appended "
                         "(a dot in the name is kept)")
    ap.add_argument("--note", help="free text stored in the JSON sidecar")
    ap.add_argument("--allow-bringup", action="store_true",
                    help="permit a sweep on a bring-up build (normally refused)")
    ap.add_argument("--keep-armed", action="store_true",
                    help="do not send DISARM after the sweep. This has no lasting effect: "
                         "when the tool exits it closes the port, DTR drops and the "
                         "instrument disarms by itself (session lost)")
    ap.add_argument("--no-overwrite", action="store_true",
                    help="refuse to run if <out>.csv or <out>.json exists (default: replace)")
    args = ap.parse_args(argv)

    # Everything that can be checked without the instrument is checked first.
    try:
        conf: dict[str, Any] = _load_config(args.config) if args.config else {}
        cfg = dict(conf.get("cfg", {}))
        cfg.update(_parse_kv(args.cfg))
        note = args.note or conf.get("note")
        waypoints = args.wp or conf.get("waypoints")
        step = args.step or conf.get("step")
        cycles = args.cycles or int(conf.get("cycles", 1))
        list_file = args.list or conf.get("list_file")
        list_pts = conf.get("list")
        if not waypoints and not list_file and not list_pts:
            ap.error("give --wp/--step, --list, or a --config with a definition")
        definition = None
        points: list[ListPoint] | None = None
        if waypoints:
            if step is None:
                ap.error("--wp needs --step")
            definition = waypoint_sweep([_decimal(w, "waypoint") for w in waypoints],
                                        _decimal(step, "step"), cycles)
        else:
            points = _list_points(list_file) if list_file else _config_list_points(list_pts)
            list_add_commands(points)           # range and grid checks
        if args.no_overwrite:
            existing = [str(p) for p in sweep_paths(args.out) if p.exists()]
            if existing:
                raise FileExistsError(f"refusing to overwrite {', '.join(existing)} "
                                      "(--no-overwrite)")
        _check_writable(sweep_paths(args.out))    # a typo must not cost the sweep
    except CLI_ERRORS as exc:
        print(f"iv-sweep: {exc}", file=sys.stderr)
        return 1

    res: SweepResult | None = None
    meta: dict[str, Any] = {}
    errors: list[str] = []
    interrupted = False
    try:
        with _open(args, allow_bringup=args.allow_bringup) as inst:
            meta["idn"] = inst.idn()
            if cfg:
                inst.cfg(**cfg)
            if definition is not None:
                d = inst.sweep_waypoints(definition)
            else:
                assert points is not None
                d = inst.sweep_list(points)
            print(f"sweep: {d.kind}, {d.points} points, ~{d.est_s} s")
            # read before the sweep: the sidecar has them even if the session is lost
            meta["cfg"] = inst.command("CFG?")
            meta["cal"] = inst.command("CAL?")
            inst.arm()
            try:
                res = inst.run_sweep(allow_incomplete=True, allow_bringup=args.allow_bringup)
            except BaseException:
                res = inst.last_sweep           # the records collected so far
                raise
            finally:
                # On a lost session the instrument has disarmed by itself.
                if not args.keep_armed and inst.session_lost is None:
                    try:
                        inst.disarm()
                    except CLI_ERRORS as exc:
                        errors.append(f"DISARM failed: {exc}")
    except KeyboardInterrupt:
        interrupted = True
        errors.insert(0, "interrupted")
    except CLI_ERRORS as exc:
        errors.insert(0, str(exc))

    if res is not None:
        extra = {"error": "; ".join(errors), "partial": not res.ended} if errors else None

        def write(base: Path, overwrite: bool) -> tuple[Path, Path] | None:
            try:
                return write_sweep(base, res, idn=meta.get("idn"), cfg=meta.get("cfg"),
                                   cal=meta.get("cal"), note=note, extra=extra,
                                   overwrite=overwrite)
            except OSError as exc:
                errors.append(f"cannot write the results to {base}: {exc}")
                return None
        written = write(Path(args.out), not args.no_overwrite)
        if written is None:
            # Never lose a finished sweep: a new directory of its own in the
            # temporary directory (nothing there can be overwritten), under
            # the requested name or, if that name is the problem, "sweep".
            try:
                rescue = Path(tempfile.mkdtemp(prefix="iv-sweep-"))
            except OSError as exc:
                errors.append(f"cannot create a directory for the results: {exc}")
            else:
                for name in [n for n in (Path(args.out).name, "sweep") if n]:
                    written = write(rescue / name, False)
                    if written is not None:
                        break
        if written is not None:
            prefix = "" if res.ended else "partial results: "
            print(f"{prefix}status={res.status} records={len(res.records)} -> "
                  f"{written[0]}, {written[1]}")
    for text in errors:
        print(f"iv-sweep: {text}", file=sys.stderr)
    if interrupted:
        return 130
    if errors or res is None:
        return 1
    return 0 if res.status == "complete" else 2


# ---- iv-cal -------------------------------------------------------------------------

def cal_main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="iv-cal",
        description="Guided calibration (docs/firmware/calibration.md): ZERO, ADC gains with "
                    "a 0.01 %% reference resistor and a DMM, the DAC with the DUT open, R_s "
                    "with a load. Shows the CAL.SET line and asks before sending and saving.")
    _add_device_args(ap)
    ap.add_argument("--steps", default="zero,gains,dac,rs",
                    help="comma-separated subset of zero,gains,dac,rs (dac and rs need the "
                         "ADC offsets, from the zero or gains step in the same run: every "
                         "run is a new session, whose zero starts stale)")
    ap.add_argument("--r-ref", type=float, help="reference resistor in ohm (gains step)")
    ap.add_argument("--vmax", default="9", help="sweep amplitude for the dac/rs steps (V)")
    ap.add_argument("--n-avg", type=int, default=16)
    ap.add_argument("--note", help="NOTE stored with the record (<= 31 chars, no spaces)")
    args = ap.parse_args(argv)
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    if "gains" in steps and not args.r_ref:
        ap.error("the gains step needs --r-ref")
    try:
        with _open(args) as inst:
            wf = CalibrationWorkflow(inst, n_avg=args.n_avg)
            wf.load_current()
            if "zero" in steps:
                wf.step_zero()
            if "gains" in steps:
                wf.step_gains(args.r_ref)
            if "dac" in steps:
                wf.step_dac(args.vmax)
            if "rs" in steps:
                wf.step_series_resistance(args.vmax)
            seq = wf.apply(note=args.note)
    except KeyboardInterrupt:
        print("iv-cal: interrupted", file=sys.stderr)
        return 130
    except CLI_ERRORS as exc:
        print(f"iv-cal: {exc}", file=sys.stderr)
        return 1
    return 0 if seq is not None else 3


if __name__ == "__main__":  # pragma: no cover
    sys.exit(diag_main())
