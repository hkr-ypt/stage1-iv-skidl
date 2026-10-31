/*
 * sweep_gen.c — sweep validation and on-the-fly setpoint generation
 * (contract: sweep_gen.h; docs/firmware/firmware_spec.md §7.6,
 * docs/firmware/protocol.md §5.5).
 *
 * No datasheet facts are involved. Integer arithmetic only:
 *  - segment lengths are computed in int64 (two int32 waypoints can be up to
 *    2^32 - 1 apart), point counts in uint64 before the SWEEP_POINTS_MAX
 *    check, so no input can overflow;
 *  - point k of segment s is w[s-1] + sign * k * step, exactly, so the up
 *    and down branches of a loop produce identical setpoints;
 *  - segments are numbered 1..n_wp-1 (segment s runs from w[s-1] to w[s]);
 *    0 is the w0 point and every list point. The same 1-based number is
 *    reported in *bad_segment and in the PT record's seg field, and repeats
 *    in every cycle.
 * Validation order: EMPTY, STEP_SMALL, CYCLES, ZERO_SEGMENT, NOT_DIVISIBLE,
 * LIMIT, TOO_MANY (waypoints); EMPTY, TOO_MANY, LIMIT/SETTLE in point order
 * (list).
 */
#include "sweep_gen.h"

/* Absolute length of segment s (1-based) in uV. */
static uint32_t seg_len(const sweep_def_t *d, unsigned s)
{
    int64_t diff = (int64_t)d->wp_uv[s] - (int64_t)d->wp_uv[s - 1u];
    return (uint32_t)((diff < 0) ? -diff : diff);
}

static bool abs_exceeds(int32_t v, int32_t limit)
{
    int64_t mag = (v < 0) ? -(int64_t)v : (int64_t)v;
    return mag > (int64_t)limit;
}

/* Structure of a waypoint definition (everything except the limit), and its
 * point count. */
static swg_status_t wp_structure(const sweep_def_t *d, uint64_t *total,
                                 uint32_t *bad_segment)
{
    if (d->n_wp < 2u) {
        return SWG_EMPTY;
    }
    if (d->n_wp > SWEEP_WP_MAX) {
        return SWG_TOO_MANY;            /* malformed: more waypoints than storage */
    }
    if (d->step_uv <= 0 || d->step_uv < SWEEP_STEP_MIN_UV) {
        return SWG_STEP_SMALL;
    }
    if (d->cycles < 1u || d->cycles > SWEEP_CYCLES_MAX) {
        return SWG_CYCLES;
    }
    if (d->cycles > 1u && d->wp_uv[d->n_wp - 1u] != d->wp_uv[0]) {
        return SWG_CYCLES;
    }
    for (unsigned s = 1; s < d->n_wp; s++) {
        if (d->wp_uv[s] == d->wp_uv[s - 1u]) {
            *bad_segment = s;
            return SWG_ZERO_SEGMENT;
        }
    }
    uint32_t step = (uint32_t)d->step_uv;
    uint64_t per_cycle = 0;
    for (unsigned s = 1; s < d->n_wp; s++) {
        uint32_t len = seg_len(d, s);
        if (len % step != 0u) {
            *bad_segment = s;
            return SWG_NOT_DIVISIBLE;
        }
        per_cycle += len / step;
    }
    *total = 1u + per_cycle * (uint64_t)d->cycles;
    return SWG_OK;
}

swg_status_t sweep_validate(const sweep_def_t *d, int32_t v_limit_uv,
                            uint32_t *n_points, uint32_t *bad_segment)
{
    uint32_t bad = 0;
    uint64_t total = 0;
    swg_status_t st;

    if (d == NULL) {
        st = SWG_EMPTY;
    } else if (d->kind == SWEEP_WAYPOINTS) {
        st = wp_structure(d, &total, &bad);
        if (st == SWG_OK) {
            /* Every point lies between two waypoints. */
            for (unsigned s = 0; s < d->n_wp; s++) {
                if (abs_exceeds(d->wp_uv[s], v_limit_uv)) {
                    st = SWG_LIMIT;
                    break;
                }
            }
        }
        if (st == SWG_OK && total > SWEEP_POINTS_MAX) {
            st = SWG_TOO_MANY;
        }
    } else if (d->kind == SWEEP_LIST) {
        if (d->list == NULL || d->list_n == 0u) {
            st = SWG_EMPTY;
        } else if (d->list_n > SWEEP_LIST_MAX) {
            st = SWG_TOO_MANY;
        } else {
            st = SWG_OK;
            for (uint32_t i = 0; i < d->list_n; i++) {
                const sweep_point_t *p = &d->list[i];
                if (abs_exceeds(p->v_uv, v_limit_uv)) {
                    st = SWG_LIMIT;
                    break;
                }
                if (p->settle_us != 0u &&
                    (p->settle_us < SETTLE_MIN_US || p->settle_us > SETTLE_MAX_US)) {
                    st = SWG_SETTLE;
                    break;
                }
            }
            total = d->list_n;
        }
    } else {
        st = SWG_EMPTY;
    }

    if (n_points != NULL) {
        *n_points = (st == SWG_OK) ? (uint32_t)total : 0u;
    }
    if (bad_segment != NULL) {
        *bad_segment = bad;
    }
    return st;
}

static uint32_t gcd_u32(uint32_t a, uint32_t b)
{
    while (b != 0u) {
        uint32_t t = a % b;
        a = b;
        b = t;
    }
    return a;
}

int32_t sweep_suggest_step(const sweep_def_t *d)
{
    if (d == NULL || d->kind != SWEEP_WAYPOINTS || d->n_wp < 2u ||
        d->n_wp > SWEEP_WP_MAX || d->step_uv < SWEEP_STEP_MIN_UV) {
        return 0;
    }
    /* A step divides every segment iff it divides their gcd. */
    uint32_t g = 0;
    for (unsigned s = 1; s < d->n_wp; s++) {
        g = gcd_u32(g, seg_len(d, s));
    }
    if (g == 0u) {
        return 0;                       /* every segment has zero length */
    }
    /* Largest divisor of g that is <= the requested step: walk the divisor
     * pairs (q, g/q) for q <= sqrt(g) (at most 65536 iterations). */
    uint32_t want = (uint32_t)d->step_uv;
    uint32_t best = 0;
    for (uint32_t q = 1; q <= g / q; q++) {
        if (g % q != 0u) {
            continue;
        }
        uint32_t r = g / q;
        if (q <= want && q > best) {
            best = q;
        }
        if (r <= want && r > best) {
            best = r;
        }
    }
    return (best >= (uint32_t)SWEEP_STEP_MIN_UV) ? (int32_t)best : 0;
}

void sweep_iter_init(sweep_iter_t *it, const sweep_def_t *d)
{
    it->d = d;
    it->idx = 0;
    it->total = 0;
    it->cycle = 1;
    it->seg = 0;
    it->k = 0;
    if (d == NULL) {
        return;
    }
    if (d->kind == SWEEP_WAYPOINTS) {
        uint64_t total = 0;
        uint32_t bad = 0;
        if (wp_structure(d, &total, &bad) == SWG_OK && total <= SWEEP_POINTS_MAX) {
            it->total = (uint32_t)total;
        }
    } else if (d->kind == SWEEP_LIST) {
        if (d->list != NULL && d->list_n <= SWEEP_LIST_MAX) {
            it->total = d->list_n;
        }
    }
}

bool sweep_iter_next(sweep_iter_t *it, int32_t *v_uv, uint32_t *settle_us,
                     uint16_t *seg, uint32_t *idx)
{
    if (it == NULL || it->d == NULL || it->idx >= it->total) {
        return false;
    }
    const sweep_def_t *d = it->d;
    int32_t  v;
    uint32_t settle = 0;
    uint16_t sg;

    if (d->kind == SWEEP_LIST) {
        v = d->list[it->idx].v_uv;
        settle = d->list[it->idx].settle_us;
        sg = 0;
    } else if (it->seg == 0u) {
        v = d->wp_uv[0];
        sg = 0;
        it->seg = 1;
        it->k = 1;
    } else {
        unsigned s = it->seg;
        int64_t a = d->wp_uv[s - 1u];
        int64_t b = d->wp_uv[s];
        int64_t delta = (int64_t)it->k * (int64_t)d->step_uv;
        v = (int32_t)((b > a) ? a + delta : a - delta);   /* between a and b */
        sg = (uint16_t)s;
        int32_t k_end = (int32_t)(seg_len(d, s) / (uint32_t)d->step_uv);
        if (it->k >= k_end) {
            it->k = 1;
            if (s + 1u < d->n_wp) {
                it->seg = (uint8_t)(s + 1u);
            } else {
                it->seg = 1;            /* next cycle repeats w1..wn */
                it->cycle++;
            }
        } else {
            it->k++;
        }
    }

    if (v_uv != NULL) {
        *v_uv = v;
    }
    if (settle_us != NULL) {
        *settle_us = settle;
    }
    if (seg != NULL) {
        *seg = sg;
    }
    if (idx != NULL) {
        *idx = it->idx;
    }
    it->idx++;
    return true;
}

const char *swg_status_name(swg_status_t s)
{
    switch (s) {
    case SWG_OK:            return "ok";
    case SWG_EMPTY:         return "empty";
    case SWG_STEP_SMALL:    return "step_small";
    case SWG_NOT_DIVISIBLE: return "not_divisible";
    case SWG_ZERO_SEGMENT:  return "zero_segment";
    case SWG_LIMIT:         return "limit";
    case SWG_TOO_MANY:      return "too_many";
    case SWG_CYCLES:        return "cycles";
    case SWG_SETTLE:        return "settle";
    }
    return "unknown";
}
