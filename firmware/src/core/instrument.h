/*
 * instrument.h — the operating-state machine of firmware_spec.md §6:
 * BOOT, DISARMED, ARMING, ARMED, SWEEPING, DISARMING, FAULT; the arm
 * preconditions, the normative arm/disarm/fault sequences (§6.3), the fault
 * latch and the per-pass health evaluation (SI-9).
 *
 * Invariants kept here:
 *  - SI-3/SI-4: ARM loads the zero code before hb_enable(); DISARMING loads
 *    it before hb_disable(); FAULT entry runs dac_zero_safe() before
 *    hb_disable().
 *  - SI-6: nothing but inst_arm() (the ARM command) leaves DISARMED for an
 *    armed state; boot always ends in DISARMED or FAULT.
 *  - SI-7: session loss / host timeout in an armed state -> DISARMING with
 *    the heartbeat still running.
 */
#ifndef INSTRUMENT_H
#define INSTRUMENT_H

#include "fw_types.h"

typedef enum {
    DR_NONE = 0, DR_COMMAND, DR_SESSION_LOST, DR_HOST_TIMEOUT, DR_FAULT, DR_RESET,
} disarm_reason_t;
const char *disarm_reason_name(disarm_reason_t r);

void            inst_init(void);
void            inst_service(uint64_t now_us);
inst_state_t    inst_state(void);
fault_t         inst_fault_code(void);
const char     *inst_fault_detail(void);
disarm_reason_t inst_disarm_reason(void);
/* W_* bits for STAT?. */
uint32_t        inst_warnings(uint64_t now_us);
/* ARMING, ARMED or SWEEPING: the states that need a host session. */
bool            inst_output_armed(void);
/* The health predicate passed to hb_service(). */
bool            inst_hb_permitted(void);

/* ARM: E_OK, E_STATE, E_FAULT or E_PRECOND with *why = the precondition. */
err_t           inst_arm_check(const char **why);
void            inst_arm(void);
/* DISARM command / session loss / host timeout. No-op unless armed. */
void            inst_disarm(disarm_reason_t r);
/* FAULT.CLR: E_OK, E_STATE or E_PRECOND with *why. */
err_t           inst_fault_clear(const char **why);
/* Latch a fault and run the FAULT entry sequence (no-op if one is latched). */
void            inst_fault(fault_t f, const char *detail);
/* POST finished (BOOT -> DISARMED, or FAULT(F_POST)). */
void            inst_post_done(bool passed, const char *detail);
void            inst_sweep_started(void);   /* ARMED -> SWEEPING */
void            inst_sweep_finished(void);  /* SWEEPING -> ARMED */
/* Bring-up only: ARM also needs PC5 free of HB.HOLD for T_DISARM. */
void            inst_note_hb_hold(uint64_t now_us, bool holding);

#endif /* INSTRUMENT_H */
