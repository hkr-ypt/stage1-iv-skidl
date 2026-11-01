/*
 * dac_test_port.h — minimal test implementation of the port.h calls used by
 * drivers/dac8552.c (SPI1 -> DAC8552, PC5 force-low, fake clock), routing the
 * transport into the bit-level model sim/model_dac8552.c, with fault
 * injection and observation for tests/unit/test_dac8552.c.
 *
 * Transport model (what the STM32 port does, SLAS430A p.13-15 semantics):
 *  - port_dac_cs(asserted) drives /SYNC: dacm_sync(low = asserted).
 *  - port_dac_write(): each byte is clocked into the model (8 falling edges)
 *    unless PA5/PA7 are in bit-bang (GPIO) mode, in which case the SPI
 *    peripheral reaches no pin and the bytes are lost. Cost: ceil(n * 8 / 6)
 *    us at 6 MHz plus 1 us overhead. A failing write clocks the first
 *    `write_fail_bits` bits of the data (0..24, a BSY timeout after a partial
 *    or complete frame) and costs `timeout_us`.
 *  - port_dac_wait_idle() fails (costing timeout_us) while `bsy_stuck`.
 *  - port_dac_spi_reinit() costs 2 us; with `reinit_heals` it clears
 *    `bsy_stuck` and `spi_dead`.
 *  - bit-bang pins: SCLK/MOSI go straight to dacm_pins(); entering and
 *    leaving bit-bang mode drives SCLK low (SPI idle level, CPOL 0).
 * Observation: an event log, every frame the model executed (control byte,
 * data, which path), protocol violations (write without /SYNC, MOSI change
 * while SCLK is high or at a falling edge, pins driven outside bit-bang
 * mode, delays > 50 us), /SYNC high-time minimum, and port_now_us() calls
 * (port_now_us is not FAULT-SAFE: dac_zero_safe must not call it).
 */
#ifndef DAC_TEST_PORT_H
#define DAC_TEST_PORT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "model_dac8552.h"

typedef enum {
    DEV_HB_LOW = 1,
    DEV_CS_ASSERT,
    DEV_CS_RELEASE,
    DEV_WRITE_OK,
    DEV_WRITE_FAIL,
    DEV_DELAY,
    DEV_WAIT_IDLE_OK,
    DEV_WAIT_IDLE_FAIL,
    DEV_REINIT,
    DEV_BB_BEGIN,
    DEV_BB_PINS,
    DEV_BB_END,
    DEV_NOW,
} dtp_ev_kind_t;

typedef struct {
    dtp_ev_kind_t kind;
    uint64_t      t;
    uint32_t      arg;
} dtp_event_t;

typedef struct {
    uint8_t  ctrl;
    uint16_t data;
    bool     bitbang;
} dtp_frame_t;

#define DTP_MAX_EVENTS 1024u
#define DTP_MAX_FRAMES 256u

typedef struct {
    dac_model_t dac;
    uint64_t    now;
    bool        cs;          /* /SYNC low */
    bool        bitbang;     /* PA5/PA7 are GPIOs */
    bool        sclk, mosi;  /* GPIO levels while bit-banging */

    /* ---- fault injection ---- */
    uint32_t    write_calls;      /* port_dac_write calls so far (0-based index of the next) */
    uint64_t    write_fail_mask;  /* bit k: write call k fails (k < 64) */
    uint8_t     write_fail_bits;  /* bits clocked before a failing write times out */
    bool        spi_dead;         /* every write fails */
    bool        bsy_stuck;        /* wait_idle fails */
    bool        reinit_heals;     /* port_dac_spi_reinit clears spi_dead/bsy_stuck */
    /* called on the n-th port_dac_bitbang_pins call (1-based), once */
    uint32_t    pins_hook_at;
    void      (*pins_hook)(void);

    /* ---- observation ---- */
    uint32_t    hb_force_low_calls;
    uint32_t    now_calls;
    uint32_t    wait_idle_calls, reinit_calls;
    uint32_t    bb_begin_calls, bb_end_calls, bb_pins_calls;
    uint32_t    write_ok, write_fail;
    uint32_t    cs_falls, cs_rises;
    uint32_t    delay_calls;
    uint64_t    delay_total_us;
    /* violations */
    uint32_t    write_without_cs;
    uint32_t    write_during_bitbang;
    uint32_t    pins_outside_bitbang;
    uint32_t    mosi_change_while_high;
    uint32_t    delay_over_50;
    uint32_t    writes_in_one_sync_max; /* most writes inside one /SYNC-low period */
    uint32_t    writes_this_sync;
    /* /SYNC high time between a rise and the next fall (fake us) */
    bool        have_rise;
    uint64_t    last_rise_t;
    uint64_t    min_sync_high_us;
    /* executed frames */
    dtp_frame_t frames[DTP_MAX_FRAMES];
    uint32_t    nframes;          /* total (the array keeps the first DTP_MAX_FRAMES) */
    uint32_t    ctrl_count[256];
    uint32_t    frames_34_not_ffff; /* 0x34 frames whose data is not 0xFFFF */
    uint32_t    order_violations;   /* a 0x00 frame not immediately followed by 0x34 */
    bool        pending_a;          /* last executed frame was 0x00 */
    /* event log */
    dtp_event_t ev[DTP_MAX_EVENTS];
    uint32_t    nev;
} dtp_t;

extern dtp_t dtp;

/* Power-on model, time = 1000 us, /SYNC high, no injection, clear all. */
void dtp_reset(void);
/* Clear observations (events, frames, counters) but keep model, time and
 * injection settings. */
void dtp_clear_obs(void);
/* Emulate an interrupted transfer: /SYNC is (or stays) low and the first
 * nbits of bytes[] are clocked into the model (hardware SPI path), without
 * logging. If the port /SYNC was high it falls first (new frame). */
void dtp_preload_bits(const uint8_t *bytes, unsigned nbits);
/* Index of the first event of the given kind at or after `from`, or -1. */
int  dtp_find(dtp_ev_kind_t kind, uint32_t from);

#endif /* DAC_TEST_PORT_H */
