# STM32F411RET6 port

The register-level implementation of `src/port/port.h` for the Stage 1 board
(CMSIS headers only, no ST HAL), plus TinyUSB 0.21.0 glue. Build, option
bytes and flashing: `docs/firmware/build.md`.

| File | Contents |
|---|---|
| `startup.c` | vector table (sector 0), reset handler: MSP/PSP, FPU, `.data`/`.bss`, canary, DWT |
| `main.c` | boot order of `firmware_spec.md` §7.2, then `app_init()` / `app_pass()` forever |
| `system.c` | clocks (HSE 8 MHz → PLL 96 MHz, VOS 1, 3 WS, CSS), DWT waits, IWDG (/32, 1500), reset cause, BOR level, UID, `DBG_TIM2_STOP` query |
| `time.c` | TIM2 1 µs free-running timebase (64-bit extension), TIM5 dead-man (2 ms), pass hooks |
| `gpio_spi.c` | early safe init (PC5 low, PA4 high before output, SPI1, zero pair, LCKR), all pins, SPI1 (DAC, 6 MHz mode 1), SPI2 (ADC, 1.5 MHz mode 1), bit-bang fallback, EXTI8 /DRDY capture |
| `adc1.c` | background VBUS (PA0 ×2) and temperature (TS_CAL1/2) |
| `flash.c` | sectors 1–2 erase/program (x32), D-cache reset |
| `faults.c` | naked fault entry on a reserved stack, zero, crash record (`.noinit`, CRC), reset; dead-man handler; `CRASH` tests |
| `usb.c`, `usb_descriptors.c`, `tusb_config.h` | TinyUSB device CDC, VBUS sensing off, soft connect, descriptors (VID 0x1209, PID `FW_USB_PID`) |
| `stm32f411re.ld` | memory map: vectors in sector 0, nothing in sectors 1–2, code from sector 3; thread stack at the bottom of SRAM; `.noinit` |
| `target.cmake` | the image target `stage1_iv_fw-<variant>` with `.hex`, `.bin`, `.map` |

Status: compiles with `-Werror`, links, passes cppcheck and the link checks
(`firmware/tools/check_image.py`); **not yet run on hardware**
(`docs/firmware/limitations.md` §1 lists what to confirm first).
