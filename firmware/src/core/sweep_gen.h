/*
 * sweep_gen.h — sweep definitions and on-the-fly setpoint generation (pure).
 *
 * Waypoint sweep: waypoints w0..wn (integer uV), a step (uV), cycles >= 1.
 *  - EXACT GRID: every segment length |w[i+1]-w[i]| must be a non-zero
 *    integer multiple of the step, and step >= SWEEP_STEP_MIN_UV. No rounding
 *    ever happens, so the up and down branches of a loop land on identical
 *    setpoints (and therefore identical DAC codes).
 *  - Points: w0, then for each segment the points after its start up to and
 *    including its end (a turning point is measured once).
 *  - cycles > 1 repeats segments 1..n and requires wn == w0.
 *
 * List sweep: up to SWEEP_LIST_MAX host-supplied points, each with an
 * optional per-point settle time (0 = use the configured settle).
 */
#ifndef SWEEP_GEN_H
#define SWEEP_GEN_H

#include "fw_types.h"

typedef struct {
    int32_t  v_uv;
    uint32_t settle_us;   /* 0 = default */
} sweep_point_t;

typedef enum { SWEEP_NONE = 0, SWEEP_WAYPOINTS, SWEEP_LIST } sweep_kind_t;

typedef struct {
    sweep_kind_t         kind;
    /* waypoints */
    int32_t              wp_uv[SWEEP_WP_MAX];
    uint8_t              n_wp;
    int32_t              step_uv;
    uint16_t             cycles;
    /* list (storage owned by the caller) */
    const sweep_point_t *list;
    uint32_t             list_n;
} sweep_def_t;

typedef enum {
    SWG_OK = 0,
    SWG_EMPTY,          /* no definition / fewer than 2 waypoints / empty list */
    SWG_STEP_SMALL,     /* step < SWEEP_STEP_MIN_UV or <= 0                     */
    SWG_NOT_DIVISIBLE,  /* a segment is not an integer multiple of the step     */
    SWG_ZERO_SEGMENT,   /* two consecutive equal waypoints                       */
    SWG_LIMIT,          /* a point exceeds |v_limit|                             */
    SWG_TOO_MANY,       /* more than SWEEP_POINTS_MAX points                     */
    SWG_CYCLES,         /* cycles out of range or wn != w0 with cycles > 1       */
    SWG_SETTLE,         /* list settle out of range                               */
} swg_status_t;

/* Validate a definition and count its points. For SWG_NOT_DIVISIBLE,
 * *bad_segment receives the index of the first offending segment. */
swg_status_t sweep_validate(const sweep_def_t *d, int32_t v_limit_uv,
                            uint32_t *n_points, uint32_t *bad_segment);

/* For SWG_NOT_DIVISIBLE: the largest step <= the requested one that divides
 * every segment exactly (0 if none >= SWEEP_STEP_MIN_UV). */
int32_t sweep_suggest_step(const sweep_def_t *d);

typedef struct {
    const sweep_def_t *d;
    uint32_t idx;        /* index of the next point                    */
    uint32_t total;
    /* waypoint iteration state */
    uint16_t cycle;
    uint8_t  seg;        /* current segment (0 = the w0 point itself)  */
    int32_t  k;          /* step number within the segment             */
} sweep_iter_t;

void sweep_iter_init(sweep_iter_t *it, const sweep_def_t *d);
/* Next point; returns false when exhausted. *seg is the segment number
 * reported in PT records (0 for w0 / list points use 0). */
bool sweep_iter_next(sweep_iter_t *it, int32_t *v_uv, uint32_t *settle_us,
                     uint16_t *seg, uint32_t *idx);

const char *swg_status_name(swg_status_t s);

#endif /* SWEEP_GEN_H */
