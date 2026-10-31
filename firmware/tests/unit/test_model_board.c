/*
 * test_model_board.c — the analog board model (sim/model_board.c) against
 * the numbers it must reproduce (firmware_spec.md §4.4 pump simulation,
 * §4.3/§4.6 scaling, the blocks/ sources). The model exercises firmware logic; these
 * tests make sure the logic is exercised against realistic behaviour.
 */
#include "unity.h"

#include "model_board.h"
#include "model_dac8552.h"

#include <math.h>

static dac_model_t   s_dac;
static board_model_t s_b;

void setUp(void)
{
    dacm_power_on(&s_dac);
    board_init(&s_b, &s_dac);
}

void tearDown(void) {}

/* Square wave on PC5 with the given half period, for `us`. */
static uint64_t pump(uint64_t t, uint32_t half_us, uint64_t us)
{
    bool level = s_b.hb_level;
    for (uint64_t end = t + us; t < end; t += half_us) {
        level = !level;
        board_hb(&s_b, level, t);
    }
    board_advance(&s_b, t);
    return t;
}

static void load_codes(uint16_t a, uint16_t b)
{
    s_dac.dac[0] = a;
    s_dac.dac[1] = b;
    s_dac.buf[0] = a;
    s_dac.buf[1] = b;
}

static void test_pump_steady_state_and_arm_edges(void)
{
    uint64_t t = 0;
    int edges = 0;
    bool level = false;
    while (!s_b.gate_on && edges < 40) {
        level = !level;
        board_hb(&s_b, level, t);
        t += 250;
        edges += level ? 1 : 0;
    }
    TEST_ASSERT_TRUE(s_b.gate_on);
    TEST_ASSERT_TRUE_MESSAGE(edges >= 2 && edges <= 5, "rising edges to arm");
    t = pump(t, 250, 200000);
    TEST_ASSERT_DOUBLE_WITHIN(0.25, 2.6, s_b.v_arm);   /* ~2.6-2.7 V at 2 kHz */
    /* 500 Hz still arms well above V_IH */
    setUp();
    pump(0, 1000, 300000);
    TEST_ASSERT_TRUE(s_b.v_arm > 2.0);
    TEST_ASSERT_TRUE(s_b.gate_on);
}

static uint64_t disarm_time(bool leave_high)
{
    uint64_t t = pump(0, 250, 200000);
    if (s_b.hb_level != leave_high) {
        board_hb(&s_b, leave_high, t);
    }
    uint64_t t_last = t;
    while (s_b.gate_on && t - t_last < 200000) {
        t += 100;
        board_advance(&s_b, t);
    }
    return t - t_last;
}

static void test_disarm_times_match_pump_simulation(void)
{
    uint64_t lo = disarm_time(false);
    TEST_ASSERT_TRUE_MESSAGE(lo >= 45000 && lo <= 63000, "PC5 low: 52-54 ms nominal, 63 ms worst");
    setUp();
    uint64_t hi = disarm_time(true);
    TEST_ASSERT_TRUE_MESSAGE(hi >= 55000 && hi <= 77000, "PC5 high: 64-66 ms nominal, 77 ms worst");
    TEST_ASSERT_TRUE(hi > lo);
    /* both stay inside the firmware's T_DISARM = 100 ms */
    TEST_ASSERT_TRUE(hi < 100000);
}

static void test_single_edge_cannot_arm(void)
{
    board_hb(&s_b, true, 0);
    board_advance(&s_b, 100000);
    TEST_ASSERT_FALSE(s_b.gate_on);
    TEST_ASSERT_TRUE(s_b.v_arm < 0.87);
}

static void test_vsweep_scaling(void)
{
    pump(0, 250, 100000);
    double off = s_b.sweep_offset_v;
    load_codes(0x8000, 0xFFFF);
    TEST_ASSERT_DOUBLE_WITHIN(0.001, off, board_vsweep(&s_b));
    load_codes(131, 0xFFFF);
    TEST_ASSERT_DOUBLE_WITHIN(0.002, -10.0, board_vsweep(&s_b) - off);
    load_codes(65404, 0xFFFF);
    TEST_ASSERT_DOUBLE_WITHIN(0.002, 10.0, board_vsweep(&s_b) - off);
    /* B lowered: the positive bound no longer holds (SI-1 is the only guard) */
    load_codes(65535, 0x0000);
    TEST_ASSERT_TRUE(board_vsweep(&s_b) > 13.0);
    TEST_ASSERT_TRUE(board_vsweep(&s_b) <= 13.8 + 1e-9);   /* U3B swing */
    /* gate open: no reference, no output */
    setUp();
    load_codes(65404, 0xFFFF);
    TEST_ASSERT_DOUBLE_WITHIN(0.003, 0.0, board_vsweep(&s_b));
}

static void armed_at(uint16_t code)
{
    pump(0, 250, 100000);
    load_codes(code, 0xFFFF);
    s_b.sweep_offset_v = 0.0;
}

static void test_resistor_operating_point(void)
{
    armed_at(65404);
    double vh, vl, i, ish;
    bool lim;
    board_solve(&s_b, &vh, &vl, &i, &ish, &lim);
    double vs = board_vsweep(&s_b);
    TEST_ASSERT_FALSE(lim);
    TEST_ASSERT_DOUBLE_WITHIN(2e-5, vs / (1000.0 + 121.0), i);
    TEST_ASSERT_DOUBLE_WITHIN(0.01, 1000.0 * i, vh - vl);
    /* INA outputs at the ADC pins */
    double ai = board_ain(&s_b, 0, 0), av = board_ain(&s_b, 2, 0);
    TEST_ASSERT_DOUBLE_WITHIN(0.001, 2.5 + 24.0056 * ish + s_b.ina_i_offset_v, ai);
    TEST_ASSERT_DOUBLE_WITHIN(0.002, 2.5 + 0.199203 * (vh - vl) + s_b.ina_v_offset_v, av);
    TEST_ASSERT_DOUBLE_WITHIN(1e-12, 2.5, board_ain(&s_b, 8, 0));
    TEST_ASSERT_DOUBLE_WITHIN(1e-12, 0.0, board_ain(&s_b, 1, 0));
}

static void test_current_limit_and_short(void)
{
    armed_at(65404);
    s_b.dut.kind = DUT_RESISTOR;
    s_b.dut.r_ohm = 1e-3;
    double vh, vl, i, ish;
    bool lim;
    board_solve(&s_b, &vh, &vl, &i, &ish, &lim);
    TEST_ASSERT_TRUE(lim);
    TEST_ASSERT_DOUBLE_WITHIN(1e-4, 0.049, i);
}

static void test_diode_and_cell(void)
{
    armed_at(36031);                         /* ~+1 V */
    s_b.dut.kind = DUT_DIODE;
    s_b.dut.is_a = 1e-12;
    s_b.dut.n = 1.0;
    s_b.dut.rs_ohm = 1.0;
    double vh, vl, i, ish;
    bool lim;
    board_solve(&s_b, &vh, &vl, &i, &ish, &lim);
    TEST_ASSERT_TRUE(i > 0.0);
    TEST_ASSERT_TRUE(vh - vl > 0.3 && vh - vl < 0.8);
    armed_at(29504);                         /* ~-1 V: reverse */
    s_b.dut.kind = DUT_DIODE;
    s_b.dut.is_a = 1e-12;
    s_b.dut.n = 1.0;
    s_b.dut.rs_ohm = 1.0;
    board_solve(&s_b, &vh, &vl, &i, &ish, &lim);
    TEST_ASSERT_TRUE(fabs(i) < 1e-9);
    /* a cell drives current into a 0 V output */
    armed_at(32768);
    s_b.dut.kind = DUT_CELL;
    s_b.dut.v_cell = 1.5;
    s_b.dut.r_ohm = 10.0;
    board_solve(&s_b, &vh, &vl, &i, &ish, &lim);
    TEST_ASSERT_TRUE(i < -0.005);
}

static void test_rails_missing_signature(void)
{
    armed_at(65404);
    s_b.rail_neg = false;
    double vh, vl, i, ish;
    bool lim;
    board_solve(&s_b, &vh, &vl, &i, &ish, &lim);
    TEST_ASSERT_DOUBLE_WITHIN(1e-9, 0.0, i);                 /* U12 open */
    TEST_ASSERT_TRUE(board_ain(&s_b, 0, 0) < 0.5);           /* both INA outputs near AGND */
    TEST_ASSERT_TRUE(board_ain(&s_b, 2, 0) < 0.5);
}

static void test_open_sense_leads(void)
{
    armed_at(65404);
    s_b.sense_plus_open = true;
    double av = board_ain(&s_b, 2, 0);
    TEST_ASSERT_TRUE(av < 2.5 + 0.01);         /* V_K reads ~0 or below */
    setUp();
    armed_at(65404);
    s_b.force_open = true;
    double vh, vl, i, ish;
    bool lim;
    board_solve(&s_b, &vh, &vl, &i, &ish, &lim);
    TEST_ASSERT_DOUBLE_WITHIN(1e-9, 0.0, i);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_pump_steady_state_and_arm_edges);
    RUN_TEST(test_disarm_times_match_pump_simulation);
    RUN_TEST(test_single_edge_cannot_arm);
    RUN_TEST(test_vsweep_scaling);
    RUN_TEST(test_resistor_operating_point);
    RUN_TEST(test_current_limit_and_short);
    RUN_TEST(test_diode_and_cell);
    RUN_TEST(test_rails_missing_signature);
    RUN_TEST(test_open_sense_leads);
    return UNITY_END();
}
