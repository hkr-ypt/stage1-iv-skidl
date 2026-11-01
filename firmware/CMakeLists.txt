# Stage 1 I-V Characterization System — firmware, simulator and unit tests.
#
# One source tree, two kinds of build (see docs/firmware/build.md):
#   * cross build for the STM32F411RET6 (toolchain file cmake/arm-none-eabi.cmake):
#       presets fw-release, fw-debug, fw-bringup
#   * native build of the unit tests and the whole-firmware simulator:
#       presets host (release variant), host-bringup, coverage
#
# Third-party sources are fetched by commit hash with tools/fetch_deps.sh into
# third_party/ (gitignored); see third_party.lock.
cmake_minimum_required(VERSION 3.25)
project(stage1_iv_firmware C ASM)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ---- build variant ---------------------------------------------------------------
set(FW_BUILD_VARIANT "release" CACHE STRING "Firmware variant: release | debug | bringup")
set_property(CACHE FW_BUILD_VARIANT PROPERTY STRINGS release debug bringup)
if(NOT FW_BUILD_VARIANT MATCHES "^(release|debug|bringup)$")
    message(FATAL_ERROR "FW_BUILD_VARIANT must be release, debug or bringup (got '${FW_BUILD_VARIANT}')")
endif()
string(TOUPPER "${FW_BUILD_VARIANT}" FW_VARIANT_UPPER)

# ---- USB identity (docs/firmware/usb_identity.md) ------------------------------------
# VID 0x1209 is pid.codes. The project's planned PID designation "500S" is NOT
# registered and is not a valid hex PID, so it is never put into a descriptor.
# Until a PID is registered the build uses 0x0001, the pid.codes PID reserved
# for private in-house testing (not unique; must not ship).
set(FW_USB_PID "0x0001" CACHE STRING "USB idProduct (0x0001 = pid.codes private-testing PID)")
option(FW_USB_PID_REGISTERED "ON only when FW_USB_PID is the PID registered for this project" OFF)
if(FW_USB_PID_REGISTERED AND FW_USB_PID STREQUAL "0x0001")
    message(FATAL_ERROR "FW_USB_PID_REGISTERED=ON requires a registered PID, not the 0x0001 test PID")
endif()
if(NOT FW_USB_PID MATCHES "^0x[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]$")
    message(FATAL_ERROR "FW_USB_PID must be a 4-digit hex number such as 0x0001")
endif()

# ---- source revision: generated/fw_revision.h, re-evaluated at every build ------------
set(FW_GENERATED_DIR "${CMAKE_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${FW_GENERATED_DIR}")
add_custom_target(fw_revision
    COMMAND "${CMAKE_COMMAND}" "-DSRC=${CMAKE_SOURCE_DIR}"
            "-DOUT=${FW_GENERATED_DIR}/fw_revision.h"
            -P "${CMAKE_SOURCE_DIR}/cmake/fw_revision.cmake"
    BYPRODUCTS "${FW_GENERATED_DIR}/fw_revision.h"
    COMMENT "Source revision"
    VERBATIM)
set(FW_VERSION "0.1.0")

# ---- third-party sources -------------------------------------------------------------
set(FW_THIRD_PARTY "${CMAKE_SOURCE_DIR}/third_party" CACHE PATH "Fetched third-party sources")
foreach(dep unity tinyusb cmsis_device_f4 cmsis_core)
    if(NOT EXISTS "${FW_THIRD_PARTY}/${dep}")
        message(FATAL_ERROR "third_party/${dep} is missing: run firmware/tools/fetch_deps.sh")
    endif()
endforeach()

# ---- common compile settings ------------------------------------------------------------
set(FW_WARNINGS
    -Wall -Wextra -Werror -Wconversion -Wsign-conversion -Wshadow -Wdouble-promotion
    -Wstrict-prototypes -Wmissing-prototypes -Wundef -Wcast-align -Wformat=2)
set(FW_DEFINES
    FW_VARIANT_${FW_VARIANT_UPPER}=1
    FW_VERSION_STRING="${FW_VERSION}"
    FW_USB_PID=${FW_USB_PID}
    FW_USB_PID_REGISTERED=$<IF:$<BOOL:${FW_USB_PID_REGISTERED}>,1,0>)

file(GLOB FW_PORTABLE_SRC CONFIGURE_DEPENDS
    "${CMAKE_SOURCE_DIR}/src/*.c"
    "${CMAKE_SOURCE_DIR}/src/app/*.c"
    "${CMAKE_SOURCE_DIR}/src/core/*.c"
    "${CMAKE_SOURCE_DIR}/src/drivers/*.c")
set(FW_PORTABLE_INCLUDES
    "${CMAKE_SOURCE_DIR}/src"
    "${CMAKE_SOURCE_DIR}/src/port"
    "${CMAKE_SOURCE_DIR}/src/app"
    "${CMAKE_SOURCE_DIR}/src/core"
    "${CMAKE_SOURCE_DIR}/src/drivers")

add_library(fw_portable STATIC ${FW_PORTABLE_SRC})
target_include_directories(fw_portable PUBLIC ${FW_PORTABLE_INCLUDES})
target_include_directories(fw_portable PRIVATE "${FW_GENERATED_DIR}")
add_dependencies(fw_portable fw_revision)
target_compile_definitions(fw_portable PUBLIC ${FW_DEFINES})
target_compile_options(fw_portable PRIVATE ${FW_WARNINGS})

if(CMAKE_CROSSCOMPILING)
    include("${CMAKE_SOURCE_DIR}/port_stm32/target.cmake")
else()
    include("${CMAKE_SOURCE_DIR}/cmake/host.cmake")
endif()
