/*
 * port_sim.h — the simulation implementation of port.h, plus the control API
 * used by system tests (tests/unit/test_sys_*.c) and the simulator executable
 * (sim_main.c).
 *
 * Time is virtual and deterministic: port_now_us() returns the simulated
 * clock. It advances by
 *   - port_delay_us(us),
 *   - the duration of every SPI transfer (bytes * 8 / SCLK: SPI1 6 MHz,
 *     SPI2 1.5 MHz) plus fixed per-call overheads,
 *   - a configurable per-pass cost added in port_pass_end(),
 *   - explicit psim_advance_us() calls from a test.
 * The simulator executable additionally paces virtual time to the wall clock
 * (speed factor) so a real host program can talk to it over a pty.
 *
 * MCU reset vs power cycle: psim_mcu_reset() resets only what an MCU reset
 * resets (port state, RAM-side flags); the DAC model keeps its codes, the
 * charge pump keeps its charge, the ADC keeps running, the calibration flash
 * keeps its content and the .noinit crash record survives. psim_power_on()
 * resets everything.
 */
#ifndef PORT_SIM_H
#define PORT_SIM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "fw_types.h"
#include "model_dac8552.h"
#include "model_ads1256.h"
#include "model_board.h"

typedef struct {
    uint32_t pass_cost_us;    /* virtual time added per loop pass (default 20) */
    uint32_t bor_level;       /* option-byte BOR level (default 3)              */
    uint32_t vbus_mv;         /* default 5000                                   */
    bool     configured;      /* default true                                   */
    bool     dtr;             /* default true                                   */
    double   adc_noise_codes; /* default 2.0                                    */
    uint32_t seed;
} psim_options_t;

void          psim_default_options(psim_options_t *o);
void          psim_power_on(const psim_options_t *o);
void          psim_mcu_reset(void);
uint64_t      psim_time_us(void);
void          psim_advance_us(uint64_t us);
void          psim_set_pass_cost_us(uint32_t us);

dac_model_t   *psim_dac(void);
adsm_t        *psim_ads(void);
board_model_t *psim_board(void);

/* Host link: bytes the firmware wrote / bytes delivered to the firmware. */
size_t        psim_host_take_tx(uint8_t *buf, size_t max);
void          psim_host_give_rx(const uint8_t *buf, size_t n);
void          psim_set_vbus_mv(uint32_t mv);
void          psim_set_configured(bool c);
void          psim_set_dtr(bool d);
bool          psim_host_connected(void);   /* last port_host_connect() value */
void          psim_set_tx_space_limit(size_t bytes); /* emulate a stalled host */

/* System knobs. */
void          psim_set_bor_level(int lvl);
void          psim_set_dbg_tim2_frozen(bool f);
void          psim_set_stack_ok(bool ok);
void          psim_set_hse_ok(bool ok);

/* Fault injection on the SPI transports (next n transfers time out). */
void          psim_fail_spi1(uint32_t n);
void          psim_fail_spi2(uint32_t n);

/* Observation. */
typedef struct {
    uint64_t hb_edges;
    uint64_t hb_last_edge_us;
    bool     hb_level;
    uint32_t iwdg_kicks;
    uint32_t deadman_reloads;
    bool     deadman_enabled;
    uint32_t resets_requested;   /* port_system_reset() calls           */
    bool     led;
    uint32_t cal_erases, cal_programs;
} psim_obs_t;
void          psim_observe(psim_obs_t *o);

/* Calibration flash image (2 sectors) — tests may corrupt it. */
uint8_t      *psim_cal_sector_mut(unsigned idx);
/* Make the next erase or program fail part-way (power-loss emulation):
 * the sector is left partly written. */
void          psim_cal_tear_next(void);

#endif /* PORT_SIM_H */
