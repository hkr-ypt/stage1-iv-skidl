/*
 * test_sys_protocol.c — system tests of the host protocol (protocol.md) and
 * the measurement data path on the full firmware + simulation port: framing
 * and errors, configuration, sweeps (waypoint, hysteresis, list), record
 * format and ordering, overflow and re-fetch, MEAS?/ZERO, calibration
 * storage, event loss, per-point classification flags.
 */
#include "unity.h"

#include "sys/sys_harness.h"

#include "app.h"
#include "engine.h"
#include "instrument.h"
#include "port.h"
#include "port_sim_ext.h"
#include "proto.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void setUp(void)
{
    h_boot();
}

void tearDown(void)
{
    TEST_ASSERT_EQUAL_UINT32(0, psim_dac()->b_not_fullscale_loads);
}

static void arm(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("ARM"));
    TEST_ASSERT_TRUE(h_run_until_state(ST_ARMED, 500000));
}

static bool starts(const char *s, const char *p)
{
    return strncmp(s, p, strlen(p)) == 0;
}

/* Parse a PT line into its 17 comma-separated fields. */
static int pt_fields(const char *line, char f[17][32])
{
    if (!starts(line, "PT ")) {
        return -1;
    }
    const char *p = line + 3;
    int n = 0;
    while (n < 17) {
        size_t l = strcspn(p, ",");
        if (l >= 32) {
            return -1;
        }
        memcpy(f[n], p, l);
        f[n][l] = '\0';
        n++;
        if (p[l] != ',') {
            break;
        }
        p += l + 1;
    }
    return n;
}

/* Run a defined sweep to its end and collect its PT lines. */
static size_t run_sweep(size_t *first_pt, char *end_line, size_t cap, uint64_t timeout_us)
{
    size_t from = h_lines();
    const char *r = h_cmd("SWP.RUN");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK id="), r);
    TEST_ASSERT_TRUE(h_wait_line("EVT SWEEP_END", from, timeout_us));
    size_t e = h_find("EVT SWEEP_END", from);
    snprintf(end_line, cap, "%s", h_line(e));
    *first_pt = h_find("PT ", from);
    size_t n = 0;
    for (size_t i = from; i < e; i++) {
        if (starts(h_line(i), "PT ")) {
            n++;
        }
    }
    return n;
}

/* ---- framing and errors ------------------------------------------------------------- */

static void test_errors_and_one_response_per_command(void)
{
    TEST_ASSERT_EQUAL_STRING("ERR UNKNOWN unknown verb", h_cmd("FOO"));
    TEST_ASSERT_EQUAL_STRING("ERR SYNTAX unbalanced =", h_cmd("CFG n_avg="));
    TEST_ASSERT_EQUAL_STRING("ERR SYNTAX unbalanced =", h_cmd("CFG =3"));
    TEST_ASSERT_EQUAL_STRING("ERR ARG unknown key bogus", h_cmd("CFG bogus=1"));
    TEST_ASSERT_EQUAL_STRING("ERR ARG duplicate key n_avg", h_cmd("CFG n_avg=2 n_avg=3"));
    TEST_ASSERT_EQUAL_STRING("ERR ARG takes no arguments", h_cmd("PING x"));
    char longline[400];
    memset(longline, 'A', sizeof longline - 1);
    longline[sizeof longline - 1] = '\0';
    TEST_ASSERT_EQUAL_STRING("ERR SYNTAX line too long", h_cmd(longline));
    psim_host_give_rx((const uint8_t *)"PI\x01NG\n", 6);
    TEST_ASSERT_TRUE(h_wait_line("ERR SYNTAX bad character", 0, 100000));
    /* verbs and keys are case-insensitive; CRLF accepted; blank lines ignored */
    psim_host_give_rx((const uint8_t *)"\r\n\n  \n", 6);
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("ping\r"), "OK PONG t_us=", 13));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("cfg N_AVG=2"));
    /* a burst of commands: one response each, in order */
    size_t from = h_lines();
    h_send("PING");
    h_send("FOO");
    h_send("CFG?");
    h_send("SWP?");
    h_run_us(50000);
    const char *expect[] = { "OK PONG", "ERR UNKNOWN", "OK CFG", "OK SWP" };
    size_t k = 0;
    for (size_t i = from; i < h_lines() && k < 4; i++) {
        if (starts(h_line(i), "OK") || starts(h_line(i), "ERR")) {
            TEST_ASSERT_TRUE_MESSAGE(starts(h_line(i), expect[k]), h_line(i));
            k++;
        }
    }
    TEST_ASSERT_EQUAL(4, k);
}

static void test_numbers_exact_or_rejected(void)
{
    TEST_ASSERT_EQUAL_STRING("ERR ARG ramp_rate inexact", h_cmd("CFG ramp_rate=0.0100001"));
    TEST_ASSERT_EQUAL_STRING("ERR ARG v_limit not a number", h_cmd("CFG v_limit=0x10"));
    TEST_ASSERT_EQUAL_STRING("ERR ARG v_limit not a number", h_cmd("CFG v_limit=inf"));
    TEST_ASSERT_EQUAL_STRING("ERR RANGE v_limit out of range", h_cmd("CFG v_limit=10.000001"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG v_limit=5e0 i_compliance=1e-3"));
    const char *c = h_cmd("CFG?");
    TEST_ASSERT_NOT_NULL(strstr(c, " v_limit=5.000000"));
    TEST_ASSERT_NOT_NULL(strstr(c, " i_compliance=0.001000000"));
}

static void test_cfg_all_or_nothing(void)
{
    char before[512];
    snprintf(before, sizeof before, "%s", h_cmd("CFG?"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("CFG n_avg=4 settle_us=50"), "ERR RANGE", 9));
    TEST_ASSERT_EQUAL_STRING(before, h_cmd("CFG?"));
    TEST_ASSERT_EQUAL_STRING("ERR RANGE adc_rate not a v1 rate", h_cmd("CFG adc_rate=7500"));
    TEST_ASSERT_EQUAL_STRING(before, h_cmd("CFG?"));
}

static void test_identity(void)
{
    const char *r = h_cmd("*IDN?");
    char v[64];
    TEST_ASSERT_TRUE(h_kv(r, "usb_vid", v, sizeof v));
    TEST_ASSERT_EQUAL_STRING("0x1209", v);
    TEST_ASSERT_TRUE(h_kv(r, "usb_pid", v, sizeof v));
    TEST_ASSERT_EQUAL_STRING("0x0001", v);
    TEST_ASSERT_TRUE(h_kv(r, "usb_pid_status", v, sizeof v));
    TEST_ASSERT_EQUAL_STRING("dev-test", v);
    TEST_ASSERT_TRUE(h_kv(r, "planned_pid", v, sizeof v));
    TEST_ASSERT_EQUAL_STRING("500S", v);
    TEST_ASSERT_TRUE(h_kv(r, "serial", v, sizeof v));
    TEST_ASSERT_EQUAL_STRING("0034002B3436511233353533", v);
    TEST_ASSERT_TRUE(h_kv(r, "build", v, sizeof v));
    TEST_ASSERT_EQUAL_STRING(FW_VARIANT_NAME, v);
    const char *s = h_cmd("STAT?");
    TEST_ASSERT_TRUE((h_kv_ll(s, "warn") & W_DEV_USB_PID) != 0);
}

/* ---- sweeps ---------------------------------------------------------------------- */

static void test_wp_divisibility_rule(void)
{
    TEST_ASSERT_EQUAL_STRING("ERR ARG not divisible suggest=0.250000", h_cmd("SWP.WP 0 1 -0.5 STEP=0.3"));
    TEST_ASSERT_EQUAL_STRING("ERR ARG step below 0.5 mV", h_cmd("SWP.WP 0 1 STEP=0.0004"));
    TEST_ASSERT_EQUAL_STRING("ERR ARG zero-length segment", h_cmd("SWP.WP 0 1 1 STEP=0.5"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 1 STEP=0.5 CYCLES=2"), "ERR ARG cycles", 14));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 1 -1 0 STEP=0.5 CYCLES=2"), "OK SWP kind=wp points=17 ", 25));
}

static void test_hysteresis_loop_identical_codes(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=200 first_settle_us=1000"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 2 -2 0 STEP=0.25 CYCLES=2"), "OK SWP kind=wp points=65 ", 25));
    arm();
    size_t first;
    char end[256];
    size_t n = run_sweep(&first, end, sizeof end, 30000000);
    TEST_ASSERT_EQUAL(65, n);
    TEST_ASSERT_NOT_NULL(strstr(end, "status=complete"));
    TEST_ASSERT_NOT_NULL(strstr(end, "points=65"));
    /* v_set -> dac_code must be a function: same setpoint, same code */
    int code_of[81];
    for (int i = 0; i < 81; i++) {
        code_of[i] = -1;
    }
    char f[17][32];
    long seq_prev = -1;
    size_t seen = 0;
    for (size_t i = first; i < h_lines(); i++) {
        if (!starts(h_line(i), "PT ")) {
            continue;
        }
        TEST_ASSERT_EQUAL(17, pt_fields(h_line(i), f));
        long seq = strtol(f[1], NULL, 10);
        if (seq_prev >= 0) {
            TEST_ASSERT_EQUAL(seq_prev + 1, seq);   /* contiguous */
        }
        seq_prev = seq;
        TEST_ASSERT_EQUAL((long)seen, strtol(f[2], NULL, 10));   /* idx */
        int code = (int)strtol(f[7], NULL, 10);
        double vset = strtod(f[8], NULL);
        int key = (int)lround(vset / 0.25) + 40;
        TEST_ASSERT_TRUE(key >= 0 && key < 81);
        if (code_of[key] < 0) {
            code_of[key] = code;
        }
        TEST_ASSERT_EQUAL(code_of[key], code);
        unsigned flags = (unsigned)strtoul(f[16], NULL, 16);
        TEST_ASSERT_EQUAL(seen == 0 ? 1u : 0u, (flags >> 7) & 1u);   /* FIRST */
        TEST_ASSERT_EQUAL(0u, flags & (PF_KELVIN | PF_NO_OUTPUT | PF_CLIM | PF_LATE));
        if (++seen == 65) {
            break;
        }
    }
    /* end=zero: back to 0 V, still ARMED */
    h_run_us(500000);
    TEST_ASSERT_EQUAL(ST_ARMED, h_state());
    TEST_ASSERT_TRUE(fabs(h_vsweep()) < 0.01);
}

static void test_records_carry_measured_values(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG n_avg=4 settle_us=500"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP -5 5 STEP=1"), "OK SWP", 6));
    arm();
    size_t first;
    char end[256];
    TEST_ASSERT_EQUAL(11, run_sweep(&first, end, sizeof end, 30000000));
    char f[17][32];
    for (size_t i = first, k = 0; k < 11; i++) {
        if (!starts(h_line(i), "PT ")) {
            continue;
        }
        TEST_ASSERT_EQUAL(17, pt_fields(h_line(i), f));
        double vset = strtod(f[8], NULL);
        double vk = strtod(f[14], NULL);
        double ia = strtod(f[15], NULL);
        /* 1 k DUT behind R_top 106 + R_bot 15: V_K = V_set * 1000 / 1121 (+ offsets) */
        TEST_ASSERT_DOUBLE_WITHIN(0.01, vset * 1000.0 / 1121.0, vk);
        TEST_ASSERT_DOUBLE_WITHIN(1e-5, vk / 1000.0, ia);
        TEST_ASSERT_EQUAL_STRING("4", f[9]);
        TEST_ASSERT_NOT_EQUAL(0, strcmp(f[12], "-"));   /* sd present for n >= 2 */
        /* I,V,V,I with even n: coincident centroids */
        long ti = strtol(f[5], NULL, 10), tv = strtol(f[6], NULL, 10);
        TEST_ASSERT_TRUE(labs(ti - tv) <= 2);
        k++;
    }
}

static void test_list_sweep_settles(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SWP.LIST.CLR"));
    TEST_ASSERT_EQUAL_STRING("OK n=3", h_cmd("SWP.LIST.ADD 0.1 0.2:20000 0.3"));
    TEST_ASSERT_EQUAL_STRING("OK n=4", h_cmd("SWP.LIST.ADD -0.1:100"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.LIST.ADD 1 2 3 4 5 6 7 8 9"), "ERR ARG", 7));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.LIST.ADD 0.1:50"), "ERR RANGE settle_us", 19));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.LIST.USE"), "OK SWP kind=list points=4", 25));
    arm();
    size_t first;
    char end[256];
    TEST_ASSERT_EQUAL(4, run_sweep(&first, end, sizeof end, 10000000));
    char f[17][32];
    long t_set[4];
    for (size_t i = first, k = 0; k < 4; i++) {
        if (starts(h_line(i), "PT ")) {
            pt_fields(h_line(i), f);
            t_set[k++] = strtol(f[4], NULL, 10);
        }
    }
    /* point 1 settles 20 ms, the others the default 1 ms */
    TEST_ASSERT_TRUE(t_set[2] - t_set[1] > 20000);
    TEST_ASSERT_TRUE(t_set[3] - t_set[2] < 10000);
}

static void test_sweep_rejected_beyond_v_limit(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG v_limit=3"));
    TEST_ASSERT_EQUAL_STRING("ERR RANGE beyond v_limit", h_cmd("SWP.WP 0 5 STEP=1"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG v_limit=10"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 5 STEP=1"), "OK SWP", 6));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG v_limit=3"));
    arm();
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.RUN"), "ERR RANGE", 9));
    TEST_ASSERT_EQUAL(ST_ARMED, h_state());
}

static void test_abort_ramps_to_zero(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=100000"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 8 STEP=0.5"), "OK SWP", 6));
    arm();
    size_t from = h_lines();
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.RUN"), "OK id=", 6));
    h_run_us(1000000);                   /* 17 points x 100 ms: mid-sweep */
    TEST_ASSERT_EQUAL(ST_SWEEPING, h_state());
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SWP.ABORT"));
    TEST_ASSERT_TRUE(h_wait_line("EVT SWEEP_END", from, 100000));
    TEST_ASSERT_NOT_NULL(strstr(h_line(h_find("EVT SWEEP_END", from)), "status=aborted"));
    h_run_us(1200000);
    TEST_ASSERT_EQUAL(ST_ARMED, h_state());
    TEST_ASSERT_TRUE(fabs(h_vsweep()) < 0.01);
}

/* ---- data path: stalled host, overflow, re-fetch ------------------------------------------ */

static void test_stalled_host_short_sweep_loses_nothing(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=100"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 5 0 STEP=0.01"), "OK SWP kind=wp points=1001", 26));
    arm();
    size_t from = h_lines();
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.RUN"), "OK id=", 6));
    psim_set_tx_space_limit(0);          /* host stops reading */
    h_run_us(2000000);
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
    TEST_ASSERT_TRUE(h_wait_line("EVT SWEEP_END", from, 20000000));
    TEST_ASSERT_NOT_NULL(strstr(h_line(h_find("EVT SWEEP_END", from)), "status=complete"));
    size_t n = 0;
    long prev = -1;
    char f[17][32];
    for (size_t i = from; i < h_lines(); i++) {
        if (starts(h_line(i), "PT ")) {
            pt_fields(h_line(i), f);
            long seq = strtol(f[1], NULL, 10);
            if (prev >= 0) {
                TEST_ASSERT_EQUAL(prev + 1, seq);
            }
            prev = seq;
            n++;
        }
    }
    TEST_ASSERT_EQUAL(1001, n);
}

static void test_long_stall_overflows_explicitly(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=100"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 5 0 STEP=0.002"), "OK SWP kind=wp points=5001", 26));
    arm();
    size_t from = h_lines();
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.RUN"), "OK id=", 6));
    psim_set_tx_space_limit(0);
    h_run_us(8000000);
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
    TEST_ASSERT_TRUE(h_wait_line("EVT SWEEP_END", from, 5000000));
    const char *end = h_line(h_find("EVT SWEEP_END", from));
    TEST_ASSERT_NOT_NULL(strstr(end, "status=overflow"));
    long pts = (long)h_kv_ll(end, "points");
    /* every record pushed was delivered, in order, before SWEEP_END */
    size_t n = 0;
    for (size_t i = from; i < h_find("EVT SWEEP_END", from); i++) {
        n += starts(h_line(i), "PT ") ? 1u : 0u;
    }
    TEST_ASSERT_EQUAL(pts, (long)n);
    TEST_ASSERT_TRUE(pts >= 1024 && pts < 5001);
    h_run_us(1500000);
    TEST_ASSERT_EQUAL(ST_ARMED, h_state());
    TEST_ASSERT_TRUE(fabs(h_vsweep()) < 0.01);
}

static void test_fetch_during_sweep_does_not_overflow(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=100"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 4 0 STEP=0.002"), "OK SWP kind=wp points=4001", 26));
    arm();
    size_t from = h_lines();
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.RUN"), "OK id=", 6));
    h_run_us(3000000);
    const char *r = h_cmd("DATA.FETCH FROM=1");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK first="), r);
    TEST_ASSERT_TRUE(h_wait_line("EVT SWEEP_END", from, 30000000));
    TEST_ASSERT_NOT_NULL(strstr(h_line(h_find("EVT SWEEP_END", from)), "status=complete"));
}

static void test_fetch_resends_retained(void)
{
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 1 STEP=0.1"), "OK SWP", 6));
    arm();
    size_t first;
    char end[256];
    TEST_ASSERT_EQUAL(11, run_sweep(&first, end, sizeof end, 10000000));
    size_t from = h_lines();
    TEST_ASSERT_EQUAL_STRING("OK first=5 last=11", h_cmd("DATA.FETCH FROM=5"));
    h_run_us(20000);
    size_t n = 0;
    for (size_t i = from; i < h_lines(); i++) {
        n += starts(h_line(i), "PT 1,") ? 1u : 0u;
    }
    TEST_ASSERT_EQUAL(7, n);
    TEST_ASSERT_EQUAL_STRING("OK first=12 last=11", h_cmd("DATA.FETCH FROM=100"));
}

static void test_event_loss_is_reported(void)
{
    /* Commands are throttled before events could fill the ring, so only
     * autonomous events can be lost: inject them while the host is stalled. */
    psim_set_tx_space_limit(0);
    for (int i = 0; i < 400; i++) {
        proto_event("EVT WARN TEST_FILL", 18);
        h_run_us(200);
    }
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
    TEST_ASSERT_TRUE(h_wait_line("EVT WARN EVT_LOST", 0, 1000000));
    TEST_ASSERT_TRUE((h_kv_ll(h_cmd("STAT?"), "warn") & W_EVT_LOST) != 0);
}

/* ---- MEAS?, ZERO -------------------------------------------------------------------------- */

static void test_meas_and_zero(void)
{
    /* A DUT left connected is refused only when it drives a reading beyond the
     * worst-case front-end offsets (10 mV / 100 uA referred to the DUT): here a
     * 15 mV residual at V_SWEEP across the connected 1 kohm DUT. */
    psim_board()->sweep_offset_v = 0.015;
    const char *r = h_cmd("ZERO");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "ERR RANGE implausible"), r);   /* DUT connected */
    psim_board()->sweep_offset_v = 0.002;
    h_jacks_open(true);
    r = h_cmd("ZERO");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK ZERO"), r);
    TEST_ASSERT_EQUAL(64, h_kv_ll(r, "pairs"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("STAT?") + 0, "OK STAT", 7));
    TEST_ASSERT_NOT_NULL(strstr(h_cmd("STAT?"), " zero=valid"));
    /* with the zero applied, the open-jack reading is ~0 */
    r = h_cmd("MEAS? N=16");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK MEAS n=16"), r);
    TEST_ASSERT_DOUBLE_WITHIN(20e-6, 0.0, h_kv_d(r, "v_v"));
    TEST_ASSERT_DOUBLE_WITHIN(0.2e-6, 0.0, h_kv_d(r, "i_a"));
    TEST_ASSERT_EQUAL(PF_MEAS | PF_CAL_NOMINAL, (int)h_kv_ll(r, "flags"));
    /* a rate change marks the zero stale */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG adc_rate=50"));
    h_run_us(1000000);
    TEST_ASSERT_NOT_NULL(strstr(h_cmd("STAT?"), " zero=stale"));
    TEST_ASSERT_EQUAL_STRING("ERR RANGE duration above 10 s", h_cmd("MEAS? N=256"));
    r = h_cmd("MEAS?");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK MEAS n=1 "), r);
    TEST_ASSERT_NOT_NULL(strstr(r, "i_sd=- v_sd=-"));
}

static void test_zero_armed_requires_zero_output(void)
{
    h_jacks_open(true);
    arm();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=1"));
    h_run_us(300000);
    TEST_ASSERT_EQUAL_STRING("ERR STATE output not at 0 V", h_cmd("ZERO"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=0"));
    h_run_us(300000);
    TEST_ASSERT_TRUE(starts(h_cmd("ZERO"), "OK ZERO"));
}

/* ---- calibration storage ----------------------------------------------------------------- */

static void test_cal_save_reload_and_torn_write(void)
{
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("CAL?"), "OK CAL source=nominal seq=0", 27));
    TEST_ASSERT_EQUAL_STRING("ERR RANGE implausible coefficients", h_cmd("CAL.SET r_s=500"));
    TEST_ASSERT_EQUAL_STRING("ERR ARG r_s inexact", h_cmd("CAL.SET r_s=121.00001"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET r_s=118.5 g_v_uv=1.5"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("CAL?"), "OK CAL source=ram", 17));
    TEST_ASSERT_EQUAL_STRING("OK seq=1", h_cmd("CAL.SAVE TS=1700000000 NOTE=bench1"));
    TEST_ASSERT_EQUAL_STRING("OK seq=2", h_cmd("CAL.SAVE TS=1700000001"));
    /* torn third save: the previous record survives */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET r_s=150"));
    psim_cal_tear_next();
    TEST_ASSERT_EQUAL_STRING("ERR FLASH erase/program/verify failed", h_cmd("CAL.SAVE TS=1700000002"));
    psim_mcu_reset();
    app_init();
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 2000000));
    char c[400];
    snprintf(c, sizeof c, "%s", h_cmd("CAL?"));
    TEST_ASSERT_EQUAL(0, strncmp(c, "OK CAL source=stored seq=2", 26));
    /* the tear left the new record's area erased: nothing stored was lost */
    TEST_ASSERT_EQUAL(0, h_kv_ll(h_cmd("STAT?"), "warn") & W_CAL_DAMAGED);
    TEST_ASSERT_NOT_NULL_MESSAGE(strstr(c, " r_s=118.5000"), c);
    TEST_ASSERT_NOT_NULL(strstr(c, " g_v_uv=1.500000"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.DEFAULT"));
    TEST_ASSERT_EQUAL_STRING("OK source=stored seq=2", h_cmd("CAL.RELOAD"));
    /* CAL? output round-trips through CAL.SET exactly */
    char set[400] = "CAL.SET";
    const char *keys[] = { "g_dac_uv", "c_dac", "g_v_uv", "g_i_na", "r_s", "r_top", "i_plateau" };
    char v[64];
    snprintf(set, sizeof set, "CAL.SET");
    for (int i = 0; i < 7; i++) {
        TEST_ASSERT_TRUE(h_kv(c, keys[i], v, sizeof v));
        size_t l = strlen(set);
        snprintf(set + l, sizeof set - l, " %s=%s", keys[i], v);
    }
    /* r_s and g_v_uv were set: measured = bits 4 and 2 */
    TEST_ASSERT_NOT_NULL_MESSAGE(strstr(c, " seq=2 measured=0x14 g_dac_uv="), c);
    char before[400];
    snprintf(before, sizeof before, "%s", strstr(c, " g_dac_uv="));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd(set));
    char after[400];
    snprintf(after, sizeof after, "%s", h_cmd("CAL?"));
    /* ... the mask included: the nominal values CAL? printed are not
     * measurements, so the round trip keeps 0x14 (and CAL_NOMINAL) */
    TEST_ASSERT_EQUAL_MESSAGE(0, strncmp(after, "OK CAL source=ram seq=2 measured=0x14 g_dac_uv=", 47), after);
    TEST_ASSERT_EQUAL_STRING(before, strstr(after, " g_dac_uv="));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.DEFAULT"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("CAL?"), "OK CAL source=nominal seq=2 measured=0x00 ", 42));
}

/* ---- classification flags on the modelled board ------------------------------------------- */

static size_t sweep_flags(const char *def, unsigned *or_flags)
{
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd(def), "OK SWP", 6));
    size_t first;
    char end[256];
    size_t n = run_sweep(&first, end, sizeof end, 20000000);
    *or_flags = 0;
    char f[17][32];
    for (size_t i = first, k = 0; k < n; i++) {
        if (starts(h_line(i), "PT ")) {
            pt_fields(h_line(i), f);
            *or_flags |= (unsigned)strtoul(f[16], NULL, 16);
            k++;
        }
    }
    return n;
}

static void test_open_sense_plus_flags_kelvin(void)
{
    arm();
    psim_board()->sense_plus_open = true;
    size_t from = h_lines();
    unsigned fl;
    sweep_flags("SWP.WP 0 5 STEP=0.5", &fl);
    TEST_ASSERT_TRUE(fl & PF_KELVIN);
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, h_find("EVT WARN KELVIN", from));
    TEST_ASSERT_TRUE((h_kv_ll(h_cmd("STAT?"), "warn") & W_KELVIN) != 0);
}

static void test_force_open_flags_no_output(void)
{
    arm();
    board_model_t *b = psim_board();
    b->force_open = true;
    b->sense_plus_open = true;
    b->sense_minus_open = true;
    unsigned fl;
    sweep_flags("SWP.WP 0 5 STEP=1", &fl);
    TEST_ASSERT_TRUE(fl & PF_NO_OUTPUT);
}

static void test_current_limit_flags_clim(void)
{
    arm();
    psim_board()->dut.kind = DUT_RESISTOR;
    psim_board()->dut.r_ohm = 10.0;      /* 8 V / 131 ohm >> 49 mA plateau */
    unsigned fl;
    sweep_flags("SWP.WP 0 8 STEP=1", &fl);
    TEST_ASSERT_TRUE(fl & PF_CLIM);
}

static void test_compliance_ends_sweep(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG i_compliance=0.002"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 5 STEP=0.25"), "OK SWP", 6));
    arm();
    size_t first;
    char end[256];
    size_t n = run_sweep(&first, end, sizeof end, 10000000);
    TEST_ASSERT_NOT_NULL(strstr(end, "status=compliance"));
    TEST_ASSERT_NOT_NULL(strstr(end, "trip_i_a="));
    TEST_ASSERT_TRUE(n >= 8 && n <= 10);      /* 2 mA * 1121 ohm = 2.24 V */
    TEST_ASSERT_EQUAL(ST_ARMED, h_state());
    TEST_ASSERT_TRUE(fabs(h_vsweep()) < 0.01);
}

static void test_events_never_overtake_earlier_records(void)
{
    /* The host stops reading while sweeps keep being run. Each SWEEP_END must
     * still follow every record of its sweep, and the next SWEEP_BEGIN the
     * previous SWEEP_END (protocol.md §2). */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=100"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 1 STEP=0.01"), "OK SWP kind=wp points=101", 25));
    arm();
    size_t from = h_lines();
    psim_set_tx_space_limit(0);
    h_send("SWP.RUN");
    TEST_ASSERT_TRUE(h_run_until_state(ST_SWEEPING, 1000000));
    TEST_ASSERT_TRUE(h_run_until_state(ST_ARMED, 10000000));
    h_send("SWP.RUN");
    TEST_ASSERT_TRUE(h_run_until_state(ST_SWEEPING, 1000000));
    TEST_ASSERT_TRUE(h_run_until_state(ST_ARMED, 10000000));
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
    h_run_us(3000000);

    size_t begin2 = h_find("EVT SWEEP_BEGIN id=2", from);
    size_t end1 = h_find("EVT SWEEP_END id=1", from);
    size_t end2 = h_find("EVT SWEEP_END id=2", from);
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, end1);
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, end2);
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, begin2);
    TEST_ASSERT_TRUE_MESSAGE(begin2 > end1, "SWEEP_BEGIN 2 overtook SWEEP_END 1");
    size_t n1 = 0, n2 = 0;
    for (size_t i = from; i < h_lines(); i++) {
        if (starts(h_line(i), "PT 1,")) {
            TEST_ASSERT_TRUE_MESSAGE(i < end1, "a sweep-1 record after its SWEEP_END");
            n1++;
        } else if (starts(h_line(i), "PT 2,")) {
            TEST_ASSERT_TRUE_MESSAGE(i > begin2 && i < end2, "a sweep-2 record outside BEGIN..END");
            n2++;
        }
    }
    TEST_ASSERT_EQUAL_UINT32(101, n1);
    TEST_ASSERT_EQUAL_UINT32(101, n2);
    /* each STATE event follows its sweep's records too */
    size_t armed2 = SIZE_MAX;
    for (size_t i = end2; i < h_lines(); i++) {
        if (starts(h_line(i), "EVT STATE ARMED")) {
            armed2 = i;
            break;
        }
    }
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, armed2);
}

static void test_duplicate_keys_differing_in_case_rejected(void)
{
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("CFG n_avg=2 N_AVG=7"), "ERR ARG duplicate key", 21));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("CAL.SET r_s=10 R_S=11"), "ERR ARG duplicate key", 21));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("MEAS? N=2 n=3"), "ERR ARG duplicate key", 21));
}

static void test_integer_keys_accept_the_number_grammar(void)
{
    /* exact values in any strict-decimal form; inexact ones rejected */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=1e3 n_avg=+4 host_timeout_s=30.0"));
    const char *r = h_cmd("CFG?");
    TEST_ASSERT_NOT_NULL(strstr(r, " settle_us=1000"));
    TEST_ASSERT_NOT_NULL(strstr(r, " n_avg=4"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("CFG settle_us=1000.5"), "ERR ARG", 7));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("CFG n_avg=-1"), "ERR RANGE", 9));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("CFG n_avg=2.5e0"), "ERR ARG", 7));
}

static size_t count_responses(size_t from)
{
    size_t n = 0;
    for (size_t i = from; i < h_lines(); i++) {
        if (starts(h_line(i), "OK") || starts(h_line(i), "ERR")) {
            n++;
        }
    }
    return n;
}

static void test_stalled_host_state_events_never_lost(void)
{
    /* The host stops reading behind a sweep's records but keeps sending
     * DISARM/ARM. The ordered queue must hold back the commands instead of
     * dropping their STATE events: the last STATE the host reads is the state
     * the instrument is in. */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=100"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 3 STEP=0.1"), "OK SWP", 6));
    arm();
    size_t from = h_lines();
    psim_set_tx_space_limit(0);
    h_send("SWP.RUN");
    TEST_ASSERT_TRUE(h_run_until_state(ST_SWEEPING, 1000000));
    TEST_ASSERT_TRUE(h_run_until_state(ST_ARMED, 5000000));
    for (int k = 0; k < 10; k++) {
        h_send("DISARM");
        h_run_us(400000);
        h_send("ARM");
        h_run_us(400000);
    }
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
    h_run_us(10000000);
    proto_stats_t ps;
    proto_get_stats(&ps);
    TEST_ASSERT_EQUAL_UINT32(0, ps.evt_lost);
    TEST_ASSERT_EQUAL_UINT32(21, count_responses(from));
    size_t last = SIZE_MAX;
    for (size_t i = from; i < h_lines(); i++) {
        if (starts(h_line(i), "EVT STATE ")) {
            last = i;
        }
    }
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, last);
    const char *want = h_state() == ST_ARMED ? "EVT STATE ARMED" : "EVT STATE DISARMED";
    TEST_ASSERT_TRUE_MESSAGE(starts(h_line(last), want), h_line(last));
}

static void test_stalled_host_back_to_back_sweeps_keep_their_events(void)
{
    /* SWP.RUN after SWP.RUN while the host is not reading: every sweep that
     * ran arrives as SWEEP_BEGIN, all of its records, SWEEP_END */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=100"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 0.1 STEP=0.01"), "OK SWP kind=wp points=11", 24));
    arm();
    size_t from = h_lines();
    psim_set_tx_space_limit(0);
    for (int k = 0; k < 6; k++) {
        h_send("SWP.RUN");
        h_run_us(600000);
    }
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
    h_run_us(10000000);
    proto_stats_t ps;
    proto_get_stats(&ps);
    TEST_ASSERT_EQUAL_UINT32(0, ps.evt_lost);
    TEST_ASSERT_EQUAL_UINT32(6, count_responses(from));
    unsigned ran = 0;
    for (size_t i = from; i < h_lines(); i++) {
        if (!starts(h_line(i), "OK id=")) {
            continue;
        }
        unsigned id = (unsigned)atoi(h_line(i) + 6);
        char b[48], e[48], pt[16];
        snprintf(b, sizeof b, "EVT SWEEP_BEGIN id=%u ", id);
        snprintf(e, sizeof e, "EVT SWEEP_END id=%u ", id);
        snprintf(pt, sizeof pt, "PT %u,", id);
        size_t ib = h_find(b, from), ie = h_find(e, from);
        TEST_ASSERT_NOT_EQUAL_MESSAGE(SIZE_MAX, ib, b);
        TEST_ASSERT_NOT_EQUAL_MESSAGE(SIZE_MAX, ie, e);
        size_t n = 0;
        for (size_t j = from; j < h_lines(); j++) {
            if (starts(h_line(j), pt)) {
                TEST_ASSERT_TRUE_MESSAGE(j > ib && j < ie, "a record outside its sweep");
                n++;
            }
        }
        TEST_ASSERT_EQUAL_UINT32(11, n);
        ran++;
    }
    TEST_ASSERT_TRUE(ran >= 2u);
}

static void test_disarm_acts_while_commands_are_held(void)
{
    /* A host that writes but does not read: two sweeps fill the ordered
     * queue, the third SWP.RUN is held, and DISARM behind it must still take
     * effect at once; its OK keeps its place in the command order. */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=100 end=hold"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 2 STEP=0.02"), "OK SWP", 6));
    arm();
    size_t from = h_lines();
    psim_set_tx_space_limit(0);
    for (int k = 0; k < 3; k++) {
        h_send("SWP.RUN");
        h_run_us(1500000);
    }
    TEST_ASSERT_TRUE(fabs(h_vsweep()) > 1.0);   /* end=hold: still at 2 V */
    h_send("DISARM");
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 3000000));
    TEST_ASSERT_TRUE(fabs(h_vsweep()) < 0.01);
    TEST_ASSERT_FALSE(h_gate());
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
    h_run_us(5000000);
    TEST_ASSERT_EQUAL_UINT32(4, count_responses(from));
    size_t last = SIZE_MAX;
    for (size_t i = from; i < h_lines(); i++) {
        if (starts(h_line(i), "OK") || starts(h_line(i), "ERR")) {
            last = i;
        }
    }
    TEST_ASSERT_EQUAL_STRING("OK", h_line(last));   /* DISARM, answered last */
    proto_stats_t ps;
    proto_get_stats(&ps);
    TEST_ASSERT_EQUAL_UINT32(0, ps.evt_lost);
}

static void test_swp_abort_acts_while_commands_are_held(void)
{
    /* a large response fills the TX ring of a host that is not reading: the
     * SWP.ABORT behind it still stops the sweep at once, and is answered OK */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=100000"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 4 STEP=0.1"), "OK SWP", 6));
    arm();
    size_t from = h_lines();
    psim_set_tx_space_limit(0);
    h_send("SWP.RUN");
    h_run_us(500000);
    for (int k = 0; k < 6; k++) {
        h_send("DIAG?");               /* ~450 B each: the TX gate closes */
    }
    h_run_us(100000);
    TEST_ASSERT_EQUAL(ST_SWEEPING, h_state());
    h_send("SWP.ABORT");
    h_run_us(300000);
    TEST_ASSERT_EQUAL(ST_ARMED, h_state());
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
    h_run_us(3000000);
    TEST_ASSERT_EQUAL_UINT32(8, count_responses(from));
    TEST_ASSERT_NOT_NULL(strstr(h_line(h_find("EVT SWEEP_END", from)), "status=aborted"));
    size_t last = SIZE_MAX;
    for (size_t i = from; i < h_lines(); i++) {
        if (starts(h_line(i), "OK") || starts(h_line(i), "ERR")) {
            last = i;
        }
    }
    TEST_ASSERT_EQUAL_STRING("OK", h_line(last));   /* SWP.ABORT, not ERR STATE */
}

static void test_disarm_during_deferred_measurement(void)
{
    /* MEAS? holds later commands for up to 10 s; a DISARM behind it acts at
     * once, the measurement is answered ERR STATE, then the DISARM OK */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG adc_rate=10"));
    h_run_us(2000000);
    arm();
    size_t from = h_lines();
    h_send("MEAS? N=40");
    h_run_us(300000);
    h_send("DISARM");
    h_run_us(300000);
    TEST_ASSERT_TRUE(h_state() == ST_DISARMING || h_state() == ST_DISARMED);
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 2000000));
    h_run_us(200000);
    size_t m = SIZE_MAX, d = SIZE_MAX;
    for (size_t i = from; i < h_lines(); i++) {
        if (starts(h_line(i), "ERR STATE") && m == SIZE_MAX) {
            m = i;
        } else if (strcmp(h_line(i), "OK") == 0) {
            d = i;
        }
    }
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, m);
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, d);
    TEST_ASSERT_TRUE(m < d);
    TEST_ASSERT_EQUAL_UINT32(2, count_responses(from));
}

static size_t count_prefix(const char *prefix, size_t from)
{
    size_t n = 0;
    for (size_t i = from; i < h_lines(); i++) {
        if (starts(h_line(i), prefix)) {
            n++;
        }
    }
    return n;
}

static void hold_tx_with_diag(void)
{
    for (int k = 0; k < 6; k++) {
        h_send("DIAG?");               /* ~450 B each: the TX gate closes */
    }
    h_run_us(100000);
}

static void test_early_abort_never_leaves_a_later_sweep_running(void)
{
    /* SWP.ABORT acts on the running sweep at once; a held SWP.RUN sent before
     * it then starts another sweep, which the in-order SWP.ABORT must end */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=100000"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 2 STEP=0.1"), "OK SWP", 6));
    arm();
    size_t from = h_lines();
    psim_set_tx_space_limit(0);
    h_send("SWP.RUN");
    h_run_us(500000);
    hold_tx_with_diag();
    h_send("SWP.RUN");
    h_send("SWP.ABORT");
    h_run_us(300000);
    TEST_ASSERT_EQUAL(ST_ARMED, h_state());
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
    h_run_us(5000000);
    TEST_ASSERT_EQUAL_UINT32(9, count_responses(from));
    TEST_ASSERT_EQUAL_UINT32(0, count_prefix("ERR", from));
    TEST_ASSERT_EQUAL(ST_ARMED, h_state());
    for (size_t i = from; i < h_lines(); i++) {
        if (starts(h_line(i), "EVT SWEEP_END")) {
            TEST_ASSERT_NOT_NULL_MESSAGE(strstr(h_line(i), "status=aborted"), h_line(i));
        }
    }
}

static void test_early_disarm_still_acts_after_a_held_arm(void)
{
    /* the DISARM arrives while DISARMED (nothing to do yet) behind a held ARM;
     * once the ARM has armed and parsing is held again, it must still act */
    size_t from = h_lines();
    psim_set_tx_space_limit(0);
    h_send("MEAS? N=200");
    h_send("ARM");
    for (int k = 0; k < 6; k++) {
        h_send("DIAG?");
    }
    h_send("DISARM");
    /* the held ARM runs once MEAS? is done; the waiting DISARM undoes it in
     * the same pass, so the gate never turns on */
    uint32_t gate_on = psim_board()->gate_on_events;
    h_run_us(10000000);
    TEST_ASSERT_EQUAL(ST_DISARMED, h_state());
    TEST_ASSERT_EQUAL_UINT32(gate_on, psim_board()->gate_on_events);
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
    h_run_us(2000000);
    TEST_ASSERT_EQUAL_UINT32(9, count_responses(from));
    TEST_ASSERT_EQUAL(ST_DISARMED, h_state());
}

static void test_abort_and_disarm_in_one_packet(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=100000"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 2 STEP=0.1"), "OK SWP", 6));
    arm();
    size_t from = h_lines();
    psim_set_tx_space_limit(0);
    h_send("SWP.RUN");
    h_run_us(500000);
    hold_tx_with_diag();
    psim_host_give_rx((const uint8_t *)"SWP.ABORT\nDISARM\n", 17);
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 2000000));
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
    h_run_us(2000000);
    TEST_ASSERT_EQUAL_UINT32(9, count_responses(from));
    TEST_ASSERT_EQUAL_UINT32(0, count_prefix("ERR", from));
}

static void test_padded_disarm_acts_early(void)
{
    /* blanks around the verb do not matter: any DISARM without arguments */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG adc_rate=10"));
    h_run_us(2000000);
    arm();
    size_t from = h_lines();
    h_send("MEAS? N=40");
    h_run_us(300000);
    h_send("\t  disarm                                        \t ");
    h_run_us(300000);
    TEST_ASSERT_TRUE(h_state() == ST_DISARMING || h_state() == ST_DISARMED);
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 2000000));
    h_run_us(200000);
    TEST_ASSERT_EQUAL_UINT32(2, count_responses(from));
}

static void test_writer_only_host_is_disarmed_by_the_host_timeout(void)
{
    /* A host that only writes fills the RX ring; a DISARM behind 1 KB of
     * unparsed input is not seen, but unread input is not host activity, so
     * the host timeout still disarms. */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG host_timeout_s=5"));
    arm();
    size_t from = h_lines();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=2"));
    TEST_ASSERT_TRUE(h_wait_line("EVT SRC_DONE", from, 2000000));
    psim_set_tx_space_limit(0);
    for (int k = 0; k < 1500; k++) {
        psim_host_give_rx((const uint8_t *)"DIAG?\n", 6);
    }
    psim_host_give_rx((const uint8_t *)"DISARM\n", 7);
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 8000000));
    TEST_ASSERT_EQUAL_STRING("host_timeout", disarm_reason_name(inst_disarm_reason()));
    TEST_ASSERT_TRUE(fabs(h_vsweep()) < 0.01);
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
}

static void test_waiting_disarm_undoes_an_arm_ahead_of_it_in_a_backlog(void)
{
    /* ARM, 300 other lines, DISARM in one packet behind a held TX side: when
     * the backlog then drains unheld, the ARM ahead of the waiting DISARM is
     * undone at once, not after the 300 lines */
    size_t from = h_lines();
    psim_set_tx_space_limit(0);
    hold_tx_with_diag();
    static char pkt[4 + 300 * 2 + 8];
    size_t n = 0;
    memcpy(pkt + n, "ARM\n", 4);
    n += 4;
    for (int k = 0; k < 300; k++) {
        memcpy(pkt + n, "X\n", 2);
        n += 2;
    }
    memcpy(pkt + n, "DISARM\n", 7);
    n += 7;
    psim_host_give_rx((const uint8_t *)pkt, n);
    h_run_us(100000);
    uint32_t gate_on = psim_board()->gate_on_events;
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
    h_run_us(3000000);
    TEST_ASSERT_EQUAL_UINT32(gate_on, psim_board()->gate_on_events);
    TEST_ASSERT_EQUAL(ST_DISARMED, h_state());
    TEST_ASSERT_EQUAL_UINT32(6 + 1 + 300 + 1, count_responses(from));
}

static void test_session_loss_during_deferred_job_frees_the_slot(void)
{
    /* SELFTEST runs for a while; the host disconnects mid-way */
    h_send("SELFTEST");
    h_run_us(5000);
    psim_set_dtr(false);
    h_run_us(500000);
    psim_set_dtr(true);
    h_run_us(100000);
    h_jacks_open(true);
    const char *r = h_cmd("ZERO");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK ZERO"), r);
}

/* ZERO accepts the INA128P worst-case offsets of an in-spec board up to 85 C
 * (U7: (50 + 500) uV + 20.5 uV/C x 60 C = 1.78 mV RTI, 8.9 mV at V_K; U6:
 * 258 uV + 8.83 uV/C x 60 C = 0.79 mV RTI, 79 uA over R10), and still
 * refuses offsets beyond them. */
static void test_zero_accepts_worst_case_front_end_offsets(void)
{
    h_jacks_open(true);
    psim_board()->ina_v_offset_v = 0.0090 * 0.199203;     /* 9.0 mV at V_K, output-referred */
    psim_board()->ina_i_offset_v = 79e-6 * 24.0056;       /* 79 uA through R10, output-referred */
    const char *r = h_cmd("ZERO");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK ZERO"), r);
    r = h_cmd("MEAS? N=16");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK MEAS"), r);
    TEST_ASSERT_DOUBLE_WITHIN(20e-6, 0.0, h_kv_d(r, "v_v"));   /* the offsets are removed */
    TEST_ASSERT_DOUBLE_WITHIN(0.3e-6, 0.0, h_kv_d(r, "i_a"));
    psim_board()->ina_v_offset_v = 0.0120 * 0.199203;     /* 12 mV: beyond any in-spec part */
    r = h_cmd("ZERO");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "ERR RANGE implausible"), r);
    psim_board()->ina_v_offset_v = 0.0003;
    psim_board()->ina_i_offset_v = 120e-6 * 24.0056;      /* 120 uA: beyond any in-spec part */
    r = h_cmd("ZERO");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "ERR RANGE implausible"), r);
}

/* ---- final firmware audit: measurement and classification regressions ---------------- */

static unsigned pt_flags(const char *line)
{
    char f[17][32];
    TEST_ASSERT_EQUAL_MESSAGE(17, pt_fields(line, f), line);
    return (unsigned)strtoul(f[16], NULL, 16);
}

/* A lifted SENSE+ lead on a low-impedance DUT reads V_K = -I x R_bot. The
 * points at plateau-level current stay CLIM (not DUT data) and are KELVIN as
 * well, so the lead is reported (KELVIN on every point, EVT WARN KELVIN)
 * instead of hidden behind CLIM. */
static void test_open_sense_plus_on_low_impedance_dut_is_flagged_kelvin(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET r_s=121 r_top=106"));
    psim_board()->dut.kind = DUT_RESISTOR;
    psim_board()->dut.r_ohm = 10.0;
    psim_board()->sense_plus_open = true;
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 10 STEP=0.5"), "OK SWP", 6));
    arm();
    size_t from = h_lines();
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.RUN"), "OK id=", 6));
    TEST_ASSERT_TRUE(h_wait_line("EVT SWEEP_END", from, 20000000));
    unsigned clim = 0, kelvin = 0, clim_alone = 0;
    for (size_t k = from; k < h_lines(); k++) {
        if (strncmp(h_line(k), "PT ", 3) != 0) {
            continue;
        }
        unsigned f = pt_flags(h_line(k));
        clim += (f & PF_CLIM) ? 1u : 0u;
        kelvin += (f & PF_KELVIN) ? 1u : 0u;
        clim_alone += ((f & PF_CLIM) && !(f & PF_KELVIN)) ? 1u : 0u;
    }
    TEST_ASSERT_TRUE(kelvin >= 10u);
    TEST_ASSERT_TRUE(clim >= 1u);            /* the plateau-level points */
    TEST_ASSERT_EQUAL_UINT32(0, clim_alone); /* none hides the lead */
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, h_find("EVT WARN KELVIN", from));
}

/* An active DUT (a cell) driven into the plateau at the opposite polarity
 * holds V_K at its own voltage, on the other side of V_set. Those points are
 * the plateau, not DUT data: CLIM, without a false sense-lead warning. */
static void test_active_dut_on_the_plateau_keeps_clim(void)
{
    psim_board()->dut.kind = DUT_CELL;
    psim_board()->dut.r_ohm = 1.0;
    arm();
    for (int pol = 0; pol < 2; pol++) {
        psim_board()->dut.v_cell = pol ? -1.5 : 1.5;
        const char *def = pol ? "SWP.WP 0 10 STEP=1" : "SWP.WP 0 -10 STEP=1";
        TEST_ASSERT_EQUAL(0, strncmp(h_cmd(def), "OK SWP", 6));
        size_t from = h_lines();
        TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.RUN"), "OK id=", 6));
        TEST_ASSERT_TRUE(h_wait_line("EVT SWEEP_END", from, 20000000));
        /* I = (V_set - V_cell) / 122 ohm reaches the 49 mA plateau near
         * |V_set| = 4.5 V; from 6 V on the point lies >= 1.5 V off the load
         * line, far outside even the nominal tolerance */
        char f[17][32];
        unsigned plateau = 0, clim = 0, kelvin = 0;
        for (size_t k = from; k < h_lines(); k++) {
            if (strncmp(h_line(k), "PT ", 3) != 0) {
                continue;
            }
            TEST_ASSERT_EQUAL(17, pt_fields(h_line(k), f));
            unsigned fl = (unsigned)strtoul(f[16], NULL, 16);
            if (fabs(atof(f[8])) >= 6.0) {
                plateau++;
                clim += (fl & PF_CLIM) ? 1u : 0u;
            }
            kelvin += (fl & PF_KELVIN) ? 1u : 0u;
        }
        TEST_ASSERT_EQUAL_UINT32(5, plateau);
        TEST_ASSERT_EQUAL_UINT32(plateau, clim);
        TEST_ASSERT_EQUAL_UINT32(0, kelvin);
        TEST_ASSERT_EQUAL(SIZE_MAX, h_find("EVT WARN KELVIN", from));
    }
}

/* The real plateau of a healthy low-impedance DUT is still CLIM. */
static void test_plateau_on_short_is_still_clim(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET r_s=121 r_top=106"));
    psim_board()->dut.kind = DUT_RESISTOR;
    psim_board()->dut.r_ohm = 0.01;
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 10 STEP=0.5"), "OK SWP", 6));
    arm();
    size_t from = h_lines();
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.RUN"), "OK id=", 6));
    TEST_ASSERT_TRUE(h_wait_line("EVT SWEEP_END", from, 20000000));
    TEST_ASSERT_NOT_EQUAL(SIZE_MAX, h_find("EVT SWEEP_END id=1 status=complete", from));
    unsigned clim = 0, kelvin = 0;
    for (size_t k = from; k < h_lines(); k++) {
        if (strncmp(h_line(k), "PT ", 3) == 0) {
            unsigned f = pt_flags(h_line(k));
            clim += (f & PF_CLIM) ? 1u : 0u;
            kelvin += (f & PF_KELVIN) ? 1u : 0u;
        }
    }
    TEST_ASSERT_TRUE(clim >= 5u);
    TEST_ASSERT_EQUAL_UINT32(0, kelvin);
}

/* The default software compliance must not act inside the hardware plateau
 * (OPA547T, expected 40..65 mA): a 60 mA plateau into a short is flagged
 * CLIM in both polarities instead of being zeroed by the firmware. */
static void test_default_compliance_lets_the_plateau_act(void)
{
    psim_board()->i_lim = 0.060;
    psim_board()->dut.kind = DUT_RESISTOR;
    psim_board()->dut.r_ohm = 0.01;
    arm();
    for (int pol = 0; pol < 2; pol++) {
        size_t from = h_lines();
        TEST_ASSERT_EQUAL_STRING("OK", h_cmd(pol ? "SRC V=-10" : "SRC V=10"));
        TEST_ASSERT_TRUE(h_wait_line("EVT SRC_DONE", from, 5000000));
        TEST_ASSERT_NOT_EQUAL_MESSAGE(SIZE_MAX, h_find("EVT SRC_DONE v_v=", from), "SRC_DONE missing");
        TEST_ASSERT_EQUAL(SIZE_MAX, h_find("EVT WARN COMPLIANCE", from));
        const char *r = h_cmd("MEAS? N=4");
        TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK MEAS"), r);
        TEST_ASSERT_DOUBLE_WITHIN(0.002, pol ? -0.060 : 0.060, h_kv_d(r, "i_a"));
        TEST_ASSERT_TRUE_MESSAGE((h_kv_ll(r, "flags") & PF_CLIM) != 0, r);
    }
}

/* A missing +-15 V rail while armed (U12 opens; both INA128 outputs near
 * AGND) is the front-end fault, not a DUT over-current or plateau. */
static void test_rail_lost_while_armed_is_afe_fault(void)
{
    psim_board()->dut.kind = DUT_RESISTOR;
    psim_board()->dut.r_ohm = 1000.0;
    arm();
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SRC V=3"));
    h_run_us(500000);
    size_t from = h_lines();
    psim_board()->rail_pos = false;
    TEST_ASSERT_TRUE(h_run_until_state(ST_FAULT, 1000000));
    TEST_ASSERT_TRUE(h_wait_line("EVT FAULT", from, 100000));
    size_t k = h_find("EVT FAULT", from);
    TEST_ASSERT_EQUAL_STRING("EVT FAULT AFE detail=afe_signature", h_line(k));
    TEST_ASSERT_EQUAL(SIZE_MAX, h_find("EVT WARN COMPLIANCE", from));
    TEST_ASSERT_EQUAL_HEX16(0x8000, h_dac_a());
    psim_board()->rail_pos = true;
}

/* Compliance during an SRC ramp is checked on back-to-back monitor
 * conversions: the output stops within tens of mV of the crossing instead of
 * running on for up to 100 ms (1 V at 10 V/s). */
static void test_compliance_during_ramp_acts_promptly(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG i_compliance=0.005"));
    psim_board()->dut.kind = DUT_RESISTOR;
    psim_board()->dut.r_ohm = 10.0;
    arm();
    h_send("SRC V=5");
    double vmax = 0.0;
    uint64_t t0 = psim_time_us();
    while (psim_time_us() - t0 < 2000000u) {
        h_run_us(100);
        double v = h_vsweep();
        vmax = v > vmax ? v : vmax;
    }
    /* the limit is crossed at 5 mA x (10 + 121) ohm = 0.655 V */
    TEST_ASSERT_TRUE_MESSAGE(vmax < 0.70, "the ramp ran on past the compliance point");
    TEST_ASSERT_TRUE(vmax > 0.60);
    TEST_ASSERT_DOUBLE_WITHIN(0.01, 0.0, h_vsweep());
}

/* At the design pass budget (150 us) a point's first conversion starts up to
 * two passes after its settle: that is not LATE. */
static void test_no_late_flags_at_the_pass_budget(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=1000 n_avg=1"));
    psim_board()->dut.kind = DUT_RESISTOR;
    psim_board()->dut.r_ohm = 1000.0;
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 1 STEP=0.025"), "OK SWP", 6));
    arm();
    psim_set_pass_cost_us(150);
    size_t from = h_lines();
    h_send("SWP.RUN");
    TEST_ASSERT_TRUE(h_wait_line("EVT SWEEP_END", from, 20000000));
    psim_set_pass_cost_us(20);
    unsigned late = 0, pts = 0;
    for (size_t k = from; k < h_lines(); k++) {
        if (strncmp(h_line(k), "PT ", 3) == 0) {
            pts++;
            late += (pt_flags(h_line(k)) & PF_LATE) ? 1u : 0u;
        }
    }
    TEST_ASSERT_EQUAL_UINT32(41, pts);
    TEST_ASSERT_EQUAL_UINT32(0, late);
}

/* A standard deviation at its 6553.4-code ceiling is marked SD_SAT. */
static void test_saturated_sd_is_flagged(void)
{
    h_jacks_open(true);
    adsm_set_noise(psim_ads(), 20000.0, 7u);
    const char *r = h_cmd("MEAS? N=64");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK MEAS"), r);
    TEST_ASSERT_NOT_NULL(strstr(r, "i_sd=6553.4"));
    TEST_ASSERT_TRUE_MESSAGE((h_kv_ll(r, "flags") & PF_SD_SAT) != 0, r);
    adsm_set_noise(psim_ads(), 1.0, 7u);
    r = h_cmd("MEAS? N=64");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK MEAS"), r);
    TEST_ASSERT_EQUAL(0, h_kv_ll(r, "flags") & PF_SD_SAT);
    adsm_set_noise(psim_ads(), 0.0, 7u);
}

/* i_A keeps the resolution averaging gives. One current code is 12.44 nA
 * and i_A is printed to 0.1 nA. Two dithered 256-sample means half a code
 * apart must differ by about 6.2 nA; means rounded to whole codes (the old
 * firmware) can differ only by 0 or 12.4 nA. v_V is printed to 1 uV (0.67
 * of its 1.50 uV code), so its gain is smaller and it is not tested here. */
static void test_averaged_means_keep_sub_code_resolution(void)
{
    const double half_code_v = 0.5 * (2.0 * 2.5 / (2.0 * 8388607.0)); /* at the ADC input */
    h_jacks_open(true);
    adsm_set_noise(psim_ads(), 0.5, 3u);
    const char *r = h_cmd("MEAS? N=256");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK MEAS n=256"), r);
    double i0 = h_kv_d(r, "i_a");
    psim_board()->ina_i_offset_v += half_code_v;
    r = h_cmd("MEAS? N=256");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK MEAS n=256"), r);
    double di = h_kv_d(r, "i_a") - i0;
    psim_board()->ina_i_offset_v -= half_code_v;
    adsm_set_noise(psim_ads(), 0.0, 3u);
    /* sigma of the difference is about 0.6 nA (0.5 code noise, n = 256) */
    TEST_ASSERT_DOUBLE_WITHIN(2.5e-9, 6.22e-9, di);
}

/* The whole firmware keeps the ADS1256 model's timing and command rules
 * (t6, t10, t11, SCLK, reads outside t17, nothing sent while busy, SYNC always
 * completed by WAKEUP): init, data-rate changes, MEAS?, the monitor and an
 * armed sweep. */
static void test_adc_traffic_obeys_the_data_sheet(void)
{
    /* The simulator re-runs app_init() after an emulated reset without the
     * .bss clearing of the target's startup code, so the driver state left by
     * the previous test can send one transaction to the freshly powered model
     * before POST re-initialises it. That traffic does not exist on the
     * target: count from here, and run the full init again with SELFTEST. */
    adsm_clear_violations(psim_ads());
    uint32_t cals0 = adsm_selfcals(psim_ads());
    size_t from = h_lines();
    h_send("SELFTEST");
    TEST_ASSERT_TRUE(h_wait_line("OK", from, 3000000u));
    TEST_ASSERT_TRUE_MESSAGE(starts(h_line(h_find("OK", from)), "OK SELFTEST"), h_line(h_find("OK", from)));
    /* at least the init at PGA 1 and the reconfiguration to PGA 2 */
    uint32_t cals1 = adsm_selfcals(psim_ads());
    TEST_ASSERT_TRUE(cals1 - cals0 >= 2u);
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG adc_rate=100"));
    h_run_us(600000);
    TEST_ASSERT_EQUAL_UINT32(cals1 + 1u, adsm_selfcals(psim_ads()));
    const char *r = h_cmd("MEAS? N=4");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK MEAS n=4"), r);
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG adc_rate=1000"));
    h_run_us(100000);
    TEST_ASSERT_EQUAL_UINT32(cals1 + 2u, adsm_selfcals(psim_ads()));
    arm();
    r = h_cmd("SWP.WP 0 1 -1 0 STEP=0.25");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK SWP kind=wp points=17 "), r);
    size_t first = 0;
    char end[256];
    TEST_ASSERT_EQUAL(17, run_sweep(&first, end, sizeof end, 20000000u));
    adsm_violations_t v;
    adsm_get_violations(psim_ads(), &v);
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

/* A late sample's time is the centroid of the conversion it actually read.
 * At 2000 SPS (P = 500 us, t18 = 680 us, read deadline 464 us) a main-loop
 * stall of 650 us across a point's first I edge (short enough that the
 * heartbeat gap, with up to 250 us since its last edge, stays under 1 ms)
 * makes the read miss its deadline; the re-arm skips the
 * edge one period later and the read takes the one two periods later. That
 * conversion ends 2P = 1000 us after the on-time one would have, so t_i moves
 * by 1000 us (the old midpoint of WAKEUP and edge moved it by 500 us). */
static void test_late_sample_time_is_its_conversion_centroid(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG adc_rate=2000"));
    h_run_us(100000);
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG settle_us=20000 n_avg=1"));
    psim_board()->dut.kind = DUT_RESISTOR;
    psim_board()->dut.r_ohm = 1000.0;
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 0.5 STEP=0.25"), "OK SWP", 6));
    arm();
    size_t from = h_lines();
    uint16_t code0 = h_dac_a();
    h_send("SWP.RUN");
    /* point 0 is 0 V (no DAC change); point 1 (0.25 V) is the first change */
    uint64_t t_change = 0;
    for (uint64_t t0 = psim_time_us(); psim_time_us() - t0 < 2000000u;) {
        h_run_us(20);
        if (h_dac_a() != code0) {
            t_change = psim_time_us();
            break;
        }
    }
    TEST_ASSERT_TRUE(t_change != 0u);
    /* the I conversion's WAKEUP comes 1-2 passes after the 20 ms settle and its
     * first edge t18 = 680 us later (about 20.73 ms after the DAC write, which
     * the loop above sees within one pass): stall from about 100 us before
     * that edge to about 550 us after it (past the deadline and the next edge) */
    h_run_us(t_change + 20620u - psim_time_us());
    psim_advance_us(650);
    TEST_ASSERT_TRUE(h_wait_line("EVT SWEEP_END", from, 5000000));
    char f[17][32];
    long long d_i[3] = { 0, 0, 0 };
    unsigned flags[3] = { 0, 0, 0 };
    unsigned pts = 0;
    for (size_t k = from; k < h_lines(); k++) {
        if (starts(h_line(k), "PT ")) {
            TEST_ASSERT_EQUAL(17, pt_fields(h_line(k), f));
            long long idx = atoll(f[2]);
            TEST_ASSERT_TRUE(idx >= 0 && idx < 3);
            d_i[idx] = atoll(f[5]) - atoll(f[4]);   /* t_i_us - t_set_us */
            flags[idx] = (unsigned)strtoul(f[16], NULL, 16);
            pts++;
        }
    }
    TEST_ASSERT_EQUAL_UINT32(3, pts);
    TEST_ASSERT_TRUE_MESSAGE((flags[1] & PF_LATE) != 0, "the stalled point is not LATE");
    TEST_ASSERT_EQUAL_UINT32(0, flags[2] & PF_LATE);
    /* point 2 has the same settle and an on-time read: the reference */
    TEST_ASSERT_INT_WITHIN(100, 1000, (int)(d_i[1] - d_i[2]));
}

/* A host that stops reading and then closes the port leaves up to 4 KB of
 * output in TinyUSB's CDC TX FIFO (the simulator's device-side TX ring stands
 * for it). The firmware cannot withdraw it: TinyUSB's DWC2 driver may be
 * sending from that FIFO (protocol.md §1). When the port is opened again
 * that tail arrives first, cut off mid-line; the new session's empty line
 * ends it, so EVT HELLO starts a line of its own and the host can find it. */
static void test_reopened_port_hello_starts_a_line_after_the_old_tail(void)
{
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 2 STEP=0.001"), "OK SWP", 6));
    arm();
    h_send("SWP.RUN");
    psim_set_tx_space_limit(4096);           /* the CDC TX FIFO size */
    for (int k = 0; k < 20000; k++) {        /* the host is not reading */
        app_pass();
    }
    TEST_ASSERT_TRUE(psim_host_tx_pending() > 3000u);
    psim_set_dtr(false);                     /* it closes the port */
    for (int k = 0; k < 2000; k++) {
        app_pass();
    }
    psim_set_dtr(true);                      /* opens it again and reads */
    static uint8_t buf[8192];
    size_t n = 0;
    for (int k = 0; k < 2000 && n < sizeof buf - 1u; k++) {
        app_pass();
        n += psim_host_take_tx(buf + n, sizeof buf - 1u - n);
    }
    buf[n] = 0;
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
    const char *text = (const char *)buf;
    const char *hello = strstr(text, "EVT HELLO ");
    TEST_ASSERT_NOT_NULL_MESSAGE(hello, text);
    TEST_ASSERT_NULL_MESSAGE(strstr(hello + 1, "EVT HELLO "), "more than one HELLO");
    size_t tail = (size_t)(hello - text);
    /* the old session's tail came first: at most the FIFO plus the newline */
    TEST_ASSERT_TRUE_MESSAGE(tail > 3000u, "the old tail was not delivered");
    TEST_ASSERT_TRUE(tail <= 4096u + 1u);
    TEST_ASSERT_EQUAL_CHAR('\n', hello[-1]);
    TEST_ASSERT_TRUE_MESSAGE(hello[-2] != '\n', "the tail ended on a line boundary: case not exercised");
    TEST_ASSERT_NOT_NULL(strstr(hello, " disarm_reason=session_lost"));
    /* nothing of the old session after HELLO */
    TEST_ASSERT_NULL(strstr(hello, "\nPT "));
}

/* As above, without reading the reopened port: the old session's tail. */
static void leave_a_tail(void)
{
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.WP 0 2 STEP=0.001"), "OK SWP", 6));
    arm();
    h_send("SWP.RUN");
    psim_set_tx_space_limit(4096);           /* the CDC TX FIFO size */
    for (int k = 0; k < 20000; k++) {        /* the host is not reading */
        app_pass();
    }
    TEST_ASSERT_TRUE(psim_host_tx_pending() > 3000u);
    psim_set_dtr(false);                     /* it closes the port */
    for (int k = 0; k < 2000; k++) {
        app_pass();
    }
}

/* Like h_run_us(), but the host reads nothing (h_run_us() drains it). */
static void run_unread_us(uint64_t us)
{
    uint64_t end = psim_time_us() + us;
    while (psim_time_us() < end) {
        app_pass();
    }
}

static const char *reopen_and_read(void)
{
    static uint8_t buf[8192];
    psim_set_dtr(true);
    size_t n = 0;
    for (int k = 0; k < 2000 && n < sizeof buf - 1u; k++) {
        app_pass();
        n += psim_host_take_tx(buf + n, sizeof buf - 1u - n);
    }
    buf[n] = 0;
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
    return (const char *)buf;
}

/* A re-enumeration (here a cable pulled and plugged in again, or the
 * configuration dropped and set again) clears TinyUSB's CDC TX FIFO at
 * SET_CONFIGURATION: no old tail precedes the next session's HELLO. */
static void test_cable_replug_clears_the_old_tail(void)
{
    leave_a_tail();
    psim_set_vbus_mv(0);
    run_unread_us(500000);
    TEST_ASSERT_FALSE(psim_host_connected());
    psim_set_vbus_mv(5000);
    run_unread_us(500000);
    TEST_ASSERT_TRUE(psim_host_connected());
    const char *text = reopen_and_read();
    TEST_ASSERT_EQUAL_STRING_LEN_MESSAGE("\nEVT HELLO ", text, 11, text);
}

static void test_reconfiguration_clears_the_old_tail(void)
{
    leave_a_tail();
    psim_set_configured(false);
    run_unread_us(10000);
    psim_set_configured(true);
    run_unread_us(10000);
    const char *text = reopen_and_read();
    TEST_ASSERT_EQUAL_STRING_LEN_MESSAGE("\nEVT HELLO ", text, 11, text);
}

/* A VBUS dip that no pass samples (the firmware reads VBUS once per pass,
 * ADC1 every 10 ms) is no re-enumeration: the session and its unread
 * output stay, as on the target. */
static void test_unseen_vbus_dip_keeps_the_sessions_output(void)
{
    h_send("*IDN?");
    for (int k = 0; k < 200; k++) {          /* answered, not read */
        app_pass();
    }
    size_t pending = psim_host_tx_pending();
    TEST_ASSERT_TRUE(pending > 0u);
    psim_set_vbus_mv(0);
    psim_set_vbus_mv(5000);                  /* no pass in between */
    TEST_ASSERT_EQUAL_UINT32(pending, psim_host_tx_pending());
    const char *text = reopen_and_read();    /* DTR is still up: just reads */
    TEST_ASSERT_NOT_NULL_MESSAGE(strstr(text, "OK IDN "), text);
}

/* With the cable out the host cannot read the device; what is left is
 * cleared when the link comes back (test above). */
static void test_host_reads_nothing_while_the_link_is_down(void)
{
    h_send("*IDN?");
    for (int k = 0; k < 200; k++) {
        app_pass();
    }
    TEST_ASSERT_TRUE(psim_host_tx_pending() > 0u);
    psim_set_vbus_mv(0);
    uint8_t buf[64];
    TEST_ASSERT_EQUAL_UINT32(0, psim_host_take_tx(buf, sizeof buf));
    psim_set_vbus_mv(5000);
    TEST_ASSERT_TRUE(psim_host_take_tx(buf, sizeof buf) > 0u);
}

/* The printed value with its last digit moved by delta (all CAL? values are
 * positive fixed-point numbers). */
static void step_printed(const char *v, int delta, char *out, size_t cap)
{
    const char *dot = strchr(v, '.');
    TEST_ASSERT_NOT_NULL(dot);
    int d = (int)strlen(dot + 1);
    char digits[64];
    size_t j = 0;
    for (const char *p = v; *p != '\0' && j < sizeof digits - 1u; p++) {
        if (*p != '.') {
            digits[j++] = *p;
        }
    }
    digits[j] = '\0';
    long long x = strtoll(digits, NULL, 10) + delta;
    char tmp[64];
    snprintf(tmp, sizeof tmp, "%0*lld", d + 1, x);
    int len = (int)strlen(tmp);
    snprintf(out, cap, "%.*s.%s", len - d, tmp, tmp + len - d);
}

/* CAL.SET's measured rule (calib_differs_from_nominal) and CAL.SET/CAL?
 * (commands.c) use the same resolution for every key: the nominal value as
 * CAL? prints it counts as nominal, one step either side as measured. */
static void test_measured_rule_matches_the_cal_set_resolution(void)
{
    static const char *const keys[7] = {
        "g_dac_uv", "c_dac", "g_v_uv", "g_i_na", "r_s", "r_top", "i_plateau",
    };
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.DEFAULT"));
    char c[400];
    snprintf(c, sizeof c, "%s", h_cmd("CAL?"));
    for (unsigned k = 0; k < 7u; k++) {
        char nom[64], v[64], cmd[128];
        TEST_ASSERT_TRUE(h_kv(c, keys[k], nom, sizeof nom));
        static const int deltas[3] = { -1, 1, 0 };   /* ends on nominal */
        for (unsigned i = 0; i < 3u; i++) {
            int delta = deltas[i];
            step_printed(nom, delta, v, sizeof v);
            snprintf(cmd, sizeof cmd, "CAL.SET %s=%s", keys[k], v);
            TEST_ASSERT_EQUAL_STRING_MESSAGE("OK", h_cmd(cmd), cmd);
            unsigned m = (unsigned)h_kv_ll(h_cmd("CAL?"), "measured");
            TEST_ASSERT_EQUAL_HEX32_MESSAGE(delta == 0 ? 0u : 1u << k, m, cmd);
        }
    }
}

/* After a cable pull the D+ pull-up can back-feed the VBUS node through U10
 * to about 2.7...3.0 V (firmware_handoff.md §6; up to 3.09 V as read with the
 * ADC1 tolerances). That must read as "VBUS absent", or the pull-up holds
 * itself on. A valid VBUS (>= 4.4 V, 4.27 V as read) must keep the link. */
static void test_vbus_backfeed_releases_the_pullup(void)
{
    TEST_ASSERT_TRUE(psim_host_connected());
    psim_set_vbus_mv(4270);                 /* lowest valid VBUS, as read */
    h_run_us(1000000);
    TEST_ASSERT_TRUE(psim_host_connected());
    psim_set_configured(false);             /* cable pulled: host gone */
    psim_set_vbus_mv(3090);                 /* back-fed by the pull-up */
    h_run_us(500000);
    TEST_ASSERT_FALSE_MESSAGE(psim_host_connected(), "D+ pull-up held on by its own back-feed");
    psim_set_vbus_mv(5000);                 /* plugged in again */
    psim_set_configured(true);
    h_run_us(100000);
    TEST_ASSERT_TRUE(psim_host_connected());
}

/* Bring-up order (bringup_checklist.md §7 before §8): CAL.SET i_plateau and
 * CAL.SAVE on a unit whose other coefficients are still nominal. That is not
 * a calibration: records keep CAL_NOMINAL, STAT? keeps the warning and the
 * classifier keeps the nominal Kelvin tolerance, so a healthy unit with a
 * typical DAC zero error (2.5 mV x 8.03 = 20 mV at V_SWEEP) gets no false
 * KELVIN flags. Once every coefficient the tolerance depends on is set, the
 * marking goes. */
static bool warns_cal_nominal(void)
{
    return ((unsigned long long)h_kv_ll(h_cmd("STAT?"), "warn") & W_CAL_NOMINAL) != 0u;
}

static void test_partial_calibration_stays_nominal(void)
{
    psim_board()->sweep_offset_v = 0.020;
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET i_plateau=0.045"));
    TEST_ASSERT_EQUAL_STRING("OK seq=1", h_cmd("CAL.SAVE TS=1700000000"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("CAL?"), "OK CAL source=stored seq=1 measured=0x40 ", 41));
    TEST_ASSERT_TRUE(warns_cal_nominal());
    arm();
    unsigned fl;
    TEST_ASSERT_EQUAL(9, sweep_flags("SWP.WP 0 8 STEP=1", &fl));
    TEST_ASSERT_TRUE_MESSAGE((fl & PF_CAL_NOMINAL) != 0, "partial calibration not marked CAL_NOMINAL");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, fl & PF_KELVIN, "false KELVIN flags on nominal gains");
    /* the mask survives a reset */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("DISARM"));
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 3000000));
    psim_mcu_reset();
    app_init();
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 2000000));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("CAL?"), "OK CAL source=stored seq=1 measured=0x40 ", 41));
    /* the coefficients the tolerance depends on, one CAL.SET at a time
     * (measured values: each a last digit away from the nominal one) */
    psim_board()->sweep_offset_v = 0.002;
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET g_dac_uv=306.401388 c_dac=32767.5001"));
    TEST_ASSERT_TRUE(warns_cal_nominal());
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET g_v_uv=1.498588 g_i_na=12.435569 r_s=121.0001"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("CAL?"), "OK CAL source=ram seq=1 measured=0x5F ", 38));
    TEST_ASSERT_FALSE(warns_cal_nominal());
    const char *r = h_cmd("MEAS? N=4");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK MEAS n=4"), r);
    TEST_ASSERT_EQUAL_UINT32(0, (unsigned)h_kv_ll(r, "flags") & PF_CAL_NOMINAL);
    /* setting a coefficient to its nominal value (as CAL? prints it) makes it
     * nominal again: a CAL? round trip cannot pass nominal gains off as
     * measured ones */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET r_s=121"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("CAL?"), "OK CAL source=ram seq=1 measured=0x4F ", 38));
    TEST_ASSERT_TRUE(warns_cal_nominal());
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET r_s=121.0001"));
    TEST_ASSERT_FALSE(warns_cal_nominal());
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.DEFAULT"));
    TEST_ASSERT_TRUE(warns_cal_nominal());
}

/* CAL_NOMINAL needs every one of g_dac, c_dac, g_v, g_i and r_s measured (the
 * Kelvin tolerance depends on each; r_s and g_i carry the I*R_s term): with
 * any one of them nominal, and everything else measured, records and STAT?
 * keep the marking. */
static void test_each_core_coefficient_keeps_cal_nominal(void)
{
    static const char *const measured[7] = {
        "g_dac_uv=306.52", "c_dac=32790.3", "g_v_uv=1.4993", "g_i_na=12.4211",
        "r_s=118.7", "r_top=104.2", "i_plateau=0.047",
    };
    for (unsigned skip = 0; skip < 5u; skip++) {     /* the five CAL_M_CORE keys */
        TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.DEFAULT"));
        char set[200] = "CAL.SET";
        for (unsigned k = 0; k < 7u; k++) {
            if (k != skip) {
                size_t l = strlen(set);
                snprintf(set + l, sizeof set - l, " %s", measured[k]);
            }
        }
        TEST_ASSERT_EQUAL_STRING_MESSAGE("OK", h_cmd(set), set);
        TEST_ASSERT_EQUAL_HEX32(CAL_M_ALL & ~(1u << skip),
                                (unsigned)h_kv_ll(h_cmd("CAL?"), "measured"));
        TEST_ASSERT_TRUE_MESSAGE(warns_cal_nominal(), set);
        const char *r = h_cmd("MEAS? N=1");
        TEST_ASSERT_TRUE_MESSAGE(starts(r, "OK MEAS n=1"), r);
        TEST_ASSERT_TRUE_MESSAGE(((unsigned)h_kv_ll(r, "flags") & PF_CAL_NOMINAL) != 0u, set);
    }
    /* the five alone are enough (r_top and i_plateau may stay nominal) */
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.DEFAULT"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET g_dac_uv=306.52 c_dac=32790.3 g_v_uv=1.4993"
                                         " g_i_na=12.4211 r_s=118.7"));
    TEST_ASSERT_EQUAL_HEX32(CAL_M_CORE, (unsigned)h_kv_ll(h_cmd("CAL?"), "measured"));
    TEST_ASSERT_FALSE(warns_cal_nominal());
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.DEFAULT"));
}

/* A newest record that has gone bad (here: one flipped bit) is not used; the
 * older one is, and STAT? says so (CAL_DAMAGED) until a save overwrites it. */
static void test_damaged_newest_record_is_reported(void)
{
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET r_s=118.5"));
    TEST_ASSERT_EQUAL_STRING("OK seq=1", h_cmd("CAL.SAVE TS=1700000000"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CAL.SET r_s=119.5"));
    TEST_ASSERT_EQUAL_STRING("OK seq=2", h_cmd("CAL.SAVE TS=1700000001"));
    TEST_ASSERT_EQUAL(0, h_kv_ll(h_cmd("STAT?"), "warn") & W_CAL_DAMAGED);
    psim_cal_sector_mut(1)[40] ^= 0x04u;      /* seq 2 is in sector 1 */
    psim_mcu_reset();
    app_init();
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 2000000));
    const char *r = h_cmd("CAL?");
    TEST_ASSERT_EQUAL(0, strncmp(r, "OK CAL source=stored seq=1 ", 27));
    TEST_ASSERT_NOT_NULL(strstr(r, " r_s=118.5000"));
    TEST_ASSERT_TRUE((h_kv_ll(h_cmd("STAT?"), "warn") & W_CAL_DAMAGED) != 0);
    TEST_ASSERT_EQUAL_STRING("OK seq=2", h_cmd("CAL.SAVE TS=1700000002"));
    TEST_ASSERT_EQUAL(0, h_kv_ll(h_cmd("STAT?"), "warn") & W_CAL_DAMAGED);
}

/* Sweep records keep the sub-code resolution too: the PT line's i_A comes
 * from the record's rounded mean plus its 1/256-code fraction. Two dithered
 * one-point sweeps of 256 pairs, half a code apart, differ by about 6.2 nA;
 * whole-code means could differ only by 0 or 12.4 nA, and a fraction of the
 * wrong sign would cancel the shift. */
static void test_sweep_records_keep_sub_code_resolution(void)
{
    const double half_code_v = 0.5 * (2.0 * 2.5 / (2.0 * 8388607.0));
    h_jacks_open(true);
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("CFG n_avg=256"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd("SWP.LIST.CLR"));
    TEST_ASSERT_EQUAL_STRING("OK n=1", h_cmd("SWP.LIST.ADD 0"));
    TEST_ASSERT_EQUAL(0, strncmp(h_cmd("SWP.LIST.USE"), "OK SWP kind=list points=1 ", 26));
    adsm_set_noise(psim_ads(), 0.5, 3u);
    arm();
    double i_a[2];
    for (int k = 0; k < 2; k++) {
        size_t first = 0;
        char end[256];
        TEST_ASSERT_EQUAL(1, run_sweep(&first, end, sizeof end, 20000000u));
        char f[17][32];
        TEST_ASSERT_EQUAL(17, pt_fields(h_line(first), f));
        TEST_ASSERT_EQUAL_STRING("256", f[9]);
        i_a[k] = atof(f[15]);
        psim_board()->ina_i_offset_v += half_code_v;
    }
    psim_board()->ina_i_offset_v -= 2.0 * half_code_v;
    adsm_set_noise(psim_ads(), 0.0, 3u);
    TEST_ASSERT_DOUBLE_WITHIN(2.5e-9, 6.22e-9, i_a[1] - i_a[0]);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_errors_and_one_response_per_command);
    RUN_TEST(test_numbers_exact_or_rejected);
    RUN_TEST(test_cfg_all_or_nothing);
    RUN_TEST(test_identity);
    RUN_TEST(test_wp_divisibility_rule);
    RUN_TEST(test_hysteresis_loop_identical_codes);
    RUN_TEST(test_records_carry_measured_values);
    RUN_TEST(test_list_sweep_settles);
    RUN_TEST(test_sweep_rejected_beyond_v_limit);
    RUN_TEST(test_abort_ramps_to_zero);
    RUN_TEST(test_stalled_host_short_sweep_loses_nothing);
    RUN_TEST(test_long_stall_overflows_explicitly);
    RUN_TEST(test_fetch_during_sweep_does_not_overflow);
    RUN_TEST(test_fetch_resends_retained);
    RUN_TEST(test_event_loss_is_reported);
    RUN_TEST(test_meas_and_zero);
    RUN_TEST(test_zero_accepts_worst_case_front_end_offsets);
    RUN_TEST(test_open_sense_plus_on_low_impedance_dut_is_flagged_kelvin);
    RUN_TEST(test_active_dut_on_the_plateau_keeps_clim);
    RUN_TEST(test_plateau_on_short_is_still_clim);
    RUN_TEST(test_default_compliance_lets_the_plateau_act);
    RUN_TEST(test_rail_lost_while_armed_is_afe_fault);
    RUN_TEST(test_compliance_during_ramp_acts_promptly);
    RUN_TEST(test_no_late_flags_at_the_pass_budget);
    RUN_TEST(test_saturated_sd_is_flagged);
    RUN_TEST(test_averaged_means_keep_sub_code_resolution);
    RUN_TEST(test_sweep_records_keep_sub_code_resolution);
    RUN_TEST(test_adc_traffic_obeys_the_data_sheet);
    RUN_TEST(test_late_sample_time_is_its_conversion_centroid);
    RUN_TEST(test_reopened_port_hello_starts_a_line_after_the_old_tail);
    RUN_TEST(test_cable_replug_clears_the_old_tail);
    RUN_TEST(test_reconfiguration_clears_the_old_tail);
    RUN_TEST(test_unseen_vbus_dip_keeps_the_sessions_output);
    RUN_TEST(test_host_reads_nothing_while_the_link_is_down);
    RUN_TEST(test_measured_rule_matches_the_cal_set_resolution);
    RUN_TEST(test_vbus_backfeed_releases_the_pullup);
    RUN_TEST(test_partial_calibration_stays_nominal);
    RUN_TEST(test_each_core_coefficient_keeps_cal_nominal);
    RUN_TEST(test_damaged_newest_record_is_reported);
    RUN_TEST(test_zero_armed_requires_zero_output);
    RUN_TEST(test_cal_save_reload_and_torn_write);
    RUN_TEST(test_open_sense_plus_flags_kelvin);
    RUN_TEST(test_force_open_flags_no_output);
    RUN_TEST(test_current_limit_flags_clim);
    RUN_TEST(test_compliance_ends_sweep);
    RUN_TEST(test_session_loss_during_deferred_job_frees_the_slot);
    RUN_TEST(test_events_never_overtake_earlier_records);
    RUN_TEST(test_duplicate_keys_differing_in_case_rejected);
    RUN_TEST(test_integer_keys_accept_the_number_grammar);
    RUN_TEST(test_stalled_host_state_events_never_lost);
    RUN_TEST(test_stalled_host_back_to_back_sweeps_keep_their_events);
    RUN_TEST(test_disarm_acts_while_commands_are_held);
    RUN_TEST(test_swp_abort_acts_while_commands_are_held);
    RUN_TEST(test_disarm_during_deferred_measurement);
    RUN_TEST(test_early_abort_never_leaves_a_later_sweep_running);
    RUN_TEST(test_early_disarm_still_acts_after_a_held_arm);
    RUN_TEST(test_abort_and_disarm_in_one_packet);
    RUN_TEST(test_padded_disarm_acts_early);
    RUN_TEST(test_writer_only_host_is_disarmed_by_the_host_timeout);
    RUN_TEST(test_waiting_disarm_undoes_an_arm_ahead_of_it_in_a_backlog);
    return UNITY_END();
}
