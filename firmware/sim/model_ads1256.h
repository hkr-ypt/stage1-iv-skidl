/*
 * model_ads1256.h — register- and timing-level behavioural model of the
 * ADS1256 (TI SBAS288K) as wired on this board, used by the unit tests and
 * the whole-firmware simulator.
 *
 * The model is driven byte by byte with explicit timestamps (the caller knows
 * the SCLK rate) and ASSERTS the datasheet timing it can observe, counting
 * violations instead of aborting, so tests can require zero violations:
 *   t1   SCLK period >= 4 tau (520.8 ns)          -> v.sclk
 *   t6   >= 50 tau (6.51 us) from the end of RDATA/RREG (incl. its count
 *        byte) to the first data-byte clock        -> v.t6
 *   t11  >= 4 tau after WREG/RREG/RDATA, >= 24 tau after SYNC/WAKEUP, before
 *        the next command byte starts              -> v.t11
 *   t10  /CS held >= 8 tau after the last byte      -> v.t10
 *   data read while /DRDY is high (update window or no data yet)
 *                                                   -> v.read_not_ready
 *   a command sent while a calibration/reset is in progress -> v.busy
 *
 * Conversion behaviour:
 *   - SYNC stops conversions; WAKEUP starts them. The first result after
 *     WAKEUP is ready t18(DR) later; then one every 1/DR (continuous).
 *   - Each update drives /DRDY high for t17 (16 tau) then low.
 *   - Reading all 24 data bits drives /DRDY high until the next update.
 *   - RDATA returns the last completed conversion.
 *   - Result = (V(AINp) - V(AINn)) * PGA / (2*VREF) * (2^23 - 1), clipped,
 *     plus optional gaussian noise; inputs come from a callback.
 *   - SELFCAL: /DRDY high for the Table 21 duration, then low.
 *   - RESET pin low >= 4 tau or RESET command: registers to their reset values
 *     (STATUS 0x30 | DRDY, MUX 0x01, ADCON 0x20 — CLK bits kept by the
 *     command, restored by the pin —, DRATE 0xF0, IO 0xE0), then an automatic
 *     self-calibration.
 *   - ID nibble reads as 3.
 * Fault injection: stuck DOUT (low/high), /DRDY never falls, spontaneous
 * reset, noise.
 */
#ifndef MODEL_ADS1256_H
#define MODEL_ADS1256_H

#include <stdbool.h>
#include <stdint.h>

/* Pin voltage for AIN0..AIN7 (0..7) and AINCOM (8) at time t. */
typedef double (*adsm_input_fn)(void *ctx, int ain, uint64_t t_us);

typedef struct {
    uint32_t sclk, t6, t11, t10, read_not_ready, busy, unknown_cmd, short_read;
} adsm_violations_t;

typedef enum { ADSM_STUCK_NONE = 0, ADSM_STUCK_LOW, ADSM_STUCK_HIGH } adsm_stuck_t;

typedef struct adsm adsm_t;   /* opaque; allocate with adsm_new() */

adsm_t  *adsm_new(adsm_input_fn fn, void *ctx);
void     adsm_free(adsm_t *m);
void     adsm_power_on(adsm_t *m, uint64_t now_us);
void     adsm_reset_pin(adsm_t *m, bool high, uint64_t now_us);
void     adsm_cs(adsm_t *m, bool asserted, uint64_t now_us);
/* One byte on SPI. t_start_us/t_end_us: first and last SCLK of the byte
 * (the caller derives them from its SCLK rate; period = (t_end-t_start)/7.5).
 * Returns the MISO byte. */
uint8_t  adsm_byte(adsm_t *m, uint8_t mosi, double t_start_us, double t_end_us);
/* Advance internal time (conversions, calibration, /DRDY). */
void     adsm_advance(adsm_t *m, uint64_t now_us);
bool     adsm_drdy_low(const adsm_t *m);
/* Report the first /DRDY falling edge since the previous call, if any. */
bool     adsm_take_drdy_fall(adsm_t *m, uint64_t *t_us);

/* Observation and fault injection. */
void     adsm_get_violations(const adsm_t *m, adsm_violations_t *out);
void     adsm_clear_violations(adsm_t *m);
void     adsm_get_regs(const adsm_t *m, uint8_t regs[11]);
void     adsm_set_noise(adsm_t *m, double sigma_codes, uint32_t seed);
void     adsm_set_stuck(adsm_t *m, adsm_stuck_t s);
void     adsm_set_drdy_dead(adsm_t *m, bool dead);
void     adsm_spontaneous_reset(adsm_t *m, uint64_t now_us);
uint32_t adsm_conversions(const adsm_t *m);
uint32_t adsm_selfcals(const adsm_t *m);

#endif /* MODEL_ADS1256_H */
