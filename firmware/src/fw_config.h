/*
 * fw_config.h — build variant and global constants of the Stage 1 I-V firmware.
 *
 * The build variant is chosen by CMake (FW_BUILD_VARIANT = release | debug |
 * bringup) and arrives here as exactly one of FW_VARIANT_RELEASE,
 * FW_VARIANT_DEBUG or FW_VARIANT_BRINGUP. FW_BRINGUP gates every command that
 * can break a safety invariant (DAC.RAW, HB.*, ADC.PGA, ADC.RESETPULSE,
 * CRASH); it is 1 only in the bringup variant and the build refuses any other
 * combination. See docs/firmware/build.md.
 */
#ifndef FW_CONFIG_H
#define FW_CONFIG_H

#if (defined(FW_VARIANT_RELEASE) + defined(FW_VARIANT_DEBUG) + defined(FW_VARIANT_BRINGUP)) != 1
#error "exactly one of FW_VARIANT_RELEASE, FW_VARIANT_DEBUG, FW_VARIANT_BRINGUP must be defined"
#endif

#if defined(FW_VARIANT_BRINGUP)
#define FW_BRINGUP 1
#define FW_VARIANT_NAME "bringup"
#elif defined(FW_VARIANT_DEBUG)
#define FW_BRINGUP 0
#define FW_VARIANT_NAME "debug"
#else
#define FW_BRINGUP 0
#define FW_VARIANT_NAME "release"
#endif

#ifndef FW_VERSION_STRING
#define FW_VERSION_STRING "0.1.0-dev"
#endif
#define FW_PROTOCOL_VERSION 1
#define FW_HW_REV "stage1-iv@8a84b55"

/* ---- timing model (docs/firmware/firmware_spec.md §4.4, §7.1) ---------- */
#define HB_HALF_PERIOD_US        250u     /* 2 kHz square wave on a fixed grid   */
#define HB_GAP_FAULT_US          1000u    /* any edge spacing above this faults  */
#define HB_SPACING_WARN_US       500u     /* counted in DIAG?                    */
#define T_ARM_US                 100000u  /* first edge -> ARMED                 */
#define T_DISARM_US              100000u  /* last edge (PC5 low) -> DISARMED     */
#define DEADMAN_TIMEOUT_US       2000u    /* TIM5 dead-man (target only)         */
#define PASS_BUDGET_US           150u     /* design budget for one loop pass     */
#define PASS_OVERRUN_COUNT_US    400u     /* passes longer than this are counted */
#define DISARM_DEADLINE_US       1100000u /* DISARMING ramp must finish by then  */
#define DISARM_MIN_RAMP_UV_PER_S 10000000 /* 10 V/s floor for the disarm ramp    */
#define HOST_TIMEOUT_DEFAULT_S   30u
#define DAC_SCRUB_PERIOD_US      100000u
#define ADC_SCRUB_PERIOD_US      1000000u
#define MONITOR_PERIOD_US        100000u
#define AFE_CHECK_MAX_AGE_US     1000000u
#define VBUS_DEBOUNCE_US         20000u
#define VBUS_ATTACH_MV           3800u    /* VBUS (not pin) voltage thresholds    */
#define VBUS_DETACH_MV           3000u

/* ---- output limits ------------------------------------------------------ */
#define V_LIMIT_MAX_UV           10000000 /* 10.000 V absolute software ceiling  */
#define SETTLE_MIN_US            100u
#define SETTLE_MAX_US            60000000u
#define RAMP_STEP_MAX_UV         10000    /* <= 10 mV per ramp step               */
#define RAMP_STEP_PERIOD_US      1000u    /* >= 1 ms between ramp steps           */

/* ---- capacities --------------------------------------------------------- */
#define RESULTS_CAPACITY         1024u
#define SWEEP_LIST_MAX           2048u
#define SWEEP_WP_MAX             16u
#define SWEEP_POINTS_MAX         1000000u
#define SWEEP_STEP_MIN_UV        500
#define SWEEP_CYCLES_MAX         1000u
#define N_AVG_MAX                256u
#define MEAS_MAX_DURATION_US     10000000u /* MEAS? and ZERO are bounded to 10 s */
#define ZERO_PAIRS_MAX           64u
#define PROTO_LINE_MAX           256u
#define PROTO_RX_RING            1024u
#define PROTO_TX_RING            4096u
#define PROTO_RESP_MAX           768u
#define LIST_ADD_MAX_POINTS      8u

#endif /* FW_CONFIG_H */
