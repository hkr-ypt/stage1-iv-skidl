/*
 * time.c — TIM2 microsecond timebase (64-bit extension), main-loop hooks
 * and the TIM5 dead-man timer of the STM32F411RET6 port.
 *
 * Facts relied on (RM0383 Rev 4 §13, §6.2):
 *  - TIM2 and TIM5 are 32-bit; their kernel clock is 2 x PCLK1 (96 MHz)
 *    when the APB1 prescaler is not 1, PCLK1 otherwise (HSI fallback).
 *    PSC = f/1 MHz - 1 gives 1 us ticks; PSC is buffered, so an update event
 *    (EGR.UG) loads it.
 *  - TIM2 runs free with no interrupt (spec §7.1); 2^32 us = 71.6 min, far
 *    longer than any pass, so one comparison per pass extends it.
 *  - TIM5 is the dead-man (spec §7.9): ARR = DEADMAN_TIMEOUT_US, reloaded
 *    (CNT := 0) at the end of every pass, update interrupt at the highest
 *    priority. Its handler is the common fault path (faults.c).
 */
#include "port_stm32.h"

static volatile uint32_t s_hi;
static volatile uint32_t s_last;
static bool              s_deadman_on;

void stm32_timebase_init(void)
{
    RCC->APB1ENR |= RCC_APB1ENR_TIM2EN;
    (void)RCC->APB1ENR;
    TIM2->CR1 = 0u;
    TIM2->PSC = stm32_clocks()->tim_apb1_hz / 1000000u - 1u;
    TIM2->ARR = 0xFFFFFFFFu;
    TIM2->CNT = 0u;
    TIM2->EGR = TIM_EGR_UG;
    TIM2->SR = 0u;
    TIM2->CR1 = TIM_CR1_CEN;
    s_hi = 0u;
    s_last = 0u;
}

uint64_t stm32_time_extend(uint32_t cnt)
{
    uint32_t hi = s_hi;
    if (cnt < s_last) {
        hi++;
    }
    return ((uint64_t)hi << 32) | cnt;
}

static void time_update(uint32_t cnt)
{
    uint32_t primask = __get_PRIMASK();
    __disable_irq();
    if (cnt < s_last) {
        s_hi = s_hi + 1u;
    }
    s_last = cnt;
    __set_PRIMASK(primask);
}

uint64_t port_now_us(void)
{
    uint32_t cnt = TIM2->CNT;
    uint64_t t = stm32_time_extend(cnt);
    if (__get_IPSR() == 0u) {
        time_update(cnt);
    }
    return t;
}

void port_pass_begin(void)
{
    uint64_t now = port_now_us();
    stm32_adc1_poll(now);
}

/* ---- dead-man (TIM5) ----------------------------------------------------------------- */

void stm32_deadman_init(void)
{
    RCC->APB1ENR |= RCC_APB1ENR_TIM5EN;
    (void)RCC->APB1ENR;
    TIM5->CR1 = 0u;
    TIM5->PSC = stm32_clocks()->tim_apb1_hz / 1000000u - 1u;
    TIM5->ARR = DEADMAN_TIMEOUT_US;
    TIM5->EGR = TIM_EGR_UG;
    TIM5->SR = 0u;
    TIM5->DIER = TIM_DIER_UIE;
    NVIC_SetPriority(TIM5_IRQn, PRIO_DEADMAN);
    NVIC_ClearPendingIRQ(TIM5_IRQn);
    s_deadman_on = false;
}

void port_deadman_enable(bool enable)
{
    if (enable == s_deadman_on) {
        return;
    }
    if (enable) {
        TIM5->CNT = 0u;
        TIM5->SR = 0u;
        NVIC_ClearPendingIRQ(TIM5_IRQn);
        NVIC_EnableIRQ(TIM5_IRQn);
        TIM5->CR1 = TIM_CR1_CEN;
    } else {
        TIM5->CR1 = 0u;
        NVIC_DisableIRQ(TIM5_IRQn);
        TIM5->SR = 0u;
        NVIC_ClearPendingIRQ(TIM5_IRQn);
    }
    s_deadman_on = enable;
}

void port_pass_end(bool healthy)
{
    TIM5->CNT = 0u;              /* dead-man reload */
    if (healthy) {
        stm32_iwdg_kick();
    }
}
