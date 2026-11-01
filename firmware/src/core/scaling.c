/*
 * scaling.c — volts/amperes <-> DAC/ADC codes (contract: scaling.h).
 *
 * Models (docs/firmware/firmware_spec.md §4.3, §4.6, §7.10):
 *  - DAC8552 channel A through the U3 level shift (blocks/dac_sweep.py):
 *    V_SWEEP = g_dac * (code - c_dac); nominally g_dac = (R4/R3) VREF/65536
 *    = 306.40 uV/code and c_dac = 32767.5 with channel B at 0xFFFF
 *    (DAC8552 SLAS430A: 16-bit straight binary, codes 0..65535).
 *  - ADS1256 at PGA 2 (SBAS288K, LSB = 2 VREF / (PGA (2^23 - 1))):
 *    V_K = g_v * (code - o_v), I = g_i * (code - o_i), with the session zero
 *    offsets o_v, o_i in raw codes (0 when no valid zero exists).
 * Doubles are used inside the conversions only; inputs and outputs are
 * integers (uV, nV, pA). Every conversion from double to an integer is
 * range-checked first, so NaN or absurd coefficients cannot cause undefined
 * behaviour: setpoints are rejected, readings saturate.
 */
#include "scaling.h"

#include <math.h>

err_t scaling_v_to_code(const cal_coeffs_t *c, int32_t v_uv, int32_t v_limit_uv,
                        uint16_t *code_out)
{
    if (c == NULL || code_out == NULL) {
        return E_RANGE;
    }
    if (v_limit_uv > V_LIMIT_MAX_UV) {
        return E_RANGE;
    }
    int64_t mag = (v_uv < 0) ? -(int64_t)v_uv : (int64_t)v_uv;
    if (mag > (int64_t)v_limit_uv) {
        return E_RANGE;                 /* also every v for a negative limit */
    }
    double x = c->c_dac + ((double)v_uv / 1e6) / c->g_dac;
    double f = floor(x + 0.5);
    if (!(f >= 0.0 && f <= 65535.0)) {  /* NaN fails this test too */
        return E_RANGE;
    }
    *code_out = (uint16_t)f;
    return E_OK;
}

/* Round to nearest (halves upward) and saturate into int64. */
static int64_t round_sat_i64(double x)
{
    double f = floor(x + 0.5);
    if (isnan(f)) {
        return 0;                       /* NaN: no meaningful value */
    }
    if (f >= 9223372036854775807.0) {   /* 2^63 after rounding to double */
        return INT64_MAX;
    }
    if (f <= -9223372036854775808.0) {
        return INT64_MIN;
    }
    return (int64_t)f;
}

int32_t scaling_code_to_v_uv(const cal_coeffs_t *c, uint16_t code)
{
    if (c == NULL) {
        return 0;
    }
    double uv = c->g_dac * ((double)code - c->c_dac) * 1e6;
    int64_t r = round_sat_i64(uv);
    if (r > INT32_MAX) {
        return INT32_MAX;
    }
    if (r < INT32_MIN) {
        return INT32_MIN;
    }
    return (int32_t)r;
}

int64_t scaling_v_nv(const cal_coeffs_t *c, const zero_t *z, double mean_code)
{
    if (c == NULL) {
        return 0;
    }
    double offset = (z != NULL && z->valid) ? z->o_v : 0.0;
    return round_sat_i64(c->g_v * (mean_code - offset) * 1e9);
}

int64_t scaling_i_pa(const cal_coeffs_t *c, const zero_t *z, double mean_code)
{
    if (c == NULL) {
        return 0;
    }
    double offset = (z != NULL && z->valid) ? z->o_i : 0.0;
    return round_sat_i64(c->g_i * (mean_code - offset) * 1e12);
}
