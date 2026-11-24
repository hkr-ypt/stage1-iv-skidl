/*
 * gpio_spi.c — pins, SPI1 (DAC8552), SPI2 (ADS1256), /DRDY EXTI, PC5
 * heartbeat pin and the PC13 LED of the STM32F411RET6 port.
 *
 * Facts relied on (docs/firmware/firmware_spec.md §4.1, stage1_iv.net):
 *  - PC5 SAFE_HB (low first), PA4 CS_DAC (R34 pull-up to +5VA: never analog,
 *    never an internal pull; written high BEFORE it becomes an output),
 *    PA5/PA7 SPI1 AF5, PB12 CS_ADC, PB13-15 SPI2 AF5 (PB14 pull-down, >=
 *    medium speed, ES0287 2.11.4), PA8 /DRDY input (R19 external pull-up),
 *    PA9 /RESET_ADC output high (never AF10, never input), PA0 analog
 *    (adc1.c), PA11/PA12 AF10 very high speed, PC13 LED (low = on, sink
 *    only), PA13/PA14 SWD untouched, all other pins analog.
 *  - GPIO writes go through BSRR only (GPIOC is shared by PC5 and PC13).
 *    LCKR freezes PC5 and PA4 (RM0383 §8.4.8 key sequence).
 *  - SPI (RM0383 §20): mode 1 = CPOL 0, CPHA 1; SSM = SSI = 1 master; BR
 *    f_PCLK / 2^(BR+1). SPI1 on APB2: /16 at 96 MHz = 6 MHz (DAC8552 max
 *    30 MHz), /4 at HSI 16 MHz = 4 MHz. SPI2 on APB1: /32 at 48 MHz =
 *    1.5 MHz, /16 at 16 MHz = 1 MHz (ADS1256 SCLK <= f_CLKIN/4 = 1.92 MHz).
 *    /SYNC (PA4) may rise only once BSY = 0 (SLAS430A p.15). SPE is never
 *    cleared while BSY = 1 (ES0287 2.11.1); a stuck SPI is reset through
 *    RCC_APB2RSTR.
 *  - EXTI8 <- PA8 via SYSCFG_EXTICR3 (RM0383 §7.2.5, §10.2). The pending
 *    bit latches even while the line is masked (§10.3.6): arming clears
 *    EXTI_PR and the NVIC pending bit, then unmasks.
 *  - DAC bit-bang fallback: mode 1, data changes while SCLK is high and the
 *    DAC latches on the falling edge (SLAS430A Fig. 1), ~1 MHz.
 */
#include "port_stm32.h"

#define PIN(n) (1u << (n))

/* MODER / OSPEEDR / PUPDR values */
#define MODE_IN     0u
#define MODE_OUT    1u
#define MODE_AF     2u
#define MODE_AN     3u
#define SPD_LOW     0u
#define SPD_MED     1u
#define SPD_HIGH    2u
#define SPD_VHIGH   3u
#define PULL_NONE   0u
#define PULL_UP     1u
#define PULL_DOWN   2u

#define DAC_SPI_TIMEOUT_DEFAULT_US 50u

static void pin_mode(GPIO_TypeDef *g, unsigned n, uint32_t mode)
{
    g->MODER = (g->MODER & ~(3u << (2u * n))) | (mode << (2u * n));
}

static void pin_speed(GPIO_TypeDef *g, unsigned n, uint32_t spd)
{
    g->OSPEEDR = (g->OSPEEDR & ~(3u << (2u * n))) | (spd << (2u * n));
}

static void pin_pull(GPIO_TypeDef *g, unsigned n, uint32_t pull)
{
    g->PUPDR = (g->PUPDR & ~(3u << (2u * n))) | (pull << (2u * n));
}

static void pin_af(GPIO_TypeDef *g, unsigned n, uint32_t af)
{
    unsigned r = n / 8u;
    unsigned s = (n % 8u) * 4u;
    g->AFR[r] = (g->AFR[r] & ~(0xFu << s)) | (af << s);
}

static void pin_set(GPIO_TypeDef *g, unsigned n, bool high)
{
    g->BSRR = high ? PIN(n) : (PIN(n) << 16);
}

static void pin_lock(GPIO_TypeDef *g, uint32_t mask)
{
    g->LCKR = GPIO_LCKR_LCKK | mask;
    g->LCKR = mask;
    g->LCKR = GPIO_LCKR_LCKK | mask;
    (void)g->LCKR;
    (void)g->LCKR;
}

/* ---- SPI1 (DAC) ------------------------------------------------------------------ */

static uint32_t s_spi1_mhz;   /* core MHz SPI1's divider was chosen for */

static void spi1_configure(void)
{
    uint32_t mhz = stm32_core_mhz();
    uint32_t br = (mhz >= 96u) ? 3u : 1u;          /* /16 or /4 */
    SPI1->CR1 = 0u;
    SPI1->CR2 = 0u;
    SPI1->CR1 = SPI_CR1_CPHA | SPI_CR1_MSTR | SPI_CR1_SSM | SPI_CR1_SSI
              | (br << SPI_CR1_BR_Pos);
    SPI1->CR1 |= SPI_CR1_SPE;
    s_spi1_mhz = mhz;
}

void port_dac_cs(bool asserted)
{
    pin_set(GPIOA, 4, !asserted);
}

bool port_dac_wait_idle(uint32_t timeout_us)
{
    return stm32_wait_reg(&SPI1->SR, SPI_SR_TXE | SPI_SR_BSY, SPI_SR_TXE, timeout_us);
}

bool port_dac_write(const uint8_t *tx, size_t n, uint32_t timeout_us)
{
    if (timeout_us == 0u) {
        timeout_us = DAC_SPI_TIMEOUT_DEFAULT_US;
    }
    stm32_timeout_t t;
    stm32_timeout_start(&t, timeout_us);
    for (size_t i = 0; i < n; i++) {
        while ((SPI1->SR & SPI_SR_TXE) == 0u) {
            if (stm32_timeout_expired(&t)) {
                return false;
            }
        }
        *(volatile uint8_t *)&SPI1->DR = tx[i];
        while ((SPI1->SR & SPI_SR_RXNE) == 0u) {
            if (stm32_timeout_expired(&t)) {
                return false;
            }
        }
        (void)*(volatile uint8_t *)&SPI1->DR;   /* MISO not wired: discard */
    }
    while ((SPI1->SR & (SPI_SR_TXE | SPI_SR_BSY)) != SPI_SR_TXE) {
        if (stm32_timeout_expired(&t)) {
            return false;
        }
    }
    return true;
}

void port_dac_spi_reinit(void)
{
    RCC->APB2RSTR |= RCC_APB2RSTR_SPI1RST;
    RCC->APB2RSTR &= ~RCC_APB2RSTR_SPI1RST;
    spi1_configure();
}

void stm32_dac_spi_retune(void)
{
    if (s_spi1_mhz != stm32_core_mhz()) {
        port_dac_spi_reinit();
    }
}

void port_dac_bitbang_begin(void)
{
    pin_set(GPIOA, 5, false);
    pin_set(GPIOA, 7, false);
    pin_mode(GPIOA, 5, MODE_OUT);
    pin_mode(GPIOA, 7, MODE_OUT);
}

void port_dac_bitbang_pins(bool sclk, bool mosi)
{
    GPIOA->BSRR = (sclk ? PIN(5) : PIN(5) << 16) | (mosi ? PIN(7) : PIN(7) << 16);
}

void port_dac_bitbang_end(void)
{
    pin_mode(GPIOA, 5, MODE_AF);
    pin_mode(GPIOA, 7, MODE_AF);
}

/* ---- PC5 heartbeat and PC13 LED ----------------------------------------------------- */

void port_hb_write(bool high)
{
    pin_set(GPIOC, 5, high);
}

void port_hb_force_low(void)
{
    GPIOC->BSRR = PIN(5) << 16;
}

void port_led_write(bool on)
{
    pin_set(GPIOC, 13, !on);   /* sink only: low = on */
}

/* ---- boot step 1 ------------------------------------------------------------------- */

void stm32_early_safe_init(void)
{
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_GPIOBEN | RCC_AHB1ENR_GPIOCEN;
    RCC->APB2ENR |= RCC_APB2ENR_SPI1EN;
    (void)RCC->APB2ENR;

    /* PC5 low first */
    port_hb_force_low();
    pin_speed(GPIOC, 5, SPD_LOW);
    pin_pull(GPIOC, 5, PULL_NONE);
    GPIOC->OTYPER &= ~PIN(5);
    pin_mode(GPIOC, 5, MODE_OUT);

    /* PA4 /SYNC high BEFORE it becomes an output, no pull */
    pin_set(GPIOA, 4, true);
    pin_pull(GPIOA, 4, PULL_NONE);
    pin_speed(GPIOA, 4, SPD_MED);
    GPIOA->OTYPER &= ~PIN(4);
    pin_mode(GPIOA, 4, MODE_OUT);

    /* PA5 / PA7 AF5 (SPI1) */
    pin_af(GPIOA, 5, 5u);
    pin_af(GPIOA, 7, 5u);
    pin_speed(GPIOA, 5, SPD_MED);
    pin_speed(GPIOA, 7, SPD_MED);
    pin_mode(GPIOA, 5, MODE_AF);
    pin_mode(GPIOA, 7, MODE_AF);
    spi1_configure();

    dac_zero_safe();

    /* PA9 /RESET_ADC high, then output; PB12 /CS_ADC high, then output */
    pin_set(GPIOA, 9, true);
    pin_speed(GPIOA, 9, SPD_LOW);
    pin_mode(GPIOA, 9, MODE_OUT);
    pin_set(GPIOB, 12, true);
    pin_speed(GPIOB, 12, SPD_MED);
    pin_mode(GPIOB, 12, MODE_OUT);

    pin_lock(GPIOC, PIN(5));
    pin_lock(GPIOA, PIN(4));
}

/* ---- boot step 5: every other pin ----------------------------------------------------- */

void stm32_gpio_init_rest(void)
{
    /* PC13 LED: high (off) then output, low speed */
    pin_set(GPIOC, 13, true);
    pin_speed(GPIOC, 13, SPD_LOW);
    pin_mode(GPIOC, 13, MODE_OUT);

    /* PA8 /DRDY input, no pull (R19) */
    pin_pull(GPIOA, 8, PULL_NONE);
    pin_mode(GPIOA, 8, MODE_IN);

    /* PA0 VBUS sense: analog (adc1.c) */
    pin_mode(GPIOA, 0, MODE_AN);

    /* USB PA11 / PA12 AF10 very high speed */
    pin_af(GPIOA, 11, 10u);
    pin_af(GPIOA, 12, 10u);
    pin_speed(GPIOA, 11, SPD_VHIGH);
    pin_speed(GPIOA, 12, SPD_VHIGH);
    pin_mode(GPIOA, 11, MODE_AF);
    pin_mode(GPIOA, 12, MODE_AF);

    /* PA10 OTG_FS_ID (not connected on this board): AF10, open drain, internal
     * pull-up, as TinyUSB's tested STM32F4 board files set it. ID reads high
     * (B-device), so the OTG core starts in device mode. As an analog pin the
     * ID input would read 0 (RM0383 §8.3.12: the Schmitt trigger output is
     * forced to 0) and the core could start as a host, which ignores
     * device-register writes (GINTSTS.MMIS); usb.c also forces device mode. */
    pin_pull(GPIOA, 10, PULL_UP);
    GPIOA->OTYPER |= PIN(10);
    pin_af(GPIOA, 10, 10u);
    pin_mode(GPIOA, 10, MODE_AF);

    /* unconnected pins -> analog (PA13/PA14 SWD and PA4 untouched) */
    static const uint8_t pa[] = { 1, 2, 3, 6, 15 };
    static const uint8_t pb[] = { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 };
    static const uint8_t pc[] = { 0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 14, 15 };
    for (size_t i = 0; i < sizeof pa; i++) {
        pin_pull(GPIOA, pa[i], PULL_NONE);
        pin_mode(GPIOA, pa[i], MODE_AN);
    }
    for (size_t i = 0; i < sizeof pb; i++) {
        pin_pull(GPIOB, pb[i], PULL_NONE);
        pin_mode(GPIOB, pb[i], MODE_AN);
    }
    for (size_t i = 0; i < sizeof pc; i++) {
        pin_pull(GPIOC, pc[i], PULL_NONE);
        pin_mode(GPIOC, pc[i], MODE_AN);
    }
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIODEN | RCC_AHB1ENR_GPIOHEN;
    (void)RCC->AHB1ENR;
    pin_mode(GPIOD, 2, MODE_AN);

    /* SPI1 at the final clock (6 MHz) */
    stm32_dac_spi_retune();
}

/* ---- SPI2 (ADC) ------------------------------------------------------------------------ */

void stm32_adc_spi_init(void)
{
    RCC->APB1ENR |= RCC_APB1ENR_SPI2EN;
    (void)RCC->APB1ENR;
    pin_af(GPIOB, 13, 5u);
    pin_af(GPIOB, 14, 5u);
    pin_af(GPIOB, 15, 5u);
    pin_speed(GPIOB, 13, SPD_MED);
    pin_speed(GPIOB, 14, SPD_MED);
    pin_speed(GPIOB, 15, SPD_MED);
    pin_pull(GPIOB, 14, PULL_DOWN);
    pin_mode(GPIOB, 13, MODE_AF);
    pin_mode(GPIOB, 14, MODE_AF);
    pin_mode(GPIOB, 15, MODE_AF);

    uint32_t br = (stm32_clocks()->pclk1_hz >= 48000000u) ? 4u : 3u;   /* /32 or /16 */
    SPI2->CR1 = 0u;
    SPI2->CR2 = 0u;
    SPI2->CR1 = SPI_CR1_CPHA | SPI_CR1_MSTR | SPI_CR1_SSM | SPI_CR1_SSI
              | (br << SPI_CR1_BR_Pos);
    SPI2->CR1 |= SPI_CR1_SPE;
}

void port_adc_cs(bool asserted)
{
    pin_set(GPIOB, 12, !asserted);
}

bool port_adc_xfer(const uint8_t *tx, uint8_t *rx, size_t n, uint32_t timeout_us)
{
    stm32_timeout_t t;
    stm32_timeout_start(&t, timeout_us);
    /* A transfer that timed out can leave its last byte in flight and its
     * received byte unread. Left there, every later rx[i] would be the reply
     * to tx[i-1] (the skew renews itself, as the last reply is left behind
     * each time). Let it finish, discard it, and clear OVR (DR then SR read,
     * RM0383 §20). In normal operation BSY = RXNE = 0 here. */
    while ((SPI2->SR & SPI_SR_BSY) != 0u) {
        if (stm32_timeout_expired(&t)) {
            return false;
        }
    }
    while ((SPI2->SR & SPI_SR_RXNE) != 0u) {
        (void)*(volatile uint8_t *)&SPI2->DR;
    }
    (void)SPI2->SR;
    for (size_t i = 0; i < n; i++) {
        while ((SPI2->SR & SPI_SR_TXE) == 0u) {
            if (stm32_timeout_expired(&t)) {
                return false;
            }
        }
        *(volatile uint8_t *)&SPI2->DR = tx ? tx[i] : 0u;
        while ((SPI2->SR & SPI_SR_RXNE) == 0u) {
            if (stm32_timeout_expired(&t)) {
                return false;
            }
        }
        uint8_t b = *(volatile uint8_t *)&SPI2->DR;
        if (rx) {
            rx[i] = b;
        }
    }
    while ((SPI2->SR & SPI_SR_BSY) != 0u) {
        if (stm32_timeout_expired(&t)) {
            return false;
        }
    }
    return true;
}

void port_adc_reset_write(bool high)
{
    pin_set(GPIOA, 9, high);
}

bool port_adc_drdy_low(void)
{
    return (GPIOA->IDR & PIN(8)) == 0u;
}

/* ---- /DRDY capture (EXTI8) ------------------------------------------------------------ */

static volatile bool     s_drdy_have;
static volatile uint64_t s_drdy_t;

void stm32_drdy_init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_SYSCFGEN;
    (void)RCC->APB2ENR;
    SYSCFG->EXTICR[2] = (SYSCFG->EXTICR[2] & ~SYSCFG_EXTICR3_EXTI8) | SYSCFG_EXTICR3_EXTI8_PA;
    EXTI->IMR &= ~EXTI_IMR_MR8;
    EXTI->RTSR &= ~EXTI_RTSR_TR8;
    EXTI->FTSR |= EXTI_FTSR_TR8;
    EXTI->PR = EXTI_PR_PR8;
    NVIC_SetPriority(EXTI9_5_IRQn, PRIO_DRDY);
    NVIC_ClearPendingIRQ(EXTI9_5_IRQn);
    NVIC_EnableIRQ(EXTI9_5_IRQn);
}

void port_adc_drdy_arm(void)
{
    EXTI->IMR &= ~EXTI_IMR_MR8;
    EXTI->PR = EXTI_PR_PR8;
    NVIC_ClearPendingIRQ(EXTI9_5_IRQn);
    s_drdy_have = false;
    EXTI->IMR |= EXTI_IMR_MR8;
}

bool port_adc_drdy_take(uint64_t *t_us)
{
    if (!s_drdy_have) {
        return false;
    }
    __disable_irq();
    uint64_t t = s_drdy_t;
    s_drdy_have = false;
    __enable_irq();
    *t_us = t;
    return true;
}

/* Timestamps the first falling edge after arming and masks itself. Touches
 * neither SPI2 nor PC5 (SI-10). */
void EXTI9_5_IRQHandler(void)
{
    uint32_t cnt = TIM2->CNT;
    if (EXTI->PR & EXTI_PR_PR8) {
        EXTI->IMR &= ~EXTI_IMR_MR8;
        EXTI->PR = EXTI_PR_PR8;
        s_drdy_t = stm32_time_extend(cnt);
        s_drdy_have = true;
    }
    /* lines 5-7 and 9 are never unmasked */
    EXTI->PR = EXTI_PR_PR5 | EXTI_PR_PR6 | EXTI_PR_PR7 | EXTI_PR_PR9;
}
