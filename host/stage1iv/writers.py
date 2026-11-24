"""Data files: one CSV row per ``PT`` record plus a JSON sidecar.

The CSV keeps the 17 protocol fields exactly as the device printed them (raw
codes are the authoritative data; ``v_set_V``, ``v_V``, ``i_A`` keep their
fixed decimals, never scientific notation), followed by derived columns: the
decoded flag names and the three times unwrapped to 64-bit microseconds from
the sweep start.

The JSON sidecar holds ``*IDN?``, the ``SWEEP_BEGIN`` and ``SWEEP_END``
event fields (configuration, calibration record and session zero, so raw
codes can be reprocessed), ``CFG?``, ``CAL?``, host metadata and a statement
that accuracy figures are provisional. Device values are stored as the exact
strings received.

File names: :func:`write_sweep` appends ``.csv``/``.json`` to the base name
(``diode_0.5V`` -> ``diode_0.5V.csv``); only an explicit ``.csv``/``.json``
suffix is replaced. Existing files are overwritten unless
``overwrite=False``.
"""

from __future__ import annotations

import csv
import datetime as _dt
import getpass
import json
import os
import platform
import socket
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .records import PT_FIELDS, PTRecord, decimal_text, decode_flags, unwrap_record_times

SIDECAR_FORMAT = "stage1iv-sweep/1"

#: Suffixes :func:`sweep_paths` replaces instead of appending to.
SWEEP_SUFFIXES = (".csv", ".json")

ACCURACY_STATEMENT = (
    "Accuracy figures for this instrument are provisional targets and are UNVERIFIED "
    "until hardware bring-up and metrology (docs/firmware/firmware_spec.md section 14). "
    "Unit and simulation tests verify arithmetic and algorithms only; they say nothing "
    "about analog accuracy. The lowest reliably measurable current is not established.")

CSV_EXTRA_FIELDS = ("flag_names", "t_set_us_unwrapped", "t_i_us_unwrapped", "t_v_us_unwrapped")
CSV_FIELDS = PT_FIELDS + CSV_EXTRA_FIELDS


def _mode(overwrite: bool) -> str:
    return "w" if overwrite else "x"


def write_csv(path: str | os.PathLike, records: Sequence[PTRecord], *,
              overwrite: bool = True) -> Path:
    """Write ``records`` (in point order) to ``path``; returns the path.

    With ``overwrite=False`` an existing file raises :class:`FileExistsError`."""
    path = Path(path)
    times = unwrap_record_times(records)
    with path.open(_mode(overwrite), newline="", encoding="ascii") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(CSV_FIELDS)
        for rec, (ts, ti, tv) in zip(records, times):
            w.writerow(rec.wire_values() + ["|".join(decode_flags(rec.flags)), ts, ti, tv])
    return path


def read_csv(path: str | os.PathLike) -> list[dict[str, str]]:
    """Read a CSV written by :func:`write_csv` (rows as dicts of strings)."""
    with Path(path).open(newline="", encoding="ascii") as fh:
        return list(csv.DictReader(fh))


def _fields(obj: Any) -> dict[str, str] | None:
    """Exact field strings of a Response/Event/IdnInfo/CalInfo/mapping."""
    if obj is None:
        return None
    f = getattr(obj, "fields", None)
    if f is not None:
        return {str(k): str(v) for k, v in dict(f).items()}
    if isinstance(obj, Mapping):     # e.g. converted values: Decimals in fixed-point text
        return {str(k): ("-" if v is None else
                         decimal_text(v) if isinstance(v, Decimal) else str(v))
                for k, v in obj.items()}
    raise TypeError(f"cannot store {type(obj).__name__} in the sidecar")


def host_metadata(note: str | None = None, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Time, host, user, versions and an optional user note."""
    from . import __version__
    try:
        user = getpass.getuser()
    except Exception:  # pragma: no cover - no user database
        user = None
    meta: dict[str, Any] = {
        "written_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "user": user,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "stage1iv_version": __version__,
        "note": note,
    }
    if extra:
        meta.update(extra)
    return meta


def build_sidecar(*, idn: Any = None, sweep_begin: Any = None, sweep_end: Any = None,
                  cfg: Any = None, cal: Any = None, note: str | None = None,
                  result: Any = None, csv_name: str | None = None,
                  extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The sidecar document (see the module docstring)."""
    doc: dict[str, Any] = {
        "format": SIDECAR_FORMAT,
        "accuracy_statement": ACCURACY_STATEMENT,
        "csv": csv_name,
        "idn": _fields(idn),
        "sweep_begin": _fields(sweep_begin),
        "sweep_end": _fields(sweep_end),
        "cfg": _fields(cfg),
        "cal": _fields(cal),
        "host": host_metadata(note, extra),
    }
    if result is not None:
        doc["sweep"] = {
            "sweep_id": result.sweep_id,
            "points_planned": result.points_planned,
            "est_s": decimal_text(result.est_s) if isinstance(result.est_s, Decimal)
            else str(result.est_s),
            "status": result.status,
            "status_inferred": result.status_inferred,
            "records": len(result.records),
            "missing_idx": list(result.missing),
            "seq_gaps_recovered": [list(g) for g in result.seq_gaps],
            "data_fetches": result.fetches,
        }
        if sweep_begin is None and result.begin is None:
            doc["sweep_begin_missing"] = "SWEEP_BEGIN was not received (dropped event)"
        if sweep_end is None and result.end is None:
            doc["sweep_end_missing"] = "SWEEP_END was not received (dropped event)"
    return doc


def write_sidecar(path: str | os.PathLike, *, overwrite: bool = True, **kwargs: Any) -> Path:
    """Write :func:`build_sidecar` as JSON to ``path``.

    With ``overwrite=False`` an existing file raises :class:`FileExistsError`."""
    path = Path(path)
    doc = build_sidecar(**kwargs)
    with path.open(_mode(overwrite), encoding="utf-8") as fh:
        fh.write(json.dumps(doc, indent=2, sort_keys=False) + "\n")
    return path


def sweep_paths(base: str | os.PathLike) -> tuple[Path, Path]:
    """``(<base>.csv, <base>.json)``. The suffixes are appended, so a dot in
    the name is kept (``diode_0.5V`` -> ``diode_0.5V.csv``); only an explicit
    ``.csv``/``.json`` suffix is replaced (``run1.csv`` -> ``run1.csv``,
    ``run1.json``)."""
    base = Path(base)
    if base.suffix.lower() in SWEEP_SUFFIXES:
        base = base.with_suffix("")
    return base.with_name(base.name + ".csv"), base.with_name(base.name + ".json")


def write_sweep(base: str | os.PathLike, result: Any, *, idn: Any = None, cfg: Any = None,
                cal: Any = None, note: str | None = None,
                extra: Mapping[str, Any] | None = None,
                overwrite: bool = True) -> tuple[Path, Path]:
    """Write ``<base>.csv`` and ``<base>.json`` for a :class:`SweepResult`
    (names from :func:`sweep_paths`).

    Existing files are replaced (the default, as before). With
    ``overwrite=False`` nothing is written and :class:`FileExistsError` is
    raised if either file exists."""
    csv_target, json_target = sweep_paths(base)
    if not overwrite:
        existing = [str(p) for p in (csv_target, json_target) if p.exists()]
        if existing:
            raise FileExistsError(f"refusing to overwrite {', '.join(existing)}")
    csv_path = write_csv(csv_target, result.records, overwrite=overwrite)
    json_path = write_sidecar(json_target, idn=idn, sweep_begin=result.begin,
                              sweep_end=result.end, cfg=cfg, cal=cal, note=note, result=result,
                              csv_name=csv_path.name, extra=extra, overwrite=overwrite)
    return csv_path, json_path


def records_from_lines(lines: Iterable[str]) -> list[PTRecord]:
    """Parse ``PT`` lines (e.g. from a log) into records."""
    from .records import parse_pt
    return [parse_pt(line) for line in lines if line.startswith("PT ")]


__all__ = ["ACCURACY_STATEMENT", "CSV_FIELDS", "CSV_EXTRA_FIELDS", "SIDECAR_FORMAT",
           "SWEEP_SUFFIXES", "write_csv", "read_csv", "host_metadata", "build_sidecar",
           "write_sidecar", "sweep_paths", "write_sweep", "records_from_lines"]
