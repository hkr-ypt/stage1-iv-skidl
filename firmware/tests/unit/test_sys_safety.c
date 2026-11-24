/*
 * test_sys_safety.c — system tests of the safety invariants (spec §3.2) and
 * the state machine (§6) on the full firmware + simulation port + board
 * model: arm/disarm sequences, session loss, host timeout, loop stalls,
 * dead-man, watchdog, SPI/ADC faults, compliance, reset behaviour.
 *
 * Every test starts from a fresh power-on. The board model's gate follows the
 * charge pump driven by the firmware's real PC5 edges, so "output disarmed"
 * is checked on the modelled hardware, not on firmware variables.
 */
#include "unity.h"

#include "sys/sys_harness.h"

#include "app.h"
#include "dac8552.h"
#include "engine.h"
#include "heartbeat.h"
#include "instrument.h"
#include "port.h"
#include "port_sim_ext.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

void setUp(void)
{
    h_boot();
}

void tearDown(void)
{
    /* B = 0xFFFF in every frame the firmware ever loaded (SI-1) */
    TEST_ASSERT_EQUAL_UINT32(0, psim_dac()->b_not_fullscale_loads);
    TEST_ASSERT_EQUAL_UINT32(0, psim_dac()->reserved_violations);
    TEST_ASSERT_EQUAL_UINT32(0, psim_dac()->pd_nonzero_frames);
}

static void arm(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("ARM"));
    TEST_ASSERT_TRUE(h_run_until_state(ST_ARMED, 500000));
}

/* ---- boot ---------------------------------------------------------------------- */

static void test_boot_ends_disarmed_with_zero_pair(void)
{
    TEST_ASSERT_EQUAL(ST_DISARMED, h_state());
    TEST_ASSERT_EQUAL_HEX16(0x8000, h_dac_a());
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, h_dac_b());
    TEST_ASSERT_EQUAL_UINT64(0, h_hb_edges());
    TEST_ASSERT_FALSE(h_gate());
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, h_find("EVT HELLO", 0));
}

/* ---- arm ------------------------------------------------------------------------ */

static void test_arm_sequence_and_timing(void)
{
    size_t from = h_lines();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("ARM"));
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, h_find("EVT STATE ARMING", from));
    /* SI-3: the zero code is loaded before the first edge */
    TEST_ASSERT_EQUAL_HEX16(engine_zero_code(), h_dac_a());
    TEST_ASSERT_TRUE(h_run_until_state(ST_ARMED, 500000));
    psim_obs_t o;
    psim_observe(&o);
    TEST_ASSERT_TRUE(h_gate());
    /* the gate closes within ~3 edges; ARMED is reported 100 ms after the first */
    TEST_ASSERT_TRUE(psim_board()->gate_on_events >= 1);
    uint64_t gap = h_run_measure_hb(200000);
    TEST_ASSERT_TRUE_MESSAGE(gap <= 1000, "heartbeat spacing above 1 ms");
    TEST_ASSERT_TRUE(gap >= 200);
}

static void test_arm_preconditions(void)
{
    psim_set_bor_level(2);
    h_run_us(20000);
    TEST_ASSERT_EQUAL_STRING("ERR PRECOND bor", h_cmd("ARM"));
    psim_set_bor_level(3);

    /* +-15 V off: both INA outputs near AGND -> front-end check fails */
    psim_board()->rail_pos = false;
    h_run_us(300000);
    TEST_ASSERT_EQUAL_STRING("ERR PRECOND afe", h_cmd("ARM"));
    psim_board()->rail_pos = true;
    h_run_us(300000);
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("ARM"));
}

static void test_arm_with_charged_cell_is_not_blocked(void)
{
    /* an active DUT drives current into the 0 V output: AFE must not block */
    board_model_t *b = psim_board();
    b->dut.kind = DUT_CELL;
    b->dut.v_cell = 1.5;
    b->dut.r_ohm = 5.0;
    h_run_us(300000);
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("ARM"));
}

static void test_arm_at_slow_data_rates(void)
{
    /* t18 >= 100 ms (<= 10 SPS): one monitor pair spans more than the
     * monitor period, and the AFE check must still be evaluated */
    static const char *const rates[] = { "2.5", "5", "10", "15" };
    char line[48];
    for (size_t k = 0; k < sizeof rates / sizeof rates[0]; ++k) {
        snprintf(line, sizeof line, "CFG adc_rate=%s", rates[k]);
        TEST_ASSERT_EQUAL_STRING("OK", h_cmd(line));
        psim_board()->rail_pos = false;
        h_run_us(3000000);
        TEST_ASSERT_EQUAL_STRING_MESSAGE("ERR PRECOND afe", h_cmd("ARM"), rates[k]);
        psim_board()->rail_pos = true;
        h_run_us(3000000);
        TEST_ASSERT_EQUAL_STRING_MESSAGE("OK", h_cmd("ARM"), rates[k]);
        TEST_ASSERT_TRUE(h_run_until_state(ST_ARMED, 500000));
        TEST_ASSERT_EQUAL_STRING("OK", h_cmd("DISARM"));
        TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 2000000));
    }
}

static void disarm_with_compliance_trip(void (*during)(void))
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG i_compliance=0.006"));
    arm();
    size_t from = h_lines();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=5"));
    TEST_ASSERT_TRUE(h_wait_line("EVT SRC_DONE", from, 2000000));
    from = h_lines();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("DISARM"));
    during();
    TEST_ASSERT_TRUE(h_wait_line("EVT WARN COMPLIANCE", from, 1000000));
    /* the zero code is loaded at the trip: the disarm completes normally */
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 1000000));
    TEST_ASSERT_EQUAL(SIZE_MAX, h_find("EVT FAULT", from));
    TEST_ASSERT_EQUAL_HEX16(engine_zero_code(), h_dac_a());
    TEST_ASSERT_FALSE(h_gate());
}

static void dut_shorts(void)
{
    psim_board()->dut.r_ohm = 10.0;
}

static void dut_is_cell(void)
{
    board_model_t *b = psim_board();
    b->dut.kind = DUT_CELL;
    b->dut.v_cell = 5.0;
    b->dut.r_ohm = 200.0;
}

static void test_compliance_trip_during_disarm_ramp_completes_disarm(void)
{
    disarm_with_compliance_trip(dut_shorts);
}

static void test_cell_tripping_compliance_on_disarm_completes_disarm(void)
{
    /* a source DUT near Voc draws more current as the disarm ramps down */
    disarm_with_compliance_trip(dut_is_cell);
}

static void test_compliance_during_src_ramp_is_reported(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG i_compliance=0.001"));
    arm();
    size_t from = h_lines();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=3"));
    TEST_ASSERT_TRUE(h_wait_line("EVT WARN COMPLIANCE", from, 2000000));
    /* the ramp ends there: SRC_DONE reports the level actually held */
    TEST_ASSERT_TRUE(h_wait_line("EVT SRC_DONE", from, 100000));
    size_t d = h_find("EVT SRC_DONE", from);
    TEST_ASSERT_NOT_NULL(strstr(h_line(d), "status=compliance"));
    TEST_ASSERT_NOT_NULL(strstr(h_line(d), "v_v=0"));
    TEST_ASSERT_FALSE(engine_ramping());
}

static void test_fault_during_sweep_goes_straight_to_fault(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=100000"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 2 STEP=0.5"), "OK SWP", 6));
    arm();
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.RUN"), "OK id=", 6));
    TEST_ASSERT_TRUE(h_run_until_state(ST_SWEEPING, 1000000));
    h_run_us(200000);
    size_t from = h_lines();
    psim_set_stack_ok(false);
    TEST_ASSERT_TRUE(h_run_until_state(ST_FAULT, 100000));
    h_run_us(50000);
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, h_find("EVT SWEEP_END", from));
    /* SWEEPING -> FAULT, never through ARMED */
    TEST_ASSERT_EQUAL(SIZE_MAX, h_find("EVT STATE ARMED", from));
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, h_find("EVT STATE FAULT", from));
}

static void test_sweep_refused_during_src_ramp(void)
{
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 1 STEP=0.5"), "OK SWP", 6));
    arm();
    size_t from = h_lines();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=5"));   /* 0.5 s ramp */
    TEST_ASSERT_EQUAL_STRING("ERR BUSY ramp in progress", h_cmd("SWP.RUN"));
    TEST_ASSERT_TRUE(h_wait_line("EVT SRC_DONE", from, 2000000));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.RUN"), "OK id=", 6));
}

static void test_dac_scrub_only_after_the_scrub_period(void)
{
    /* a ramp step is one frame pair; the scrub adds a pair only every 100 ms */
    arm();
    uint32_t f0 = psim_dac()->frames_executed;
    size_t from = h_lines();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=1"));   /* 100 steps of 10 mV, 100 ms */
    TEST_ASSERT_TRUE(h_wait_line("EVT SRC_DONE", from, 1000000));
    uint32_t frames = psim_dac()->frames_executed - f0;
    TEST_ASSERT_TRUE_MESSAGE(frames >= 200u, "fewer frames than ramp steps");
    TEST_ASSERT_TRUE_MESSAGE(frames <= 2u * 100u + 2u * 3u, "scrub ran after every update");
}

static void test_compliance_at_zero_is_reported_once(void)
{
    /* a source DUT already beyond i_compliance at 0 V: the output cannot do
     * anything about it, but the host is told (once, not every conversion) */
    board_model_t *b = psim_board();
    b->dut.kind = DUT_CELL;
    b->dut.v_cell = 1.0;
    b->dut.r_ohm = 50.0;                  /* -20 mA at 0 V */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG i_compliance=0.005"));
    size_t from = h_lines();
    arm();
    h_run_us(1000000);
    size_t n = 0;
    for (size_t i = from; i < h_lines(); i++) {
        if (strncmp(h_line(i), "EVT WARN COMPLIANCE", 19) == 0) {
            n++;
        }
    }
    TEST_ASSERT_EQUAL_UINT32(1, n);
    TEST_ASSERT_EQUAL(ST_ARMED, h_state());
    TEST_ASSERT_EQUAL_HEX16(engine_zero_code(), h_dac_a());
}

static void test_arm_needs_session(void)
{
    psim_set_dtr(false);
    h_run_us(50000);
    /* no session: nothing is answered, and ARM would be refused anyway */
    TEST_ASSERT_EQUAL(ST_DISARMED, h_state());
    const char *why;
    TEST_ASSERT_EQUAL(E_PRECOND, inst_arm_check(&why));
    TEST_ASSERT_EQUAL_STRING("session", why);
}

/* ---- disarm (SI-4) ---------------------------------------------------------------- */

static void test_disarm_ramps_and_zeroes_before_heartbeat_stops(void)
{
    arm();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=5"));
    TEST_ASSERT_TRUE(h_wait_line("EVT SRC_DONE", 0, 2000000));
    TEST_ASSERT_DOUBLE_WITHIN(0.01, 5.0, h_vsweep());

    uint64_t t0 = psim_time_us();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("DISARM"));
    TEST_ASSERT_EQUAL(ST_DISARMING, h_state());
    /* run pass by pass: when the last edge happens the DAC must hold the zero code */
    psim_obs_t o;
    uint64_t edges = h_hb_edges();
    uint16_t a_at_last_edge = 0;
    while (h_state() == ST_DISARMING && psim_time_us() - t0 < 2000000) {
        h_run_us(1);
        psim_observe(&o);
        if (o.hb_edges != edges) {
            edges = o.hb_edges;
            a_at_last_edge = h_dac_a();
        }
    }
    TEST_ASSERT_EQUAL(ST_DISARMED, h_state());
    TEST_ASSERT_EQUAL_HEX16(engine_zero_code(), a_at_last_edge);
    /* 5 V at >= 10 V/s takes <= 0.5 s; DISARMED 100 ms after the last edge */
    uint64_t dt = psim_time_us() - t0;
    TEST_ASSERT_TRUE(dt < 700000);
    TEST_ASSERT_FALSE(h_gate());
    TEST_ASSERT_TRUE(fabs(h_vsweep()) < 0.01);
}

/* ---- session loss and host timeout (SI-7) --------------------------------------------- */

static void session_loss_case(void (*drop)(void), const char *reason)
{
    arm();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=-3"));
    h_run_us(500000);
    drop();
    h_run_us(2000);
    TEST_ASSERT_EQUAL(ST_DISARMING, h_state());
    /* heartbeat still running during the ramp (not a health item) */
    uint64_t e0 = h_hb_edges();
    h_run_us(5000);
    TEST_ASSERT_TRUE(h_hb_edges() > e0);
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 1500000));
    TEST_ASSERT_FALSE(h_gate());
    TEST_ASSERT_EQUAL_STRING(reason, disarm_reason_name(inst_disarm_reason()));
}

static void drop_dtr(void)
{
    psim_set_dtr(false);
}

static void drop_vbus(void)
{
    psim_set_vbus_mv(0);
}

static void drop_configured(void)
{
    psim_set_configured(false);
}

static void test_dtr_drop_disarms(void)
{
    session_loss_case(drop_dtr, "session_lost");
    /* reconnect: no re-arm, HELLO reports why */
    size_t from = h_lines();
    psim_set_dtr(true);
    TEST_ASSERT_TRUE(h_wait_line("EVT HELLO", from, 200000));
    char v[32];
    TEST_ASSERT_TRUE(h_kv(h_line(h_find("EVT HELLO", from)), "disarm_reason", v, sizeof v));
    TEST_ASSERT_EQUAL_STRING("session_lost", v);
    h_run_us(500000);
    TEST_ASSERT_EQUAL(ST_DISARMED, h_state());
}

static void test_vbus_drop_disarms(void)
{
    session_loss_case(drop_vbus, "session_lost");
    TEST_ASSERT_FALSE(psim_host_connected());   /* D+ pull-up released */
}

static void test_unconfigure_disarms(void)
{
    session_loss_case(drop_configured, "session_lost");
}

static void test_host_timeout_disarms(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG host_timeout_s=1"));
    arm();
    size_t from = h_lines();
    h_run_us(700000);               /* ~0.8 s since the last byte (ARM) */
    TEST_ASSERT_EQUAL(ST_ARMED, h_state());
    h_run_us(400000);
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, h_find("EVT WARN HOST_TIMEOUT", from));
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 1500000));
    TEST_ASSERT_EQUAL_STRING("host_timeout", disarm_reason_name(inst_disarm_reason()));
}

static void test_any_byte_keeps_host_alive(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG host_timeout_s=1"));
    arm();
    for (int i = 0; i < 6; i++) {
        h_run_us(500000);
        psim_host_give_rx((const uint8_t *)" ", 1);   /* not even a command */
    }
    TEST_ASSERT_EQUAL(ST_ARMED, h_state());
}

/* ---- loop timing, dead-man, watchdog (SI-2, SI-9, SI-11) ------------------------------- */

static void test_slow_pass_latches_loop_overrun(void)
{
    arm();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=2"));
    h_run_us(400000);
    psim_stall_next_pass(1500);     /* > 1.0 ms gap, < 2 ms dead-man */
    h_run_us(2000);
    TEST_ASSERT_EQUAL(ST_FAULT, h_state());
    TEST_ASSERT_EQUAL(F_LOOP_OVERRUN, inst_fault_code());
    TEST_ASSERT_EQUAL_HEX16(0x8000, h_dac_a());
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, h_dac_b());
    h_run_us(150000);
    TEST_ASSERT_FALSE(h_gate());
}

static void test_hang_fires_deadman_and_never_rearms(void)
{
    arm();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=4"));
    h_run_us(600000);
    psim_stall_next_pass(5000);     /* no pass for 5 ms: the dead-man fires at 2 ms */
    h_run_us(10000);
    psim_ext_obs_t x;
    psim_observe_ext(&x);
    TEST_ASSERT_EQUAL_UINT32(1, x.deadman_fires);
    TEST_ASSERT_EQUAL_HEX16(0x8000, h_dac_a());
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 2000000));   /* SI-6 */
    crash_record_t cr;
    TEST_ASSERT_TRUE(port_crash_get(&cr));
    TEST_ASSERT_EQUAL_UINT32(CRASH_DEADMAN, cr.cause);
    TEST_ASSERT_EQUAL_STRING("reset", disarm_reason_name(inst_disarm_reason()));
    h_run_us(300000);
    TEST_ASSERT_FALSE(h_gate());
    TEST_ASSERT_TRUE(fabs(h_vsweep()) < 0.01);
}

static void test_bringup_stack_overflow_crash(void)
{
#if FW_BRINGUP
    /* CRASH STACK: overflow into the MPU guard -> MemManage -> zero pair,
     * crash record (frame not read), reset, DISARMED, never re-armed */
    arm();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=4"));
    h_run_us(600000);
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CRASH STACK"));
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 2000000));
    crash_record_t cr;
    TEST_ASSERT_TRUE(port_crash_get(&cr));
    TEST_ASSERT_EQUAL_UINT32(CRASH_MEMMANAGE, cr.cause);
    TEST_ASSERT_EQUAL_HEX32(0xFFFFFFFFu, cr.pc);
    TEST_ASSERT_EQUAL_HEX16(0x8000, h_dac_a());
    TEST_ASSERT_EQUAL_STRING("reset", disarm_reason_name(inst_disarm_reason()));
    h_run_us(300000);
    TEST_ASSERT_FALSE(h_gate());
#else
    TEST_ASSERT_EQUAL_STRING("ERR UNKNOWN unknown verb", h_cmd("CRASH STACK"));
#endif
}

static void test_bringup_dac_raw_off_during_src_ramp(void)
{
#if FW_BRINGUP
    /* DAC.RAW OFF zeroes the output at once: an SRC ramp cut short ends with
     * status=aborted, not with a compliance report */
    arm();
    size_t from = h_lines();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=5"));
    h_run_us(100000);
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("DAC.RAW OFF"));
    TEST_ASSERT_TRUE(h_wait_line("EVT SRC_DONE", from, 100000));
    TEST_ASSERT_NOT_NULL(strstr(h_line(h_find("EVT SRC_DONE", from)), "status=aborted"));
    TEST_ASSERT_EQUAL(SIZE_MAX, h_find("EVT WARN COMPLIANCE", from));
    TEST_ASSERT_EQUAL_HEX16(engine_zero_code(), h_dac_a());
#endif
}

static void test_disarmed_hang_resets_by_watchdog(void)
{
    port_crash_test(2);             /* hang with interrupts off */
    h_run_us(3000000);
    psim_ext_obs_t x;
    psim_observe_ext(&x);
    TEST_ASSERT_EQUAL_UINT32(1, x.iwdg_fires);
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 2000000));
    TEST_ASSERT_TRUE((port_reset_flags() & RST_IWDG) != 0);
}

static void test_iwdg_kicked_in_fault(void)
{
    arm();
    psim_set_dbg_tim2_frozen(true);
    h_run_us(1000);
    TEST_ASSERT_EQUAL(F_INVARIANT, inst_fault_code());
    psim_set_dbg_tim2_frozen(false);
    h_run_us(3000000);              /* far longer than the IWDG timeout */
    psim_ext_obs_t x;
    psim_observe_ext(&x);
    TEST_ASSERT_EQUAL_UINT32(0, x.iwdg_fires);
    TEST_ASSERT_EQUAL(ST_FAULT, h_state());   /* the latched fault is not lost */
}

/* ---- transports ---------------------------------------------------------------------- */

static void test_dac_spi_failure_faults_and_zeroes(void)
{
    arm();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=3"));
    h_run_us(100000);
    psim_fail_spi1(1000);           /* SPI1 dead: the zero needs the bit-bang path */
    h_run_us(200000);
    TEST_ASSERT_EQUAL(ST_FAULT, h_state());
    TEST_ASSERT_EQUAL(F_DAC_SPI, inst_fault_code());
    TEST_ASSERT_EQUAL_HEX16(0x8000, h_dac_a());
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, h_dac_b());
    dac_stats_t ds;
    dac_get_stats(&ds);
    TEST_ASSERT_TRUE(ds.zero_bitbang >= 1);
}

static void test_adc_drdy_dead_faults(void)
{
    arm();
    adsm_set_drdy_dead(psim_ads(), true);
    h_run_us(300000);
    TEST_ASSERT_EQUAL(ST_FAULT, h_state());
    fault_t f = inst_fault_code();
    TEST_ASSERT_TRUE(f == F_ADC_TIMEOUT || f == F_STALE);
    TEST_ASSERT_EQUAL_HEX16(0x8000, h_dac_a());
}

static void test_adc_spontaneous_reset_detected(void)
{
    arm();
    adsm_spontaneous_reset(psim_ads(), psim_time_us());
    h_run_us(1500000);
    TEST_ASSERT_EQUAL(ST_FAULT, h_state());
    TEST_ASSERT_TRUE(inst_fault_code() == F_ADC_COMM || inst_fault_code() == F_ADC_TIMEOUT);
}

static void test_adc_stuck_dout_detected(void)
{
    arm();
    adsm_set_stuck(psim_ads(), ADSM_STUCK_LOW);
    h_run_us(1500000);
    TEST_ASSERT_EQUAL(ST_FAULT, h_state());
}

static void test_stack_canary_faults(void)
{
    psim_set_stack_ok(false);
    h_run_us(1000);
    TEST_ASSERT_EQUAL(F_STACK, inst_fault_code());
    const char *why;
    psim_set_stack_ok(true);
    h_run_us(200000);
    TEST_ASSERT_EQUAL(E_PRECOND, inst_fault_clear(&why));
    TEST_ASSERT_EQUAL_STRING("stack", why);
}

/* ---- FAULT.CLR ------------------------------------------------------------------------ */

static void test_fault_clear_needs_t_disarm(void)
{
    arm();
    psim_set_dbg_tim2_frozen(true);
    h_run_us(500);
    psim_set_dbg_tim2_frozen(false);
    TEST_ASSERT_EQUAL(ST_FAULT, h_state());
    TEST_ASSERT_EQUAL_STRING("ERR PRECOND wait", h_cmd("FAULT.CLR"));
    h_run_us(120000);
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("FAULT.CLR"));
    TEST_ASSERT_EQUAL(ST_DISARMED, h_state());
    /* no re-arm by itself */
    h_run_us(500000);
    TEST_ASSERT_EQUAL(ST_DISARMED, h_state());
    TEST_ASSERT_FALSE(h_gate());
}

static void test_fault_blocks_commands(void)
{
    psim_set_stack_ok(false);
    h_run_us(1000);
    TEST_ASSERT_EQUAL_STRING("ERR FAULT STACK", h_cmd("ARM"));
    TEST_ASSERT_EQUAL_STRING("ERR FAULT STACK", h_cmd("SWP.RUN"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("STAT?"), "OK STAT state=FAULT", 19));
}

/* ---- compliance ------------------------------------------------------------------------- */

static void test_compliance_stop_zeroes_and_stays_armed(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG i_compliance=0.001"));
    arm();
    size_t from = h_lines();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=3"));   /* 3 V / ~1.1 k = 2.7 mA */
    TEST_ASSERT_TRUE(h_wait_line("EVT WARN COMPLIANCE", from, 2000000));
    TEST_ASSERT_EQUAL(ST_ARMED, h_state());
    TEST_ASSERT_EQUAL_HEX16(engine_zero_code(), h_dac_a());
    /* the trip happened at about 1.1 V (1 mA * 1121 ohm) */
    TEST_ASSERT_TRUE(fabs(h_vsweep()) < 0.01);
}

static void test_compliance_fault_mode(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG i_compliance=0.001 on_compliance=fault"));
    arm();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=3"));
    TEST_ASSERT_TRUE(h_run_until_state(ST_FAULT, 2000000));
    TEST_ASSERT_EQUAL(F_COMPLIANCE, inst_fault_code());
    TEST_ASSERT_EQUAL_HEX16(0x8000, h_dac_a());
}

/* ---- reset (SI-6) ------------------------------------------------------------------------- */

static void test_mcu_reset_while_armed_never_rearms(void)
{
    arm();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=6"));
    h_run_us(800000);
    TEST_ASSERT_DOUBLE_WITHIN(0.02, 6.0, h_vsweep());
    psim_mcu_reset();
    app_init();
    /* boot zeroes the DAC at once, before the pump decays */
    TEST_ASSERT_EQUAL_HEX16(0x8000, h_dac_a());
    TEST_ASSERT_TRUE(fabs(h_vsweep()) < 0.01);
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 2000000));
    h_run_us(1000000);
    TEST_ASSERT_EQUAL(ST_DISARMED, h_state());
    TEST_ASSERT_FALSE(h_gate());
}

static void test_reset_command(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("RESET"));
    h_run_us(500000);
    TEST_ASSERT_TRUE((port_reset_flags() & RST_SFT) != 0);
    TEST_ASSERT_EQUAL(ST_DISARMED, h_state());
}

static void test_v_limit_not_below_held_level(void)
{
    arm();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=2"));
    h_run_us(400000);
    TEST_ASSERT_EQUAL_STRING("ERR RANGE v_limit below the held level", h_cmd("CFG v_limit=1"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG v_limit=2.5"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SRC V=3"), "ERR RANGE", 9));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("CFG adc_rate=50"), "ERR STATE", 9));
}

static void test_flash_save_only_disarmed(void)
{
    arm();
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("CAL.SAVE TS=1"), "ERR STATE", 9));
    psim_obs_t o;
    psim_observe(&o);
    TEST_ASSERT_EQUAL_UINT32(0, o.cal_erases);
}

static void test_bringup_verbs_absent(void)
{
#if !FW_BRINGUP
    arm();
    TEST_ASSERT_EQUAL_STRING("ERR UNKNOWN unknown verb", h_cmd("DAC.RAW 0 0 CONFIRM=UNSAFE"));
    TEST_ASSERT_EQUAL_STRING("ERR UNKNOWN unknown verb", h_cmd("HB.FREQ 1000"));
    TEST_ASSERT_EQUAL_STRING("ERR UNKNOWN unknown verb", h_cmd("CRASH HANG"));
#else
    /* bring-up: DAC.RAW suspends the B invariant only until DAC.RAW OFF */
    arm();
    TEST_ASSERT_EQUAL_STRING("ERR ARG CONFIRM=UNSAFE required (exact case)", h_cmd("DAC.RAW 30000 0 CONFIRM=unsafe"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("DAC.RAW 32768 0 CONFIRM=UNSAFE"));
    TEST_ASSERT_EQUAL_HEX16(0x0000, h_dac_b());
    h_run_us(300000);
    TEST_ASSERT_EQUAL(ST_ARMED, h_state());
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("DAC.RAW OFF"));
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, h_dac_b());
    psim_dac()->b_not_fullscale_loads = 0;   /* the deliberate test load */
#endif
}

/* ---- final firmware audit: regression tests -------------------------------------- */

/* A CAL.SAVE left pending when its session ends must never erase while the
 * output is armed (SI-8): ARM is refused while the job is pending, and the
 * job re-checks SI-8 at the erase itself. */
static void test_calsave_pending_across_session_never_erases_armed(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG adc_rate=2.5"));
    h_run_us(3000000);
    psim_obs_t o;
    psim_observe(&o);
    uint32_t e0 = o.cal_erases;
    h_send("CAL.SAVE TS=1");
    h_run_us(200);
    TEST_ASSERT_TRUE_MESSAGE(engine_job_active(), "CAL.SAVE should wait for the conversion in flight");
    psim_set_dtr(false);                  /* the host closes the port ... */
    h_run_us(1000);
    psim_set_dtr(true);                   /* ... and a new session opens it */
    h_run_us(1000);
    const char *r = h_cmd("ARM");
    TEST_ASSERT_EQUAL_STRING("ERR PRECOND busy", r);
    bool erased_armed = false;
    uint64_t t0 = psim_time_us();
    for (;;) {
        psim_observe(&o);
        if (o.cal_erases != e0 || psim_time_us() - t0 > 2000000u) {
            break;
        }
        if (h_state() != ST_DISARMED || hb_enabled()) {
            erased_armed = true;
        }
        h_run_us(10);
    }
    TEST_ASSERT_TRUE_MESSAGE(o.cal_erases != e0, "the save never ran");
    TEST_ASSERT_FALSE(erased_armed);
    TEST_ASSERT_EQUAL(ST_DISARMED, h_state());
    TEST_ASSERT_FALSE(hb_enabled());
    h_run_us(10000);
    TEST_ASSERT_FALSE(engine_job_active());
    arm();                                /* afterwards ARM works again */
}

/* The record a CAL.SAVE writes is the one RAM held when the command was
 * accepted. The job waits for an idle ADC and can outlive its session; the
 * next session may change RAM meanwhile (here CAL.DEFAULT). That must neither
 * end up in the record under the old command's timestamp nor be reported as
 * "stored". */
static void test_calsave_pending_across_session_saves_its_own_record(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET r_s=118"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG adc_rate=2.5"));
    h_run_us(3000000);
    psim_obs_t o;
    psim_observe(&o);
    uint32_t e0 = o.cal_erases;
    h_send("CAL.SAVE TS=1");
    h_run_us(200);
    TEST_ASSERT_TRUE_MESSAGE(engine_job_active(), "CAL.SAVE should wait for the conversion in flight");
    psim_set_dtr(false);                  /* the host closes the port ... */
    h_run_us(1000);
    psim_set_dtr(true);                   /* ... and a new session opens it */
    h_run_us(1000);
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.DEFAULT"));
    uint64_t t0 = psim_time_us();
    do {
        h_run_us(1000);
        psim_observe(&o);
    } while (o.cal_erases == e0 && psim_time_us() - t0 < 3000000u);
    TEST_ASSERT_TRUE_MESSAGE(o.cal_erases != e0, "the save never ran");
    h_run_us(100000);
    TEST_ASSERT_FALSE(engine_job_active());
    char c[400];
    snprintf(c, sizeof c, "%s", h_cmd("CAL?"));
    /* RAM holds the nominal constants; seq names the record just written */
    TEST_ASSERT_EQUAL_MESSAGE(0, strncmp(c, "OK CAL source=nominal seq=1 ", 28), c);
    TEST_ASSERT_EQUAL(1, h_kv_ll(h_cmd("STAT?"), "cal_seq"));
    psim_mcu_reset();
    app_init();
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 2000000));
    snprintf(c, sizeof c, "%s", h_cmd("CAL?"));
    TEST_ASSERT_EQUAL_MESSAGE(0, strncmp(c, "OK CAL source=stored seq=1 measured=0x10 ", 41), c);
    TEST_ASSERT_NOT_NULL_MESSAGE(strstr(c, " r_s=118.0000"), c);
}

/* The same with CAL.RELOAD in the new session: RAM then holds the older
 * record, which must not be reported as the stored calibration once the
 * pending save has written a newer one. */
static void test_calsave_pending_across_session_then_reload_reports_ram(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET r_s=119"));
    TEST_ASSERT_EQUAL_STRING("OK seq=1", h_cmd("CAL.SAVE TS=1"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET r_s=118"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG adc_rate=2.5"));
    h_run_us(3000000);
    psim_obs_t o;
    psim_observe(&o);
    uint32_t e0 = o.cal_erases;
    h_send("CAL.SAVE TS=2");
    h_run_us(200);
    TEST_ASSERT_TRUE_MESSAGE(engine_job_active(), "CAL.SAVE should wait for the conversion in flight");
    psim_set_dtr(false);
    h_run_us(1000);
    psim_set_dtr(true);
    h_run_us(1000);
    TEST_ASSERT_EQUAL_STRING("OK source=stored seq=1", h_cmd("CAL.RELOAD"));
    uint64_t t0 = psim_time_us();
    do {
        h_run_us(1000);
        psim_observe(&o);
    } while (o.cal_erases == e0 && psim_time_us() - t0 < 3000000u);
    TEST_ASSERT_TRUE_MESSAGE(o.cal_erases != e0, "the save never ran");
    h_run_us(100000);
    char c[400];
    snprintf(c, sizeof c, "%s", h_cmd("CAL?"));
    TEST_ASSERT_EQUAL_MESSAGE(0, strncmp(c, "OK CAL source=ram seq=2 ", 24), c);
    TEST_ASSERT_NOT_NULL_MESSAGE(strstr(c, " r_s=119.0000"), c);
    psim_mcu_reset();
    app_init();
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 2000000));
    snprintf(c, sizeof c, "%s", h_cmd("CAL?"));
    TEST_ASSERT_EQUAL_MESSAGE(0, strncmp(c, "OK CAL source=stored seq=2 ", 27), c);
    TEST_ASSERT_NOT_NULL_MESSAGE(strstr(c, " r_s=118.0000"), c);
}

/* RESET is a DISARMED/FAULT command; an ARM sent before the reset executes
 * is refused, so the reset never hits an armed output. */
static void test_arm_refused_while_reset_pending(void)
{
    psim_ext_obs_t x;
    psim_observe_ext(&x);
    uint32_t r0 = x.mcu_resets;
    size_t from = h_lines();
    h_send("RESET");
    h_send("ARM");
    bool armed_before_reset = false;
    uint64_t t0 = psim_time_us();
    for (;;) {
        psim_observe_ext(&x);
        if (x.mcu_resets != r0 || psim_time_us() - t0 > 1000000u) {
            break;
        }
        inst_state_t st = h_state();
        armed_before_reset = armed_before_reset || st == ST_ARMING || st == ST_ARMED || h_gate();
        h_run_us(5);
    }
    TEST_ASSERT_TRUE(x.mcu_resets != r0);
    TEST_ASSERT_FALSE(armed_before_reset);
    size_t ok = h_find("OK", from);
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, ok);
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, h_find("ERR STATE reset pending", ok + 1));
}

/* RESET writes the zero pair before the MCU reset (port_system_reset, as on
 * the target), whatever the DAC held: here it is corrupted by hand while
 * DISARMED, as a stray write would. */
static void test_reset_writes_the_zero_pair_first(void)
{
    dac_stats_t s0, s1;
    dac_get_stats(&s0);
    h_send("RESET");
    uint64_t t0 = psim_time_us();
    while (!psim_reset_requested() && psim_time_us() - t0 < 1000000u) {
        psim_dac()->dac[0] = 0x9000u;   /* the zero pair must be rewritten */
        app_pass();
    }
    TEST_ASSERT_TRUE(psim_reset_requested());
    dac_get_stats(&s1);
    TEST_ASSERT_EQUAL_UINT32(s0.zero_safe + 1u, s1.zero_safe);
    TEST_ASSERT_EQUAL_HEX16(0x8000, psim_dac()->dac[0]);
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, psim_dac()->dac[1]);
    psim_mcu_reset();
    app_init();
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 2000000));
}

#if FW_BRINGUP
/* SI-8 at the erase itself. A CAL.SAVE that outlives its session waits for
 * the ADC conversion in flight; a bring-up HB.EDGE in the new session (a
 * DISARMED command) makes an edge shortly before that conversion ends. The
 * save job re-checks SI-8 there and gives up instead of erasing within
 * T_DISARM of an edge. At 2.5 SPS one conversion takes t18 = 400 ms. */
static void test_bringup_calsave_rechecks_si8_at_the_erase(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG adc_rate=2.5"));
    h_run_us(3000000);                       /* SELFCAL at 2.5 SPS, ~1.2 s */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET r_s=119"));
    psim_obs_t o0;
    psim_observe(&o0);
    /* just after a conversion completes, the monitor starts the next one,
     * which the job will wait for */
    uint32_t c0 = adsm_conversions(psim_ads());
    for (int k = 0; k < 20000 && adsm_conversions(psim_ads()) == c0; k++) {
        h_run_us(100);
    }
    h_run_us(10000);
    TEST_ASSERT_EQUAL(ADS_CONVERTING, ads_state());
    h_send("CAL.SAVE TS=1700000000");
    h_run_us(2000);
    psim_set_dtr(false);                     /* the host leaves: the job stays */
    h_run_us(5000);
    psim_set_dtr(true);
    h_run_us(5000);
    h_run_us(320000);                        /* ~340 ms into the conversion */
    TEST_ASSERT_EQUAL(ADS_CONVERTING, ads_state());
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("HB.EDGE"));
    h_run_us(600000);
    psim_obs_t o1;
    psim_observe(&o1);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(o0.cal_erases, o1.cal_erases, "erased within T_DISARM of an edge");
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("HB.HOLD OFF"));
}
#endif

/* ARM whose zero write fails latches FAULT and must not answer OK. */
static void test_arm_answers_fault_when_zero_write_fails(void)
{
    psim_fail_spi1(10);
    const char *r = h_cmd("ARM");
    TEST_ASSERT_TRUE_MESSAGE(strncmp(r, "ERR FAULT", 9) == 0, r);
    TEST_ASSERT_EQUAL(ST_FAULT, h_state());
    TEST_ASSERT_EQUAL_HEX16(0x8000, h_dac_a());
    TEST_ASSERT_FALSE(h_gate());
}

/* A non-ADC fault during SELFTEST clears POST; FAULT.CLR re-runs it, so ARM is
 * not left refused with 'post'. */
static void test_fault_during_selftest_reruns_post_on_clear(void)
{
    h_send("SELFTEST");
    h_run_us(2000);
    TEST_ASSERT_TRUE(engine_job_active());
    psim_set_dbg_tim2_frozen(true);       /* F_INVARIANT while the self-test runs */
    h_run_us(200);
    TEST_ASSERT_EQUAL(ST_FAULT, h_state());
    psim_set_dbg_tim2_frozen(false);
    h_run_us(200000);
    TEST_ASSERT_FALSE(engine_post_passed());
    TEST_ASSERT_EQUAL_STRING("ERR PRECOND adc", h_cmd("FAULT.CLR"));   /* POST re-run */
    h_run_us(3000000);
    TEST_ASSERT_TRUE(engine_post_passed());
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("FAULT.CLR"));
    h_run_us(1500000);
    arm();
}

/* A conversion in flight when a sweep is aborted belongs to no point: it must
 * not become the first sample of the next sweep (stale value, and a t_i
 * before t_set that breaks the host's time unwrap). */
static void test_abort_then_rerun_discards_orphan_conversion(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG adc_rate=10 settle_us=100000 first_settle_us=100000 n_avg=1"));
    h_run_us(2000000);
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SWP.LIST.CLR"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.LIST.ADD 0.5 0.5 0.5 0.5 0.5 0.5"), "OK", 2));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.LIST.USE"), "OK SWP", 6));
    h_run_us(1500000);
    arm();
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.RUN"), "OK id=", 6));
    size_t k0 = h_lines();
    TEST_ASSERT_TRUE(h_wait_line("PT 1,", k0, 5000000));
    size_t k1 = h_find("PT 1,", k0) + 1;
    TEST_ASSERT_TRUE(h_wait_line("PT 1,", k1, 5000000));
    h_run_us(175000);                     /* the next point's I conversion is in flight */
    h_send("SWP.ABORT");
    h_send("SWP.LIST.CLR");
    h_send("SWP.LIST.ADD 0.6 0.6 0.6");
    h_send("SWP.LIST.USE");
    size_t from = h_lines();
    h_send("SWP.RUN");
    TEST_ASSERT_TRUE(h_wait_line("PT 2,", from, 5000000));
    size_t k = h_find("PT 2,", from);
    unsigned long sid, seq, idx, seg, t_set, t_i, t_v;
    TEST_ASSERT_EQUAL(7, sscanf(h_line(k), "PT %lu,%lu,%lu,%lu,%lu,%lu,%lu",
                                &sid, &seq, &idx, &seg, &t_set, &t_i, &t_v));
    TEST_ASSERT_EQUAL_UINT32(0, idx);
    TEST_ASSERT_TRUE_MESSAGE(t_i >= t_set && t_i < 2000000ul, h_line(k));
    TEST_ASSERT_TRUE_MESSAGE(t_v >= t_set && t_v < 2000000ul, h_line(k));
}

/* Bring-up: HB.HOLD/HB.EDGE drive a static PC5 level; repeated level changes
 * from the host must not pump the reference gate closed. */
static void test_bringup_hb_hold_cannot_pump_the_gate(void)
{
#if FW_BRINGUP
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("HB.HOLD HIGH"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("HB.HOLD LOW"));
    TEST_ASSERT_EQUAL_STRING("ERR STATE edge within T_DISARM of the last one", h_cmd("HB.HOLD HIGH"));
    TEST_ASSERT_EQUAL_STRING("ERR STATE edge within T_DISARM of the last one", h_cmd("HB.EDGE"));
    for (int i = 0; i < 200; i++) {
        h_send("HB.HOLD HIGH");
        h_send("HB.HOLD LOW");
    }
    bool gate_seen = false;
    uint64_t t0 = psim_time_us();
    while (psim_time_us() - t0 < 300000u) {
        h_run_us(5);
        gate_seen = gate_seen || h_gate();
    }
    TEST_ASSERT_FALSE(gate_seen);
    h_run_us(100000);                     /* after T_DISARM a single edge is allowed again */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("HB.HOLD HIGH"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("HB.HOLD OFF"));
#else
    TEST_IGNORE_MESSAGE("bring-up only");
#endif
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_boot_ends_disarmed_with_zero_pair);
    RUN_TEST(test_arm_sequence_and_timing);
    RUN_TEST(test_arm_preconditions);
    RUN_TEST(test_arm_with_charged_cell_is_not_blocked);
    RUN_TEST(test_arm_at_slow_data_rates);
    RUN_TEST(test_arm_needs_session);
    RUN_TEST(test_disarm_ramps_and_zeroes_before_heartbeat_stops);
    RUN_TEST(test_dtr_drop_disarms);
    RUN_TEST(test_vbus_drop_disarms);
    RUN_TEST(test_unconfigure_disarms);
    RUN_TEST(test_host_timeout_disarms);
    RUN_TEST(test_any_byte_keeps_host_alive);
    RUN_TEST(test_slow_pass_latches_loop_overrun);
    RUN_TEST(test_hang_fires_deadman_and_never_rearms);
    RUN_TEST(test_bringup_stack_overflow_crash);
    RUN_TEST(test_bringup_dac_raw_off_during_src_ramp);
    RUN_TEST(test_disarmed_hang_resets_by_watchdog);
    RUN_TEST(test_iwdg_kicked_in_fault);
    RUN_TEST(test_dac_spi_failure_faults_and_zeroes);
    RUN_TEST(test_adc_drdy_dead_faults);
    RUN_TEST(test_adc_spontaneous_reset_detected);
    RUN_TEST(test_adc_stuck_dout_detected);
    RUN_TEST(test_stack_canary_faults);
    RUN_TEST(test_fault_clear_needs_t_disarm);
    RUN_TEST(test_fault_blocks_commands);
    RUN_TEST(test_compliance_stop_zeroes_and_stays_armed);
    RUN_TEST(test_compliance_fault_mode);
    RUN_TEST(test_mcu_reset_while_armed_never_rearms);
    RUN_TEST(test_reset_command);
    RUN_TEST(test_v_limit_not_below_held_level);
    RUN_TEST(test_flash_save_only_disarmed);
    RUN_TEST(test_bringup_verbs_absent);
    RUN_TEST(test_compliance_trip_during_disarm_ramp_completes_disarm);
    RUN_TEST(test_cell_tripping_compliance_on_disarm_completes_disarm);
    RUN_TEST(test_compliance_during_src_ramp_is_reported);
    RUN_TEST(test_fault_during_sweep_goes_straight_to_fault);
    RUN_TEST(test_sweep_refused_during_src_ramp);
    RUN_TEST(test_dac_scrub_only_after_the_scrub_period);
    RUN_TEST(test_compliance_at_zero_is_reported_once);
    RUN_TEST(test_calsave_pending_across_session_never_erases_armed);
    RUN_TEST(test_calsave_pending_across_session_saves_its_own_record);
    RUN_TEST(test_calsave_pending_across_session_then_reload_reports_ram);
    RUN_TEST(test_arm_refused_while_reset_pending);
    RUN_TEST(test_reset_writes_the_zero_pair_first);
    RUN_TEST(test_arm_answers_fault_when_zero_write_fails);
    RUN_TEST(test_fault_during_selftest_reruns_post_on_clear);
    RUN_TEST(test_abort_then_rerun_discards_orphan_conversion);
    RUN_TEST(test_bringup_hb_hold_cannot_pump_the_gate);
#if FW_BRINGUP
    RUN_TEST(test_bringup_calsave_rechecks_si8_at_the_erase);
#endif
    return UNITY_END();
}
