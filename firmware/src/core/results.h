/*
 * results.h — the measurement result ring.
 *
 * The sweep engine writes records; the protocol layer streams them in order.
 * The sweep NEVER waits for USB:
 *  - a record is "unsent" until the protocol has formatted it into the TX
 *    stream; sent records stay retained (for DATA.FETCH) until overwritten;
 *  - a high-water mark remembers the first record that has NEVER been sent.
 *    results_push() fails (returns false) only when the slot it would
 *    overwrite holds a never-sent record, i.e. the host has fallen
 *    RESULTS_CAPACITY records behind the live stream
 *    (results_never_sent() == RESULTS_CAPACITY). The engine then ends the
 *    sweep with status=overflow. Nothing is ever dropped silently;
 *  - a re-send requested with results_rewind() (DATA.FETCH) never makes a
 *    push fail: a record that was sent once may be overwritten while it is
 *    pending again, and the send cursor then moves forward past it (to the
 *    lowest retained record).
 * Sequence numbers are global and monotonic (uint32, starting at 1 per boot).
 */
#ifndef RESULTS_H
#define RESULTS_H

#include "fw_types.h"

typedef struct {
    uint32_t seq;
    uint32_t idx;          /* point index within the sweep                    */
    uint32_t t_set_us;     /* DAC load time, relative to the sweep start       */
    uint32_t t_i_us;       /* centroid of the I conversion midpoints (rel.)    */
    uint32_t t_v_us;       /* centroid of the V conversion midpoints (rel.)    */
    int32_t  i_raw;        /* mean raw ADC code, rounded                        */
    int32_t  v_raw;
    uint16_t i_sd;         /* sample std dev in 0.1 codes, saturating at 0xFFFE
                            * (6553.4 codes; PF_SD_SAT is then set); 0xFFFF = n/a */
    uint16_t v_sd;
    uint16_t sweep_id;
    uint16_t seg;
    uint16_t dac_code;
    uint16_t flags;        /* PF_* */
    uint16_t n;            /* conversions per channel                           */
    int8_t   i_frac;       /* mean - i_raw in 1/256 code, so that v_V and i_A   */
    int8_t   v_frac;       /* keep the resolution averaging gives (pt_mean_*)   */
} pt_rec_t;

/* The unrounded mean of a record's channel, in codes (raw + frac/256). */
static inline double pt_mean_i(const pt_rec_t *r) { return (double)r->i_raw + (double)r->i_frac / 256.0; }
static inline double pt_mean_v(const pt_rec_t *r) { return (double)r->v_raw + (double)r->v_frac / 256.0; }

void     results_init(void);
/* Append; assigns rec->seq. Returns false only if the slot to be overwritten
 * holds a record that has never been sent (results_never_sent() is at
 * RESULTS_CAPACITY). */
bool     results_push(pt_rec_t *rec);
/* Next unsent record without consuming it; false if none. */
bool     results_peek_unsent(pt_rec_t *out);
void     results_mark_sent(void);
/* Records pending in the stream, [send cursor, next), re-sends included. */
size_t   results_unsent(void);
/* Records never sent yet, [high-water mark, next): the overflow criterion. */
size_t   results_never_sent(void);
/* Retained range [lowest, next) — records with seq in this range can be fetched. */
uint32_t results_lowest_seq(void);
uint32_t results_next_seq(void);
/* Re-send from `seq` (clamped to the retained range): the send cursor moves
 * back (never forward past pending records); returns the first seq that will
 * be sent next (== results_next_seq() when nothing is pending). */
uint32_t results_rewind(uint32_t seq);
/* Skip everything unsent, and mark it as sent (used when a new host session
 * starts; the records stay fetchable). */
void     results_skip_unsent(void);

#endif /* RESULTS_H */
