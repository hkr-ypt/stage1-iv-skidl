/*
 * dac8552.c — the only code that talks to U4 (DAC8552) on SPI1; implements
 * dac8552.h.
 *
 * Datasheet facts relied on (TI SLAS430A):
 *  - Fig. 43 / Table 1 (p.14): 24-bit frame, control byte DB23..DB16 =
 *    0 0 LDB LDA X BufSel PD1 PD0, then 16 data bits, MSB first.
 *    0x00 = write buffer A, no load, PD 00; 0x34 = write buffer B and load
 *    DAC A and DAC B, PD 00 (Example 1, p.16: the simultaneous update).
 *  - p.13 / p.4 pin 5: DIN is latched on SCLK falling edges (SPI mode 1:
 *    CPOL 0, CPHA 1); the frame executes on the 24th falling edge.
 *  - p.15 "SYNC interrupt": /SYNC rising before the 24th falling edge
 *    discards the frame. Hence /SYNC is raised only after port_dac_write()
 *    has returned, i.e. after SPI1 BSY == 0, and the robust zero raises it
 *    on purpose to abort any partial frame.
 *  - p.5 timing: t8 /SYNC high >= 33 ns, t9 24th edge to next /SYNC fall
 *    >= 100 ns: a 1 us delay after every frame covers both.
 *  - p.15 "Power-on reset" / no readback: the driver keeps a shadow of what
 *    it last sent successfully; dac_scrub() re-sends it.
 *
 * Board/spec facts (docs/firmware/firmware_spec.md §4.3, §7.4): B must be
 * 0xFFFF for the positive output bound, so the production frame builder has
 * no B or PD parameter. dac_zero_safe() is FAULT-SAFE: it calls only
 * port_hb_force_low, port_dac_wait_idle, port_dac_spi_reinit, port_dac_cs,
 * port_dac_write, port_delay_us and port_dac_bitbang_* (no port_now_us, no
 * interrupts), and is bounded to about 170 us with the timeouts below
 * (20 + 2 x (20 + 1) + 1 us SPI attempts, 2 x 48 x 1 us bit-banged half
 * periods plus framing delays).
 *
 * Shadow rules: dac_update/dac_scrub change the shadow only on success;
 * dac_zero_safe always ends with the shadow (0x8000, 0xFFFF) valid. While
 * the shadow is unknown dac_shadow_a() reads DAC_CODE_ZERO_NOMINAL and
 * dac_shadow_b() DAC_B_FULLSCALE (only dac_shadow_valid() says it is known).
 * Hardening beyond the five documented steps: if dac_zero_safe() preempts a
 * running bit-bang (a fault handler or the dead-man interrupting the
 * thread-mode FAULT entry), PA5/PA7 are still GPIOs and SPI1 would clock
 * into nothing; s_bb_active detects that and port_dac_bitbang_end() hands
 * the pins back to SPI1 (with /SYNC already high) before step 4.
 *
 * Bring-up DAC.RAW (FW_BRINGUP only): dac_raw_write() makes the shadow
 * (a, b) and sets dac_raw_active(); a successful dac_update() or any
 * dac_zero_safe()/dac_init() clears it. While it is active dac_scrub() does
 * nothing (spec §8.4: DAC.RAW suspends the scrub). Its SPI timeouts are
 * counted in stats.failures like those of dac_update/dac_scrub.
 */
#include "dac8552.h"

#include "port.h"

#define DAC_FRAME_BYTES          3u
#define DAC_SYNC_HIGH_US         1u   /* >= t8 / t9, and >= 1 us abort pulse */
#define DAC_ZS_IDLE_TIMEOUT_US   20u  /* a 3-byte frame takes 6 us at 4 MHz  */
#define DAC_ZS_WRITE_TIMEOUT_US  20u
#define DAC_BITBANG_HALF_US      1u

static struct {
    bool        valid;
    uint16_t    a;
    uint16_t    b;
    dac_stats_t stats;
#if FW_BRINGUP
    bool        raw_active;
#endif
} s_dac;

/* PA5/PA7 are GPIOs (between port_dac_bitbang_begin and _end). */
static volatile bool s_bb_active;

void dac8552_frames(uint16_t code_a, uint8_t out[6])
{
    out[0] = DAC_CTRL_WRITE_A;
    out[1] = (uint8_t)(code_a >> 8);
    out[2] = (uint8_t)(code_a & 0xFFu);
    out[3] = DAC_CTRL_WRITE_B_LOAD;
    out[4] = (uint8_t)(DAC_B_FULLSCALE >> 8);
    out[5] = (uint8_t)(DAC_B_FULLSCALE & 0xFFu);
}

void dac_init(void)
{
    s_dac.valid = false;
    s_dac.a = DAC_CODE_ZERO_NOMINAL;
    s_dac.b = DAC_B_FULLSCALE;
    s_dac.stats = (dac_stats_t){0};
#if FW_BRINGUP
    s_dac.raw_active = false;
#endif
    s_bb_active = false;
}

/* One frame framed by /SYNC; /SYNC rises only after the write returned
 * (BSY == 0 or timeout) and stays high >= 1 us before anything else. */
static bool dac_send_frame(const uint8_t *frame, uint32_t timeout_us)
{
    port_dac_cs(true);
    bool ok = port_dac_write(frame, DAC_FRAME_BYTES, timeout_us);
    port_dac_cs(false);
    port_delay_us(DAC_SYNC_HIGH_US);
    return ok;
}

static bool dac_send_pair(const uint8_t frames[6])
{
    if (!dac_send_frame(&frames[0], DAC_SPI_TIMEOUT_US)) {
        return false;
    }
    return dac_send_frame(&frames[3], DAC_SPI_TIMEOUT_US);
}

bool dac_update(uint16_t code_a)
{
    uint8_t frames[6];
    dac8552_frames(code_a, frames);
    if (!dac_send_pair(frames)) {
        s_dac.stats.failures++;
        return false;
    }
    s_dac.valid = true;
    s_dac.a = code_a;
    s_dac.b = DAC_B_FULLSCALE;
#if FW_BRINGUP
    s_dac.raw_active = false;
#endif
    s_dac.stats.updates++;
    return true;
}

bool dac_scrub(void)
{
    if (!s_dac.valid) {
        return true;
    }
#if FW_BRINGUP
    if (s_dac.raw_active) {
        return true;
    }
#endif
    uint8_t frames[6];
    dac8552_frames(s_dac.a, frames);
    if (!dac_send_pair(frames)) {
        s_dac.stats.failures++;
        return false;
    }
    s_dac.stats.scrubs++;
    return true;
}

void dac_get_stats(dac_stats_t *out)
{
    *out = s_dac.stats;
}

bool dac_shadow_valid(void)
{
    return s_dac.valid;
}

uint16_t dac_shadow_a(void)
{
    return s_dac.a;
}

uint16_t dac_shadow_b(void)
{
    return s_dac.b;
}

/* ---- robust zero (FAULT-SAFE) --------------------------------------------- */

/* One 24-bit frame on PA5/PA7 in mode 1: per bit, DIN is set while SCLK is
 * low, SCLK rises (DIN stable), then falls (the DAC latches DIN). DIN never
 * changes within a high phase or at a falling edge. */
static void dac_bitbang_frame(const uint8_t *frame)
{
    port_dac_cs(true);
    port_delay_us(DAC_BITBANG_HALF_US);
    for (unsigned i = 0; i < DAC_FRAME_BYTES; i++) {
        for (int bit = 7; bit >= 0; bit--) {
            bool d = ((frame[i] >> bit) & 1u) != 0u;
            port_dac_bitbang_pins(false, d);
            port_dac_bitbang_pins(true, d);
            port_delay_us(DAC_BITBANG_HALF_US);
            port_dac_bitbang_pins(false, d);
            port_delay_us(DAC_BITBANG_HALF_US);
        }
    }
    port_dac_cs(false);
    port_delay_us(DAC_SYNC_HIGH_US);
}

void dac_zero_safe(void)
{
    uint8_t frames[6];
    dac8552_frames(DAC_CODE_ZERO_NOMINAL, frames);

    /* 1. The heartbeat stops first (the gate starts to decay). */
    port_hb_force_low();

    /* 2. /SYNC high >= 1 us: a partial frame (< 24 bits) is discarded
     * (SLAS430A: /SYNC high before the 24th falling edge aborts the write).
     * Raised before any SPI1 reset, so that no SCLK edge the reset may cause
     * can reach the DAC's shift register. */
    port_dac_cs(false);
    port_delay_us(DAC_SYNC_HIGH_US);

    /* 3. SPI1 idle, else an RCC reset (the port never clears SPE while BSY). */
    bool spi_ok = port_dac_wait_idle(DAC_ZS_IDLE_TIMEOUT_US);
    if (!spi_ok) {
        port_dac_spi_reinit();
    }
    if (s_bb_active) {
        port_dac_bitbang_end(); /* we preempted a bit-bang: pins back to AF5 */
        s_bb_active = false;
    }

    /* 4. The zero pair over SPI1. */
    if (!dac_send_frame(&frames[0], DAC_ZS_WRITE_TIMEOUT_US) ||
        !dac_send_frame(&frames[3], DAC_ZS_WRITE_TIMEOUT_US)) {
        spi_ok = false;
    }

    /* 5. Fallback: the same pair bit-banged on PA5/PA7 (/SYNC is high here,
     * so the pin switch-over cannot clock a bit into the DAC). */
    if (!spi_ok) {
        s_bb_active = true;
        port_dac_bitbang_begin();
        port_delay_us(DAC_BITBANG_HALF_US);
        dac_bitbang_frame(&frames[0]);
        dac_bitbang_frame(&frames[3]);
        port_dac_bitbang_end();
        s_bb_active = false;
        s_dac.stats.zero_bitbang++;
    }

    s_dac.valid = true;
    s_dac.a = DAC_CODE_ZERO_NOMINAL;
    s_dac.b = DAC_B_FULLSCALE;
#if FW_BRINGUP
    s_dac.raw_active = false;
#endif
    s_dac.stats.zero_safe++;
}

/* ---- bring-up only ---------------------------------------------------------- */
#if FW_BRINGUP
bool dac_raw_write(uint16_t code_a, uint16_t code_b)
{
    const uint8_t frames[6] = {
        DAC_CTRL_WRITE_A,
        (uint8_t)(code_a >> 8), (uint8_t)(code_a & 0xFFu),
        DAC_CTRL_WRITE_B_LOAD,
        (uint8_t)(code_b >> 8), (uint8_t)(code_b & 0xFFu),
    };
    if (!dac_send_pair(frames)) {
        s_dac.stats.failures++;
        return false;
    }
    s_dac.valid = true;
    s_dac.a = code_a;
    s_dac.b = code_b;
    s_dac.raw_active = true;
    return true;
}

bool dac_raw_active(void)
{
    return s_dac.raw_active;
}
#endif
