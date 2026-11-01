/*
 * test_scaling.c — unit tests of core/scaling.c with the nominal constants
 * (docs/firmware/firmware_spec.md §4.3: +-10.000 V is codes 131...65404,
 * 306.40 uV/code, zero at 32767.5) and with perturbed calibrations: exact
 * rounding, v_limit and DAC-range rejection (never clamping), monotonicity
 * and round trips over all 65536 codes, and the ADC conversions with and
 * without a session zero.
 */
#include <math.h>
#include <string.h>

#include "scaling.h"
#include "unity.h"

static cal_coeffs_t s_nom;

void setUp(void)
{
    calib_nominal(&s_nom);
}

void tearDown(void) {}

/* ---- setpoint -> code --------------------------------------------------------- */

static void test_nominal_endpoints(void)
{
    uint16_t code = 0;
    TEST_ASSERT_EQUAL_INT(E_OK, scaling_v_to_code(&s_nom, 10000000, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_UINT16(65404, code);
    TEST_ASSERT_EQUAL_INT(E_OK, scaling_v_to_code(&s_nom, -10000000, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_UINT16(131, code);
    /* 0 V: 32767.5 rounds up to 32768, which is +153 uV (protocol.md §8). */
    TEST_ASSERT_EQUAL_INT(E_OK, scaling_v_to_code(&s_nom, 0, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_UINT16(32768, code);
    TEST_ASSERT_EQUAL_INT32(153, scaling_code_to_v_uv(&s_nom, 32768));
    TEST_ASSERT_EQUAL_INT32(-153, scaling_code_to_v_uv(&s_nom, 32767));
    /* 0.5 V -> 32767.5 + 1631.85 = 34399.35 -> 34399. */
    TEST_ASSERT_EQUAL_INT(E_OK, scaling_v_to_code(&s_nom, 500000, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_UINT16(34399, code);
    /* code 0 and 65535 are -+32767.5 codes from the zero: -+10.040007 V */
    TEST_ASSERT_EQUAL_INT32(-10040007, scaling_code_to_v_uv(&s_nom, 0));
    TEST_ASSERT_EQUAL_INT32(10040007, scaling_code_to_v_uv(&s_nom, 65535));
}

static void test_rejects_beyond_v_limit(void)
{
    uint16_t code = 0xBEEF;
    TEST_ASSERT_EQUAL_INT(E_OK, scaling_v_to_code(&s_nom, 5000000, 5000000, &code));
    TEST_ASSERT_EQUAL_INT(E_OK, scaling_v_to_code(&s_nom, -5000000, 5000000, &code));
    code = 0xBEEF;
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&s_nom, 5000001, 5000000, &code));
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&s_nom, -5000001, 5000000, &code));
    TEST_ASSERT_EQUAL_HEX16(0xBEEF, code);                 /* never written on error */
    /* A limit above the 10 V software ceiling is itself an error. */
    TEST_ASSERT_EQUAL_INT(E_OK, scaling_v_to_code(&s_nom, 0, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&s_nom, 0, V_LIMIT_MAX_UV + 1, &code));
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&s_nom, 0, INT32_MAX, &code));
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&s_nom, 10000001, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&s_nom, INT32_MIN, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&s_nom, INT32_MAX, V_LIMIT_MAX_UV, &code));
    /* Zero limit allows exactly 0 V; a negative limit allows nothing. */
    TEST_ASSERT_EQUAL_INT(E_OK, scaling_v_to_code(&s_nom, 0, 0, &code));
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&s_nom, 1, 0, &code));
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&s_nom, 0, -1, &code));
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&s_nom, 0, INT32_MIN, &code));
}

/* Largest accepted setpoint (uV) for a calibration, by bisection. */
static int32_t max_accepted_uv(const cal_coeffs_t *c)
{
    int32_t lo = 0, hi = V_LIMIT_MAX_UV + 1;   /* lo accepted, hi rejected */
    uint16_t code;
    TEST_ASSERT_EQUAL_INT(E_OK, scaling_v_to_code(c, lo, V_LIMIT_MAX_UV, &code));
    while (hi - lo > 1) {
        int32_t mid = lo + (hi - lo) / 2;
        if (scaling_v_to_code(c, mid, V_LIMIT_MAX_UV, &code) == E_OK) {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    return lo;
}

static void test_rejects_beyond_code_range_perturbed(void)
{
    uint16_t code = 0;
    cal_coeffs_t c = s_nom;

    /* Zero shifted up by 300 codes (inside the plausibility window). */
    c.c_dac = s_nom.c_dac + 300.0;
    TEST_ASSERT_TRUE(calib_plausible(&c));
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&c, 10000000, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_INT(E_OK, scaling_v_to_code(&c, -10000000, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_UINT16(431, code);
    int32_t vmax = max_accepted_uv(&c);
    TEST_ASSERT_EQUAL_INT(E_OK, scaling_v_to_code(&c, vmax, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_UINT16(65535, code);                 /* the last code, not clamped */
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&c, vmax + 1, V_LIMIT_MAX_UV, &code));
    /* the boundary sits where c_dac + v/g_dac crosses 65535.5 */
    double v_edge = (65535.5 - c.c_dac) * c.g_dac * 1e6;
    TEST_ASSERT_TRUE((double)vmax < v_edge && (double)(vmax + 1) >= v_edge);

    /* Zero shifted down by 400 codes: -10 V would need a negative code. */
    c = s_nom;
    c.c_dac = s_nom.c_dac - 400.0;
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&c, -10000000, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_INT(E_OK, scaling_v_to_code(&c, 10000000, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_UINT16(65004, code);

    /* Gain 2 % low: both +-10 V ends leave the code range. */
    c = s_nom;
    c.g_dac = s_nom.g_dac * 0.98;
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&c, 10000000, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&c, -10000000, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_INT(E_OK, scaling_v_to_code(&c, 9800000, V_LIMIT_MAX_UV, &code));

    /* Nonsense coefficients never produce a code. */
    c = s_nom;
    c.g_dac = 0.0;
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&c, 0, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&c, 1, V_LIMIT_MAX_UV, &code));
    c = s_nom;
    c.c_dac = NAN;
    TEST_ASSERT_EQUAL_INT(E_RANGE, scaling_v_to_code(&c, 0, V_LIMIT_MAX_UV, &code));
    c = s_nom;
    c.g_dac = INFINITY;
    TEST_ASSERT_EQUAL_INT(E_OK, scaling_v_to_code(&c, 0, V_LIMIT_MAX_UV, &code));
    TEST_ASSERT_EQUAL_UINT16(32768, code);
    TEST_ASSERT_EQUAL_INT32(INT32_MAX, scaling_code_to_v_uv(&c, 40000));
    TEST_ASSERT_EQUAL_INT32(INT32_MIN, scaling_code_to_v_uv(&c, 0));
    c.g_dac = NAN;
    TEST_ASSERT_EQUAL_INT32(0, scaling_code_to_v_uv(&c, 0));
}

static void test_all_codes_monotonic_and_round_trip(void)
{
    uint16_t back = 0;
    int32_t prev = INT32_MIN;
    unsigned in_limit = 0;
    for (uint32_t code = 0; code <= 65535u; code++) {
        int32_t v = scaling_code_to_v_uv(&s_nom, (uint16_t)code);
        if (code > 0) {
            /* 306.40 uV per code, rounded to 1 uV */
            TEST_ASSERT_TRUE(v - prev == 306 || v - prev == 307);
        }
        prev = v;
        err_t e = scaling_v_to_code(&s_nom, v, V_LIMIT_MAX_UV, &back);
        if (v >= -10000000 && v <= 10000000) {
            TEST_ASSERT_EQUAL_INT(E_OK, e);
            TEST_ASSERT_EQUAL_UINT16(code, back);
            in_limit++;
        } else {
            TEST_ASSERT_EQUAL_INT(E_RANGE, e);
        }
    }
    TEST_ASSERT_EQUAL_UINT(65404u - 131u + 1u, in_limit);

    /* setpoint -> code is monotonic and within half a code of the request */
    uint16_t prev_code = 0;
    for (int32_t v = -10000000; v <= 10000000; v += 7) {
        uint16_t code = 0;
        TEST_ASSERT_EQUAL_INT(E_OK, scaling_v_to_code(&s_nom, v, V_LIMIT_MAX_UV, &code));
        TEST_ASSERT_TRUE(code >= prev_code);
        prev_code = code;
        double err = s_nom.g_dac * ((double)code - s_nom.c_dac) - (double)v / 1e6;
        TEST_ASSERT_TRUE(fabs(err) <= s_nom.g_dac * 0.5 + 1e-12);
    }
}

/* ---- ADC code -> V, I ------------------------------------------------------------ */

static void test_adc_conversions_nominal(void)
{
    /* g_v = 1.498586958 uV/code, g_i = 12.43556797 nA/code (firmware_spec §7.10) */
    TEST_ASSERT_EQUAL_INT64(1498587, scaling_v_nv(&s_nom, NULL, 1000.0));
    TEST_ASSERT_EQUAL_INT64(-1498587, scaling_v_nv(&s_nom, NULL, -1000.0));
    TEST_ASSERT_EQUAL_INT64(12435568, scaling_i_pa(&s_nom, NULL, 1000.0));
    TEST_ASSERT_EQUAL_INT64(-12435568, scaling_i_pa(&s_nom, NULL, -1000.0));
    TEST_ASSERT_EQUAL_INT64(0, scaling_v_nv(&s_nom, NULL, 0.0));
    /* Full scale: 8388607 codes = 12.571057 V on V, 104.317093 mA on I. */
    TEST_ASSERT_INT64_WITHIN(1, 12571057047LL, scaling_v_nv(&s_nom, NULL, 8388607.0));
    TEST_ASSERT_INT64_WITHIN(1, 104317092558LL, scaling_i_pa(&s_nom, NULL, 8388607.0));
}

static void test_adc_zero_offsets(void)
{
    zero_t z;
    memset(&z, 0, sizeof z);
    z.o_v = -112.4;
    z.o_i = 387.1;

    /* An invalid zero is ignored. */
    z.valid = false;
    TEST_ASSERT_EQUAL_INT64(scaling_i_pa(&s_nom, NULL, 387.1), scaling_i_pa(&s_nom, &z, 387.1));
    TEST_ASSERT_TRUE(scaling_i_pa(&s_nom, &z, 387.1) > 4000000);

    /* A valid zero: reading at the offset is exactly 0; V and I use their own. */
    z.valid = true;
    TEST_ASSERT_EQUAL_INT64(0, scaling_v_nv(&s_nom, &z, -112.4));
    TEST_ASSERT_EQUAL_INT64(0, scaling_i_pa(&s_nom, &z, 387.1));
    TEST_ASSERT_EQUAL_INT64(1498587, scaling_v_nv(&s_nom, &z, 1000.0 - 112.4));
    TEST_ASSERT_EQUAL_INT64(-12435568, scaling_i_pa(&s_nom, &z, 387.1 - 1000.0));
    TEST_ASSERT_TRUE(scaling_v_nv(&s_nom, &z, 387.1) != 0);
}

static void test_adc_rounding_and_saturation(void)
{
    cal_coeffs_t c = s_nom;
    c.g_v = ldexp(1.0, -20);       /* 953.67431640625 nV/code, exact in binary */
    c.g_i = ldexp(1.0, -40);       /* 0.9094947017729282 pA/code */
    TEST_ASSERT_EQUAL_INT64(954, scaling_v_nv(&c, NULL, 1.0));
    TEST_ASSERT_EQUAL_INT64(-954, scaling_v_nv(&c, NULL, -1.0));
    TEST_ASSERT_EQUAL_INT64(477, scaling_v_nv(&c, NULL, 0.5));       /* 476.84 */
    TEST_ASSERT_EQUAL_INT64(-477, scaling_v_nv(&c, NULL, -0.5));
    TEST_ASSERT_EQUAL_INT64(1, scaling_i_pa(&c, NULL, 1.0));
    TEST_ASSERT_EQUAL_INT64(1, scaling_i_pa(&c, NULL, 0.55));        /* 0.5002 */
    TEST_ASSERT_EQUAL_INT64(0, scaling_i_pa(&c, NULL, 0.54));        /* 0.4911 */
    TEST_ASSERT_EQUAL_INT64(-1, scaling_i_pa(&c, NULL, -0.56));      /* -0.5093 */
    TEST_ASSERT_EQUAL_INT64(0, scaling_i_pa(&c, NULL, -0.54));

    /* Absurd inputs saturate instead of invoking undefined conversions. */
    TEST_ASSERT_EQUAL_INT64(INT64_MAX, scaling_v_nv(&s_nom, NULL, 1e300));
    TEST_ASSERT_EQUAL_INT64(INT64_MIN, scaling_i_pa(&s_nom, NULL, -1e300));
    TEST_ASSERT_EQUAL_INT64(0, scaling_v_nv(&s_nom, NULL, NAN));
    TEST_ASSERT_EQUAL_INT64(INT64_MAX, scaling_i_pa(&s_nom, NULL, INFINITY));
}

static void test_clip_codes(void)
{
    TEST_ASSERT_TRUE(scaling_code_clipped(8388607));
    TEST_ASSERT_TRUE(scaling_code_clipped(-8388608));
    TEST_ASSERT_FALSE(scaling_code_clipped(8388606));
    TEST_ASSERT_FALSE(scaling_code_clipped(-8388607));
    TEST_ASSERT_FALSE(scaling_code_clipped(0));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_nominal_endpoints);
    RUN_TEST(test_rejects_beyond_v_limit);
    RUN_TEST(test_rejects_beyond_code_range_perturbed);
    RUN_TEST(test_all_codes_monotonic_and_round_trip);
    RUN_TEST(test_adc_conversions_nominal);
    RUN_TEST(test_adc_zero_offsets);
    RUN_TEST(test_adc_rounding_and_saturation);
    RUN_TEST(test_clip_codes);
    return UNITY_END();
}
