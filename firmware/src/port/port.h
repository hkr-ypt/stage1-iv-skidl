/*
 * port.h — the platform interface. Everything above this line (app/, core/,
 * drivers/) is portable C that never touches a register; everything below it
 * is implemented twice:
 *
 *   firmware/port_stm32/  the STM32F411RET6 board (registers, TinyUSB)
 *   firmware/sim/         port_sim.c: virtual time, bit-level DAC and ADC
 *                         models, an analog board model, in-memory host link
 *
 * and the unit tests link the simulation port, so drivers are tested through
 * the same calls the target uses.
 *
 * Rules that both implementations must obey (docs/firmware/architecture.md):
 *  - No function here may block for more than its stated bound.
 *  - port_hb_write() is called only by drivers/heartbeat.c (and the fault
 *    paths via port_hb_force_low()).
 *  - Functions marked FAULT-SAFE must work from a HardFault/NMI/dead-man
 *    handler: no interrupts, no TIM2, no heap, bounded busy-waits only.
 */
#ifndef PORT_H
#define PORT_H

#include "fw_types.h"

/* ---- time ------------------------------------------------------------------ */
uint64_t port_now_us(void);              /* monotonic microseconds, never wraps     */
void     port_delay_us(uint32_t us);     /* busy wait, <= 50 us per call. FAULT-SAFE */

/* True when executing in an exception/interrupt handler (IPSR != 0). */
bool     port_in_exception(void);

/* ---- main-loop hooks ------------------------------------------------------- */
void     port_pass_begin(void);          /* start of every pass (time extension)    */
/* End of every pass: reloads the dead-man timer; kicks the IWDG if `healthy`
 * (the pass finished within budget). */
void     port_pass_end(bool healthy);
/* The dead-man timer is armed only while the heartbeat is permitted. */
void     port_deadman_enable(bool enable);

/* ---- PC5 heartbeat (drivers/heartbeat.c only) -------------------------------- */
void     port_hb_write(bool high);
void     port_hb_force_low(void);        /* FAULT-SAFE */

/* ---- SPI1 -> DAC8552 (write-only) --------------------------------------------- */
void     port_dac_cs(bool asserted);     /* asserted = /SYNC (PA4) low              */
/* Transmit n bytes, mode 1; returns only after SPI1 BSY == 0 (the last bit has
 * left the shift register) or the timeout expired (returns false). */
bool     port_dac_write(const uint8_t *tx, size_t n, uint32_t timeout_us);
/* Primitives for dac_zero_safe(), all FAULT-SAFE: */
bool     port_dac_wait_idle(uint32_t timeout_us); /* TXE == 1 && BSY == 0 */
void     port_dac_spi_reinit(void);       /* RCC reset of SPI1 + reconfigure         */
void     port_dac_bitbang_begin(void);    /* PA5/PA7 -> GPIO outputs, SCLK low       */
void     port_dac_bitbang_pins(bool sclk, bool mosi);
void     port_dac_bitbang_end(void);      /* PA5/PA7 -> AF5 again                    */

/* ---- SPI2 -> ADS1256 ------------------------------------------------------------ */
void     port_adc_cs(bool asserted);     /* asserted = /CS (PB12) low                */
/* Full-duplex transfer of n bytes, mode 1, SCLK 1.5 MHz; rx may be NULL. */
bool     port_adc_xfer(const uint8_t *tx, uint8_t *rx, size_t n, uint32_t timeout_us);
void     port_adc_reset_write(bool high); /* PA9 /RESET level                        */
bool     port_adc_drdy_low(void);        /* PA8 level                                */
/* Clear any latched /DRDY falling edge (EXTI and NVIC pending) and arm capture
 * of the next one. */
void     port_adc_drdy_arm(void);
/* If a falling edge was captured since the last arm: return true, its TIM2
 * timestamp in *t_us, and disarm capture (one edge per arm). */
bool     port_adc_drdy_take(uint64_t *t_us);

/* ---- host link (USB CDC on the target) ---------------------------------------- */
size_t   port_host_read(uint8_t *buf, size_t max);        /* non-blocking          */
size_t   port_host_write(const uint8_t *buf, size_t n);   /* non-blocking, partial */
size_t   port_host_write_space(void);
void     port_host_flush(void);
void     port_usb_service(void);         /* tud_task(), bounded                      */
uint32_t port_vbus_mv(void);             /* latest VBUS estimate (PA0 x 2), mV       */
bool     port_host_configured(void);     /* enumerated and not suspended             */
bool     port_host_dtr(void);            /* CDC line state DTR                       */
void     port_host_connect(bool connect);/* soft connect (D+ pull-up)                */

/* ---- calibration flash (sectors 1 and 2 of the STM32F411) --------------------- */
#define PORT_CAL_SECTORS 2u
size_t         port_cal_sector_size(void);
const uint8_t *port_cal_sector(unsigned idx);              /* memory-mapped read    */
/* Erase stalls the CPU up to 500 ms on the target. Callers ensure DISARMED. */
bool           port_cal_erase(unsigned idx);
bool           port_cal_program(unsigned idx, size_t offset, const uint8_t *data, size_t n);

/* ---- system ---------------------------------------------------------------------- */
void     port_uid(uint32_t uid[3]);
int      port_bor_level(void);           /* 3, 2, 1, or 0 = BOR off                 */
bool     port_dbg_tim2_frozen(void);     /* DBGMCU_APB1_FZ.DBG_TIM2_STOP set        */
uint32_t port_reset_flags(void);         /* RST_* bits captured at boot             */
bool     port_hse_ok(void);              /* running from the HSE/PLL clock          */
bool     port_crash_get(crash_record_t *out);
void     port_crash_clear(void);
int32_t  port_mcu_temp_cdeg(void);       /* centi-degC, INT32_MIN if unavailable     */
void     port_led_write(bool on);        /* PC13: on = pin low                       */
bool     port_stack_ok(void);            /* canary intact                            */
void     port_system_reset(void);        /* never returns                            */
/* Bring-up only: provoke a crash of the given kind (hardfault, hang, hang_irqoff). */
void     port_crash_test(int kind);

#endif /* PORT_H */
