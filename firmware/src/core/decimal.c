/*
 * decimal.c — strict decimal parsing into scaled integers and fixed-point
 * integer printing (contract: decimal.h, docs/firmware/protocol.md §3).
 *
 * No datasheet facts are involved. Design rules:
 *  - Parsing never overflows and never rounds. The mantissa digits are
 *    reduced to their significant part `sig` (first to last non-zero digit,
 *    at most 19 digits are ever accumulated, so uint64 cannot overflow) plus
 *    the number of trailing zeros; the result is sig * 10^E with
 *        E = trailing_zeros + exponent + scale - fractional_digits.
 *    E < 0 means a non-zero digit would fall below the target resolution
 *    (DEC_INEXACT); more than 19 integer digits is out of range (DEC_RANGE).
 *    Exponent magnitudes saturate at DEC_EXP_SAT, which is far beyond any
 *    value that could still be representable, so saturation never changes
 *    the verdict.
 *  - Order of verdicts: DEC_SYNTAX, then DEC_RANGE for values outside int64,
 *    then DEC_INEXACT, then DEC_RANGE for [min, max].
 *  - Printing works on the uint64 magnitude, so INT64_MIN is safe, and a
 *    negative value always carries its sign ("-0.005").
 */
#include "decimal.h"

#define DEC_EXP_SAT   1000000000LL  /* exponent saturation (1e9)            */
#define DEC_MAX_DIGITS 19           /* 10^19 > INT64_MAX >= 10^18             */

static const uint64_t k_pow10[DEC_MAX_DIGITS + 1] = {
    1ULL,
    10ULL,
    100ULL,
    1000ULL,
    10000ULL,
    100000ULL,
    1000000ULL,
    10000000ULL,
    100000000ULL,
    1000000000ULL,
    10000000000ULL,
    100000000000ULL,
    1000000000000ULL,
    10000000000000ULL,
    100000000000000ULL,
    1000000000000000ULL,
    10000000000000000ULL,
    100000000000000000ULL,
    1000000000000000000ULL,
    10000000000000000000ULL,
};

static bool is_digit(char ch)
{
    return ch >= '0' && ch <= '9';
}

static unsigned digit_value(char ch)
{
    return (unsigned)(ch - '0');
}

dec_status_t dec_parse_scaled(const char *s, size_t len, int scale,
                              int64_t min, int64_t max, int64_t *out)
{
    if (s == NULL || out == NULL) {
        return DEC_SYNTAX;
    }

    size_t i = 0;
    bool neg = false;
    if (i < len && (s[i] == '+' || s[i] == '-')) {
        neg = (s[i] == '-');
        i++;
    }

    /* Mantissa: digits, optional point, digits. Positions count every
     * mantissa digit (integer and fractional) from 0. */
    size_t n_digits = 0;          /* all mantissa digits                      */
    size_t n_int = 0;
    size_t n_frac = 0;
    bool   have_point = false;
    bool   have_nz = false;       /* a non-zero digit has been seen           */
    size_t first_nz = 0;          /* position of the first non-zero digit     */
    size_t last_nz = 0;           /* position of the last non-zero digit      */
    uint64_t sig = 0;             /* digits first_nz..last_nz, if <= 19 long  */
    bool   sig_long = false;      /* more than DEC_MAX_DIGITS significant     */

    while (i < len) {
        char ch = s[i];
        if (is_digit(ch)) {
            if (ch != '0') {
                if (!have_nz) {
                    have_nz = true;
                    first_nz = n_digits;
                    sig = digit_value(ch);
                } else if (!sig_long) {
                    size_t sig_len = n_digits - first_nz + 1u;
                    if (sig_len > DEC_MAX_DIGITS) {
                        sig_long = true;
                    } else {
                        /* sig has (last_nz - first_nz + 1) digits; shifting it
                         * by (n_digits - last_nz) keeps it below 10^19. */
                        sig = sig * k_pow10[n_digits - last_nz] + digit_value(ch);
                    }
                }
                last_nz = n_digits;
            }
            n_digits++;
            if (have_point) {
                n_frac++;
            } else {
                n_int++;
            }
            i++;
        } else if (ch == '.' && !have_point) {
            have_point = true;
            i++;
        } else {
            break;
        }
    }
    /* "digits [. digits]" or ". digits": a point needs digits after it, and
     * a number without a point needs digits before it. */
    if (have_point ? (n_frac == 0) : (n_int == 0)) {
        return DEC_SYNTAX;
    }

    /* Exponent. */
    int64_t exp10 = 0;
    if (i < len && (s[i] == 'e' || s[i] == 'E')) {
        i++;
        bool exp_neg = false;
        if (i < len && (s[i] == '+' || s[i] == '-')) {
            exp_neg = (s[i] == '-');
            i++;
        }
        size_t n_exp = 0;
        while (i < len && is_digit(s[i])) {
            if (exp10 < DEC_EXP_SAT) {
                exp10 = exp10 * 10 + (int64_t)digit_value(s[i]);
            }
            n_exp++;
            i++;
        }
        if (n_exp == 0) {
            return DEC_SYNTAX;
        }
        if (exp10 > DEC_EXP_SAT) {
            exp10 = DEC_EXP_SAT;
        }
        if (exp_neg) {
            exp10 = -exp10;
        }
    }
    if (i != len) {
        return DEC_SYNTAX;
    }

    int64_t value;
    if (!have_nz) {
        value = 0;
    } else {
        /* Counts are bounded by len, far below 2^62 on any real input. */
        int64_t sig_digits = (int64_t)(last_nz - first_nz) + 1;
        int64_t trailing = (int64_t)(n_digits - 1u - last_nz);
        int64_t e = trailing + exp10 + (int64_t)scale - (int64_t)n_frac;

        if (sig_digits + e > DEC_MAX_DIGITS) {
            return DEC_RANGE;           /* >= 10^19 > INT64_MAX in magnitude */
        }
        if (e < 0) {
            return DEC_INEXACT;         /* a non-zero digit below 10^-scale  */
        }
        /* sig_digits + e <= 19 implies sig_digits <= 19 (sig is exact) and
         * sig * 10^e < 10^19 < 2^64 (sig_long cannot be set here). */
        uint64_t mag = sig * k_pow10[e];
        if (neg) {
            if (mag > (uint64_t)INT64_MAX + 1u) {
                return DEC_RANGE;
            }
            value = (mag == (uint64_t)INT64_MAX + 1u) ? INT64_MIN : -(int64_t)mag;
        } else {
            if (mag > (uint64_t)INT64_MAX) {
                return DEC_RANGE;
            }
            value = (int64_t)mag;
        }
    }

    if (value < min || value > max) {
        return DEC_RANGE;
    }
    *out = value;
    return DEC_OK;
}

dec_status_t dec_parse_u32(const char *s, size_t len, uint32_t min, uint32_t max,
                           uint32_t *out)
{
    if (s == NULL || out == NULL || len == 0) {
        return DEC_SYNTAX;
    }
    uint32_t v = 0;
    bool overflow = false;
    for (size_t i = 0; i < len; i++) {
        if (!is_digit(s[i])) {
            return DEC_SYNTAX;
        }
        uint32_t d = digit_value(s[i]);
        if (!overflow) {
            if (v > (UINT32_MAX - d) / 10u) {
                overflow = true;        /* keep scanning: syntax wins */
            } else {
                v = v * 10u + d;
            }
        }
    }
    if (overflow || v < min || v > max) {
        return DEC_RANGE;
    }
    *out = v;
    return DEC_OK;
}

static size_t format_fail(char *buf, size_t cap)
{
    if (buf != NULL && cap > 0) {
        buf[0] = '\0';
    }
    return 0;
}

size_t dec_format(char *buf, size_t cap, int64_t value, unsigned frac)
{
    bool neg = value < 0;
    uint64_t mag = neg ? (uint64_t)0 - (uint64_t)value : (uint64_t)value;

    uint64_t n_sig = 1;                         /* decimal digits of mag   */
    for (uint64_t t = mag; t >= 10u; t /= 10u) {
        n_sig++;
    }
    /* At least one digit before the point: "0.005". */
    uint64_t n_dig = (n_sig > (uint64_t)frac) ? n_sig : (uint64_t)frac + 1u;
    uint64_t need = (neg ? 1u : 0u) + n_dig + (frac > 0u ? 1u : 0u);
    if (buf == NULL || need >= (uint64_t)cap) {
        return format_fail(buf, cap);
    }

    size_t pos = (size_t)need;
    buf[pos] = '\0';
    for (uint64_t k = 0; k < n_dig; k++) {
        if (frac > 0u && k == (uint64_t)frac) {
            buf[--pos] = '.';
        }
        buf[--pos] = (char)('0' + (int)(mag % 10u));
        mag /= 10u;
    }
    if (neg) {
        buf[--pos] = '-';
    }
    return (size_t)need;
}

size_t dec_format_hex(char *buf, size_t cap, uint32_t value, unsigned digits)
{
    static const char hex[16] = {
        '0', '1', '2', '3', '4', '5', '6', '7',
        '8', '9', 'A', 'B', 'C', 'D', 'E', 'F',
    };
    uint64_t n_sig = 1;                         /* hex digits of value      */
    for (uint32_t t = value; t >= 16u; t >>= 4) {
        n_sig++;
    }
    /* Like "%0*X": `digits` is a minimum, never a truncation. */
    uint64_t need = (n_sig > (uint64_t)digits) ? n_sig : (uint64_t)digits;
    if (buf == NULL || need >= (uint64_t)cap) {
        return format_fail(buf, cap);
    }
    size_t pos = (size_t)need;
    buf[pos] = '\0';
    uint32_t v = value;
    while (pos > 0) {
        buf[--pos] = hex[v & 0xFu];
        v >>= 4;
    }
    return (size_t)need;
}
