/*
 * config.c — defaults, range check and storage of the acquisition
 * configuration (config.h). No hardware facts; the ranges are those of
 * docs/firmware/protocol.md §5.4.
 */
#include "config.h"

#include "ads1256.h"

static cfg_t s_cfg;

void cfg_defaults(cfg_t *c)
{
    c->adc_rate_x10 = 10000u;          /* 1000 SPS */
    c->n_avg = 1u;
    c->settle_us = 1000u;
    c->first_settle_us = 50000u;
    c->ramp_rate_uv_s = 10000000u;     /* 10 V/s */
    c->v_limit_uv = V_LIMIT_MAX_UV;
    /* 100 mA, the maximum: above the 92.6 mA worst-case passive bound, so the
     * default never acts on a passive DUT and the hardware plateau (OPA547T,
     * expected 40..65 mA) is what limits, flagged CLIM in both polarities
     * (spec §7.5 "effectively off"). 50 mA lay inside that plateau. */
    c->i_compliance_na = CFG_I_COMPL_MAX_NA;
    c->on_compliance = ONC_STOP;
    c->end = END_ZERO;
    c->line_hz = 50u;
    c->host_timeout_s = HOST_TIMEOUT_DEFAULT_S;
}

void cfg_init(void)
{
    cfg_defaults(&s_cfg);
}

const cfg_t *cfg_get(void)
{
    return &s_cfg;
}

void cfg_set(const cfg_t *c)
{
    s_cfg = *c;
}

bool cfg_valid(const cfg_t *c)
{
    return ads_rate_by_sps_x10(c->adc_rate_x10) != NULL
        && c->n_avg >= 1u && c->n_avg <= N_AVG_MAX
        && c->settle_us >= SETTLE_MIN_US && c->settle_us <= SETTLE_MAX_US
        && c->first_settle_us >= SETTLE_MIN_US && c->first_settle_us <= SETTLE_MAX_US
        && c->ramp_rate_uv_s >= CFG_RAMP_MIN_UV_S && c->ramp_rate_uv_s <= CFG_RAMP_MAX_UV_S
        && c->v_limit_uv >= CFG_V_LIMIT_MIN_UV && c->v_limit_uv <= V_LIMIT_MAX_UV
        && c->i_compliance_na >= CFG_I_COMPL_MIN_NA && c->i_compliance_na <= CFG_I_COMPL_MAX_NA
        && (c->on_compliance == ONC_STOP || c->on_compliance == ONC_FAULT)
        && (c->end == END_ZERO || c->end == END_HOLD)
        && (c->line_hz == 50u || c->line_hz == 60u)
        && c->host_timeout_s <= CFG_HOST_TIMEOUT_MAX_S;
}

const char *cfg_on_compliance_name(on_compliance_t v)
{
    return v == ONC_FAULT ? "fault" : "stop";
}

const char *cfg_end_name(end_action_t v)
{
    return v == END_HOLD ? "hold" : "zero";
}
