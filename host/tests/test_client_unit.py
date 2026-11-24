"""Client behaviour against the scripted FakeDevice (protocol.md)."""

import threading
import time

import pytest

from stage1iv.client import Instrument, is_urgent
from stage1iv.errors import (BringupBuildRefused, DeviceError, ErrPrecond, ErrUnknown,
                             ProtocolError, SessionLost, StateChangeFailed,
                             TimeoutError as S1Timeout)


def test_idn_and_dev_pid(inst):
    idn = inst.idn()
    assert idn.usb_vid == 0x1209 and idn.usb_pid == 0x0001
    assert idn.usb_pid_status == "dev-test" and idn.planned_pid == "500S"
    assert not idn.is_bringup


def test_error_mapping(inst):
    with pytest.raises(ErrUnknown):
        inst.command("BOGUS")


def test_responses_matched_in_order_with_interleaved_events(device, inst):
    device.chatter = True
    for _ in range(20):
        assert inst.ping() >= 0
        assert inst.stat()["state"] == "DISARMED"
    assert any(e.type == "WARN" for e in inst.events)


def test_arm_waits_for_state_and_disarm(inst):
    inst.arm()
    assert inst.state == "ARMED"
    inst.disarm()
    assert inst.state == "DISARMED"


def test_src_reports_a_ramp_ended_by_compliance(device, inst):
    inst.arm()
    assert inst.src("1.5").get("status") == "reached"
    device.src_status = "compliance"
    with pytest.raises(StateChangeFailed) as exc:
        inst.src("3")
    assert exc.value.event.get("status") == "compliance"


def test_sweep_with_lost_lines_recovered_by_fetch(device, inst):
    device.drop_seqs = {3, 4, 7}
    inst.sweep_waypoints([0, 1], "0.1")
    inst.arm()
    res = inst.run_sweep()
    assert res.status == "complete"
    assert [r.idx for r in res.records] == list(range(11))
    assert [r.seq for r in res.records] == list(range(1, 12))
    assert any(line.startswith("DATA.FETCH FROM=3") for line in device.received)


def test_deferred_command_uses_long_timeout(device, inst):
    device.meas_delay = 2.5          # longer than command_timeout (2 s)
    m = inst.meas()
    assert m.i_raw == 100 and m.v_raw == -200


def test_deferred_timeout_raises(device, inst):
    device.meas_delay = 4.0
    with pytest.raises(S1Timeout):
        inst.zero()


def test_keepalive_does_not_corrupt_matching(device):
    i = Instrument(device.t, keepalive_interval=0.01, command_timeout=2.0)
    i.open()

    def pings():
        return sum(1 for l in device.received if l == "PING")

    try:
        # Commands keep running until several keep-alive PINGs have been
        # interleaved with them. The keep-alive only pings while a library
        # call or a hold() is in progress (HL-1); inside hold() it pings at
        # its period whatever the application does, so its PINGs are written
        # (under the send lock) while other commands are outstanding.
        deadline = time.monotonic() + 10.0
        n = 0
        with i.hold():
            while (n < 50 or pings() < 3) and time.monotonic() < deadline:
                assert i.cfg_get()["n_avg"] == 1
                assert i.idn(refresh=True).model == "stage1-iv"
                n += 1
                time.sleep(0.002)
    finally:
        i.close()
    assert n >= 50
    assert pings() >= 3


def test_bringup_build_refused(device):
    device.build = "bringup"
    i = Instrument(device.t, keepalive_interval=None)
    i.open()
    try:
        assert i.idn().is_bringup
        i.sweep_waypoints([0, 1], "0.5")
        i.arm()
        with pytest.raises(BringupBuildRefused):
            i.run_sweep()
    finally:
        i.close()


def test_session_lost_on_disconnect(device, inst):
    device.t.device_disconnect("unplugged")
    with pytest.raises(SessionLost):
        for _ in range(20):
            inst.ping()
            time.sleep(0.01)


# ---- review fixes (host-library audit) ----------------------------------------------

def _pings_sent(device) -> int:
    return device.t.sent_lines().count("PING")


def _capture(fn):
    try:
        return fn()
    except BaseException as exc:           # noqa: BLE001 - reported to the test
        return exc


@pytest.fixture
def kinst(device):
    """An instrument with a fast automatic keep-alive (0.05 s)."""
    i = Instrument(device.t, keepalive_interval=0.05, command_timeout=2.0, deferred_timeout=3.0)
    i.open()
    yield i
    i.close()


def test_keepalive_only_while_a_call_or_hold_is_in_progress(device, kinst):
    """HL-1: no PING for an idle (or hung) application; PINGs inside hold()."""
    kinst.stat()
    time.sleep(0.1)
    idle = _pings_sent(device)
    time.sleep(0.5)                        # application idle: nothing keeps it alive
    assert _pings_sent(device) == idle <= 1
    with kinst.hold():
        time.sleep(0.5)
    in_hold = _pings_sent(device)
    assert in_hold >= idle + 5             # entry PING + every 0.05 s
    time.sleep(0.1)
    after = _pings_sent(device)
    time.sleep(0.5)
    assert _pings_sent(device) == after <= in_hold + 1
    assert kinst.stat()["state"] == "DISARMED"      # matching intact
    assert not kinst.protocol_errors


def test_keepalive_pings_are_written_during_a_long_deferred_command(device, kinst):
    """HL-1/HL-3: a blocking wait inside a command keeps the session alive; the
    PINGs are written while MEAS? is outstanding and matched in order."""
    device.meas_delay = 0.6
    m = kinst.meas()
    sent = device.t.sent_lines()
    assert sent[sent.index("MEAS?") + 1:].count("PING") >= 4
    assert m.i_raw == 100 and m.v_raw == -200
    assert kinst.ping() > 0 and kinst.stat()["state"] == "DISARMED"
    assert not kinst.protocol_errors


def test_record_callback_runs_outside_the_keepalive(device, kinst):
    """HL-1: a blocking on_record callback is application code: no PINGs for it."""
    kinst.sweep_waypoints([0, 1], "0.1")
    kinst.arm()
    seen = []

    def cb(rec):
        if rec.idx == 3:
            time.sleep(0.1)
            before = _pings_sent(device)
            time.sleep(0.4)
            seen.append((before, _pings_sent(device)))

    res = kinst.run_sweep(on_record=cb)
    assert res.status == "complete" and len(res.records) == 11
    assert seen and seen[0][0] == seen[0][1]


def test_keepalive_disabled_entirely_but_hold_still_pings(device, inst):
    """keepalive_interval=None: no automatic PING, even during a long wait;
    hold() and keepalive() are explicit requests and still ping."""
    device.meas_delay = 0.5
    inst.meas()
    assert _pings_sent(device) == 0
    assert inst.keepalive() is True
    assert device.t.wait_sent(lambda s: s.count("PING") == 1)
    with inst.hold():
        assert inst.keepalive_period == 5.0
    assert device.t.wait_sent(lambda s: s.count("PING") >= 2)
    assert inst.ping() > 0 and not inst.protocol_errors


def test_keepalive_period_follows_host_timeout(device):
    """HL-4: period = min(keepalive_interval, host_timeout_s / 3); None when off."""
    i = Instrument(device.t, keepalive_interval=5.0)
    assert i.keepalive_period == 5.0                  # not known yet: default
    i.open()
    try:
        i.cfg(host_timeout_s=3)
        assert i.keepalive_period == 1.0
        i.cfg(host_timeout_s=0)
        assert i.keepalive_period is None             # timeout off: nothing to keep alive
        i.cfg(host_timeout_s=60)
        assert i.keepalive_period == 5.0
        device.cfg["host_timeout_s"] = "6"            # read back with CFG? ...
        i.cfg_get()
        assert i.keepalive_period == 2.0
        device.cfg["host_timeout_s"] = "1"            # ... or STAT?
        i.stat()
        assert abs(i.keepalive_period - 1 / 3) < 1e-9
    finally:
        i.close()


def test_short_host_timeout_shortens_the_hold_period(device):
    """HL-4: with host_timeout_s=1 a hold pings every 1/3 s, not every 5 s."""
    i = Instrument(device.t, keepalive_interval=5.0, command_timeout=2.0)
    i.open()
    try:
        i.cfg(host_timeout_s=1)
        before = _pings_sent(device)
        with i.hold():
            time.sleep(1.4)
        assert _pings_sent(device) - before >= 4
    finally:
        i.close()


def test_urgent_verbs_are_classified_like_the_firmware():
    assert is_urgent("DISARM") and is_urgent("  swp.abort\t") and is_urgent("Disarm ")
    assert not is_urgent("DISARM X=1") and not is_urgent("SWP.RUN") and not is_urgent("PING")


def test_disarm_and_abort_are_written_while_a_deferred_command_waits(device, inst):
    """HL-3: DISARM / SWP.ABORT bypass the command lock (the firmware acts on
    them on arrival); FIFO response matching stays intact."""
    inst.arm()
    device.meas_delay = 1.0
    out = {}
    meas = threading.Thread(target=lambda: out.update(m=_capture(inst.meas)))
    meas.start()
    assert device.t.wait_sent(lambda s: "MEAS?" in s)
    t_abort = threading.Thread(target=lambda: out.update(a=_capture(inst.sweep_abort)))
    t_disarm = threading.Thread(target=lambda: out.update(d=_capture(inst.disarm)))
    t_abort.start()
    assert device.t.wait_sent(lambda s: "SWP.ABORT" in s, timeout=0.3)
    t_disarm.start()
    assert device.t.wait_sent(lambda s: "DISARM" in s, timeout=0.3)
    assert meas.is_alive()                 # both written before MEAS? was answered
    for t in (meas, t_abort, t_disarm):
        t.join(5.0)
    assert out["m"].i_raw == 100
    assert out["a"] is None
    assert getattr(out["d"], "arg", None) == "DISARMED"
    assert inst.state == "DISARMED"
    assert inst.ping() > 0 and inst.stat()["state"] == "DISARMED"
    assert not inst.protocol_errors


def test_disarm_is_still_sent_when_the_pairing_is_lost(device, inst):
    """HL-3: DISARM is idempotent, so it is sent even in the desync state."""
    inst.arm()
    device.override["STAT?"] = ["OK"]      # untagged answer to STAT?: pairing lost
    with pytest.raises(ProtocolError):
        inst.stat()
    with pytest.raises(ProtocolError):
        inst.ping()
    ev = inst.disarm()
    assert "DISARM" in device.t.sent_lines()
    assert ev is not None and ev.arg == "DISARMED" and inst.state == "DISARMED"
    inst.resync()
    assert inst.ping() > 0


def test_disarm_in_fault_still_raises_err_state(device, inst):
    device.override["DISARM"] = ["ERR STATE not allowed in FAULT"]
    inst.state = "FAULT"
    with pytest.raises(DeviceError) as e:
        inst.disarm()
    assert e.value.code == "STATE"
    assert inst.ping() > 0


class _Stop(BaseException):
    """A BaseException that is not an Exception (like KeyboardInterrupt)."""


@pytest.mark.parametrize("exc_type", [RuntimeError, _Stop])
def test_sweep_aborted_when_the_record_callback_raises(device, inst, exc_type):
    """HL-2: SWP.ABORT on any exception while the sweep has not ended."""
    inst.sweep_waypoints([0, 1], "0.1")
    inst.arm()

    def cb(rec):
        if rec.idx == 2:
            raise exc_type("bug in the callback")

    with pytest.raises(exc_type):
        inst.run_sweep(on_record=cb)
    assert device.t.wait_sent(lambda s: "SWP.ABORT" in s)
    assert [r.idx for r in inst.last_sweep.records] == [0, 1, 2]
    assert inst.last_sweep.status == "unknown"
    assert inst.ping() > 0 and not inst.protocol_errors


def test_sweep_aborted_when_data_fetch_fails(device, inst):
    device.drop_seqs = {3}
    device.override["DATA.FETCH"] = ["ERR INTERNAL ring corrupt"]
    inst.sweep_waypoints([0, 1], "0.1")
    inst.arm()
    with pytest.raises(DeviceError):
        inst.run_sweep()
    assert device.t.wait_sent(lambda s: "SWP.ABORT" in s)


def test_sweep_aborted_even_when_the_pairing_is_lost(device, inst):
    """HL-2 + HL-3: the abort is sent (unchecked) in the desync state too."""
    inst.sweep_waypoints([0, 1], "0.1")
    inst.arm()

    def cb(rec):
        if rec.idx == 2:
            device.override["STAT?"] = ["OK"]
            inst.stat()                    # ProtocolError: pairing lost

    with pytest.raises(ProtocolError):
        inst.run_sweep(on_record=cb)
    assert device.t.wait_sent(lambda s: "SWP.ABORT" in s)


def test_no_abort_after_a_complete_or_refused_sweep(device, inst):
    inst.sweep_waypoints([0, 1], "0.5")
    with pytest.raises(DeviceError):
        inst.run_sweep()                   # DISARMED: ERR STATE, nothing started
    inst.arm()
    assert inst.run_sweep().status == "complete"
    time.sleep(0.1)
    assert "SWP.ABORT" not in device.t.sent_lines()


def test_arm_retries_a_stale_front_end_check(device, inst):
    """HL-8: ERR PRECOND afe right after DISARM is retried."""
    device.override["ARM"] = ["ERR PRECOND afe"] * 3
    inst.arm()
    assert inst.state == "ARMED"
    assert device.received.count("ARM") == 4


def test_arm_retry_is_bounded_and_only_for_transient_refusals(device, inst):
    device.override["ARM"] = ["ERR PRECOND afe"] * 1000
    t0 = time.monotonic()
    with pytest.raises(ErrPrecond) as e:
        inst.arm(afe_retry=0.3)
    assert e.value.precondition == "afe"
    assert 0.25 <= time.monotonic() - t0 < 2.0
    device.override["ARM"] = ["ERR PRECOND bor"]
    n = device.received.count("ARM")
    with pytest.raises(ErrPrecond):
        inst.arm()
    assert device.received.count("ARM") == n + 1


def test_arm_retries_while_the_adc_reconfigures(device, inst):
    """Right after CFG adc_rate=... the ADC self-calibrates at the new rate and
    ARM is refused 'adc' (CFG answers first): retried like a stale 'afe'
    check, and bounded the same way."""
    device.override["ARM"] = ["ERR PRECOND adc"] * 2
    inst.arm()
    assert inst.state == "ARMED"
    assert device.received.count("ARM") == 3
    inst.disarm()
    device.override["ARM"] = ["ERR PRECOND adc"] * 1000
    t0 = time.monotonic()
    with pytest.raises(ErrPrecond) as e:
        inst.arm(afe_retry=0.3)
    assert e.value.precondition == "adc"
    assert 0.25 <= time.monotonic() - t0 < 2.0


def test_arm_retry_window_includes_two_conversions(device, inst):
    inst.cfg_get()
    assert abs(inst._afe_retry_window() - (2.0 + 2 / 1000)) < 1e-9
    inst.cfg(adc_rate="2.5")
    assert abs(inst._afe_retry_window() - (2.0 + 2 / 2.5)) < 1e-9


def test_measurement_decodes_sd_sat(device, inst):
    device.override["MEAS?"] = ["OK MEAS n=4 i_raw=1 v_raw=2 i_sd=6553.4 v_sd=1.0 "
                                "v_v=0.000003 i_a=0.0000000124 flags=0x0C00"]
    m = inst.meas(4)
    assert m.flag_names == ["MEAS", "SD_SAT"]


# ---- final review fixes ------------------------------------------------------------------

HELLO = ("EVT HELLO model=stage1-iv fw=0.1.0 proto=2 state=DISARMED fault=NONE "
         "disarm_reason=session_lost reset=por crash=0")
OLD_PT = ("PT 1,5,4,1,4000,4300,4600,34072,0.400000,1,400,2668,-,-,0.400000,"
          "0.0004000000,0x0300")


def _open_after(lines, **kwargs):
    """A device that holds ``lines`` from an earlier session (protocol.md
    section 1) and then answers commands; the instrument opened on it."""
    from stage1iv.transport import LoopbackTransport
    from conftest import FakeDevice
    t = LoopbackTransport()
    for ln in lines:
        t.device_write_line(ln)
    dev = FakeDevice(t)
    i = Instrument(t, keepalive_interval=None, command_timeout=2.0, hello_timeout=1.0, **kwargs)
    i.open()
    return t, dev, i


@pytest.mark.parametrize("tail", [
    # the old session's HELLO cut off mid-line: the new session's newline ends it
    ["OK PONG t_us=1", OLD_PT, "", HELLO[:60]],
    # a whole earlier session that was never read, then the old tail
    ["", HELLO.replace("session_lost", "none"), "OK", "OK PONG t_us=7", OLD_PT, ""],
])
def test_an_old_sessions_tail_before_hello_is_skipped(tail):
    """HOST-3: output an earlier session left unread arrives before this
    session's HELLO; none of it is a response, record or HELLO of this one."""
    t, dev, i = _open_after(tail + [HELLO.replace("crash=0", "crash=0 this=1")])
    try:
        assert i.session_lost is None
        assert i.last_hello is not None and "this=1" in i.last_hello.line
        assert i.ping() > 0 and i.stat()["state"] == "DISARMED"
        assert not i.protocol_errors and not i.records
    finally:
        i.close()
        dev.stop()
        t.close()


def test_hello_after_the_first_command_is_still_a_restart(device, inst):
    """HOST-3: replacing a HELLO only applies before anything was sent."""
    assert inst.ping() > 0
    device.w(HELLO)
    with pytest.raises(SessionLost):
        for _ in range(100):
            inst.ping()
            time.sleep(0.01)


def test_hello_between_the_first_command_and_its_response_is_a_restart():
    """protocol.md section 1, as the library implements it: once a command is
    written, a new HELLO means the session restarted (commands in flight may
    be lost), also before the first response arrived."""
    t, dev, i = _open_after(["", HELLO])
    try:
        dev.override["PING"] = [HELLO.replace("crash=0", "crash=0 again=1")]
        with pytest.raises(SessionLost):
            i.ping()
    finally:
        i.close()
        dev.stop()
        t.close()


def test_open_waits_for_a_hello_that_follows_an_old_one():
    """HOST-3: the session's HELLO can arrive a little after an old session's
    HELLO from the unread tail; open() waits for the input to pause, so the
    first command is not sent in between (that would read as a restart)."""
    from stage1iv.transport import LoopbackTransport
    from conftest import FakeDevice
    t = LoopbackTransport()
    for ln in ["", HELLO.replace("session_lost", "none"), "OK"]:
        t.device_write_line(ln)
    threading.Timer(0.05, lambda: (t.device_write_line(""),
                                   t.device_write_line(HELLO + " this=1"))).start()
    dev = FakeDevice(t)
    i = Instrument(t, keepalive_interval=None, command_timeout=2.0, hello_timeout=1.0)
    try:
        i.open()
        assert "this=1" in i.last_hello.line
        assert i.ping() > 0 and i.session_lost is None
    finally:
        i.close()
        dev.stop()
        t.close()


def test_old_events_and_fragments_before_hello_are_not_delivered():
    """HOST-3: an old tail's events (a fault, a state) are not the session's:
    callbacks and the event history see only the HELLO and what follows; its
    cut-off lines are not protocol errors."""
    from stage1iv.transport import LoopbackTransport
    from conftest import FakeDevice
    t = LoopbackTransport()
    for ln in ["SION_LOST t=1", "EVT FAULT code=OVERTEMP detail=old", "EVT STATE FAULT",
               "PT 1,5,4,1,4000,4300,46", "", HELLO, "EVT WARN AFTER_HELLO n=1"]:
        t.device_write_line(ln)
    dev = FakeDevice(t)
    seen = []
    i = Instrument(t, keepalive_interval=None, command_timeout=2.0, hello_timeout=1.0)
    i.add_event_callback(lambda ev: seen.append(ev.type))
    try:
        i.open()
        assert i.ping() > 0
        assert seen == ["HELLO", "WARN"]
        assert [e.type for e in i.events] == ["HELLO", "WARN"]
        assert i.state == "DISARMED" and not i.protocol_errors and not i.records
    finally:
        i.close()
        dev.stop()
        t.close()


def test_unanswered_pings_do_not_delay_an_urgent_command():
    """HOST-2: an urgent command's deadline is not pushed back by the chained
    deadlines of keep-alive PINGs ahead of it."""
    from stage1iv.transport import LoopbackTransport
    t = LoopbackTransport()                  # a device that answers nothing
    i = Instrument(t, keepalive_interval=0.02, command_timeout=0.5)
    i.open()
    try:
        with i.hold():
            time.sleep(0.5)                  # unanswered keep-alive PINGs pile up
        assert sum(1 for s in i._slots if s.background) >= 10
        t0 = time.monotonic()
        with pytest.raises(S1Timeout):
            i.sweep_abort()
        assert time.monotonic() - t0 < 2.0   # its own 0.5 s, not 0.5 s per PING ahead
    finally:
        i.close()
        t.close()


def test_keepalive_continues_past_the_pending_cap_during_a_long_command(device):
    """HOST-1: with a short keepalive_interval the 32 pending PINGs are
    reached early in a long deferred command; bytes must still reach the
    device at host_timeout_s / 3 so the healthy command is not disarmed,
    and no faster, to keep the device's receive buffer bounded."""
    from stage1iv.client import KEEPALIVE_MAX_PENDING
    i = Instrument(device.t, keepalive_interval=0.01, command_timeout=2.0, deferred_timeout=6.0)
    i.open()
    try:
        i.cfg(host_timeout_s=3)              # past the cap: one PING per second
        device.meas_delay = 3.0
        i.meas()
        sent = device.t.sent_lines()
        pings = sent[sent.index("MEAS?") + 1:].count("PING")
        assert KEEPALIVE_MAX_PENDING + 1 <= pings <= KEEPALIVE_MAX_PENDING + 4
        assert i.ping() > 0 and not i.protocol_errors
    finally:
        i.close()


@pytest.mark.parametrize("busy", ["BOOT", "DISARMING"])
def test_wait_ready_waits_out_boot_and_disarming(device, inst, busy):
    """HOST-9: CFG/ZERO/SWP.* answer ERR STATE in BOOT and in DISARMING (a
    previous session's disarm ramp); wait_ready() polls through both."""
    device.state = busy
    threading.Timer(0.3, lambda: setattr(device, "state", "DISARMED")).start()
    assert inst.wait_ready(timeout=3.0) == "DISARMED"
    device.state = "FAULT"
    assert inst.wait_ready(timeout=3.0) == "FAULT"     # returned as it is
    device.state = busy
    with pytest.raises(S1Timeout):
        inst.wait_ready(timeout=0.2)


def test_events_held_at_open_are_delivered_before_open_returns():
    """A previous session lost while armed leaves its disarm ramp running;
    the new HELLO says state=DISARMING, and the ramp's end (EVT STATE DISARMED
    reason=session_lost) can arrive while open() waits for its input to
    pause. open() returns only after that event is delivered (index 0, before
    any command), and it is not this session's loss: disarm() and DATA.FETCH
    right after open() work."""
    from stage1iv.transport import LoopbackTransport
    from conftest import FakeDevice
    hello = HELLO.replace("disarm_reason=session_lost", "state=DISARMING disarm_reason=session_lost")
    hello = hello.replace("state=DISARMED ", "", 1)
    for _ in range(5):                       # the old race lost about one open in four
        t = LoopbackTransport()
        t.device_write_line("")
        t.device_write_line(hello)
        threading.Timer(0.05, lambda: t.device_write_line(
            "EVT STATE DISARMED reason=session_lost")).start()
        dev = FakeDevice(t)
        seen = []
        i = Instrument(t, keepalive_interval=None, command_timeout=2.0, hello_timeout=1.0)
        i.add_event_callback(lambda ev: seen.append((ev.type, ev.arg)))
        try:
            i.open()
            assert i.state == "DISARMED"
            assert seen == [("HELLO", None), ("STATE", "DISARMED")]
            i.disarm()                       # must not raise SessionLost
            i.data_fetch(1)
            assert i.session_lost is None
        finally:
            i.close()
            dev.stop()
            t.close()


def test_only_a_disarm_for_a_loss_fails_listeners(device, inst):
    """EVT STATE DISARMING reason=host_timeout is this session's loss; the
    DISARMED with that reason only ends the ramp (e.g. one the previous
    session left) and must not fail a waiting call."""
    with inst.listen(lambda ev: False) as lst:
        device.w("EVT STATE DISARMED reason=session_lost")
        assert lst.get(0.3) is None
    with inst.listen(lambda ev: False) as lst:
        device.w("EVT STATE DISARMING reason=host_timeout")
        with pytest.raises(SessionLost):
            lst.get(1.0)
