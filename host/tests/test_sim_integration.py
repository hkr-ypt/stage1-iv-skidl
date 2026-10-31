"""End-to-end tests: the real host library against the real firmware running in
the simulator (firmware/sim, docs/firmware/simulator.md).

The simulator executable is found via $STAGE1IV_SIM or
firmware/build/host/stage1_iv_sim. Without it these tests are SKIPPED, unless
STAGE1IV_REQUIRE_SIM=1 (set in CI), in which case a missing simulator is a
failure: CI must never report a skipped suite as passing.

The analog board model exercises the firmware logic; it does not predict the
instrument's accuracy.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal

import pytest

from stage1iv.errors import ErrPrecond, ErrRange, SweepFailed
from stage1iv.sim import SimulatorProcess, find_simulator
from stage1iv.sweeps import hysteresis_loop, linear

SIM = find_simulator()
REQUIRE = os.environ.get("STAGE1IV_REQUIRE_SIM") == "1"

if SIM is None:
    if REQUIRE:
        raise RuntimeError("STAGE1IV_REQUIRE_SIM=1 but the simulator executable was not found")
    pytestmark = pytest.mark.skip(reason="firmware simulator not built")


@pytest.fixture
def sim():
    with SimulatorProcess(speed=1.0, seed=1) as s:
        yield s


def wait_state(inst, state, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = inst.stat()["state"]
        if s == state:
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def inst(sim):
    # allow_bringup only matters for a bring-up simulator build; the refusal
    # itself is covered by tests/test_client_unit.py
    with sim.instrument(allow_bringup=True) as i:
        assert wait_state(i, "DISARMED")      # boot POST finished
        yield i


def jacks_open(sim, open_):
    sim.ctl.sense_plus_open(open_)
    sim.ctl.sense_minus_open(open_)
    sim.ctl.force_open(open_)
    if open_:
        sim.ctl.dut_open()
    else:
        sim.ctl.dut_resistor(1000)


def test_identity_and_status(inst):
    idn = inst.idn()
    assert idn.model == "stage1-iv" and idn.proto == 1
    assert idn.usb_vid == 0x1209 and idn.usb_pid == 1 and idn.planned_pid == "500S"
    st = inst.stat()
    assert st["state"] == "DISARMED" and st["session"] == 1 and st["afe"] == "ok"


def test_bidirectional_resistor_sweep_matches_model(sim, inst):
    inst.cfg(settle_us=500)
    inst.sweep_waypoints(linear(-10, 10, 1))
    inst.arm()
    res = inst.run_sweep()
    inst.disarm()
    assert res.status == "complete" and len(res.records) == 21
    for r in res.records:
        # 1 kohm DUT behind R_top 106 + R_bot 15 (model offsets ~ mV)
        expect = float(r.v_set_v) * 1000.0 / 1121.0
        assert abs(float(r.v_v) - expect) < 0.01
        assert abs(float(r.i_a) - float(r.v_v) / 1000.0) < 2e-5
        assert not (r.flags & 0x003F)        # no OVR/CLIM/KELVIN/NO_OUTPUT/COMPLIANCE
    assert abs(sim.ctl.obs()["v_sweep"]) < 0.01


def test_diode_hysteresis_loop_identical_codes(sim, inst):
    jacks_open(sim, True)
    inst.zero()                          # so reverse-bias current reads ~0
    jacks_open(sim, False)
    sim.ctl.dut_diode("1e-12", "1.8", "2")
    inst.cfg(settle_us=300, i_compliance="0.03")
    inst.sweep_waypoints(hysteresis_loop("2", "0.1", cycles=2))
    inst.arm()
    res = inst.run_sweep()
    inst.disarm()
    by_v: dict[Decimal, set[int]] = {}
    for r in res.records:
        by_v.setdefault(r.v_set_v, set()).add(r.dac_code)
    assert all(len(codes) == 1 for codes in by_v.values())
    fwd = [r for r in res.records if r.v_set_v > Decimal("1.5")]
    rev = [r for r in res.records if r.v_set_v < Decimal("-1.5")]
    assert all(r.i_a > 0 for r in fwd) and all(abs(r.i_a) < Decimal("1e-6") for r in rev)


def test_zero_then_records_not_stale(sim, inst):
    jacks_open(sim, False)
    with pytest.raises(ErrRange):
        inst.zero()                       # DUT connected: implausible offsets
    jacks_open(sim, True)
    z = inst.zero()
    assert z.pairs == 64
    jacks_open(sim, False)
    inst.sweep_waypoints(linear(0, 1, "0.5"))
    inst.arm()
    res = inst.run_sweep()
    inst.disarm()
    assert all(not (r.flags & (1 << 9)) for r in res.records)   # ZERO_STALE clear


def test_session_loss_disarms_and_never_rearms(sim):
    with sim.instrument() as inst:
        assert wait_state(inst, "DISARMED")
        inst.arm()
        inst.src("3")
        assert abs(sim.ctl.obs()["v_sweep"] - 3) < 0.02
    # port closed: DTR dropped, session lost
    time.sleep(1.5)
    obs = sim.ctl.obs()
    assert obs["gate"] == 0 and abs(obs["v_sweep"]) < 0.01
    with sim.instrument() as inst:
        assert wait_state(inst, "DISARMED")
        hello = inst.last_hello
        assert hello is not None and hello.fields.get("disarm_reason") == "session_lost"
        assert inst.stat()["state"] == "DISARMED"


def test_vbus_unplug_mid_sweep(sim, inst):
    inst.cfg(settle_us=20000)
    inst.sweep_waypoints(linear(0, 5, "0.25"))
    inst.arm()
    inst.command("SWP.RUN")
    time.sleep(0.3)
    sim.ctl.vbus(0)
    time.sleep(1.5)
    obs = sim.ctl.obs()
    assert obs["connected"] == 0 and obs["gate"] == 0 and abs(obs["v_sweep"]) < 0.01


def test_host_timeout(sim):
    with sim.instrument(keepalive_interval=None) as inst:
        assert wait_state(inst, "DISARMED")
        inst.cfg(host_timeout_s=1)
        inst.arm()
        time.sleep(2.0)
        st = inst.stat()
        assert st["state"] == "DISARMED" and st["disarm_reason"] == "host_timeout"


def test_arm_preconditions(sim, inst):
    sim.ctl.bor(2)
    time.sleep(0.1)
    with pytest.raises(ErrPrecond):
        inst.arm()
    sim.ctl.bor(3)
    sim.ctl.rails(1, 0)
    time.sleep(0.4)
    with pytest.raises(ErrPrecond) as e:
        inst.arm()
    assert "afe" in str(e.value)


def test_loop_stall_faults_and_zeroes(sim, inst):
    inst.arm()
    inst.src("2")
    sim.ctl.stall(1500)
    time.sleep(0.3)
    st = inst.stat()
    assert st["state"] == "FAULT" and st["fault"] == "LOOP_OVERRUN"
    obs = sim.ctl.obs()
    assert obs["dac_a"] == 32768 and obs["dac_b"] == 65535 and obs["gate"] == 0
    inst.fault_clr()
    assert inst.stat()["state"] == "DISARMED"


def test_hang_deadman_reset_reports_crash(sim, inst):
    inst.arm()
    inst.src("4")
    sim.ctl.stall(5000)                  # dead-man after 2 ms -> reset
    time.sleep(1.0)
    inst.close()                         # one reader per port
    with sim.instrument() as i2:
        assert wait_state(i2, "DISARMED")
        st = i2.stat()
        assert st["crash"] == 1
        assert i2.diag()["crash"] == "deadman"
    assert sim.ctl.obs()["gate"] == 0


def test_adc_and_spi_fault_injection(sim, inst):
    inst.arm()
    sim.ctl.adc_drdy_dead(True)
    time.sleep(0.5)
    st = inst.stat()
    assert st["state"] == "FAULT" and st["fault"] in ("ADC_TIMEOUT", "STALE")
    assert sim.ctl.obs()["dac_a"] == 32768


def test_host_stall_without_loss(sim, inst):
    """~1500 points; the host stops reading for 2 s (< 1024 records behind)."""
    inst.cfg(settle_us=1000)
    inst.sweep_waypoints(linear(0, "1.5", "0.001"))
    inst.arm()
    stalled = {"done": False}

    def on_record(r):
        if r.idx == 200 and not stalled["done"]:
            stalled["done"] = True
            sim.ctl.tx_limit(0)
            time.sleep(2.0)
            sim.ctl.tx_limit(None)
    res = inst.run_sweep(on_record=on_record)
    inst.disarm()
    assert res.status == "complete" and len(res.records) == 1501
    assert [r.idx for r in res.records] == list(range(1501))


def test_long_stall_overflows_explicitly(sim, inst):
    """The host stops reading for 4 s at ~700 points/s: the firmware ends the
    sweep with status=overflow, and every record it pushed is delivered."""
    inst.cfg(settle_us=100, adc_rate=3750)
    inst.sweep_waypoints(linear(0, 5, "0.001"))
    inst.arm()
    stalled = {"done": False}

    def on_record(r):
        if not stalled["done"]:
            stalled["done"] = True
            sim.ctl.tx_limit(0)
            time.sleep(4.0)
            sim.ctl.tx_limit(None)
    with pytest.raises(SweepFailed) as e:
        inst.run_sweep(on_record=on_record, timeout=60)
    res = e.value.result
    assert res.status == "overflow"
    assert len(res.records) == int(res.end["points"])
    assert len(res.records) >= 1024
    assert [r.idx for r in res.records] == list(range(len(res.records)))
    inst.disarm()


def test_abort_and_compliance(sim, inst):
    inst.cfg(i_compliance="0.002")
    inst.sweep_waypoints(linear(0, 5, "0.25"))
    inst.arm()
    res = inst.run_sweep(allow_incomplete=True)
    assert res.status == "compliance"
    assert 8 <= len(res.records) <= 10
    assert abs(sim.ctl.obs()["v_sweep"]) < 0.01
    inst.disarm()


def test_calibration_persists_across_reset(sim, inst):
    inst.cal_set(r_s="118.5")
    seq = inst.cal_save(ts=1700000000, note="sim")
    assert seq == 1
    sim.ctl.mcu_reset()
    time.sleep(0.5)
    inst.close()
    with sim.instrument() as i2:
        assert wait_state(i2, "DISARMED")
        cal = i2.cal_get()
        assert cal.source == "stored" and cal.seq == 1
        assert Decimal(str(cal.coeffs["r_s"])) == Decimal("118.5")


def test_bringup_dac_raw_only_in_bringup(sim, inst):
    inst.arm()
    if inst.idn().is_bringup:
        inst.command("DAC.RAW 32768 0 CONFIRM=UNSAFE")   # raises on ERR
        assert sim.ctl.obs()["dac_b"] == 0
        inst.command("DAC.RAW OFF")
        assert sim.ctl.obs()["dac_b"] == 65535
    else:
        from stage1iv.errors import ErrUnknown
        with pytest.raises(ErrUnknown):
            inst.command("DAC.RAW 32768 0 CONFIRM=UNSAFE")
        assert sim.ctl.obs()["dac_b_not_fs_loads"] == 0
    inst.disarm()
