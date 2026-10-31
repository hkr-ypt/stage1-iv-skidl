/*
 * main.c — boot sequence of the STM32F411RET6 target (firmware_spec.md §7.2)
 * and the super-loop. The portable application (app_init/app_pass) is the
 * same code the simulator runs.
 *
 * Order (normative, spec §7.2):
 *   1 early_safe_init on HSI: PC5 low, PA4 high then output, SPI1, the DAC
 *     zero pair, PA9 and PB12 high, LCKR on PC5/PA4
 *   2 reset flags (then RMVF), crash record validation
 *   3 IWDG start
 *   4 HSE/PLL 96 MHz (VOS 1, 3 WS, caches, CSS) or stay on HSI; core faults
 *     and NVIC grouping (thread mode already runs on PSP, startup.c)
 *   5 TIM2 timebase, every other pin, SPI2, EXTI8, ADC1, TIM5 dead-man
 *   8 USB (soft-disconnected; not started without HSE)
 *   6/7/9 calibration load, ADC init, BOOT state: app_init()
 */
#include "port_stm32.h"

#include "app.h"

int main(void)
{
    stm32_early_safe_init();
    stm32_reset_cause_capture();
    stm32_crash_boot();
    stm32_iwdg_start();
    (void)stm32_clock_init();      /* false: HSI, F_CLOCK latched by inst_init */
    stm32_core_init();
    stm32_timebase_init();
    stm32_gpio_init_rest();
    stm32_adc_spi_init();
    stm32_drdy_init();
    stm32_adc1_init();
    stm32_deadman_init();
    (void)stm32_usb_init();
    app_init();
    for (;;) {
        app_pass();
    }
}
