"""Command-line tools: ``iv-diag``, ``iv-sweep``, ``iv-cal``.

Device selection (all tools): ``--port <name>`` or discovery by USB VID/PID
(``--pid``, default the development PID 0x0001 or ``$STAGE1IV_PID``) and
optionally ``--serial <24 hex>``. A fixed device path is never assumed.
``--sim [EXE]`` runs against the firmware simulator instead (testing).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Sequence

from .calibration import CalibrationWorkflow
from .client import Instrument
from .errors import Stage1IVError
from .sweeps import ListPoint, waypoint_sweep
from .writers import write_sweep

log = logging.getLogger(__name__)


def _add_device_args(ap: argparse.ArgumentParser) -> None:
    g = ap.add_argument_group("device")
    g.add_argument("--port", help="serial port name (otherwise discovered by VID/PID)")
    g.add_argument("--serial", help="instrument serial (24 hex characters)")
    g.add_argument("--pid", help="USB PID (default 0x0001 or $STAGE1IV_PID)")
    g.add_argument("--sim", nargs="?", const="", metavar="EXE",
                   help="use the firmware simulator (optionally its path)")
    g.add_argument("-v", "--verbose", action="store_true")


@contextlib.contextmanager
def _open(args: argparse.Namespace, **kwargs: Any) -> Iterator[Instrument]:
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if args.sim is not None:
        from .sim import SimulatorProcess
        with SimulatorProcess(args.sim or None) as sim:
            with sim.instrument(**kwargs) as inst:
                yield inst
        return
    with Instrument.open_serial(args.port, serial=args.serial, pid=args.pid, **kwargs) as inst:
        yield inst


def _print_fields(title: str, fields: dict[str, Any]) -> None:
    print(f"[{title}]")
    for k, v in fields.items():
        print(f"  {k:20s} {v}")


# ---- iv-diag ------------------------------------------------------------------------

def diag_main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="iv-diag", description="Print identity, status, "
                                 "diagnostics, calibration and configuration.")
    _add_device_args(ap)
    ap.add_argument("--json", action="store_true", help="one JSON object instead of text")
    args = ap.parse_args(argv)
    try:
        with _open(args) as inst:
            out = {
                "idn": inst.idn().fields,
                "stat": {k: str(v) for k, v in inst.stat().items()},
                "diag": {k: str(v) for k, v in inst.diag().items()},
                "cal": inst.command("CAL?").fields,
                "cfg": inst.command("CFG?").fields,
            }
    except Stage1IVError as exc:
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


def _list_points(path: str) -> list[ListPoint]:
    pts = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [x.strip() for x in line.replace(";", ",").split(",")]
        try:
            v = Decimal(parts[0])
            settle = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"{path}:{n}: cannot parse {line!r}: {exc}") from exc
        pts.append(ListPoint(v, settle))
    return pts


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
    ap.add_argument("--list", metavar="FILE", help="list sweep: one 'volts[,settle_us]' per line")
    ap.add_argument("--cfg", nargs="*", default=[], metavar="KEY=VALUE",
                    help="instrument configuration (CFG keys)")
    ap.add_argument("--out", required=True, help="output base name (no extension)")
    ap.add_argument("--note", help="free text stored in the JSON sidecar")
    ap.add_argument("--allow-bringup", action="store_true",
                    help="permit a sweep on a bring-up build (normally refused)")
    ap.add_argument("--keep-armed", action="store_true", help="do not DISARM afterwards")
    args = ap.parse_args(argv)

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
    if waypoints:
        if step is None:
            ap.error("--wp needs --step")
        definition = waypoint_sweep([Decimal(str(w)) for w in waypoints], Decimal(str(step)), cycles)
    try:
        with _open(args, allow_bringup=args.allow_bringup) as inst:
            idn = inst.idn()
            if cfg:
                inst.cfg(**cfg)
            if waypoints:
                d = inst.sweep_waypoints(definition)
            elif list_file:
                d = inst.sweep_list(_list_points(list_file))
            else:
                d = inst.sweep_list([ListPoint(Decimal(str(p[0])), int(p[1])) if isinstance(p, list)
                                     else ListPoint(Decimal(str(p)), 0) for p in list_pts])
            print(f"sweep: {d.kind}, {d.points} points, ~{d.est_s} s")
            inst.arm()
            try:
                res = inst.run_sweep(allow_incomplete=True, allow_bringup=args.allow_bringup)
            finally:
                if not args.keep_armed:
                    inst.disarm()
            csv_path, json_path = write_sweep(args.out, res, idn=idn,
                                              cfg=inst.command("CFG?"),
                                              cal=inst.command("CAL?"), note=note)
    except Stage1IVError as exc:
        print(f"iv-sweep: {exc}", file=sys.stderr)
        return 1
    print(f"status={res.status} records={len(res.records)} -> {csv_path}, {json_path}")
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
                    help="comma-separated subset of zero,gains,dac,rs")
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
    except Stage1IVError as exc:
        print(f"iv-cal: {exc}", file=sys.stderr)
        return 1
    return 0 if seq is not None else 3


if __name__ == "__main__":  # pragma: no cover
    sys.exit(diag_main())
