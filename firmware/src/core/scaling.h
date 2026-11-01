/*
 * scaling.h — conversions between volts/amperes and DAC/ADC codes.
 *
 * Units on this boundary: setpoints in integer microvolts (int32), measured
 * voltages in integer nanovolts (int64), measured currents in integer
 * picoamperes (int64). Doubles are used inside the conversions only.
 *
 * Sign convention: positive I is conventional current leaving FORCE+, through
 * the DUT, into FORCE-; positive V_K means SENSE+ above SENSE-.
 */
#ifndef SCALING_H
#define SCALING_H

#include "calib.h"

/* Setpoint -> DAC code: code = floor(c_dac + v/g_dac + 0.5).
 * Returns E_RANGE if |v| > v_limit_uV, v_limit_uV > V_LIMIT_MAX_UV, or the
 * code falls outside 0..65535. Never clamps. */
err_t   scaling_v_to_code(const cal_coeffs_t *c, int32_t v_uv, int32_t v_limit_uv,
                          uint16_t *code_out);
/* DAC code -> calibrated V_SWEEP in uV (rounded to nearest). */
int32_t scaling_code_to_v_uv(const cal_coeffs_t *c, uint16_t code);

/* Mean raw ADC code -> DUT voltage (nV) / current (pA), offset-corrected with
 * the session zero when z->valid, otherwise with a zero offset. */
int64_t scaling_v_nv(const cal_coeffs_t *c, const zero_t *z, double mean_code);
int64_t scaling_i_pa(const cal_coeffs_t *c, const zero_t *z, double mean_code);

/* ADC clip codes. */
static inline bool scaling_code_clipped(int32_t code)
{
    return code == 8388607 || code == -8388608;
}

#endif /* SCALING_H */
