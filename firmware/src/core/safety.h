/*
 * safety.h — pure checks of firmware_spec.md §7.7 and the per-pass invariant
 * checks of SI-9.
 *
 * Per-point classifier (all quantities in SI units):
 *   e   = V_set - V_K - I*R_s - (V_K/R_sd)*R_top
 *   tol = 10 mV + 0.2 %|V_set| + 5 ohm*|I|     (calibrated)
 *         50 mV + 1 %|V_set|   + 15 ohm*|I|    (nominal constants)
 *   OVR_V / OVR_I : the channel's mean came from a clipped conversion; a
 *                   clipped channel is not used by the other checks
 *   NO_OUTPUT     : |V_set| >= 1 V, |I| < max(2 uA, 4 sigma_I(zero))
 *                   (100 uA without a valid zero: the worst-case INA128
 *                   offset to 85 C), |V_K| < 0.1 |V_set|
 *   CLIM          : |I| >= 0.8 i_plateau and e*sign(V_set) > tol; also
 *                   KELVIN when V_K carries the open-SENSE+ signature
 *                   (V_K*sign(V_set) < -0.1 V and |V_K + I*15 ohm| <=
 *                   3 ohm*|I| + 20 mV), so a lifted lead is not hidden
 *   KELVIN        : |e| > tol, unless NO_OUTPUT or CLIM explains it
 * The flags are warnings for the operator, not proofs (spec §7.7).
 */
#ifndef SAFETY_H
#define SAFETY_H

#include "fw_types.h"

typedef struct {
    double v_set;          /* V, calibrated V_SWEEP for the loaded code     */
    double v_k;            /* V, measured Kelvin voltage                    */
    double i;              /* A, measured current                           */
    bool   clip_v, clip_i;
    bool   cal_nominal;
    double sigma_i_zero;   /* A, single-conversion sigma of the zero; < 0 = no zero */
    double r_s, r_top, r_sd, i_plateau;
} point_check_t;

uint16_t safety_classify(const point_check_t *p);
double   safety_kelvin_error(const point_check_t *p);
double   safety_kelvin_tol(const point_check_t *p);

/* Gate-open front-end check of one I/V pair (spec §6.2 item 4): fails on a
 * clip code or when both channels are below -0.8 x full scale. */
bool     safety_afe_pair_ok(int32_t i_code, int32_t v_code);

/* T_STALE = 2 t18(DR) + 100 ms. */
uint64_t safety_t_stale_us(uint32_t t18_us);

/* Invariant checks run every pass in every state. Returns F_NONE or the
 * fault to latch with *detail naming the check. */
fault_t  safety_check_invariants(inst_state_t st, bool hb_enabled, const char **detail);

#endif /* SAFETY_H */
