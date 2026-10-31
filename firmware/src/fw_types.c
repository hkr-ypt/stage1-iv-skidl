/*
 * fw_types.c — wire names of the shared enumerations (docs/firmware/protocol.md).
 */
#include "fw_types.h"

const char *state_name(inst_state_t s)
{
    static const char *const names[ST_COUNT_] = {
        [ST_BOOT] = "BOOT",           [ST_DISARMED] = "DISARMED",
        [ST_ARMING] = "ARMING",       [ST_ARMED] = "ARMED",
        [ST_SWEEPING] = "SWEEPING",   [ST_DISARMING] = "DISARMING",
        [ST_FAULT] = "FAULT",
    };
    return ((unsigned)s < (unsigned)ST_COUNT_) ? names[s] : "UNKNOWN";
}

const char *fault_name(fault_t f)
{
    static const char *const names[F_COUNT_] = {
        [F_NONE] = "NONE",                 [F_POST] = "POST",
        [F_ADC_TIMEOUT] = "ADC_TIMEOUT",   [F_ADC_COMM] = "ADC_COMM",
        [F_DAC_SPI] = "DAC_SPI",           [F_LOOP_OVERRUN] = "LOOP_OVERRUN",
        [F_INVARIANT] = "INVARIANT",       [F_AFE] = "AFE",
        [F_STALE] = "STALE",               [F_DISARM_TIMEOUT] = "DISARM_TIMEOUT",
        [F_CLOCK] = "CLOCK",               [F_STACK] = "STACK",
        [F_COMPLIANCE] = "COMPLIANCE",
    };
    return ((unsigned)f < (unsigned)F_COUNT_) ? names[f] : "UNKNOWN";
}

const char *err_name(err_t e)
{
    static const char *const names[E_COUNT_] = {
        [E_OK] = "OK",           [E_SYNTAX] = "SYNTAX",   [E_UNKNOWN] = "UNKNOWN",
        [E_ARG] = "ARG",         [E_RANGE] = "RANGE",     [E_STATE] = "STATE",
        [E_BUSY] = "BUSY",       [E_FAULT] = "FAULT",     [E_PRECOND] = "PRECOND",
        [E_FLASH] = "FLASH",     [E_INTERNAL] = "INTERNAL",
    };
    return ((unsigned)e < (unsigned)E_COUNT_) ? names[e] : "INTERNAL";
}

const char *crash_cause_name(uint32_t cause)
{
    switch ((crash_cause_t)cause) {
    case CRASH_NONE:       return "none";
    case CRASH_HARDFAULT:  return "hardfault";
    case CRASH_MEMMANAGE:  return "memmanage";
    case CRASH_BUSFAULT:   return "busfault";
    case CRASH_USAGEFAULT: return "usagefault";
    case CRASH_NMI:        return "nmi";
    case CRASH_CSS:        return "css";
    case CRASH_DEADMAN:    return "deadman";
    case CRASH_SOFT:       return "soft";
    }
    return "unknown";
}
