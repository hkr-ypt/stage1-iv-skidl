/*
 * model_dac8552.c — bit-level behavioural model of the DAC8552 serial
 * interface and registers (TI SLAS430A), implementing model_dac8552.h.
 *
 * Datasheet facts modelled (SLAS430A, July 2006 rev. October 2006):
 *  - p.4 pin 5 / p.13 "Serial Interface": /SYNC is level triggered. Its
 *    falling edge enables the 24-bit input shift register; DIN is clocked in
 *    on each SCLK FALLING edge, MSB (DB23) first (Fig. 43). On the 24th
 *    falling edge the shift register is locked and the command is executed
 *    immediately, without waiting for /SYNC to rise; further clocking does
 *    not change the shift register; a new frame starts only at the next
 *    falling edge of /SYNC.
 *  - p.15 "SYNC interrupt", Fig. 45: /SYNC rising before the 24th falling
 *    edge resets the shift register and discards the write: no buffer, DAC
 *    register or operating-mode change. The model counts every /SYNC-low
 *    period that ends without a completed frame in frames_aborted (also an
 *    empty one with zero clocks: the firmware never produces one on purpose,
 *    so tests can require the counter to stay at zero).
 *  - p.14 Fig. 43 / Table 1: control byte DB23..DB16 = 0 0 LDB LDA X BufSel
 *    PD1 PD0. DB23/DB22 are reserved ("must be 0 for proper operation"); the
 *    datasheet does not say what happens otherwise, so the model counts the
 *    violation and executes the remaining bits as decoded (tests must require
 *    reserved_violations == 0). DB19 is don't-care. The 16 data bits and the
 *    PD bits go to data buffer A (BufSel 0) or B (BufSel 1); LDA/LDB copy
 *    buffer A/B (code and PD state) into DAC register A/B. Examples 1-4
 *    (p.16) follow from these rules, e.g. Example 3 powers A and B down to
 *    different modes with one load.
 *  - p.14 Table 2 / p.15 Table 3: PD1:PD0 = 00 normal, 01 1 kOhm to GND,
 *    10 100 kOhm to GND, 11 high impedance. dacm_effective() reports 0 for a
 *    resistively powered-down channel and -1 for high impedance.
 *  - p.15 "Power-on reset": DAC registers zero, outputs at zero scale, until a
 *    valid write and load. The input buffers are not stated to be cleared;
 *    the model (per its header) clears them too. /SYNC idles high (R34
 *    pull-up on the board), SCLK low.
 *
 * Statistics: b_not_fullscale_loads counts loads after which channel B is not
 * effectively at full scale, i.e. dacm_effective(B) != 0xFFFF (a powered-down
 * B breaks the positive output bound just like a low code).
 *
 * The SPI path (dacm_byte) assumes mode 1 with CPOL 0: SCLK idles low before
 * and after the byte, so it leaves m->sclk = false.
 */
#include "model_dac8552.h"

#include <string.h>

#define DACM_FRAME_BITS     24u
#define DACM_CTRL_RESERVED  0xC0u
#define DACM_CTRL_LDB       0x20u
#define DACM_CTRL_LDA       0x10u
#define DACM_CTRL_BUFSEL    0x04u
#define DACM_CTRL_PD_MASK   0x03u
#define DACM_FULLSCALE      0xFFFFu

void dacm_power_on(dac_model_t *m)
{
    memset(m, 0, sizeof(*m));
    m->sync_low = false;
    m->sclk = false;
}

static void dacm_execute(dac_model_t *m)
{
    uint8_t  ctrl = (uint8_t)((m->shift >> 16) & 0xFFu);
    uint16_t data = (uint16_t)(m->shift & 0xFFFFu);
    unsigned sel  = (ctrl & DACM_CTRL_BUFSEL) ? 1u : 0u;
    uint8_t  pd   = (uint8_t)(ctrl & DACM_CTRL_PD_MASK);
    bool     lda  = (ctrl & DACM_CTRL_LDA) != 0u;
    bool     ldb  = (ctrl & DACM_CTRL_LDB) != 0u;

    m->frames_executed++;
    if (ctrl & DACM_CTRL_RESERVED) {
        m->reserved_violations++;
    }
    if (pd != 0u) {
        m->pd_nonzero_frames++;
    }
    m->buf[sel] = data;
    m->buf_pd[sel] = pd;
    if (lda) {
        m->dac[0] = m->buf[0];
        m->dac_pd[0] = m->buf_pd[0];
    }
    if (ldb) {
        m->dac[1] = m->buf[1];
        m->dac_pd[1] = m->buf_pd[1];
    }
    if (lda || ldb) {
        m->loads++;
        if (dacm_effective(m, 1) != (int32_t)DACM_FULLSCALE) {
            m->b_not_fullscale_loads++;
        }
    }
}

/* One SCLK falling edge with DIN = bit. */
static void dacm_clock(dac_model_t *m, bool bit)
{
    if (!m->sync_low || m->frame_done) {
        return; /* shift register disabled or locked */
    }
    m->shift = ((m->shift << 1) | (bit ? 1u : 0u)) & 0xFFFFFFu;
    m->nbits++;
    if (m->nbits >= DACM_FRAME_BITS) {
        m->frame_done = true;
        dacm_execute(m);
    }
}

void dacm_sync(dac_model_t *m, bool low)
{
    if (low && !m->sync_low) {
        /* falling edge: a new frame */
        m->sync_low = true;
        m->frame_done = false;
        m->shift = 0;
        m->nbits = 0;
    } else if (!low && m->sync_low) {
        /* rising edge: interrupts an incomplete frame */
        m->sync_low = false;
        if (!m->frame_done) {
            m->frames_aborted++;
            m->shift = 0;
            m->nbits = 0;
        }
    }
}

void dacm_byte(dac_model_t *m, uint8_t b)
{
    for (unsigned i = 8u; i-- > 0u;) {
        dacm_clock(m, (((unsigned)b >> i) & 1u) != 0u);
    }
    m->sclk = false;
}

void dacm_pins(dac_model_t *m, bool sclk, bool mosi)
{
    if (m->sclk && !sclk) {
        dacm_clock(m, mosi);
    }
    m->sclk = sclk;
}

int32_t dacm_effective(const dac_model_t *m, int ch)
{
    if (ch < 0 || ch > 1) {
        return -1;
    }
    switch (m->dac_pd[ch] & DACM_CTRL_PD_MASK) {
    case 0u:
        return (int32_t)m->dac[ch];
    case 3u:
        return -1;
    default:
        return 0;
    }
}
