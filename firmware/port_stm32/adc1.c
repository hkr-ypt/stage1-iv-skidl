/*
 * adc1.c — background measurement of VBUS (PA0 / ADC1_IN0) and the MCU
 * temperature (ADC1_IN18) of the STM32F411RET6 port.
 *
 * Facts relied on:
 *  - VBUS reaches PA0 through R28/R29 = 100 k / 100 k (blocks/mcu.py):
 *    VBUS = 2 x V_pin. The 50 kohm source impedance needs the longest sample
 *    time, 480 cycles (DS10314 Table 66 R_AIN; spec §4.1).
 *  - ADC clock <= 36 MHz: PCLK2 96 MHz / 4 = 24 MHz (ADC_CCR.ADCPRE = 01);
 *    on the HSI fallback 16 MHz / 4 = 4 MHz. 12-bit, VREF+ = VDDA = 3.3 V.
 *  - Temperature sensor: ADC_CCR.TSVREFE, sampling >= 10 us (DS10314 Table
 *    80: 480 cycles at 24 MHz = 20 us), factory points TS_CAL1 (30 degC) and
 *    TS_CAL2 (110 degC) at 0x1FFF7A2C / 0x1FFF7A2E, taken at VDDA = 3.3 V
 *    (DS10314 Table 81). The value is an ambient proxy only.
 *  - One conversion at a time, started by SWSTART and collected on a later
 *    pass (EOC); nothing here blocks. A conversion takes < 200 us even on
 *    HSI; one without EOC after ADC_EOC_TIMEOUT_US is abandoned, and VBUS
 *    then reads 0 (absent: the session ends and the output disarms) until a
 *    later conversion completes, never a frozen "present".
 */
#include "port_stm32.h"

#define ADC_PERIOD_US   10000u
#define ADC_EOC_TIMEOUT_US 5000u
#define TS_CAL1_ADDR    0x1FFF7A2Cu
#define TS_CAL2_ADDR    0x1FFF7A2Eu
#define VDDA_MV         3300u

typedef enum { A_IDLE = 0, A_VBUS, A_TEMP } adc_phase_t;

static adc_phase_t s_phase;
static uint64_t    s_next;
static uint64_t    s_started;
static uint32_t    s_vbus_mv;
static int32_t     s_temp_cdeg = INT32_MIN;
static bool        s_ok;

void stm32_adc1_init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_ADC1EN;
    (void)RCC->APB2ENR;
    ADC->CCR = (ADC->CCR & ~ADC_CCR_ADCPRE) | ADC_CCR_ADCPRE_0 | ADC_CCR_TSVREFE;
    ADC1->CR1 = 0u;                                   /* 12-bit, no scan */
    ADC1->CR2 = 0u;
    ADC1->SMPR2 = (ADC1->SMPR2 & ~ADC_SMPR2_SMP0) | ADC_SMPR2_SMP0;    /* 480 cycles */
    ADC1->SMPR1 = (ADC1->SMPR1 & ~ADC_SMPR1_SMP18) | ADC_SMPR1_SMP18;
    ADC1->SQR1 = 0u;                                  /* one conversion */
    ADC1->CR2 = ADC_CR2_ADON;
    s_phase = A_IDLE;
    s_next = 0u;
    s_ok = true;
}

static void start(uint32_t channel)
{
    ADC1->SQR3 = channel;
    ADC1->SR = 0u;
    ADC1->CR2 |= ADC_CR2_SWSTART;
}

static int32_t temp_cdeg(uint32_t raw)
{
    int32_t c1 = (int32_t)*(const volatile uint16_t *)TS_CAL1_ADDR;
    int32_t c2 = (int32_t)*(const volatile uint16_t *)TS_CAL2_ADDR;
    if (c2 <= c1) {
        return INT32_MIN;
    }
    return 3000 + ((int32_t)raw - c1) * 8000 / (c2 - c1);
}

void stm32_adc1_poll(uint64_t now_us)
{
    if (!s_ok) {
        return;
    }
    switch (s_phase) {
    case A_IDLE:
        if (now_us >= s_next) {
            start(0u);
            s_phase = A_VBUS;
            s_started = now_us;
            s_next = now_us + ADC_PERIOD_US;
        }
        break;
    case A_VBUS:
        if (ADC1->SR & ADC_SR_EOC) {
            uint32_t raw = ADC1->DR & 0xFFFu;
            s_vbus_mv = raw * VDDA_MV * 2u / 4095u;
            start(18u);
            s_phase = A_TEMP;
            s_started = now_us;
        } else if (now_us - s_started > ADC_EOC_TIMEOUT_US) {
            s_vbus_mv = 0u;   /* unknown counts as absent */
            s_phase = A_IDLE;
        }
        break;
    case A_TEMP:
        if (ADC1->SR & ADC_SR_EOC) {
            s_temp_cdeg = temp_cdeg(ADC1->DR & 0xFFFu);
            s_phase = A_IDLE;
        } else if (now_us - s_started > ADC_EOC_TIMEOUT_US) {
            s_temp_cdeg = INT32_MIN;
            s_phase = A_IDLE;
        }
        break;
    default:
        s_phase = A_IDLE;
        break;
    }
}

uint32_t port_vbus_mv(void)
{
    return s_vbus_mv;
}

int32_t port_mcu_temp_cdeg(void)
{
    return s_temp_cdeg;
}
