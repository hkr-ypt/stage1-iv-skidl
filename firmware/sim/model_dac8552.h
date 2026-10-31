/*
 * model_dac8552.h — bit-level behavioural model of the DAC8552 serial
 * interface (TI SLAS430A), used by the unit tests and the whole-firmware
 * simulator.
 *
 * Modelled behaviour:
 *  - /SYNC falling edge starts a frame; data is latched on SCLK falling
 *    edges, MSB first; the 24th falling edge executes the frame immediately;
 *    further edges are ignored until /SYNC rises and falls again.
 *  - /SYNC rising before the 24th edge discards the frame (counted).
 *  - Control byte DB23..DB16 = 0 0 LDB LDA X BufSel PD1 PD0 (Table 1):
 *    the data goes to buffer A or B (BufSel), PD bits are stored with it,
 *    LDA/LDB load DAC A/B from their buffers (with their PD state).
 *    DB23/DB22 != 0 is counted as a reserved-bit violation.
 *  - Power-on: DAC registers and buffers zero, outputs at zero scale, PD 00.
 *    An MCU reset does NOT reset the model (the real DAC keeps its codes).
 *  - Output: effective code of a powered-down channel is reported as 0
 *    (1 k / 100 k to GND) or -1 (high impedance) by dacm_effective().
 */
#ifndef MODEL_DAC8552_H
#define MODEL_DAC8552_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    /* interface state */
    bool     sync_low;
    bool     sclk;          /* last SCLK level seen on the bit-bang path */
    bool     frame_done;    /* 24 bits received in this /SYNC-low period */
    uint32_t shift;
    uint8_t  nbits;
    /* registers */
    uint16_t buf[2];        /* [0] = A, [1] = B */
    uint8_t  buf_pd[2];
    uint16_t dac[2];
    uint8_t  dac_pd[2];
    /* statistics for tests */
    uint32_t frames_executed;
    uint32_t frames_aborted;      /* /SYNC rose before 24 bits           */
    uint32_t reserved_violations; /* DB23/DB22 set                        */
    uint32_t loads;               /* frames that loaded at least one DAC  */
    uint32_t b_not_fullscale_loads; /* loads that left DAC B != 0xFFFF    */
    uint32_t pd_nonzero_frames;
} dac_model_t;

void     dacm_power_on(dac_model_t *m);
void     dacm_sync(dac_model_t *m, bool low);
/* Hardware SPI path: one byte clocked in (8 falling edges, MSB first). */
void     dacm_byte(dac_model_t *m, uint8_t b);
/* Bit-bang path: pin levels; data latched on each SCLK falling edge. */
void     dacm_pins(dac_model_t *m, bool sclk, bool mosi);
/* Effective output code of channel ch (0 = A, 1 = B); -1 if high impedance. */
int32_t  dacm_effective(const dac_model_t *m, int ch);

#endif /* MODEL_DAC8552_H */
