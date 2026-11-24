"""The iv-sweep / iv-diag / iv-cal command-line tools against the FakeDevice."""

from __future__ import annotations

import contextlib
import io
import json

import pytest

from stage1iv import cli, discovery
from stage1iv import writers as W
from stage1iv.client import Instrument
from stage1iv.errors import TimeoutError as S1Timeout
from stage1iv.sweeps import ListPoint, list_add_commands


@pytest.fixture
def use_fake(monkeypatch, inst):
    """Route the tools' instrument to the FakeDevice-backed ``inst``."""
    @contextlib.contextmanager
    def fake_open(args, **kwargs):
        yield inst
    monkeypatch.setattr(cli, "_open", fake_open)
    return inst


@pytest.fixture
def no_ports(monkeypatch):
    monkeypatch.setattr(discovery, "_default_lister", lambda: [])


def test_list_file_omitted_settle_means_configured_settle(tmp_path):
    """HL-7: no settle_us column -> None (plain V), not 0 (which is refused)."""
    f = tmp_path / "pts.txt"
    f.write_text("0.5\n-0.5   # comment\n1, \n0.25, 2000\n")
    pts = cli._list_points(str(f))
    assert pts[0] == ListPoint(pts[0].v, None) and pts[3].settle_us == 2000
    assert list_add_commands(pts) == ["SWP.LIST.ADD 0.5 -0.5 1 0.25:2000"]


def test_config_list_omitted_settle_means_configured_settle():
    pts = cli._config_list_points([0.5, "-0.5", [1], [0.25, 2000], [2, None]])
    assert list_add_commands(pts) == ["SWP.LIST.ADD 0.5 -0.5 1 0.25:2000 2"]
    with pytest.raises(ValueError):
        cli._config_list_points([[0.25, 2000.5]])     # inexact settle time


def test_config_list_sweep_runs(tmp_path, use_fake, device):
    conf = tmp_path / "s.json"
    conf.write_text(json.dumps({"list": [0.5, [1, None], [0.25, 2000]]}))
    rc = cli.sweep_main(["--config", str(conf), "--out", str(tmp_path / "l")])
    assert "SWP.LIST.ADD 0.5 1 0.25:2000" in device.received
    assert rc == 0
    assert len(W.read_csv(tmp_path / "l.csv")) == 3


def test_device_not_found_is_a_message_not_a_traceback(no_ports, tmp_path, capsys):
    """HL-12: DeviceNotFound (a LookupError) -> one line, exit 1."""
    rc = cli.sweep_main(["--wp", "0", "1", "--step", "0.5", "--out", str(tmp_path / "x")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "iv-sweep: no instrument found" in err and "Traceback" not in err
    assert cli.diag_main([]) == 1
    assert cli.cal_main(["--steps", "zero"]) == 1
    assert not (tmp_path / "x.csv").exists()


@pytest.mark.parametrize("argv, text", [
    (["--wp", "0", "1", "-0.5", "--step", "0.3"], "suggest step=0.25"),     # GridError
    (["--wp", "0", "x1", "--step", "0.5"], "not a decimal number"),
    (["--wp", "0", "11", "--step", "1"], "10 V"),
    (["--list", "{missing}"], "No such file"),          # {missing}: a file under tmp_path
])
def test_definition_errors_are_messages(no_ports, tmp_path, capsys, argv, text):
    missing = str(tmp_path / "missing" / "points.txt")
    argv = [missing if a == "{missing}" else a for a in argv]
    rc = cli.sweep_main(argv + ["--out", str(tmp_path / "x")])
    assert rc == 1
    err = capsys.readouterr().err
    assert text in err and "Traceback" not in err


def test_bad_cfg_value_is_a_message(use_fake, tmp_path, capsys):
    rc = cli.sweep_main(["--wp", "0", "1", "--step", "0.5", "--cfg", "n_avg=2.5",
                         "--out", str(tmp_path / "x")])
    assert rc == 1
    assert "n_avg" in capsys.readouterr().err


def test_sweep_files_keep_a_dotted_out_name(use_fake, tmp_path, capsys):
    rc = cli.sweep_main(["--wp", "0", "1", "--step", "0.5", "--out", str(tmp_path / "d_0.5V")])
    assert rc == 0
    assert (tmp_path / "d_0.5V.csv").exists() and (tmp_path / "d_0.5V.json").exists()
    assert not (tmp_path / "d_0.csv").exists()


def test_no_overwrite_refuses_before_measuring(use_fake, device, tmp_path, capsys):
    (tmp_path / "r.csv").write_text("old\n")
    rc = cli.sweep_main(["--wp", "0", "1", "--step", "0.5", "--out", str(tmp_path / "r"),
                         "--no-overwrite"])
    assert rc == 1 and "refusing to overwrite" in capsys.readouterr().err
    assert (tmp_path / "r.csv").read_text() == "old\n"
    assert "ARM" not in device.received
    assert cli.sweep_main(["--wp", "0", "1", "--step", "0.5", "--out", str(tmp_path / "r")]) == 0
    assert (tmp_path / "r.csv").read_text() != "old\n"     # default: replaced as before


def test_partial_results_written_when_the_sweep_fails(use_fake, device, tmp_path, monkeypatch,
                                                      capsys):
    """HL-12: a timeout mid-sweep must not lose the records collected so far."""
    real = Instrument.run_sweep

    def failing_run_sweep(self, on_record=None, **kwargs):
        def cb(rec):
            if rec.idx == 4:
                raise S1Timeout("simulated: sweep stalled")
        return real(self, on_record=cb, **kwargs)

    monkeypatch.setattr(Instrument, "run_sweep", failing_run_sweep)
    rc = cli.sweep_main(["--wp", "0", "1", "--step", "0.1", "--out", str(tmp_path / "p")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "simulated: sweep stalled" in err
    rows = W.read_csv(tmp_path / "p.csv")
    assert [int(r["idx"]) for r in rows] == [0, 1, 2, 3, 4]
    doc = json.loads((tmp_path / "p.json").read_text())
    assert doc["sweep"]["status"] == "unknown" and doc["host"]["partial"] is True
    assert "stalled" in doc["host"]["error"]
    sent = device.t.sent_lines()
    assert "SWP.ABORT" in sent and sent.index("SWP.ABORT") < sent.index("DISARM")


def test_results_written_when_the_final_disarm_fails(use_fake, device, tmp_path, monkeypatch,
                                                     capsys):
    def bad_disarm(self, *a, **k):
        raise S1Timeout("simulated: no DISARMED")

    monkeypatch.setattr(Instrument, "disarm", bad_disarm)
    rc = cli.sweep_main(["--wp", "0", "1", "--step", "0.5", "--out", str(tmp_path / "d")])
    assert rc == 1
    assert "DISARM failed: simulated: no DISARMED" in capsys.readouterr().err
    doc = json.loads((tmp_path / "d.json").read_text())
    assert doc["sweep"]["status"] == "complete" and doc["host"]["partial"] is False
    assert len(W.read_csv(tmp_path / "d.csv")) == 3


def test_iv_cal_errors_are_messages(use_fake, capsys, monkeypatch):
    """iv-cal --steps zero has nothing to apply: a message, not a traceback."""
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))      # "Press Enter when ready"
    assert cli.cal_main(["--steps", "zero"]) == 1
    assert "iv-cal: nothing to apply" in capsys.readouterr().err


def test_sweep_refuses_a_missing_output_directory_before_opening(tmp_path, monkeypatch, capsys):
    """HOST-7: a typo in --out must not cost a finished sweep: it is refused
    before the instrument is opened."""
    opened = []
    monkeypatch.setattr(cli, "_open", lambda *a, **k: opened.append(1))
    rc = cli.sweep_main(["--wp", "0", "1", "--step", "0.5",
                         "--out", str(tmp_path / "no_such_dir" / "run1")])
    assert rc == 1 and not opened
    assert "No such file or directory" in capsys.readouterr().err


def test_sweep_results_fall_back_to_the_temp_dir(tmp_path, monkeypatch, capsys, use_fake):
    """HOST-7: if the results cannot be written where asked once the sweep is
    done, they are written to the temporary directory instead of being lost."""
    real = cli.write_sweep
    calls = []

    def flaky(base, *a, **k):
        calls.append(base)
        if len(calls) == 1:
            raise OSError("disk full")
        return real(base, *a, **k)
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    monkeypatch.setattr(cli, "write_sweep", flaky)
    monkeypatch.setattr(cli.tempfile, "gettempdir", lambda: str(fallback))
    rc = cli.sweep_main(["--wp", "0", "1", "--step", "0.5", "--out", str(tmp_path / "r")])
    out, err = capsys.readouterr()
    assert rc == 1 and "disk full" in err
    assert (fallback / "r.csv").exists() and str(fallback / "r.csv") in out
    assert len(W.read_csv(fallback / "r.csv")) == 3


def test_open_waits_for_boot_except_for_iv_diag(monkeypatch):
    """HOST-9: the tools wait while the instrument is in BOOT; iv-diag does
    not, so it can still report on a board that stays there."""
    waited = []

    class Inst:
        def wait_ready(self):
            waited.append(1)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(cli.Instrument, "open_serial", lambda *a, **k: Inst())
    args = cli.argparse.Namespace(sim=None, port="x", serial=None, pid=None, verbose=False)
    with cli._open(args):
        pass
    with cli._open(args, ready=False):
        pass
    assert waited == [1]
