/*
 * instrument.c — operating-state machine (contract: instrument.h;
 * firmware_spec.md §3.2, §6).
 *
 * Facts relied on (spec §4.4, from the charge-pump simulation of R36/C47/D6/
 * C48/R37 and TMUX1101 SCDS410D Table 8-1):
 *  - the reference gate is guaranteed on 7 ms after the first heartbeat edge
 *    (95 % of steady state by 17 ms): ARMED is reported T_ARM = 100 ms after
 *    the first edge;
 *  - it is guaranteed off 63 ms after the last edge with PC5 low (77 ms if
 *    left high; PC5 is always left low): DISARMED is reported T_DISARM =
 *    100 ms after the last edge;
 *  - a disarm ramp of 10 V/s needs <= 1.0 s from +-10 V; its deadline is
 *    DISARM_DEADLINE_US = 1.1 s.
 */
#include "instrument.h"

#include "ads1256.h"
#include "config.h"
#include "dac8552.h"
#include "engine.h"
#include "heartbeat.h"
#include "port.h"
#include "proto.h"
#include "safety.h"
#include "scaling.h"
#include "session.h"

static struct {
    inst_state_t    st;
    fault_t         fault;
    const char     *detail;
    disarm_reason_t reason;
    uint64_t        t_state;
    uint64_t        t_disarming;
    bool            disarm_hb_off;     /* DISARMING: zero loaded, PC5 stopped */
    uint64_t        t_stale_ref;       /* staleness reference at ARM          */
    bool            hb_hold;           /* bring-up HB.HOLD active             */
    uint64_t        t_hb_hold_end;
} s_i;

const char *disarm_reason_name(disarm_reason_t r)
{
    switch (r) {
    case DR_COMMAND:      return "command";
    case DR_SESSION_LOST: return "session_lost";
    case DR_HOST_TIMEOUT: return "host_timeout";
    case DR_FAULT:        return "fault";
    case DR_RESET:        return "reset";
    case DR_NONE:
    default:              return "none";
    }
}

inst_state_t inst_state(void)
{
    return s_i.st;
}

fault_t inst_fault_code(void)
{
    return s_i.fault;
}

const char *inst_fault_detail(void)
{
    return s_i.detail;
}

disarm_reason_t inst_disarm_reason(void)
{
    return s_i.reason;
}

bool inst_output_armed(void)
{
    return s_i.st == ST_ARMING || s_i.st == ST_ARMED || s_i.st == ST_SWEEPING;
}

bool inst_hb_permitted(void)
{
    if (s_i.fault != F_NONE) {
        return false;
    }
    return inst_output_armed() || (s_i.st == ST_DISARMING && !s_i.disarm_hb_off);
}

/* ---- events ------------------------------------------------------------------ */

static void emit_state(void)
{
    char buf[96];
    lb_t b;
    lb_init(&b, buf, sizeof buf);
    lb_puts(&b, "EVT STATE ");
    lb_puts(&b, state_name(s_i.st));
    if (s_i.st == ST_FAULT) {
        lb_kv_s(&b, "fault", fault_name(s_i.fault));
    }
    if (s_i.st == ST_DISARMING || (s_i.st == ST_DISARMED && s_i.reason != DR_NONE)) {
        lb_kv_s(&b, "reason", disarm_reason_name(s_i.reason));
    }
    proto_event_ordered(&b);
}

static void set_state(inst_state_t st)
{
    if (st == s_i.st) {
        return;
    }
    s_i.st = st;
    s_i.t_state = port_now_us();
    emit_state();
}

static void emit_warn(const char *what)
{
    char buf[48];
    lb_t b;
    lb_init(&b, buf, sizeof buf);
    lb_puts(&b, "EVT WARN ");
    lb_puts(&b, what);
    proto_event_lb(&b);
}

/* ---- lifecycle ------------------------------------------------------------------ */

void inst_init(void)
{
    s_i.st = ST_BOOT;
    s_i.fault = F_NONE;
    s_i.detail = "";
    s_i.t_state = port_now_us();
    s_i.t_disarming = 0;
    s_i.disarm_hb_off = false;
    s_i.t_stale_ref = 0;
    s_i.hb_hold = false;
    s_i.t_hb_hold_end = 0;
    /* SI-6: a reset never leaves the output armed. Report it when the reset
     * was not a plain power-on (the previous run may have been armed). */
    uint32_t rf = port_reset_flags();
    crash_record_t cr;
    bool crash = port_crash_get(&cr);
    s_i.reason = ((rf & RST_POR) == 0u && (rf != 0u || crash)) ? DR_RESET : DR_NONE;
    if (!port_hse_ok()) {
        inst_fault(F_CLOCK, "hse");
    }
}

void inst_post_done(bool passed, const char *detail)
{
    if (s_i.st != ST_BOOT) {
        return;   /* e.g. F_CLOCK already latched at boot */
    }
    if (passed) {
        set_state(ST_DISARMED);
    } else {
        inst_fault(F_POST, detail);
    }
}

/* ---- fault entry (spec §6.3) ------------------------------------------------------ */

void inst_fault(fault_t f, const char *detail)
{
    if (s_i.fault != F_NONE || f == F_NONE) {
        return;
    }
    /* SI-4: zero pair (PC5 forced low first inside), then the heartbeat stops */
    dac_zero_safe();
    hb_disable();
    bool was_armed = inst_output_armed() || s_i.st == ST_DISARMING;
    s_i.fault = f;
    s_i.detail = detail ? detail : "";
    if (was_armed) {
        s_i.reason = DR_FAULT;
    }
    engine_fault_entry(f);

    char buf[80];
    lb_t b;
    lb_init(&b, buf, sizeof buf);
    lb_puts(&b, "EVT FAULT ");
    lb_puts(&b, fault_name(f));
    lb_kv_s(&b, "detail", s_i.detail[0] ? s_i.detail : "none");
    proto_event_lb(&b);
    set_state(ST_FAULT);
}

static bool adc_class(fault_t f)
{
    return f == F_POST || f == F_ADC_TIMEOUT || f == F_ADC_COMM || f == F_STALE;
}

err_t inst_fault_clear(const char **why)
{
    *why = "";
    if (s_i.st != ST_FAULT) {
        *why = "not in FAULT";
        return E_STATE;
    }
    if (s_i.fault == F_CLOCK) {
        *why = "clock";
        return E_PRECOND;
    }
    if (s_i.fault == F_STACK || !port_stack_ok()) {
        *why = "stack";
        return E_PRECOND;
    }
    const char *d;
    if (safety_check_invariants(ST_DISARMED, hb_enabled(), &d) != F_NONE) {
        *why = "invariant";
        return E_PRECOND;
    }
    hb_stats_t hs;
    hb_get_stats(&hs);
    uint64_t now = port_now_us();
    if (hs.edges != 0u && now - hs.last_edge_us < T_DISARM_US) {
        *why = "wait";
        return E_PRECOND;
    }
    /* After an ADC-class fault the ADC is re-initialised and POST must pass
     * again; after any other fault that interrupted a POST/SELFTEST
     * (engine_fault_entry clears post_passed) the self-test is re-run too, or
     * ARM would stay refused with 'post' after a successful FAULT.CLR. */
    if (!engine_post_passed() || (adc_class(s_i.fault) && !engine_adc_ready())) {
        if (!engine_job_active()) {
            (void)engine_job_start(JOB_POST, 0);
        }
        *why = "adc";
        return E_PRECOND;
    }
    if (!dac_update(engine_zero_code())) {
        *why = "dac";
        return E_PRECOND;
    }
    s_i.fault = F_NONE;
    s_i.detail = "";
    set_state(ST_DISARMED);
    return E_OK;
}

/* ---- arm / disarm --------------------------------------------------------------- */

err_t inst_arm_check(const char **why)
{
    *why = "";
    if (s_i.st == ST_FAULT) {
        *why = fault_name(s_i.fault);
        return E_FAULT;
    }
    if (s_i.st != ST_DISARMED) {
        *why = state_name(s_i.st);
        return E_STATE;
    }
    uint64_t now = port_now_us();
    if (!session_active()) {
        *why = "session";
        return E_PRECOND;
    }
    if (!engine_post_passed()) {
        *why = "post";
        return E_PRECOND;
    }
    if (!engine_adc_ready()) {
        *why = "adc";
        return E_PRECOND;
    }
    if (engine_job_active()) {
        *why = "busy";    /* e.g. a CAL.SAVE that outlived its session (SI-8) */
        return E_PRECOND;
    }
    if (s_i.hb_hold || (s_i.t_hb_hold_end != 0u && now - s_i.t_hb_hold_end < T_DISARM_US)) {
        *why = "hb";
        return E_PRECOND;
    }
    if (engine_afe() != AFE_OK || now - engine_afe_time_us() > AFE_CHECK_MAX_AGE_US) {
        *why = "afe";
        return E_PRECOND;
    }
    if (!cfg_valid(cfg_get())) {
        *why = "config";
        return E_PRECOND;
    }
    uint16_t code;
    if (scaling_v_to_code(engine_cal(), 0, V_LIMIT_MAX_UV, &code) != E_OK) {
        *why = "cal";
        return E_PRECOND;
    }
    if (port_bor_level() != 3) {
        *why = "bor";
        return E_PRECOND;
    }
    return E_OK;
}

void inst_arm(void)
{
    /* SI-3: the zero pair is loaded before the first edge. */
    if (!engine_arm_prepare()) {
        inst_fault(F_DAC_SPI, "dac_spi");
        return;
    }
    s_i.reason = DR_NONE;
    s_i.t_stale_ref = port_now_us();
    hb_enable();
    set_state(ST_ARMING);
}

void inst_disarm(disarm_reason_t r)
{
    if (!inst_output_armed()) {
        return;
    }
    bool sweeping = (s_i.st == ST_SWEEPING);
    /* leave SWEEPING first so the sweep end does not report ARMED */
    s_i.st = ST_DISARMING;
    s_i.reason = r;
    if (sweeping) {
        engine_sweep_abort(r == DR_SESSION_LOST ? SWS_SESSION_LOST
                           : r == DR_HOST_TIMEOUT ? SWS_HOST_TIMEOUT : SWS_ABORTED);
    }
    if (r == DR_HOST_TIMEOUT) {
        emit_warn("HOST_TIMEOUT");
    } else if (r == DR_SESSION_LOST) {
        emit_warn("SESSION_LOST");
    }
    s_i.t_disarming = port_now_us();
    s_i.t_state = s_i.t_disarming;
    s_i.disarm_hb_off = false;
    engine_disarm_begin();
    emit_state();
}

void inst_sweep_started(void)
{
    if (s_i.st == ST_ARMED) {
        set_state(ST_SWEEPING);
    }
}

void inst_sweep_finished(void)
{
    /* a fault ends the sweep from inst_fault(): SWEEPING -> FAULT directly */
    if (s_i.st == ST_SWEEPING && s_i.fault == F_NONE) {
        set_state(ST_ARMED);
    }
}

void inst_note_hb_hold(uint64_t now_us, bool holding)
{
    if (s_i.hb_hold && !holding) {
        s_i.t_hb_hold_end = now_us;
    }
    s_i.hb_hold = holding;
}

/* ---- per-pass evaluation ------------------------------------------------------------ */

static void check_adc(void)
{
    if (ads_state() != ADS_ERROR || s_i.fault != F_NONE) {
        return;
    }
    if (engine_job_active()) {
        return;   /* POST / SELFTEST own the outcome */
    }
    switch (ads_error()) {
    case ADS_ERR_TIMEOUT:  inst_fault(F_ADC_TIMEOUT, "adc_timeout"); break;
    case ADS_ERR_READBACK: inst_fault(F_ADC_COMM, "adc_readback"); break;
    case ADS_ERR_STUCK:    inst_fault(F_ADC_COMM, "adc_stuck"); break;
    case ADS_ERR_SPI:      inst_fault(F_ADC_COMM, "adc_spi"); break;
    case ADS_ERR_NONE:
    default:               inst_fault(F_ADC_COMM, "adc"); break;
    }
}

static void check_stale(uint64_t now)
{
    if (!hb_enabled()) {
        return;
    }
    const ads_rate_t *r = ads_rate_by_sps_x10(cfg_get()->adc_rate_x10);
    uint32_t t18 = r ? r->t18_us : 400180u;
    uint64_t ref = engine_last_conversion_us();
    if (s_i.t_stale_ref > ref) {
        ref = s_i.t_stale_ref;
    }
    if (now - ref > safety_t_stale_us(t18)) {
        inst_fault(F_STALE, "stale");
    }
}

void inst_service(uint64_t now_us)
{
    const char *d;
    fault_t f = safety_check_invariants(s_i.st, hb_enabled(), &d);
    if (f != F_NONE) {
        inst_fault(f, d);
    }
    check_adc();
    if (s_i.fault == F_NONE) {
        check_stale(now_us);
    }

    switch (s_i.st) {
    case ST_ARMING: {
        hb_stats_t hs;
        hb_get_stats(&hs);
        if (hb_started() && now_us - hs.first_edge_us >= T_ARM_US) {
            set_state(ST_ARMED);
        }
        break;
    }
    case ST_DISARMING:
        if (!s_i.disarm_hb_off) {
            if (engine_disarm_done()) {
                hb_disable();                 /* the zero code is loaded (SI-4) */
                s_i.disarm_hb_off = true;
            } else if (now_us - s_i.t_disarming > DISARM_DEADLINE_US) {
                inst_fault(F_DISARM_TIMEOUT, "deadline");
            }
        } else {
            hb_stats_t hs;
            hb_get_stats(&hs);
            if (now_us - hs.last_edge_us >= T_DISARM_US) {
                set_state(ST_DISARMED);
            }
        }
        break;
    case ST_BOOT:
    case ST_DISARMED:
    case ST_ARMED:
    case ST_SWEEPING:
    case ST_FAULT:
    case ST_COUNT_:
    default:
        break;
    }
}

uint32_t inst_warnings(uint64_t now_us)
{
    uint32_t w = 0;
    if (port_bor_level() != 3) {
        w |= W_BOR_OFF;
    }
    if (engine_cal_nominal()) {
        w |= W_CAL_NOMINAL;
    }
    if (engine_zero_state(now_us) != ZERO_VALID) {
        w |= W_ZERO_STALE;
    }
    crash_record_t cr;
    if (port_crash_get(&cr)) {
        w |= W_CRASH;
    }
    w |= engine_warn_bits();
    proto_stats_t ps;
    proto_get_stats(&ps);
    if (ps.evt_lost != 0u) {
        w |= W_EVT_LOST;
    }
    if (engine_afe() == AFE_FAIL) {
        w |= W_AFE;
    }
    const calib_scan_t *sc = engine_cal_scan();
    for (unsigned k = 0; k < 2u; k++) {
        if (sc->status[k] == CALREC_WRONG_UID) {
            w |= W_CAL_UID;
        } else if (sc->status[k] != CALREC_VALID && sc->status[k] != CALREC_ERASED) {
            w |= W_CAL_DAMAGED;
        }
    }
#if !FW_USB_PID_REGISTERED
    w |= W_DEV_USB_PID;
#endif
    return w;
}
