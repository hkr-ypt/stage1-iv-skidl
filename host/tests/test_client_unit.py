"""Client behaviour against the scripted FakeDevice (protocol.md)."""

import threading
import time

import pytest

from stage1iv.client import Instrument
from stage1iv.errors import (BringupBuildRefused, ErrUnknown, SessionLost,
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
        # interleaved with them. The short pause lets the keep-alive thread
        # win the (unfair) command lock between commands, as it does when a
        # real application is not issuing commands back to back.
        deadline = time.monotonic() + 10.0
        n = 0
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
