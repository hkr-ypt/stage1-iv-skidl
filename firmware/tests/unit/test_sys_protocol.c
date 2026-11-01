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
    const char *r = h_cmd("ZERO");
    TEST_ASSERT_TRUE_MESSAGE(starts(r, "ERR RANGE implausible"), r);   /* DUT connected */
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
    char before[400];
    snprintf(before, sizeof before, "%s", c + strlen("OK CAL source=stored seq=2"));
    TEST_ASSERT_EQUAL_STRING("OK", h_cmd(set));
    TEST_ASSERT_EQUAL_STRING(before, h_cmd("CAL?") + strlen("OK CAL source=ram seq=2"));
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

static bool arming_or_armed(void)
{
    return h_state() == ST_ARMING || h_state() == ST_ARMED;
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
    /* the held ARM runs once MEAS? is done; the DISARM then acts at once,
     * already during ARMING */
    TEST_ASSERT_TRUE(h_run_until(arming_or_armed, 10000000));
    TEST_ASSERT_TRUE(h_run_until_state(ST_DISARMED, 3000000));
    TEST_ASSERT_FALSE(h_gate());
    psim_set_tx_space_limit(PSIM_TX_NO_LIMIT);
    h_run_us(2000000);
    TEST_ASSERT_EQUAL_UINT32(9, count_responses(from));
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
    return UNITY_END();
}
