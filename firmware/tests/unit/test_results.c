/*
 * test_results.c — unit tests of core/results.c (results.h; firmware_spec.md
 * §7.8): overflow exactly at RESULTS_CAPACITY never-sent records, streaming
 * with interleaved push/send, rewind (DATA.FETCH) clamping, the rule that a
 * re-send never makes a push fail (the cursor is clamped forward instead),
 * skip_unsent at a session start, monotonic sequence numbers, and a
 * randomised comparison against an independent reference model.
 */
#include <stdlib.h>
#include <string.h>

#include "results.h"
#include "unity.h"

#define CAP RESULTS_CAPACITY

void setUp(void)
{
    results_init();
}

void tearDown(void) {}

/* Record contents derived from the sequence number, so any stale or mixed-up
 * slot is detected. */
static pt_rec_t make_rec(uint32_t salt)
{
    pt_rec_t r;
    memset(&r, 0, sizeof r);
    r.seq = 0xDEADBEEFu;
    r.idx = salt;
    r.t_set_us = salt * 3u + 1u;
    r.t_i_us = salt * 5u + 2u;
    r.t_v_us = salt * 7u + 3u;
    r.i_raw = -(int32_t)(salt & 0x7FFFFFu);
    r.v_raw = (int32_t)(salt * 11u & 0x7FFFFFu);
    r.i_sd = (uint16_t)salt;
    r.v_sd = (uint16_t)(salt >> 3);
    r.sweep_id = (uint16_t)(salt >> 10);
    r.seg = (uint16_t)(salt % 16u);
    r.dac_code = (uint16_t)(salt * 13u);
    r.flags = (uint16_t)(salt & 0x7FFu);
    r.n = (uint16_t)(1u + salt % 256u);
    return r;
}

static void check_rec(uint32_t seq, uint32_t salt, const pt_rec_t *got)
{
    pt_rec_t want = make_rec(salt);
    want.seq = seq;
    TEST_ASSERT_EQUAL_UINT32(seq, got->seq);
    TEST_ASSERT_EQUAL_MEMORY(&want, got, sizeof want);
}

/* Push with salt == the seq it is expected to receive. */
static bool push_seq(void)
{
    uint32_t seq = results_next_seq();
    pt_rec_t r = make_rec(seq);
    bool ok = results_push(&r);
    if (ok) {
        TEST_ASSERT_EQUAL_UINT32(seq, r.seq);
        TEST_ASSERT_EQUAL_UINT32(seq + 1u, results_next_seq());
    } else {
        TEST_ASSERT_EQUAL_UINT32(0xDEADBEEFu, r.seq);     /* untouched */
        TEST_ASSERT_EQUAL_UINT32(seq, results_next_seq());
    }
    return ok;
}

/* Peek, verify, mark sent; returns the seq that was sent. */
static uint32_t send_one(void)
{
    pt_rec_t r;
    TEST_ASSERT_TRUE(results_peek_unsent(&r));
    check_rec(r.seq, r.seq, &r);
    results_mark_sent();
    return r.seq;
}

/* ---- basic -------------------------------------------------------------------------- */

static void test_init_state(void)
{
    pt_rec_t r;
    TEST_ASSERT_EQUAL_size_t(0, results_unsent());
    TEST_ASSERT_EQUAL_size_t(0, results_never_sent());
    TEST_ASSERT_EQUAL_UINT32(1, results_lowest_seq());
    TEST_ASSERT_EQUAL_UINT32(1, results_next_seq());
    TEST_ASSERT_FALSE(results_peek_unsent(&r));
    results_mark_sent();                                  /* no-op */
    TEST_ASSERT_EQUAL_UINT32(1, results_next_seq());
    TEST_ASSERT_EQUAL_UINT32(1, results_rewind(0));
    TEST_ASSERT_FALSE(results_push(NULL));
    TEST_ASSERT_EQUAL_UINT32(1, results_next_seq());
}

static void test_overflow_exactly_at_capacity(void)
{
    for (uint32_t i = 0; i < CAP; i++) {
        TEST_ASSERT_TRUE(push_seq());
    }
    TEST_ASSERT_EQUAL_size_t(CAP, results_unsent());
    TEST_ASSERT_EQUAL_size_t(CAP, results_never_sent());
    TEST_ASSERT_FALSE(push_seq());                        /* the 1025th */
    TEST_ASSERT_FALSE(push_seq());                        /* and it stays refused */
    TEST_ASSERT_EQUAL_UINT32(1, results_lowest_seq());

    /* one record sent frees exactly one slot */
    TEST_ASSERT_EQUAL_UINT32(1, send_one());
    TEST_ASSERT_TRUE(push_seq());
    TEST_ASSERT_FALSE(push_seq());
    TEST_ASSERT_EQUAL_UINT32(2, results_lowest_seq());
    TEST_ASSERT_EQUAL_UINT32(CAP + 2u, results_next_seq());

    /* the oldest pending record is still intact */
    TEST_ASSERT_EQUAL_UINT32(2, send_one());

    /* re-init clears everything, including a full ring */
    results_init();
    TEST_ASSERT_EQUAL_size_t(0, results_unsent());
    TEST_ASSERT_EQUAL_UINT32(1, results_next_seq());
    TEST_ASSERT_TRUE(push_seq());
}

static void test_streaming_seq_monotonic(void)
{
    /* The host lags by a varying amount below CAP: pushes never fail and the
     * stream is gap-free and in order. */
    uint32_t expect = 1;
    srand(7);
    for (uint32_t round = 0; round < 20000; round++) {
        unsigned burst = (unsigned)(rand() % 40);
        for (unsigned b = 0; b < burst && results_never_sent() < CAP - 1u; b++) {
            TEST_ASSERT_TRUE(push_seq());
        }
        unsigned drain = (unsigned)(rand() % 40);
        for (unsigned b = 0; b < drain && results_unsent() > 0; b++) {
            TEST_ASSERT_EQUAL_UINT32(expect, send_one());
            expect++;
        }
    }
    while (results_unsent() > 0) {
        TEST_ASSERT_EQUAL_UINT32(expect, send_one());
        expect++;
    }
    TEST_ASSERT_EQUAL_UINT32(results_next_seq(), expect);
    TEST_ASSERT_TRUE(expect > 10u * CAP);
}

/* ---- rewind / DATA.FETCH ----------------------------------------------------------- */

static void fill_and_send(uint32_t n)
{
    for (uint32_t i = 0; i < n; i++) {
        TEST_ASSERT_TRUE(push_seq());
        send_one();
    }
}

static void test_rewind_and_fetch_clamping(void)
{
    fill_and_send(3000);
    TEST_ASSERT_EQUAL_UINT32(3001, results_next_seq());
    TEST_ASSERT_EQUAL_UINT32(3001u - CAP, results_lowest_seq());
    TEST_ASSERT_EQUAL_size_t(0, results_unsent());

    /* above the retained range: nothing to re-send */
    TEST_ASSERT_EQUAL_UINT32(3001, results_rewind(5000));
    TEST_ASSERT_EQUAL_UINT32(3001, results_rewind(UINT32_MAX));
    TEST_ASSERT_EQUAL_size_t(0, results_unsent());

    /* inside: exactly from there */
    TEST_ASSERT_EQUAL_UINT32(2500, results_rewind(2500));
    TEST_ASSERT_EQUAL_size_t(501, results_unsent());
    TEST_ASSERT_EQUAL_size_t(0, results_never_sent());
    /* a later start does not skip what is already pending */
    TEST_ASSERT_EQUAL_UINT32(2500, results_rewind(2600));
    TEST_ASSERT_EQUAL_UINT32(2500, send_one());

    /* below: clamped to the lowest retained record */
    TEST_ASSERT_EQUAL_UINT32(3001u - CAP, results_rewind(1));
    TEST_ASSERT_EQUAL_UINT32(3001u - CAP, results_rewind(0));
    TEST_ASSERT_EQUAL_size_t(CAP, results_unsent());
    for (uint32_t s = 3001u - CAP; s < 3001u; s++) {
        TEST_ASSERT_EQUAL_UINT32(s, send_one());
    }
    TEST_ASSERT_FALSE(results_peek_unsent(NULL));
}

static void test_rewind_then_capacity_pushes_succeed(void)
{
    fill_and_send(2000);
    uint32_t lowest = results_lowest_seq();
    TEST_ASSERT_EQUAL_UINT32(lowest, results_rewind(0));
    TEST_ASSERT_EQUAL_size_t(CAP, results_unsent());      /* the whole ring is pending */
    TEST_ASSERT_EQUAL_size_t(0, results_never_sent());

    /* A full ring of pending re-sends does not block the sweep. */
    for (uint32_t i = 0; i < CAP; i++) {
        TEST_ASSERT_TRUE(push_seq());
        /* the cursor never points below the retained range */
        TEST_ASSERT_TRUE(results_rewind(UINT32_MAX) >= results_lowest_seq());
        TEST_ASSERT_EQUAL_size_t(CAP, results_unsent());
        TEST_ASSERT_EQUAL_size_t(i + 1u, results_never_sent());
    }
    /* Every re-send was overwritten; the cursor is at the first new record. */
    TEST_ASSERT_EQUAL_UINT32(2001, results_lowest_seq());
    pt_rec_t r;
    TEST_ASSERT_TRUE(results_peek_unsent(&r));
    check_rec(2001, 2001, &r);

    /* Now the ring is full of never-sent records: overflow as before. */
    TEST_ASSERT_FALSE(push_seq());
    for (uint32_t s = 2001; s < 2001u + CAP; s++) {
        TEST_ASSERT_EQUAL_UINT32(s, send_one());
    }
    TEST_ASSERT_TRUE(push_seq());
}

static void test_cursor_clamped_forward_mid_resend(void)
{
    fill_and_send(1500);                                  /* retained 477..1500 */
    TEST_ASSERT_EQUAL_UINT32(477, results_rewind(1));
    for (uint32_t s = 477; s < 487; s++) {                /* host re-reads 10 */
        TEST_ASSERT_EQUAL_UINT32(s, send_one());
    }
    for (uint32_t i = 0; i < 100; i++) {                  /* sweep continues */
        TEST_ASSERT_TRUE(push_seq());
    }
    /* 477..576 were overwritten; the cursor moved from 487 to 577 */
    TEST_ASSERT_EQUAL_UINT32(577, results_lowest_seq());
    TEST_ASSERT_EQUAL_UINT32(577, send_one());
    TEST_ASSERT_EQUAL_size_t(CAP - 1u, results_unsent());
    TEST_ASSERT_EQUAL_size_t(100, results_never_sent());
    /* the stream then continues in order through the new records */
    uint32_t s = 578;
    while (results_unsent() > 0) {
        TEST_ASSERT_EQUAL_UINT32(s, send_one());
        s++;
    }
    TEST_ASSERT_EQUAL_UINT32(1601, s);
    TEST_ASSERT_EQUAL_size_t(0, results_never_sent());

    /* a re-sent record does not lower the high-water mark */
    results_rewind(1590);
    TEST_ASSERT_EQUAL_size_t(11, results_unsent());
    TEST_ASSERT_EQUAL_size_t(0, results_never_sent());
}

static void test_overflow_when_host_never_reads(void)
{
    /* The host reads 300 records, then stops; after a rewind (which must not
     * help it) it still overflows after exactly CAP never-sent records. */
    for (uint32_t i = 0; i < 300; i++) {
        TEST_ASSERT_TRUE(push_seq());
    }
    for (uint32_t i = 0; i < 300; i++) {
        send_one();
    }
    results_rewind(1);
    uint32_t pushed = 0;
    while (push_seq()) {
        pushed++;
        TEST_ASSERT_TRUE(pushed <= CAP);
    }
    TEST_ASSERT_EQUAL_UINT32(CAP, pushed);
    TEST_ASSERT_EQUAL_size_t(CAP, results_never_sent());
    TEST_ASSERT_EQUAL_UINT32(301, results_lowest_seq());
    /* nothing was dropped: the first never-sent record is next in line */
    pt_rec_t r;
    TEST_ASSERT_TRUE(results_peek_unsent(&r));
    check_rec(301, 301, &r);
}

static void test_skip_unsent(void)
{
    for (uint32_t i = 0; i < 500; i++) {
        TEST_ASSERT_TRUE(push_seq());
    }
    send_one();
    results_skip_unsent();
    TEST_ASSERT_EQUAL_size_t(0, results_unsent());
    TEST_ASSERT_EQUAL_size_t(0, results_never_sent());
    TEST_ASSERT_EQUAL_UINT32(1, results_lowest_seq());
    TEST_ASSERT_FALSE(results_peek_unsent(NULL));
    /* a whole ring can be pushed again before overflow */
    for (uint32_t i = 0; i < CAP; i++) {
        TEST_ASSERT_TRUE(push_seq());
    }
    TEST_ASSERT_FALSE(push_seq());
    /* skipped records stay fetchable while retained (1524 pushed: 501..1524) */
    results_skip_unsent();
    TEST_ASSERT_EQUAL_UINT32(501, results_rewind(1));
    TEST_ASSERT_EQUAL_UINT32(501, send_one());
}

/* ---- randomised comparison with a reference model ---------------------------------- */

#define MODEL_MAX 60000u

static struct {
    uint32_t next, cursor;
    bool     sent_once[MODEL_MAX + 2];
    uint32_t salt[MODEL_MAX + 2];
} m;

static uint32_t model_lowest(void)
{
    return (m.next > CAP) ? m.next - CAP : 1u;
}

static size_t model_never_sent(void)
{
    size_t n = 0;
    for (uint32_t s = model_lowest(); s < m.next; s++) {
        n += !m.sent_once[s];
    }
    return n;
}

static void test_random_against_model(void)
{
    memset(&m, 0, sizeof m);
    m.next = 1;
    m.cursor = 1;
    srand(2024);
    unsigned refused = 0;
    for (int iter = 0; iter < 300000 && m.next < MODEL_MAX; iter++) {
        int op = rand() % 100;
        /* Every third phase of 3000 operations the host stops reading (only
         * pushes and rewinds), so the ring also runs into overflow. */
        bool stalled = (iter / 3000) % 3 == 2;
        if (stalled) {
            op = (op < 85) ? 0 : 90;
        }
        if (op < 45) {
            /* push: refused iff the record it would overwrite never went out */
            uint32_t victim = m.next - CAP;
            bool model_ok = !(m.next > CAP && !m.sent_once[victim]);
            uint32_t salt = (uint32_t)rand();
            pt_rec_t r = make_rec(salt);
            bool ok = results_push(&r);
            TEST_ASSERT_EQUAL(model_ok, ok);
            if (ok) {
                TEST_ASSERT_EQUAL_UINT32(m.next, r.seq);
                m.salt[m.next] = salt;
                m.next++;
                if (m.cursor < model_lowest()) {
                    m.cursor = model_lowest();
                }
            } else {
                refused++;
            }
        } else if (op < 85) {
            pt_rec_t r;
            bool have = results_peek_unsent(&r);
            TEST_ASSERT_EQUAL(m.cursor < m.next, have);
            if (have) {
                check_rec(m.cursor, m.salt[m.cursor], &r);
                if (rand() % 4 != 0) {
                    results_mark_sent();
                    m.sent_once[m.cursor] = true;
                    m.cursor++;
                }
            }
        } else if (op < 97) {
            uint32_t from;
            switch (rand() % 4) {
            case 0:  from = 0; break;
            case 1:  from = UINT32_MAX; break;
            default: from = m.next - (uint32_t)(rand() % (int)(CAP + 200u)); break;
            }
            uint32_t t = from;
            if (t < model_lowest()) {
                t = model_lowest();
            }
            if (t > m.next) {
                t = m.next;
            }
            if (t < m.cursor) {
                m.cursor = t;
            }
            TEST_ASSERT_EQUAL_UINT32(m.cursor, results_rewind(from));
        } else {
            results_skip_unsent();
            for (uint32_t s = m.cursor; s < m.next; s++) {
                m.sent_once[s] = true;
            }
            m.cursor = m.next;
        }
        TEST_ASSERT_EQUAL_UINT32(m.next, results_next_seq());
        TEST_ASSERT_EQUAL_UINT32(model_lowest(), results_lowest_seq());
        TEST_ASSERT_EQUAL_size_t(m.next - m.cursor, results_unsent());
        if (iter % 64 == 0) {
            TEST_ASSERT_EQUAL_size_t(model_never_sent(), results_never_sent());
        }
    }
    TEST_ASSERT_TRUE(m.next > 20000u);
    TEST_ASSERT_TRUE(refused > 0);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_init_state);
    RUN_TEST(test_overflow_exactly_at_capacity);
    RUN_TEST(test_streaming_seq_monotonic);
    RUN_TEST(test_rewind_and_fetch_clamping);
    RUN_TEST(test_rewind_then_capacity_pushes_succeed);
    RUN_TEST(test_cursor_clamped_forward_mid_resend);
    RUN_TEST(test_overflow_when_host_never_reads);
    RUN_TEST(test_skip_unsent);
    RUN_TEST(test_random_against_model);
    return UNITY_END();
}
