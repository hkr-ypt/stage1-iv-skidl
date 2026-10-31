/*
 * startup.c — vector table and reset entry of the STM32F411RET6 (no ST
 * startup assembly file). Everything here is linked into flash sector 0
 * (sections .isr_vector and .boot.*, see stm32f411re.ld).
 *
 * Facts relied on:
 *  - Vector table layout: 16 Cortex-M4 system vectors + 86 device IRQs
 *    (IRQn 0..85, SPI5_IRQn = 85) from stm32f411xe.h (cmsis-device-f4
 *    v2.6.9) / RM0383 Rev 4 §10.1.3 Table 37. 102 words -> 512-byte VTOR
 *    alignment (ARMv7-M ARM B3.2.5); the table is at 0x08000000.
 *  - CPACR (0xE000ED88) CP10/CP11 = full access before any FP instruction
 *    (PM0214 §4.6.1); the code is compiled -mfloat-abi=hard, so this is done
 *    in assembly before the first C statement.
 *  - CONTROL.SPSEL = 1 makes thread mode use PSP; handlers always use MSP
 *    (PM0214 §2.1.3). Thread mode is switched to PSP here, before main(),
 *    which is earlier than §7.2 step 4 of the spec asks and strictly safer:
 *    a C function cannot change its own stack pointer half-way through.
 *  - DWT CYCCNT needs DEMCR.TRCENA (ARMv7-M ARM C1.6.5); it is the only
 *    time base until TIM2 runs (spec §7.2 step 1).
 *
 * Reset_Handler copies .data and zeroes .bss; it never touches .noinit (crash
 * record, large buffers), the thread stack (except the canary) or the
 * handler stacks.
 */
#include "port_stm32.h"

/* ---- default handler ----------------------------------------------------------
 * Every vector the firmware does not use (including SVCall, PendSV, SysTick,
 * DebugMon and all device IRQs except TIM5, EXTI9_5 and OTG_FS) lands in
 * Default_Handler, which is the common fault path (faults.c) with cause
 * HARDFAULT: an unexpected interrupt is a firmware bug, and the response is
 * the same zero-record-reset as for a fault. */
#define WEAK_DEFAULT __attribute__((weak, alias("Default_Handler")))

/* lr still holds EXC_RETURN, so the HardFault entry finds the right frame. */
__attribute__((naked)) void Default_Handler(void)
{
    __asm volatile("b HardFault_Handler");
}

void SVC_Handler(void) WEAK_DEFAULT;
void DebugMon_Handler(void) WEAK_DEFAULT;
void PendSV_Handler(void) WEAK_DEFAULT;
void SysTick_Handler(void) WEAK_DEFAULT;

void WWDG_IRQHandler(void) WEAK_DEFAULT;
void PVD_IRQHandler(void) WEAK_DEFAULT;
void TAMP_STAMP_IRQHandler(void) WEAK_DEFAULT;
void RTC_WKUP_IRQHandler(void) WEAK_DEFAULT;
void FLASH_IRQHandler(void) WEAK_DEFAULT;
void RCC_IRQHandler(void) WEAK_DEFAULT;
void EXTI0_IRQHandler(void) WEAK_DEFAULT;
void EXTI1_IRQHandler(void) WEAK_DEFAULT;
void EXTI2_IRQHandler(void) WEAK_DEFAULT;
void EXTI3_IRQHandler(void) WEAK_DEFAULT;
void EXTI4_IRQHandler(void) WEAK_DEFAULT;
void DMA1_Stream0_IRQHandler(void) WEAK_DEFAULT;
void DMA1_Stream1_IRQHandler(void) WEAK_DEFAULT;
void DMA1_Stream2_IRQHandler(void) WEAK_DEFAULT;
void DMA1_Stream3_IRQHandler(void) WEAK_DEFAULT;
void DMA1_Stream4_IRQHandler(void) WEAK_DEFAULT;
void DMA1_Stream5_IRQHandler(void) WEAK_DEFAULT;
void DMA1_Stream6_IRQHandler(void) WEAK_DEFAULT;
void ADC_IRQHandler(void) WEAK_DEFAULT;
void TIM1_BRK_TIM9_IRQHandler(void) WEAK_DEFAULT;
void TIM1_UP_TIM10_IRQHandler(void) WEAK_DEFAULT;
void TIM1_TRG_COM_TIM11_IRQHandler(void) WEAK_DEFAULT;
void TIM1_CC_IRQHandler(void) WEAK_DEFAULT;
void TIM2_IRQHandler(void) WEAK_DEFAULT;
void TIM3_IRQHandler(void) WEAK_DEFAULT;
void TIM4_IRQHandler(void) WEAK_DEFAULT;
void I2C1_EV_IRQHandler(void) WEAK_DEFAULT;
void I2C1_ER_IRQHandler(void) WEAK_DEFAULT;
void I2C2_EV_IRQHandler(void) WEAK_DEFAULT;
void I2C2_ER_IRQHandler(void) WEAK_DEFAULT;
void SPI1_IRQHandler(void) WEAK_DEFAULT;
void SPI2_IRQHandler(void) WEAK_DEFAULT;
void USART1_IRQHandler(void) WEAK_DEFAULT;
void USART2_IRQHandler(void) WEAK_DEFAULT;
void EXTI15_10_IRQHandler(void) WEAK_DEFAULT;
void RTC_Alarm_IRQHandler(void) WEAK_DEFAULT;
void OTG_FS_WKUP_IRQHandler(void) WEAK_DEFAULT;
void DMA1_Stream7_IRQHandler(void) WEAK_DEFAULT;
void SDIO_IRQHandler(void) WEAK_DEFAULT;
void SPI3_IRQHandler(void) WEAK_DEFAULT;
void DMA2_Stream0_IRQHandler(void) WEAK_DEFAULT;
void DMA2_Stream1_IRQHandler(void) WEAK_DEFAULT;
void DMA2_Stream2_IRQHandler(void) WEAK_DEFAULT;
void DMA2_Stream3_IRQHandler(void) WEAK_DEFAULT;
void DMA2_Stream4_IRQHandler(void) WEAK_DEFAULT;
void DMA2_Stream5_IRQHandler(void) WEAK_DEFAULT;
void DMA2_Stream6_IRQHandler(void) WEAK_DEFAULT;
void DMA2_Stream7_IRQHandler(void) WEAK_DEFAULT;
void USART6_IRQHandler(void) WEAK_DEFAULT;
void I2C3_EV_IRQHandler(void) WEAK_DEFAULT;
void I2C3_ER_IRQHandler(void) WEAK_DEFAULT;
void FPU_IRQHandler(void) WEAK_DEFAULT;
void SPI4_IRQHandler(void) WEAK_DEFAULT;
void SPI5_IRQHandler(void) WEAK_DEFAULT;

typedef void (*vector_fn)(void);

#define N_DEVICE_IRQS 86u   /* IRQn 0 .. SPI5_IRQn (85) */

typedef struct {
    uint32_t *initial_sp;
    vector_fn system[15];             /* exceptions 1..15  */
    vector_fn irq[N_DEVICE_IRQS];     /* exceptions 16..   */
} vector_table_t;

_Static_assert(SPI5_IRQn == 85, "STM32F411 vector count changed");

__attribute__((section(".isr_vector"), used))
const vector_table_t g_vector_table = {
    .initial_sp = __handler_stack_top,
    .system = {
        Reset_Handler,          /*  1 Reset                */
        NMI_Handler,            /*  2 NMI (incl. CSS)      */
        HardFault_Handler,      /*  3                      */
        MemManage_Handler,      /*  4                      */
        BusFault_Handler,       /*  5                      */
        UsageFault_Handler,     /*  6                      */
        0, 0, 0, 0,             /*  7-10 reserved          */
        SVC_Handler,            /* 11                      */
        DebugMon_Handler,       /* 12                      */
        0,                      /* 13 reserved             */
        PendSV_Handler,         /* 14                      */
        SysTick_Handler,        /* 15                      */
    },
    .irq = {
        WWDG_IRQHandler,            /*  0 */
        PVD_IRQHandler,             /*  1 */
        TAMP_STAMP_IRQHandler,      /*  2 */
        RTC_WKUP_IRQHandler,        /*  3 */
        FLASH_IRQHandler,           /*  4 */
        RCC_IRQHandler,             /*  5 */
        EXTI0_IRQHandler,           /*  6 */
        EXTI1_IRQHandler,           /*  7 */
        EXTI2_IRQHandler,           /*  8 */
        EXTI3_IRQHandler,           /*  9 */
        EXTI4_IRQHandler,           /* 10 */
        DMA1_Stream0_IRQHandler,    /* 11 */
        DMA1_Stream1_IRQHandler,    /* 12 */
        DMA1_Stream2_IRQHandler,    /* 13 */
        DMA1_Stream3_IRQHandler,    /* 14 */
        DMA1_Stream4_IRQHandler,    /* 15 */
        DMA1_Stream5_IRQHandler,    /* 16 */
        DMA1_Stream6_IRQHandler,    /* 17 */
        ADC_IRQHandler,             /* 18 */
        Default_Handler,            /* 19 reserved */
        Default_Handler,            /* 20 reserved */
        Default_Handler,            /* 21 reserved */
        Default_Handler,            /* 22 reserved */
        EXTI9_5_IRQHandler,         /* 23  PA8 /DRDY */
        TIM1_BRK_TIM9_IRQHandler,   /* 24 */
        TIM1_UP_TIM10_IRQHandler,   /* 25 */
        TIM1_TRG_COM_TIM11_IRQHandler, /* 26 */
        TIM1_CC_IRQHandler,         /* 27 */
        TIM2_IRQHandler,            /* 28 */
        TIM3_IRQHandler,            /* 29 */
        TIM4_IRQHandler,            /* 30 */
        I2C1_EV_IRQHandler,         /* 31 */
        I2C1_ER_IRQHandler,         /* 32 */
        I2C2_EV_IRQHandler,         /* 33 */
        I2C2_ER_IRQHandler,         /* 34 */
        SPI1_IRQHandler,            /* 35 */
        SPI2_IRQHandler,            /* 36 */
        USART1_IRQHandler,          /* 37 */
        USART2_IRQHandler,          /* 38 */
        Default_Handler,            /* 39 reserved */
        EXTI15_10_IRQHandler,       /* 40 */
        RTC_Alarm_IRQHandler,       /* 41 */
        OTG_FS_WKUP_IRQHandler,     /* 42 */
        Default_Handler,            /* 43 reserved */
        Default_Handler,            /* 44 reserved */
        Default_Handler,            /* 45 reserved */
        Default_Handler,            /* 46 reserved */
        DMA1_Stream7_IRQHandler,    /* 47 */
        Default_Handler,            /* 48 reserved */
        SDIO_IRQHandler,            /* 49 */
        TIM5_IRQHandler,            /* 50  dead-man */
        SPI3_IRQHandler,            /* 51 */
        Default_Handler,            /* 52 reserved */
        Default_Handler,            /* 53 reserved */
        Default_Handler,            /* 54 reserved */
        Default_Handler,            /* 55 reserved */
        DMA2_Stream0_IRQHandler,    /* 56 */
        DMA2_Stream1_IRQHandler,    /* 57 */
        DMA2_Stream2_IRQHandler,    /* 58 */
        DMA2_Stream3_IRQHandler,    /* 59 */
        DMA2_Stream4_IRQHandler,    /* 60 */
        Default_Handler,            /* 61 reserved */
        Default_Handler,            /* 62 reserved */
        Default_Handler,            /* 63 reserved */
        Default_Handler,            /* 64 reserved */
        Default_Handler,            /* 65 reserved */
        Default_Handler,            /* 66 reserved */
        OTG_FS_IRQHandler,          /* 67  USB */
        DMA2_Stream5_IRQHandler,    /* 68 */
        DMA2_Stream6_IRQHandler,    /* 69 */
        DMA2_Stream7_IRQHandler,    /* 70 */
        USART6_IRQHandler,          /* 71 */
        I2C3_EV_IRQHandler,         /* 72 */
        I2C3_ER_IRQHandler,         /* 73 */
        Default_Handler,            /* 74 reserved */
        Default_Handler,            /* 75 reserved */
        Default_Handler,            /* 76 reserved */
        Default_Handler,            /* 77 reserved */
        Default_Handler,            /* 78 reserved */
        Default_Handler,            /* 79 reserved */
        Default_Handler,            /* 80 reserved */
        FPU_IRQHandler,             /* 81 */
        Default_Handler,            /* 82 reserved */
        Default_Handler,            /* 83 reserved */
        SPI4_IRQHandler,            /* 84 */
        SPI5_IRQHandler,            /* 85 */
    },
};

/* ---- reset ---------------------------------------------------------------------- */

void stm32_reset_c(void) __attribute__((noreturn, used, section(".boot.reset_c")));

/*
 * Assembly first: stacks and FPU access before any compiler-generated code.
 *   MSP = top of the 1 KiB handler stack (also vector[0]; set again in case a
 *         debugger or a bootloader started us with another value),
 *   CPACR |= CP10/CP11 full access,
 *   PSP = top of the 8 KiB thread stack, CONTROL.SPSEL = 1 (privileged).
 */
__attribute__((naked, noreturn, section(".boot.reset")))
void Reset_Handler(void)
{
    __asm volatile(
        "cpsid i                     \n"
        "ldr   r0, =__handler_stack_top \n"
        "msr   msp, r0               \n"
        "ldr   r0, =0xE000ED88       \n"   /* SCB->CPACR */
        "ldr   r1, [r0]              \n"
        "orr   r1, r1, #0x00F00000   \n"   /* CP10, CP11 = 0b11 */
        "str   r1, [r0]              \n"
        "dsb                         \n"
        "isb                         \n"
        "ldr   r0, =__thread_stack_top \n"
        "msr   psp, r0               \n"
        "movs  r0, #2                \n"   /* SPSEL = 1, nPRIV = 0, FPCA = 0 */
        "msr   control, r0           \n"
        "isb                         \n"
        "b     stm32_reset_c         \n"
        ".ltorg                      \n");
}

extern uint32_t __data_start__[];
extern uint32_t __data_end__[];
extern const uint32_t __data_load__[];
extern uint32_t __bss_start__[];
extern uint32_t __bss_end__[];
extern vector_fn __preinit_array_start[];
extern vector_fn __preinit_array_end[];
extern vector_fn __init_array_start[];
extern vector_fn __init_array_end[];

/* Built with -fno-tree-loop-distribute-patterns (target.cmake) so the loops
 * below stay loops and do not become memcpy/memset calls. */
void stm32_reset_c(void)
{
    SCB->VTOR = (uint32_t)(uintptr_t)&g_vector_table;

    const uint32_t *src = __data_load__;
    /* linker-defined bounds of one region: comparing them is the intent */
    // cppcheck-suppress comparePointers
    for (uint32_t *dst = __data_start__; dst < __data_end__; dst++) {
        *dst = *src++;
    }
    // cppcheck-suppress comparePointers
    for (uint32_t *dst = __bss_start__; dst < __bss_end__; dst++) {
        *dst = 0u;
    }
    /* .noinit is deliberately left alone. */

    __thread_stack_bottom[0] = STM32_STACK_CANARY;

    /* DWT cycle counter: the only time base before TIM2 (and in fault paths). */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0u;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

    // cppcheck-suppress comparePointers
    for (vector_fn *f = __preinit_array_start; f < __preinit_array_end; f++) {
        (*f)();
    }
    // cppcheck-suppress comparePointers
    for (vector_fn *f = __init_array_start; f < __init_array_end; f++) {
        (*f)();
    }

    __enable_irq();   /* nothing is enabled in the NVIC yet */
    (void)main();

    /* main() never returns; if it does, that is a crash: an undefined
     * instruction enters the common fault path (UsageFault). */
    for (;;) {
        __asm volatile("udf #0");
    }
}
