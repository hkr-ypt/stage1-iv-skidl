/*
 * test_model_dac8552.c — unit tests of the DAC8552 bit-level model
 * (sim/model_dac8552.c) against TI SLAS430A: Fig. 43 frame format, Table 1
 * control matrix, Table 2/3 power-down modes, Examples 1-4 (p.16), the /SYNC
 * interrupt rule (p.15, Fig. 45), execution on the 24th falling edge
 * (p.13), and power-on reset (p.15). The bit-bang (pin) path must be exactly
 * equivalent to the byte path.
 */
#include <stdlib.h>
#include <string.h>

#include "model_dac8552.h"
#include "unity.h"

static dac_model_t m;

void setUp(void)
{
    dacm_power_on(&m);
}

void tearDown(void)
{
}

/* ---- helpers --------------------------------------------------------------- */

static void frame_bytes(dac_model_t *d, uint8_t c, uint16_t data)
{
    dacm_sync(d, true);
    dacm_byte(d, c);
    dacm_byte(d, (uint8_t)(data >> 8));
    dacm_byte(d, (uint8_t)(data & 0xFFu));
    dacm_sync(d, false);
}

/* One bit on the pin path in mode 1: DIN set with SCLK low, SCLK high, low. */
static void pin_bit(dac_model_t *d, bool bit)
{
    dacm_pins(d, false, bit);
    dacm_pins(d, true, bit);
    dacm_pins(d, false, bit);
}

static void pin_bits(dac_model_t *d, uint32_t word, unsigned nbits, unsigned total)
{
    /* the first nbits of a `total`-bit word, MSB first */
    for (unsigned i = 0; i < nbits; i++) {
        pin_bit(d, ((word >> (total - 1u - i)) & 1u) != 0u);
    }
}

static void frame_pins(dac_model_t *d, uint8_t c, uint16_t data)
{
    uint32_t w = ((uint32_t)c << 16) | data;
    dacm_sync(d, true);
    pin_bits(d, w, 24u, 24u);
    dacm_sync(d, false);
}

static void assert_regs_equal(const dac_model_t *a, const dac_model_t *b)
{
    TEST_ASSERT_EQUAL_HEX16(a->buf[0], b->buf[0]);
    TEST_ASSERT_EQUAL_HEX16(a->buf[1], b->buf[1]);
    TEST_ASSERT_EQUAL_UINT8(a->buf_pd[0], b->buf_pd[0]);
    TEST_ASSERT_EQUAL_UINT8(a->buf_pd[1], b->buf_pd[1]);
    TEST_ASSERT_EQUAL_HEX16(a->dac[0], b->dac[0]);
    TEST_ASSERT_EQUAL_HEX16(a->dac[1], b->dac[1]);
    TEST_ASSERT_EQUAL_UINT8(a->dac_pd[0], b->dac_pd[0]);
    TEST_ASSERT_EQUAL_UINT8(a->dac_pd[1], b->dac_pd[1]);
    TEST_ASSERT_EQUAL_UINT32(a->frames_executed, b->frames_executed);
    TEST_ASSERT_EQUAL_UINT32(a->frames_aborted, b->frames_aborted);
    TEST_ASSERT_EQUAL_UINT32(a->reserved_violations, b->reserved_violations);
    TEST_ASSERT_EQUAL_UINT32(a->loads, b->loads);
    TEST_ASSERT_EQUAL_UINT32(a->b_not_fullscale_loads, b->b_not_fullscale_loads);
    TEST_ASSERT_EQUAL_UINT32(a->pd_nonzero_frames, b->pd_nonzero_frames);
    TEST_ASSERT_EQUAL(a->sync_low, b->sync_low);
    TEST_ASSERT_EQUAL(a->frame_done, b->frame_done);
    TEST_ASSERT_EQUAL_UINT8(a->nbits, b->nbits);
}

/* ---- power-on ------------------------------------------------------------------ */

static void test_power_on_state(void)
{
    dac_model_t d;
    memset(&d, 0xA5, sizeof(d));
    dacm_power_on(&d);
    TEST_ASSERT_FALSE(d.sync_low);
    TEST_ASSERT_FALSE(d.sclk);
    TEST_ASSERT_FALSE(d.frame_done);
    TEST_ASSERT_EQUAL_UINT8(0, d.nbits);
    for (int ch = 0; ch < 2; ch++) {
        TEST_ASSERT_EQUAL_HEX16(0, d.dac[ch]);
        TEST_ASSERT_EQUAL_HEX16(0, d.buf[ch]);
        TEST_ASSERT_EQUAL_UINT8(0, d.dac_pd[ch]);
        TEST_ASSERT_EQUAL_UINT8(0, d.buf_pd[ch]);
        TEST_ASSERT_EQUAL_INT32(0, dacm_effective(&d, ch)); /* zero scale */
    }
    TEST_ASSERT_EQUAL_UINT32(0, d.frames_executed + d.frames_aborted + d.reserved_violations +
                                    d.loads + d.b_not_fullscale_loads + d.pd_nonzero_frames);
}

/* ---- frame mechanics ---------------------------------------------------------- */

static void test_example1_simultaneous_update(void)
{
    frame_bytes(&m, 0x00, 0x1234); /* write buffer A only */
    TEST_ASSERT_EQUAL_HEX16(0x1234, m.buf[0]);
    TEST_ASSERT_EQUAL_HEX16(0, m.dac[0]);
    TEST_ASSERT_EQUAL_INT32(0, dacm_effective(&m, 0)); /* output unchanged */
    TEST_ASSERT_EQUAL_UINT32(0, m.loads);

    frame_bytes(&m, 0x34, 0xFFFF); /* write buffer B, load A and B */
    TEST_ASSERT_EQUAL_HEX16(0x1234, m.dac[0]);
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, m.dac[1]);
    TEST_ASSERT_EQUAL_INT32(0x1234, dacm_effective(&m, 0));
    TEST_ASSERT_EQUAL_INT32(0xFFFF, dacm_effective(&m, 1));
    TEST_ASSERT_EQUAL_UINT32(2, m.frames_executed);
    TEST_ASSERT_EQUAL_UINT32(1, m.loads);
    TEST_ASSERT_EQUAL_UINT32(0, m.b_not_fullscale_loads);
    TEST_ASSERT_EQUAL_UINT32(0, m.frames_aborted);
    TEST_ASSERT_EQUAL_UINT32(0, m.pd_nonzero_frames);
}

static void test_executes_on_24th_edge_before_sync_rises(void)
{
    dacm_sync(&m, true);
    dacm_byte(&m, 0x30); /* write buffer A and load A and B */
    dacm_byte(&m, 0xBE);
    TEST_ASSERT_EQUAL_UINT32(0, m.frames_executed);
    pin_bits(&m, 0xEF, 7u, 8u); /* 23 bits so far */
    TEST_ASSERT_EQUAL_UINT32(0, m.frames_executed);
    TEST_ASSERT_EQUAL_HEX16(0, m.dac[0]);
    pin_bit(&m, true); /* 24th falling edge: 0xEF ends in 1 */
    TEST_ASSERT_TRUE(m.frame_done);
    TEST_ASSERT_TRUE(m.sync_low);
    TEST_ASSERT_EQUAL_UINT32(1, m.frames_executed);
    TEST_ASSERT_EQUAL_HEX16(0xBEEF, m.dac[0]);
    dacm_sync(&m, false);
    TEST_ASSERT_EQUAL_UINT32(0, m.frames_aborted);
}

static void test_clocks_after_24th_edge_ignored(void)
{
    dacm_sync(&m, true);
    dacm_byte(&m, 0x34);
    dacm_byte(&m, 0xFF);
    dacm_byte(&m, 0xFF);
    /* a 4th and 5th byte in the same /SYNC-low period: ignored */
    dacm_byte(&m, 0x00);
    dacm_byte(&m, 0x00);
    pin_bits(&m, 0x5, 3u, 3u);
    dacm_sync(&m, false);
    TEST_ASSERT_EQUAL_UINT32(1, m.frames_executed);
    TEST_ASSERT_EQUAL_UINT32(0, m.frames_aborted);
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, m.dac[1]);
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, m.buf[1]);
    TEST_ASSERT_EQUAL_HEX16(0x0000, m.buf[0]);
}

static void test_sync_rise_before_24_bits_discards_frame(void)
{
    /* a known state first */
    frame_bytes(&m, 0x00, 0x1111);
    frame_bytes(&m, 0x34, 0xFFFF);
    const dac_model_t before = m;
    /* 0x30 0x00 0x00 would write buffer A = 0 and load both */
    for (unsigned n = 0; n < 24u; n++) {
        dac_model_t d = before;
        dacm_sync(&d, true);
        if (n >= 8u) {
            dacm_byte(&d, 0x30);
        }
        if (n >= 16u) {
            dacm_byte(&d, 0x00);
        }
        pin_bits(&d, (n < 8u) ? 0x30u : 0x00u, n % 8u, 8u);
        TEST_ASSERT_EQUAL_UINT8(n, d.nbits);
        dacm_sync(&d, false);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(before.frames_aborted + 1u, d.frames_aborted, "abort counted");
        TEST_ASSERT_EQUAL_UINT32(before.frames_executed, d.frames_executed);
        TEST_ASSERT_EQUAL_HEX16(before.buf[0], d.buf[0]);
        TEST_ASSERT_EQUAL_HEX16(before.buf[1], d.buf[1]);
        TEST_ASSERT_EQUAL_HEX16(before.dac[0], d.dac[0]);
        TEST_ASSERT_EQUAL_HEX16(before.dac[1], d.dac[1]);
        TEST_ASSERT_EQUAL_UINT32(before.loads, d.loads);
        TEST_ASSERT_EQUAL_UINT8(0, d.nbits);
        /* the next frame starts clean: no leftover bits prefix it */
        frame_bytes(&d, 0x00, 0x2222);
        frame_bytes(&d, 0x34, 0xFFFF);
        TEST_ASSERT_EQUAL_HEX16(0x2222, d.dac[0]);
        TEST_ASSERT_EQUAL_HEX16(0xFFFF, d.dac[1]);
        TEST_ASSERT_EQUAL_UINT32(0, d.reserved_violations);
    }
}

static void test_sync_is_level_triggered(void)
{
    /* a repeated "low" is not a new falling edge: the frame continues */
    dacm_sync(&m, true);
    dacm_byte(&m, 0x30);
    dacm_byte(&m, 0x42);
    dacm_sync(&m, true);
    dacm_byte(&m, 0x42);
    TEST_ASSERT_EQUAL_UINT32(1, m.frames_executed);
    TEST_ASSERT_EQUAL_HEX16(0x4242, m.dac[0]);
    /* a repeated "high" is not a rising edge and aborts nothing */
    dacm_sync(&m, false);
    dacm_sync(&m, false);
    TEST_ASSERT_EQUAL_UINT32(0, m.frames_aborted);
}

static void test_clocks_while_sync_high_ignored(void)
{
    dacm_byte(&m, 0x34);
    dacm_byte(&m, 0x12);
    dacm_byte(&m, 0x34);
    pin_bits(&m, 0xFFFFFF, 24u, 24u);
    TEST_ASSERT_EQUAL_UINT32(0, m.frames_executed);
    TEST_ASSERT_EQUAL_UINT32(0, m.frames_aborted);
    TEST_ASSERT_EQUAL_UINT8(0, m.nbits);
    TEST_ASSERT_EQUAL_HEX16(0, m.buf[1]);
    /* SCLK high when /SYNC falls, then low: that falling edge is bit 1 */
    dacm_pins(&m, true, false);
    dacm_sync(&m, true);
    dacm_pins(&m, false, false);
    TEST_ASSERT_EQUAL_UINT8(1, m.nbits);
}

static void test_empty_sync_pulse_counts_as_aborted(void)
{
    dacm_sync(&m, true);
    dacm_sync(&m, false);
    TEST_ASSERT_EQUAL_UINT32(1, m.frames_aborted);
    TEST_ASSERT_EQUAL_UINT32(0, m.frames_executed);
}

/* ---- control byte decoding (Table 1) ------------------------------------------- */

static void test_reserved_bits_counted(void)
{
    frame_bytes(&m, 0x80 | 0x34, 0xFFFF);
    TEST_ASSERT_EQUAL_UINT32(1, m.reserved_violations);
    frame_bytes(&m, 0x40, 0x0001);
    TEST_ASSERT_EQUAL_UINT32(2, m.reserved_violations);
    frame_bytes(&m, 0x00, 0x0001);
    frame_bytes(&m, 0x34, 0xFFFF);
    TEST_ASSERT_EQUAL_UINT32(2, m.reserved_violations);
    TEST_ASSERT_EQUAL_UINT32(4, m.frames_executed);
}

static void test_db19_is_dont_care(void)
{
    dac_model_t a, b;
    dacm_power_on(&a);
    dacm_power_on(&b);
    frame_bytes(&a, 0x00, 0x5A5A);
    frame_bytes(&a, 0x34, 0xFFFF);
    frame_bytes(&b, 0x08, 0x5A5A);
    frame_bytes(&b, 0x3C, 0xFFFF);
    assert_regs_equal(&a, &b);
    TEST_ASSERT_EQUAL_UINT32(0, b.reserved_violations);
}

static void test_bufsel_lda_ldb(void)
{
    /* Example 2: sequential loads */
    frame_bytes(&m, 0x10, 0xAAAA); /* buffer A, load A */
    TEST_ASSERT_EQUAL_HEX16(0xAAAA, m.dac[0]);
    TEST_ASSERT_EQUAL_HEX16(0x0000, m.dac[1]);
    TEST_ASSERT_EQUAL_UINT32(1, m.loads);
    TEST_ASSERT_EQUAL_UINT32(1, m.b_not_fullscale_loads); /* B still 0 */
    frame_bytes(&m, 0x24, 0xBBBB); /* buffer B, load B */
    TEST_ASSERT_EQUAL_HEX16(0xAAAA, m.dac[0]);
    TEST_ASSERT_EQUAL_HEX16(0xBBBB, m.dac[1]);
    TEST_ASSERT_EQUAL_UINT32(2, m.b_not_fullscale_loads);
    frame_bytes(&m, 0x24, 0xFFFF);
    TEST_ASSERT_EQUAL_UINT32(2, m.b_not_fullscale_loads);
    TEST_ASSERT_EQUAL_UINT32(3, m.loads);

    /* writing buffer B while loading only A: A comes from buffer A */
    frame_bytes(&m, 0x00, 0x1357); /* buffer A, no load */
    frame_bytes(&m, 0x14, 0x0000); /* buffer B = 0, load A only */
    TEST_ASSERT_EQUAL_HEX16(0x1357, m.dac[0]);
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, m.dac[1]); /* B register untouched */
    TEST_ASSERT_EQUAL_HEX16(0x0000, m.buf[1]);
    /* ...but the stale buffer B is loaded by the next LDB */
    frame_bytes(&m, 0x20, 0x2468); /* buffer A, load B */
    TEST_ASSERT_EQUAL_HEX16(0x2468, m.buf[0]);
    TEST_ASSERT_EQUAL_HEX16(0x0000, m.dac[1]);
    TEST_ASSERT_EQUAL_UINT32(3, m.b_not_fullscale_loads);
    TEST_ASSERT_EQUAL_UINT32(0, m.pd_nonzero_frames);
}

static void test_power_down_modes(void)
{
    frame_bytes(&m, 0x00, 0x8000);
    frame_bytes(&m, 0x34, 0xFFFF);
    /* Example 3: A to 1 k, B to 100 k simultaneously */
    frame_bytes(&m, 0x01, 0x0000);
    TEST_ASSERT_EQUAL_UINT8(0, m.dac_pd[0]); /* not loaded yet */
    TEST_ASSERT_EQUAL_INT32(0x8000, dacm_effective(&m, 0));
    frame_bytes(&m, 0x36, 0x0000);
    TEST_ASSERT_EQUAL_UINT8(1, m.dac_pd[0]);
    TEST_ASSERT_EQUAL_UINT8(2, m.dac_pd[1]);
    TEST_ASSERT_EQUAL_INT32(0, dacm_effective(&m, 0));
    TEST_ASSERT_EQUAL_INT32(0, dacm_effective(&m, 1));
    TEST_ASSERT_EQUAL_UINT32(2, m.pd_nonzero_frames);
    TEST_ASSERT_EQUAL_UINT32(1, m.b_not_fullscale_loads); /* B effectively 0 */

    /* Example 4: A then B to high impedance */
    frame_bytes(&m, 0x13, 0x0000);
    TEST_ASSERT_EQUAL_INT32(-1, dacm_effective(&m, 0));
    TEST_ASSERT_EQUAL_INT32(0, dacm_effective(&m, 1));
    frame_bytes(&m, 0x27, 0x0000);
    TEST_ASSERT_EQUAL_INT32(-1, dacm_effective(&m, 1));
    TEST_ASSERT_EQUAL_UINT32(4, m.pd_nonzero_frames);

    /* exit: PD 00 + new data + load (p.15) */
    frame_bytes(&m, 0x00, 0x8000);
    frame_bytes(&m, 0x34, 0xFFFF);
    TEST_ASSERT_EQUAL_INT32(0x8000, dacm_effective(&m, 0));
    TEST_ASSERT_EQUAL_INT32(0xFFFF, dacm_effective(&m, 1));
    TEST_ASSERT_EQUAL_UINT8(0, m.dac_pd[0]);
    TEST_ASSERT_EQUAL_UINT8(0, m.dac_pd[1]);
    TEST_ASSERT_EQUAL_UINT32(4, m.pd_nonzero_frames);
}

static void test_pd_state_travels_with_the_buffer(void)
{
    /* A stale power-down in buffer A is loaded by a later 0x34: this is why
     * the firmware always writes buffer A (PD 00) right before 0x34. */
    frame_bytes(&m, 0x00, 0x8000);
    frame_bytes(&m, 0x34, 0xFFFF);
    frame_bytes(&m, 0x02, 0x0000); /* buffer A: PD 100 k, no load */
    frame_bytes(&m, 0x24, 0xFFFF); /* load B only: A unaffected */
    TEST_ASSERT_EQUAL_INT32(0x8000, dacm_effective(&m, 0));
    frame_bytes(&m, 0x34, 0xFFFF);
    TEST_ASSERT_EQUAL_UINT8(2, m.dac_pd[0]);
    TEST_ASSERT_EQUAL_INT32(0, dacm_effective(&m, 0));
    TEST_ASSERT_EQUAL_INT32(0xFFFF, dacm_effective(&m, 1));
}

static void test_effective_invalid_channel(void)
{
    TEST_ASSERT_EQUAL_INT32(-1, dacm_effective(&m, 2));
    TEST_ASSERT_EQUAL_INT32(-1, dacm_effective(&m, -1));
}

/* ---- pin path ---------------------------------------------------------------- */

static void test_pins_latch_on_falling_edge_only(void)
{
    dacm_sync(&m, true);
    dacm_pins(&m, true, true); /* rising: nothing */
    TEST_ASSERT_EQUAL_UINT8(0, m.nbits);
    dacm_pins(&m, true, false); /* level unchanged: nothing */
    TEST_ASSERT_EQUAL_UINT8(0, m.nbits);
    dacm_pins(&m, false, false); /* falling: latches DIN at this edge = 0 */
    TEST_ASSERT_EQUAL_UINT8(1, m.nbits);
    TEST_ASSERT_EQUAL_HEX32(0, m.shift);
    dacm_pins(&m, false, true); /* low, DIN changes: nothing */
    TEST_ASSERT_EQUAL_UINT8(1, m.nbits);
    dacm_pins(&m, true, true);
    dacm_pins(&m, false, true);
    TEST_ASSERT_EQUAL_UINT8(2, m.nbits);
    TEST_ASSERT_EQUAL_HEX32(1, m.shift);
}

static void test_pin_frame_equals_byte_frame(void)
{
    dac_model_t a, b;
    dacm_power_on(&a);
    dacm_power_on(&b);
    frame_bytes(&a, 0x00, 0x8000);
    frame_bytes(&a, 0x34, 0xFFFF);
    frame_pins(&b, 0x00, 0x8000);
    frame_pins(&b, 0x34, 0xFFFF);
    assert_regs_equal(&a, &b);
    TEST_ASSERT_EQUAL_HEX16(0x8000, b.dac[0]);
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, b.dac[1]);
}

static uint32_t rng_state;
static uint32_t rnd(void)
{
    /* xorshift32, fixed seed */
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 17;
    rng_state ^= rng_state << 5;
    return rng_state;
}

static void test_bitbang_equivalent_to_bytes_random(void)
{
    dac_model_t a, b;
    dacm_power_on(&a);
    dacm_power_on(&b);
    rng_state = 0x2545F491u;
    for (int op = 0; op < 20000; op++) {
        uint32_t word = rnd() & 0xFFFFFFu;
        if ((rnd() & 3u) != 0u) {
            word &= 0x3FFFFFu; /* mostly legal control bytes */
        }
        unsigned nbytes = (unsigned)(rnd() % 6u); /* 0..5 bytes: partial, exact, extra */
        if ((rnd() & 1u) != 0u) {
            nbytes = 3u;
        }
        uint8_t bytes[5] = {(uint8_t)(word >> 16), (uint8_t)(word >> 8), (uint8_t)word,
                            (uint8_t)rnd(), (uint8_t)rnd()};
        dacm_sync(&a, true);
        dacm_sync(&b, true);
        for (unsigned i = 0; i < nbytes; i++) {
            dacm_byte(&a, bytes[i]);
            /* the pin model, randomly mixing with the byte path */
            if ((rnd() & 7u) == 0u) {
                dacm_byte(&b, bytes[i]);
            } else {
                pin_bits(&b, bytes[i], 8u, 8u);
            }
        }
        assert_regs_equal(&a, &b);
        TEST_ASSERT_EQUAL_HEX32(a.shift, b.shift);
        dacm_sync(&a, false);
        dacm_sync(&b, false);
        assert_regs_equal(&a, &b);
        TEST_ASSERT_EQUAL_HEX32(a.shift, b.shift);
    }
    TEST_ASSERT_TRUE(a.frames_aborted > 1000u);
    TEST_ASSERT_TRUE(a.frames_executed > 10000u);
    TEST_ASSERT_TRUE(a.reserved_violations > 100u);
    TEST_ASSERT_TRUE(a.pd_nonzero_frames > 100u);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_power_on_state);
    RUN_TEST(test_example1_simultaneous_update);
    RUN_TEST(test_executes_on_24th_edge_before_sync_rises);
    RUN_TEST(test_clocks_after_24th_edge_ignored);
    RUN_TEST(test_sync_rise_before_24_bits_discards_frame);
    RUN_TEST(test_sync_is_level_triggered);
    RUN_TEST(test_clocks_while_sync_high_ignored);
    RUN_TEST(test_empty_sync_pulse_counts_as_aborted);
    RUN_TEST(test_reserved_bits_counted);
    RUN_TEST(test_db19_is_dont_care);
    RUN_TEST(test_bufsel_lda_ldb);
    RUN_TEST(test_power_down_modes);
    RUN_TEST(test_pd_state_travels_with_the_buffer);
    RUN_TEST(test_effective_invalid_channel);
    RUN_TEST(test_pins_latch_on_falling_edge_only);
    RUN_TEST(test_pin_frame_equals_byte_frame);
    RUN_TEST(test_bitbang_equivalent_to_bytes_random);
    return UNITY_END();
}
