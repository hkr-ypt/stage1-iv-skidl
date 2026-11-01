# Cross build for the STM32F411RET6 (included by firmware/CMakeLists.txt when
# the arm-none-eabi toolchain file is in use; see docs/firmware/build.md).
#
# Output: build/<preset>/stage1_iv_fw-<variant>.{elf,hex,bin,map}. The variant
# is part of every file name so that a bring-up image cannot be mistaken for
# a production one; the USB product string and *IDN? build= say it as well.

set(FW_TUSB "${FW_THIRD_PARTY}/tinyusb/src")
set(FW_TARGET_SYSTEM_INCLUDES
    "${FW_THIRD_PARTY}/cmsis_core/CMSIS/Core/Include"
    "${FW_THIRD_PARTY}/cmsis_device_f4/Include"
    "${FW_TUSB}")

if(FW_BUILD_VARIANT STREQUAL "debug")
    set(FW_OPT -Og -g3)
else()
    set(FW_OPT -O2 -g3)
endif()
target_compile_options(fw_portable PRIVATE ${FW_OPT})

# bcdDevice 0xMMmp from FW_VERSION (major.minor.patch)
string(REPLACE "." ";" _ver "${FW_VERSION}")
list(GET _ver 0 _maj)
list(GET _ver 1 _min)
list(GET _ver 2 _pat)
math(EXPR FW_VERSION_BCD "(${_maj} << 8) | (${_min} << 4) | ${_pat}" OUTPUT_FORMAT HEXADECIMAL)

# ---- TinyUSB 0.21.0 device stack (third-party: relaxed warnings) ---------------
add_library(fw_tinyusb OBJECT
    "${FW_TUSB}/tusb.c"
    "${FW_TUSB}/common/tusb_fifo.c"
    "${FW_TUSB}/device/usbd.c"
    "${FW_TUSB}/class/cdc/cdc_device.c"
    "${FW_TUSB}/portable/synopsys/dwc2/dcd_dwc2.c"
    "${FW_TUSB}/portable/synopsys/dwc2/dwc2_common.c")
target_include_directories(fw_tinyusb PRIVATE "${CMAKE_SOURCE_DIR}/port_stm32")
target_include_directories(fw_tinyusb SYSTEM PRIVATE ${FW_TARGET_SYSTEM_INCLUDES})
target_compile_definitions(fw_tinyusb PRIVATE STM32F411xE)
target_compile_options(fw_tinyusb PRIVATE ${FW_OPT} -w)

# ---- the firmware image ----------------------------------------------------------
set(FW_IMAGE "stage1_iv_fw-${FW_BUILD_VARIANT}")
file(GLOB FW_PORT_SRC CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/port_stm32/*.c")
add_executable(${FW_IMAGE} ${FW_PORT_SRC} $<TARGET_OBJECTS:fw_tinyusb>)
set_target_properties(${FW_IMAGE} PROPERTIES SUFFIX ".elf")
target_include_directories(${FW_IMAGE} PRIVATE "${CMAKE_SOURCE_DIR}/port_stm32" ${FW_PORTABLE_INCLUDES})
target_include_directories(${FW_IMAGE} SYSTEM PRIVATE ${FW_TARGET_SYSTEM_INCLUDES})
target_compile_definitions(${FW_IMAGE} PRIVATE ${FW_DEFINES} STM32F411xE FW_VERSION_BCD=${FW_VERSION_BCD})
# -fno-tree-loop-distribute-patterns: the start-up copy/zero loops must not
# become memcpy/memset calls (startup.c).
target_compile_options(${FW_IMAGE} PRIVATE ${FW_WARNINGS} ${FW_OPT} -fstack-usage
                       -fno-tree-loop-distribute-patterns)
target_link_libraries(${FW_IMAGE} PRIVATE fw_portable m)
# -nostartfiles: startup.c is the only start-up code (no crt0: its exit()
# path would pull in stdio and the heap).
target_link_options(${FW_IMAGE} PRIVATE
    "-nostartfiles"
    "-T${CMAKE_SOURCE_DIR}/port_stm32/stm32f411re.ld"
    "-Wl,-Map=${CMAKE_BINARY_DIR}/${FW_IMAGE}.map"
    "-Wl,--print-memory-usage"
    "-Wl,--no-warn-rwx-segments")

add_custom_command(TARGET ${FW_IMAGE} POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O ihex "$<TARGET_FILE:${FW_IMAGE}>" "${CMAKE_BINARY_DIR}/${FW_IMAGE}.hex"
    COMMAND ${CMAKE_OBJCOPY} -O binary "$<TARGET_FILE:${FW_IMAGE}>" "${CMAKE_BINARY_DIR}/${FW_IMAGE}.bin"
    COMMAND ${CMAKE_SIZE} -A -x "$<TARGET_FILE:${FW_IMAGE}>"
    COMMENT "objcopy ${FW_IMAGE}.hex / .bin, section sizes"
    VERBATIM)
