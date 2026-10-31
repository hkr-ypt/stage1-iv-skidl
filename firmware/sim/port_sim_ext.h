/*
 * port_sim_ext.h — simulator-private additions to port_sim.h (owned by the
 * sim agent; candidates for merging into port_sim.h).
 *
 * Reset and hang emulation. On the target port_system_reset() never returns
 * (NVIC_SystemReset, ARM DDI 0403 B3.2.6 AIRCR.SYSRESETREQ). In the
 * simulator it cannot stop the C call stack, so it only RECORDS a reset
 * request and returns. From that moment the emulated CPU is "stopped": every
 * thread-mode port call with a side effect (PC5, SPI1, SPI2, host TX, flash,
 * LED, dead-man, IWDG kick in port_pass_end) is ignored, so the rest of the
 * pass that requested the reset cannot disturb the models. The owner of the
 * loop (sim_main.c or a system test) performs the reset:
 *
 *     app_pass();
 *     if (psim_reset_requested()) { psim_mcu_reset(); app_init(); }
 *
 * The same applies to a hang (port_crash_test(1) / (2)): psim_hung() is true,
 * the loop owner stops calling app_pass() and only advances time
 * (psim_advance_us), during which the emulated dead-man (if enabled and the
 * hang is not "interrupts off") or the IWDG requests the reset.
 */
#ifndef PORT_SIM_EXT_H
#define PORT_SIM_EXT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "port_sim.h"

/* psim_set_tx_space_limit() argument meaning "no limit" (the 64 KB ring). */
#define PSIM_TX_NO_LIMIT  SIZE_MAX
/* Host link ring capacity (each direction). */
#define PSIM_HOST_RING_BYTES 65536u
/* Emulated IWDG timeout: prescaler /32, reload 1500 at LSI max 47 kHz
 * (DS10314 Table 40) = 1.021 s, the shortest the real part can give. */
#define PSIM_IWDG_TIMEOUT_US 1020000u
/* Flash timing, PSIZE x32 typical (DS10314 Table 45). */
#define PSIM_FLASH_ERASE_US  250000u
#define PSIM_FLASH_WORD_US   16u

/* Reset / hang state (see above). */
bool     psim_reset_requested(void);
uint32_t psim_pending_reset_cause(void);   /* RST_SFT or RST_IWDG, 0 if none */
bool     psim_hung(void);
bool     psim_cpu_stopped(void);           /* reset requested or hung        */

/* Power cycle that keeps the non-volatile and physical state: the flash
 * content, the BOR option level, the board configuration (DUT, rails,
 * leads, limits), the host-side flags and the pass cost. Everything else is
 * as after psim_power_on() (virtual time restarts at 0, the pump is empty,
 * the DAC registers are zero, the crash record is lost, reset flags POR). */
void     psim_power_cycle(void);

/* Emulate a stuck service: the next pass advances virtual time by `us`
 * inside the pass (in port_usb_service(), or in port_pass_end() before the
 * dead-man reload if the pass made no port_usb_service() call). The stall
 * ends early if a reset is requested meanwhile (e.g. by the dead-man). */
void     psim_stall_next_pass(uint64_t us);

void     psim_set_in_exception(bool on);   /* port_in_exception() result      */
void     psim_set_mcu_temp_cdeg(int32_t cdeg);

/* Host link helpers for the pty pump in sim_main.c. */
size_t   psim_host_rx_space(void);
size_t   psim_host_tx_pending(void);

/* Extra observation. */
typedef struct {
    uint64_t passes;             /* port_pass_begin() calls                 */
    uint32_t mcu_resets;         /* psim_mcu_reset() calls since power-on   */
    uint32_t reset_flags;        /* what port_reset_flags() returns         */
    uint32_t deadman_fires;
    uint32_t iwdg_fires;
    uint32_t hardfaults;         /* port_crash_test(0)                      */
    bool     reset_requested;
    bool     hung;
    bool     hung_irqoff;
    uint32_t spin_advances;      /* 1 us steps forced by busy-poll loops    */
    uint32_t delay_over_50us;    /* port_delay_us() calls above its bound   */
    uint64_t spi1_bytes, spi2_bytes;
    uint32_t spi1_failures, spi2_failures;
    uint32_t exti_captures;      /* /DRDY falls latched while armed         */
    uint64_t rx_dropped;         /* psim_host_give_rx() bytes not accepted  */
    uint64_t tx_discarded;       /* written while not configured / stopped  */
    uint32_t cal_tears;
    uint64_t stall_us_total;
} psim_ext_obs_t;
void     psim_observe_ext(psim_ext_obs_t *o);

#endif /* PORT_SIM_EXT_H */
