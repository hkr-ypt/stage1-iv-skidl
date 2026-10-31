/*
 * system.c — clocks, cycle-counted waits, IWDG, reset cause, BOR, UID and
 * other system queries of the STM32F411RET6 port.
 *
 * Facts relied on (RM0383 Rev 4, DS10314 Rev 8, PM0214):
 *  - PWR_CR.VOS[15:14] = 11 (scale 1) is required above 84 MHz; HCLK 96 MHz
 *    at 2.7-3.6 V needs 3 flash wait states (RM0383 §3.4.1 Table 6:
 *    90 < HCLK <= 100 MHz). Latency is raised before the clock and checked by
 *    reading FLASH_ACR back.
 *  - PLL (RM0383 §6.3.2): f_VCO_in = HSE/M must be 1-2 MHz (2 MHz),
 *    f_VCO = 100-432 MHz (384), SYSCLK = f_VCO/P (96), USB = f_VCO/Q must be
 *    48 MHz (Q = 8). APB1 <= 50 MHz (/2 = 48), APB2 <= 100 MHz (/1).
 *  - HSE 8 MHz crystal on PH0/PH1 (blocks/mcu.py); HSE start-up typ 2 ms
 *    (DS10314 Table 39); we allow 100 ms. CSS (RCC_CR.CSSON) raises NMI and
 *    switches SYSCLK to HSI on an HSE failure (RM0383 §6.2.7).
 *  - IWDG (RM0383 §17): KR 0x5555 unlocks PR/RLR, 0xCCCC starts, 0xAAAA
 *    reloads. PR = 3 (/32), RLR = 1500: timeout = 32*1500/f_LSI = 1.02 s at
 *    47 kHz .. 2.82 s at 17 kHz (DS10314 Table 40 LSI 17-47 kHz).
 *  - RCC_CSR reset flags, cleared by RMVF (RM0383 §6.3.21). PINRSTF is also
 *    set by every internal reset (the pin is driven low by the reset
 *    generator), so PIN is reported alone only (commands.c).
 *  - FLASH_OPTCR.BOR_LEV[3:2]: 00 = level 3 (2.70-3.60 V), 01 = level 2,
 *    10 = level 1, 11 = off (RM0383 §3.6.6).
 *  - DBGMCU_APB1_FZ.DBG_TIM2_STOP (bit 0) / DBG_IWDG_STOP (bit 12)
 *    (RM0383 §23.16.4). The firmware never sets DBG_TIM2_STOP.
 *  - 96-bit unique ID at 0x1FFF7A10 (RM0383 §24.1).
 *  - DWT CYCCNT counts core clocks once DEMCR.TRCENA and DWT_CTRL.CYCCNTENA
 *    are set (startup.c).
 */
#include "port_stm32.h"

uint32_t SystemCoreClock = 16000000u;   /* used by TinyUSB (dwc2_stm32.h) */

static stm32_clocks_t s_clk = { 16000000u, 16000000u, 16000000u, 16000000u };
static uint32_t       s_reset_flags;
static bool           s_hse_ok;

const stm32_clocks_t *stm32_clocks(void)
{
    return &s_clk;
}

uint32_t stm32_core_mhz(void)
{
    switch (RCC->CFGR & RCC_CFGR_SWS) {
    case RCC_CFGR_SWS_PLL: return 96u;
    case RCC_CFGR_SWS_HSE: return 8u;
    case RCC_CFGR_SWS_HSI:
    default:               return 16u;
    }
}

/* ---- bounded waits ------------------------------------------------------------- */

void stm32_timeout_start(stm32_timeout_t *t, uint32_t us)
{
    t->start = DWT->CYCCNT;
    t->cycles = us * stm32_core_mhz();
    t->polls = 0u;
}

bool stm32_timeout_expired(stm32_timeout_t *t)
{
    t->polls++;
    if (t->polls >= t->cycles) {
        return true;   /* CYCCNT stopped: each poll takes >= 1 cycle */
    }
    return (DWT->CYCCNT - t->start) >= t->cycles;
}

bool stm32_wait_reg(const volatile uint32_t *reg, uint32_t mask, uint32_t value,
                    uint32_t timeout_us)
{
    stm32_timeout_t t;
    stm32_timeout_start(&t, timeout_us);
    while ((*reg & mask) != value) {
        if (stm32_timeout_expired(&t)) {
            return (*reg & mask) == value;
        }
    }
    return true;
}

void port_delay_us(uint32_t us)
{
    uint32_t start = DWT->CYCCNT;
    uint32_t cycles = us * stm32_core_mhz();
    uint32_t polls = 0u;
    while ((DWT->CYCCNT - start) < cycles && polls < cycles) {
        polls++;
    }
}

void stm32_delay_ms(uint32_t ms)
{
    while (ms-- > 0u) {
        port_delay_us(1000u);
    }
}

/* ---- reset cause ------------------------------------------------------------------ */

void stm32_reset_cause_capture(void)
{
    uint32_t csr = RCC->CSR;
    uint32_t f = 0u;
    if (csr & RCC_CSR_PORRSTF)  { f |= RST_POR; }
    if (csr & RCC_CSR_PINRSTF)  { f |= RST_PIN; }
    if (csr & RCC_CSR_BORRSTF)  { f |= RST_BOR; }
    if (csr & RCC_CSR_SFTRSTF)  { f |= RST_SFT; }
    if (csr & RCC_CSR_IWDGRSTF) { f |= RST_IWDG; }
    if (csr & RCC_CSR_WWDGRSTF) { f |= RST_WWDG; }
    if (csr & RCC_CSR_LPWRRSTF) { f |= RST_LPWR; }
    s_reset_flags = f;
    RCC->CSR |= RCC_CSR_RMVF;
}

uint32_t port_reset_flags(void)
{
    return s_reset_flags;
}

/* ---- IWDG --------------------------------------------------------------------------- */

void stm32_iwdg_start(void)
{
#if defined(FW_VARIANT_DEBUG)
    /* debug builds only: a halted core must not be reset under the debugger
     * (DBG_TIM2_STOP stays clear: a frozen timebase would hide halts) */
    DBGMCU->APB1FZ |= DBGMCU_APB1_FZ_DBG_IWDG_STOP;
#endif
    IWDG->KR = 0xCCCCu;          /* start (also starts LSI) */
    IWDG->KR = 0x5555u;          /* unlock PR / RLR          */
    IWDG->PR = 3u;               /* /32                      */
    IWDG->RLR = 1500u;
    (void)stm32_wait_reg(&IWDG->SR, IWDG_SR_PVU | IWDG_SR_RVU, 0u, 50000u);
    IWDG->KR = 0xAAAAu;
}

void stm32_iwdg_kick(void)
{
    IWDG->KR = 0xAAAAu;
}

/* ---- clocks --------------------------------------------------------------------------- */

static void set_clock_state(uint32_t hclk, uint32_t pclk1, uint32_t pclk2)
{
    s_clk.hclk_hz = hclk;
    s_clk.pclk1_hz = pclk1;
    s_clk.pclk2_hz = pclk2;
    s_clk.tim_apb1_hz = (pclk1 == hclk) ? pclk1 : 2u * pclk1;
    SystemCoreClock = hclk;
}

bool stm32_clock_init(void)
{
    /* Voltage scale 1 for 96 MHz */
    RCC->APB1ENR |= RCC_APB1ENR_PWREN;
    (void)RCC->APB1ENR;
    PWR->CR = (PWR->CR & ~PWR_CR_VOS) | PWR_CR_VOS;

    RCC->CR |= RCC_CR_HSEON;
    if (!stm32_wait_reg(&RCC->CR, RCC_CR_HSERDY, RCC_CR_HSERDY, 100000u)) {
        RCC->CR &= ~RCC_CR_HSEON;
        s_hse_ok = false;
        set_clock_state(16000000u, 16000000u, 16000000u);
        return false;
    }

    /* flash: 3 wait states, prefetch and caches, before raising the clock */
    FLASH->ACR = FLASH_ACR_LATENCY_3WS | FLASH_ACR_PRFTEN | FLASH_ACR_ICEN | FLASH_ACR_DCEN;
    if ((FLASH->ACR & FLASH_ACR_LATENCY) != FLASH_ACR_LATENCY_3WS) {
        s_hse_ok = false;
        set_clock_state(16000000u, 16000000u, 16000000u);
        return false;
    }

    RCC->CR &= ~RCC_CR_PLLON;
    (void)stm32_wait_reg(&RCC->CR, RCC_CR_PLLRDY, 0u, 10000u);
    RCC->PLLCFGR = (4u << RCC_PLLCFGR_PLLM_Pos)
                 | (192u << RCC_PLLCFGR_PLLN_Pos)
                 | (1u << RCC_PLLCFGR_PLLP_Pos)        /* 01 = /4 */
                 | RCC_PLLCFGR_PLLSRC_HSE
                 | (8u << RCC_PLLCFGR_PLLQ_Pos);
    RCC->CR |= RCC_CR_PLLON;
    if (!stm32_wait_reg(&RCC->CR, RCC_CR_PLLRDY, RCC_CR_PLLRDY, 10000u)
        || !stm32_wait_reg(&PWR->CSR, PWR_CSR_VOSRDY, PWR_CSR_VOSRDY, 10000u)) {
        s_hse_ok = false;
        set_clock_state(16000000u, 16000000u, 16000000u);
        return false;
    }

    /* AHB /1, APB1 /2, APB2 /1, then switch */
    RCC->CFGR = (RCC->CFGR & ~(RCC_CFGR_HPRE | RCC_CFGR_PPRE1 | RCC_CFGR_PPRE2))
              | RCC_CFGR_HPRE_DIV1 | RCC_CFGR_PPRE1_DIV2 | RCC_CFGR_PPRE2_DIV1;
    RCC->CFGR = (RCC->CFGR & ~RCC_CFGR_SW) | RCC_CFGR_SW_PLL;
    if (!stm32_wait_reg(&RCC->CFGR, RCC_CFGR_SWS, RCC_CFGR_SWS_PLL, 10000u)) {
        s_hse_ok = false;
        set_clock_state(16000000u, 16000000u, 16000000u);
        return false;
    }
    RCC->CR |= RCC_CR_CSSON;
    set_clock_state(96000000u, 48000000u, 96000000u);
    s_hse_ok = true;
    return true;
}

bool port_hse_ok(void)
{
    return s_hse_ok;
}

/* ---- core ------------------------------------------------------------------------------- */

void stm32_core_init(void)
{
    NVIC_SetPriorityGrouping(3u);   /* 4 bits of preemption, no sub-priority */
    SCB->SHCSR |= SCB_SHCSR_MEMFAULTENA_Msk | SCB_SHCSR_BUSFAULTENA_Msk
                | SCB_SHCSR_USGFAULTENA_Msk;
    SCB->CCR |= SCB_CCR_DIV_0_TRP_Msk;
    NVIC_SetPriority(MemoryManagement_IRQn, PRIO_FAULTS);
    NVIC_SetPriority(BusFault_IRQn, PRIO_FAULTS);
    NVIC_SetPriority(UsageFault_IRQn, PRIO_FAULTS);
}

bool port_in_exception(void)
{
    return __get_IPSR() != 0u;
}

bool port_stack_ok(void)
{
    return __thread_stack_bottom[0] == STM32_STACK_CANARY;
}

int port_bor_level(void)
{
    switch ((FLASH->OPTCR & FLASH_OPTCR_BOR_LEV) >> FLASH_OPTCR_BOR_LEV_Pos) {
    case 0u: return 3;
    case 1u: return 2;
    case 2u: return 1;
    default: return 0;
    }
}

bool port_dbg_tim2_frozen(void)
{
    return (DBGMCU->APB1FZ & DBGMCU_APB1_FZ_DBG_TIM2_STOP) != 0u;
}

void port_uid(uint32_t uid[3])
{
    const volatile uint32_t *p = (const volatile uint32_t *)UID_BASE;
    uid[0] = p[0];
    uid[1] = p[1];
    uid[2] = p[2];
}

void port_system_reset(void)
{
    port_hb_force_low();
    NVIC_SystemReset();
}
