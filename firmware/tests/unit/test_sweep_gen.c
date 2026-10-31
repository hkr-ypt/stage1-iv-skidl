/*
 * test_sweep_gen.c — unit tests of core/sweep_gen.c (docs/firmware/
 * firmware_spec.md §7.6, protocol.md §5.5): exact endpoints, identical
 * setpoints on the up and down branches of a hysteresis loop, cycles and
 * their segment numbering, divisibility errors with a brute-force-checked
 * step suggestion, zero-length segments, limits, the 1e6-point cap without
 * integer overflow at the int32 extremes, list sweeps and settle limits, and
 * randomised whole-sweep invariants (fixed seed).
 */
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include "sweep_gen.h"
#include "unity.h"

void setUp(void) {}
void tearDown(void) {}

#define V(volts_x1e6) ((int32_t)(volts_x1e6))
#define MAXPTS 200000u

typedef struct {
    int32_t  v;
    uint32_t settle;
    uint16_t seg;
    uint32_t idx;
} pt_t;

static pt_t s_pts[MAXPTS];

static sweep_def_t wp_def(int32_t step, uint16_t cycles, unsigned n, ...)
{
    sweep_def_t d;
    memset(&d, 0, sizeof d);
    d.kind = SWEEP_WAYPOINTS;
    d.step_uv = step;
    d.cycles = cycles;
    d.n_wp = (uint8_t)n;
    va_list ap;
    va_start(ap, n);
    for (unsigned i = 0; i < n; i++) {
        d.wp_uv[i] = va_arg(ap, int32_t);
    }
    va_end(ap);
    return d;
}

/* Iterate the whole definition into s_pts; returns the number of points. */
static uint32_t collect(const sweep_def_t *d)
{
    sweep_iter_t it;
    sweep_iter_init(&it, d);
    uint32_t n = 0;
    pt_t p;
    while (sweep_iter_next(&it, &p.v, &p.settle, &p.seg, &p.idx)) {
        TEST_ASSERT_TRUE(n < MAXPTS);
        TEST_ASSERT_EQUAL_UINT32(n, p.idx);
        s_pts[n++] = p;
    }
    /* exhausted stays exhausted */
    TEST_ASSERT_FALSE(sweep_iter_next(&it, &p.v, &p.settle, &p.seg, &p.idx));
    return n;
}

static swg_status_t validate(const sweep_def_t *d, int32_t limit, uint32_t *n, uint32_t *bad)
{
    return sweep_validate(d, limit, n, bad);
}

/* ---- basic waypoint sweeps ------------------------------------------------------ */

static void test_linear_exact_endpoints(void)
{
    sweep_def_t d = wp_def(500000, 1, 2, V(0), V(5000000));
    uint32_t n = 0, bad = 99;
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 10000000, &n, &bad));
    TEST_ASSERT_EQUAL_UINT32(11, n);
    TEST_ASSERT_EQUAL_UINT32(0, bad);
    TEST_ASSERT_EQUAL_UINT32(11, collect(&d));
    for (uint32_t i = 0; i < 11; i++) {
        TEST_ASSERT_EQUAL_INT32((int32_t)i * 500000, s_pts[i].v);
        TEST_ASSERT_EQUAL_UINT16(i == 0 ? 0 : 1, s_pts[i].seg);
        TEST_ASSERT_EQUAL_UINT32(0, s_pts[i].settle);
    }

    /* descending through zero, odd step */
    d = wp_def(1700, 1, 2, V(5100), V(-5100));
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(7, n);
    TEST_ASSERT_EQUAL_UINT32(7, collect(&d));
    static const int32_t want[] = { 5100, 3400, 1700, 0, -1700, -3400, -5100 };
    for (uint32_t i = 0; i < 7; i++) {
        TEST_ASSERT_EQUAL_INT32(want[i], s_pts[i].v);
    }

    /* a single step */
    d = wp_def(500, 1, 2, V(-250), V(250));
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(2, collect(&d));
    TEST_ASSERT_EQUAL_INT32(-250, s_pts[0].v);
    TEST_ASSERT_EQUAL_INT32(250, s_pts[1].v);
}

static void test_loop_branches_identical(void)
{
    static const int32_t steps[] = { 500, 1000, 2500, 4000, 10000, 312500, 1000000, 5000000, 10000000 };
    for (size_t si = 0; si < sizeof steps / sizeof steps[0]; si++) {
        int32_t step = steps[si];
        sweep_def_t d = wp_def(step, 1, 4, V(0), V(10000000), V(-10000000), V(0));
        uint32_t n = 0;
        TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 10000000, &n, NULL));
        uint32_t per10 = (uint32_t)(10000000 / step);
        TEST_ASSERT_EQUAL_UINT32(1u + 4u * per10, n);
        TEST_ASSERT_EQUAL_UINT32(n, collect(&d));

        /* grid membership and the shape of each branch */
        unsigned at_top = 0, at_bottom = 0, at_zero = 0;
        for (uint32_t i = 0; i < n; i++) {
            TEST_ASSERT_EQUAL_INT32(0, s_pts[i].v % step);
            if (i > 0) {
                int32_t dv = s_pts[i].v - s_pts[i - 1].v;
                TEST_ASSERT_EQUAL_INT32(s_pts[i].seg == 2 ? -step : step, dv);
            }
            at_top += s_pts[i].v == 10000000;
            at_bottom += s_pts[i].v == -10000000;
            at_zero += s_pts[i].v == 0;
        }
        TEST_ASSERT_EQUAL_UINT(1, at_top);       /* turning points measured once */
        TEST_ASSERT_EQUAL_UINT(1, at_bottom);
        TEST_ASSERT_EQUAL_UINT(3, at_zero);      /* start, crossing, end */
        TEST_ASSERT_EQUAL_INT32(0, s_pts[n - 1].v);

        /* Every down-branch setpoint strictly inside (-10, 10) V is also an
         * up-branch setpoint, and vice versa: identical values, so identical
         * DAC codes. Index the grid by (v + 10 V) / step. */
        uint32_t cells = 2u * per10 + 1u;
        uint8_t *up = calloc(cells, 1), *down = calloc(cells, 1);
        TEST_ASSERT_NOT_NULL(up);
        TEST_ASSERT_NOT_NULL(down);
        for (uint32_t i = 1; i < n; i++) {
            uint32_t cell = (uint32_t)((s_pts[i].v + 10000000) / step);
            if (s_pts[i].seg == 2) {
                down[cell]++;
            } else {
                up[cell]++;
            }
        }
        for (uint32_t cell = 1; cell + 1 < cells; cell++) {
            TEST_ASSERT_EQUAL_UINT8(1, up[cell]);
            TEST_ASSERT_EQUAL_UINT8(1, down[cell]);
        }
        free(up);
        free(down);
    }
}

static void test_cycles_and_segment_numbering(void)
{
    sweep_def_t d = wp_def(250000, 3, 3, V(0), V(1000000), V(0));
    uint32_t n = 0;
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(25, n);
    TEST_ASSERT_EQUAL_UINT32(25, collect(&d));
    static const int32_t cyc[8] = { 250000, 500000, 750000, 1000000, 750000, 500000, 250000, 0 };
    TEST_ASSERT_EQUAL_INT32(0, s_pts[0].v);
    TEST_ASSERT_EQUAL_UINT16(0, s_pts[0].seg);
    for (uint32_t i = 1; i < 25; i++) {
        uint32_t j = (i - 1) % 8;
        TEST_ASSERT_EQUAL_INT32(cyc[j], s_pts[i].v);
        TEST_ASSERT_EQUAL_UINT16(j < 4 ? 1 : 2, s_pts[i].seg);
    }

    /* bipolar loop, two cycles: segments 1,2,3,1,2,3 */
    d = wp_def(500000, 2, 4, V(0), V(1000000), V(-1000000), V(0));
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(1 + 2 * 8, n);
    TEST_ASSERT_EQUAL_UINT32(n, collect(&d));
    static const uint16_t segs[17] = { 0, 1, 1, 2, 2, 2, 2, 3, 3, 1, 1, 2, 2, 2, 2, 3, 3 };
    static const int32_t vals[17] = { 0, 500000, 1000000, 500000, 0, -500000, -1000000, -500000, 0,
                                      500000, 1000000, 500000, 0, -500000, -1000000, -500000, 0 };
    for (uint32_t i = 0; i < 17; i++) {
        TEST_ASSERT_EQUAL_UINT16(segs[i], s_pts[i].seg);
        TEST_ASSERT_EQUAL_INT32(vals[i], s_pts[i].v);
    }

    /* cycle rules */
    d = wp_def(500000, 2, 2, V(0), V(1000000));
    TEST_ASSERT_EQUAL_INT(SWG_CYCLES, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(0, n);
    d.cycles = 1;
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 10000000, &n, NULL));
    d = wp_def(500000, 0, 3, V(0), V(1000000), V(0));
    TEST_ASSERT_EQUAL_INT(SWG_CYCLES, validate(&d, 10000000, &n, NULL));
    d.cycles = SWEEP_CYCLES_MAX + 1;
    TEST_ASSERT_EQUAL_INT(SWG_CYCLES, validate(&d, 10000000, &n, NULL));
    d.cycles = SWEEP_CYCLES_MAX;
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(1 + 4 * SWEEP_CYCLES_MAX, n);
}

/* ---- validation errors -------------------------------------------------------------- */

/* Reference: the largest s in [SWEEP_STEP_MIN_UV, step] dividing every segment. */
static int32_t naive_suggest(const sweep_def_t *d)
{
    for (int32_t s = d->step_uv; s >= SWEEP_STEP_MIN_UV; s--) {
        bool ok = true;
        for (unsigned i = 1; i < d->n_wp && ok; i++) {
            int64_t len = llabs((int64_t)d->wp_uv[i] - d->wp_uv[i - 1]);
            ok = (len % s) == 0;
        }
        if (ok) {
            return s;
        }
    }
    return 0;
}

static void test_not_divisible_with_suggestion(void)
{
    uint32_t n = 7, bad = 0;
    sweep_def_t d = wp_def(1000, 1, 2, V(0), V(1000700));
    TEST_ASSERT_EQUAL_INT(SWG_NOT_DIVISIBLE, validate(&d, 10000000, &n, &bad));
    TEST_ASSERT_EQUAL_UINT32(1, bad);
    TEST_ASSERT_EQUAL_UINT32(0, n);
    /* 1000700 = 2^2 5^2 10007: no divisor in [500, 1000] */
    TEST_ASSERT_EQUAL_INT32(0, sweep_suggest_step(&d));

    d = wp_def(3000, 1, 3, V(0), V(1000000), V(-750000));
    TEST_ASSERT_EQUAL_INT(SWG_NOT_DIVISIBLE, validate(&d, 10000000, &n, &bad));
    TEST_ASSERT_EQUAL_UINT32(1, bad);
    TEST_ASSERT_EQUAL_INT32(2500, sweep_suggest_step(&d));   /* gcd 250000 */
    d.step_uv = 2500;
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 10000000, &n, &bad));
    TEST_ASSERT_EQUAL_UINT32(1 + 400 + 700, n);

    /* second segment is the offender */
    d = wp_def(1000, 1, 3, V(0), V(1000000), V(2003500));
    TEST_ASSERT_EQUAL_INT(SWG_NOT_DIVISIBLE, validate(&d, 10000000, &n, &bad));
    TEST_ASSERT_EQUAL_UINT32(2, bad);
    TEST_ASSERT_EQUAL_INT32(500, sweep_suggest_step(&d));

    /* an already valid step suggests itself, on either side of sqrt(gcd) */
    d = wp_def(2000, 1, 2, V(0), V(1000000));
    TEST_ASSERT_EQUAL_INT32(2000, sweep_suggest_step(&d));
    d = wp_def(1000, 1, 2, V(0), V(10000000));
    TEST_ASSERT_EQUAL_INT32(1000, sweep_suggest_step(&d));
    d.step_uv = 999;      /* 10^7 = 2^7 5^7: largest divisor <= 999 is 800 */
    TEST_ASSERT_EQUAL_INT32(800, sweep_suggest_step(&d));
    /* below the minimum there is nothing to suggest */
    d.step_uv = 499;
    TEST_ASSERT_EQUAL_INT32(0, sweep_suggest_step(&d));
    d.kind = SWEEP_LIST;
    TEST_ASSERT_EQUAL_INT32(0, sweep_suggest_step(&d));
    TEST_ASSERT_EQUAL_INT32(0, sweep_suggest_step(NULL));

    /* Cross-check against brute force on random definitions. */
    srand(12345);
    for (int iter = 0; iter < 3000; iter++) {
        sweep_def_t r;
        memset(&r, 0, sizeof r);
        r.kind = SWEEP_WAYPOINTS;
        r.cycles = 1;
        r.n_wp = (uint8_t)(2 + rand() % 4);
        int32_t unit = 1 + rand() % 3000;
        r.wp_uv[0] = (rand() % 2001 - 1000) * 7;
        for (unsigned i = 1; i < r.n_wp; i++) {
            int32_t k = 1 + rand() % 400;
            r.wp_uv[i] = r.wp_uv[i - 1] + ((rand() & 1) ? k : -k) * unit;
        }
        r.step_uv = SWEEP_STEP_MIN_UV + rand() % 9500;
        TEST_ASSERT_EQUAL_INT32(naive_suggest(&r), sweep_suggest_step(&r));
        int32_t sug = sweep_suggest_step(&r);
        if (sug != 0) {
            r.step_uv = sug;
            TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&r, INT32_MAX, NULL, NULL));
        }
    }
}

static void test_zero_length_segment(void)
{
    uint32_t n = 0, bad = 0;
    sweep_def_t d = wp_def(500, 1, 3, V(0), V(1000), V(1000));
    TEST_ASSERT_EQUAL_INT(SWG_ZERO_SEGMENT, validate(&d, 10000000, &n, &bad));
    TEST_ASSERT_EQUAL_UINT32(2, bad);
    d = wp_def(500, 1, 2, V(0), V(0));
    TEST_ASSERT_EQUAL_INT(SWG_ZERO_SEGMENT, validate(&d, 10000000, &n, &bad));
    TEST_ASSERT_EQUAL_UINT32(1, bad);
    /* zero length wins over a non-divisible segment elsewhere */
    d = wp_def(500, 1, 3, V(0), V(777), V(777));
    TEST_ASSERT_EQUAL_INT(SWG_ZERO_SEGMENT, validate(&d, 10000000, &n, &bad));
    /* an iterator over it yields nothing */
    TEST_ASSERT_EQUAL_UINT32(0, collect(&d));
}

static void test_step_and_empty(void)
{
    uint32_t n = 0;
    sweep_def_t d = wp_def(499, 1, 2, V(0), V(499000));
    TEST_ASSERT_EQUAL_INT(SWG_STEP_SMALL, validate(&d, 10000000, &n, NULL));
    d.step_uv = 0;
    TEST_ASSERT_EQUAL_INT(SWG_STEP_SMALL, validate(&d, 10000000, &n, NULL));
    d.step_uv = -500;
    TEST_ASSERT_EQUAL_INT(SWG_STEP_SMALL, validate(&d, 10000000, &n, NULL));
    d.step_uv = INT32_MIN;
    TEST_ASSERT_EQUAL_INT(SWG_STEP_SMALL, validate(&d, 10000000, &n, NULL));
    d.step_uv = 500;
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(999, n);

    d.n_wp = 1;
    TEST_ASSERT_EQUAL_INT(SWG_EMPTY, validate(&d, 10000000, &n, NULL));
    d.n_wp = 0;
    TEST_ASSERT_EQUAL_INT(SWG_EMPTY, validate(&d, 10000000, &n, NULL));
    d.n_wp = SWEEP_WP_MAX + 1;   /* malformed: more waypoints than storage */
    TEST_ASSERT_EQUAL_INT(SWG_TOO_MANY, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(0, collect(&d));
    d.kind = SWEEP_NONE;
    TEST_ASSERT_EQUAL_INT(SWG_EMPTY, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(0, collect(&d));
    TEST_ASSERT_EQUAL_INT(SWG_EMPTY, validate(NULL, 10000000, &n, NULL));
    d.kind = (sweep_kind_t)42;
    TEST_ASSERT_EQUAL_INT(SWG_EMPTY, validate(&d, 10000000, &n, NULL));

    /* 16 waypoints is allowed */
    memset(&d, 0, sizeof d);
    d.kind = SWEEP_WAYPOINTS;
    d.step_uv = 1000;
    d.cycles = 1;
    d.n_wp = SWEEP_WP_MAX;
    for (unsigned i = 0; i < SWEEP_WP_MAX; i++) {
        d.wp_uv[i] = (i % 2) ? 5000 : -5000;
    }
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(1 + 15 * 10, n);
    TEST_ASSERT_EQUAL_UINT32(n, collect(&d));
    TEST_ASSERT_EQUAL_UINT16(15, s_pts[n - 1].seg);
    TEST_ASSERT_EQUAL_INT32(5000, s_pts[n - 1].v);
}

static void test_limits(void)
{
    uint32_t n = 0;
    sweep_def_t d = wp_def(500, 1, 3, V(0), V(10000000), V(-10000000));
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_INT(SWG_LIMIT, validate(&d, 9999999, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(0, n);
    d = wp_def(1000, 1, 2, V(0), V(10001000));
    TEST_ASSERT_EQUAL_INT(SWG_LIMIT, validate(&d, 10000000, &n, NULL));
    d = wp_def(1000, 1, 2, V(-10001000), V(0));
    TEST_ASSERT_EQUAL_INT(SWG_LIMIT, validate(&d, 10000000, &n, NULL));
    d = wp_def(1000, 1, 2, V(0), V(5001000));
    TEST_ASSERT_EQUAL_INT(SWG_LIMIT, validate(&d, 5000000, &n, NULL));
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 5001000, &n, NULL));
    TEST_ASSERT_EQUAL_INT(SWG_LIMIT, validate(&d, -1, &n, NULL));
    /* INT32_MIN magnitude does not overflow the limit comparison */
    d = wp_def(65535, 1, 2, INT32_MIN, INT32_MAX);
    TEST_ASSERT_EQUAL_INT(SWG_LIMIT, validate(&d, INT32_MAX, &n, NULL));
}

static void test_point_cap_and_extremes(void)
{
    uint32_t n = 0;
    /* exactly SWEEP_POINTS_MAX points (pure validator, no v_limit in the way) */
    sweep_def_t d = wp_def(500, 1, 2, V(0), V(499999500));
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, INT32_MAX, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(SWEEP_POINTS_MAX, n);
    sweep_iter_t it;
    sweep_iter_init(&it, &d);
    uint32_t count = 0, idx = 0;
    int32_t v = 0, prev = -500;
    uint16_t seg = 0;
    while (sweep_iter_next(&it, &v, NULL, &seg, &idx)) {
        if (v - prev != 500) {
            TEST_FAIL_MESSAGE("not a 500 uV staircase");
        }
        prev = v;
        count++;
    }
    TEST_ASSERT_EQUAL_UINT32(SWEEP_POINTS_MAX, count);
    TEST_ASSERT_EQUAL_INT32(499999500, v);
    TEST_ASSERT_EQUAL_UINT32(SWEEP_POINTS_MAX - 1u, idx);

    d.wp_uv[1] = 500000000;
    TEST_ASSERT_EQUAL_INT(SWG_TOO_MANY, validate(&d, INT32_MAX, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(0, collect(&d));

    /* realistic: a full +-10 V loop at 0.5 mV is 80000 points per cycle */
    d = wp_def(500, 12, 4, V(0), V(10000000), V(-10000000), V(0));
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(960001, n);
    d.cycles = 13;
    TEST_ASSERT_EQUAL_INT(SWG_TOO_MANY, validate(&d, 10000000, &n, NULL));

    /* int32 extremes: 2^32 - 1 = 65535 * 65537, no overflow anywhere.
     * |INT32_MIN| exceeds every int32 limit ... */
    d = wp_def(65535, 1, 2, INT32_MIN, INT32_MAX);
    TEST_ASSERT_EQUAL_INT(SWG_LIMIT, validate(&d, INT32_MAX, &n, NULL));
    /* ... but the structure is valid, and the iterator spans all of int32. */
    TEST_ASSERT_EQUAL_UINT32(65538, collect(&d));
    TEST_ASSERT_EQUAL_INT32(INT32_MIN, s_pts[0].v);
    TEST_ASSERT_EQUAL_INT32(INT32_MIN + 65535, s_pts[1].v);
    TEST_ASSERT_EQUAL_INT32(INT32_MAX, s_pts[65537].v);
    d = wp_def(65535, 1, 2, INT32_MAX, INT32_MIN);
    TEST_ASSERT_EQUAL_UINT32(65538, collect(&d));
    TEST_ASSERT_EQUAL_INT32(INT32_MAX - 65535, s_pts[1].v);
    TEST_ASSERT_EQUAL_INT32(INT32_MIN, s_pts[65537].v);
    /* 2^32 - 65536 long and within the limit: validates */
    d = wp_def(65535, 1, 2, -2147418113, INT32_MAX);
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, INT32_MAX, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(65537, n);
    TEST_ASSERT_EQUAL_UINT32(65537, collect(&d));
    TEST_ASSERT_EQUAL_INT32(-2147418113, s_pts[0].v);
    TEST_ASSERT_EQUAL_INT32(INT32_MAX, s_pts[65536].v);

    /* 15 extreme segments x 1000 cycles: the count must not wrap */
    memset(&d, 0, sizeof d);
    d.kind = SWEEP_WAYPOINTS;
    d.step_uv = 65535;
    d.cycles = 1000;
    d.n_wp = 15;
    for (unsigned i = 0; i < 15; i++) {
        d.wp_uv[i] = (i % 2) ? INT32_MAX : INT32_MIN;
    }
    TEST_ASSERT_EQUAL_INT(SWG_LIMIT, validate(&d, INT32_MAX, &n, NULL));
    for (unsigned i = 0; i < 15; i++) {
        d.wp_uv[i] = (i % 2) ? INT32_MAX : -2147418113;
    }
    TEST_ASSERT_EQUAL_INT(SWG_TOO_MANY, validate(&d, INT32_MAX, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(0, collect(&d));
}

/* ---- list sweeps ---------------------------------------------------------------- */

static void test_list_mode(void)
{
    static sweep_point_t list[SWEEP_LIST_MAX + 1];
    static const sweep_point_t five[5] = {
        { 0, 0 }, { 1000000, SETTLE_MIN_US }, { -2500, SETTLE_MAX_US }, { 10000000, 0 }, { -10000000, 250 },
    };
    memcpy(list, five, sizeof five);
    sweep_def_t d;
    memset(&d, 0, sizeof d);
    d.kind = SWEEP_LIST;
    d.list = list;
    d.list_n = 5;
    d.step_uv = 1;         /* ignored for lists */
    d.cycles = 0;          /* ignored for lists */
    uint32_t n = 0, bad = 9;
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 10000000, &n, &bad));
    TEST_ASSERT_EQUAL_UINT32(5, n);
    TEST_ASSERT_EQUAL_UINT32(5, collect(&d));
    for (uint32_t i = 0; i < 5; i++) {
        TEST_ASSERT_EQUAL_INT32(five[i].v_uv, s_pts[i].v);
        TEST_ASSERT_EQUAL_UINT32(five[i].settle_us, s_pts[i].settle);
        TEST_ASSERT_EQUAL_UINT16(0, s_pts[i].seg);
    }

    TEST_ASSERT_EQUAL_INT(SWG_LIMIT, validate(&d, 9999999, &n, NULL));
    list[2].settle_us = SETTLE_MIN_US - 1;
    TEST_ASSERT_EQUAL_INT(SWG_SETTLE, validate(&d, 10000000, &n, NULL));
    list[2].settle_us = SETTLE_MAX_US + 1;
    TEST_ASSERT_EQUAL_INT(SWG_SETTLE, validate(&d, 10000000, &n, NULL));
    list[2].settle_us = 1;
    TEST_ASSERT_EQUAL_INT(SWG_SETTLE, validate(&d, 10000000, &n, NULL));
    list[2].settle_us = UINT32_MAX;
    TEST_ASSERT_EQUAL_INT(SWG_SETTLE, validate(&d, 10000000, &n, NULL));
    list[2].settle_us = 0;
    list[1].v_uv = INT32_MIN;
    TEST_ASSERT_EQUAL_INT(SWG_LIMIT, validate(&d, 10000000, &n, NULL));
    list[1].v_uv = 0;

    d.list_n = 0;
    TEST_ASSERT_EQUAL_INT(SWG_EMPTY, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(0, collect(&d));
    d.list_n = 5;
    d.list = NULL;
    TEST_ASSERT_EQUAL_INT(SWG_EMPTY, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(0, collect(&d));

    d.list = list;
    for (uint32_t i = 0; i < SWEEP_LIST_MAX + 1u; i++) {
        list[i].v_uv = (int32_t)i * 1000 - 1000000;
        list[i].settle_us = 0;
    }
    d.list_n = SWEEP_LIST_MAX;
    TEST_ASSERT_EQUAL_INT(SWG_OK, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(SWEEP_LIST_MAX, n);
    TEST_ASSERT_EQUAL_UINT32(SWEEP_LIST_MAX, collect(&d));
    TEST_ASSERT_EQUAL_INT32(list[SWEEP_LIST_MAX - 1].v_uv, s_pts[SWEEP_LIST_MAX - 1].v);
    d.list_n = SWEEP_LIST_MAX + 1;
    TEST_ASSERT_EQUAL_INT(SWG_TOO_MANY, validate(&d, 10000000, &n, NULL));
    TEST_ASSERT_EQUAL_UINT32(0, collect(&d));
}

/* ---- randomised invariants --------------------------------------------------------- */

static void test_random_waypoint_invariants(void)
{
    srand(4242);
    for (int iter = 0; iter < 400; iter++) {
        sweep_def_t d;
        memset(&d, 0, sizeof d);
        d.kind = SWEEP_WAYPOINTS;
        d.step_uv = SWEEP_STEP_MIN_UV + rand() % 50000;
        d.n_wp = (uint8_t)(2 + rand() % (SWEEP_WP_MAX - 1));
        int32_t max_k = 10000000 / d.step_uv;
        for (unsigned i = 0; i < d.n_wp; i++) {
            int32_t k;
            do {
                k = rand() % (2 * max_k + 1) - max_k;
            } while (i > 0 && k * d.step_uv == d.wp_uv[i - 1]);
            d.wp_uv[i] = k * d.step_uv;
        }
        d.cycles = 1;
        if (rand() % 3 == 0 && d.n_wp >= 3) {
            d.wp_uv[d.n_wp - 1] = d.wp_uv[0];
            if (d.wp_uv[d.n_wp - 2] == d.wp_uv[0]) {
                continue;
            }
            d.cycles = (uint16_t)(1 + rand() % 3);
        }
        uint32_t n = 0;
        swg_status_t st = validate(&d, 10000000, &n, NULL);
        if (st == SWG_TOO_MANY) {
            continue;
        }
        TEST_ASSERT_EQUAL_INT(SWG_OK, st);
        if (n > MAXPTS) {
            continue;
        }
        TEST_ASSERT_EQUAL_UINT32(n, collect(&d));
        TEST_ASSERT_EQUAL_INT32(d.wp_uv[0], s_pts[0].v);
        TEST_ASSERT_EQUAL_INT32(d.wp_uv[d.n_wp - 1], s_pts[n - 1].v);
        unsigned waypoint_hits = 0;
        for (uint32_t i = 1; i < n; i++) {
            int32_t dv = s_pts[i].v - s_pts[i - 1].v;
            TEST_ASSERT_TRUE(dv == d.step_uv || dv == -d.step_uv);
            TEST_ASSERT_TRUE(s_pts[i].seg >= 1 && s_pts[i].seg < d.n_wp);
            TEST_ASSERT_TRUE(abs(s_pts[i].v) <= 10000000);
            /* moving towards the segment's end point */
            int32_t end = d.wp_uv[s_pts[i].seg];
            int32_t start = d.wp_uv[s_pts[i].seg - 1];
            TEST_ASSERT_TRUE((end > start) ? dv > 0 : dv < 0);
            /* a segment change happens exactly after its end point */
            bool last_of_seg = (i + 1 == n) || s_pts[i + 1].seg != s_pts[i].seg;
            TEST_ASSERT_EQUAL(last_of_seg, s_pts[i].v == end);
            waypoint_hits += last_of_seg;
        }
        TEST_ASSERT_EQUAL_UINT((unsigned)(d.n_wp - 1) * d.cycles, waypoint_hits);
    }
}

static void test_status_names(void)
{
    for (int s = SWG_OK; s <= SWG_SETTLE; s++) {
        const char *name = swg_status_name((swg_status_t)s);
        TEST_ASSERT_NOT_NULL(name);
        TEST_ASSERT_TRUE(strlen(name) > 0);
        TEST_ASSERT_TRUE(strcmp(name, "unknown") != 0);
        for (int t = SWG_OK; t < s; t++) {
            TEST_ASSERT_TRUE(strcmp(name, swg_status_name((swg_status_t)t)) != 0);
        }
    }
    TEST_ASSERT_EQUAL_STRING("unknown", swg_status_name((swg_status_t)99));
    TEST_ASSERT_EQUAL_STRING("not_divisible", swg_status_name(SWG_NOT_DIVISIBLE));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_linear_exact_endpoints);
    RUN_TEST(test_loop_branches_identical);
    RUN_TEST(test_cycles_and_segment_numbering);
    RUN_TEST(test_not_divisible_with_suggestion);
    RUN_TEST(test_zero_length_segment);
    RUN_TEST(test_step_and_empty);
    RUN_TEST(test_limits);
    RUN_TEST(test_point_cap_and_extremes);
    RUN_TEST(test_list_mode);
    RUN_TEST(test_random_waypoint_invariants);
    RUN_TEST(test_status_names);
    return UNITY_END();
}
