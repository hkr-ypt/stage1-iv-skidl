/*
 * test_safety.c — the per-point classifier, the front-end check and the
 * invariant checks of core/safety.c (firmware_spec.md §7.7, §9.2 table
 * "safety"), with inputs computed from the board's nominal circuit:
 *   R_top = 106 ohm (F1 + R30 + U12), R_bot = 15 ohm (F2 + R10),
 *   R_s = 121 ohm, R_sd = 50.2 kohm, plateau 49 mA.
 * Each case builds the (V_set, V_K, I) a real fault would produce.
 */
#include "unity.h"

#include "dac8552.h"
#include "safety.h"

#include <math.h>

extern bool g_stub_stack_ok;
extern bool g_stub_tim2_frozen;

#define R_TOP 106.0
#define R_BOT 15.0
#define R_S   121.0
#define R_SD  50200.0

void setUp(void)
{
    g_stub_stack_ok = true;
    g_stub_tim2_frozen = false;
    dac_init();
}

void tearDown(void) {}

static point_check_t base(bool nominal)
{
    point_check_t p = {0};
    p.cal_nominal = nominal;
    p.sigma_i_zero = 50e-9;
    p.r_s = R_S;
    p.r_top = R_TOP;
    p.r_sd = R_SD;
    p.i_plateau = 0.049;
    return p;
}

/* Healthy load line: V_set drives the DUT through R_top/R_bot; the SENSE+
 * attenuator draws V_hi / R_sd through R_top. */
static point_check_t on_load_line(double v_set, double r_dut, bool nominal)
{
    point_check_t p = base(nominal);
    double i = v_set / (r_dut + R_S + R_TOP * r_dut / R_SD);
    p.v_set = v_set;
    p.i = i;
    p.v_k = i * r_dut;
    return p;
}

static void test_resistor_sweep_is_clean(void)
{
    for (double v = -10.0; v <= 10.0; v += 0.25) {
        for (int nominal = 0; nominal < 2; nominal++) {
            point_check_t p = on_load_line(v, 1000.0, nominal != 0);
            TEST_ASSERT_EQUAL_HEX16(0, safety_classify(&p));
            TEST_ASSERT_DOUBLE_WITHIN(1e-6, 0.0, safety_kelvin_error(&p));
        }
    }
}

static void test_diode_forward_and_reverse_clean(void)
{
    /* forward: ~0.65 V across the diode, the rest across R_s */
    for (double v = 1.0; v <= 5.0; v += 0.5) {
        point_check_t p = base(false);
        p.v_set = v;
        p.v_k = 0.65;
        p.i = (v - 0.65 - (0.65 / R_SD) * R_TOP) / R_S;
        TEST_ASSERT_EQUAL_HEX16(0, safety_classify(&p));
    }
    /* reverse: almost no current, V_K ~ V_set (open DUT with Kelvin on it) */
    point_check_t p = base(false);
    p.v_set = -5.0;
    p.v_k = -5.0 / (1.0 + R_TOP / R_SD);
    p.i = 1e-9;
    TEST_ASSERT_EQUAL_HEX16(0, safety_classify(&p));
}

static void test_dut_drawing_45_mA_on_load_line_not_clim(void)
{
    point_check_t p = on_load_line(10.0, 100.0, false);   /* 45 mA */
    TEST_ASSERT_TRUE(p.i > 0.044 && p.i < 0.049);
    TEST_ASSERT_EQUAL_HEX16(0, safety_classify(&p));
}

static void test_current_limit_plateau_is_clim(void)
{
    /* U5 limits at 49 mA: V_K falls short of the load line */
    point_check_t p = base(false);
    p.v_set = 10.0;
    p.i = 0.049;
    p.v_k = 0.049 * 20.0;            /* a 20 ohm DUT in limit */
    TEST_ASSERT_EQUAL_HEX16(PF_CLIM, safety_classify(&p));
    p.v_set = -10.0;
    p.i = -0.049;
    p.v_k = -0.98;
    TEST_ASSERT_EQUAL_HEX16(PF_CLIM, safety_classify(&p));
}

static void test_open_sense_plus_is_kelvin(void)
{
    for (double v = 0.5; v <= 10.0; v += 0.5) {
        point_check_t p = on_load_line(v, 1000.0, false);
        p.v_k = 0.0;                 /* R32 pulls U7 +IN to AGND */
        TEST_ASSERT_EQUAL_HEX16(PF_KELVIN, safety_classify(&p));
    }
}

static void test_open_sense_minus_only_at_high_current(void)
{
    /* V_K reads high by I * R_bot: detectable once 10 ohm*|I| exceeds tol */
    point_check_t lo = on_load_line(1.0, 1000.0, false);  /* ~0.9 mA */
    lo.v_k += lo.i * R_BOT;
    TEST_ASSERT_EQUAL_HEX16(0, safety_classify(&lo));
    point_check_t hi = on_load_line(10.0, 200.0, false);  /* ~31 mA */
    hi.v_k += hi.i * R_BOT;
    TEST_ASSERT_EQUAL_HEX16(PF_KELVIN, safety_classify(&hi));
}

static void test_swapped_kelvin_is_kelvin(void)
{
    point_check_t p = on_load_line(2.0, 1000.0, false);
    p.v_k = -p.v_k;
    TEST_ASSERT_EQUAL_HEX16(PF_KELVIN, safety_classify(&p));
}

static void test_force_path_open_is_no_output(void)
{
    /* U12 open (+-15 V missing) or PTC/FORCE open: no current, V_K ~ 0 */
    point_check_t p = base(false);
    p.v_set = 5.0;
    p.i = 0.3e-6;
    p.v_k = 0.002;
    TEST_ASSERT_EQUAL_HEX16(PF_NO_OUTPUT, safety_classify(&p));
    /* below 1 V the check is not applied */
    p.v_set = 0.5;
    TEST_ASSERT_NOT_EQUAL(PF_NO_OUTPUT, safety_classify(&p) & PF_NO_OUTPUT);
    /* without a zero the current threshold is 30 uA */
    p.v_set = 5.0;
    p.sigma_i_zero = -1.0;
    p.i = 25e-6;
    TEST_ASSERT_EQUAL_HEX16(PF_NO_OUTPUT, safety_classify(&p));
}

static void test_open_dut_with_kelvin_connected_is_clean(void)
{
    point_check_t p = base(false);
    p.v_set = 8.0;
    p.i = 0.2e-6;
    p.v_k = 8.0 / (1.0 + R_TOP / R_SD);
    TEST_ASSERT_EQUAL_HEX16(0, safety_classify(&p));
}

static void test_clip_flags_suppress_other_checks(void)
{
    point_check_t p = on_load_line(5.0, 1000.0, false);
    p.v_k = 0.0;
    p.clip_v = true;
    TEST_ASSERT_EQUAL_HEX16(PF_OVR_V, safety_classify(&p));
    p.clip_i = true;
    TEST_ASSERT_EQUAL_HEX16(PF_OVR_V | PF_OVR_I, safety_classify(&p));
}

static void test_tolerance_formula(void)
{
    point_check_t p = base(false);
    p.v_set = 10.0;
    p.i = 0.01;
    TEST_ASSERT_DOUBLE_WITHIN(1e-12, 0.010 + 0.02 + 0.05, safety_kelvin_tol(&p));
    p.cal_nominal = true;
    TEST_ASSERT_DOUBLE_WITHIN(1e-12, 0.050 + 0.10 + 0.15, safety_kelvin_tol(&p));
}

static void test_afe_pair_check(void)
{
    TEST_ASSERT_TRUE(safety_afe_pair_ok(100, -200));
    /* +-15 V off: both channels near -FS */
    TEST_ASSERT_FALSE(safety_afe_pair_ok(-7700000, -7700000));
    /* only one channel low (e.g. a 1.5 V cell reverse): not the signature */
    TEST_ASSERT_TRUE(safety_afe_pair_ok(-7700000, 1000));
    TEST_ASSERT_FALSE(safety_afe_pair_ok(8388607, 0));
    TEST_ASSERT_FALSE(safety_afe_pair_ok(0, -8388608));
    TEST_ASSERT_TRUE(safety_afe_pair_ok(-6710885, -6710885));
    TEST_ASSERT_FALSE(safety_afe_pair_ok(-6710887, -6710887));
}

static void test_t_stale(void)
{
    TEST_ASSERT_EQUAL_UINT64(2u * 1180u + 100000u, safety_t_stale_us(1180u));
}

static void test_invariants(void)
{
    const char *d;
    TEST_ASSERT_EQUAL(F_NONE, safety_check_invariants(ST_DISARMED, false, &d));
    TEST_ASSERT_EQUAL(F_NONE, safety_check_invariants(ST_ARMED, true, &d));
    TEST_ASSERT_EQUAL(F_NONE, safety_check_invariants(ST_DISARMING, true, &d));
    TEST_ASSERT_EQUAL(F_NONE, safety_check_invariants(ST_DISARMING, false, &d));
    /* heartbeat state must match the instrument state */
    TEST_ASSERT_EQUAL(F_INVARIANT, safety_check_invariants(ST_ARMED, false, &d));
    TEST_ASSERT_EQUAL_STRING("hb_state", d);
    TEST_ASSERT_EQUAL(F_INVARIANT, safety_check_invariants(ST_DISARMED, true, &d));
    TEST_ASSERT_EQUAL(F_INVARIANT, safety_check_invariants(ST_FAULT, true, &d));
    g_stub_tim2_frozen = true;
    TEST_ASSERT_EQUAL(F_INVARIANT, safety_check_invariants(ST_DISARMED, false, &d));
    TEST_ASSERT_EQUAL_STRING("tim2_frozen", d);
    g_stub_tim2_frozen = false;
    g_stub_stack_ok = false;
    TEST_ASSERT_EQUAL(F_STACK, safety_check_invariants(ST_DISARMED, false, &d));
    g_stub_stack_ok = true;
    /* the B shadow after a normal update is 0xFFFF */
    TEST_ASSERT_TRUE(dac_update(0x8000));
    TEST_ASSERT_EQUAL(F_NONE, safety_check_invariants(ST_ARMED, true, &d));
#if FW_BRINGUP
    TEST_ASSERT_TRUE(dac_raw_write(0x8000, 0x0000));
    TEST_ASSERT_EQUAL(F_NONE, safety_check_invariants(ST_ARMED, true, &d));  /* suspended */
    TEST_ASSERT_TRUE(dac_update(0x8000));
#endif
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_resistor_sweep_is_clean);
    RUN_TEST(test_diode_forward_and_reverse_clean);
    RUN_TEST(test_dut_drawing_45_mA_on_load_line_not_clim);
    RUN_TEST(test_current_limit_plateau_is_clim);
    RUN_TEST(test_open_sense_plus_is_kelvin);
    RUN_TEST(test_open_sense_minus_only_at_high_current);
    RUN_TEST(test_swapped_kelvin_is_kelvin);
    RUN_TEST(test_force_path_open_is_no_output);
    RUN_TEST(test_open_dut_with_kelvin_connected_is_clean);
    RUN_TEST(test_clip_flags_suppress_other_checks);
    RUN_TEST(test_tolerance_formula);
    RUN_TEST(test_afe_pair_check);
    RUN_TEST(test_t_stale);
    RUN_TEST(test_invariants);
    return UNITY_END();
}
