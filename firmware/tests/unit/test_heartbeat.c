/*
 * test_heartbeat.c — unit tests of drivers/heartbeat.c with a fake clock
 * (tests/unit/support/heartbeat).
 *
 * Requirements checked (docs/firmware/firmware_spec.md §4.4, §7.4, SI-2):
 *  - edges only while permitted and enabled, at most one per pass, never
 *    "once per pass";
 *  - fixed 250 us grid: with random passes of 5..150 us (fixed seed) every
 *    edge lands in the first pass after its grid time, the fundamental is
 *    exactly 2 kHz (4000 +- 2 edges per 10^6 us; no drift over 10^6 edges);
 *  - a pass more than a half period late resynchronises once, without a
 *    burst of short spacings;
 *  - gap fault: spacing 1000 us passes, 1001 us returns HB_GAP_FAULT without
 *    toggling; no false fault on the first pass after hb_enable;
 *  - disable drives PC5 low and leaves it low without moving last_edge_us;
 *  - exception context: nothing touched, HB_NOT_THREAD;
 *  - init resets all state; bring-up HB.FREQ range and gap scaling and the
 *    HB.EDGE/HB.HOLD static level (FW_BRINGUP), absent symbols otherwise.
 */
#include <string.h>

#include "heartbeat.h"
#include "heartbeat/hb_test_port.h"
#include "unity.h"

#if !FW_BRINGUP
extern bool hb_set_frequency_hz(uint32_t hz) __attribute__((weak));
extern bool hb_bringup_hold(bool high) __attribute__((weak));
extern bool hb_bringup_holding(void) __attribute__((weak));
#endif

void setUp(void)
{
    htp_reset();
    hb_init();
}

void tearDown(void)
{
}

/* ---- helpers ------------------------------------------------------------------ */

static uint32_t rng_state;
static uint32_t rnd(void)
{
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 17;
    rng_state ^= rng_state << 5;
    return rng_state;
}

static uint32_t rnd_range(uint32_t lo, uint32_t hi)
{
    return lo + rnd() % (hi - lo + 1u);
}

/* One main-loop pass of `len` us ending in hb_service(permitted). */
static hb_result_t pass(uint32_t len, bool permitted)
{
    htp.now += len;
    return hb_service(permitted);
}

/* ---- init / enable / disable ------------------------------------------------------ */

static void test_init_drives_low_and_clears(void)
{
    TEST_ASSERT_TRUE(htp.writes >= 1u);
    TEST_ASSERT_FALSE(htp.level);
    TEST_ASSERT_EQUAL_UINT64(0, htp.writes_high);
    TEST_ASSERT_FALSE(hb_enabled());
    TEST_ASSERT_FALSE(hb_started());
    TEST_ASSERT_EQUAL_UINT32(250, hb_half_period_us());
    TEST_ASSERT_EQUAL_UINT32(1000, hb_gap_limit_us());
    hb_stats_t s;
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT64(0, s.edges);
    TEST_ASSERT_EQUAL_UINT32(0, s.max_spacing_us + s.late_spacings + s.resyncs);

    /* run, then re-init (the simulator's MCU reset): everything cleared */
    hb_enable();
    for (int i = 0; i < 100; i++) {
        TEST_ASSERT_EQUAL(HB_OK, pass(97, true));
    }
    TEST_ASSERT_TRUE(htp.rises > 0u);
    htp.level = true; /* make the low write observable */
    hb_init();
    TEST_ASSERT_FALSE(htp.level);
    TEST_ASSERT_FALSE(hb_enabled());
    TEST_ASSERT_FALSE(hb_started());
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT64(0, s.edges);
    TEST_ASSERT_EQUAL_UINT64(0, s.last_edge_us);
    TEST_ASSERT_EQUAL_UINT32(0, s.max_spacing_us);
    /* not enabled after init: permitted passes produce nothing */
    uint64_t w = htp.writes_high;
    for (int i = 0; i < 100; i++) {
        TEST_ASSERT_EQUAL(HB_OK, pass(50, true));
    }
    TEST_ASSERT_EQUAL_UINT64(w, htp.writes_high);
}

static void test_first_edge_on_next_service(void)
{
    htp.now += 5000000u; /* a long disabled period */
    hb_enable();
    TEST_ASSERT_TRUE(hb_enabled());
    TEST_ASSERT_FALSE(hb_started());
    TEST_ASSERT_EQUAL_UINT64(0, htp.rises); /* enable itself makes no edge */
    TEST_ASSERT_EQUAL(HB_OK, pass(120, true));
    TEST_ASSERT_EQUAL_UINT64(1, htp.rises);
    TEST_ASSERT_TRUE(htp.level);
    TEST_ASSERT_TRUE(hb_started());
    hb_stats_t s;
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT64(htp.now, s.first_edge_us);
    TEST_ASSERT_EQUAL_UINT64(htp.now, s.last_edge_us);
    TEST_ASSERT_EQUAL_UINT32(0, s.resyncs);
    /* the grid is anchored at the first edge: a full half period follows */
    uint64_t e0 = htp.now;
    TEST_ASSERT_EQUAL(HB_OK, pass(249, true));
    TEST_ASSERT_EQUAL_UINT64(1, htp.rises + htp.falls);
    TEST_ASSERT_EQUAL(HB_OK, pass(1, true));
    TEST_ASSERT_EQUAL_UINT64(1, htp.falls);
    TEST_ASSERT_EQUAL_UINT64(e0 + 250u, htp.last_change_us);
}

static void test_first_service_after_enable_never_faults(void)
{
    /* after running and a long disabled period (with and without passes) */
    hb_enable();
    for (int i = 0; i < 50; i++) {
        TEST_ASSERT_EQUAL(HB_OK, pass(60, true));
    }
    TEST_ASSERT_EQUAL(HB_OK, pass(60, false));
    TEST_ASSERT_FALSE(htp.level);
    htp.now += 10000000u;
    hb_enable();
    TEST_ASSERT_EQUAL(HB_OK, pass(100, true));
    TEST_ASSERT_TRUE(htp.level);
    hb_disable();
    for (int i = 0; i < 1000; i++) {
        TEST_ASSERT_EQUAL(HB_OK, pass(1000, false));
    }
    hb_enable();
    TEST_ASSERT_EQUAL(HB_OK, pass(150, true));
    TEST_ASSERT_TRUE(htp.level);
    /* the enable time is the gap reference: 1000 us is fine, 1001 is not */
    hb_disable();
    hb_enable();
    TEST_ASSERT_EQUAL(HB_OK, pass(1000, true));
    hb_disable();
    hb_enable();
    TEST_ASSERT_EQUAL(HB_GAP_FAULT, pass(1001, true));
}

static void test_reenable_does_not_reset_gap_reference(void)
{
    hb_enable();
    TEST_ASSERT_EQUAL(HB_OK, pass(10, true)); /* first edge */
    htp.now += 900u;
    hb_enable(); /* already enabled: must not hide the gap */
    TEST_ASSERT_EQUAL(HB_GAP_FAULT, pass(101, true));
}

static void test_disable_leaves_pc5_low(void)
{
    hb_enable();
    TEST_ASSERT_EQUAL(HB_OK, pass(10, true));
    TEST_ASSERT_TRUE(htp.level);
    htp.now += 37u;
    hb_disable();
    TEST_ASSERT_FALSE(htp.level);
    TEST_ASSERT_FALSE(hb_enabled());
    TEST_ASSERT_FALSE(hb_started());
    hb_stats_t s;
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT64(htp.now, s.last_edge_us); /* the falling edge */
    TEST_ASSERT_EQUAL_UINT64(2, s.edges);
    uint64_t last = s.last_edge_us;
    uint64_t hi = htp.writes_high;
    /* DISARMED: hb_service(false) every pass keeps it low, last edge fixed */
    for (int i = 0; i < 10000; i++) {
        TEST_ASSERT_EQUAL(HB_OK, pass(rnd_range(5, 150), false));
        TEST_ASSERT_FALSE(htp.level);
    }
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT64(last, s.last_edge_us);
    TEST_ASSERT_EQUAL_UINT64(2, s.edges);
    TEST_ASSERT_EQUAL_UINT64(hi, htp.writes_high);

    /* disabling while low: no new edge, last_edge_us is the real last edge */
    hb_enable();
    TEST_ASSERT_EQUAL(HB_OK, pass(10, true));  /* rise */
    TEST_ASSERT_EQUAL(HB_OK, pass(250, true)); /* fall */
    TEST_ASSERT_FALSE(htp.level);
    uint64_t fall_t = htp.now;
    htp.now += 100u;
    TEST_ASSERT_EQUAL(HB_OK, hb_service(false));
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT64(fall_t, s.last_edge_us);
    TEST_ASSERT_EQUAL_UINT64(4, s.edges);
    TEST_ASSERT_FALSE(hb_enabled());
    /* permitted again but not re-enabled: nothing */
    TEST_ASSERT_EQUAL(HB_OK, pass(300, true));
    TEST_ASSERT_FALSE(htp.level);
}

static void test_exception_context_touches_nothing(void)
{
    hb_enable();
    TEST_ASSERT_EQUAL(HB_OK, pass(10, true));
    TEST_ASSERT_TRUE(htp.level);
    uint64_t w = htp.writes;
    hb_stats_t s0, s1;
    hb_get_stats(&s0);
    htp.in_exception = true;
    TEST_ASSERT_EQUAL(HB_NOT_THREAD, pass(300, true)); /* an edge would be due */
    TEST_ASSERT_EQUAL(HB_NOT_THREAD, pass(10, false)); /* even "not permitted" */
    TEST_ASSERT_EQUAL(HB_NOT_THREAD, pass(2000, true));
    TEST_ASSERT_EQUAL_UINT64(w, htp.writes);
    TEST_ASSERT_EQUAL_UINT64(0, htp.writes_in_exception);
    TEST_ASSERT_TRUE(htp.level);
    TEST_ASSERT_TRUE(hb_enabled());
    hb_get_stats(&s1);
    TEST_ASSERT_EQUAL_MEMORY(&s0, &s1, sizeof(s0));
    /* back in thread mode the gap is still measured from the real last edge */
    htp.in_exception = false;
    TEST_ASSERT_EQUAL(HB_GAP_FAULT, pass(1, true));
    TEST_ASSERT_EQUAL_UINT64(w, htp.writes);
}

/* ---- the grid ------------------------------------------------------------------------ */

static struct {
    uint64_t pass_start;    /* time of the previous hb_service call */
    uint64_t service_calls;
    uint64_t edges;         /* edges seen through the hook in this run */
    uint64_t edges_this_call;
    uint64_t e0;            /* grid anchor (first edge) */
    uint64_t prev_edge;
    uint64_t max_late;      /* edge time - grid time */
    uint64_t min_spacing, max_spacing;
    uint64_t window_edges;  /* edges in [e0, e0 + 10^6) */
    uint64_t bad_grid;      /* edges outside [grid, previous pass end] */
    uint64_t multi_edge_calls;
    uint32_t half;
} g;

static void grid_hook(bool level, uint64_t t)
{
    (void)level;
    if (g.edges == 0u) {
        g.e0 = t;
    } else {
        uint64_t sp = t - g.prev_edge;
        if (sp < g.min_spacing) {
            g.min_spacing = sp;
        }
        if (sp > g.max_spacing) {
            g.max_spacing = sp;
        }
    }
    uint64_t grid = g.e0 + g.edges * g.half;
    /* each edge lands in the first pass at or after its grid time:
     * grid <= t, and the previous pass was still before the grid time */
    if (t < grid || (g.edges > 0u && g.pass_start >= grid)) {
        g.bad_grid++;
    }
    if (t - grid > g.max_late) {
        g.max_late = t - grid;
    }
    if (t < g.e0 + 1000000u) {
        g.window_edges++;
    }
    g.prev_edge = t;
    g.edges++;
    g.edges_this_call++;
}

static void grid_reset(uint32_t half)
{
    memset(&g, 0, sizeof(g));
    g.min_spacing = UINT64_MAX;
    g.half = half;
    htp.on_edge = grid_hook;
}

static void grid_pass(uint32_t len)
{
    g.pass_start = htp.now;
    htp.now += len;
    g.edges_this_call = 0;
    TEST_ASSERT_EQUAL(HB_OK, hb_service(true));
    if (g.edges_this_call > 1u) {
        g.multi_edge_calls++;
    }
    g.service_calls++;
}

static void test_grid_random_passes_one_million_edges(void)
{
    rng_state = 0x9E3779B9u;
    grid_reset(250);
    hb_enable();
    uint64_t max_pass = 0;
    while (g.edges < 1000000u) {
        uint32_t len = rnd_range(5, 150);
        if (len > max_pass) {
            max_pass = len;
        }
        grid_pass(len);
    }
    hb_stats_t s;
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT64(0, g.bad_grid);
    TEST_ASSERT_EQUAL_UINT64(0, g.multi_edge_calls);
    TEST_ASSERT_TRUE(g.max_late < max_pass);          /* within one pass of its grid time */
    TEST_ASSERT_TRUE(g.window_edges >= 3998u && g.window_edges <= 4002u);
    /* no drift: edge 10^6 - 1 is still on the grid of the first one */
    TEST_ASSERT_TRUE(g.prev_edge - (g.e0 + (g.edges - 1u) * 250u) < 150u);
    TEST_ASSERT_TRUE(g.min_spacing >= 250u - 149u);
    TEST_ASSERT_TRUE(g.max_spacing <= 250u + 149u);
    TEST_ASSERT_EQUAL_UINT32(0, s.resyncs);
    TEST_ASSERT_EQUAL_UINT32(0, s.late_spacings);
    TEST_ASSERT_EQUAL_UINT32((uint32_t)g.max_spacing, s.max_spacing_us);
    TEST_ASSERT_EQUAL_UINT64(g.edges, s.edges);
    TEST_ASSERT_EQUAL_UINT64(g.e0, s.first_edge_us);
    TEST_ASSERT_EQUAL_UINT64(g.prev_edge, s.last_edge_us);
    /* never once per pass: ~3x more passes than edges */
    TEST_ASSERT_TRUE(g.service_calls > 3u * g.edges);
}

static void test_alternating_short_long_passes_keep_even_duty(void)
{
    /* the pathological 5 us / 150 us alternation of spec §4.4 */
    grid_reset(250);
    hb_enable();
    for (int i = 0; i < 200000; i++) {
        grid_pass((i & 1) ? 150u : 5u);
    }
    TEST_ASSERT_EQUAL_UINT64(0, g.bad_grid);
    TEST_ASSERT_TRUE(g.min_spacing >= 100u);
    TEST_ASSERT_TRUE(g.max_spacing <= 400u);
    TEST_ASSERT_TRUE(g.window_edges >= 3998u && g.window_edges <= 4002u);
    /* duty: high and low times both average 250 us */
    hb_stats_t s;
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(0, s.resyncs);
}

static void test_tiny_passes_do_not_toggle_every_pass(void)
{
    grid_reset(250);
    hb_enable();
    for (int i = 0; i < 100000; i++) {
        grid_pass(5);
    }
    TEST_ASSERT_TRUE(g.min_spacing >= 250u && g.max_spacing < 255u);
    TEST_ASSERT_EQUAL_UINT64(100000u * 5u / 250u, g.edges); /* 2000 edges, not 100000 */
}

static void test_late_pass_resyncs_without_burst(void)
{
    static const uint32_t late_len[] = {260, 299, 300, 400, 499, 500, 600, 750, 900};
    static const uint64_t since_target[] = {0, 100, 200};
    for (unsigned j = 0; j < 3u * (sizeof(late_len) / sizeof(late_len[0])); j++) {
        unsigned i = j / 3u;
        htp_reset();
        hb_init();
        grid_reset(250);
        hb_enable();
        for (int p = 0; p < 1000; p++) {
            grid_pass(50);
        }
        /* passes of 50 us on a 250 us grid: edges exactly on the grid; walk
         * on until the last edge is `since_target` us old */
        while (htp.now - g.prev_edge != since_target[j % 3u]) {
            grid_pass(50);
        }
        uint64_t since_edge = htp.now - g.prev_edge;
        uint32_t len = late_len[i];
        if (since_edge + len > 1000u) {
            len = (uint32_t)(1000u - since_edge);
        }
        hb_stats_t s0, s1;
        hb_get_stats(&s0);
        uint64_t edges_before = g.edges;
        grid_pass(len);
        TEST_ASSERT_EQUAL_UINT64(edges_before + 1u, g.edges); /* one late edge, no burst */
        uint64_t late_edge = g.prev_edge;
        hb_get_stats(&s1);
        bool expect_resync = (since_edge + len) >= 500u; /* >= half past the due time */
        TEST_ASSERT_EQUAL_UINT32(s0.resyncs + (expect_resync ? 1u : 0u), s1.resyncs);
        /* the following spacings: no short burst after a resync */
        g.min_spacing = UINT64_MAX;
        uint64_t first_after = 0;
        for (int p = 0; p < 200; p++) {
            grid_pass(50);
            if (first_after == 0u && g.prev_edge != late_edge) {
                first_after = g.prev_edge;
            }
        }
        TEST_ASSERT_EQUAL_UINT64(0, g.multi_edge_calls);
        if (expect_resync) {
            TEST_ASSERT_TRUE(first_after - late_edge >= 250u);
            TEST_ASSERT_TRUE(g.min_spacing >= 250u);
        } else {
            TEST_ASSERT_TRUE(g.min_spacing >= 1u);
        }
        TEST_ASSERT_TRUE(g.max_spacing <= 1000u);
    }
}

static void test_resync_boundary(void)
{
    hb_enable();
    TEST_ASSERT_EQUAL(HB_OK, pass(10, true)); /* edge at t0, next due t0+250 */
    TEST_ASSERT_EQUAL(HB_OK, pass(499, true)); /* 249 us late: no resync */
    hb_stats_t s;
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(0, s.resyncs);
    TEST_ASSERT_EQUAL_UINT64(2, s.edges);
    /* next due at t0+500, i.e. 1 us from now: grid kept */
    TEST_ASSERT_EQUAL(HB_OK, pass(1, true));
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT64(3, s.edges);
    /* now exactly a half period late -> resync */
    TEST_ASSERT_EQUAL(HB_OK, pass(500, true));
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(1, s.resyncs);
    TEST_ASSERT_EQUAL_UINT64(4, s.edges);
    TEST_ASSERT_EQUAL(HB_OK, pass(249, true));
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT64(4, s.edges);
    TEST_ASSERT_EQUAL(HB_OK, pass(1, true));
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT64(5, s.edges);
}

/* ---- gap fault ------------------------------------------------------------------------- */

static void test_gap_fault_boundary(void)
{
    hb_enable();
    TEST_ASSERT_EQUAL(HB_OK, pass(10, true));
    TEST_ASSERT_EQUAL(HB_OK, pass(1000, true)); /* spacing exactly 1000: allowed */
    hb_stats_t s;
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(1000, s.max_spacing_us);
    TEST_ASSERT_EQUAL_UINT32(1, s.late_spacings);
    bool lvl = htp.level;
    uint64_t w = htp.writes;
    TEST_ASSERT_EQUAL(HB_GAP_FAULT, pass(1001, true));
    TEST_ASSERT_EQUAL_UINT64(w, htp.writes);   /* checked before toggling */
    TEST_ASSERT_EQUAL(lvl, htp.level);
    TEST_ASSERT_EQUAL(HB_GAP_FAULT, pass(5, true)); /* stays faulted */
    TEST_ASSERT_EQUAL_UINT64(w, htp.writes);
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(1000, s.max_spacing_us); /* never an edge beyond the limit */
    /* the caller's FAULT entry disables: PC5 low */
    TEST_ASSERT_EQUAL(HB_OK, pass(5, false));
    TEST_ASSERT_FALSE(htp.level);
}

static void test_gap_fault_on_slow_steady_loop(void)
{
    /* passes of 1001 us every time: the first spacing already faults */
    hb_enable();
    TEST_ASSERT_EQUAL(HB_OK, pass(1, true));
    TEST_ASSERT_EQUAL(HB_GAP_FAULT, pass(1001, true));
    /* passes of 600 us: spacing 600 each (late, resync), no fault */
    hb_disable();
    hb_enable();
    hb_stats_t s0, s1;
    hb_get_stats(&s0);
    for (int i = 0; i < 100; i++) {
        TEST_ASSERT_EQUAL(HB_OK, pass(600, true));
    }
    hb_get_stats(&s1);
    TEST_ASSERT_EQUAL_UINT64(s0.edges + 100u, s1.edges);
    TEST_ASSERT_EQUAL_UINT32(s0.late_spacings + 99u, s1.late_spacings);
    TEST_ASSERT_EQUAL_UINT32(s0.resyncs + 99u, s1.resyncs);
}

/* ---- permission ------------------------------------------------------------------------- */

static uint64_t g_rises_unpermitted;
static bool g_in_permitted_call;
static void perm_hook(bool level, uint64_t t)
{
    (void)t;
    if (level && !g_in_permitted_call) {
        g_rises_unpermitted++;
    }
}

static void test_edges_only_when_permitted(void)
{
    rng_state = 12345u;
    htp.on_edge = perm_hook;
    g_rises_unpermitted = 0;
    bool permitted = false;
    uint64_t edges_permitted = 0;
    for (int i = 0; i < 300000; i++) {
        if ((rnd() % 500u) == 0u) {
            permitted = !permitted;
            if (permitted) {
                hb_enable(); /* the instrument enables when it arms */
            }
        }
        uint64_t r0 = htp.rises + htp.falls;
        g_in_permitted_call = permitted;
        hb_result_t res = pass(rnd_range(5, 150), permitted);
        g_in_permitted_call = false;
        TEST_ASSERT_EQUAL(HB_OK, res);
        if (!permitted) {
            TEST_ASSERT_FALSE(htp.level);
            TEST_ASSERT_FALSE(hb_enabled());
        } else {
            edges_permitted += htp.rises + htp.falls - r0;
        }
        TEST_ASSERT_TRUE(htp.rises + htp.falls - r0 <= 1u);
    }
    TEST_ASSERT_EQUAL_UINT64(0, g_rises_unpermitted);
    TEST_ASSERT_TRUE(edges_permitted > 10000u);
}

/* ---- bring-up ---------------------------------------------------------------------------- */

#if FW_BRINGUP
static void test_bringup_frequency_range_and_gap(void)
{
    TEST_ASSERT_FALSE(hb_set_frequency_hz(0));
    TEST_ASSERT_FALSE(hb_set_frequency_hz(499));
    TEST_ASSERT_FALSE(hb_set_frequency_hz(5001));
    TEST_ASSERT_EQUAL_UINT32(250, hb_half_period_us());
    TEST_ASSERT_EQUAL_UINT32(1000, hb_gap_limit_us());

    static const struct {
        uint32_t hz, half, gap;
    } cases[] = {
        {500, 1000, 1500}, {667, 750, 1250}, {1000, 500, 1000}, {1500, 333, 1000},
        {2000, 250, 1000}, {3000, 167, 1000}, {5000, 100, 1000},
    };
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        TEST_ASSERT_TRUE(hb_set_frequency_hz(cases[i].hz));
        TEST_ASSERT_EQUAL_UINT32(cases[i].half, hb_half_period_us());
        TEST_ASSERT_EQUAL_UINT32(cases[i].gap, hb_gap_limit_us());
    }
    /* init restores the production timing */
    hb_init();
    TEST_ASSERT_EQUAL_UINT32(250, hb_half_period_us());
    TEST_ASSERT_EQUAL_UINT32(1000, hb_gap_limit_us());
}

static void test_bringup_frequency_grid_and_gap(void)
{
    /* 500 Hz: 1000 edges per second, gap limit 1500 */
    rng_state = 777u;
    TEST_ASSERT_TRUE(hb_set_frequency_hz(500));
    grid_reset(1000);
    hb_enable();
    while (htp.now < 3000000u) {
        grid_pass(rnd_range(5, 150));
    }
    TEST_ASSERT_EQUAL_UINT64(0, g.bad_grid);
    TEST_ASSERT_TRUE(g.window_edges >= 998u && g.window_edges <= 1002u);
    hb_disable();
    hb_enable();
    htp.on_edge = NULL;
    TEST_ASSERT_EQUAL(HB_OK, pass(10, true));
    TEST_ASSERT_EQUAL(HB_OK, pass(1500, true));
    TEST_ASSERT_EQUAL(HB_GAP_FAULT, pass(1501, true));

    /* 5 kHz with passes up to 50 us: 10000 edges per second */
    hb_init();
    TEST_ASSERT_TRUE(hb_set_frequency_hz(5000));
    grid_reset(100);
    hb_enable();
    uint64_t t_end = htp.now + 2000000u;
    while (htp.now < t_end) {
        grid_pass(rnd_range(5, 50));
    }
    TEST_ASSERT_EQUAL_UINT64(0, g.bad_grid);
    TEST_ASSERT_TRUE(g.window_edges >= 9998u && g.window_edges <= 10002u);
    hb_stats_t s;
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(0, s.resyncs);
    TEST_ASSERT_EQUAL_UINT32(1000, hb_gap_limit_us());
}

static void test_bringup_hold(void)
{
    hb_stats_t s;
    TEST_ASSERT_FALSE(hb_bringup_holding());
    htp.now += 1000u;
    TEST_ASSERT_TRUE(hb_bringup_hold(true)); /* HB.EDGE: one rising edge */
    TEST_ASSERT_TRUE(htp.level);
    TEST_ASSERT_TRUE(hb_bringup_holding());
    TEST_ASSERT_EQUAL_UINT64(1, htp.rises);
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT64(1, s.edges);
    TEST_ASSERT_EQUAL_UINT64(htp.now, s.last_edge_us);
    uint64_t t_rise = htp.now;

    /* already high: nothing new written */
    uint64_t w = htp.writes;
    htp.now += 10u;
    TEST_ASSERT_TRUE(hb_bringup_hold(true));
    TEST_ASSERT_EQUAL_UINT64(w, htp.writes);
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT64(1, s.edges);
    TEST_ASSERT_EQUAL_UINT64(t_rise, s.last_edge_us);

    /* DISARMED passes call hb_service(false): the held level stays */
    for (int i = 0; i < 1000; i++) {
        TEST_ASSERT_EQUAL(HB_OK, pass(100, false));
    }
    TEST_ASSERT_TRUE(htp.level);
    TEST_ASSERT_EQUAL_UINT64(w, htp.writes);
    TEST_ASSERT_TRUE(hb_bringup_holding());
    /* and permitted passes do not start the heartbeat either */
    TEST_ASSERT_EQUAL(HB_OK, pass(100, true));
    TEST_ASSERT_EQUAL_UINT64(w, htp.writes);

    /* hold(false): PC5 low, the falling edge is counted, the hold ends */
    TEST_ASSERT_TRUE(hb_bringup_hold(false));
    TEST_ASSERT_FALSE(htp.level);
    TEST_ASSERT_FALSE(hb_bringup_holding());
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT64(2, s.edges);
    TEST_ASSERT_EQUAL_UINT64(htp.now, s.last_edge_us);
    uint64_t t_fall = htp.now;
    /* hold(false) while low: still low, no edge */
    htp.now += 5u;
    TEST_ASSERT_TRUE(hb_bringup_hold(false));
    hb_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT64(2, s.edges);
    TEST_ASSERT_EQUAL_UINT64(t_fall, s.last_edge_us);
    /* with the hold over, hb_service(false) drives low again as usual */
    TEST_ASSERT_EQUAL(HB_OK, pass(10, false));
    TEST_ASSERT_FALSE(htp.level);

    /* hb_disable ends a hold with PC5 low */
    TEST_ASSERT_TRUE(hb_bringup_hold(true));
    hb_disable();
    TEST_ASSERT_FALSE(htp.level);
    TEST_ASSERT_FALSE(hb_bringup_holding());

    /* hold(false) without edge count when hold(false) is the first call */
    /* refused while enabled: nothing written */
    hb_enable();
    TEST_ASSERT_EQUAL(HB_OK, pass(10, true));
    TEST_ASSERT_TRUE(htp.level);
    w = htp.writes;
    TEST_ASSERT_FALSE(hb_bringup_hold(false));
    TEST_ASSERT_FALSE(hb_bringup_hold(true));
    TEST_ASSERT_EQUAL_UINT64(w, htp.writes);
    TEST_ASSERT_FALSE(hb_bringup_holding());
    TEST_ASSERT_TRUE(htp.level);
    TEST_ASSERT_EQUAL(HB_OK, pass(250, true)); /* the grid continues */
    TEST_ASSERT_FALSE(htp.level);

    /* hb_enable while holding high: the hold ends low, then a normal start */
    hb_disable();
    TEST_ASSERT_TRUE(hb_bringup_hold(true));
    TEST_ASSERT_TRUE(htp.level);
    hb_enable();
    TEST_ASSERT_FALSE(hb_bringup_holding());
    TEST_ASSERT_FALSE(htp.level);
    TEST_ASSERT_TRUE(hb_enabled());
    TEST_ASSERT_EQUAL(HB_OK, pass(20, true));
    TEST_ASSERT_TRUE(htp.level); /* first edge after enable is a rising one */
    TEST_ASSERT_TRUE(hb_started());

    /* init ends a hold */
    hb_disable();
    TEST_ASSERT_TRUE(hb_bringup_hold(true));
    hb_init();
    TEST_ASSERT_FALSE(hb_bringup_holding());
    TEST_ASSERT_FALSE(htp.level);

    /* exception context while holding: hb_service touches nothing */
    TEST_ASSERT_TRUE(hb_bringup_hold(true));
    htp.in_exception = true;
    w = htp.writes;
    TEST_ASSERT_EQUAL(HB_NOT_THREAD, pass(10, false));
    TEST_ASSERT_EQUAL_UINT64(w, htp.writes);
    htp.in_exception = false;
}
#else
static void test_no_bringup_symbols_outside_bringup(void)
{
    TEST_ASSERT_TRUE(hb_set_frequency_hz == NULL);
    TEST_ASSERT_TRUE(hb_bringup_hold == NULL);
    TEST_ASSERT_TRUE(hb_bringup_holding == NULL);
}
#endif

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_init_drives_low_and_clears);
    RUN_TEST(test_first_edge_on_next_service);
    RUN_TEST(test_first_service_after_enable_never_faults);
    RUN_TEST(test_reenable_does_not_reset_gap_reference);
    RUN_TEST(test_disable_leaves_pc5_low);
    RUN_TEST(test_exception_context_touches_nothing);
    RUN_TEST(test_grid_random_passes_one_million_edges);
    RUN_TEST(test_alternating_short_long_passes_keep_even_duty);
    RUN_TEST(test_tiny_passes_do_not_toggle_every_pass);
    RUN_TEST(test_late_pass_resyncs_without_burst);
    RUN_TEST(test_resync_boundary);
    RUN_TEST(test_gap_fault_boundary);
    RUN_TEST(test_gap_fault_on_slow_steady_loop);
    RUN_TEST(test_edges_only_when_permitted);
#if FW_BRINGUP
    RUN_TEST(test_bringup_frequency_range_and_gap);
    RUN_TEST(test_bringup_frequency_grid_and_gap);
    RUN_TEST(test_bringup_hold);
#else
    RUN_TEST(test_no_bringup_symbols_outside_bringup);
#endif
    return UNITY_END();
}
