/*
 * port_stm32.h — PRIVATE interfaces between the files of the STM32F411RET6
 * port (firmware/port_stm32). Nothing outside this directory includes it;
 * the public contract is src/port/port.h.
 *
 * Board facts used throughout the port (stage1_iv.net, blocks/mcu.py, U9
 * LQFP-64; docs/firmware/firmware_spec.md §4.1):
 *   PC5  SAFE_HB     PA4  CS_DAC (R34 pull-up to +5VA)   PA5/PA7 SCK1/MOSI1
 *   PB12 CS_ADC      PB13/PB14/PB15 SCK2/MISO2/MOSI2      PA8 DRDY  PA9 RESET_ADC
 *   PA0  VBUS_SENSE (R28/R29 100k/100k)  PA11/PA12 USB_DM/DP  PC13 LED (sunk)
 *   PA13/PA14 SWD, PH0/PH1 8 MHz crystal, everything else not connected.
 *
 * Clock plan (RM0383 Rev 4 §6.3.2 RCC_PLLCFGR, Table 5; DS10314 Rev 8):
 *   HSE 8 MHz / M 4 = 2 MHz VCO input, x N 192 = 384 MHz VCO (100..432),
 *   / P 4 = 96 MHz SYSCLK, / Q 8 = 48 MHz OTG FS. AHB /1, APB1 /2 = 48 MHz
 *   (max 50), APB2 /1 = 96 MHz (max 100). Timers on APB1 run at 2 x PCLK1 =
 *   96 MHz because the APB1 prescaler is not 1 (RM0383 §6.2, TIMPRE = 0).
 *   Before the PLL (and after an HSE failure) everything runs from HSI 16 MHz
 *   with all prescalers at /1.
 */
#ifndef PORT_STM32_H
#define PORT_STM32_H

#include "port.h"
#include "dac8552.h"

/* CMSIS device header (cmsis-device-f4 v2.6.9, STM32F411xE defined by CMake). */
#include "stm32f4xx.h"

/* ---- NVIC priorities (4 bits, all preemption: PRIGROUP = 3) ------------------ */
#define PRIO_FAULTS      0u   /* MemManage/BusFault/UsageFault (configurable)   */
#define PRIO_DEADMAN     0u   /* TIM5: highest configurable priority            */
#define PRIO_USB         1u   /* OTG_FS                                         */
#define PRIO_DRDY        2u   /* EXTI9_5 (PA8 /DRDY)                            */

/* ---- clocks (system.c) ------------------------------------------------------- */
typedef struct {
    uint32_t hclk_hz;     /* SYSCLK = HCLK                                   */
    uint32_t pclk1_hz;    /* APB1                                            */
    uint32_t pclk2_hz;    /* APB2                                            */
    uint32_t tim_apb1_hz; /* kernel clock of TIM2/TIM5                       */
} stm32_clocks_t;

/* Clock state configured by stm32_clock_init() (HSI 16 MHz before it). */
const stm32_clocks_t *stm32_clocks(void);

/* FAULT-SAFE: core clock in MHz derived from RCC_CFGR.SWS at the time of the
 * call (96 on the PLL, 16 on HSI, 8 on HSE). Correct after a CSS event, when
 * the hardware has already switched SYSCLK back to HSI. AHB is always /1. */
uint32_t stm32_core_mhz(void);

/* ---- bounded waits on DWT CYCCNT (system.c) -----------------------------------
 * FAULT-SAFE. The poll counter bounds the loop even if CYCCNT is not counting
 * (e.g. a debugger cleared DEMCR.TRCENA): every poll takes >= 1 cycle, so the
 * bound is never shorter than requested. us * MHz must fit 32 bits (< 44 s). */
typedef struct {
    uint32_t start;
    uint32_t cycles;
    uint32_t polls;
} stm32_timeout_t;

void stm32_timeout_start(stm32_timeout_t *t, uint32_t us);
bool stm32_timeout_expired(stm32_timeout_t *t);
/* Wait until (*reg & mask) == value; false on timeout. FAULT-SAFE. */
bool stm32_wait_reg(const volatile uint32_t *reg, uint32_t mask, uint32_t value,
                    uint32_t timeout_us);
/* Boot-only long busy wait (USB FDMOD settling etc.). */
void stm32_delay_ms(uint32_t ms);

/* ---- boot steps, in the order main.c calls them -------------------------------- */
void stm32_early_safe_init(void);        /* gpio_spi.c: §7.2 step 1             */
void stm32_reset_cause_capture(void);    /* system.c: RCC_CSR -> RST_*, RMVF     */
void stm32_crash_boot(void);             /* faults.c: validate .noinit record   */
void stm32_iwdg_start(void);             /* system.c                            */
void stm32_iwdg_kick(void);              /* system.c                            */
bool stm32_clock_init(void);             /* system.c: false = stayed on HSI     */
void stm32_core_init(void);              /* system.c: NVIC grouping, fault enables, MPU guard */
void stm32_timebase_init(void);          /* time.c: TIM2 1 MHz free running     */
void stm32_gpio_init_rest(void);         /* gpio_spi.c: §4.1 table              */
void stm32_adc_spi_init(void);           /* gpio_spi.c: SPI2                    */
void stm32_drdy_init(void);              /* gpio_spi.c: EXTI8 on PA8 (masked)   */
void stm32_adc1_init(void);              /* adc1.c                              */
void stm32_deadman_init(void);           /* time.c: TIM5 configured, stopped    */
bool stm32_usb_init(void);               /* usb.c: TinyUSB, soft-disconnected   */

/* ---- internal services ---------------------------------------------------------- */
/* Extend a raw TIM2 count read by the caller to 64 bits (time.c). Callable
 * from any context; correct while < 2^32 us passed since the last pass. */
uint64_t stm32_time_extend(uint32_t tim2_cnt);
/* Background ADC1 sequencing, called from port_pass_begin() (adc1.c). */
void     stm32_adc1_poll(uint64_t now_us);
/* FAULT-SAFE: if SPI1's baud-rate divider was chosen for another core clock
 * than the current one (CSS fell back to HSI), reset and reconfigure SPI1. */
void     stm32_dac_spi_retune(void);

/* Linker-script symbols (stm32f411re.ld). */
extern uint32_t __thread_stack_bottom[];  /* canary word lives here         */
extern uint32_t __thread_stack_top[];
extern uint32_t __handler_stack_top[];
extern uint32_t __fault_stack_top[];
extern const uint8_t __cal_flash_start[]; /* 0x08004000, sectors 1 and 2    */
extern const uint8_t __cal_sector_bytes[];/* absolute symbol: 16 KiB        */

#define STM32_STACK_CANARY  0x5AFEC0DEu

/* ---- exception and interrupt handlers (startup.c vector table) ------------------ */
void Reset_Handler(void);
void NMI_Handler(void);
void HardFault_Handler(void);
void MemManage_Handler(void);
void BusFault_Handler(void);
void UsageFault_Handler(void);
void Default_Handler(void);
void TIM5_IRQHandler(void);
void EXTI9_5_IRQHandler(void);
void OTG_FS_IRQHandler(void);

/* C part of the common fault path (faults.c), entered from the naked
 * assembly stub on the reserved fault stack. Never returns. */
void stm32_fault_c(const uint32_t *frame, uint32_t exc_return, uint32_t cause)
    __attribute__((noreturn));

int main(void);

#endif /* PORT_STM32_H */
