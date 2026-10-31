# Native build: unit tests, system tests and the whole-firmware simulator.
#
# Test discovery (no CMake edits needed when adding a test):
#   tests/unit/test_<mod>.c        links fw_portable + fw_models + unity, plus
#                                  every tests/unit/support/<mod>/*.c (a minimal
#                                  test port that implements the port.h calls
#                                  the module under test uses);
#   tests/unit/test_sys_<name>.c   links the full simulation port (sim/port_sim.c,
#                                  board/DAC/ADC models) + fw_portable + unity,
#                                  plus tests/unit/support/sys/*.c.
option(FW_SANITIZE "Build host code with AddressSanitizer and UBSan" ON)
option(FW_COVERAGE "Build host code with gcov instrumentation" OFF)

set(FW_HOST_FLAGS -g -O1)
if(FW_SANITIZE)
    list(APPEND FW_HOST_FLAGS -fsanitize=address,undefined -fno-omit-frame-pointer
                              -fno-sanitize-recover=all)
    add_link_options(-fsanitize=address,undefined)
endif()
if(FW_COVERAGE)
    list(APPEND FW_HOST_FLAGS --coverage -O0)
    add_link_options(--coverage)
endif()
target_compile_options(fw_portable PRIVATE ${FW_HOST_FLAGS})

enable_testing()

# ---- Unity ------------------------------------------------------------------------
add_library(unity STATIC "${FW_THIRD_PARTY}/unity/src/unity.c")
target_include_directories(unity PUBLIC "${FW_THIRD_PARTY}/unity/src")
target_compile_definitions(unity PUBLIC UNITY_INCLUDE_DOUBLE UNITY_SUPPORT_64 UNITY_DOUBLE_PRECISION=1e-12)
target_compile_options(unity PRIVATE ${FW_HOST_FLAGS})

# ---- models (DAC, ADC, board) ------------------------------------------------------
file(GLOB FW_MODEL_SRC CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/sim/model_*.c")
add_library(fw_models STATIC ${FW_MODEL_SRC})
target_include_directories(fw_models PUBLIC "${CMAKE_SOURCE_DIR}/sim" ${FW_PORTABLE_INCLUDES})
target_compile_definitions(fw_models PUBLIC ${FW_DEFINES})
target_compile_options(fw_models PRIVATE ${FW_WARNINGS} ${FW_HOST_FLAGS})
target_link_libraries(fw_models PUBLIC m)

# ---- simulation port (object library: every port_* symbol is always linked) --------
set(FW_SIMPORT_SRC "${CMAKE_SOURCE_DIR}/sim/port_sim.c")
add_library(fw_simport OBJECT ${FW_SIMPORT_SRC})
target_include_directories(fw_simport PUBLIC "${CMAKE_SOURCE_DIR}/sim" ${FW_PORTABLE_INCLUDES})
target_compile_definitions(fw_simport PUBLIC ${FW_DEFINES})
target_compile_options(fw_simport PRIVATE ${FW_WARNINGS} ${FW_HOST_FLAGS})

# ---- simulator executable -------------------------------------------------------------
add_executable(stage1_iv_sim "${CMAKE_SOURCE_DIR}/sim/sim_main.c" $<TARGET_OBJECTS:fw_simport>)
target_include_directories(stage1_iv_sim PRIVATE "${CMAKE_SOURCE_DIR}/sim" ${FW_PORTABLE_INCLUDES})
target_compile_options(stage1_iv_sim PRIVATE ${FW_WARNINGS} ${FW_HOST_FLAGS})
target_link_libraries(stage1_iv_sim PRIVATE fw_portable fw_models m util)

# ---- unit and system tests ---------------------------------------------------------------
file(GLOB FW_UNIT_TESTS CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/tests/unit/test_*.c")
foreach(test_src ${FW_UNIT_TESTS})
    get_filename_component(test_name "${test_src}" NAME_WE)
    string(REGEX REPLACE "^test_" "" test_mod "${test_name}")
    if(test_name MATCHES "^test_sys_")
        file(GLOB support_src CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/tests/unit/support/sys/*.c")
        add_executable(${test_name} "${test_src}" ${support_src} $<TARGET_OBJECTS:fw_simport>)
        target_link_libraries(${test_name} PRIVATE fw_portable fw_models unity m)
    else()
        file(GLOB support_src CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/tests/unit/support/${test_mod}/*.c")
        add_executable(${test_name} "${test_src}" ${support_src})
        target_link_libraries(${test_name} PRIVATE fw_portable fw_models unity m)
    endif()
    target_include_directories(${test_name} PRIVATE
        "${CMAKE_SOURCE_DIR}/sim" "${CMAKE_SOURCE_DIR}/tests/unit/support" ${FW_PORTABLE_INCLUDES})
    target_compile_definitions(${test_name} PRIVATE ${FW_DEFINES})
    target_compile_options(${test_name} PRIVATE ${FW_HOST_FLAGS} -Wall -Wextra -Werror)
    add_test(NAME ${test_name} COMMAND ${test_name})
    set_tests_properties(${test_name} PROPERTIES TIMEOUT 300)
endforeach()
