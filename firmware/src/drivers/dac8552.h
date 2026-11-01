/*
 * dac8552.h — the only code that talks to U4 (DAC8552, TI SLAS430A) on SPI1.
 *
 * Board facts (blocks/dac_sweep.py): channel A sweeps, channel B sets the
 * midpoint, V_SWEEP = 8.0321 * VREF_DAC * (code_A - code_B/2) / 65536. The
 * positive output bound holds only while B = 0xFFFF; nothing in hardware
 * enforces it. Therefore:
 *
 *  - every update is the SLAS430A "Example 1" pair: `00 Ah Al` (write buffer
 *    A, load nothing) then `34 FF FF` (write buffer B = 0xFFFF, load DAC A and
 *    DAC B together). Channel B and the power-down bits are constants, not
 *    parameters, so no production call can produce B != 0xFFFF or PD != 00.
 *  - /SYNC (PA4) is raised only after SPI BSY == 0; raising it earlier makes
 *    the DAC discard the frame (SLAS430A p.15). /SYNC toggles between the two
 *    frames (t8 >= 33 ns, t9 >= 100 ns).
 *  - the DAC has no readback; the driver keeps a shadow of what it last sent
 *    successfully, and dac_scrub() re-sends it.
 */
#ifndef DAC8552_H
#define DAC8552_H

#include "fw_types.h"

#define DAC_B_FULLSCALE        0xFFFFu
#define DAC_CODE_ZERO_NOMINAL  0x8000u
#define DAC_CTRL_WRITE_A       0x00u   /* write buffer A, load nothing        */
#define DAC_CTRL_WRITE_B_LOAD  0x34u   /* write buffer B, load DAC A and B   */
#define DAC_SPI_TIMEOUT_US     50u

/* Pure: build the 6-byte update pair for code_a. out = {00, Ah, Al, 34, FF, FF}. */
void dac8552_frames(uint16_t code_a, uint8_t out[6]);

/* Reset the shadow to "unknown" (call once at init after the early zero). */
void dac_init(void);

/* Send the pair for code_a. On success the shadow becomes (code_a, 0xFFFF) and
 * true is returned. On an SPI timeout /SYNC is released, the shadow is
 * unchanged, and false is returned (the caller latches F_DAC_SPI). */
bool dac_update(uint16_t code_a);

/* Re-send the shadow pair (no-op returning true if the shadow is unknown). */
bool dac_scrub(void);

typedef struct {
    uint32_t updates;       /* successful dac_update() calls          */
    uint32_t scrubs;        /* successful dac_scrub() calls           */
    uint32_t failures;      /* SPI timeouts in dac_update/dac_scrub   */
    uint32_t zero_safe;     /* dac_zero_safe() calls                  */
    uint32_t zero_bitbang;  /* of which needed the bit-bang fallback  */
} dac_stats_t;
void     dac_get_stats(dac_stats_t *out);

bool     dac_shadow_valid(void);
uint16_t dac_shadow_a(void);
uint16_t dac_shadow_b(void);          /* 0xFFFF unless the bring-up DAC.RAW ran */

/*
 * Robust zero, used by FAULT entry, the fault handlers and the dead-man ISR.
 * FAULT-SAFE: uses only FAULT-SAFE port calls, bounded to ~200 us.
 *   1. PC5 low (port_hb_force_low).
 *   2. Wait for SPI1 idle; on timeout reset SPI1 (never clear SPE while busy).
 *   3. /SYNC high for >= 1 us: aborts any half-sent frame (< 24 bits).
 *   4. Send 00 80 00 then 34 FF FF, each framed by /SYNC.
 *   5. If 2 or 4 failed: bit-bang the same frames on PA5/PA7 in mode 1.
 * Afterwards the DAC holds A = 0x8000, B = 0xFFFF and the shadow says so.
 */
void dac_zero_safe(void);

#if FW_BRINGUP
/* Bring-up only (DAC.RAW): send an arbitrary A/B pair. Breaks the positive
 * output bound when b < 0xFFFF. Suspends the B invariant until dac_update()
 * or dac_zero_safe() runs again. */
bool dac_raw_write(uint16_t code_a, uint16_t code_b);
bool dac_raw_active(void);
#endif

#endif /* DAC8552_H */
