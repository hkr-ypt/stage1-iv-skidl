/*
 * faults.c — the common fault path, the TIM5 dead-man handler, the .noinit
 * crash record and the bring-up crash test of the STM32F411RET6 port
 * (firmware_spec.md §7.9).
 *
 * Facts relied on (PM0214 §2.3.7, §4.4; ARMv7-M ARM B1.5.6-B1.5.8):
 *  - on exception entry the core stacks r0-r3, r12, lr, pc, xPSR on the
 *    active stack (MSP in handler mode, PSP in thread mode); EXC_RETURN bit 2
 *    in lr says which. The stacked pc is frame[6], lr frame[5], xPSR
 *    frame[7].
 *  - CFSR/HFSR/MMFAR/BFAR (SCB 0xE000ED28.. 0xE000ED38) describe the fault.
 *  - A CSS event raises NMI with RCC_CIR.CSSF; writing CSSC clears it (RM0383
 *    §6.3.3), otherwise the NMI would repeat. The hardware has already
 *    switched SYSCLK to HSI, so SPI1's divider is retuned (stm32_dac_spi_retune).
 *  - Every handler shares one naked entry that switches SP to a reserved
 *    512-byte fault stack before any C code runs, so a corrupt or overflowed
 *    stack still reaches the zeroing code. The C part never returns:
 *    PC5 low, dac_zero_safe() (frame-aligned zero pair, SPI reset and
 *    bit-bang fallbacks), crash record, NVIC_SystemReset().
 *  - The stacked frame is read only if it lies inside SRAM, so a fault with
 *    a wild SP cannot fault again here.
 */
#include "port_stm32.h"

#include <string.h>

#define SRAM_START 0x20000000u
#define SRAM_END   0x20020000u

/* Numeric causes used in the assembly stubs (checked against fw_types.h). */
#define C_HARDFAULT  1
#define C_MEMMANAGE  2
#define C_BUSFAULT   3
#define C_USAGEFAULT 4
#define C_NMI        5
#define C_DEADMAN    7
_Static_assert(C_HARDFAULT == CRASH_HARDFAULT && C_MEMMANAGE == CRASH_MEMMANAGE
               && C_BUSFAULT == CRASH_BUSFAULT && C_USAGEFAULT == CRASH_USAGEFAULT
               && C_NMI == CRASH_NMI && C_DEADMAN == CRASH_DEADMAN,
               "crash cause numbers changed");

static crash_record_t s_crash __attribute__((section(".noinit.crash")));

/* Bitwise CRC-32 (IEEE, reflected), no tables: FAULT-SAFE. */
static uint32_t crc32_words(const uint32_t *w, size_t n)
{
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) {
        for (unsigned b = 0; b < 4u; b++) {
            crc ^= (w[i] >> (8u * b)) & 0xFFu;
            for (unsigned k = 0; k < 8u; k++) {
                crc = (crc & 1u) ? (crc >> 1) ^ 0xEDB88320u : (crc >> 1);
            }
        }
    }
    return ~crc;
}

#define CRASH_WORDS ((sizeof(crash_record_t) / 4u) - 1u)

static bool record_valid(const crash_record_t *r)
{
    return r->magic == CRASH_MAGIC && r->crc == crc32_words((const uint32_t *)r, CRASH_WORDS);
}

void stm32_crash_boot(void)
{
    if (!record_valid(&s_crash)) {
        memset(&s_crash, 0, sizeof s_crash);
    }
}

bool port_crash_get(crash_record_t *out)
{
    if (!record_valid(&s_crash)) {
        return false;
    }
    *out = s_crash;
    return true;
}

void port_crash_clear(void)
{
    memset(&s_crash, 0, sizeof s_crash);
}

/* Readable stacked frame: in SRAM and above the MPU guard (a frame pushed
 * into the guard by a stack overflow is not read: that would fault again). */
static bool in_sram(const uint32_t *p, size_t words)
{
    uintptr_t a = (uintptr_t)p;
    return a >= SRAM_START + STM32_MPU_GUARD_BYTES && a + words * 4u <= SRAM_END
           && (a & 3u) == 0u;
}

void stm32_fault_c(const uint32_t *frame, uint32_t exc_return, uint32_t cause)
{
    __disable_irq();
    (void)exc_return;
    port_hb_force_low();
    if (cause == CRASH_NMI && (RCC->CIR & RCC_CIR_CSSF)) {
        RCC->CIR = RCC_CIR_CSSC;
        cause = CRASH_CSS;
    }
    stm32_dac_spi_retune();
    dac_zero_safe();

    crash_record_t r;
    r.magic = CRASH_MAGIC;
    r.cause = cause;
    if (in_sram(frame, 8u)) {
        r.lr = frame[5];
        r.pc = frame[6];
        r.xpsr = frame[7];
    } else {
        r.lr = r.pc = r.xpsr = 0xFFFFFFFFu;
    }
    r.cfsr = SCB->CFSR;
    r.hfsr = SCB->HFSR;
    r.mmfar = SCB->MMFAR;
    r.bfar = SCB->BFAR;
    r.rcc_cr = RCC->CR;
    r.rcc_cfgr = RCC->CFGR;
    r.crc = crc32_words((const uint32_t *)&r, CRASH_WORDS);
    s_crash = r;
    __DSB();
    NVIC_SystemReset();
    for (;;) {
    }
}

/* Naked entry: frame pointer in r0 (MSP or PSP by EXC_RETURN bit 2),
 * EXC_RETURN in r1, cause in r2, then SP := the reserved fault stack. */
#define FAULT_ENTRY(name, cause_lit)                         \
    __attribute__((naked)) void name(void)                   \
    {                                                        \
        __asm volatile(                                      \
            "tst   lr, #4                 \n"                \
            "ite   eq                     \n"                \
            "mrseq r0, msp                \n"                \
            "mrsne r0, psp                \n"                \
            "mov   r1, lr                 \n"                \
            "movs  r2, #" #cause_lit "    \n"                \
            "ldr   r3, =__fault_stack_top \n"                \
            "mov   sp, r3                 \n"                \
            "b     stm32_fault_c          \n"                \
            ".ltorg                       \n");              \
    }

FAULT_ENTRY(HardFault_Handler, 1)
FAULT_ENTRY(MemManage_Handler, 2)
FAULT_ENTRY(BusFault_Handler, 3)
FAULT_ENTRY(UsageFault_Handler, 4)
FAULT_ENTRY(NMI_Handler, 5)
FAULT_ENTRY(TIM5_IRQHandler, 7)      /* dead-man: no pass for 2 ms */

void port_crash_test(int kind)
{
    switch (kind) {
    case 0:
        /* undefined instruction with UsageFault disabled escalates to HardFault */
        SCB->SHCSR &= ~SCB_SHCSR_USGFAULTENA_Msk;
        __DSB();
        __ISB();
        __asm volatile("udf #0x5A");
        break;
    case 1:
        for (;;) {          /* hang, interrupts on: the dead-man (if armed) or IWDG */
            __NOP();
        }
    case 3: {
        /* thread-stack overflow: SP to the top of the MPU guard, then a push
         * into it (DACCVIOL), whose exception stacking also lands in the
         * guard (MSTKERR): MemManage */
        uint32_t guard_top = (uint32_t)__thread_stack_bottom + STM32_MPU_GUARD_BYTES;
        __asm volatile("mov  sp, %0      \n"
                       "push {r0-r3}     \n"
                       :
                       : "r"(guard_top)
                       : "memory");
        for (;;) {
        }
    }
    case 2:
    default:
        __disable_irq();    /* hang, interrupts off: only the IWDG */
        for (;;) {
            __NOP();
        }
    }
}
