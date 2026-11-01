/*
 * stm32f411re.ld — memory map of the Stage 1 I-V firmware on the STM32F411RET6.
 *
 * Flash (RM0383 Rev 4 §3.3, Table 4 "Flash module organization"):
 *   sector 0  0x08000000-0x08003FFF  16 KiB  vector table + reset/startup ONLY
 *   sector 1  0x08004000-0x08007FFF  16 KiB  calibration record A  } nothing is
 *   sector 2  0x08008000-0x0800BFFF  16 KiB  calibration record B  } linked here
 *   sector 3+ 0x0800C000-0x0807FFFF 464 KiB  all other code and constants
 * Erasing sector 1 or 2 therefore never erases code, and programming the
 * image (.elf/.hex) never touches the calibration (the .bin does: README.md).
 *
 * SRAM 128 KiB at 0x20000000 (RM0383 §2.3), from the bottom up:
 *   .thread_stack  8 KiB  PSP of thread mode. At the BOTTOM so an overflow
 *                         runs into its lowest 32 B, an MPU no-access region
 *                         (stm32_core_init: MemManage), instead of into data.
 *                         The word above the guard is the canary
 *                         (port_stack_ok()).
 *   .data / .bss          initialised / zeroed by Reset_Handler
 *   .noinit               NOLOAD, never touched by the startup code: the crash
 *                         record and the large buffers (sections .noinit.*)
 *   (free)
 *   fault stack   512 B   used only by the common fault path, which switches
 *                         to it so a corrupt MSP still reaches the handler
 *   handler stack 1 KiB   MSP of every exception (initial SP, top of SRAM)
 *
 * No heap: `end` is deliberately not defined. If the link fails with an
 * undefined reference to `end`, something pulled in _sbrk/malloc (forbidden:
 * docs/firmware/firmware_spec.md §5, §7.1).
 */
ENTRY(Reset_Handler)

MEMORY
{
    FLASH_VEC (rx) : ORIGIN = 0x08000000, LENGTH = 16K
    FLASH_CAL      : ORIGIN = 0x08004000, LENGTH = 32K
    FLASH     (rx) : ORIGIN = 0x0800C000, LENGTH = 464K
    RAM      (rwx) : ORIGIN = 0x20000000, LENGTH = 128K
}

THREAD_STACK_SIZE  = 8K;
HANDLER_STACK_SIZE = 1K;
FAULT_STACK_SIZE   = 512;

/* Calibration sectors, consumed by flash.c (port_cal_sector()). */
__cal_flash_start  = ORIGIN(FLASH_CAL);
__cal_sector_bytes = 16K;

/* Stacks at the top of SRAM (8-byte aligned, AAPCS). */
__handler_stack_top    = ORIGIN(RAM) + LENGTH(RAM);
__handler_stack_bottom = __handler_stack_top - HANDLER_STACK_SIZE;
__fault_stack_top      = __handler_stack_bottom;
__fault_stack_bottom   = __fault_stack_top - FAULT_STACK_SIZE;

SECTIONS
{
    /* ---- sector 0: vectors + startup only ---------------------------------- */
    .isr_vector :
    {
        . = ALIGN(512);           /* VTOR alignment for 16 + 86 vectors      */
        __vectors_start = .;
        KEEP(*(.isr_vector))
        *(.boot .boot.*)          /* Reset_Handler and the C start-up code    */
        . = ALIGN(4);
    } > FLASH_VEC

    /* ---- sector 3 onwards: everything else --------------------------------- */
    .text :
    {
        . = ALIGN(4);
        *(.text .text.* .gnu.linkonce.t.*)
        *(.glue_7) *(.glue_7t) *(.vfp11_veneer) *(.v4_bx)
        KEEP(*(.init)) KEEP(*(.fini))
        . = ALIGN(4);
    } > FLASH

    .rodata :
    {
        . = ALIGN(4);
        *(.rodata .rodata.* .gnu.linkonce.r.*)
        . = ALIGN(4);
    } > FLASH

    .ARM.extab : { *(.ARM.extab* .gnu.linkonce.armextab.*) } > FLASH
    .ARM.exidx :
    {
        __exidx_start = .;
        *(.ARM.exidx* .gnu.linkonce.armexidx.*)
        __exidx_end = .;
    } > FLASH

    /* Constructors (none expected in this C code base; run by startup.c). */
    .preinit_array :
    {
        __preinit_array_start = .;
        KEEP(*(.preinit_array))
        __preinit_array_end = .;
    } > FLASH
    .init_array :
    {
        __init_array_start = .;
        KEEP(*(SORT(.init_array.*)))
        KEEP(*(.init_array))
        __init_array_end = .;
    } > FLASH
    .fini_array :
    {
        KEEP(*(SORT(.fini_array.*)))
        KEEP(*(.fini_array))
    } > FLASH

    /* ---- SRAM ----------------------------------------------------------------- */
    .thread_stack (NOLOAD) :
    {
        __thread_stack_bottom = .;
        . += THREAD_STACK_SIZE;
        __thread_stack_top = .;
    } > RAM

    .data :
    {
        . = ALIGN(4);
        __data_start__ = .;
        *(.data .data.* .gnu.linkonce.d.*)
        . = ALIGN(4);
        __data_end__ = .;
    } > RAM AT > FLASH
    __data_load__ = LOADADDR(.data);

    .bss (NOLOAD) :
    {
        . = ALIGN(4);
        __bss_start__ = .;
        *(.bss .bss.* .gnu.linkonce.b.*)
        *(COMMON)
        . = ALIGN(4);
        __bss_end__ = .;
    } > RAM

    .noinit (NOLOAD) :
    {
        . = ALIGN(8);
        __noinit_start__ = .;
        *(.noinit .noinit.*)
        . = ALIGN(8);
        __noinit_end__ = .;
    } > RAM

    /* ---- non-allocated sections ------------------------------------------------- */
    .ARM.attributes 0 : { KEEP(*(.ARM.attributes)) }
    .comment        0 : { *(.comment) }
    .debug          0 : { *(.debug) }
    .debug_info     0 : { *(.debug_info .gnu.linkonce.wi.*) }
    .debug_abbrev   0 : { *(.debug_abbrev) }
    .debug_line     0 : { *(.debug_line .debug_line.* .debug_line_end) }
    .debug_frame    0 : { *(.debug_frame) }
    .debug_str      0 : { *(.debug_str) }
    .debug_loc      0 : { *(.debug_loc) }
    .debug_macinfo  0 : { *(.debug_macinfo) }
    .debug_aranges  0 : { *(.debug_aranges) }
    .debug_ranges   0 : { *(.debug_ranges) }
    .debug_pubnames 0 : { *(.debug_pubnames) }
    .debug_pubtypes 0 : { *(.debug_pubtypes) }
    .debug_macro    0 : { *(.debug_macro) }
    .debug_addr     0 : { *(.debug_addr) }
    .debug_line_str 0 : { *(.debug_line_str) }
    .debug_loclists 0 : { *(.debug_loclists) }
    .debug_rnglists 0 : { *(.debug_rnglists) }
    .debug_str_offsets 0 : { *(.debug_str_offsets) }

    /DISCARD/ : { *(.note.GNU-stack) *(.gnu_debuglink) *(.gnu.lto_*) }
}

/* ---- link-time checks ------------------------------------------------------------- */
ASSERT(__vectors_start == 0x08000000, "vector table must be at 0x08000000")
ASSERT(ADDR(.text) >= 0x0800C000, "code must start in flash sector 3")
ASSERT(__thread_stack_bottom == 0x20000000, "thread stack must sit at the bottom of SRAM")
ASSERT((__thread_stack_bottom & 31) == 0, "the MPU guard region needs 32-byte alignment")
ASSERT(__noinit_end__ <= __fault_stack_bottom, "SRAM overflow: .data+.bss+.noinit reach the fault/handler stacks")
ASSERT(SIZEOF(.data) + SIZEOF(.rodata) + SIZEOF(.text) <= LENGTH(FLASH), "flash overflow")
ASSERT(!DEFINED(_sbrk) && !DEFINED(_malloc_r), "heap use is forbidden (_sbrk/_malloc_r linked)")
ASSERT(!DEFINED(_dtoa_r), "float formatting is forbidden (_dtoa_r linked)")
