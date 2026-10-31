/*
 * config.h — the host-settable acquisition configuration (protocol.md §5.4,
 * firmware_spec.md §7.5 "Defaults and ranges").
 *
 * Values are stored in the integer units the protocol parses them into, so
 * nothing is ever rounded: SPS x 10, us, uV, uV/s, nA, seconds.
 */
#ifndef CONFIG_H
#define CONFIG_H

#include "fw_types.h"

typedef enum { ONC_STOP = 0, ONC_FAULT } on_compliance_t;
typedef enum { END_ZERO = 0, END_HOLD } end_action_t;

typedef struct {
    uint32_t        adc_rate_x10;      /* SPS x 10, one of the v1 rates      */
    uint16_t        n_avg;             /* I/V pairs per point, 1..256         */
    uint32_t        settle_us;         /* SETTLE_MIN_US .. SETTLE_MAX_US      */
    uint32_t        first_settle_us;   /* after the pre-ramp                  */
    uint32_t        ramp_rate_uv_s;    /* 10 000 .. 10 000 000 (0.01..10 V/s) */
    int32_t         v_limit_uv;        /* 1 000 .. V_LIMIT_MAX_UV             */
    uint32_t        i_compliance_na;   /* 1 000 .. 100 000 000 (1 uA..100 mA) */
    on_compliance_t on_compliance;
    end_action_t    end;
    uint8_t         line_hz;           /* 50 or 60 (informational)            */
    uint32_t        host_timeout_s;    /* 0 (off) .. 3600                     */
} cfg_t;

/* Ranges (protocol.md §5.4). */
#define CFG_RAMP_MIN_UV_S        10000u
#define CFG_RAMP_MAX_UV_S        10000000u
#define CFG_V_LIMIT_MIN_UV       1000
#define CFG_I_COMPL_MIN_NA       1000u
#define CFG_I_COMPL_MAX_NA       100000000u
#define CFG_HOST_TIMEOUT_MAX_S   3600u

void         cfg_defaults(cfg_t *c);
const cfg_t *cfg_get(void);
void         cfg_set(const cfg_t *c);   /* caller has validated every field */
void         cfg_init(void);            /* active configuration := defaults */

/* True if every field is inside its documented range. */
bool         cfg_valid(const cfg_t *c);

const char  *cfg_on_compliance_name(on_compliance_t v);
const char  *cfg_end_name(end_action_t v);

#endif /* CONFIG_H */
