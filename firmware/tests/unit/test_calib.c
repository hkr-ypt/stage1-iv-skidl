/*
 * test_calib.c — calibration record, A/B sector selection and persistence
 * (core/calib.c) on a fake flash with STM32F411 semantics and power-loss
 * injection (support/calib/fake_flash.c).
 *
 * The property that matters: no interrupted erase or program can lose the
 * newest valid record, and nothing invalid is ever used.
 */
#include "unity.h"

#include "calib.h"
#include "calib/fake_flash.h"

#include <math.h>
#include <string.h>

static const uint32_t k_uid[3] = { 0x0034002Bu, 0x34365112u, 0x33353533u };

void setUp(void)
{
    fake_flash_reset();
}

void tearDown(void)
{
    TEST_ASSERT_EQUAL_UINT(0, fake_flash_misaligned_programs());
    TEST_ASSERT_EQUAL_UINT(0, fake_flash_dirty_programs());
}

static cal_coeffs_t coeffs(double r_s)
{
    cal_coeffs_t c;
    calib_nominal(&c);
    c.r_s = r_s;
    return c;
}

static void test_record_layout_and_crc(void)
{
    TEST_ASSERT_EQUAL(128, sizeof(cal_record_t));
    TEST_ASSERT_EQUAL(32, offsetof(cal_record_t, c));
    TEST_ASSERT_EQUAL(124, offsetof(cal_record_t, crc));
    /* CRC-32/IEEE check value */
    TEST_ASSERT_EQUAL_HEX32(0xCBF43926u, crc32_ieee("123456789", 9));
    cal_record_t r;
    cal_coeffs_t c = coeffs(120.0);
    calib_build_record(&r, &c, CAL_M_ALL, 7, k_uid, 1700000000u, "bench");
    TEST_ASSERT_EQUAL(CALREC_VALID, calib_check(&r, k_uid));
    TEST_ASSERT_EQUAL_STRING("bench", r.note);
    /* every single-bit flip is caught */
    for (size_t byte = 0; byte < sizeof r; byte++) {
        for (unsigned bit = 0; bit < 8; bit++) {
            cal_record_t x = r;
            ((uint8_t *)&x)[byte] ^= (uint8_t)(1u << bit);
            TEST_ASSERT_NOT_EQUAL(CALREC_VALID, calib_check(&x, k_uid));
        }
    }
}

static void test_check_statuses(void)
{
    cal_record_t r;
    cal_coeffs_t c = coeffs(120.0);
    memset(&r, 0xFF, sizeof r);
    TEST_ASSERT_EQUAL(CALREC_ERASED, calib_check(&r, k_uid));
    calib_build_record(&r, &c, CAL_M_ALL, 1, k_uid, 0, NULL);
    uint32_t other[3] = { 1, 2, 3 };
    TEST_ASSERT_EQUAL(CALREC_WRONG_UID, calib_check(&r, other));
    cal_record_t v = r;
    v.version = 2;
    v.crc = crc32_ieee(&v, offsetof(cal_record_t, crc));
    TEST_ASSERT_EQUAL(CALREC_BAD_VERSION, calib_check(&v, k_uid));
    cal_record_t m = r;
    m.magic = 0x12345678u;
    m.crc = crc32_ieee(&m, offsetof(cal_record_t, crc));
    TEST_ASSERT_EQUAL(CALREC_BAD_MAGIC, calib_check(&m, k_uid));
    /* CRC-valid but implausible (e.g. written by a buggy tool) */
    cal_coeffs_t bad = coeffs(500.0);
    calib_build_record(&v, &bad, CAL_M_ALL, 1, k_uid, 0, NULL);
    TEST_ASSERT_EQUAL(CALREC_IMPLAUSIBLE, calib_check(&v, k_uid));
}

static void test_plausibility_window(void)
{
    cal_coeffs_t n;
    calib_nominal(&n);
    TEST_ASSERT_TRUE(calib_plausible(&n));
    TEST_ASSERT_DOUBLE_WITHIN(1e-9, 306.4013e-6, n.g_dac);
    TEST_ASSERT_DOUBLE_WITHIN(1e-6, 32767.5, n.c_dac);
    struct { int field; double lo_ok, hi_ok, lo_bad, hi_bad; } k[] = {
        { 0, n.g_dac * 0.981, n.g_dac * 1.019, n.g_dac * 0.979, n.g_dac * 1.021 },
        { 1, n.c_dac - 999, n.c_dac + 999, n.c_dac - 1001, n.c_dac + 1001 },
        { 2, n.g_v * 0.951, n.g_v * 1.049, n.g_v * 0.949, n.g_v * 1.051 },
        { 3, n.g_i * 0.951, n.g_i * 1.049, n.g_i * 0.949, n.g_i * 1.051 },
        { 6, 0.0201, 0.0999, 0.0199, 0.1001 },
    };
    for (size_t i = 0; i < sizeof k / sizeof k[0]; i++) {
        double *f[] = { &n.g_dac, &n.c_dac, &n.g_v, &n.g_i, &n.r_s, &n.r_top, &n.i_plateau };
        double vals[4] = { k[i].lo_ok, k[i].hi_ok, k[i].lo_bad, k[i].hi_bad };
        for (int j = 0; j < 4; j++) {
            cal_coeffs_t c;
            calib_nominal(&c);
            double *cf[] = { &c.g_dac, &c.c_dac, &c.g_v, &c.g_i, &c.r_s, &c.r_top, &c.i_plateau };
            *cf[k[i].field] = vals[j];
            TEST_ASSERT_EQUAL_MESSAGE(j < 2, calib_plausible(&c), "window edge");
        }
        (void)f;
    }
    cal_coeffs_t c = n;
    c.r_top = c.r_s + 1.0;                      /* R_top is part of R_s */
    TEST_ASSERT_FALSE(calib_plausible(&c));
    c = n;
    c.g_v = NAN;
    TEST_ASSERT_FALSE(calib_plausible(&c));
}

static void test_empty_flash_gives_nominal(void)
{
    cal_coeffs_t c;
    calib_scan_t scan;
    uint32_t seq = 99;
    TEST_ASSERT_FALSE(calib_load(&c, &scan, &seq, NULL));
    cal_coeffs_t n;
    calib_nominal(&n);
    TEST_ASSERT_EQUAL_MEMORY(&n, &c, sizeof n);
    TEST_ASSERT_EQUAL(0u, seq);
    TEST_ASSERT_EQUAL(-1, scan.newest);
    TEST_ASSERT_EQUAL(0u, calib_target_sector(&scan));
}

static void test_alternating_sectors_and_sequence(void)
{
    uint32_t seq;
    for (unsigned i = 1; i <= 6; i++) {
        cal_coeffs_t c = coeffs(110.0 + i);   /* r_s > r_top (106) */
        TEST_ASSERT_EQUAL(E_OK, calib_save(&c, CAL_M_ALL, i, NULL, &seq));
        TEST_ASSERT_EQUAL_UINT32(i, seq);
        cal_coeffs_t back;
        calib_scan_t scan;
        uint32_t s2;
        TEST_ASSERT_TRUE(calib_load(&back, &scan, &s2, NULL));
        TEST_ASSERT_EQUAL_UINT32(i, s2);
        TEST_ASSERT_EQUAL_DOUBLE(110.0 + i, back.r_s);
        TEST_ASSERT_EQUAL((int)((i - 1u) % 2u), scan.newest);   /* 0,1,0,1,... */
    }
    TEST_ASSERT_EQUAL_UINT(3, fake_flash_erase_count(0));
    TEST_ASSERT_EQUAL_UINT(3, fake_flash_erase_count(1));
}

static void save_two(void)
{
    uint32_t seq;
    cal_coeffs_t a = coeffs(111.0), b = coeffs(122.0);
    TEST_ASSERT_EQUAL(E_OK, calib_save(&a, CAL_M_ALL, 1, NULL, &seq));
    TEST_ASSERT_EQUAL(E_OK, calib_save(&b, CAL_M_ALL, 2, NULL, &seq));
}

static double loaded_r_s(uint32_t *seq)
{
    cal_coeffs_t c;
    calib_scan_t scan;
    (void)calib_load(&c, &scan, seq, NULL);
    return c.r_s;
}

static void test_torn_erase_keeps_newest(void)
{
    save_two();                                   /* newest = seq 2 in sector 1 */
    for (size_t words = 0; words < 40; words += 7) {
        fake_flash_tear_erase(words, (words % 2) != 0);
        uint32_t seq;
        cal_coeffs_t c = coeffs(133.0);
        TEST_ASSERT_EQUAL(E_FLASH, calib_save(&c, CAL_M_ALL, 3, NULL, &seq));
        fake_flash_power_cycle();
        uint32_t s;
        TEST_ASSERT_EQUAL_DOUBLE(122.0, loaded_r_s(&s));
        TEST_ASSERT_EQUAL_UINT32(2, s);
    }
}

static void test_torn_program_keeps_newest(void)
{
    save_two();
    for (size_t words = 0; words < 32; words++) {
        fake_flash_tear_program_after(words, (words % 3) == 0);
        uint32_t seq;
        cal_coeffs_t c = coeffs(144.0);
        TEST_ASSERT_EQUAL(E_FLASH, calib_save(&c, CAL_M_ALL, 3, NULL, &seq));
        fake_flash_power_cycle();
        uint32_t s;
        TEST_ASSERT_EQUAL_DOUBLE(122.0, loaded_r_s(&s));
        TEST_ASSERT_EQUAL_UINT32(2, s);
    }
    /* and a clean save afterwards still works */
    uint32_t seq;
    cal_coeffs_t c = coeffs(155.0);
    TEST_ASSERT_EQUAL(E_OK, calib_save(&c, CAL_M_ALL, 4, NULL, &seq));
    TEST_ASSERT_EQUAL_UINT32(3, seq);
}

static void test_silent_erase_and_refusals(void)
{
    save_two();
    uint32_t seq;
    cal_coeffs_t c = coeffs(166.0);
    fake_flash_silent_next_erase();               /* claims success, erases nothing */
    TEST_ASSERT_EQUAL(E_FLASH, calib_save(&c, CAL_M_ALL, 3, NULL, &seq));
    fake_flash_fail_next_erase();
    TEST_ASSERT_EQUAL(E_FLASH, calib_save(&c, CAL_M_ALL, 3, NULL, &seq));
    fake_flash_fail_program_after(5);
    TEST_ASSERT_EQUAL(E_FLASH, calib_save(&c, CAL_M_ALL, 3, NULL, &seq));
    uint32_t s;
    TEST_ASSERT_EQUAL_DOUBLE(122.0, loaded_r_s(&s));
    cal_coeffs_t bad = coeffs(500.0);
    TEST_ASSERT_EQUAL(E_RANGE, calib_save(&bad, CAL_M_ALL, 3, NULL, &seq));
}

static void test_other_mcu_record_ignored(void)
{
    save_two();
    fake_flash_set_uid(1, 2, 3);                  /* board moved to another MCU */
    cal_coeffs_t c;
    calib_scan_t scan;
    uint32_t seq;
    TEST_ASSERT_FALSE(calib_load(&c, &scan, &seq, NULL));
    TEST_ASSERT_EQUAL(CALREC_WRONG_UID, scan.status[0]);
    TEST_ASSERT_EQUAL(CALREC_WRONG_UID, scan.status[1]);
}

static void test_corrupt_newest_falls_back_to_older(void)
{
    save_two();
    fake_flash_mem(1)[40] ^= 0x01;                /* sector 1 holds seq 2 */
    uint32_t s;
    TEST_ASSERT_EQUAL_DOUBLE(111.0, loaded_r_s(&s));
    TEST_ASSERT_EQUAL_UINT32(1, s);
    /* the next save goes to the sector NOT holding the newest valid record */
    uint32_t seq;
    cal_coeffs_t c = coeffs(177.0);
    TEST_ASSERT_EQUAL(E_OK, calib_save(&c, CAL_M_ALL, 5, NULL, &seq));
    TEST_ASSERT_EQUAL_UINT32(2, seq);
    TEST_ASSERT_EQUAL_DOUBLE(177.0, loaded_r_s(&s));
}

static void test_small_sector_refused(void)
{
    fake_flash_set_sector_size(64);
    uint32_t seq;
    cal_coeffs_t c = coeffs(120.0);
    TEST_ASSERT_EQUAL(E_FLASH, calib_save(&c, CAL_M_ALL, 1, NULL, &seq));
}

/* The c_dac window holds the DAC8552 worst case (zero-code error 12 mV, gain
 * error 0.2 % FSR at mid-scale, half of B's 0.5 % FSR full-scale error: 544
 * codes; about 580 with the divider and difference amplifier), while a
 * dropped or extra digit is still refused. */
static void test_c_dac_window_holds_the_dac8552_worst_case(void)
{
    cal_coeffs_t c;
    const double lsb_v = 2.5 / 65536.0;
    double shift = (12e-3 + 0.002 * 2.5 / 2.0 + 0.005 * 2.5 / 2.0) / lsb_v;
    TEST_ASSERT_DOUBLE_WITHIN(1.0, 544.0, shift);
    const double ok[] = { 32767.5 + 580.0, 32767.5 - 580.0 };
    for (size_t i = 0; i < 2; i++) {
        calib_nominal(&c);
        c.c_dac = ok[i];
        TEST_ASSERT_TRUE(calib_plausible(&c));
    }
    const double typo[] = { 3276.75, 327675.0, 23767.5, 42767.5 };
    for (size_t i = 0; i < 4; i++) {
        calib_nominal(&c);
        c.c_dac = typo[i];
        TEST_ASSERT_FALSE(calib_plausible(&c));
    }
}

/* A record of the format before the mask existed (field 0), stored in
 * sector 0 of an otherwise erased flash; returns the mask calib_load gives. */
static uint32_t load_legacy(const cal_coeffs_t *c, uint32_t seq)
{
    cal_record_t r;
    uint32_t got_seq, m = 0xFFu;
    cal_coeffs_t back;
    calib_scan_t scan;
    calib_build_record(&r, c, CAL_M_ALL, seq, k_uid, 0, NULL);
    r.measured = 0u;
    r.crc = crc32_ieee(&r, offsetof(cal_record_t, crc));
    TEST_ASSERT_EQUAL(CALREC_VALID, calib_check(&r, k_uid));
    fake_flash_reset();
    memcpy(fake_flash_mem(0), &r, sizeof r);
    TEST_ASSERT_TRUE(calib_load(&back, &scan, &got_seq, &m));
    TEST_ASSERT_EQUAL_UINT32(seq, got_seq);
    TEST_ASSERT_EQUAL_MEMORY(c, &back, sizeof back);
    return m;
}

/* Which coefficients were measured survives a save and a load. In a record
 * written before the mask existed (field 0) a coefficient counts as measured
 * unless it is bit-equal to the nominal one: the bring-up record (i_plateau
 * alone) must not load as a full calibration. */
static void test_measured_mask_round_trip_and_legacy_records(void)
{
    uint32_t seq, m = 0xFFu;
    cal_coeffs_t c = coeffs(118.0), back;
    calib_scan_t scan;
    TEST_ASSERT_FALSE(calib_load(&back, &scan, &seq, &m));
    TEST_ASSERT_EQUAL_HEX32(0u, m);                         /* nominal */
    TEST_ASSERT_EQUAL(E_OK, calib_save(&c, CAL_M_I_PLATEAU, 1, NULL, &seq));
    TEST_ASSERT_TRUE(calib_load(&back, &scan, &seq, &m));
    TEST_ASSERT_EQUAL_HEX32(CAL_M_I_PLATEAU, m);
    TEST_ASSERT_EQUAL(E_OK, calib_save(&c, CAL_M_CORE | CAL_M_I_PLATEAU | 0xF00u, 2, NULL, &seq));
    TEST_ASSERT_TRUE(calib_load(&back, &scan, &seq, &m));
    TEST_ASSERT_EQUAL_HEX32(CAL_M_CORE | CAL_M_I_PLATEAU, m);   /* only CAL_M_ALL bits */
    /* records of the earlier format: measured (then reserved0) = 0 */
    cal_coeffs_t l;
    calib_nominal(&l);
    l.i_plateau = 0.045;                                   /* bring-up record */
    TEST_ASSERT_EQUAL_HEX32(CAL_M_I_PLATEAU, load_legacy(&l, 3));
    TEST_ASSERT_EQUAL_HEX32(CAL_M_R_S, load_legacy(&c, 4)); /* r_s alone */
    l.g_dac *= 1.0004;                                     /* a full calibration */
    l.c_dac += 12.3;
    l.g_v *= 0.9993;
    l.g_i *= 1.0011;
    l.r_s = 118.7;
    l.r_top = 104.2;
    TEST_ASSERT_EQUAL_HEX32(CAL_M_ALL, load_legacy(&l, 5));
    calib_nominal(&l);                                     /* nominal, as saved */
    TEST_ASSERT_EQUAL_HEX32(0u, load_legacy(&l, 6));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_record_layout_and_crc);
    RUN_TEST(test_check_statuses);
    RUN_TEST(test_plausibility_window);
    RUN_TEST(test_c_dac_window_holds_the_dac8552_worst_case);
    RUN_TEST(test_measured_mask_round_trip_and_legacy_records);
    RUN_TEST(test_empty_flash_gives_nominal);
    RUN_TEST(test_alternating_sectors_and_sequence);
    RUN_TEST(test_torn_erase_keeps_newest);
    RUN_TEST(test_torn_program_keeps_newest);
    RUN_TEST(test_silent_erase_and_refusals);
    RUN_TEST(test_other_mcu_record_ignored);
    RUN_TEST(test_corrupt_newest_falls_back_to_older);
    RUN_TEST(test_small_sector_refused);
    return UNITY_END();
}
