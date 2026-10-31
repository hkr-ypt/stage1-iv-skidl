/*
 * fw_types.h — shared enumerations: instrument states, fault codes, warning
 * bits, protocol error codes and per-point flags. The string names used on
 * the wire are defined next to each enum (fw_types.c) and documented in
 * docs/firmware/protocol.md; changing one is a protocol change.
 */
#ifndef FW_TYPES_H
#define FW_TYPES_H

#include "fw_config.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* ---- instrument state (docs/firmware/firmware_spec.md §6) --------------- */
typedef enum {
    ST_BOOT = 0,
    ST_DISARMED,
    ST_ARMING,
    ST_ARMED,
    ST_SWEEPING,
    ST_DISARMING,
    ST_FAULT,
    ST_COUNT_
} inst_state_t;

/* ---- latched faults (§6.4) ---------------------------------------------- */
typedef enum {
    F_NONE = 0,
    F_POST,             /* power-on self test failed                        */
    F_ADC_TIMEOUT,      /* no valid /DRDY within the conversion timeout      */
    F_ADC_COMM,         /* register read-back mismatch, stuck DOUT, SPI fault */
    F_DAC_SPI,          /* SPI1 transfer did not complete                     */
    F_LOOP_OVERRUN,     /* heartbeat edge spacing above HB_GAP_FAULT_US       */
    F_INVARIANT,        /* internal consistency check failed                  */
    F_AFE,              /* ADC clip code while armed                          */
    F_STALE,            /* no completed conversion within T_STALE while armed */
    F_DISARM_TIMEOUT,   /* the DISARMING ramp missed its deadline             */
    F_CLOCK,            /* HSE did not start (USB unusable)                   */
    F_STACK,            /* stack canary damaged                               */
    F_COMPLIANCE,       /* software compliance with on_compliance=fault       */
    F_COUNT_
} fault_t;

/* ---- warning bits reported in STAT? (not latched faults) ----------------- */
enum {
    W_BOR_OFF      = 1u << 0,  /* option bytes not at BOR level 3: ARM refused  */
    W_CAL_NOMINAL  = 1u << 1,  /* no valid calibration record: nominal constants */
    W_ZERO_STALE   = 1u << 2,  /* no valid session zero                         */
    W_CRASH        = 1u << 3,  /* a crash record from a previous run is held    */
    W_KELVIN       = 1u << 4,  /* KELVIN flagged on >= 3 consecutive points     */
    W_EVT_LOST     = 1u << 5,  /* an event was dropped (TX ring full)           */
    W_AFE          = 1u << 6,  /* last gate-open front-end check failed         */
    W_NO_OUTPUT    = 1u << 7,  /* NO_OUTPUT flagged recently                    */
    W_CAL_UID      = 1u << 8,  /* a stored record belongs to another MCU        */
    W_DEV_USB_PID  = 1u << 9,  /* running with the development USB PID         */
};

/* ---- protocol error codes (docs/firmware/protocol.md) --------------------- */
typedef enum {
    E_OK = 0,
    E_SYNTAX,
    E_UNKNOWN,
    E_ARG,
    E_RANGE,
    E_STATE,
    E_BUSY,
    E_FAULT,
    E_PRECOND,
    E_FLASH,
    E_INTERNAL,
    E_COUNT_
} err_t;

/* ---- per-point flags (PT records) ------------------------------------------ */
enum {
    PF_OVR_V       = 1u << 0,
    PF_OVR_I       = 1u << 1,
    PF_CLIM        = 1u << 2,
    PF_KELVIN      = 1u << 3,
    PF_NO_OUTPUT   = 1u << 4,
    PF_COMPLIANCE  = 1u << 5,
    PF_LATE        = 1u << 6,
    PF_FIRST       = 1u << 7,
    PF_CAL_NOMINAL = 1u << 8,
    PF_ZERO_STALE  = 1u << 9,
    PF_MEAS        = 1u << 10,
};

/* ---- reset causes (decoded from RCC_CSR at boot) ---------------------------- */
enum {
    RST_POR  = 1u << 0,
    RST_PIN  = 1u << 1,
    RST_BOR  = 1u << 2,
    RST_SFT  = 1u << 3,
    RST_IWDG = 1u << 4,
    RST_WWDG = 1u << 5,
    RST_LPWR = 1u << 6,
};

/* ---- crash record kept in .noinit RAM across a reset ------------------------ */
#define CRASH_MAGIC 0x43524153u /* "CRAS" */
typedef enum {
    CRASH_NONE = 0,
    CRASH_HARDFAULT,
    CRASH_MEMMANAGE,
    CRASH_BUSFAULT,
    CRASH_USAGEFAULT,
    CRASH_NMI,
    CRASH_CSS,
    CRASH_DEADMAN,
    CRASH_SOFT,        /* software-requested crash (bring-up CRASH command) */
} crash_cause_t;

typedef struct {
    uint32_t magic;
    uint32_t cause;    /* crash_cause_t */
    uint32_t pc, lr, xpsr;
    uint32_t cfsr, hfsr, mmfar, bfar;
    uint32_t rcc_cr, rcc_cfgr;
    uint32_t crc;      /* crc32 over the preceding words */
} crash_record_t;

/* Wire names (fw_types.c). Each returns a static string, never NULL. */
const char *state_name(inst_state_t s);
const char *fault_name(fault_t f);
const char *err_name(err_t e);
const char *crash_cause_name(uint32_t cause);

#endif /* FW_TYPES_H */
