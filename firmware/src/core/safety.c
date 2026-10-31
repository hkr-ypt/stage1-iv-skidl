/*
 * safety.c — per-point classifier, front-end check and per-pass invariant
 * checks (contract: safety.h; firmware_spec.md §3.2 SI-1/SI-9, §6.2, §7.7).
 *
 * Facts relied on:
 *  - ADS1256 clip codes 0x7FFFFF / 0x800000 (SBAS288K Table 16); with
 *    either +-15 V rail off both INA128 outputs sit near AGND, i.e. both
 *    channels near -full scale at PGA 2 (blocks/sense_amp.py, spec §6.2).
 *  - R_sd = R12 + R32 = 50.2 kohm loads SENSE+ through R_top
 *    (blocks/sense_amp.py), hence the (V_K/R_sd) R_top term.
 *  - DAC channel B must read 0xFFFF in the shadow outside bring-up DAC.RAW
 *    (SI-1); the heartbeat runs exactly in the states that need it (SI-2);
 *    DBG_TIM2_STOP must be clear (spec §7.4 "Debug freeze").
 */
#include "safety.h"

#include "dac8552.h"
#include "port.h"
#include "scaling.h"

#include <math.h>

#define AFE_LOW_CODE     (-6710886)   /* -0.8 x (2^23 - 1) */

double safety_kelvin_error(const point_check_t *p)
{
    return p->v_set - p->v_k - p->i * p->r_s - (p->v_k / p->r_sd) * p->r_top;
}

double safety_kelvin_tol(const point_check_t *p)
{
    if (p->cal_nominal) {
        return 0.050 + 0.01 * fabs(p->v_set) + 15.0 * fabs(p->i);
    }
    return 0.010 + 0.002 * fabs(p->v_set) + 5.0 * fabs(p->i);
}

uint16_t safety_classify(const point_check_t *p)
{
    uint16_t flags = 0;
    if (p->clip_v) {
        flags |= PF_OVR_V;
    }
    if (p->clip_i) {
        flags |= PF_OVR_I;
    }
    if (p->clip_v || p->clip_i) {
        return flags;   /* a clipped value is not used by the other checks */
    }
    double av = fabs(p->v_set);
    double ai = fabs(p->i);
    double i_floor = (p->sigma_i_zero < 0.0) ? 30e-6 : fmax(2e-6, 4.0 * p->sigma_i_zero);
    if (av >= 1.0 && ai < i_floor && fabs(p->v_k) < 0.1 * av) {
        return (uint16_t)(flags | PF_NO_OUTPUT);
    }
    double e = safety_kelvin_error(p);
    double tol = safety_kelvin_tol(p);
    double sign = (p->v_set < 0.0) ? -1.0 : 1.0;
    if (ai >= 0.8 * p->i_plateau && e * sign > tol) {
        return (uint16_t)(flags | PF_CLIM);
    }
    if (fabs(e) > tol) {
        flags |= PF_KELVIN;
    }
    return flags;
}

bool safety_afe_pair_ok(int32_t i_code, int32_t v_code)
{
    if (scaling_code_clipped(i_code) || scaling_code_clipped(v_code)) {
        return false;
    }
    return !(i_code < AFE_LOW_CODE && v_code < AFE_LOW_CODE);
}

uint64_t safety_t_stale_us(uint32_t t18_us)
{
    return 2u * (uint64_t)t18_us + 100000u;
}

fault_t safety_check_invariants(inst_state_t st, bool hb_enabled, const char **detail)
{
    if (!port_stack_ok()) {
        *detail = "canary";
        return F_STACK;
    }
    if (port_dbg_tim2_frozen()) {
        *detail = "tim2_frozen";
        return F_INVARIANT;
    }
    bool raw = false;
#if FW_BRINGUP
    raw = dac_raw_active();
#endif
    if (!raw && dac_shadow_valid() && dac_shadow_b() != DAC_B_FULLSCALE) {
        *detail = "b_shadow";
        return F_INVARIANT;
    }
    bool needs_hb = (st == ST_ARMING || st == ST_ARMED || st == ST_SWEEPING);
    bool may_hb = needs_hb || st == ST_DISARMING;
    if ((needs_hb && !hb_enabled) || (hb_enabled && !may_hb)) {
        *detail = "hb_state";
        return F_INVARIANT;
    }
    *detail = "";
    return F_NONE;
}
