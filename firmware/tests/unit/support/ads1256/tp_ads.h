/*
 * tp_ads.h — test port for test_ads1256: implements the port.h calls used by
 * drivers/ads1256.c on top of the ADS1256 model (sim/model_ads1256.h) with a
 * VIRTUAL clock in whole microseconds.
 *
 *  - port_adc_xfer: bytes back to back at SCLK 1.5 MHz (8 / 1.5 = 5.333 us per
 *    byte; first SCLK at the slot start, last at +7.5 periods), each passed to
 *    adsm_byte() with its t_start/t_end so the model checks t1/t6/t11; the
 *    clock then moves to the next whole microsecond after the last byte.
 *  - port_delay_us advances the clock.
 *  - EXTI emulation of PA8: every model /DRDY falling edge sets "pending";
 *    port_adc_drdy_arm() clears pending (unless the fault knob says it does
 *    not) and unmasks; the first edge after that is latched with its time
 *    (the ISR masks itself and clears pending). With pending set at unmask,
 *    the ISR fires at once with the unmask time (as on the real EXTI).
 *  - After every clock advance the model is advanced and its edge report is
 *    collected (adsm_take_drdy_fall).
 */
#ifndef TP_ADS_H
#define TP_ADS_H

#include <stdbool.h>
#include <stdint.h>

#include "model_ads1256.h"

typedef struct {
    uint32_t cs_asserts;           /* transactions                              */
    uint32_t xfers;
    uint32_t xfer_without_cs;
    uint32_t max_txn_per_service;  /* over tp_service() calls                   */
    uint64_t max_service_us;       /* virtual time inside one ads_service()     */
    uint32_t max_delay_arg;        /* largest port_delay_us() argument          */
    bool     cs_asserted;
    uint32_t reset_pulses;
    uint64_t min_reset_low_us;
    uint32_t arms;
    uint32_t spi_fail_injected;
} tp_obs_t;

void     tp_setup(adsm_input_fn fn, void *ctx);   /* fresh model, power-on at t=0 */
void     tp_teardown(void);
adsm_t  *tp_model(void);
uint64_t tp_now(void);
void     tp_advance(uint64_t us);
/* Random extra time per main-loop pass, uniform in [lo, hi] us. */
void     tp_set_pass(uint32_t lo_us, uint32_t hi_us, uint32_t seed);
/* One main-loop pass: ads_service() (instrumented) + the random pass time. */
void     tp_pass(void);
/* ads_service() only (instrumented), no extra time. */
void     tp_service(void);
uint32_t tp_rand(uint32_t lo, uint32_t hi);

/* Faults / knobs */
void     tp_fail_xfers(uint32_t n);               /* next n transfers time out  */
void     tp_set_arm_clears_pending(bool clears);  /* false: EXTI PR not cleared */
void     tp_inject_edge(void);                    /* glitch on /DRDY now        */
/* interrupt emulation: delay before the RDATA data bytes (preemption mid-read)
 * and between a /DRDY edge and its latched timestamp (EXTI latency) */
void     tp_set_read_preempt_us(uint32_t us);
void     tp_set_drdy_latency_us(uint32_t us);
bool     tp_edge_latched(uint64_t *t);            /* peek at the EXTI latch     */
void     tp_observe(tp_obs_t *o);

#endif /* TP_ADS_H */
