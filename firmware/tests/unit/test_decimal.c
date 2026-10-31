/*
 * test_decimal.c — unit tests of core/decimal.c: the strict number grammar,
 * exactness at a target scale, int64 overflow safety, limits, and fixed-point
 * printing (including INT64_MIN and values between -1 and 0). Randomised
 * round trips use a fixed-seed xorshift generator and an snprintf oracle.
 */
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "decimal.h"
#include "unity.h"

void setUp(void) {}
void tearDown(void) {}

/* ---- helpers ----------------------------------------------------------------- */

static uint64_t s_rng = 0x9E3779B97F4A7C15ULL;

static uint64_t rnd(void)
{
    /* xorshift64* */
    s_rng ^= s_rng >> 12;
    s_rng ^= s_rng << 25;
    s_rng ^= s_rng >> 27;
    return s_rng * 0x2545F4914F6CDD1DULL;
}

static uint32_t rnd_below(uint32_t n)
{
    return (uint32_t)(rnd() % n);
}

static dec_status_t parse(const char *s, int scale, int64_t *out)
{
    return dec_parse_scaled(s, strlen(s), scale, INT64_MIN, INT64_MAX, out);
}

typedef struct {
    const char  *text;
    int          scale;
    dec_status_t status;
    int64_t      value;     /* checked only for DEC_OK */
} parse_case_t;

static void run_cases(const parse_case_t *cases, size_t n)
{
    char msg[160];
    for (size_t i = 0; i < n; i++) {
        int64_t v = 0x5A5A5A5A;
        dec_status_t st = parse(cases[i].text, cases[i].scale, &v);
        snprintf(msg, sizeof msg, "input \"%.60s\" scale %d", cases[i].text, cases[i].scale);
        TEST_ASSERT_EQUAL_INT_MESSAGE(cases[i].status, st, msg);
        if (cases[i].status == DEC_OK) {
            TEST_ASSERT_EQUAL_INT64_MESSAGE(cases[i].value, v, msg);
        } else {
            TEST_ASSERT_EQUAL_INT64_MESSAGE(0x5A5A5A5A, v, msg);  /* untouched */
        }
    }
}

/* ---- parsing: accepted forms ------------------------------------------------- */

static void test_parse_basic_forms(void)
{
    static const parse_case_t cases[] = {
        { "1.5", 6, DEC_OK, 1500000 },
        { "+10", 6, DEC_OK, 10000000 },
        { "-2.5", 6, DEC_OK, -2500000 },
        { "0.000125", 6, DEC_OK, 125 },
        { ".5", 6, DEC_OK, 500000 },
        { "-.5", 6, DEC_OK, -500000 },
        { "+.000001", 6, DEC_OK, 1 },
        { "1e-3", 6, DEC_OK, 1000 },
        { "1E-3", 6, DEC_OK, 1000 },
        { "2.5E+1", 0, DEC_OK, 25 },
        { "2.5e1", 0, DEC_OK, 25 },
        { "250e-1", 0, DEC_OK, 25 },
        { ".25e2", 0, DEC_OK, 25 },
        { "-0", 6, DEC_OK, 0 },
        { "0.0", 0, DEC_OK, 0 },
        { "0e99999999999999999999", 6, DEC_OK, 0 },
        { "0.000e-999", 0, DEC_OK, 0 },
        { "1.0000000", 6, DEC_OK, 1000000 },         /* trailing zeros are exact */
        { "1.000000000000000000000000000000", 0, DEC_OK, 1 },
        { "00000000000000000000000000000012", 0, DEC_OK, 12 },
        { "2000", -3, DEC_OK, 2 },                    /* negative scale          */
        { "1e3", -3, DEC_OK, 1 },
        { "9223372036854775807", 0, DEC_OK, INT64_MAX },
        { "-9223372036854775808", 0, DEC_OK, INT64_MIN },
        { "922337203685477.5807", 4, DEC_OK, INT64_MAX },
        { "-92233.72036854775808e14", 0, DEC_OK, INT64_MIN },
        { "9.223372036854775807e18", 0, DEC_OK, INT64_MAX },
        { "1e18", 0, DEC_OK, 1000000000000000000LL },
        { "0.1", 1, DEC_OK, 1 },
        { "3750", 1, DEC_OK, 37500 },                 /* data rate in 0.1 SPS    */
        { "0.05", 9, DEC_OK, 50000000 },              /* current in nA           */
        { "100000000000000000000e-20", 0, DEC_OK, 1 },/* long significand, ok    */
    };
    run_cases(cases, sizeof cases / sizeof cases[0]);
}

static void test_parse_inexact(void)
{
    static const parse_case_t cases[] = {
        { "1.0000005", 6, DEC_INEXACT, 0 },
        { "1e-7", 6, DEC_INEXACT, 0 },
        { "0.0000001", 6, DEC_INEXACT, 0 },
        { "1.5", 0, DEC_INEXACT, 0 },
        { "-1.5", 0, DEC_INEXACT, 0 },
        { "1", -1, DEC_INEXACT, 0 },
        { "1500", -3, DEC_INEXACT, 0 },
        { "1e-99999999999999999999", 6, DEC_INEXACT, 0 },
        { "0.00000000000000000000000000000000000001", 6, DEC_INEXACT, 0 },
        { "1.00000000000000000000000000000000000001", 6, DEC_INEXACT, 0 },
    };
    run_cases(cases, sizeof cases / sizeof cases[0]);
}

static void test_parse_overflow_is_range_never_ub(void)
{
    static const parse_case_t cases[] = {
        { "9223372036854775808", 0, DEC_RANGE, 0 },
        { "-9223372036854775809", 0, DEC_RANGE, 0 },
        { "18446744073709551616", 0, DEC_RANGE, 0 },   /* 2^64 */
        { "99999999999999999999", 0, DEC_RANGE, 0 },
        { "1e19", 0, DEC_RANGE, 0 },
        { "1e99999999", 6, DEC_RANGE, 0 },
        { "1e99999999999999999999999", 6, DEC_RANGE, 0 },
        { "-1e99999999", 6, DEC_RANGE, 0 },
        { "10", 18, DEC_RANGE, 0 },                    /* 1e19 */
        { "9.3", 18, DEC_RANGE, 0 },
        { "1", 2147483647, DEC_RANGE, 0 },
        { "1e-5", 2147483647, DEC_RANGE, 0 },
        { "12345678901234567890123.5", 0, DEC_RANGE, 0 },
    };
    run_cases(cases, sizeof cases / sizeof cases[0]);

    int64_t v = 0;
    TEST_ASSERT_EQUAL_INT(DEC_INEXACT, parse("1", -2147483647 - 1, &v));
    TEST_ASSERT_EQUAL_INT(DEC_OK, parse("0", -2147483647 - 1, &v));
    TEST_ASSERT_EQUAL_INT64(0, v);
}

static void test_parse_hostile_syntax(void)
{
    static const char *const bad[] = {
        "", "-", "+", ".", "-.", "+.", "e", "e5", ".e1", "1e", "1e+", "1e-", "1E",
        "--1", "+-1", "-+1", "++1", "1..2", "1.2.3", "..1", "1.", "-1.", "1.e5",
        "0x10", "0X10", "x10", "nan", "NaN", "-nan", "inf", "-inf", "Infinity",
        " 1", "1 ", "1\t", "\t1", "1\n", "1,5", "1_000", "1e1.5", "1e--1",
        "1e+-1", "1ee1", "1e1e1", "\xd9\xa1", "1d3", "1f", "0b101", "12a", "a12", "+e1",
        "1.5V", "1 000", "- 1", "1e 1",
    };
    char msg[64];
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        int64_t v = 77;
        snprintf(msg, sizeof msg, "input \"%s\"", bad[i]);
        TEST_ASSERT_EQUAL_INT_MESSAGE(DEC_SYNTAX, parse(bad[i], 6, &v), msg);
        TEST_ASSERT_EQUAL_INT64_MESSAGE(77, v, msg);
    }
    /* NUL inside the token, and NULL pointers. */
    int64_t v = 0;
    TEST_ASSERT_EQUAL_INT(DEC_SYNTAX, dec_parse_scaled("1\0002", 3, 0, INT64_MIN, INT64_MAX, &v));
    TEST_ASSERT_EQUAL_INT(DEC_SYNTAX, dec_parse_scaled(NULL, 1, 0, INT64_MIN, INT64_MAX, &v));
    TEST_ASSERT_EQUAL_INT(DEC_SYNTAX, dec_parse_scaled("1", 1, 0, INT64_MIN, INT64_MAX, NULL));
}

static void test_parse_respects_len(void)
{
    int64_t v = 0;
    /* Only s[0..len) is the token; the rest of the line must not be read. */
    TEST_ASSERT_EQUAL_INT(DEC_OK, dec_parse_scaled("12345", 2, 0, INT64_MIN, INT64_MAX, &v));
    TEST_ASSERT_EQUAL_INT64(12, v);
    TEST_ASSERT_EQUAL_INT(DEC_OK, dec_parse_scaled("1.5 STEP=0.1", 3, 6, INT64_MIN, INT64_MAX, &v));
    TEST_ASSERT_EQUAL_INT64(1500000, v);
    TEST_ASSERT_EQUAL_INT(DEC_SYNTAX, dec_parse_scaled("1e5", 2, 0, INT64_MIN, INT64_MAX, &v));
    TEST_ASSERT_EQUAL_INT(DEC_SYNTAX, dec_parse_scaled("7", 0, 0, INT64_MIN, INT64_MAX, &v));

    /* A buffer with no terminator at all, exactly len bytes long. */
    char raw[4] = { '-', '1', '.', '5' };
    TEST_ASSERT_EQUAL_INT(DEC_OK, dec_parse_scaled(raw, sizeof raw, 1, INT64_MIN, INT64_MAX, &v));
    TEST_ASSERT_EQUAL_INT64(-15, v);
}

static void test_parse_very_long_digit_strings(void)
{
    static char buf[4096];
    int64_t v = 0;

    /* 4000 leading zeros, then 1. */
    memset(buf, '0', 4000);
    strcpy(buf + 4000, "1");
    TEST_ASSERT_EQUAL_INT(DEC_OK, parse(buf, 6, &v));
    TEST_ASSERT_EQUAL_INT64(1000000, v);

    /* 1 followed by 4000 zeros: out of range. */
    buf[0] = '1';
    memset(buf + 1, '0', 4000);
    buf[4001] = '\0';
    TEST_ASSERT_EQUAL_INT(DEC_RANGE, parse(buf, 0, &v));

    /* ... unless an exponent brings it back. */
    strcpy(buf + 4001, "e-4000");
    TEST_ASSERT_EQUAL_INT(DEC_OK, parse(buf, 0, &v));
    TEST_ASSERT_EQUAL_INT64(1, v);

    /* 0.000...0001 with 4000 zeros: inexact at any sane scale. */
    strcpy(buf, "0.");
    memset(buf + 2, '0', 4000);
    strcpy(buf + 4002, "1");
    TEST_ASSERT_EQUAL_INT(DEC_INEXACT, parse(buf, 6, &v));
    TEST_ASSERT_EQUAL_INT(DEC_OK, parse(buf, 4001, &v));
    TEST_ASSERT_EQUAL_INT64(1, v);

    /* 1.000...000 with 4000 zeros: exactly 1. */
    strcpy(buf, "1.");
    memset(buf + 2, '0', 4000);
    buf[4002] = '\0';
    TEST_ASSERT_EQUAL_INT(DEC_OK, parse(buf, 6, &v));
    TEST_ASSERT_EQUAL_INT64(1000000, v);

    /* 4000 significant digits. */
    for (size_t i = 0; i < 4000; i++) {
        buf[i] = (char)('1' + (i % 9));
    }
    buf[4000] = '\0';
    TEST_ASSERT_EQUAL_INT(DEC_RANGE, parse(buf, 0, &v));
    buf[1] = '.';
    TEST_ASSERT_EQUAL_INT(DEC_INEXACT, parse(buf, 6, &v));

    /* Huge exponent digit strings saturate without overflow. */
    strcpy(buf, "1e-");
    memset(buf + 3, '9', 3000);
    buf[3003] = '\0';
    TEST_ASSERT_EQUAL_INT(DEC_INEXACT, parse(buf, 6, &v));
    buf[2] = '+';
    TEST_ASSERT_EQUAL_INT(DEC_RANGE, parse(buf, 6, &v));
    buf[0] = '0';
    TEST_ASSERT_EQUAL_INT(DEC_OK, parse(buf, 6, &v));
    TEST_ASSERT_EQUAL_INT64(0, v);
}

static void test_parse_limits_inclusive(void)
{
    int64_t v = 0;
    TEST_ASSERT_EQUAL_INT(DEC_OK, dec_parse_scaled("10", 2, 6, -10000000, 10000000, &v));
    TEST_ASSERT_EQUAL_INT64(10000000, v);
    TEST_ASSERT_EQUAL_INT(DEC_OK, dec_parse_scaled("-10", 3, 6, -10000000, 10000000, &v));
    TEST_ASSERT_EQUAL_INT64(-10000000, v);
    TEST_ASSERT_EQUAL_INT(DEC_RANGE, dec_parse_scaled("10.000001", 9, 6, -10000000, 10000000, &v));
    TEST_ASSERT_EQUAL_INT(DEC_RANGE, dec_parse_scaled("-10.000001", 10, 6, -10000000, 10000000, &v));
    TEST_ASSERT_EQUAL_INT(DEC_RANGE, dec_parse_scaled("0", 1, 6, 1, 5, &v));
    /* Inexact wins over the limits: the value is not a number at that scale. */
    TEST_ASSERT_EQUAL_INT(DEC_INEXACT, dec_parse_scaled("11.0000001", 10, 6, -10000000, 10000000, &v));
    /* Empty interval rejects everything. */
    TEST_ASSERT_EQUAL_INT(DEC_RANGE, dec_parse_scaled("3", 1, 0, 5, 4, &v));
}

static void test_parse_u32(void)
{
    uint32_t v = 99;
    TEST_ASSERT_EQUAL_INT(DEC_OK, dec_parse_u32("0", 1, 0, UINT32_MAX, &v));
    TEST_ASSERT_EQUAL_UINT32(0, v);
    TEST_ASSERT_EQUAL_INT(DEC_OK, dec_parse_u32("4294967295", 10, 0, UINT32_MAX, &v));
    TEST_ASSERT_EQUAL_UINT32(UINT32_MAX, v);
    TEST_ASSERT_EQUAL_INT(DEC_OK, dec_parse_u32("00042", 5, 0, UINT32_MAX, &v));
    TEST_ASSERT_EQUAL_UINT32(42, v);
    TEST_ASSERT_EQUAL_INT(DEC_RANGE, dec_parse_u32("4294967296", 10, 0, UINT32_MAX, &v));
    TEST_ASSERT_EQUAL_INT(DEC_RANGE, dec_parse_u32("99999999999999999999999", 23, 0, UINT32_MAX, &v));
    TEST_ASSERT_EQUAL_INT(DEC_RANGE, dec_parse_u32("257", 3, 1, 256, &v));
    TEST_ASSERT_EQUAL_INT(DEC_RANGE, dec_parse_u32("0", 1, 1, 256, &v));
    TEST_ASSERT_EQUAL_INT(DEC_OK, dec_parse_u32("256", 3, 1, 256, &v));
    TEST_ASSERT_EQUAL_UINT32(256, v);

    static const char *const bad[] = {
        "", "-1", "+1", "1.0", "1e3", " 1", "1 ", "0x1", "a", "1-",
    };
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        v = 7;
        TEST_ASSERT_EQUAL_INT_MESSAGE(DEC_SYNTAX, dec_parse_u32(bad[i], strlen(bad[i]), 0, UINT32_MAX, &v), bad[i]);
        TEST_ASSERT_EQUAL_UINT32(7, v);
    }
    /* Syntax is reported even after an overflowing prefix. */
    TEST_ASSERT_EQUAL_INT(DEC_SYNTAX, dec_parse_u32("99999999999999999999x", 21, 0, UINT32_MAX, &v));
    TEST_ASSERT_EQUAL_INT(DEC_OK, dec_parse_u32("123456", 3, 0, UINT32_MAX, &v));
    TEST_ASSERT_EQUAL_UINT32(123, v);
}

/* ---- formatting -------------------------------------------------------------- */

static void check_format(int64_t value, unsigned frac, const char *expected)
{
    char buf[80];
    size_t n = dec_format(buf, sizeof buf, value, frac);
    TEST_ASSERT_EQUAL_STRING(expected, buf);
    TEST_ASSERT_EQUAL_size_t(strlen(expected), n);
}

static void test_format_examples(void)
{
    check_format(1500000, 6, "1.500000");
    check_format(-5, 3, "-0.005");
    check_format(-999, 3, "-0.999");
    check_format(-1000, 3, "-1.000");
    check_format(5, 3, "0.005");
    check_format(0, 3, "0.000");
    check_format(0, 0, "0");
    check_format(-1, 0, "-1");
    check_format(153, 6, "0.000153");
    check_format(-10000000, 6, "-10.000000");
    check_format(INT64_MAX, 0, "9223372036854775807");
    check_format(INT64_MIN, 0, "-9223372036854775808");
    check_format(INT64_MIN, 6, "-9223372036854.775808");
    check_format(INT64_MIN, 19, "-0.9223372036854775808");
    check_format(INT64_MIN, 25, "-0.0000009223372036854775808");
    check_format(INT64_MAX, 18, "9.223372036854775807");
    check_format(1, 10, "0.0000000001");
}

static void test_format_capacity(void)
{
    char buf[16];
    /* "1.500000" is 8 characters: needs cap 9. */
    memset(buf, '#', sizeof buf);
    TEST_ASSERT_EQUAL_size_t(8, dec_format(buf, 9, 1500000, 6));
    TEST_ASSERT_EQUAL_STRING("1.500000", buf);
    TEST_ASSERT_EQUAL_CHAR('#', buf[9]);

    memset(buf, '#', sizeof buf);
    TEST_ASSERT_EQUAL_size_t(0, dec_format(buf, 8, 1500000, 6));
    TEST_ASSERT_EQUAL_CHAR('\0', buf[0]);
    TEST_ASSERT_EQUAL_CHAR('#', buf[1]);
    TEST_ASSERT_EQUAL_CHAR('#', buf[8]);

    memset(buf, '#', sizeof buf);
    TEST_ASSERT_EQUAL_size_t(0, dec_format(buf, 0, 1, 0));
    TEST_ASSERT_EQUAL_CHAR('#', buf[0]);
    TEST_ASSERT_EQUAL_size_t(0, dec_format(NULL, 10, 1, 0));

    /* Sign counts: "-0.005" is 6 characters. */
    TEST_ASSERT_EQUAL_size_t(0, dec_format(buf, 6, -5, 3));
    TEST_ASSERT_EQUAL_size_t(6, dec_format(buf, 7, -5, 3));
    TEST_ASSERT_EQUAL_STRING("-0.005", buf);

    /* Absurd frac cannot overflow the length computation. */
    TEST_ASSERT_EQUAL_size_t(0, dec_format(buf, sizeof buf, 1, 4294967295u));
    TEST_ASSERT_EQUAL_CHAR('\0', buf[0]);
}

static void test_format_hex(void)
{
    char buf[16];
    TEST_ASSERT_EQUAL_size_t(4, dec_format_hex(buf, sizeof buf, 0x1A2Bu, 4));
    TEST_ASSERT_EQUAL_STRING("1A2B", buf);
    TEST_ASSERT_EQUAL_size_t(4, dec_format_hex(buf, sizeof buf, 0x80u, 4));
    TEST_ASSERT_EQUAL_STRING("0080", buf);
    TEST_ASSERT_EQUAL_size_t(8, dec_format_hex(buf, sizeof buf, 0xFFFFFFFFu, 8));
    TEST_ASSERT_EQUAL_STRING("FFFFFFFF", buf);
    TEST_ASSERT_EQUAL_size_t(0, dec_format_hex(buf, 0, 0, 24));          /* cap 0 */
    TEST_ASSERT_EQUAL_size_t(5, dec_format_hex(buf, sizeof buf, 0x12345u, 4)); /* like %04X */
    TEST_ASSERT_EQUAL_STRING("12345", buf);
    TEST_ASSERT_EQUAL_size_t(1, dec_format_hex(buf, sizeof buf, 0, 0));
    TEST_ASSERT_EQUAL_STRING("0", buf);
    TEST_ASSERT_EQUAL_size_t(0, dec_format_hex(buf, 4, 0xABCDu, 4));
    TEST_ASSERT_EQUAL_CHAR('\0', buf[0]);
    TEST_ASSERT_EQUAL_size_t(0, dec_format_hex(buf, sizeof buf, 1, 4294967295u));

    /* Against snprintf over random values and widths. */
    char want[32];
    for (int i = 0; i < 20000; i++) {
        uint32_t v = (uint32_t)rnd() >> rnd_below(32);
        unsigned w = rnd_below(12);
        snprintf(want, sizeof want, "%0*" PRIX32, (int)w, v);
        TEST_ASSERT_EQUAL_size_t(strlen(want), dec_format_hex(buf, sizeof buf, v, w));
        TEST_ASSERT_EQUAL_STRING(want, buf);
    }
}

/* ---- randomised round trips ---------------------------------------------------- */

static int64_t random_value(void)
{
    /* Mix of magnitudes: tiny, typical (uV-scale setpoints), huge, extremes. */
    switch (rnd_below(6)) {
    case 0:  return (int64_t)rnd_below(2001) - 1000;
    case 1:  return (int64_t)rnd_below(20000001) - 10000000;
    case 2:  return (int64_t)(rnd() >> rnd_below(64));
    case 3:  return -(int64_t)(rnd() >> (1 + rnd_below(63)));
    case 4:  return (rnd() & 1u) ? INT64_MIN + (int64_t)rnd_below(3) : INT64_MAX - (int64_t)rnd_below(3);
    default: return (int64_t)rnd();
    }
}

/* snprintf oracle for dec_format. */
static void oracle_format(char *out, size_t cap, int64_t value, unsigned frac)
{
    uint64_t mag = value < 0 ? (uint64_t)0 - (uint64_t)value : (uint64_t)value;
    const char *sign = value < 0 ? "-" : "";
    if (frac == 0) {
        snprintf(out, cap, "%s%" PRIu64, sign, mag);
        return;
    }
    if (frac >= 19) {
        snprintf(out, cap, "%s0.%0*" PRIu64, sign, (int)frac, mag);
        return;
    }
    uint64_t p = 1;
    for (unsigned i = 0; i < frac; i++) {
        p *= 10u;
    }
    snprintf(out, cap, "%s%" PRIu64 ".%0*" PRIu64, sign, mag / p, (int)frac, mag % p);
}

/* Rewrite a canonical "[-]III.FFF" into an equivalent token: move the point,
 * compensate with an exponent, pad with zeros, maybe add a '+'. */
static void rewrite_equivalent(const char *canon, unsigned frac, char *out, size_t cap)
{
    char digits[64];
    size_t nd = 0;
    bool neg = canon[0] == '-';
    for (const char *p = canon; *p; p++) {
        if (*p >= '0' && *p <= '9') {
            digits[nd++] = *p;
        }
    }
    unsigned lead = rnd_below(4);          /* extra leading zeros  */
    unsigned trail = rnd_below(4);         /* extra trailing zeros */
    size_t total = lead + nd + trail;
    size_t point = rnd_below((uint32_t)total + 1u);  /* digits before the point */
    /* value = D * 10^(e - (total - point) - trail... ) ; solve for e:
     * the digit string is lead zeros + digits + trail zeros, its integer value
     * is int(digits) * 10^trail, and the canonical value is
     * int(digits) * 10^-frac. */
    long e = (long)(total - point) - (long)trail - (long)frac;

    size_t o = 0;
    if (neg) {
        out[o++] = '-';
    } else if (rnd() & 1u) {
        out[o++] = '+';
    }
    for (size_t i = 0; i < total; i++) {
        if (i == point && point != 0) {
            out[o++] = '.';
        }
        if (i == 0 && point == 0) {
            out[o++] = '.';
        }
        char ch = (i < lead) ? '0' : (i < lead + nd) ? digits[i - lead] : '0';
        out[o++] = ch;
    }
    /* point == total: no point at all (a point needs digits after it). */
    if (e != 0 || (rnd() & 1u)) {
        snprintf(out + o, cap - o, "%c%s%ld", (rnd() & 1u) ? 'e' : 'E',
                 (e >= 0 && (rnd() & 1u)) ? "+" : "", e);
    } else {
        out[o] = '\0';
    }
}

static void test_random_round_trips(void)
{
    char buf[80], want[80], alt[160];
    for (int iter = 0; iter < 200000; iter++) {
        int64_t v = random_value();
        unsigned frac = rnd_below(22);
        size_t n = dec_format(buf, sizeof buf, v, frac);
        oracle_format(want, sizeof want, v, frac);
        TEST_ASSERT_EQUAL_STRING(want, buf);
        TEST_ASSERT_EQUAL_size_t(strlen(want), n);

        /* format -> parse at the same scale is the identity */
        int64_t back = 0;
        TEST_ASSERT_EQUAL_INT_MESSAGE(DEC_OK, dec_parse_scaled(buf, n, (int)frac, INT64_MIN, INT64_MAX, &back), buf);
        TEST_ASSERT_EQUAL_INT64(v, back);

        /* an equivalent spelling with an exponent parses to the same value */
        rewrite_equivalent(buf, frac, alt, sizeof alt);
        back = 0;
        TEST_ASSERT_EQUAL_INT_MESSAGE(DEC_OK, parse(alt, (int)frac, &back), alt);
        TEST_ASSERT_EQUAL_INT64_MESSAGE(v, back, alt);

        /* one scale finer multiplies by ten, or is out of range */
        int64_t ten;
        dec_status_t st = parse(alt, (int)frac + 1, &back);
        if (__builtin_mul_overflow(v, (int64_t)10, &ten)) {
            TEST_ASSERT_EQUAL_INT_MESSAGE(DEC_RANGE, st, alt);
        } else {
            TEST_ASSERT_EQUAL_INT_MESSAGE(DEC_OK, st, alt);
            TEST_ASSERT_EQUAL_INT64(ten, back);
        }

        /* one scale coarser is exact iff the last digit is zero */
        if (frac > 0) {
            st = parse(alt, (int)frac - 1, &back);
            if (v % 10 == 0) {
                TEST_ASSERT_EQUAL_INT_MESSAGE(DEC_OK, st, alt);
                TEST_ASSERT_EQUAL_INT64(v / 10, back);
            } else {
                TEST_ASSERT_EQUAL_INT_MESSAGE(DEC_INEXACT, st, alt);
            }
        }

        /* the limits are honoured at the exact boundary */
        if (v != INT64_MIN) {
            TEST_ASSERT_EQUAL_INT(DEC_RANGE, dec_parse_scaled(buf, n, (int)frac, INT64_MIN, v - 1, &back));
        }
        TEST_ASSERT_EQUAL_INT(DEC_OK, dec_parse_scaled(buf, n, (int)frac, v, v, &back));
    }
}

static void test_random_garbage_never_crashes(void)
{
    /* Random tokens over the grammar's alphabet: whatever the verdict, an
     * accepted value must re-format to a token that parses to itself. */
    static const char alphabet[] = "0123456789+-.eE x";
    char tok[40], buf[80];
    int accepted = 0;
    for (int iter = 0; iter < 300000; iter++) {
        size_t len = rnd_below(sizeof tok);
        for (size_t i = 0; i < len; i++) {
            tok[i] = alphabet[rnd_below(sizeof alphabet - 1u)];
        }
        int scale = (int)rnd_below(13);
        int64_t v = 0;
        dec_status_t st = dec_parse_scaled(tok, len, scale, INT64_MIN, INT64_MAX, &v);
        TEST_ASSERT_TRUE(st == DEC_OK || st == DEC_SYNTAX || st == DEC_INEXACT || st == DEC_RANGE);
        if (st == DEC_OK) {
            accepted++;
            size_t n = dec_format(buf, sizeof buf, v, (unsigned)scale);
            int64_t back = 0;
            TEST_ASSERT_EQUAL_INT(DEC_OK, dec_parse_scaled(buf, n, scale, INT64_MIN, INT64_MAX, &back));
            TEST_ASSERT_EQUAL_INT64(v, back);
        }
    }
    TEST_ASSERT_GREATER_THAN(1000, accepted);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_parse_basic_forms);
    RUN_TEST(test_parse_inexact);
    RUN_TEST(test_parse_overflow_is_range_never_ub);
    RUN_TEST(test_parse_hostile_syntax);
    RUN_TEST(test_parse_respects_len);
    RUN_TEST(test_parse_very_long_digit_strings);
    RUN_TEST(test_parse_limits_inclusive);
    RUN_TEST(test_parse_u32);
    RUN_TEST(test_format_examples);
    RUN_TEST(test_format_capacity);
    RUN_TEST(test_format_hex);
    RUN_TEST(test_random_round_trips);
    RUN_TEST(test_random_garbage_never_crashes);
    return UNITY_END();
}
