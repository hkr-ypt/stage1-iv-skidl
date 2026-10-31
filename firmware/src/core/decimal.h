/*
 * decimal.h — strict decimal number parsing and integer fixed-point printing.
 *
 * The protocol never parses or prints floating point text. Inputs are parsed
 * straight into scaled integers (uV, nA, us); outputs are printed from scaled
 * integers. No locale, no strtod, no printf("%f") (which pulls the heap into
 * newlib-nano).
 *
 * Accepted input grammar (whole token must match):
 *     [+-] digits [ . digits ] [ (e|E) [+-] digits ]
 *  or [+-] . digits [ (e|E) [+-] digits ]
 * At least one digit before or after the point; no spaces; no hex; no inf/nan.
 */
#ifndef DECIMAL_H
#define DECIMAL_H

#include "fw_types.h"

typedef enum {
    DEC_OK = 0,
    DEC_SYNTAX,      /* not a number in the grammar above                    */
    DEC_INEXACT,     /* more fractional digits than the target scale holds   */
    DEC_RANGE,       /* outside [min, max]                                    */
} dec_status_t;

/* Parse `s[0..len)` as value * 10^scale into *out. Example: "1.5" with
 * scale 6 gives 1500000 (uV). Inputs not exactly representable at the scale
 * are rejected with DEC_INEXACT (no rounding). */
dec_status_t dec_parse_scaled(const char *s, size_t len, int scale,
                              int64_t min, int64_t max, int64_t *out);

/* Unsigned decimal integer (no sign, no point, no exponent). */
dec_status_t dec_parse_u32(const char *s, size_t len, uint32_t min, uint32_t max,
                           uint32_t *out);

/* Print `value` (in units of 10^-frac) as a decimal with exactly `frac`
 * fractional digits, e.g. (1500000, 6) -> "1.500000", (-5, 3) -> "-0.005".
 * frac == 0 prints an integer. Writes a NUL terminator; returns the number of
 * characters written (excluding NUL), or 0 if cap is too small. */
size_t dec_format(char *buf, size_t cap, int64_t value, unsigned frac);

/* Print an unsigned integer in hex with a fixed number of digits ("%0*X"). */
size_t dec_format_hex(char *buf, size_t cap, uint32_t value, unsigned digits);

#endif /* DECIMAL_H */
