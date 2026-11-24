/*
 * test_dac8552.c — unit tests of drivers/dac8552.c through a test port that
 * routes SPI1 into the bit-level DAC8552 model (tests/unit/support/dac8552,
 * sim/model_dac8552.c).
 *
 * What is checked (docs/firmware/firmware_spec.md SI-1, §7.4, §9 dac8552 row;
 * TI SLAS430A Fig. 43, Table 1, Example 1, p.13-16):
 *  - the frame pair is 00 Ah Al + 34 FF FF for all 65 536 codes, and no
 *    production call can make the DAC execute another control byte or a B
 *    value other than 0xFFFF;
 *  - /SYNC frames every 24-bit frame, rises only after the write returned
 *    (never before 24 bits: the model counts aborted frames), toggles
 *    between the frames and stays high >= 1 us;
 *  - a transport timeout on either frame returns false, releases /SYNC,
 *    keeps the shadow, and leaves the DAC outputs as they were;
 *  - dac_scrub re-sends the shadow (not the last attempted code);
 *  - dac_zero_safe forces PC5 low first, aborts a partial frame of 0..23
 *    bits (also when frame 1 completed and frame 2 is partial), and ends at
 *    A = 0x8000, B = 0xFFFF, PD 00 with SPI healthy, with BSY stuck (RCC
 *    reset path) and with SPI dead (bit-bang path, mode 1 waveform checked),
 *    within 200 us of fake time, without calling port_now_us; also when it
 *    preempts its own bit-bang (a fault during the FAULT-entry zero);
 *  - bring-up DAC.RAW behaviour, and its absence from other builds.
 */
#include <string.h>

#include "dac8552.h"
#include "dac8552/dac_test_port.h"
#include "unity.h"

#if !FW_BRINGUP
/* Must not exist outside the bring-up build: weak references stay NULL. */
extern bool dac_raw_write(uint16_t code_a, uint16_t code_b) __attribute__((weak));
extern bool dac_raw_active(void) __attribute__((weak));
#endif

void setUp(void)
{
    dtp_reset();
    dac_init();
}

void tearDown(void)
{
}

/* ---- helpers --------------------------------------------------------------- */

static void model_frame(uint8_t c, uint16_t data)
{
    /* drive the DAC model directly (another master / a previous boot) */
    dacm_sync(&dtp.dac, true);
    dacm_byte(&dtp.dac, c);
    dacm_byte(&dtp.dac, (uint8_t)(data >> 8));
    dacm_byte(&dtp.dac, (uint8_t)(data & 0xFFu));
    dacm_sync(&dtp.dac, false);
}

static void assert_dac_holds(uint16_t a, uint16_t b)
{
    TEST_ASSERT_EQUAL_HEX16(a, dtp.dac.dac[0]);
    TEST_ASSERT_EQUAL_HEX16(b, dtp.dac.dac[1]);
    TEST_ASSERT_EQUAL_UINT8(0, dtp.dac.dac_pd[0]);
    TEST_ASSERT_EQUAL_UINT8(0, dtp.dac.dac_pd[1]);
    TEST_ASSERT_EQUAL_INT32(a, dacm_effective(&dtp.dac, 0));
    TEST_ASSERT_EQUAL_INT32(b, dacm_effective(&dtp.dac, 1));
}

static void assert_zero_pair_everywhere(void)
{
    assert_dac_holds(DAC_CODE_ZERO_NOMINAL, DAC_B_FULLSCALE);
    TEST_ASSERT_EQUAL_HEX16(0x8000, dtp.dac.buf[0]);
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, dtp.dac.buf[1]);
    TEST_ASSERT_EQUAL_UINT8(0, dtp.dac.buf_pd[0]);
    TEST_ASSERT_EQUAL_UINT8(0, dtp.dac.buf_pd[1]);
    TEST_ASSERT_TRUE(dac_shadow_valid());
    TEST_ASSERT_EQUAL_HEX16(0x8000, dac_shadow_a());
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, dac_shadow_b());
}

static void assert_no_protocol_violations(void)
{
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, dtp.write_without_cs, "SPI write with /SYNC high");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, dtp.write_during_bitbang, "SPI write while pins are GPIO");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, dtp.pins_outside_bitbang, "GPIO pins driven in AF mode");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, dtp.mosi_change_while_high, "DIN changed while SCLK high");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, dtp.delay_over_50, "port_delay_us > 50 us");
    TEST_ASSERT_TRUE_MESSAGE(dtp.writes_in_one_sync_max <= 1u, "more than one write per /SYNC frame");
    if (dtp.cs_falls > 1u) {
        TEST_ASSERT_TRUE_MESSAGE(dtp.min_sync_high_us >= 1u, "/SYNC high < 1 us between frames");
    }
    TEST_ASSERT_FALSE_MESSAGE(dtp.cs, "/SYNC left low");
    TEST_ASSERT_FALSE(dtp.dac.sync_low);
    TEST_ASSERT_FALSE(dtp.bitbang);
}

static void assert_only_production_frames(void)
{
    for (unsigned c = 0; c < 256u; c++) {
        if (c != 0x00u && c != 0x34u) {
            TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, dtp.ctrl_count[c], "unexpected control byte");
        }
    }
    TEST_ASSERT_EQUAL_UINT32(0, dtp.frames_34_not_ffff);
    TEST_ASSERT_EQUAL_UINT32(0, dtp.dac.reserved_violations);
    TEST_ASSERT_EQUAL_UINT32(0, dtp.dac.pd_nonzero_frames);
    TEST_ASSERT_EQUAL_UINT32(0, dtp.dac.b_not_fullscale_loads);
}

/* ---- frame builder ------------------------------------------------------------ */

static void test_frames_exhaustive_all_codes(void)
{
    for (uint32_t code = 0; code <= 0xFFFFu; code++) {
        uint8_t f[6];
        memset(f, 0xA5, sizeof(f));
        dac8552_frames((uint16_t)code, f);
        TEST_ASSERT_EQUAL_HEX8(0x00, f[0]);
        TEST_ASSERT_EQUAL_HEX8((uint8_t)(code >> 8), f[1]);
        TEST_ASSERT_EQUAL_HEX8((uint8_t)(code & 0xFFu), f[2]);
        TEST_ASSERT_EQUAL_HEX8(0x34, f[3]);
        TEST_ASSERT_EQUAL_HEX8(0xFF, f[4]);
        TEST_ASSERT_EQUAL_HEX8(0xFF, f[5]);
    }
    /* the constants are the SLAS430A Example 1 commands */
    TEST_ASSERT_EQUAL_HEX8(0x00, DAC_CTRL_WRITE_A);
    TEST_ASSERT_EQUAL_HEX8(0x34, DAC_CTRL_WRITE_B_LOAD); /* LDB|LDA|BufSel=B, PD 00 */
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, DAC_B_FULLSCALE);
}

/* ---- dac_update ---------------------------------------------------------------- */

static void test_update_exhaustive_through_model(void)
{
    for (uint32_t code = 0; code <= 0xFFFFu; code++) {
        TEST_ASSERT_TRUE(dac_update((uint16_t)code));
        if (dtp.dac.dac[0] != code || dtp.dac.dac[1] != 0xFFFFu || dtp.dac.dac_pd[0] != 0u ||
            dtp.dac.dac_pd[1] != 0u || dac_shadow_a() != code) {
            TEST_FAIL_MESSAGE("model/shadow mismatch");
        }
    }
    assert_dac_holds(0xFFFF, 0xFFFF);
    TEST_ASSERT_EQUAL_UINT32(0, dtp.dac.frames_aborted);
    TEST_ASSERT_EQUAL_UINT32(2u * 65536u, dtp.dac.frames_executed);
    TEST_ASSERT_EQUAL_UINT32(65536u, dtp.dac.loads);
    TEST_ASSERT_EQUAL_UINT32(65536u, dtp.ctrl_count[0x00]);
    TEST_ASSERT_EQUAL_UINT32(65536u, dtp.ctrl_count[0x34]);
    TEST_ASSERT_EQUAL_UINT32(0, dtp.order_violations); /* every 00 A is followed by 34 FFFF */
    TEST_ASSERT_EQUAL_UINT32(2u * 65536u, dtp.cs_falls); /* /SYNC toggles between frames */
    TEST_ASSERT_EQUAL_UINT32(0, dtp.hb_force_low_calls);
    assert_only_production_frames();
    assert_no_protocol_violations();
    dac_stats_t s;
    dac_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(65536u, s.updates);
    TEST_ASSERT_EQUAL_UINT32(0, s.failures);
    TEST_ASSERT_TRUE(dac_shadow_valid());
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, dac_shadow_b());
}

static void test_update_sync_rises_only_after_write_returns(void)
{
    TEST_ASSERT_TRUE(dac_update(0x1234));
    /* /SYNC low -> exactly one completed write -> /SYNC high -> >= 1 us */
    uint32_t i = 0;
    for (int frame = 0; frame < 2; frame++) {
        int a = dtp_find(DEV_CS_ASSERT, i);
        TEST_ASSERT_TRUE(a >= 0);
        int w = dtp_find(DEV_WRITE_OK, (uint32_t)a);
        int r = dtp_find(DEV_CS_RELEASE, (uint32_t)a);
        TEST_ASSERT_TRUE(w > a && r > w);
        TEST_ASSERT_EQUAL_UINT32(3, dtp.ev[w].arg);
        int next_a = dtp_find(DEV_CS_ASSERT, (uint32_t)r);
        if (next_a >= 0) {
            TEST_ASSERT_TRUE(dtp.ev[next_a].t >= dtp.ev[r].t + 1u);
        }
        i = (uint32_t)r;
    }
    TEST_ASSERT_EQUAL_UINT32(2, dtp.nframes);
    TEST_ASSERT_EQUAL_HEX8(0x00, dtp.frames[0].ctrl);
    TEST_ASSERT_EQUAL_HEX16(0x1234, dtp.frames[0].data);
    TEST_ASSERT_EQUAL_HEX8(0x34, dtp.frames[1].ctrl);
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, dtp.frames[1].data);
    TEST_ASSERT_EQUAL_UINT32(0, dtp.dac.frames_aborted);
    assert_no_protocol_violations();
}

static void test_update_timeout_on_either_frame(void)
{
    static const uint8_t fail_bits[] = {0, 1, 7, 8, 12, 16, 23, 24};
    for (unsigned fi = 0; fi < sizeof(fail_bits); fi++) {
        for (uint32_t frame = 0; frame < 2u; frame++) {
            dtp_reset();
            dac_init();
            TEST_ASSERT_TRUE(dac_update(0x1234));
            dac_stats_t s0, s1;
            dac_get_stats(&s0);
            const dac_model_t m0 = dtp.dac;
            uint32_t w0 = dtp.write_calls;
            dtp.write_fail_mask = 1ull << (w0 + frame);
            dtp.write_fail_bits = fail_bits[fi];
            dtp_clear_obs();

            TEST_ASSERT_FALSE(dac_update(0xABCD));

            TEST_ASSERT_FALSE_MESSAGE(dtp.cs, "/SYNC not released after a timeout");
            TEST_ASSERT_FALSE(dtp.dac.sync_low);
            TEST_ASSERT_TRUE(dac_shadow_valid());
            TEST_ASSERT_EQUAL_HEX16(0x1234, dac_shadow_a());
            TEST_ASSERT_EQUAL_HEX16(0xFFFF, dac_shadow_b());
            dac_get_stats(&s1);
            TEST_ASSERT_EQUAL_UINT32(s0.failures + 1u, s1.failures);
            TEST_ASSERT_EQUAL_UINT32(s0.updates, s1.updates);
            if (frame == 0u) {
                /* frame 2 must not follow a failed frame 1 */
                TEST_ASSERT_EQUAL_UINT32(w0 + 1u, dtp.write_calls);
                TEST_ASSERT_EQUAL_UINT32(m0.loads, dtp.dac.loads);
                TEST_ASSERT_EQUAL_HEX16(0x1234, dtp.dac.dac[0]);
            } else if (fail_bits[fi] < 24u) {
                /* buffer A took the new code, but nothing was loaded */
                TEST_ASSERT_EQUAL_HEX16(0xABCD, dtp.dac.buf[0]);
                TEST_ASSERT_EQUAL_HEX16(0x1234, dtp.dac.dac[0]);
                TEST_ASSERT_EQUAL_UINT32(m0.loads, dtp.dac.loads);
            } else {
                /* BSY stuck after a complete frame: the DAC did load; the
                 * caller latches F_DAC_SPI and zeroes (documented) */
                TEST_ASSERT_EQUAL_HEX16(0xABCD, dtp.dac.dac[0]);
            }
            TEST_ASSERT_EQUAL_HEX16(0xFFFF, dtp.dac.dac[1]);
            TEST_ASSERT_EQUAL_UINT32(m0.frames_aborted + (fail_bits[fi] < 24u ? 1u : 0u),
                                     dtp.dac.frames_aborted);
            assert_only_production_frames();

            /* the interface is not left mid-frame: the next update is clean */
            dtp.write_fail_mask = 0;
            TEST_ASSERT_TRUE(dac_update(0x5555));
            assert_dac_holds(0x5555, 0xFFFF);
            TEST_ASSERT_EQUAL_HEX16(0x5555, dac_shadow_a());
            TEST_ASSERT_EQUAL_UINT32(0, dtp.dac.reserved_violations);
            assert_no_protocol_violations();
        }
    }
}

static void test_update_failure_from_unknown_shadow(void)
{
    dtp.spi_dead = true;
    TEST_ASSERT_FALSE(dac_update(0x4000));
    TEST_ASSERT_FALSE(dac_shadow_valid());
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, dac_shadow_b());
    dac_stats_t s;
    dac_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(1, s.failures);
    TEST_ASSERT_EQUAL_UINT32(0, s.updates);
    TEST_ASSERT_FALSE(dtp.cs);
}

/* ---- dac_scrub ------------------------------------------------------------------ */

static void test_scrub_unknown_shadow_is_noop(void)
{
    TEST_ASSERT_FALSE(dac_shadow_valid());
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, dac_shadow_b()); /* never reads as a broken B */
    TEST_ASSERT_TRUE(dac_scrub());
    TEST_ASSERT_EQUAL_UINT32(0, dtp.write_calls);
    TEST_ASSERT_EQUAL_UINT32(0, dtp.cs_falls);
    dac_stats_t s;
    dac_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(0, s.scrubs);
}

static void test_scrub_restores_corrupted_dac(void)
{
    TEST_ASSERT_TRUE(dac_update(0x4321));
    /* an undetected corruption: B low and powered down, A elsewhere */
    model_frame(0x00, 0x0001);
    model_frame(0x37, 0x0000);
    TEST_ASSERT_EQUAL_INT32(-1, dacm_effective(&dtp.dac, 1));
    dtp_clear_obs();
    TEST_ASSERT_TRUE(dac_scrub());
    assert_dac_holds(0x4321, 0xFFFF);
    TEST_ASSERT_EQUAL_HEX16(0x4321, dtp.dac.buf[0]);
    TEST_ASSERT_EQUAL_UINT8(0, dtp.dac.buf_pd[0]);
    assert_no_protocol_violations();
    TEST_ASSERT_EQUAL_UINT32(0, dtp.order_violations);
    dac_stats_t s;
    dac_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(1, s.scrubs);
    TEST_ASSERT_EQUAL_UINT32(1, s.updates);
}

static void test_scrub_sends_shadow_not_last_attempt(void)
{
    TEST_ASSERT_TRUE(dac_update(0x4321));
    dtp.write_fail_mask = 1ull << (dtp.write_calls + 1u); /* frame 2 of the next update */
    TEST_ASSERT_FALSE(dac_update(0x7777));
    TEST_ASSERT_EQUAL_HEX16(0x7777, dtp.dac.buf[0]); /* frame 1 got through */
    TEST_ASSERT_TRUE(dac_scrub());
    assert_dac_holds(0x4321, 0xFFFF);
    TEST_ASSERT_EQUAL_HEX16(0x4321, dtp.dac.buf[0]);
}

static void test_scrub_failure_keeps_shadow(void)
{
    TEST_ASSERT_TRUE(dac_update(0x2000));
    dtp.write_fail_mask = 1ull << dtp.write_calls;
    dtp.write_fail_bits = 13;
    TEST_ASSERT_FALSE(dac_scrub());
    TEST_ASSERT_FALSE(dtp.cs);
    TEST_ASSERT_TRUE(dac_shadow_valid());
    TEST_ASSERT_EQUAL_HEX16(0x2000, dac_shadow_a());
    dac_stats_t s;
    dac_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(0, s.scrubs);
    TEST_ASSERT_EQUAL_UINT32(1, s.failures);
    TEST_ASSERT_TRUE(dac_scrub());
    dac_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(1, s.scrubs);
    assert_dac_holds(0x2000, 0xFFFF);
}

static void test_init_resets_all_state(void)
{
    TEST_ASSERT_TRUE(dac_update(0x0100));
    dtp.spi_dead = true;
    TEST_ASSERT_FALSE(dac_update(0x0200));
    dac_zero_safe();
    dac_init();
    dac_stats_t s;
    dac_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(0, s.updates + s.scrubs + s.failures + s.zero_safe + s.zero_bitbang);
    TEST_ASSERT_FALSE(dac_shadow_valid());
    TEST_ASSERT_EQUAL_HEX16(0x8000, dac_shadow_a());
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, dac_shadow_b());
    uint32_t w = dtp.write_calls;
    TEST_ASSERT_TRUE(dac_scrub());
    TEST_ASSERT_EQUAL_UINT32(w, dtp.write_calls);
}

/* ---- dac_zero_safe -------------------------------------------------------------- */

typedef enum {
    ZS_HEALTHY = 0,     /* SPI fine                                   */
    ZS_BSY_RESET_HEALS, /* wait_idle times out, the RCC reset fixes it */
    ZS_BSY_SPI_DEAD,    /* wait_idle times out and SPI stays dead      */
    ZS_SPI_DEAD,        /* idle OK but every write times out           */
    ZS_FRAME2_FAILS,    /* the second zero frame times out             */
    ZS_MODE_COUNT
} zs_mode_t;

static void zs_inject(zs_mode_t mode, uint8_t fail_bits)
{
    dtp.write_fail_bits = fail_bits;
    switch (mode) {
    case ZS_HEALTHY:
        break;
    case ZS_BSY_RESET_HEALS:
        dtp.bsy_stuck = true;
        dtp.spi_dead = true;
        dtp.reinit_heals = true;
        break;
    case ZS_BSY_SPI_DEAD:
        dtp.bsy_stuck = true;
        dtp.spi_dead = true;
        break;
    case ZS_SPI_DEAD:
        dtp.spi_dead = true;
        break;
    case ZS_FRAME2_FAILS:
        dtp.write_fail_mask = 1ull << (dtp.write_calls + 1u);
        break;
    default:
        break;
    }
}

/* Initial DAC contents before the interrupted frame. */
static void zs_initial_state(unsigned kind)
{
    switch (kind) {
    case 0: /* power-on: both zero */
        break;
    case 1: /* a normal setpoint */
        model_frame(0x00, 0xF000);
        model_frame(0x34, 0xFFFF);
        break;
    default: /* corrupted: A high impedance, B low with PD 1 k, buffers odd */
        model_frame(0x13, 0x0000);
        model_frame(0x25, 0x0000);
        model_frame(0x02, 0x1234);
        break;
    }
}

static void test_zero_safe_matrix(void)
{
    static const uint8_t victims[3][3] = {
        {0x30, 0x00, 0x00}, /* write A = 0 and load both            */
        {0x27, 0x00, 0x00}, /* B to high impedance                  */
        {0x34, 0x00, 0x00}, /* B = 0 and load both                  */
    };
    static const uint8_t fail_bits[] = {0, 11, 23, 24};
    static const uint8_t pair2[3] = {0x34, 0xFF, 0xFF};
    unsigned runs = 0;

    for (unsigned mode = 0; mode < ZS_MODE_COUNT; mode++) {
        for (unsigned fbi = 0; fbi < sizeof(fail_bits); fbi++) {
            if (mode == ZS_HEALTHY && fbi > 0u) {
                continue;
            }
            for (unsigned init = 0; init < 3u; init++) {
                for (unsigned kind = 0; kind < 4u; kind++) {
                    for (unsigned k = 0; k < 24u; k++) {
                        dtp_reset();
                        dac_init();
                        zs_initial_state(init);
                        if (kind < 3u) {
                            dtp_preload_bits(victims[kind], k);
                        } else {
                            /* dac_update interrupted: frame 1 complete, frame 2 partial */
                            static const uint8_t f1[3] = {0x00, 0x12, 0x34};
                            dtp_preload_bits(f1, 24u);
                            dtp.cs = false;
                            dacm_sync(&dtp.dac, false);
                            dtp_preload_bits(pair2, k);
                        }
                        TEST_ASSERT_TRUE(dtp.dac.sync_low);
                        const dac_model_t pre = dtp.dac;
                        zs_inject((zs_mode_t)mode, fail_bits[fbi]);
                        dtp_clear_obs();
                        uint64_t t0 = dtp.now;

                        dac_zero_safe();

                        assert_zero_pair_everywhere();
                        assert_no_protocol_violations();
                        TEST_ASSERT_TRUE_MESSAGE(dtp.nev > 0u && dtp.ev[0].kind == DEV_HB_LOW,
                                                 "PC5 not forced low first");
                        TEST_ASSERT_EQUAL_UINT32(1, dtp.hb_force_low_calls);
                        TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, dtp.now_calls, "port_now_us is not FAULT-SAFE");
                        TEST_ASSERT_TRUE_MESSAGE(dtp.now - t0 <= 200u, "dac_zero_safe over 200 us");
                        TEST_ASSERT_EQUAL_UINT32(1, dtp.wait_idle_calls);
                        bool idle_failed = (mode == ZS_BSY_RESET_HEALS || mode == ZS_BSY_SPI_DEAD);
                        TEST_ASSERT_EQUAL_UINT32(idle_failed ? 1u : 0u, dtp.reinit_calls);
                        if (idle_failed) {
                            TEST_ASSERT_TRUE(dtp_find(DEV_REINIT, 0) < dtp_find(DEV_CS_ASSERT, 0));
                        }
                        TEST_ASSERT_EQUAL_UINT32(dtp.bb_begin_calls, dtp.bb_end_calls);
                        TEST_ASSERT_EQUAL_UINT32(mode == ZS_HEALTHY ? 0u : 1u, dtp.bb_begin_calls);
                        /* the partial frame never executed, nothing illegal was formed */
                        TEST_ASSERT_EQUAL_UINT32(pre.reserved_violations, dtp.dac.reserved_violations);
                        TEST_ASSERT_EQUAL_UINT32(pre.pd_nonzero_frames, dtp.dac.pd_nonzero_frames);
                        if (mode == ZS_HEALTHY) {
                            TEST_ASSERT_EQUAL_UINT32(pre.frames_aborted + 1u, dtp.dac.frames_aborted);
                            TEST_ASSERT_EQUAL_UINT32(2, dtp.nframes);
                            TEST_ASSERT_EQUAL_UINT32(0, dtp.order_violations);
                        }
                        /* every executed frame is half of the zero pair, ending with 34 FFFF */
                        TEST_ASSERT_TRUE(dtp.nframes >= 2u && dtp.nframes <= DTP_MAX_FRAMES);
                        for (uint32_t f = 0; f < dtp.nframes; f++) {
                            bool ok = (dtp.frames[f].ctrl == 0x00u && dtp.frames[f].data == 0x8000u) ||
                                      (dtp.frames[f].ctrl == 0x34u && dtp.frames[f].data == 0xFFFFu);
                            TEST_ASSERT_TRUE_MESSAGE(ok, "frame other than the zero pair");
                        }
                        TEST_ASSERT_EQUAL_HEX8(0x00, dtp.frames[dtp.nframes - 2u].ctrl);
                        TEST_ASSERT_EQUAL_HEX8(0x34, dtp.frames[dtp.nframes - 1u].ctrl);
                        if (mode != ZS_HEALTHY) {
                            TEST_ASSERT_TRUE(dtp.frames[dtp.nframes - 1u].bitbang);
                            TEST_ASSERT_TRUE(dtp.frames[dtp.nframes - 2u].bitbang);
                        }
                        dac_stats_t s;
                        dac_get_stats(&s);
                        TEST_ASSERT_EQUAL_UINT32(1, s.zero_safe);
                        TEST_ASSERT_EQUAL_UINT32(mode == ZS_HEALTHY ? 0u : 1u, s.zero_bitbang);
                        runs++;
                    }
                }
            }
        }
    }
    TEST_ASSERT_EQUAL_UINT32((1u + 4u * 4u) * 3u * 4u * 24u, runs);
}

static void test_zero_safe_healthy_is_spi_only(void)
{
    TEST_ASSERT_TRUE(dac_update(0x9999));
    dtp_clear_obs();
    uint64_t t0 = dtp.now;
    dac_zero_safe();
    assert_zero_pair_everywhere();
    TEST_ASSERT_EQUAL_UINT32(2, dtp.write_ok);
    TEST_ASSERT_EQUAL_UINT32(0, dtp.bb_begin_calls + dtp.bb_pins_calls + dtp.reinit_calls);
    TEST_ASSERT_TRUE(dtp.now - t0 <= 20u);
    /* /SYNC raised (abort pulse) before the first frame starts */
    int r = dtp_find(DEV_CS_RELEASE, 0);
    int a = dtp_find(DEV_CS_ASSERT, 0);
    TEST_ASSERT_TRUE(r >= 0 && a > r);
    TEST_ASSERT_TRUE(dtp.ev[a].t >= dtp.ev[r].t + 1u);
    TEST_ASSERT_TRUE(dtp_find(DEV_DELAY, (uint32_t)r) < a);
    dac_stats_t s;
    dac_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(1, s.zero_safe);
    TEST_ASSERT_EQUAL_UINT32(0, s.zero_bitbang);
    TEST_ASSERT_EQUAL_UINT32(1, s.updates); /* a zero is not an update */
}

/* /SYNC rises, and stays high >= 1 us, before SPI1 is waited on or reset: a
 * partial frame is aborted by the DAC8552's /SYNC rule (SLAS430A, /SYNC
 * interrupt), never completed by a stray SCK edge of an SPI1 reset. */
static void test_zero_safe_raises_sync_before_touching_spi1(void)
{
    static const uint8_t partial[3] = { 0x00, 0x12, 0x34 };
    for (int mode = ZS_HEALTHY; mode <= ZS_BSY_RESET_HEALS; mode++) {
        dtp_reset();
        dac_init();
        dtp_preload_bits(partial, 13);      /* /SYNC low, 13 bits of a frame in */
        zs_inject((zs_mode_t)mode, 0);
        dtp_clear_obs();
        dac_zero_safe();
        assert_zero_pair_everywhere();
        int rise = dtp_find(DEV_CS_RELEASE, 0);
        int wait_ok = dtp_find(DEV_WAIT_IDLE_OK, 0);
        int wait_fail = dtp_find(DEV_WAIT_IDLE_FAIL, 0);
        int wait = (wait_ok >= 0 && (wait_fail < 0 || wait_ok < wait_fail)) ? wait_ok : wait_fail;
        TEST_ASSERT_TRUE(rise >= 0 && wait > rise);
        TEST_ASSERT_TRUE(dtp.ev[wait].t >= dtp.ev[rise].t + 1u);
        int reinit = dtp_find(DEV_REINIT, 0);
        if (mode == ZS_BSY_RESET_HEALS) {
            TEST_ASSERT_TRUE(reinit > rise);
        }
    }
}

static void test_zero_safe_bitbang_waveform_is_mode1(void)
{
    dtp.spi_dead = true;
    dtp.write_fail_bits = 5; /* the failed SPI attempt left 5 bits behind */
    dac_zero_safe();
    assert_zero_pair_everywhere();
    assert_no_protocol_violations();

    /* Reconstruct the bit stream from the pin events alone. */
    int begin = dtp_find(DEV_BB_BEGIN, 0);
    TEST_ASSERT_TRUE(begin >= 0);
    /* the pin switch-over happens with /SYNC high */
    bool cs = true;
    for (int i = 0; i < begin; i++) {
        if (dtp.ev[i].kind == DEV_CS_ASSERT) {
            cs = true;
        } else if (dtp.ev[i].kind == DEV_CS_RELEASE) {
            cs = false;
        }
    }
    TEST_ASSERT_FALSE_MESSAGE(cs, "bit-bang began with /SYNC low");

    uint32_t words[4] = {0};
    unsigned nbits[4] = {0};
    int frame = -1;
    bool sclk = false, mosi = false;
    uint64_t t_last_edge = 0;
    bool have_edge = false;
    uint32_t n = dtp.nev < DTP_MAX_EVENTS ? dtp.nev : DTP_MAX_EVENTS;
    TEST_ASSERT_TRUE(dtp.nev < DTP_MAX_EVENTS);
    for (uint32_t i = (uint32_t)begin; i < n; i++) {
        const dtp_event_t *e = &dtp.ev[i];
        if (e->kind == DEV_CS_ASSERT) {
            frame++;
            TEST_ASSERT_TRUE(frame < 4);
            TEST_ASSERT_FALSE_MESSAGE(sclk, "/SYNC fell with SCLK high");
        } else if (e->kind == DEV_BB_PINS) {
            bool s = (e->arg & 2u) != 0u;
            bool d = (e->arg & 1u) != 0u;
            if (s != sclk) {
                if (have_edge) {
                    TEST_ASSERT_TRUE_MESSAGE(e->t >= t_last_edge + 1u, "SCLK phase < 1 us");
                }
                have_edge = true;
                t_last_edge = e->t;
            }
            if (sclk && !s) {
                TEST_ASSERT_TRUE(frame >= 0);
                TEST_ASSERT_EQUAL(mosi, d); /* DIN stable through the high phase */
                words[frame] = (words[frame] << 1) | (d ? 1u : 0u);
                nbits[frame]++;
            }
            sclk = s;
            mosi = d;
        }
    }
    TEST_ASSERT_EQUAL_INT(1, frame); /* two frames */
    TEST_ASSERT_EQUAL_UINT32(24, nbits[0]);
    TEST_ASSERT_EQUAL_UINT32(24, nbits[1]);
    TEST_ASSERT_EQUAL_HEX32(0x008000, words[0]);
    TEST_ASSERT_EQUAL_HEX32(0x34FFFF, words[1]);
    TEST_ASSERT_EQUAL_INT(DEV_BB_END, dtp.ev[n - 1u].kind == DEV_BB_END ? DEV_BB_END
                                                                        : dtp.ev[n - 2u].kind);
}

static void test_zero_safe_reinit_path_then_bitbang(void)
{
    zs_inject(ZS_BSY_RESET_HEALS, 0);
    dac_zero_safe();
    assert_zero_pair_everywhere();
    TEST_ASSERT_EQUAL_UINT32(1, dtp.reinit_calls);
    TEST_ASSERT_EQUAL_UINT32(2, dtp.write_ok); /* SPI works again after the reset */
    TEST_ASSERT_EQUAL_UINT32(4, dtp.nframes);  /* ... and the pair is bit-banged as well */
    TEST_ASSERT_FALSE(dtp.frames[1].bitbang);
    TEST_ASSERT_TRUE(dtp.frames[3].bitbang);
    dac_stats_t s;
    dac_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(1, s.zero_bitbang);
}

/* A fault (or the dead-man) preempts the FAULT-entry zero while it is
 * bit-banging: the nested zero must still reach the DAC. */
static bool g_nested_ok;
static bool g_nested_spi_healthy;
static void nested_zero_hook(void)
{
    if (g_nested_spi_healthy) {
        dtp.spi_dead = false;
        dtp.bsy_stuck = false;
    }
    dac_zero_safe();
    g_nested_ok = dtp.dac.dac[0] == 0x8000u && dtp.dac.dac[1] == 0xFFFFu &&
                  dtp.dac.dac_pd[0] == 0u && dtp.dac.dac_pd[1] == 0u && !dtp.cs &&
                  !dtp.dac.sync_low && !dtp.bitbang && dtp.write_during_bitbang == 0u &&
                  dtp.pins_outside_bitbang == 0u;
}

static void test_zero_safe_preempting_its_own_bitbang(void)
{
    for (int healthy = 0; healthy < 2; healthy++) {
        for (uint32_t at = 1; at <= 3u * 48u; at++) {
            dtp_reset();
            dac_init();
            zs_initial_state(2);
            dtp.spi_dead = true;
            dtp.bsy_stuck = true;
            dtp.pins_hook_at = at;
            dtp.pins_hook = nested_zero_hook;
            g_nested_spi_healthy = healthy != 0;
            g_nested_ok = false;
            dac_zero_safe();
            TEST_ASSERT_TRUE_MESSAGE(g_nested_ok, "nested dac_zero_safe did not zero the DAC");
        }
    }
}

static void test_zero_safe_with_sclk_left_high(void)
{
    /* a bit-bang that was cut off with SCLK high and /SYNC low */
    dtp.dac.sclk = true;
    dtp_preload_bits((const uint8_t[]){0x27, 0x00, 0x00}, 9);
    dtp.dac.sclk = true;
    dtp.spi_dead = true;
    dac_zero_safe();
    assert_zero_pair_everywhere();
    TEST_ASSERT_EQUAL_UINT32(0, dtp.dac.pd_nonzero_frames);
}

static void test_zero_safe_twice_is_idempotent(void)
{
    dac_zero_safe();
    dac_zero_safe();
    assert_zero_pair_everywhere();
    dac_stats_t s;
    dac_get_stats(&s);
    TEST_ASSERT_EQUAL_UINT32(2, s.zero_safe);
    TEST_ASSERT_EQUAL_UINT32(0, dtp.dac.frames_aborted); /* /SYNC was already high */
    TEST_ASSERT_EQUAL_UINT32(0, dtp.dac.b_not_fullscale_loads);
}

/* ---- bring-up DAC.RAW ---------------------------------------------------------- */

#if FW_BRINGUP
static void test_raw_write_and_restore(void)
{
    TEST_ASSERT_FALSE(dac_raw_active());
    TEST_ASSERT_TRUE(dac_raw_write(0x1111, 0x2222));
    assert_dac_holds(0x1111, 0x2222);
    TEST_ASSERT_TRUE(dac_raw_active());
    TEST_ASSERT_EQUAL_HEX16(0x1111, dac_shadow_a());
    TEST_ASSERT_EQUAL_HEX16(0x2222, dac_shadow_b());
    /* still the Example 1 command pair, only the B data differ */
    TEST_ASSERT_EQUAL_UINT32(1, dtp.ctrl_count[0x00]);
    TEST_ASSERT_EQUAL_UINT32(1, dtp.ctrl_count[0x34]);
    TEST_ASSERT_EQUAL_UINT32(0, dtp.dac.pd_nonzero_frames);

    /* the scrub is suspended while DAC.RAW holds */
    uint32_t w = dtp.write_calls;
    TEST_ASSERT_TRUE(dac_scrub());
    TEST_ASSERT_EQUAL_UINT32(w, dtp.write_calls);
    assert_dac_holds(0x1111, 0x2222);

    /* a failed raw write changes nothing */
    dtp.write_fail_mask = 1ull << (dtp.write_calls + 1u);
    TEST_ASSERT_FALSE(dac_raw_write(0x3333, 0x4444));
    TEST_ASSERT_TRUE(dac_raw_active());
    TEST_ASSERT_EQUAL_HEX16(0x2222, dac_shadow_b());
    assert_dac_holds(0x1111, 0x2222);

    /* a failed dac_update keeps DAC.RAW (the shadow still says B = 0x2222) */
    dtp.write_fail_mask = 1ull << dtp.write_calls;
    TEST_ASSERT_FALSE(dac_update(0x8000));
    TEST_ASSERT_TRUE(dac_raw_active());

    /* any successful update restores B and the invariant */
    TEST_ASSERT_TRUE(dac_update(0x8000));
    TEST_ASSERT_FALSE(dac_raw_active());
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, dac_shadow_b());
    assert_dac_holds(0x8000, 0xFFFF);

    /* ... and so does the robust zero */
    TEST_ASSERT_TRUE(dac_raw_write(0x0000, 0x0000));
    TEST_ASSERT_TRUE(dac_raw_active());
    dac_zero_safe();
    TEST_ASSERT_FALSE(dac_raw_active());
    assert_zero_pair_everywhere();

    /* ... and init */
    TEST_ASSERT_TRUE(dac_raw_write(0x0000, 0x0000));
    dac_init();
    TEST_ASSERT_FALSE(dac_raw_active());
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, dac_shadow_b());
}
#else
static void test_no_raw_symbols_outside_bringup(void)
{
    TEST_ASSERT_TRUE(dac_raw_write == NULL);
    TEST_ASSERT_TRUE(dac_raw_active == NULL);
}
#endif

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_frames_exhaustive_all_codes);
    RUN_TEST(test_update_exhaustive_through_model);
    RUN_TEST(test_update_sync_rises_only_after_write_returns);
    RUN_TEST(test_update_timeout_on_either_frame);
    RUN_TEST(test_update_failure_from_unknown_shadow);
    RUN_TEST(test_scrub_unknown_shadow_is_noop);
    RUN_TEST(test_scrub_restores_corrupted_dac);
    RUN_TEST(test_scrub_sends_shadow_not_last_attempt);
    RUN_TEST(test_scrub_failure_keeps_shadow);
    RUN_TEST(test_init_resets_all_state);
    RUN_TEST(test_zero_safe_matrix);
    RUN_TEST(test_zero_safe_healthy_is_spi_only);
    RUN_TEST(test_zero_safe_raises_sync_before_touching_spi1);
    RUN_TEST(test_zero_safe_bitbang_waveform_is_mode1);
    RUN_TEST(test_zero_safe_reinit_path_then_bitbang);
    RUN_TEST(test_zero_safe_preempting_its_own_bitbang);
    RUN_TEST(test_zero_safe_with_sclk_left_high);
    RUN_TEST(test_zero_safe_twice_is_idempotent);
#if FW_BRINGUP
    RUN_TEST(test_raw_write_and_restore);
#else
    RUN_TEST(test_no_raw_symbols_outside_bringup);
#endif
    return UNITY_END();
}
