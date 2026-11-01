# Toolchain file: GNU Arm Embedded (arm-none-eabi-gcc), Cortex-M4F with FPU.
# Verified with Ubuntu 24.04 gcc-arm-none-eabi 15:13.2.rel1-2 (GCC 13.2.1).
set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR arm)
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

set(CMAKE_C_COMPILER   arm-none-eabi-gcc)
set(CMAKE_ASM_COMPILER arm-none-eabi-gcc)
set(CMAKE_OBJCOPY      arm-none-eabi-objcopy CACHE FILEPATH "objcopy")
set(CMAKE_SIZE         arm-none-eabi-size    CACHE FILEPATH "size")

set(FW_CPU_FLAGS "-mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard")
set(CMAKE_C_FLAGS_INIT   "${FW_CPU_FLAGS} -ffunction-sections -fdata-sections -fno-common")
set(CMAKE_ASM_FLAGS_INIT "${FW_CPU_FLAGS} -x assembler-with-cpp")
set(CMAKE_EXE_LINKER_FLAGS_INIT "${FW_CPU_FLAGS} -Wl,--gc-sections --specs=nano.specs --specs=nosys.specs")

# Reproducible objects: no absolute build paths in debug info or __FILE__.
set(CMAKE_C_FLAGS_INIT "${CMAKE_C_FLAGS_INIT} -ffile-prefix-map=${CMAKE_SOURCE_DIR}=.")

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
