/*
 * test_ads1256.c — ADS1256 driver (drivers/ads1256.c) against the
 * register-level model (sim/model_ads1256.c) through the virtual-clock test
 * port (support/ads1256/tp_ads.c).
 *
 * The model asserts the SBAS288K timing (t6, t10, t11, SCLK rate, reads
 * while not ready, commands while busy); every test checks that no
 * violation was recorded. Inputs are set per AIN pin so each channel reads a
 * distinct, known code: a sample that belonged to the previous channel would
 * be detected.
 */
#include "unity.h"

#include "ads1256.h"
#include "ads1256/tp_ads.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

static double g_ain[9];

static double input(void *ctx, int ain, uint64_t t_us)
{
    (void)ctx;
    (void)t_us;
    return (ain >= 0 && ain <= 8) ? g_ain[ain] : 0.0;
}

/* expected code for V(p) - V(n) at the given PGA gain */
static int32_t expect_code(double vdiff, double gain)
{
    double c = vdiff * gain / 5.0 * 8388607.0;
    if (c > 8388607.0) {
        c = 8388607.0;
    }
    if (c < -8388608.0) {
        c = -8388608.0;
    }
    return (int32_t)lround(c);
}

static const ads_rate_t *rate_sps(uint32_t sps_x10)
{
    const ads_rate_t *r = ads_rate_by_sps_x10(sps_x10);
    TEST_ASSERT_NOT_NULL(r);
    return r;
}

static void assert_no_violations(void)
{
    adsm_violations_t v;
    adsm_get_violations(tp_model(), &v);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, v.sclk, "SCLK");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, v.t6, "t6");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, v.t11, "t11");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, v.t10, "t10");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, v.read_not_ready, "read while not ready");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, v.busy, "command while busy");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, v.unknown_cmd, "unknown command");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, v.short_read, "short read");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, v.after_sync, "command other than WAKEUP after SYNC");
}

static bool run_until_idle(uint64_t timeout_us)
{
    uint64_t end = tp_now() + timeout_us;
    while (tp_now() < end) {
        tp_pass();
        if (ads_state() == ADS_IDLE) {
            return true;
        }
        if (ads_state() == ADS_ERROR) {
            return false;
        }
    }
    return false;
}

static bool convert(ads_mux_t mux, ads_sample_t *s, uint64_t timeout_us)
{
    TEST_ASSERT_TRUE(ads_start(mux));
    uint64_t end = tp_now() + timeout_us;
    while (tp_now() < end) {
        tp_pass();
        if (ads_take(s)) {
            return true;
        }
        if (ads_state() == ADS_ERROR) {
            return false;
        }
    }
    return false;
}

static void init_at(uint32_t sps_x10, uint8_t pga)
{
    ads_cfg_t c = { rate_sps(sps_x10)->drate, pga };
    ads_init_begin(&c);
    TEST_ASSERT_TRUE_MESSAGE(run_until_idle(3000000), "init did not complete");
}

void setUp(void)
{
    for (int i = 0; i < 9; i++) {
        g_ain[i] = 0.0;
    }
    g_ain[0] = 2.5 + 0.3;     /* I channel: +0.3 V                    */
    g_ain[2] = 2.5 - 0.7;     /* V channel: -0.7 V                    */
    g_ain[8] = 2.5;           /* AINCOM = VREF                         */
    tp_setup(input, NULL);
    adsm_set_noise(tp_model(), 0.0, 1);
    tp_set_pass(5, 150, 7);
}

void tearDown(void)
{
    tp_teardown();
}

/* ---- pure helpers ------------------------------------------------------------- */

static void test_rate_table_and_deadlines(void)
{
    size_t n = 0;
    const ads_rate_t *t = ads_rate_table(&n);
    TEST_ASSERT_EQUAL(13, n);
    for (size_t i = 0; i < n; i++) {
        TEST_ASSERT_EQUAL_PTR(&t[i], ads_rate_by_code(t[i].drate));
        TEST_ASSERT_EQUAL_PTR(&t[i], ads_rate_by_sps_x10(t[i].sps_x10));
        uint32_t period = ads_period_us(&t[i]);
        TEST_ASSERT_TRUE(period * t[i].sps_x10 >= 10000000u);
        /* floor(1/DR) - (t6 + 24 SCLK + t17 + 10 us) = floor(1/DR) - 36 */
        TEST_ASSERT_EQUAL_UINT32(10000000u / t[i].sps_x10 - 36u, ads_read_deadline_us(&t[i]));
        TEST_ASSERT_TRUE(t[i].t18_us > period);   /* settling > one period */
    }
    /* 3750 SPS: deadline above the 150 us pass budget; 7500+ not offered */
    TEST_ASSERT_TRUE(ads_read_deadline_us(rate_sps(37500)) > 150u);
    TEST_ASSERT_NULL(ads_rate_by_sps_x10(75000));
    TEST_ASSERT_NULL(ads_rate_by_code(0xD0));
}

static void test_sign_extension(void)
{
    TEST_ASSERT_EQUAL_INT32(8388607, ads_sign_extend(0x7FFFFFu));
    TEST_ASSERT_EQUAL_INT32(-8388608, ads_sign_extend(0x800000u));
    TEST_ASSERT_EQUAL_INT32(-1, ads_sign_extend(0xFFFFFFu));
    TEST_ASSERT_EQUAL_INT32(0, ads_sign_extend(0u));
    TEST_ASSERT_EQUAL_INT32(1, ads_sign_extend(1u));
}

/* ---- init ---------------------------------------------------------------------------- */

static void test_init_configures_and_verifies(void)
{
    init_at(10000, ADS_PGA_2);
    uint8_t regs[11];
    adsm_get_regs(tp_model(), regs);
    TEST_ASSERT_EQUAL_HEX8(0x00, regs[0] & 0x0Eu);   /* MSB first, ACAL off, BUFEN off */
    TEST_ASSERT_EQUAL_HEX8(0x01, regs[2]);           /* CLKOUT off, SDCS off, PGA 2 */
    TEST_ASSERT_EQUAL_HEX8(rate_sps(10000)->drate, regs[3]);
    TEST_ASSERT_EQUAL_HEX8(0x00, regs[4]);           /* D0-D3 outputs, low */
    TEST_ASSERT_EQUAL_UINT8(3, ads_last_id());
    tp_obs_t o;
    tp_observe(&o);
    TEST_ASSERT_TRUE(o.reset_pulses >= 1);
    TEST_ASSERT_TRUE(o.min_reset_low_us >= 1);
    TEST_ASSERT_TRUE(o.max_txn_per_service <= 1);
    TEST_ASSERT_TRUE(o.max_service_us <= 70);
    TEST_ASSERT_EQUAL_UINT32(0, o.xfer_without_cs);
    TEST_ASSERT_TRUE(adsm_selfcals(tp_model()) >= 2);   /* automatic + explicit */
    assert_no_violations();
}

static void test_reconfigure_only_from_idle(void)
{
    ads_cfg_t c1 = { rate_sps(500)->drate, ADS_PGA_1 };
    ads_cfg_t c2 = { rate_sps(10000)->drate, ADS_PGA_2 };
    ads_init_begin(&c2);
    TEST_ASSERT_FALSE(ads_reconfigure(&c1));   /* refused while initialising */
    TEST_ASSERT_TRUE(run_until_idle(3000000));
    TEST_ASSERT_TRUE(ads_reconfigure(&c1));
    TEST_ASSERT_EQUAL(ADS_INIT, ads_state());
    TEST_ASSERT_TRUE(run_until_idle(2000000));
    uint8_t regs[11];
    adsm_get_regs(tp_model(), regs);
    TEST_ASSERT_EQUAL_HEX8(0x00, regs[2]);
    TEST_ASSERT_EQUAL_HEX8(rate_sps(500)->drate, regs[3]);
    assert_no_violations();
}

/* ---- conversions -------------------------------------------------------------------- */

static void test_channels_read_their_own_inputs(void)
{
    init_at(10000, ADS_PGA_2);
    for (int k = 0; k < 40; k++) {
        ads_mux_t m = (k % 3 == 0) ? ADS_MUX_V : ADS_MUX_I;
        ads_sample_t s;
        TEST_ASSERT_TRUE(convert(m, &s, 100000));
        TEST_ASSERT_EQUAL(m, s.mux);
        int32_t e = (m == ADS_MUX_I) ? expect_code(0.3, 2.0) : expect_code(-0.7, 2.0);
        TEST_ASSERT_INT32_WITHIN(2, e, s.code);
        TEST_ASSERT_TRUE(s.t_drdy_us - s.t_start_us >= rate_sps(10000)->t18_us * 9u / 10u);
        TEST_ASSERT_TRUE(s.t_read_us >= s.t_drdy_us);
    }
    assert_no_violations();
}

static void test_selfcheck_inputs_at_pga1(void)
{
    init_at(10000, ADS_PGA_1);
    ads_sample_t s;
    TEST_ASSERT_TRUE(convert(ADS_MUX_NEGREF, &s, 100000));
    TEST_ASSERT_INT32_WITHIN(2, -4194304, s.code);       /* AIN1 - AINCOM = -VREF */
    TEST_ASSERT_TRUE(convert(ADS_MUX_ZERO, &s, 100000));
    TEST_ASSERT_INT32_WITHIN(2, 0, s.code);
}

static void test_all_rates_random_pass_lengths(void)
{
    static const uint32_t rates[] = { 500, 1000, 5000, 10000, 20000, 37500 };
    for (size_t r = 0; r < sizeof rates / sizeof rates[0]; r++) {
        init_at(rates[r], ADS_PGA_2);
        for (int k = 0; k < 12; k++) {
            ads_sample_t s;
            ads_mux_t m = (k & 1) ? ADS_MUX_V : ADS_MUX_I;
            TEST_ASSERT_TRUE(convert(m, &s, 2000000));
            int32_t e = (m == ADS_MUX_I) ? expect_code(0.3, 2.0) : expect_code(-0.7, 2.0);
            TEST_ASSERT_INT32_WITHIN(2, e, s.code);
        }
    }
    assert_no_violations();
}

static void test_clip_flag(void)
{
    g_ain[0] = 2.5 + 3.0;       /* beyond +FS at PGA 2 */
    init_at(10000, ADS_PGA_2);
    ads_sample_t s;
    TEST_ASSERT_TRUE(convert(ADS_MUX_I, &s, 100000));
    TEST_ASSERT_EQUAL_INT32(8388607, s.code);
    TEST_ASSERT_TRUE(s.clipped);
}

/* ---- edge validity and read deadline ------------------------------------------------- */

static void test_stale_pending_edge_rejected(void)
{
    init_at(10000, ADS_PGA_2);
    tp_set_arm_clears_pending(false);    /* the previous conversion's edge stays pending */
    for (int k = 0; k < 10; k++) {
        ads_sample_t s;
        ads_mux_t m = (k & 1) ? ADS_MUX_V : ADS_MUX_I;
        TEST_ASSERT_TRUE(convert(m, &s, 100000));
        int32_t e = (m == ADS_MUX_I) ? expect_code(0.3, 2.0) : expect_code(-0.7, 2.0);
        TEST_ASSERT_INT32_WITHIN(2, e, s.code);   /* never the previous channel's data */
    }
    ads_stats_t st;
    ads_get_stats(&st);
    TEST_ASSERT_TRUE(st.stale_edges >= 1);
    assert_no_violations();
}

static void test_glitch_edge_rejected(void)
{
    init_at(1000, ADS_PGA_2);           /* 100 SPS: long t18 */
    TEST_ASSERT_TRUE(ads_start(ADS_MUX_I));
    for (int k = 0; k < 20; k++) {
        tp_pass();
    }
    tp_inject_edge();                   /* a spurious edge right after WAKEUP */
    ads_sample_t s;
    uint64_t end = tp_now() + 500000;
    bool got = false;
    while (tp_now() < end && !(got = ads_take(&s))) {
        tp_pass();
    }
    TEST_ASSERT_TRUE(got);
    TEST_ASSERT_INT32_WITHIN(2, expect_code(0.3, 2.0), s.code);
    TEST_ASSERT_TRUE(s.t_drdy_us - s.t_start_us >= rate_sps(1000)->t18_us * 9u / 10u);
}

static void test_late_read_waits_for_next_edge(void)
{
    init_at(37500, ADS_PGA_2);          /* 3750 SPS: deadline 230 us */
    tp_set_pass(150, 300, 3);           /* passes often miss the read window */
    const uint32_t deadline = ads_read_deadline_us(rate_sps(37500));
    uint32_t late = 0;
    for (int k = 0; k < 60; k++) {
        ads_sample_t s;
        ads_mux_t m = (k & 1) ? ADS_MUX_V : ADS_MUX_I;
        if (!convert(m, &s, 100000)) {
            TEST_ASSERT_EQUAL(ADS_ERR_TIMEOUT, ads_error());   /* > ADS_MAX_LATE misses */
            init_at(37500, ADS_PGA_2);
            continue;
        }
        /* a read is only issued within the deadline of the accepted edge */
        TEST_ASSERT_TRUE(s.t_read_us - s.t_drdy_us <= deadline);
        int32_t e = (m == ADS_MUX_I) ? expect_code(0.3, 2.0) : expect_code(-0.7, 2.0);
        TEST_ASSERT_INT32_WITHIN(2, e, s.code);
        late += s.late ? 1u : 0u;
    }
    TEST_ASSERT_TRUE_MESSAGE(late > 0, "no late sample produced");
    adsm_violations_t v;
    adsm_get_violations(tp_model(), &v);
    TEST_ASSERT_EQUAL_UINT32(0, v.read_not_ready);
}

static void test_read_running_into_the_next_update_is_discarded(void)
{
    /* Passes that start reads close to the deadline, plus an 8 us interrupt
     * between RDATA and the data bytes (and up to 3 us EXTI latency, the bound
     * with /DRDY above USB in the NVIC): a read that ends inside t17 is
     * discarded and the next edge is used, never delivered as a valid sample. */
    static const uint32_t lat[] = { 0u, 3u };
    for (size_t li = 0; li < sizeof lat / sizeof lat[0]; li++) {
        tp_setup(input, NULL);
        init_at(37500, ADS_PGA_2);
        tp_set_pass(150, 300, 7);
        tp_set_read_preempt_us(8);
        tp_set_drdy_latency_us(lat[li]);
        adsm_violations_t v0;
        adsm_get_violations(tp_model(), &v0);
        uint32_t prev_bad = v0.read_not_ready;
        uint32_t got = 0, bad_delivered = 0;
        for (int k = 0; k < 2000; k++) {
            ads_sample_t s;
            ads_mux_t m = (k & 1) ? ADS_MUX_V : ADS_MUX_I;
            if (!convert(m, &s, 100000)) {
                TEST_ASSERT_EQUAL(ADS_ERR_TIMEOUT, ads_error());   /* > ADS_MAX_LATE misses */
                tp_set_read_preempt_us(0);
                init_at(37500, ADS_PGA_2);
                tp_set_read_preempt_us(8);
                adsm_violations_t vr;
                adsm_get_violations(tp_model(), &vr);
                prev_bad = vr.read_not_ready;
                continue;
            }
            got++;
            adsm_violations_t v;
            adsm_get_violations(tp_model(), &v);
            if (v.read_not_ready != prev_bad && !s.late) {
                bad_delivered++;   /* this very read overlapped t17 */
            }
            prev_bad = v.read_not_ready;
            int32_t e = (m == ADS_MUX_I) ? expect_code(0.3, 2.0) : expect_code(-0.7, 2.0);
            TEST_ASSERT_INT32_WITHIN(2, e, s.code);
        }
        TEST_ASSERT_TRUE(got > 1900u);
        TEST_ASSERT_EQUAL_UINT32(0, bad_delivered);
        ads_stats_t st;
        ads_get_stats(&st);
        TEST_ASSERT_TRUE_MESSAGE(st.read_overruns > 0u, "the probe never overran a read");
    }
}

static void test_preemption_by_new_start(void)
{
    init_at(1000, ADS_PGA_2);
    TEST_ASSERT_TRUE(ads_start(ADS_MUX_I));
    for (int k = 0; k < 50; k++) {
        tp_pass();
    }
    ads_sample_t s;
    TEST_ASSERT_TRUE(convert(ADS_MUX_V, &s, 500000));   /* preempts the I conversion */
    TEST_ASSERT_EQUAL(ADS_MUX_V, s.mux);
    TEST_ASSERT_INT32_WITHIN(2, expect_code(-0.7, 2.0), s.code);
    assert_no_violations();
}

/* One conversion of AIN0; once its first /DRDY edge is latched (by the
 * emulated EXTI) the main loop stalls for stall_us while interrupts keep
 * running. Returns true with a sample, false on a driver error. */
static bool convert_with_stall(uint32_t sps_x10, uint32_t stall_us, ads_sample_t *s,
                               uint64_t *t_stall_end)
{
    tp_setup(input, NULL);
    adsm_set_noise(tp_model(), 0.0, 1);
    tp_set_pass(5, 50, 3);
    init_at(sps_x10, ADS_PGA_2);
    TEST_ASSERT_TRUE(ads_start(ADS_MUX_I));
    bool stalled = false;
    uint64_t end = tp_now() + 10u * ads_period_us(rate_sps(sps_x10)) + 100000u;
    while (tp_now() < end) {
        uint64_t t;
        if (!stalled && tp_edge_latched(&t)) {
            tp_advance(stall_us);
            stalled = true;
            if (t_stall_end != NULL) {
                *t_stall_end = tp_now();
            }
        }
        tp_pass();
        if (ads_take(s)) {
            return true;
        }
        if (ads_state() == ADS_ERROR) {
            return false;
        }
    }
    TEST_FAIL_MESSAGE("neither a sample nor an error");
    return false;
}

/* A main-loop stall of one to two data periods after a /DRDY edge: the edge
 * one period later has gone by when the missed deadline is noticed, and the
 * re-arm clears it, so the next usable edge is two periods after the first.
 * That is one late sample of the right channel, not an ADC timeout fault. */
static void test_stall_after_an_edge_gives_a_late_sample(void)
{
    static const uint32_t rates[] = { 37500u, 20000u, 10000u, 5000u, 1000u, 600u, 500u,
                                      300u, 250u, 150u, 100u, 50u, 25u };   /* all 13 v1 rates */
    for (size_t i = 0; i < sizeof rates / sizeof rates[0]; i++) {
        uint32_t p = ads_period_us(rate_sps(rates[i]));
        const uint32_t stalls[] = { p / 2u, p + p / 10u, p + p / 2u, p + (9u * p) / 10u };
        for (size_t k = 0; k < sizeof stalls / sizeof stalls[0]; k++) {
            ads_sample_t s;
            char msg[96];
            bool ok = convert_with_stall(rates[i], stalls[k], &s, NULL);
            snprintf(msg, sizeof msg, "%u.%u SPS, stall %u us: error %d", (unsigned)(rates[i] / 10u),
                     (unsigned)(rates[i] % 10u), (unsigned)stalls[k], (int)ads_error());
            TEST_ASSERT_TRUE_MESSAGE(ok, msg);
            /* half a period is within the read deadline: an ordinary sample */
            TEST_ASSERT_EQUAL_MESSAGE(k > 0u, s.late, msg);
            TEST_ASSERT_EQUAL(ADS_MUX_I, s.mux);
            TEST_ASSERT_INT32_WITHIN(2, expect_code(0.3, 2.0), s.code);
            assert_no_violations();
        }
    }
}

/* ...while a /DRDY that dies during the stall is still a timeout, within
 * 3/2 P + 2 ms (and one pass) of the end of the stall. */
static void test_drdy_dead_after_a_stall_times_out(void)
{
    static const uint32_t rates[] = { 10000u, 1000u, 100u };
    for (size_t i = 0; i < sizeof rates / sizeof rates[0]; i++) {
        uint32_t p = ads_period_us(rate_sps(rates[i]));
        tp_setup(input, NULL);
        tp_set_pass(5, 50, 3);
        init_at(rates[i], ADS_PGA_2);
        TEST_ASSERT_TRUE(ads_start(ADS_MUX_I));
        uint64_t t_end = 0;
        bool stalled = false;
        while (tp_now() < 5000000u && ads_state() != ADS_ERROR) {
            uint64_t t;
            if (!stalled && tp_edge_latched(&t)) {
                tp_advance(p + p / 2u);
                adsm_set_drdy_dead(tp_model(), true);
                t_end = tp_now();
                stalled = true;
            }
            tp_pass();
            ads_sample_t s;
            TEST_ASSERT_FALSE(ads_take(&s));
        }
        TEST_ASSERT_EQUAL(ADS_ERROR, ads_state());
        TEST_ASSERT_EQUAL(ADS_ERR_TIMEOUT, ads_error());
        TEST_ASSERT_TRUE(tp_now() - t_end <= p * 3u / 2u + 2000u + 1000u);
    }
}

/* A SELFCAL the part ignores leaves /DRDY to old data or to the conversion
 * WAKEUP started, long before the Table 21 calibration time: init and
 * reconfigure report it (after one retry) instead of running on an old
 * calibration. A real calibration is never mistaken for one. */
static void test_ignored_selfcal_is_detected(void)
{
    adsm_set_ignore_selfcal(tp_model(), true);
    ads_cfg_t c = { rate_sps(10000)->drate, ADS_PGA_2 };
    ads_init_begin(&c);
    TEST_ASSERT_FALSE(run_until_idle(3000000));
    TEST_ASSERT_EQUAL(ADS_ERR_TIMEOUT, ads_error());
    adsm_set_ignore_selfcal(tp_model(), false);
    init_at(10000, ADS_PGA_2);
    assert_no_violations();
    adsm_set_ignore_selfcal(tp_model(), true);
    ads_cfg_t c2 = { rate_sps(25u)->drate, ADS_PGA_2 };
    TEST_ASSERT_TRUE(ads_reconfigure(&c2));
    TEST_ASSERT_FALSE(run_until_idle(5000000));
    TEST_ASSERT_EQUAL(ADS_ERR_TIMEOUT, ads_error());
    adsm_set_ignore_selfcal(tp_model(), false);
    init_at(25u, ADS_PGA_2);
}

/* ---- failures ------------------------------------------------------------------------------ */

static void test_drdy_dead_times_out(void)
{
    init_at(10000, ADS_PGA_2);
    adsm_set_drdy_dead(tp_model(), true);
    TEST_ASSERT_TRUE(ads_start(ADS_MUX_I));
    uint64_t t0 = tp_now();
    while (ads_state() != ADS_ERROR && tp_now() - t0 < 100000) {
        tp_pass();
    }
    TEST_ASSERT_EQUAL(ADS_ERROR, ads_state());
    TEST_ASSERT_EQUAL(ADS_ERR_TIMEOUT, ads_error());
    uint64_t limit = rate_sps(10000)->t18_us * 3u / 2u + 2000u + 1000u;
    TEST_ASSERT_TRUE(tp_now() - t0 <= limit);
}

static void test_spi_failure_reported(void)
{
    init_at(10000, ADS_PGA_2);
    tp_fail_xfers(1);
    TEST_ASSERT_TRUE(ads_start(ADS_MUX_I));
    for (int k = 0; k < 20 && ads_state() != ADS_ERROR; k++) {
        tp_pass();
    }
    TEST_ASSERT_EQUAL(ADS_ERROR, ads_state());
    TEST_ASSERT_EQUAL(ADS_ERR_SPI, ads_error());
    /* recovery: a new init brings it back */
    init_at(10000, ADS_PGA_2);
}

static void test_spontaneous_reset_caught_by_scrub(void)
{
    init_at(10000, ADS_PGA_2);
    adsm_spontaneous_reset(tp_model(), tp_now());
    uint64_t t0 = tp_now();
    while (ads_state() != ADS_ERROR && tp_now() - t0 < 2000000) {
        tp_pass();
        if (ads_idle()) {
            (void)ads_start(ADS_MUX_I);
        }
        ads_sample_t s;
        (void)ads_take(&s);
    }
    TEST_ASSERT_EQUAL(ADS_ERROR, ads_state());
    TEST_ASSERT_EQUAL(ADS_ERR_READBACK, ads_error());
    TEST_ASSERT_TRUE(tp_now() - t0 <= ADC_SCRUB_PERIOD_US + 50000u);
}

static void test_stuck_dout_detected(void)
{
    init_at(10000, ADS_PGA_2);
    adsm_set_stuck(tp_model(), ADSM_STUCK_HIGH);
    uint64_t t0 = tp_now();
    int k = 0;
    while (ads_state() != ADS_ERROR && tp_now() - t0 < 2000000) {
        ads_sample_t s;
        if (!convert((k++ & 1) ? ADS_MUX_V : ADS_MUX_I, &s, 100000)) {
            break;
        }
    }
    TEST_ASSERT_EQUAL(ADS_ERROR, ads_state());
    TEST_ASSERT_TRUE(ads_error() == ADS_ERR_STUCK || ads_error() == ADS_ERR_READBACK);
}

static void test_real_zero_codes_are_not_stuck(void)
{
    /* a channel legitimately at 0 V with noise must never trip the detector */
    g_ain[0] = 2.5;
    g_ain[2] = 2.5;
    adsm_set_noise(tp_model(), 1.0, 11);
    init_at(37500, ADS_PGA_2);
    for (int k = 0; k < 200; k++) {
        ads_sample_t s;
        TEST_ASSERT_TRUE(convert((k & 1) ? ADS_MUX_V : ADS_MUX_I, &s, 100000));
    }
    TEST_ASSERT_NOT_EQUAL(ADS_ERROR, ads_state());
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_rate_table_and_deadlines);
    RUN_TEST(test_sign_extension);
    RUN_TEST(test_init_configures_and_verifies);
    RUN_TEST(test_reconfigure_only_from_idle);
    RUN_TEST(test_channels_read_their_own_inputs);
    RUN_TEST(test_selfcheck_inputs_at_pga1);
    RUN_TEST(test_all_rates_random_pass_lengths);
    RUN_TEST(test_clip_flag);
    RUN_TEST(test_stale_pending_edge_rejected);
    RUN_TEST(test_glitch_edge_rejected);
    RUN_TEST(test_late_read_waits_for_next_edge);
    RUN_TEST(test_read_running_into_the_next_update_is_discarded);
    RUN_TEST(test_preemption_by_new_start);
    RUN_TEST(test_stall_after_an_edge_gives_a_late_sample);
    RUN_TEST(test_drdy_dead_after_a_stall_times_out);
    RUN_TEST(test_ignored_selfcal_is_detected);
    RUN_TEST(test_drdy_dead_times_out);
    RUN_TEST(test_spi_failure_reported);
    RUN_TEST(test_spontaneous_reset_caught_by_scrub);
    RUN_TEST(test_stuck_dout_detected);
    RUN_TEST(test_real_zero_codes_are_not_stuck);
    return UNITY_END();
}
