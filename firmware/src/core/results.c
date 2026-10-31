/*
 * results.c — the measurement result ring (contract: results.h;
 * docs/firmware/firmware_spec.md §7.8).
 *
 * No datasheet facts are involved. Model (all sequence numbers):
 *  - next_seq: the number the next push receives (1 after init);
 *  - sent_hw:  the first record that has never been sent (high-water mark);
 *  - send_seq: the next record to stream; [send_seq, next_seq) is pending;
 *    [send_seq, sent_hw) are re-sends requested by a rewind;
 *  - the newest `retained` (<= RESULTS_CAPACITY) records are kept:
 *    [next_seq - retained, next_seq) is fetchable;
 *  - record `seq` lives in slot seq % RESULTS_CAPACITY.
 * Invariant: next_seq - retained <= send_seq <= sent_hw <= next_seq.
 * A push overwrites record next_seq - RESULTS_CAPACITY. It is refused only
 * when that record was never sent (next_seq - sent_hw == RESULTS_CAPACITY);
 * if the record was sent but is pending again after a rewind, it is
 * overwritten and the send cursor moves forward to the new lowest retained
 * record, so a DATA.FETCH can never end a sweep with an overflow.
 * Sequence numbers are plain uint32 counters; at the 900 points/s worst case
 * they last 55 days of continuous sweeping per boot, so wrap is not handled.
 *
 * The 44 KB array lives in .noinit RAM on the target (not zeroed by the C
 * startup; results_init() initialises it), and in ordinary static storage on
 * the host.
 */
#include "results.h"

#include <string.h>

#if defined(__arm__)
#define RESULTS_SECTION __attribute__((section(".noinit.results")))
#else
#define RESULTS_SECTION
#endif

static pt_rec_t s_ring[RESULTS_CAPACITY] RESULTS_SECTION;
static uint32_t s_next_seq;
static uint32_t s_send_seq;
static uint32_t s_sent_hw;
static uint32_t s_retained;

static pt_rec_t *slot(uint32_t seq)
{
    return &s_ring[seq % RESULTS_CAPACITY];
}

void results_init(void)
{
    memset(s_ring, 0, sizeof s_ring);
    s_next_seq = 1;
    s_send_seq = 1;
    s_sent_hw = 1;
    s_retained = 0;
}

size_t results_unsent(void)
{
    return (size_t)(s_next_seq - s_send_seq);
}

size_t results_never_sent(void)
{
    return (size_t)(s_next_seq - s_sent_hw);
}

bool results_push(pt_rec_t *rec)
{
    if (rec == NULL || results_never_sent() >= RESULTS_CAPACITY) {
        return false;
    }
    rec->seq = s_next_seq;
    *slot(s_next_seq) = *rec;
    s_next_seq++;
    if (s_retained < RESULTS_CAPACITY) {
        s_retained++;
    }
    /* A pending re-send may just have been overwritten: skip past it. */
    uint32_t lowest = s_next_seq - s_retained;
    if (s_send_seq < lowest) {
        s_send_seq = lowest;
    }
    return true;
}

bool results_peek_unsent(pt_rec_t *out)
{
    if (s_send_seq == s_next_seq) {
        return false;
    }
    if (out != NULL) {
        *out = *slot(s_send_seq);
    }
    return true;
}

void results_mark_sent(void)
{
    if (s_send_seq != s_next_seq) {
        s_send_seq++;
        if (s_send_seq > s_sent_hw) {
            s_sent_hw = s_send_seq;
        }
    }
}

uint32_t results_lowest_seq(void)
{
    return s_next_seq - s_retained;
}

uint32_t results_next_seq(void)
{
    return s_next_seq;
}

uint32_t results_rewind(uint32_t seq)
{
    uint32_t lo = results_lowest_seq();
    uint32_t target = seq;
    if (target < lo) {
        target = lo;
    }
    if (target > s_next_seq) {
        target = s_next_seq;
    }
    /* The cursor only moves back: records still pending before `target` are
     * never skipped (nothing is dropped silently). */
    if (target < s_send_seq) {
        s_send_seq = target;
    }
    return s_send_seq;
}

void results_skip_unsent(void)
{
    s_send_seq = s_next_seq;
    s_sent_hw = s_next_seq;
}
