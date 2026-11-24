/*
 * commands.c — protocol v1 command set (contract: commands.h,
 * docs/firmware/protocol.md §4-§7). No hardware facts beyond those the
 * engine, instrument and drivers already encode.
 *
 * Every handler validates all of its arguments before it changes anything,
 * so a failed command has no effect. Handlers write the response payload
 * (after "OK") into the context; events they cause are held by proto.c and
 * sent after the response. Bring-up-only verbs are compiled only when
 * FW_BRINGUP (fw_config.h); otherwise they are unknown verbs.
 */
#include "commands.h"

#include "ads1256.h"
#include "app.h"
#include "config.h"
#include "dac8552.h"
#include "decimal.h"
#include "engine.h"
#include "heartbeat.h"
#include "instrument.h"
#include "port.h"
#include "results.h"
#include "scaling.h"
#include "session.h"

#include <math.h>
#include <string.h>

/* FW_GIT_REV: generated at every build (cmake/fw_revision.cmake); a build
 * without it (e.g. static analysis alone) reports "unknown" */
#if defined(__has_include)
#if __has_include("fw_revision.h")
#include "fw_revision.h"
#endif
#endif
#ifndef FW_GIT_REV
#define FW_GIT_REV "unknown"
#endif

#define S_(st)   (1u << (st))
#define S_ANY    0xFFu
#define S_DISARMED_ARMED (S_(ST_DISARMED) | S_(ST_ARMED))

/* RESET / CRASH run once the OK has left the TX ring and had time to leave
 * the USB FIFO too (a few frames), at most 200 ms after the command. */
#define PENDING_ACTION_MIN_US     50000u
#define PENDING_ACTION_TIMEOUT_US 200000u

typedef struct {
    const cmdline_t *cl;
    lb_t            *out;       /* payload after "OK"                  */
    lb_t             msg;       /* error message                        */
    char             msg_buf[128];
    bool             deferred;
} ctx_t;

typedef err_t (*handler_t)(ctx_t *c);

typedef struct {
    const char *verb;
    uint8_t     states;
    handler_t   fn;
} cmd_t;

/* ---- module state ----------------------------------------------------------------- */

typedef enum { ACT_NONE = 0, ACT_RESET, ACT_CRASH } action_t;

static struct {
    bool       deferred;
    job_kind_t deferred_kind;
    action_t   action;
    int        crash_kind;
    uint64_t   action_t0;
    /* DIAG.CLR baselines */
    hb_stats_t  hb0;
    ads_stats_t ads0;
    dac_stats_t dac0;
} s_c;

void commands_init(void)
{
    memset(&s_c, 0, sizeof s_c);
}

/* ---- small helpers ------------------------------------------------------------------- */

static err_t fail(ctx_t *c, err_t e, const char *m)
{
    lb_reset(&c->msg);
    lb_puts(&c->msg, m);
    return e;
}

static int64_t rnd(double x)
{
    double f = floor(x + 0.5);
    if (!(f > -9.0e18 && f < 9.0e18)) {
        return 0;
    }
    return (int64_t)f;
}

static int64_t div_round(int64_t v, int64_t d)
{
    return (v >= 0) ? (v + d / 2) / d : -((-v + d / 2) / d);
}

static const tok_t *kv_get(const ctx_t *c, const char *key)
{
    for (unsigned i = 0; i < c->cl->nkv; i++) {
        if (tok_eq(c->cl->key[i], key)) {
            return &c->cl->val[i];
        }
    }
    return NULL;
}

/* Unknown or duplicate keys -> E_ARG. */
/* key names are case-insensitive (protocol.md §3), so are duplicates */
static bool key_eq(tok_t a, tok_t b)
{
    if (a.n != b.n) {
        return false;
    }
    for (uint16_t i = 0; i < a.n; i++) {
        char x = a.s[i];
        char y = b.s[i];
        if (x >= 'A' && x <= 'Z') {
            x = (char)(x - 'A' + 'a');
        }
        if (y >= 'A' && y <= 'Z') {
            y = (char)(y - 'A' + 'a');
        }
        if (x != y) {
            return false;
        }
    }
    return true;
}

static err_t check_keys(ctx_t *c, const char *const *allowed, size_t n)
{
    for (unsigned i = 0; i < c->cl->nkv; i++) {
        bool known = false;
        for (size_t k = 0; k < n; k++) {
            if (tok_eq(c->cl->key[i], allowed[k])) {
                known = true;
                break;
            }
        }
        if (!known) {
            lb_reset(&c->msg);
            lb_puts(&c->msg, "unknown key ");
            lb_putn(&c->msg, c->cl->key[i].s, c->cl->key[i].n);
            return E_ARG;
        }
        for (unsigned j = 0; j < i; j++) {
            if (key_eq(c->cl->key[j], c->cl->key[i])) {
                lb_reset(&c->msg);
                lb_puts(&c->msg, "duplicate key ");
                lb_putn(&c->msg, c->cl->key[i].s, c->cl->key[i].n);
                return E_ARG;
            }
        }
    }
    return E_OK;
}

static err_t no_args(ctx_t *c)
{
    if (c->cl->npos != 0 || c->cl->nkv != 0) {
        return fail(c, E_ARG, "takes no arguments");
    }
    return E_OK;
}

static err_t dec_err(ctx_t *c, dec_status_t st, const char *name)
{
    lb_reset(&c->msg);
    lb_puts(&c->msg, name);
    switch (st) {
    case DEC_INEXACT: lb_puts(&c->msg, " inexact"); return E_ARG;
    case DEC_RANGE:   lb_puts(&c->msg, " out of range"); return E_RANGE;
    case DEC_SYNTAX:
    case DEC_OK:
    default:          lb_puts(&c->msg, " not a number"); return E_ARG;
    }
}

static err_t parse_scaled(ctx_t *c, const tok_t *t, int scale, int64_t min, int64_t max,
                          int64_t *out, const char *name)
{
    dec_status_t st = dec_parse_scaled(t->s, t->n, scale, min, max, out);
    return st == DEC_OK ? E_OK : dec_err(c, st, name);
}

static err_t parse_u32(ctx_t *c, const tok_t *t, uint32_t min, uint32_t max, uint32_t *out,
                       const char *name)
{
    dec_status_t st = dec_parse_u32(t->s, t->n, min, max, out);
    return st == DEC_OK ? E_OK : dec_err(c, st, name);
}

static void put_est_s(lb_t *b, uint64_t est_us)
{
    lb_kv_fix(b, "est_s", (int64_t)((est_us + 5000u) / 10000u), 2);
}

static void put_reset_causes(lb_t *b, uint32_t f)
{
    static const struct { uint32_t bit; const char *name; } k[] = {
        { RST_POR, "por" }, { RST_BOR, "bor" }, { RST_SFT, "sft" }, { RST_IWDG, "iwdg" },
        { RST_WWDG, "wwdg" }, { RST_LPWR, "lpwr" }, { RST_PIN, "pin" },
    };
    if ((f & ~(uint32_t)RST_PIN) != 0u) {
        f &= ~(uint32_t)RST_PIN;   /* PIN accompanies every internal reset: only alone */
    }
    if (f == 0u) {
        lb_puts(b, "none");
        return;
    }
    bool first = true;
    for (size_t i = 0; i < sizeof k / sizeof k[0]; i++) {
        if (f & k[i].bit) {
            if (!first) {
                lb_putc(b, '+');
            }
            lb_puts(b, k[i].name);
            first = false;
        }
    }
}

static void put_serial(lb_t *b)
{
    uint32_t uid[3];
    port_uid(uid);
    char tmp[12];
    for (unsigned i = 0; i < 3; i++) {
        size_t n = dec_format_hex(tmp, sizeof tmp, uid[i], 8);
        lb_putn(b, tmp, n);
    }
}

/* ---- shared formatting ---------------------------------------------------------- */

static void put_cfg_fields(lb_t *b, const cfg_t *c)
{
    lb_kv_fix(b, "adc_rate", c->adc_rate_x10, 1);
    lb_kv_u(b, "n_avg", c->n_avg);
    lb_kv_u(b, "settle_us", c->settle_us);
    lb_kv_u(b, "first_settle_us", c->first_settle_us);
    lb_kv_fix(b, "ramp_rate", c->ramp_rate_uv_s, 6);
    lb_kv_fix(b, "v_limit", c->v_limit_uv, 6);
    lb_kv_fix(b, "i_compliance", c->i_compliance_na, 9);
    lb_kv_s(b, "on_compliance", cfg_on_compliance_name(c->on_compliance));
    lb_kv_s(b, "end", cfg_end_name(c->end));
}

/* CAL.SET / CAL? units and resolutions (protocol.md §5.6). */
typedef struct {
    const char *key;
    int         scale;     /* decimal places                    */
    double      to_si;     /* SI value = integer / to_si        */
} cal_key_t;

static const cal_key_t k_cal_keys[7] = {
    { "g_dac_uv",  6, 1e12 },   /* uV/code, 6 dp -> V/code   */
    { "c_dac",     4, 1e4  },
    { "g_v_uv",    6, 1e12 },
    { "g_i_na",    6, 1e15 },   /* nA/code, 6 dp -> A/code   */
    { "r_s",       4, 1e4  },
    { "r_top",     4, 1e4  },
    { "i_plateau", 9, 1e9  },
};

static double *cal_field(cal_coeffs_t *c, unsigned i)
{
    switch (i) {
    case 0:  return &c->g_dac;
    case 1:  return &c->c_dac;
    case 2:  return &c->g_v;
    case 3:  return &c->g_i;
    case 4:  return &c->r_s;
    case 5:  return &c->r_top;
    default: return &c->i_plateau;
    }
}

static void put_cal_values(lb_t *b)
{
    cal_coeffs_t c = *engine_cal();
    for (unsigned i = 0; i < 7u; i++) {
        lb_kv_fix(b, k_cal_keys[i].key, rnd(*cal_field(&c, i) * k_cal_keys[i].to_si),
                  (unsigned)k_cal_keys[i].scale);
    }
}

static void put_zero_fields(lb_t *b, uint64_t now)
{
    const zero_t *z = engine_zero();
    zero_state_t zs = engine_zero_state(now);
    lb_kv_s(b, "zero", engine_zero_state_name(zs));
    if (z->valid) {
        lb_kv_fix(b, "o_v", rnd(z->o_v * 100.0), 2);
        lb_kv_fix(b, "o_i", rnd(z->o_i * 100.0), 2);
        lb_kv_fix(b, "zero_sps", z->sps_x10, 1);
        lb_kv_u(b, "zero_age_s", (now - z->t_us) / 1000000u);
    } else {
        lb_kv_s(b, "o_v", "-");
        lb_kv_s(b, "o_i", "-");
        lb_kv_s(b, "zero_sps", "-");
        lb_kv_s(b, "zero_age_s", "-");
    }
}

/* ---- identity and status ------------------------------------------------------------ */

static void put_identity(lb_t *b)
{
    lb_kv_s(b, "model", "stage1-iv");
    lb_kv_s(b, "fw", FW_VERSION_STRING);
    lb_kv_s(b, "rev", FW_GIT_REV);
    lb_kv_u(b, "proto", FW_PROTOCOL_VERSION);
    lb_kv_s(b, "hw", FW_HW_REV);
    lb_kv_s(b, "build", FW_VARIANT_NAME);
    lb_key(b, "serial");
    put_serial(b);
}

static err_t h_idn(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    lb_puts(c->out, "IDN");
    put_identity(c->out);
    lb_kv_hex(c->out, "usb_vid", 0x1209u, 4);
    lb_kv_hex(c->out, "usb_pid", (uint32_t)FW_USB_PID, 4);
    lb_kv_s(c->out, "usb_pid_status", FW_USB_PID_REGISTERED ? "registered" : "dev-test");
    lb_kv_s(c->out, "planned_pid", "500S");
    return E_OK;
}

static err_t h_ping(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    lb_puts(c->out, "PONG");
    lb_kv_u(c->out, "t_us", port_now_us());
    return E_OK;
}

static err_t h_stat(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    uint64_t now = port_now_us();
    lb_t *b = c->out;
    const cal_coeffs_t *cal = engine_cal();
    const monitor_t *m = engine_monitor();
    lb_puts(b, "STAT");
    lb_kv_s(b, "state", state_name(inst_state()));
    lb_kv_s(b, "fault", fault_name(inst_fault_code()));
    lb_kv_hex(b, "warn", inst_warnings(now), 4);
    lb_kv_s(b, "disarm_reason", disarm_reason_name(inst_disarm_reason()));
    lb_kv_fix(b, "vset_v", scaling_code_to_v_uv(cal, engine_out_code()), 6);
    lb_kv_u(b, "dac", engine_out_code());
    if (m->i_valid && m->v_valid) {
        lb_kv_fix(b, "vk_v", div_round(scaling_v_nv(cal, engine_zero(), (double)m->v_code), 1000), 6);
        lb_kv_fix(b, "i_a", div_round(scaling_i_pa(cal, engine_zero(), (double)m->i_code), 100), 10);
        uint64_t t = m->i_t_us < m->v_t_us ? m->i_t_us : m->v_t_us;
        lb_kv_u(b, "mon_age_ms", (now - t) / 1000u);
    } else {
        lb_kv_s(b, "vk_v", "-");
        lb_kv_s(b, "i_a", "-");
        lb_kv_s(b, "mon_age_ms", "-");
    }
    lb_kv_s(b, "cal", engine_cal_source_name());
    lb_kv_u(b, "cal_seq", engine_cal_seq());
    const zero_t *z = engine_zero();
    lb_kv_s(b, "zero", engine_zero_state_name(engine_zero_state(now)));
    if (z->valid) {
        lb_kv_u(b, "zero_age_s", (now - z->t_us) / 1000000u);
    } else {
        lb_kv_s(b, "zero_age_s", "-");
    }
    lb_kv_s(b, "afe", engine_afe_name(engine_afe()));
    lb_kv_i(b, "bor", port_bor_level());
    lb_kv_u(b, "vbus_mv", port_vbus_mv());
    lb_kv_u(b, "session", session_active() ? 1u : 0u);
    lb_kv_u(b, "host_timeout_s", cfg_get()->host_timeout_s);
    lb_key(b, "reset");
    put_reset_causes(b, port_reset_flags());
    crash_record_t cr;
    lb_kv_u(b, "crash", port_crash_get(&cr) ? 1u : 0u);
    lb_kv_u(b, "sweep_id", engine_sweep_id());
    lb_kv_u(b, "sweep_pts", engine_sweep_done_points());
    lb_kv_s(b, "sweep_status", sweep_status_name(engine_sweep_status()));
    hb_stats_t hs;
    hb_get_stats(&hs);
    lb_kv_u(b, "hb_edges", hs.edges);
    int32_t t = port_mcu_temp_cdeg();
    if (t == INT32_MIN) {
        lb_kv_s(b, "mcu_temp_c", "na");
    } else {
        lb_kv_fix(b, "mcu_temp_c", t, 2);
    }
    lb_kv_u(b, "uptime_s", now / 1000000u);
    return E_OK;
}

static const char *ads_state_str(ads_state_t s)
{
    switch (s) {
    case ADS_INIT:       return "init";
    case ADS_IDLE:       return "idle";
    case ADS_CONVERTING: return "converting";
    case ADS_ERROR:      return "error";
    case ADS_OFF:
    default:             return "off";
    }
}

static const char *ads_err_str(ads_err_t e)
{
    switch (e) {
    case ADS_ERR_TIMEOUT:  return "timeout";
    case ADS_ERR_READBACK: return "readback";
    case ADS_ERR_SPI:      return "spi";
    case ADS_ERR_STUCK:    return "stuck";
    case ADS_ERR_NONE:
    default:               return "none";
    }
}

static err_t h_diag(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    lb_t *b = c->out;
    app_stats_t as;
    hb_stats_t hs;
    ads_stats_t ds;
    dac_stats_t dd;
    proto_stats_t ps;
    engine_stats_t es;
    app_get_stats(&as);
    hb_get_stats(&hs);
    ads_get_stats(&ds);
    dac_get_stats(&dd);
    proto_get_stats(&ps);
    engine_get_stats(&es);
    lb_puts(b, "DIAG");
    lb_kv_u(b, "passes", as.passes);
    lb_kv_u(b, "pass_max_us", as.pass_max_us);
    lb_kv_u(b, "pass_overruns", as.pass_overruns);
    lb_kv_u(b, "hb_max_spacing_us", hs.max_spacing_us);
    lb_kv_u(b, "hb_late", hs.late_spacings - s_c.hb0.late_spacings);
    lb_kv_u(b, "hb_resyncs", hs.resyncs - s_c.hb0.resyncs);
    lb_kv_s(b, "adc_state", ads_state_str(ads_state()));
    lb_kv_s(b, "adc_err", ads_err_str(ads_error()));
    lb_kv_u(b, "adc_conversions", ds.conversions - s_c.ads0.conversions);
    lb_kv_u(b, "adc_late", ds.late - s_c.ads0.late);
    lb_kv_u(b, "adc_stale_edges", ds.stale_edges - s_c.ads0.stale_edges);
    lb_kv_u(b, "adc_read_overruns", ds.read_overruns - s_c.ads0.read_overruns);
    lb_kv_u(b, "adc_scrubs", ds.scrubs - s_c.ads0.scrubs);
    lb_kv_u(b, "adc_timeouts", ds.timeouts - s_c.ads0.timeouts);
    lb_kv_u(b, "adc_inits", ds.inits - s_c.ads0.inits);
    lb_kv_hex(b, "adc_id", ads_last_id(), 1);
    lb_kv_u(b, "dac_updates", dd.updates - s_c.dac0.updates);
    lb_kv_u(b, "dac_scrubs", dd.scrubs - s_c.dac0.scrubs);
    lb_kv_u(b, "dac_failures", dd.failures - s_c.dac0.failures);
    lb_kv_u(b, "dac_zero_safe", dd.zero_safe - s_c.dac0.zero_safe);
    lb_kv_u(b, "evt_lost", ps.evt_lost);
    lb_kv_u(b, "rx_overflows", ps.rx_overflows);
    lb_kv_u(b, "points", es.points);
    lb_kv_u(b, "late_points", es.late_points);
    lb_kv_u(b, "compliance_trips", es.compliance_trips);
    lb_kv_u(b, "ramp_resyncs", es.ramp_resyncs);
    lb_kv_u(b, "sessions", session_count());
    crash_record_t cr;
    if (port_crash_get(&cr)) {
        lb_kv_s(b, "crash", crash_cause_name(cr.cause));
        lb_kv_hex(b, "crash_pc", cr.pc, 8);
        lb_kv_hex(b, "crash_lr", cr.lr, 8);
        lb_kv_hex(b, "crash_cfsr", cr.cfsr, 8);
    } else {
        lb_kv_s(b, "crash", "none");
        lb_kv_hex(b, "crash_pc", 0, 8);
        lb_kv_hex(b, "crash_lr", 0, 8);
        lb_kv_hex(b, "crash_cfsr", 0, 8);
    }
    return E_OK;
}

static err_t h_diag_clr(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    hb_get_stats(&s_c.hb0);
    ads_get_stats(&s_c.ads0);
    dac_get_stats(&s_c.dac0);
    proto_clear_stats();
    app_clear_stats();
    port_crash_clear();
    return E_OK;
}

/* ---- output control ------------------------------------------------------------------ */

static err_t h_arm(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    if (s_c.action != ACT_NONE) {
        /* a RESET (or bring-up CRASH) is pending: it would hit an armed output */
        return fail(c, E_STATE, "reset pending");
    }
    const char *why;
    e = inst_arm_check(&why);
    if (e != E_OK) {
        return fail(c, e, why);
    }
    inst_arm();
    if (inst_state() == ST_FAULT) {
        /* the zero write before the first edge failed: FAULT is latched */
        return fail(c, E_FAULT, fault_name(inst_fault_code()));
    }
    return E_OK;
}

static err_t h_disarm(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    inst_disarm(DR_COMMAND);   /* no-op in DISARMED / DISARMING */
    return E_OK;
}

static bool raw_active(void)
{
#if FW_BRINGUP
    return dac_raw_active();
#else
    return false;
#endif
}

static err_t h_src(ctx_t *c)
{
    static const char *const keys[] = { "V" };
    err_t e = check_keys(c, keys, 1);
    if (e != E_OK) {
        return e;
    }
    const tok_t *v = kv_get(c, "V");
    if (v == NULL || c->cl->npos != 0) {
        return fail(c, E_ARG, "usage: SRC V=<volts>");
    }
    int64_t uv;
    e = parse_scaled(c, v, 6, -V_LIMIT_MAX_UV, V_LIMIT_MAX_UV, &uv, "V");
    if (e != E_OK) {
        return e;
    }
    if (raw_active()) {
        return fail(c, E_STATE, "DAC.RAW active");
    }
    e = engine_src((int32_t)uv);
    if (e != E_OK) {
        return fail(c, E_RANGE, "beyond v_limit or the DAC range");
    }
    return E_OK;
}

static err_t h_fault_clr(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    const char *why;
    e = inst_fault_clear(&why);
    return e == E_OK ? E_OK : fail(c, e, why);
}

static err_t h_reset(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    s_c.action = ACT_RESET;
    s_c.action_t0 = port_now_us();
    return E_OK;
}

/* ---- measurement -------------------------------------------------------------------- */

static err_t h_meas(ctx_t *c)
{
    static const char *const keys[] = { "N" };
    err_t e = check_keys(c, keys, 1);
    if (e != E_OK) {
        return e;
    }
    if (c->cl->npos != 0) {
        return fail(c, E_ARG, "usage: MEAS? [N=<pairs>]");
    }
    uint32_t n = 0;
    const tok_t *t = kv_get(c, "N");
    if (t != NULL) {
        e = parse_u32(c, t, 1, N_AVG_MAX, &n, "N");
        if (e != E_OK) {
            return e;
        }
    }
    if (inst_state() == ST_ARMED && engine_ramping()) {
        return fail(c, E_BUSY, "ramp in progress");
    }
    e = engine_job_start(JOB_MEAS, n);
    if (e == E_RANGE) {
        return fail(c, e, "duration above 10 s");
    }
    if (e != E_OK) {
        return fail(c, e, "adc busy");
    }
    c->deferred = true;
    return E_OK;
}

static err_t h_zero(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    if (inst_state() == ST_ARMED
        && (engine_ramping() || engine_out_code() != engine_zero_code())) {
        return fail(c, E_STATE, "output not at 0 V");
    }
    e = engine_job_start(JOB_ZERO, 0);
    if (e != E_OK) {
        return fail(c, e, "adc busy");
    }
    c->deferred = true;
    return E_OK;
}

static err_t h_selfcal(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    if (!engine_adc_ready()) {
        return fail(c, E_BUSY, "adc busy");
    }
    e = engine_job_start(JOB_SELFCAL, 0);
    if (e != E_OK) {
        return fail(c, e, "adc busy");
    }
    engine_zero_mark_stale();
    c->deferred = true;
    return E_OK;
}

static err_t h_selftest(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    e = engine_job_start(JOB_SELFTEST, 0);
    if (e != E_OK) {
        return fail(c, e, "adc busy");
    }
    c->deferred = true;
    return E_OK;
}

/* ---- configuration ------------------------------------------------------------------- */

static err_t h_cfg_get(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    const cfg_t *cfg = cfg_get();
    lb_puts(c->out, "CFG");
    put_cfg_fields(c->out, cfg);
    lb_kv_u(c->out, "line_hz", cfg->line_hz);
    lb_kv_u(c->out, "host_timeout_s", cfg->host_timeout_s);
    return E_OK;
}

static err_t parse_word(ctx_t *c, const tok_t *t, const char *a, const char *b, int *out,
                        const char *name)
{
    if (tok_eq(*t, a)) {
        *out = 0;
        return E_OK;
    }
    if (tok_eq(*t, b)) {
        *out = 1;
        return E_OK;
    }
    lb_reset(&c->msg);
    lb_puts(&c->msg, name);
    lb_puts(&c->msg, " must be ");
    lb_puts(&c->msg, a);
    lb_puts(&c->msg, " or ");
    lb_puts(&c->msg, b);
    return E_ARG;
}

static err_t h_cfg(ctx_t *c)
{
    static const char *const keys[] = {
        "adc_rate", "n_avg", "settle_us", "first_settle_us", "ramp_rate", "v_limit",
        "i_compliance", "on_compliance", "end", "line_hz", "host_timeout_s",
    };
    err_t e = check_keys(c, keys, sizeof keys / sizeof keys[0]);
    if (e != E_OK) {
        return e;
    }
    if (c->cl->npos != 0 || c->cl->nkv == 0) {
        return fail(c, E_ARG, "usage: CFG key=value ...");
    }
    cfg_t n = *cfg_get();
    const tok_t *t;
    int64_t v64;
    uint32_t u;
    int w;
    if ((t = kv_get(c, "adc_rate")) != NULL) {
        if ((e = parse_scaled(c, t, 1, 1, 100000, &v64, "adc_rate")) != E_OK) {
            return e;
        }
        if (ads_rate_by_sps_x10((uint32_t)v64) == NULL) {
            return fail(c, E_RANGE, "adc_rate not a v1 rate");
        }
        n.adc_rate_x10 = (uint32_t)v64;
    }
    if ((t = kv_get(c, "n_avg")) != NULL) {
        if ((e = parse_u32(c, t, 1, N_AVG_MAX, &u, "n_avg")) != E_OK) {
            return e;
        }
        n.n_avg = (uint16_t)u;
    }
    if ((t = kv_get(c, "settle_us")) != NULL) {
        if ((e = parse_u32(c, t, SETTLE_MIN_US, SETTLE_MAX_US, &n.settle_us, "settle_us")) != E_OK) {
            return e;
        }
    }
    if ((t = kv_get(c, "first_settle_us")) != NULL) {
        if ((e = parse_u32(c, t, SETTLE_MIN_US, SETTLE_MAX_US, &n.first_settle_us,
                           "first_settle_us")) != E_OK) {
            return e;
        }
    }
    if ((t = kv_get(c, "ramp_rate")) != NULL) {
        if ((e = parse_scaled(c, t, 6, CFG_RAMP_MIN_UV_S, CFG_RAMP_MAX_UV_S, &v64,
                              "ramp_rate")) != E_OK) {
            return e;
        }
        n.ramp_rate_uv_s = (uint32_t)v64;
    }
    if ((t = kv_get(c, "v_limit")) != NULL) {
        if ((e = parse_scaled(c, t, 6, CFG_V_LIMIT_MIN_UV, V_LIMIT_MAX_UV, &v64, "v_limit")) != E_OK) {
            return e;
        }
        n.v_limit_uv = (int32_t)v64;
    }
    if ((t = kv_get(c, "i_compliance")) != NULL) {
        if ((e = parse_scaled(c, t, 9, CFG_I_COMPL_MIN_NA, CFG_I_COMPL_MAX_NA, &v64,
                              "i_compliance")) != E_OK) {
            return e;
        }
        n.i_compliance_na = (uint32_t)v64;
    }
    if ((t = kv_get(c, "on_compliance")) != NULL) {
        if ((e = parse_word(c, t, "stop", "fault", &w, "on_compliance")) != E_OK) {
            return e;
        }
        n.on_compliance = w ? ONC_FAULT : ONC_STOP;
    }
    if ((t = kv_get(c, "end")) != NULL) {
        if ((e = parse_word(c, t, "zero", "hold", &w, "end")) != E_OK) {
            return e;
        }
        n.end = w ? END_HOLD : END_ZERO;
    }
    if ((t = kv_get(c, "line_hz")) != NULL) {
        if ((e = parse_u32(c, t, 50, 60, &u, "line_hz")) != E_OK) {
            return e;
        }
        if (u != 50u && u != 60u) {
            return fail(c, E_RANGE, "line_hz must be 50 or 60");
        }
        n.line_hz = (uint8_t)u;
    }
    if ((t = kv_get(c, "host_timeout_s")) != NULL) {
        if ((e = parse_u32(c, t, 0, CFG_HOST_TIMEOUT_MAX_S, &n.host_timeout_s,
                           "host_timeout_s")) != E_OK) {
            return e;
        }
    }
    if (!cfg_valid(&n)) {
        return fail(c, E_RANGE, "invalid configuration");
    }
    bool rate_change = n.adc_rate_x10 != cfg_get()->adc_rate_x10;
    if (inst_state() == ST_ARMED) {
        if (rate_change) {
            return fail(c, E_STATE, "adc_rate needs DISARMED");
        }
        if (n.v_limit_uv < engine_held_abs_uv()) {
            return fail(c, E_RANGE, "v_limit below the held level");
        }
    }
    if (rate_change && (engine_job_active() || !engine_adc_ready())) {
        return fail(c, E_BUSY, "adc busy");
    }
    uint32_t old_rate = cfg_get()->adc_rate_x10;
    cfg_set(&n);
    if (rate_change && engine_adc_reconfigure(n.adc_rate_x10, engine_adc_pga()) != E_OK) {
        n.adc_rate_x10 = old_rate;
        cfg_set(&n);
        return fail(c, E_BUSY, "adc busy");
    }
    return E_OK;
}

/* ---- sweeps ------------------------------------------------------------------------ */

static void put_swp(lb_t *b)
{
    sweep_kind_t k = engine_sweep_kind();
    lb_puts(b, "SWP");
    lb_kv_s(b, "kind", k == SWEEP_WAYPOINTS ? "wp" : k == SWEEP_LIST ? "list" : "none");
    lb_kv_u(b, "points", engine_sweep_points());
    put_est_s(b, engine_sweep_estimate_us());
}

static err_t swg_err(ctx_t *c, swg_status_t st, int32_t suggest)
{
    switch (st) {
    case SWG_OK:
        return E_OK;
    case SWG_EMPTY:
        return fail(c, E_ARG, "empty definition");
    case SWG_STEP_SMALL:
        return fail(c, E_ARG, "step below 0.5 mV");
    case SWG_NOT_DIVISIBLE:
        lb_reset(&c->msg);
        lb_puts(&c->msg, "not divisible");
        if (suggest > 0) {
            lb_kv_fix(&c->msg, "suggest", suggest, 6);
        } else {
            lb_kv_s(&c->msg, "suggest", "none");
        }
        return E_ARG;
    case SWG_ZERO_SEGMENT:
        return fail(c, E_ARG, "zero-length segment");
    case SWG_LIMIT:
        return fail(c, E_RANGE, "beyond v_limit");
    case SWG_TOO_MANY:
        return fail(c, E_RANGE, "too many points");
    case SWG_CYCLES:
        return fail(c, E_ARG, "cycles out of range or last waypoint != first");
    case SWG_SETTLE:
        return fail(c, E_RANGE, "settle out of range");
    default:
        return fail(c, E_INTERNAL, "sweep");
    }
}

static err_t h_swp_wp(ctx_t *c)
{
    static const char *const keys[] = { "STEP", "CYCLES" };
    err_t e = check_keys(c, keys, 2);
    if (e != E_OK) {
        return e;
    }
    const tok_t *ts = kv_get(c, "STEP");
    if (ts == NULL || c->cl->npos < 2u) {
        return fail(c, E_ARG, "usage: SWP.WP <v0> <v1> ... STEP=<V> [CYCLES=<k>]");
    }
    if (c->cl->npos > SWEEP_WP_MAX) {
        return fail(c, E_ARG, "at most 16 waypoints");
    }
    int32_t wp[SWEEP_WP_MAX];
    for (unsigned i = 0; i < c->cl->npos; i++) {
        int64_t v;
        if ((e = parse_scaled(c, &c->cl->pos[i], 6, -V_LIMIT_MAX_UV, V_LIMIT_MAX_UV, &v,
                              "waypoint")) != E_OK) {
            return e;
        }
        wp[i] = (int32_t)v;
    }
    int64_t step;
    if ((e = parse_scaled(c, ts, 6, 1, 2 * (int64_t)V_LIMIT_MAX_UV, &step, "STEP")) != E_OK) {
        return e;
    }
    uint32_t cycles = 1;
    const tok_t *tc = kv_get(c, "CYCLES");
    if (tc != NULL && (e = parse_u32(c, tc, 1, SWEEP_CYCLES_MAX, &cycles, "CYCLES")) != E_OK) {
        return e;
    }
    uint32_t points = 0;
    int32_t suggest = 0;
    swg_status_t st = engine_sweep_define_wp(wp, c->cl->npos, (int32_t)step, (uint16_t)cycles,
                                             &points, &suggest);
    if (st != SWG_OK) {
        return swg_err(c, st, suggest);
    }
    put_swp(c->out);
    return E_OK;
}

static err_t h_list_clr(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    engine_list_clear();
    return E_OK;
}

static err_t h_list_add(ctx_t *c)
{
    if (c->cl->nkv != 0 || c->cl->npos == 0 || c->cl->npos > LIST_ADD_MAX_POINTS) {
        return fail(c, E_ARG, "usage: SWP.LIST.ADD <v>[:<settle_us>] ... (1-8 points)");
    }
    sweep_point_t pts[LIST_ADD_MAX_POINTS];
    for (unsigned i = 0; i < c->cl->npos; i++) {
        tok_t t = c->cl->pos[i];
        tok_t tv = t, tsu = { NULL, 0 };
        for (uint16_t k = 0; k < t.n; k++) {
            if (t.s[k] == ':') {
                tv.n = k;
                tsu.s = t.s + k + 1;
                tsu.n = (uint16_t)(t.n - k - 1u);
                break;
            }
        }
        int64_t v;
        err_t e = parse_scaled(c, &tv, 6, -V_LIMIT_MAX_UV, V_LIMIT_MAX_UV, &v, "point");
        if (e != E_OK) {
            return e;
        }
        uint32_t st = 0;
        if (tsu.s != NULL) {
            e = parse_u32(c, &tsu, SETTLE_MIN_US, SETTLE_MAX_US, &st, "settle_us");
            if (e != E_OK) {
                return e;
            }
        }
        pts[i].v_uv = (int32_t)v;
        pts[i].settle_us = st;
    }
    uint32_t total = 0;
    if (!engine_list_add(pts, c->cl->npos, &total)) {
        return fail(c, E_RANGE, "list full (2048 points)");
    }
    lb_puts(c->out, "n=");
    lb_u64(c->out, total);
    return E_OK;
}

static err_t h_list_use(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    uint32_t points = 0;
    swg_status_t st = engine_list_use(&points);
    if (st != SWG_OK) {
        return swg_err(c, st, 0);
    }
    put_swp(c->out);
    return E_OK;
}

static err_t h_swp_get(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    put_swp(c->out);
    return E_OK;
}

static void emit_sweep_begin(uint16_t id, uint32_t points)
{
    char buf[PROTO_RESP_MAX];
    lb_t b;
    lb_init(&b, buf, sizeof buf);
    uint64_t now = port_now_us();
    sweep_kind_t k = engine_sweep_kind();
    lb_puts(&b, "EVT SWEEP_BEGIN");
    lb_kv_u(&b, "id", id);
    lb_kv_s(&b, "kind", k == SWEEP_LIST ? "list" : "wp");
    lb_kv_u(&b, "points", points);
    lb_kv_u(&b, "t0_us", engine_sweep_t0_us());
    put_cfg_fields(&b, cfg_get());
    lb_kv_s(&b, "cal_source", engine_cal_source_name());
    lb_kv_u(&b, "cal_seq", engine_cal_seq());
    put_cal_values(&b);
    put_zero_fields(&b, now);
    proto_event_ordered(&b);   /* never ahead of the previous sweep's records */
}

static err_t h_swp_run(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    uint16_t id = 0;
    uint32_t points = 0;
    uint64_t est = 0;
    const char *why;
    e = engine_sweep_run(&id, &points, &est, &why);
    if (e != E_OK) {
        return fail(c, e, why);
    }
    lb_puts(c->out, "id=");
    lb_u64(c->out, id);
    lb_kv_u(c->out, "points", points);
    put_est_s(c->out, est);
    emit_sweep_begin(id, points);
    inst_sweep_started();
    return E_OK;
}

static err_t h_swp_abort(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    engine_sweep_abort(SWS_ABORTED);
    return E_OK;
}

static err_t h_fetch(ctx_t *c)
{
    static const char *const keys[] = { "FROM" };
    err_t e = check_keys(c, keys, 1);
    if (e != E_OK) {
        return e;
    }
    const tok_t *t = kv_get(c, "FROM");
    if (t == NULL || c->cl->npos != 0) {
        return fail(c, E_ARG, "usage: DATA.FETCH FROM=<seq>");
    }
    uint32_t from;
    if ((e = parse_u32(c, t, 0, UINT32_MAX, &from, "FROM")) != E_OK) {
        return e;
    }
    uint32_t first = results_rewind(from);
    lb_puts(c->out, "first=");
    lb_u64(c->out, first);
    lb_kv_u(c->out, "last", (uint64_t)results_next_seq() - 1u);
    return E_OK;
}

/* ---- calibration ----------------------------------------------------------------- */

static err_t h_cal_get(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    lb_puts(c->out, "CAL");
    lb_kv_s(c->out, "source", engine_cal_source_name());
    lb_kv_u(c->out, "seq", engine_cal_seq());
    lb_kv_hex(c->out, "measured", engine_cal_measured(), 2u);
    put_cal_values(c->out);
    return E_OK;
}

static err_t h_cal_set(ctx_t *c)
{
    static const char *const keys[] = {
        "g_dac_uv", "c_dac", "g_v_uv", "g_i_na", "r_s", "r_top", "i_plateau",
    };
    err_t e = check_keys(c, keys, 7);
    if (e != E_OK) {
        return e;
    }
    if (c->cl->npos != 0 || c->cl->nkv == 0) {
        return fail(c, E_ARG, "usage: CAL.SET key=value ...");
    }
    cal_coeffs_t n = *engine_cal();
    uint32_t measured = 0u;
    for (unsigned i = 0; i < 7u; i++) {
        const tok_t *t = kv_get(c, k_cal_keys[i].key);
        if (t == NULL) {
            continue;
        }
        measured |= 1u << i;   /* CAL_M_* follow the key order */
        int64_t v;
        if ((e = parse_scaled(c, t, k_cal_keys[i].scale, INT64_MIN / 2, INT64_MAX / 2, &v,
                              k_cal_keys[i].key)) != E_OK) {
            return e;
        }
        *cal_field(&n, i) = (double)v / k_cal_keys[i].to_si;
    }
    if (!calib_plausible(&n)) {
        return fail(c, E_RANGE, "implausible coefficients");
    }
    engine_cal_set(&n, measured);
    return E_OK;
}

static err_t h_cal_save(ctx_t *c)
{
    static const char *const keys[] = { "TS", "NOTE" };
    err_t e = check_keys(c, keys, 2);
    if (e != E_OK) {
        return e;
    }
    const tok_t *ts = kv_get(c, "TS");
    if (ts == NULL || c->cl->npos != 0) {
        return fail(c, E_ARG, "usage: CAL.SAVE TS=<unix seconds> [NOTE=<word>]");
    }
    uint32_t t;
    if ((e = parse_u32(c, ts, 0, UINT32_MAX, &t, "TS")) != E_OK) {
        return e;
    }
    char note[CAL_NOTE_LEN];
    memset(note, 0, sizeof note);
    const tok_t *tn = kv_get(c, "NOTE");
    if (tn != NULL) {
        if (tn->n >= CAL_NOTE_LEN) {
            return fail(c, E_RANGE, "NOTE longer than 31 characters");
        }
        memcpy(note, tn->s, tn->n);
    }
    /* SI-8: DISARMED (command gate) and T_DISARM after the last edge */
    hb_stats_t hs;
    hb_get_stats(&hs);
    if (hb_enabled() || (hs.edges != 0u && port_now_us() - hs.last_edge_us < T_DISARM_US)) {
        return fail(c, E_STATE, "heartbeat recently active");
    }
    if (!calib_plausible(engine_cal())) {
        return fail(c, E_RANGE, "implausible coefficients");
    }
    if (engine_cal_save_start(t, tn ? note : NULL) != E_OK) {
        return fail(c, E_BUSY, "measurement in progress");
    }
    c->deferred = true;   /* answered once the sector is written and verified */
    return E_OK;
}

static err_t h_cal_default(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    engine_cal_default();
    return E_OK;
}

static err_t h_cal_reload(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    engine_cal_reload();
    lb_puts(c->out, "source=");
    lb_puts(c->out, engine_cal_source_name());
    lb_kv_u(c->out, "seq", engine_cal_seq());
    return E_OK;
}

/* ---- bring-up only --------------------------------------------------------------------- */

#if FW_BRINGUP
static err_t h_hb_freq(ctx_t *c)
{
    if (c->cl->npos != 1 || c->cl->nkv != 0) {
        return fail(c, E_ARG, "usage: HB.FREQ <hz>");
    }
    uint32_t hz;
    err_t e = parse_u32(c, &c->cl->pos[0], 1, 100000, &hz, "hz");
    if (e != E_OK) {
        return e;
    }
    if (!hb_set_frequency_hz(hz)) {
        return fail(c, E_RANGE, "500..5000 Hz");
    }
    return E_OK;
}

/* A static PC5 level cannot pump the reference gate (one edge peaks at
 * 0.54 V < V_IL 0.87 V, spec §4.4), but repeated level changes from the host
 * would: a rising edge is refused until the gate has decayed from the
 * previous one (T_DISARM after the last edge). */
static bool hb_edge_too_soon(void)
{
    hb_stats_t hs;
    hb_get_stats(&hs);
    return hs.edges != 0u && port_now_us() - hs.last_edge_us < T_DISARM_US;
}

static err_t h_hb_edge(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    if (hb_bringup_holding()) {
        return fail(c, E_STATE, "HB.HOLD active (HB.HOLD OFF first)");
    }
    if (hb_edge_too_soon()) {
        return fail(c, E_STATE, "edge within T_DISARM of the last one");
    }
    if (!hb_bringup_hold(true)) {
        return fail(c, E_STATE, "heartbeat enabled");
    }
    inst_note_hb_hold(port_now_us(), true);
    return E_OK;
}

static err_t h_hb_hold(ctx_t *c)
{
    if (c->cl->npos != 1 || c->cl->nkv != 0) {
        return fail(c, E_ARG, "usage: HB.HOLD HIGH|LOW|OFF");
    }
    tok_t t = c->cl->pos[0];
    bool high;
    if (tok_eq(t, "HIGH")) {
        high = true;
    } else if (tok_eq(t, "LOW") || tok_eq(t, "OFF")) {
        high = false;
    } else {
        return fail(c, E_ARG, "HIGH, LOW or OFF");
    }
    if (high && !hb_bringup_holding() && hb_edge_too_soon()) {
        return fail(c, E_STATE, "edge within T_DISARM of the last one");
    }
    if (!hb_bringup_hold(high)) {
        return fail(c, E_STATE, "heartbeat enabled");
    }
    inst_note_hb_hold(port_now_us(), high);
    return E_OK;
}

static err_t h_dac_raw(ctx_t *c)
{
    if (c->cl->npos == 1 && c->cl->nkv == 0 && tok_eq(c->cl->pos[0], "OFF")) {
        engine_zero_output_now("aborted");   /* dac_update(): B back to 0xFFFF, scrub resumes */
        return E_OK;
    }
    static const char *const keys[] = { "CONFIRM" };
    err_t e = check_keys(c, keys, 1);
    if (e != E_OK) {
        return e;
    }
    const tok_t *cf = kv_get(c, "CONFIRM");
    if (c->cl->npos != 2 || cf == NULL) {
        return fail(c, E_ARG, "usage: DAC.RAW <a> <b> CONFIRM=UNSAFE | DAC.RAW OFF");
    }
    if (cf->n != 6 || strncmp(cf->s, "UNSAFE", 6) != 0) {
        return fail(c, E_ARG, "CONFIRM=UNSAFE required (exact case)");
    }
    uint32_t a, b;
    if ((e = parse_u32(c, &c->cl->pos[0], 0, 65535, &a, "a")) != E_OK
        || (e = parse_u32(c, &c->cl->pos[1], 0, 65535, &b, "b")) != E_OK) {
        return e;
    }
    if (engine_ramping()) {
        return fail(c, E_BUSY, "ramp in progress");
    }
    if (!dac_raw_write((uint16_t)a, (uint16_t)b)) {
        inst_fault(F_DAC_SPI, "dac_raw");
        return fail(c, E_FAULT, fault_name(F_DAC_SPI));
    }
    return E_OK;
}

static err_t h_adc_pga(ctx_t *c)
{
    if (c->cl->npos != 1 || c->cl->nkv != 0) {
        return fail(c, E_ARG, "usage: ADC.PGA <1|2|4|8|16|32|64>");
    }
    uint32_t g;
    err_t e = parse_u32(c, &c->cl->pos[0], 1, 64, &g, "gain");
    if (e != E_OK) {
        return e;
    }
    uint8_t code = 0;
    while ((1u << code) < g) {
        code++;
    }
    if ((1u << code) != g) {
        return fail(c, E_RANGE, "gain must be a power of two");
    }
    if (engine_job_active() || !engine_adc_ready()) {
        return fail(c, E_BUSY, "adc busy");
    }
    if (engine_adc_reconfigure(cfg_get()->adc_rate_x10, code) != E_OK) {
        return fail(c, E_BUSY, "adc busy");
    }
    return E_OK;
}

static err_t h_adc_resetpulse(ctx_t *c)
{
    err_t e = no_args(c);
    if (e != E_OK) {
        return e;
    }
    ads_pulse_reset_unannounced();
    return E_OK;
}

static err_t h_crash(ctx_t *c)
{
    if (c->cl->npos != 1 || c->cl->nkv != 0) {
        return fail(c, E_ARG, "usage: CRASH HARDFAULT|HANG|HANG_IRQOFF|STACK");
    }
    tok_t t = c->cl->pos[0];
    int kind;
    if (tok_eq(t, "HARDFAULT")) {
        kind = 0;
    } else if (tok_eq(t, "HANG")) {
        kind = 1;
    } else if (tok_eq(t, "HANG_IRQOFF")) {
        kind = 2;
    } else if (tok_eq(t, "STACK")) {
        kind = 3;
    } else {
        return fail(c, E_ARG, "HARDFAULT, HANG, HANG_IRQOFF or STACK");
    }
    s_c.action = ACT_CRASH;
    s_c.crash_kind = kind;
    s_c.action_t0 = port_now_us();
    return E_OK;
}
#endif /* FW_BRINGUP */

/* ---- command table ------------------------------------------------------------------- */

static const cmd_t k_cmds[] = {
    { "*IDN?",        S_ANY,            h_idn },
    { "PING",         S_ANY,            h_ping },
    { "STAT?",        S_ANY,            h_stat },
    { "DIAG?",        S_ANY,            h_diag },
    { "DIAG.CLR",     S_ANY,            h_diag_clr },
    { "ARM",          S_ANY,            h_arm },
    { "DISARM",       S_(ST_DISARMED) | S_(ST_ARMING) | S_(ST_ARMED) | S_(ST_SWEEPING)
                      | S_(ST_DISARMING), h_disarm },
    { "SRC",          S_(ST_ARMED),     h_src },
    { "FAULT.CLR",    S_ANY,            h_fault_clr },
    { "RESET",        S_(ST_DISARMED) | S_(ST_FAULT), h_reset },
    { "MEAS?",        S_DISARMED_ARMED, h_meas },
    { "ZERO",         S_DISARMED_ARMED, h_zero },
    { "CFG",          S_DISARMED_ARMED, h_cfg },
    { "CFG?",         S_ANY,            h_cfg_get },
    { "SWP.WP",       S_DISARMED_ARMED, h_swp_wp },
    { "SWP.LIST.CLR", S_DISARMED_ARMED, h_list_clr },
    { "SWP.LIST.ADD", S_DISARMED_ARMED, h_list_add },
    { "SWP.LIST.USE", S_DISARMED_ARMED, h_list_use },
    { "SWP?",         S_ANY,            h_swp_get },
    { "SWP.RUN",      S_(ST_ARMED),     h_swp_run },
    { "SWP.ABORT",    S_(ST_SWEEPING),  h_swp_abort },
    { "DATA.FETCH",   S_ANY,            h_fetch },
    { "CAL?",         S_ANY,            h_cal_get },
    { "CAL.SET",      S_(ST_DISARMED),  h_cal_set },
    { "CAL.SAVE",     S_(ST_DISARMED),  h_cal_save },
    { "CAL.DEFAULT",  S_(ST_DISARMED),  h_cal_default },
    { "CAL.RELOAD",   S_(ST_DISARMED),  h_cal_reload },
    { "ADC.SELFCAL",  S_(ST_DISARMED),  h_selfcal },
    { "SELFTEST",     S_(ST_DISARMED),  h_selftest },
#if FW_BRINGUP
    { "HB.FREQ",      S_(ST_ARMED),     h_hb_freq },
    { "HB.EDGE",      S_(ST_DISARMED),  h_hb_edge },
    { "HB.HOLD",      S_(ST_DISARMED),  h_hb_hold },
    { "DAC.RAW",      S_(ST_ARMED),     h_dac_raw },
    { "ADC.PGA",      S_(ST_DISARMED),  h_adc_pga },
    { "ADC.RESETPULSE", S_(ST_DISARMED), h_adc_resetpulse },
    { "CRASH",        S_ANY,            h_crash },
#endif
};

void commands_format_error(lb_t *out, err_t e, const char *msg)
{
    lb_reset(out);
    lb_puts(out, "ERR ");
    lb_puts(out, err_name(e));
    lb_putc(out, ' ');
    lb_puts(out, (msg && msg[0]) ? msg : "failed");
}

urgent_t commands_urgent_kind(const char *line, size_t len)
{
    size_t i = 0;
    while (i < len && (line[i] == ' ' || line[i] == '\t')) {
        i++;
    }
    size_t j = i;
    while (j < len && line[j] != ' ' && line[j] != '\t') {
        j++;
    }
    for (size_t k = j; k < len; k++) {
        if (line[k] != ' ' && line[k] != '\t') {
            return URGENT_NONE;   /* arguments: executed (and refused) in order */
        }
    }
    tok_t t = { line + i, (uint16_t)(j - i) };
    if (tok_eq(t, "DISARM")) {
        return URGENT_DISARM;
    }
    if (tok_eq(t, "SWP.ABORT")) {
        return URGENT_ABORT;
    }
    return URGENT_NONE;
}

bool commands_urgent_act(urgent_t k)
{
    inst_state_t st = inst_state();
    if (k == URGENT_DISARM && (st == ST_ARMING || st == ST_ARMED || st == ST_SWEEPING)) {
        inst_disarm(DR_COMMAND);   /* the in-order DISARM is then a no-op OK */
        return true;
    }
    if (k == URGENT_ABORT && st == ST_SWEEPING) {
        engine_sweep_abort(SWS_ABORTED);
        return true;
    }
    return false;
}

bool commands_urgent_settled(urgent_t k)
{
    return k == URGENT_ABORT && inst_state() != ST_SWEEPING;
}

bool commands_execute(const cmdline_t *cl, lb_t *out)
{
    const cmd_t *cmd = NULL;
    for (size_t i = 0; i < sizeof k_cmds / sizeof k_cmds[0]; i++) {
        if (tok_eq(cl->verb, k_cmds[i].verb)) {
            cmd = &k_cmds[i];
            break;
        }
    }
    if (cmd == NULL) {
        commands_format_error(out, E_UNKNOWN, "unknown verb");
        return true;
    }
    inst_state_t st = inst_state();
    if ((cmd->states & S_(st)) == 0u) {
        char m[64];
        lb_t mb;
        lb_init(&mb, m, sizeof m);
        if (st == ST_FAULT) {
            lb_puts(&mb, fault_name(inst_fault_code()));
            commands_format_error(out, E_FAULT, m);
        } else {
            lb_puts(&mb, "not allowed in ");
            lb_puts(&mb, state_name(st));
            commands_format_error(out, E_STATE, m);
        }
        return true;
    }

    char pbuf[PROTO_RESP_MAX - 8];
    lb_t payload;
    lb_init(&payload, pbuf, sizeof pbuf);
    ctx_t c;
    memset(&c, 0, sizeof c);
    c.cl = cl;
    c.out = &payload;
    lb_init(&c.msg, c.msg_buf, sizeof c.msg_buf);
    err_t e = cmd->fn(&c);
    if (e == E_OK && c.deferred) {
        s_c.deferred = true;
        s_c.deferred_kind = JOB_NONE;
        return false;
    }
    if (e != E_OK) {
        commands_format_error(out, e, c.msg.buf);
        return true;
    }
    lb_reset(out);
    lb_puts(out, "OK");
    if (payload.len > 0) {
        lb_putc(out, ' ');
        lb_putn(out, payload.buf, payload.len);
    }
    return true;
}

bool commands_deferred_pending(void)
{
    return s_c.deferred;
}

static void format_job(lb_t *out, const job_result_t *r)
{
    if (r->err != E_OK) {
        if (r->kind == JOB_ZERO && r->err == E_RANGE && r->zero.valid) {
            char m[96];
            lb_t mb;
            lb_init(&mb, m, sizeof m);
            lb_puts(&mb, "implausible");
            lb_kv_fix(&mb, "o_v", rnd(r->zero.o_v * 100.0), 2);
            lb_kv_fix(&mb, "o_i", rnd(r->zero.o_i * 100.0), 2);
            commands_format_error(out, E_RANGE, m);
        } else {
            err_t e = (r->kind == JOB_CALSAVE && r->err != E_RANGE) ? E_FLASH : r->err;
            commands_format_error(out, e, r->msg);
        }
        return;
    }
    lb_reset(out);
    lb_puts(out, "OK");
    switch (r->kind) {
    case JOB_MEAS:
        lb_puts(out, " MEAS");
        lb_kv_u(out, "n", r->n);
        lb_kv_i(out, "i_raw", r->i_raw);
        lb_kv_i(out, "v_raw", r->v_raw);
        lb_key(out, "i_sd");
        if (r->i_sd == 0xFFFFu) {
            lb_putc(out, '-');
        } else {
            lb_fix(out, r->i_sd, 1);
        }
        lb_key(out, "v_sd");
        if (r->v_sd == 0xFFFFu) {
            lb_putc(out, '-');
        } else {
            lb_fix(out, r->v_sd, 1);
        }
        lb_kv_fix(out, "v_v", div_round(r->v_nv, 1000), 6);
        lb_kv_fix(out, "i_a", div_round(r->i_pa, 100), 10);
        lb_kv_hex(out, "flags", r->flags, 4);
        break;
    case JOB_ZERO:
        lb_puts(out, " ZERO");
        lb_kv_fix(out, "o_v", rnd(r->zero.o_v * 100.0), 2);
        lb_kv_fix(out, "o_i", rnd(r->zero.o_i * 100.0), 2);
        lb_kv_fix(out, "sd_v", rnd(r->zero.sd_v * 100.0), 2);
        lb_kv_fix(out, "sd_i", rnd(r->zero.sd_i * 100.0), 2);
        lb_kv_u(out, "pairs", r->zero.pairs);
        lb_kv_fix(out, "sps", r->zero.sps_x10, 1);
        break;
    case JOB_CALSAVE:
        lb_puts(out, " seq=");
        lb_u64(out, r->seq);
        break;
    case JOB_SELFTEST:
        lb_puts(out, " SELFTEST");
        lb_kv_i(out, "negref", r->negref);
        lb_kv_i(out, "zero", r->zero_code);
        lb_kv_s(out, "afe", r->afe_ok ? "ok" : "fail");
        break;
    case JOB_SELFCAL:
    case JOB_POST:
    case JOB_RECONF:
    case JOB_NONE:
    default:
        break;
    }
}

bool commands_poll_deferred(lb_t *out)
{
    if (!s_c.deferred) {
        return false;
    }
    job_result_t r;
    if (!engine_job_take(&r)) {
        return false;
    }
    s_c.deferred = false;
    format_job(out, &r);
    return true;
}

void commands_cancel_deferred(void)
{
    if (s_c.deferred) {
        engine_job_cancel();
        s_c.deferred = false;
    }
}

void commands_emit_hello(void)
{
    char buf[320];
    lb_t b;
    lb_init(&b, buf, sizeof buf);
    lb_puts(&b, "EVT HELLO");
    put_identity(&b);
    lb_kv_s(&b, "state", state_name(inst_state()));
    lb_kv_s(&b, "fault", fault_name(inst_fault_code()));
    lb_kv_s(&b, "disarm_reason", disarm_reason_name(inst_disarm_reason()));
    lb_key(&b, "reset");
    put_reset_causes(&b, port_reset_flags());
    crash_record_t cr;
    lb_kv_u(&b, "crash", port_crash_get(&cr) ? 1u : 0u);
    proto_event_lb(&b);
}

void commands_service(uint64_t now_us)
{
    if (s_c.action == ACT_NONE) {
        return;
    }
    /* let the OK leave first (bounded wait) */
    uint64_t dt = now_us - s_c.action_t0;
    if (dt < PENDING_ACTION_MIN_US
        || (!proto_tx_empty() && dt < PENDING_ACTION_TIMEOUT_US)) {
        return;
    }
    action_t a = s_c.action;
    s_c.action = ACT_NONE;
    if (a == ACT_RESET) {
        port_system_reset();
    }
#if FW_BRINGUP
    else if (a == ACT_CRASH) {
        port_crash_test(s_c.crash_kind);
    }
#endif
}
