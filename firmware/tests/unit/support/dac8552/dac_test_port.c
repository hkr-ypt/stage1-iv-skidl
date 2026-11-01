/*
 * dac_test_port.c — test port for drivers/dac8552.c: port_dac_*, port_delay_us,
 * port_now_us and port_hb_force_low routed to a DAC8552 bit-level model
 * (sim/model_dac8552.c) with fault injection. See dac_test_port.h.
 */
#include "dac8552/dac_test_port.h"

#include <string.h>

#include "port.h"

dtp_t dtp;

static void dtp_log(dtp_ev_kind_t kind, uint32_t arg)
{
    if (dtp.nev < DTP_MAX_EVENTS) {
        dtp.ev[dtp.nev].kind = kind;
        dtp.ev[dtp.nev].t = dtp.now;
        dtp.ev[dtp.nev].arg = arg;
    }
    dtp.nev++;
}

void dtp_clear_obs(void)
{
    dtp.hb_force_low_calls = 0;
    dtp.now_calls = 0;
    dtp.wait_idle_calls = dtp.reinit_calls = 0;
    dtp.bb_begin_calls = dtp.bb_end_calls = dtp.bb_pins_calls = 0;
    dtp.write_ok = dtp.write_fail = 0;
    dtp.cs_falls = dtp.cs_rises = 0;
    dtp.delay_calls = 0;
    dtp.delay_total_us = 0;
    dtp.write_without_cs = dtp.write_during_bitbang = 0;
    dtp.pins_outside_bitbang = dtp.mosi_change_while_high = 0;
    dtp.delay_over_50 = 0;
    dtp.writes_in_one_sync_max = 0;
    dtp.writes_this_sync = 0;
    dtp.have_rise = false;
    dtp.last_rise_t = 0;
    dtp.min_sync_high_us = UINT64_MAX;
    dtp.nframes = 0;
    memset(dtp.ctrl_count, 0, sizeof(dtp.ctrl_count));
    dtp.frames_34_not_ffff = 0;
    dtp.order_violations = 0;
    dtp.pending_a = false;
    dtp.nev = 0;
}

void dtp_reset(void)
{
    memset(&dtp, 0, sizeof(dtp));
    dacm_power_on(&dtp.dac);
    dtp.now = 1000;
    dtp_clear_obs();
}

/* Record frames the model executed since `before` (frames_executed count). */
static void dtp_note_frames(uint32_t before, bool bitbang)
{
    if (dtp.dac.frames_executed == before) {
        return;
    }
    /* The model executes at most one frame per /SYNC-low period and keeps
     * the 24 bits in `shift` until /SYNC falls again. */
    uint8_t  ctrl = (uint8_t)((dtp.dac.shift >> 16) & 0xFFu);
    uint16_t data = (uint16_t)(dtp.dac.shift & 0xFFFFu);
    if (dtp.nframes < DTP_MAX_FRAMES) {
        dtp.frames[dtp.nframes].ctrl = ctrl;
        dtp.frames[dtp.nframes].data = data;
        dtp.frames[dtp.nframes].bitbang = bitbang;
    }
    dtp.nframes++;
    dtp.ctrl_count[ctrl]++;
    if (ctrl == 0x34u && data != 0xFFFFu) {
        dtp.frames_34_not_ffff++;
    }
    if (dtp.pending_a && ctrl != 0x34u) {
        dtp.order_violations++;
    }
    dtp.pending_a = (ctrl == 0x00u);
}

/* Clock the first nbits of bytes[] on the SPI path (SCLK idles low). */
static void dtp_clock_spi_bits(const uint8_t *bytes, unsigned nbits)
{
    unsigned full = nbits / 8u;
    for (unsigned i = 0; i < full; i++) {
        dacm_byte(&dtp.dac, bytes[i]);
    }
    for (unsigned b = 0; b < nbits % 8u; b++) {
        bool bit = ((bytes[full] >> (7u - b)) & 1u) != 0u;
        dacm_pins(&dtp.dac, true, bit);
        dacm_pins(&dtp.dac, false, bit);
    }
}

void dtp_preload_bits(const uint8_t *bytes, unsigned nbits)
{
    if (!dtp.cs) {
        dtp.cs = true;
        dacm_sync(&dtp.dac, true);
    }
    dtp_clock_spi_bits(bytes, nbits);
}

int dtp_find(dtp_ev_kind_t kind, uint32_t from)
{
    uint32_t n = dtp.nev < DTP_MAX_EVENTS ? dtp.nev : DTP_MAX_EVENTS;
    for (uint32_t i = from; i < n; i++) {
        if (dtp.ev[i].kind == kind) {
            return (int)i;
        }
    }
    return -1;
}

/* ---- port.h ------------------------------------------------------------- */

uint64_t port_now_us(void)
{
    dtp.now_calls++;
    dtp_log(DEV_NOW, 0);
    return dtp.now;
}

void port_delay_us(uint32_t us)
{
    dtp.delay_calls++;
    dtp.delay_total_us += us;
    if (us > 50u) {
        dtp.delay_over_50++;
    }
    dtp_log(DEV_DELAY, us);
    dtp.now += us;
}

void port_hb_force_low(void)
{
    dtp.hb_force_low_calls++;
    dtp_log(DEV_HB_LOW, 0);
}

void port_dac_cs(bool asserted)
{
    if (asserted && !dtp.cs) {
        dtp.cs_falls++;
        if (dtp.have_rise) {
            uint64_t high = dtp.now - dtp.last_rise_t;
            if (high < dtp.min_sync_high_us) {
                dtp.min_sync_high_us = high;
            }
        }
        dtp.writes_this_sync = 0;
    } else if (!asserted && dtp.cs) {
        dtp.cs_rises++;
        dtp.have_rise = true;
        dtp.last_rise_t = dtp.now;
    }
    dtp.cs = asserted;
    dtp_log(asserted ? DEV_CS_ASSERT : DEV_CS_RELEASE, 0);
    dacm_sync(&dtp.dac, asserted);
}

bool port_dac_write(const uint8_t *tx, size_t n, uint32_t timeout_us)
{
    uint32_t idx = dtp.write_calls++;
    bool fail = dtp.spi_dead || (idx < 64u && ((dtp.write_fail_mask >> idx) & 1u) != 0u);
    uint32_t before = dtp.dac.frames_executed;

    if (!dtp.cs) {
        dtp.write_without_cs++;
    } else {
        dtp.writes_this_sync++;
        if (dtp.writes_this_sync > dtp.writes_in_one_sync_max) {
            dtp.writes_in_one_sync_max = dtp.writes_this_sync;
        }
    }
    if (dtp.bitbang) {
        dtp.write_during_bitbang++;
    }

    if (fail) {
        unsigned bits = dtp.write_fail_bits;
        if (bits > n * 8u) {
            bits = (unsigned)(n * 8u);
        }
        if (!dtp.bitbang) {
            dtp_clock_spi_bits(tx, bits);
        }
        dtp_note_frames(before, false);
        dtp.write_fail++;
        dtp_log(DEV_WRITE_FAIL, (uint32_t)n);
        dtp.now += timeout_us;
        return false;
    }
    if (!dtp.bitbang) {
        for (size_t i = 0; i < n; i++) {
            dacm_byte(&dtp.dac, tx[i]);
        }
    }
    dtp_note_frames(before, false);
    dtp.write_ok++;
    dtp_log(DEV_WRITE_OK, (uint32_t)n);
    dtp.now += (n * 8u + 5u) / 6u + 1u;
    return true;
}

bool port_dac_wait_idle(uint32_t timeout_us)
{
    dtp.wait_idle_calls++;
    if (dtp.bsy_stuck) {
        dtp_log(DEV_WAIT_IDLE_FAIL, timeout_us);
        dtp.now += timeout_us;
        return false;
    }
    dtp_log(DEV_WAIT_IDLE_OK, timeout_us);
    return true;
}

void port_dac_spi_reinit(void)
{
    dtp.reinit_calls++;
    dtp_log(DEV_REINIT, 0);
    if (dtp.reinit_heals) {
        dtp.bsy_stuck = false;
        dtp.spi_dead = false;
    }
    dtp.now += 2u;
}

void port_dac_bitbang_begin(void)
{
    dtp.bb_begin_calls++;
    dtp_log(DEV_BB_BEGIN, 0);
    dtp.bitbang = true;
    dtp.sclk = false;
    uint32_t before = dtp.dac.frames_executed;
    dacm_pins(&dtp.dac, false, dtp.mosi);
    dtp_note_frames(before, true);
}

void port_dac_bitbang_pins(bool sclk, bool mosi)
{
    dtp.bb_pins_calls++;
    dtp_log(DEV_BB_PINS, (sclk ? 2u : 0u) | (mosi ? 1u : 0u));
    if (!dtp.bitbang) {
        dtp.pins_outside_bitbang++;
    } else {
        if (dtp.sclk && mosi != dtp.mosi) {
            /* DIN changed while SCLK was high or together with the falling
             * edge: violates t5/t6 around the latching edge. */
            dtp.mosi_change_while_high++;
        }
        uint32_t before = dtp.dac.frames_executed;
        dacm_pins(&dtp.dac, sclk, mosi);
        dtp_note_frames(before, true);
        dtp.sclk = sclk;
        dtp.mosi = mosi;
    }
    if (dtp.pins_hook != NULL && dtp.bb_pins_calls == dtp.pins_hook_at) {
        void (*hook)(void) = dtp.pins_hook;
        dtp.pins_hook = NULL;
        hook();
    }
}

void port_dac_bitbang_end(void)
{
    dtp.bb_end_calls++;
    dtp_log(DEV_BB_END, 0);
    /* PA5 back to AF5: SPI1 drives SCLK at its idle level (low). */
    uint32_t before = dtp.dac.frames_executed;
    dacm_pins(&dtp.dac, false, dtp.mosi);
    dtp_note_frames(before, true);
    dtp.bitbang = false;
    dtp.sclk = false;
}
