# Toolchain, build, flashing

All commands run from the repository root unless stated; nothing depends on
a machine-specific path.

## 1. Tools

| Tool | Version verified | Used for |
|---|---|---|
| CMake | ≥ 3.25 (3.28 used) | build system, presets |
| Ninja | 1.11 | generator |
| GNU Arm Embedded `arm-none-eabi-gcc` + newlib-nano | 13.2.1 (Ubuntu 24.04 `gcc-arm-none-eabi`) | target images |
| gcc | 13.3 | unit tests, system tests, simulator (ASan/UBSan) |
| Python | ≥ 3.11, pyserial 3.5, pytest | host library, tests, tools |
| cppcheck | 2.13 | static analysis (CI) |
| gcovr | 7 | coverage report (CI, optional locally) |
| OpenOCD ≥ 0.12 or STM32CubeProgrammer, ST-LINK | — | flashing, option bytes (bench only) |

## 2. Third-party sources

They are not vendored. `firmware/third_party.lock` pins each by commit hash
(TinyUSB 0.21.0, CMSIS-Device F4 v2.6.9, CMSIS 5.9.0 Core headers, Unity
2.6.1); the fetch script clones exactly that commit and verifies it:

```sh
firmware/tools/fetch_deps.sh          # fetch / verify into firmware/third_party/
firmware/tools/fetch_deps.sh --check  # verify only
```

CMake refuses to configure if a dependency is missing.

## 3. Presets

From `firmware/`:

| Preset | Builds |
|---|---|
| `host` | unit + system tests, simulator (`release` variant), ASan/UBSan |
| `host-bringup` | the same for the `bringup` variant |
| `coverage` | tests with gcov, no sanitizers |
| `fw-release` | `stage1_iv_fw-release.{elf,hex,bin,map}` (production) |
| `fw-debug` | `stage1_iv_fw-debug.*` (`-Og`, IWDG frozen while halted) |
| `fw-bringup` | `stage1_iv_fw-bringup.*` (unsafe bring-up commands compiled in) |

```sh
cd firmware
cmake --preset host && cmake --build --preset host && ctest --preset host
cmake --preset fw-release && cmake --build --preset fw-release
python3 tools/check_image.py build/fw-release      # link checks (CI does all three)
```

Images land in `firmware/build/<preset>/`. Every target file name carries the
variant, the USB product string of a bring-up image ends in `BRINGUP`, and
`*IDN?` reports `build=<variant>`: a bring-up image cannot be mistaken for a
production one. The host library refuses sweeps on a `bringup` build unless
told otherwise (`--allow-bringup`).

**Reproducibility.** `-ffile-prefix-map` removes the source path from the
objects; two builds of the same commit in different directories give
byte-identical `.bin` and `.hex` (checked in CI). The revision string
(`git describe --always --dirty`) is part of the image.

**Bring-up variant guard.** `FW_BRINGUP` is derived in `fw_config.h` from
exactly one `FW_VARIANT_*` macro; any other combination is a compile error.
CI checks the linked images: release and debug contain none of the bring-up
symbols, the bring-up image contains all of them.

## 4. USB identity options

```sh
cmake --preset fw-release -DFW_USB_PID=0x1234 -DFW_USB_PID_REGISTERED=ON
```

The default is the pid.codes private-testing PID 0x0001 with
`FW_USB_PID_REGISTERED=OFF`. Setting `ON` with 0x0001 is a configure error.
See [`usb_identity.md`](usb_identity.md).

## 5. Option bytes (once per board)

Factory parts ship with BOR off. The firmware refuses `ARM`
(`ERR PRECOND bor`) until BOR level 3 is programmed. It never writes option
bytes itself. Program BOR level 3 and keep the software watchdog start
(`WDG_SW = 1`, the factory default):

OpenOCD (ST-LINK):

```sh
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
  -c "init" -c "reset halt" \
  -c "stm32f2x options_read 0" \
  -c "stm32f2x options_write 0 0xe0" \
  -c "reset run" -c "shutdown"
```

`options_write 0 <user>` writes the user option byte (FLASH_OPTCR bits 7:0,
RM0383 §3.6.6): bits 3:2 BOR_LEV (00 = level 3), bit 5 WDG_SW (1 = software
watchdog), bits 6–7 nRST_STOP / nRST_STDBY (1 = no reset, the factory
values). Level 3 with everything else at its factory value is `0xe0` (the
factory byte is `0xec`, BOR off). Read back with `options_read` before
resetting, then confirm `STAT? bor=3`.

STM32CubeProgrammer CLI:

```sh
STM32_Programmer_CLI -c port=SWD mode=UR -ob BOR_LEV=0 WDG_SW=1
```

(`BOR_LEV=0` is "level 3" in the CubeProgrammer encoding: 2.70–3.60 V.)

## 6. Flashing

BOOT0 is tied low, so SWD is the only path. Connect under reset (NRST is on
J2 pin 5) if the running firmware holds the core busy.

```sh
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
  -c "reset_config srst_only srst_nogate connect_assert_srst" \
  -c "program firmware/build/fw-release/stage1_iv_fw-release.elf verify reset exit"
```

Program the `.elf` or `.hex`: they contain only sector 0 and sectors 3 and up,
so the calibration in sectors 1–2 survives. The `.bin` is a flat image from
0x08000000 and **overwrites the calibration sectors** with 0xFF; use it only
for a deliberate full erase.

After flashing: `iv-diag` (or `*IDN?`) shows the variant and revision;
`STAT?` must show `bor=3` before the board can arm.
