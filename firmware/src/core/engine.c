/*
 * engine.c — output, ADC schedule, measurement, sweeps and deferred jobs
 * (contract: engine.h; firmware_spec.md §7.4-§7.8, §7.10).
 *
 * Facts relied on:
 *  - DAC frames and the zero pair: drivers/dac8552.h (SLAS430A). Ramp steps
 *    <= RAMP_STEP_MAX_UV (10 mV, about 33 codes of 306.4 uV) on a 1 ms grid
 *    (spec §7.6), so 10 V/s is the fastest ramp and the disarm floor.
 *  - ADS1256 conversions: drivers/ads1256.h. Timing estimates use t18(DR)
 *    (SBAS288K Table 13) + 70 us SPI work + one 150 us pass per conversion.
 *  - POST self-checks (spec §7.2) at PGA 1: AIN1 - AINCOM = -VREF reads
 *    -2^22 codes (+-0.5 %), AIN1 - AIN3 reads 0 (+-200 codes); AIN1/AIN3 are
 *    100 ohm to AGND, AINCOM = VREF (blocks/adc.py).
 *  - Front-end check, Kelvin/plateau/no-output classification: safety.h.
 *  - R_sd = R12 + R32 (nominal.h, blocks/sense_amp.py).
 *  - ZERO plausibility: |o_i| <= 30 uA, |o_v| <= 3 mV (INA128 SBOS051G
 *    worst-case offsets referred to the DUT are 26 uA / 2.76 mV, spec §4.6).
 *
 * Re-entrancy: any call that may latch a fault (dac_update failure,
 * compliance with on_compliance=fault, clip while armed) re-enters the engine
 * through engine_fault_entry(); engine_service() stops its pass as soon as
 * the fault epoch changes.
 */
#include "engine.h"

#include "app.h"
#include "config.h"
#include "dac8552.h"
#include "heartbeat.h"
#include "instrument.h"
#include "nominal.h"
#include "port.h"
#include "proto.h"
#include "safety.h"
#include "scaling.h"

#include <math.h>
#include <string.h>

#if defined(__arm__)
#define ENGINE_NOINIT __attribute__((section(".noinit.sweep_list")))
#else
#define ENGINE_NOINIT
#endif

#define CONV_OVERHEAD_US     220u      /* 70 us SPI + one 150 us pass        */
#define LATE_SETTLE_US       200u
#define NEGREF_EXPECT        (-4194304)
#define NEGREF_TOL           20972     /* 0.5 % of 2^22                       */
#define ZERO_CODE_TOL        200
#define ZERO_MAX_I_A         30e-6
#define ZERO_MAX_V_V         3e-3
#define KELVIN_RUN_WARN      3u
#define JOB_TIMEOUT_US       15000000u /* guard above every documented bound   */
#define R_SD_OHM             (NOM_R12_OHM + NOM_R32_OHM)

/* ---- calibration and zero -------------------------------------------------------- */

static cal_coeffs_t s_cal;
static cal_source_t s_cal_src;
static uint32_t     s_cal_seq;
static calib_scan_t s_scan;
static zero_t       s_zero;
static bool         s_zero_stale;

/* ---- output ------------------------------------------------------------------------ */

typedef enum { RAMP_NONE = 0, RAMP_SRC, RAMP_PRE, RAMP_END, RAMP_DISARM } ramp_kind_t;

static struct {
    int32_t     uv;           /* setpoint of the code in force              */
    uint16_t    code;
    bool        known;
    ramp_kind_t ramp;
    int32_t     from, to;
    int32_t     limit;
    uint32_t    rate;         /* uV/s                                        */
    uint32_t    k;            /* steps done                                  */
    uint64_t    next;         /* time of the next step                       */
    uint64_t    t_last_write; /* last frame pair sent (update or scrub)   */
    uint64_t    t_load;       /* last code change through out_load()      */
    bool        disarm_done;
} s_out;

/* ---- ADC ownership, monitor -------------------------------------------------------- */

typedef enum { OWN_NONE = 0, OWN_MON, OWN_ACQ, OWN_JOB } own_t;

static own_t     s_owner;
static monitor_t s_mon;
static uint64_t  s_mon_due;
static bool      s_mon_v_next;
static bool      s_mon_pair_open;   /* the last conversion was a monitor I taken
                                       with the gate open; the next V pairs with it */
static uint64_t  s_last_conv;
static afe_t     s_afe;
static uint64_t  s_afe_t;
static bool      s_post_passed;
static uint8_t   s_pga;
static uint32_t  s_epoch;

/* ---- acquisition of one point (I,V,V,I,...) ------------------------------------------ */

typedef struct {
    bool     active;
    bool     settling;
    uint64_t t_set;
    uint64_t t_settle_end;
    uint32_t settle_req;
    uint16_t pairs;
    uint16_t j;
    bool     in_flight;
    int64_t  sum[2];
    int64_t  sq[2];
    int32_t  first[2];
    uint16_t cnt[2];
    bool     clip[2];
    bool     late;
    uint64_t tmid[2];
    uint64_t t_first_start;
    bool     tripped;
    int64_t  trip_pa;
} acq_t;

static acq_t s_acq;

/* ---- sweep --------------------------------------------------------------------------- */

typedef enum { SW_PRE = 0, SW_ACQ } sw_phase_t;

static sweep_def_t   s_def;
static sweep_point_t s_list[SWEEP_LIST_MAX] ENGINE_NOINIT;
static uint32_t      s_list_n;
static uint16_t      s_sweep_counter;

static struct {
    bool           active;
    sweep_def_t    def;
    sweep_iter_t   it;
    uint16_t       id;
    uint32_t       total;
    uint32_t       done;
    sweep_status_t status;
    uint64_t       t0;
    sw_phase_t     phase;
    int32_t        v;
    uint32_t       settle;
    uint16_t       seg;
    uint32_t       idx;
    bool           first;
    uint8_t        kelvin_run;
    bool           kelvin_warned, noout_warned, late_warned;
    uint32_t       warn_bits;
} s_sw;

/* ---- jobs ------------------------------------------------------------------------------ */

static struct {
    job_kind_t   kind;
    int          phase;
    uint64_t     t0;
    bool         done;
    bool         autoclear;   /* no command collects the result (POST, RECONF) */
    uint32_t     arg_rate_x10;
    uint8_t      arg_pga;
    uint32_t     arg_ts;
    bool         arg_has_note;
    char         arg_note[CAL_NOTE_LEN];
    job_result_t res;
    bool         have;        /* single job conversion arrived */
    ads_sample_t smp;
} s_job;

static engine_stats_t s_stats;

/* ================================================================================== */
/* helpers                                                                              */
/* ================================================================================== */

static const ads_rate_t *rate(void)
{
    const ads_rate_t *r = ads_rate_by_sps_x10(cfg_get()->adc_rate_x10);
    return r ? r : ads_rate_by_sps_x10(10000u);
}

static ads_cfg_t adc_cfg(uint8_t pga)
{
    ads_cfg_t c = { rate()->drate, pga };
    return c;
}

static int64_t div_round_i64(int64_t num, int64_t den)
{
    return (num >= 0) ? (num + den / 2) / den : -((-num + den / 2) / den);
}

static int32_t iabs32(int32_t v)
{
    return v < 0 ? -v : v;
}

static const zero_t *zero_for_scaling(void)
{
    return &s_zero;   /* scaling ignores it unless valid (a stale zero is applied) */
}

static void emit_simple(const char *s)
{
    proto_event(s, strlen(s));
}

static bool armed_states(void)
{
    return hb_enabled();
}

uint16_t engine_zero_code(void)
{
    uint16_t c;
    if (scaling_v_to_code(&s_cal, 0, V_LIMIT_MAX_UV, &c) == E_OK) {
        return c;
    }
    return DAC_CODE_ZERO_NOMINAL;
}

/* ================================================================================== */
/* calibration                                                                          */
/* ================================================================================== */

const cal_coeffs_t *engine_cal(void)          { return &s_cal; }
cal_source_t        engine_cal_source(void)   { return s_cal_src; }
uint32_t            engine_cal_seq(void)      { return s_cal_seq; }
const calib_scan_t *engine_cal_scan(void)     { return &s_scan; }

const char *engine_cal_source_name(void)
{
    switch (s_cal_src) {
    case CALSRC_STORED: return "stored";
    case CALSRC_RAM:    return "ram";
    case CALSRC_NOMINAL:
    default:            return "nominal";
    }
}

void engine_cal_set(const cal_coeffs_t *c)
{
    s_cal = *c;
    s_cal_src = CALSRC_RAM;
}

void engine_cal_default(void)
{
    calib_nominal(&s_cal);
    s_cal_src = CALSRC_NOMINAL;
}

void engine_cal_reload(void)
{
    s_cal_src = calib_load(&s_cal, &s_scan, &s_cal_seq) ? CALSRC_STORED : CALSRC_NOMINAL;
}

err_t engine_cal_save(uint32_t timestamp, const char *note, uint32_t *seq)
{
    app_note_long_op();
    err_t e = calib_save(&s_cal, timestamp, note, seq);
    cal_coeffs_t check;
    uint32_t seq_now = 0;
    bool stored = calib_load(&check, &s_scan, &seq_now);
    if (e == E_OK && stored && seq_now == *seq) {
        s_cal_src = CALSRC_STORED;
        s_cal_seq = seq_now;
    }
    return e;
}

/* ================================================================================== */
/* session zero                                                                         */
/* ================================================================================== */

const zero_t *engine_zero(void)
{
    return &s_zero;
}

zero_state_t engine_zero_state(uint64_t now_us)
{
    if (!s_zero.valid) {
        return ZERO_NONE;
    }
    if (s_zero_stale || now_us - s_zero.t_us > ZERO_STALE_AGE_US) {
        return ZERO_STALE;
    }
    return ZERO_VALID;
}

const char *engine_zero_state_name(zero_state_t z)
{
    switch (z) {
    case ZERO_VALID: return "valid";
    case ZERO_STALE: return "stale";
    case ZERO_NONE:
    default:         return "none";
    }
}

void engine_zero_mark_stale(void)
{
    s_zero_stale = true;
}

/* ================================================================================== */
/* output                                                                                */
/* ================================================================================== */

int32_t  engine_out_uv(void)          { return s_out.uv; }
uint16_t engine_out_code(void)        { return s_out.code; }
bool     engine_ramping(void)         { return s_out.ramp != RAMP_NONE; }
int32_t  engine_ramp_target_uv(void)  { return s_out.ramp != RAMP_NONE ? s_out.to : s_out.uv; }

int32_t engine_held_abs_uv(void)
{
    int32_t a = iabs32(s_out.uv);
    if (s_out.ramp != RAMP_NONE && iabs32(s_out.to) > a) {
        a = iabs32(s_out.to);
    }
    return a;
}

/* Load the code for v (SI-5: only scaling_v_to_code produces codes). */
static bool out_load(int32_t v_uv, int32_t limit_uv, bool fault_on_fail)
{
    uint16_t code;
    if (scaling_v_to_code(&s_cal, v_uv, limit_uv, &code) != E_OK) {
        if (fault_on_fail) {
            inst_fault(F_INVARIANT, "setpoint");
        }
        return false;
    }
    if (!dac_update(code)) {
        if (fault_on_fail) {
            inst_fault(F_DAC_SPI, "dac_spi");
        }
        return false;
    }
    s_out.uv = v_uv;
    s_out.code = code;
    s_out.known = true;
    s_out.t_last_write = port_now_us();
    s_out.t_load = s_out.t_last_write;
    return true;
}

static void ramp_start(ramp_kind_t kind, int32_t to_uv, uint32_t rate_uv_s, int32_t limit_uv)
{
    s_out.ramp = kind;
    s_out.from = s_out.uv;
    s_out.to = to_uv;
    s_out.rate = rate_uv_s;
    s_out.limit = limit_uv;
    s_out.k = 0;
    s_out.next = port_now_us() + RAMP_STEP_PERIOD_US;
    if (s_out.from == s_out.to && s_out.known) {
        s_out.next = port_now_us();   /* finish on the next service, no step */
    }
}

static void emit_src_done(void)
{
    char buf[48];
    lb_t b;
    lb_init(&b, buf, sizeof buf);
    lb_puts(&b, "EVT SRC_DONE");
    lb_kv_fix(&b, "v_v", s_out.uv, 6);
    proto_event_lb(&b);
}

static void ramp_finished(ramp_kind_t kind)
{
    s_out.ramp = RAMP_NONE;
    if (kind == RAMP_SRC) {
        emit_src_done();
    } else if (kind == RAMP_DISARM) {
        s_out.disarm_done = true;
    }
}

static void ramp_service(uint64_t now)
{
    if (s_out.ramp == RAMP_NONE || now < s_out.next) {
        return;
    }
    ramp_kind_t kind = s_out.ramp;
    if (s_out.from == s_out.to && s_out.known) {
        /* DISARM: SI-4, the zero code is (re)loaded before the heartbeat
         * stops. PRE: reload so the first settle starts now (t_load). */
        if (kind == RAMP_DISARM || kind == RAMP_PRE) {
            uint32_t epoch = s_epoch;
            if (!out_load(s_out.to, s_out.limit, true) || epoch != s_epoch) {
                return;
            }
        }
        ramp_finished(kind);
        return;
    }
    uint32_t k = s_out.k + 1u;
    int64_t dist = (int64_t)s_out.to - (int64_t)s_out.from;
    int64_t adist = dist < 0 ? -dist : dist;
    int64_t off = (int64_t)s_out.rate * (int64_t)k / 1000;   /* uV after k ms */
    if (off < 1) {
        off = 1;
    }
    if (off > (int64_t)k * RAMP_STEP_MAX_UV) {
        off = (int64_t)k * RAMP_STEP_MAX_UV;
    }
    int32_t v = (off >= adist) ? s_out.to
              : (int32_t)(s_out.from + (dist < 0 ? -off : off));
    uint32_t epoch = s_epoch;
    if (!out_load(v, s_out.limit, true) || epoch != s_epoch) {
        return;
    }
    s_out.k = k;
    s_out.next += RAMP_STEP_PERIOD_US;
    if (now >= s_out.next) {
        s_out.next = now + RAMP_STEP_PERIOD_US;   /* behind: resynchronise */
        s_stats.ramp_resyncs++;
    }
    if (v == s_out.to) {
        ramp_finished(kind);
    }
}

err_t engine_src(int32_t v_uv)
{
    uint16_t code;
    err_t e = scaling_v_to_code(&s_cal, v_uv, cfg_get()->v_limit_uv, &code);
    if (e != E_OK) {
        return e;
    }
    ramp_start(RAMP_SRC, v_uv, cfg_get()->ramp_rate_uv_s, cfg_get()->v_limit_uv);
    return E_OK;
}

bool engine_arm_prepare(void)
{
    s_out.ramp = RAMP_NONE;
    s_out.disarm_done = false;
    return out_load(0, V_LIMIT_MAX_UV, false);
}

void engine_zero_output_now(void)
{
    s_out.ramp = RAMP_NONE;
    (void)out_load(0, V_LIMIT_MAX_UV, true);
}

/* ================================================================================== */
/* monitor and front-end check                                                         */
/* ================================================================================== */

const monitor_t *engine_monitor(void)        { return &s_mon; }
afe_t            engine_afe(void)            { return s_afe; }
uint64_t         engine_afe_time_us(void)    { return s_afe_t; }
uint64_t         engine_last_conversion_us(void) { return s_last_conv; }
bool             engine_post_passed(void)    { return s_post_passed; }
uint8_t          engine_adc_pga(void)        { return s_pga; }

const char *engine_afe_name(afe_t a)
{
    switch (a) {
    case AFE_OK:   return "ok";
    case AFE_FAIL: return "fail";
    case AFE_UNKNOWN:
    default:       return "unknown";
    }
}

bool engine_afe_pair_ok(int32_t i_code, int32_t v_code)
{
    return safety_afe_pair_ok(i_code, v_code);
}

static bool adc_job(job_kind_t k)
{
    return k == JOB_POST || k == JOB_SELFTEST || k == JOB_SELFCAL || k == JOB_RECONF;
}

bool engine_adc_ready(void)
{
    ads_state_t a = ads_state();
    return (a == ADS_IDLE || a == ADS_CONVERTING) && !(adc_job(s_job.kind) && !s_job.done);
}

static void set_afe(bool ok, uint64_t now)
{
    afe_t prev = s_afe;
    s_afe = ok ? AFE_OK : AFE_FAIL;
    s_afe_t = now;
    if (!ok && prev != AFE_FAIL) {
        emit_simple("EVT WARN AFE");
    }
}

/* ================================================================================== */
/* conversion checks shared by the monitor and acquisitions                             */
/* ================================================================================== */

static void sweep_end(sweep_status_t st, bool have_trip, int64_t trip_pa);

static void compliance_trip(int64_t i_pa)
{
    s_stats.compliance_trips++;
    if (cfg_get()->on_compliance == ONC_FAULT) {
        inst_fault(F_COMPLIANCE, "compliance");
        return;
    }
    bool was_zero = (s_out.ramp == RAMP_NONE && s_out.code == engine_zero_code());
    uint32_t epoch = s_epoch;
    engine_zero_output_now();
    if (epoch != s_epoch) {
        return;
    }
    if (s_sw.active) {
        sweep_end(SWS_COMPLIANCE, true, i_pa);
    }
    if (!was_zero) {
        char buf[64];
        lb_t b;
        lb_init(&b, buf, sizeof buf);
        lb_puts(&b, "EVT WARN COMPLIANCE");
        lb_kv_fix(&b, "i_a", div_round_i64(i_pa, 100), 10);
        proto_event_lb(&b);
    }
}

/* Returns false if the conversion latched a fault. */
static bool conversion_checks(const ads_sample_t *smp, bool *tripped, int64_t *i_pa_out)
{
    *tripped = false;
    if (!armed_states()) {
        return true;
    }
    if (smp->clipped) {
        inst_fault(F_AFE, smp->mux == ADS_MUX_I ? "clip_i" : "clip_v");
        return false;
    }
    if (smp->mux == ADS_MUX_I) {
        int64_t i_pa = scaling_i_pa(&s_cal, zero_for_scaling(), (double)smp->code);
        int64_t lim = (int64_t)cfg_get()->i_compliance_na * 1000;
        if (i_pa > lim || i_pa < -lim) {
            *tripped = true;
            *i_pa_out = i_pa;
        }
    }
    return true;
}

/* ================================================================================== */
/* acquisition                                                                          */
/* ================================================================================== */

static void acq_begin(uint16_t pairs, uint32_t settle_us, uint64_t t_set)
{
    memset(&s_acq, 0, sizeof s_acq);
    s_acq.active = true;
    s_acq.pairs = pairs;
    s_acq.t_set = t_set;
    s_acq.settle_req = settle_us;
    s_acq.settling = settle_us > 0u;
    s_acq.t_settle_end = t_set + settle_us;
}

static ads_mux_t acq_mux(uint16_t j)
{
    bool even_pair = ((j / 2u) % 2u) == 0u;
    bool first_of_pair = (j % 2u) == 0u;
    return (even_pair == first_of_pair) ? ADS_MUX_I : ADS_MUX_V;
}

static bool acq_done(void)
{
    return s_acq.active && !s_acq.in_flight && s_acq.j >= 2u * s_acq.pairs;
}

static void acq_service(uint64_t now)
{
    if (!s_acq.active || s_acq.in_flight || s_acq.tripped || s_acq.j >= 2u * s_acq.pairs) {
        return;
    }
    if (s_acq.settling) {
        if (now < s_acq.t_settle_end) {
            return;
        }
        s_acq.settling = false;
    }
    if (ads_start(acq_mux(s_acq.j))) {
        if (s_owner == OWN_MON) {
            s_mon_v_next = false;      /* the monitor conversion was preempted */
        }
        s_owner = OWN_ACQ;
        s_acq.in_flight = true;
    }
}

static void acq_sample(const ads_sample_t *smp)
{
    s_acq.in_flight = false;
    unsigned ch = (smp->mux == ADS_MUX_I) ? 0u : 1u;
    if (s_acq.cnt[ch] == 0u) {
        s_acq.first[ch] = smp->code;
    }
    int64_t d = (int64_t)smp->code - s_acq.first[ch];
    s_acq.sum[ch] += smp->code;
    s_acq.sq[ch] += d * d;
    s_acq.cnt[ch]++;
    s_acq.clip[ch] = s_acq.clip[ch] || smp->clipped;
    s_acq.late = s_acq.late || smp->late;
    s_acq.tmid[ch] += (smp->t_start_us + smp->t_drdy_us) / 2u;
    if (s_acq.j == 0u) {
        s_acq.t_first_start = smp->t_start_us;
    }
    s_acq.j++;
}

static int32_t acq_mean(unsigned ch)
{
    if (s_acq.cnt[ch] == 0u) {
        return 0;
    }
    return (int32_t)div_round_i64(s_acq.sum[ch], (int64_t)s_acq.cnt[ch]);
}

static double acq_sd(unsigned ch)
{
    uint16_t n = s_acq.cnt[ch];
    if (n < 2u) {
        return -1.0;
    }
    double sumd = (double)(s_acq.sum[ch] - (int64_t)n * s_acq.first[ch]);
    double var = ((double)s_acq.sq[ch] - sumd * sumd / (double)n) / (double)(n - 1u);
    return var > 0.0 ? sqrt(var) : 0.0;
}

static uint16_t sd_deci(double sd)
{
    if (sd < 0.0) {
        return 0xFFFFu;
    }
    double x = floor(sd * 10.0 + 0.5);
    return x >= 65534.0 ? 0xFFFEu : (uint16_t)x;
}

static bool acq_settle_late(void)
{
    if (s_acq.settle_req == 0u) {
        return false;
    }
    return s_acq.t_first_start > s_acq.t_set + s_acq.settle_req + LATE_SETTLE_US;
}

/* Common flags and classification for a completed acquisition. */
static uint16_t acq_flags(int32_t i_raw, int32_t v_raw, bool classify, uint16_t code)
{
    uint16_t f = 0;
    if (s_cal_src == CALSRC_NOMINAL) {
        f |= PF_CAL_NOMINAL;
    }
    if (engine_zero_state(port_now_us()) != ZERO_VALID) {
        f |= PF_ZERO_STALE;
    }
    if (s_acq.late || acq_settle_late()) {
        f |= PF_LATE;
    }
    if (s_acq.clip[1]) {
        f |= PF_OVR_V;
    }
    if (s_acq.clip[0]) {
        f |= PF_OVR_I;
    }
    if (!classify) {
        return f;
    }
    point_check_t p;
    p.v_set = (double)scaling_code_to_v_uv(&s_cal, code) / 1e6;
    p.v_k = (double)scaling_v_nv(&s_cal, zero_for_scaling(), (double)v_raw) / 1e9;
    p.i = (double)scaling_i_pa(&s_cal, zero_for_scaling(), (double)i_raw) / 1e12;
    p.clip_v = s_acq.clip[1];
    p.clip_i = s_acq.clip[0];
    p.cal_nominal = (s_cal_src == CALSRC_NOMINAL);
    p.sigma_i_zero = s_zero.valid ? s_zero.sd_i * s_cal.g_i : -1.0;
    p.r_s = s_cal.r_s;
    p.r_top = s_cal.r_top;
    p.r_sd = R_SD_OHM;
    p.i_plateau = s_cal.i_plateau;
    return (uint16_t)(f | safety_classify(&p));
}

/* ================================================================================== */
/* sample dispatch                                                                      */
/* ================================================================================== */

static void mon_sample(const ads_sample_t *smp, uint64_t now)
{
    bool tripped = false;
    int64_t i_pa = 0;
    uint32_t epoch = s_epoch;
    if (!conversion_checks(smp, &tripped, &i_pa) || epoch != s_epoch) {
        return;
    }
    if (smp->mux == ADS_MUX_I) {
        s_mon.i_code = smp->code;
        s_mon.i_t_us = now;
        s_mon.i_valid = true;
        s_stats.monitor_pairs++;
        s_mon_pair_open = inst_state() == ST_DISARMED && !hb_enabled();
    } else if (smp->mux == ADS_MUX_V) {
        s_mon.v_code = smp->code;
        s_mon.v_t_us = now;
        s_mon.v_valid = true;
        /* front-end check with the gate open (DISARMED: T_DISARM elapsed) on
         * the I/V pair of consecutive conversions; at <= 10 SPS one conversion
         * alone takes longer than the monitor period, hence the t18 term */
        if (s_mon_pair_open && inst_state() == ST_DISARMED && !hb_enabled()
            && now - s_mon.i_t_us <= rate()->t18_us + MONITOR_PERIOD_US) {
            set_afe(safety_afe_pair_ok(s_mon.i_code, s_mon.v_code), now);
        }
        s_mon_pair_open = false;
    }
    if (tripped) {
        compliance_trip(i_pa);
    }
}

static void dispatch(uint64_t now)
{
    ads_sample_t smp;
    if (!ads_take(&smp)) {
        if (s_owner != OWN_NONE && ads_state() != ADS_CONVERTING) {
            /* the conversion was lost (ADC error / re-init) */
            s_mon_pair_open = false;
            if (s_owner == OWN_ACQ) {
                s_acq.in_flight = false;
            }
            s_owner = OWN_NONE;
        }
        return;
    }
    own_t owner = s_owner;
    s_owner = OWN_NONE;
    s_last_conv = now;
    if (owner != OWN_MON) {
        s_mon_pair_open = false;   /* another conversion came between I and V */
    }
    switch (owner) {
    case OWN_MON:
        mon_sample(&smp, now);
        break;
    case OWN_ACQ: {
        if (!s_acq.active) {
            break;
        }
        bool tripped = false;
        int64_t i_pa = 0;
        uint32_t epoch = s_epoch;
        if (!conversion_checks(&smp, &tripped, &i_pa) || epoch != s_epoch) {
            break;
        }
        acq_sample(&smp);
        if (tripped) {
            s_acq.tripped = true;
            s_acq.trip_pa = i_pa;
            compliance_trip(i_pa);
        }
        break;
    }
    case OWN_JOB:
        s_job.smp = smp;
        s_job.have = true;
        break;
    case OWN_NONE:
    default:
        break;   /* orphaned conversion (e.g. after an abort): discarded */
    }
}

static void monitor_service(uint64_t now)
{
    if (s_owner != OWN_NONE || !ads_idle() || inst_state() == ST_BOOT || adc_job(s_job.kind)) {
        return;
    }
    if (s_acq.active && !s_acq.settling) {
        return;   /* the acquisition needs the ADC now */
    }
    if (s_job.kind != JOB_NONE && !s_acq.active) {
        return;   /* a job is about to start its acquisition */
    }
    bool settle = s_acq.active && s_acq.settling;
    if (s_mon_v_next && !settle) {
        if (ads_start(ADS_MUX_V)) {
            s_owner = OWN_MON;
            s_mon_v_next = false;
        }
        return;
    }
    if (now < s_mon_due) {
        return;
    }
    if (settle && s_acq.t_settle_end < now + rate()->t18_us + 1000u) {
        return;   /* it would be preempted before completing */
    }
    if (ads_start(ADS_MUX_I)) {
        s_owner = OWN_MON;
        s_mon_due = now + MONITOR_PERIOD_US;
        s_mon_v_next = !settle;
    }
}

/* ================================================================================== */
/* sweeps                                                                                */
/* ================================================================================== */

const char *sweep_status_name(sweep_status_t s)
{
    switch (s) {
    case SWS_RUNNING:      return "running";
    case SWS_COMPLETE:     return "complete";
    case SWS_ABORTED:      return "aborted";
    case SWS_COMPLIANCE:   return "compliance";
    case SWS_OVERFLOW:     return "overflow";
    case SWS_SESSION_LOST: return "session_lost";
    case SWS_HOST_TIMEOUT: return "host_timeout";
    case SWS_FAULT:        return "fault";
    case SWS_NONE:
    default:               return "none";
    }
}

swg_status_t engine_sweep_define_wp(const int32_t *wp_uv, uint8_t n_wp, int32_t step_uv,
                                    uint16_t cycles, uint32_t *points, int32_t *suggest)
{
    sweep_def_t d;
    memset(&d, 0, sizeof d);
    d.kind = SWEEP_WAYPOINTS;
    if (n_wp > SWEEP_WP_MAX) {
        return SWG_TOO_MANY;
    }
    memcpy(d.wp_uv, wp_uv, (size_t)n_wp * sizeof wp_uv[0]);
    d.n_wp = n_wp;
    d.step_uv = step_uv;
    d.cycles = cycles;
    uint32_t bad = 0;
    *points = 0;
    *suggest = 0;
    swg_status_t st = sweep_validate(&d, cfg_get()->v_limit_uv, points, &bad);
    if (st == SWG_NOT_DIVISIBLE) {
        *suggest = sweep_suggest_step(&d);
    }
    if (st == SWG_OK) {
        s_def = d;
    }
    return st;
}

void engine_list_clear(void)
{
    s_list_n = 0;
    if (s_def.kind == SWEEP_LIST) {
        memset(&s_def, 0, sizeof s_def);
    }
}

bool engine_list_add(const sweep_point_t *pts, uint32_t n, uint32_t *total)
{
    if (s_list_n + n > SWEEP_LIST_MAX) {
        *total = s_list_n;
        return false;
    }
    memcpy(&s_list[s_list_n], pts, (size_t)n * sizeof pts[0]);
    s_list_n += n;
    *total = s_list_n;
    return true;
}

uint32_t engine_list_len(void)
{
    return s_list_n;
}

swg_status_t engine_list_use(uint32_t *points)
{
    sweep_def_t d;
    memset(&d, 0, sizeof d);
    d.kind = SWEEP_LIST;
    d.list = s_list;
    d.list_n = s_list_n;
    d.cycles = 1;
    uint32_t bad = 0;
    *points = 0;
    swg_status_t st = sweep_validate(&d, cfg_get()->v_limit_uv, points, &bad);
    if (st == SWG_OK) {
        s_def = d;
    }
    return st;
}

sweep_kind_t engine_sweep_kind(void)
{
    return s_def.kind;
}

uint32_t engine_sweep_points(void)
{
    if (s_def.kind == SWEEP_NONE) {
        return 0;
    }
    uint32_t n = 0, bad = 0;
    if (sweep_validate(&s_def, V_LIMIT_MAX_UV, &n, &bad) != SWG_OK) {
        return 0;
    }
    return n;
}

static uint64_t conv_pair_us(void)
{
    return 2u * ((uint64_t)rate()->t18_us + CONV_OVERHEAD_US);
}

uint64_t engine_meas_estimate_us(uint32_t pairs)
{
    return (uint64_t)pairs * conv_pair_us();
}

uint16_t engine_zero_pairs(void)
{
    uint64_t p = MEAS_MAX_DURATION_US / conv_pair_us();
    if (p > ZERO_PAIRS_MAX) {
        p = ZERO_PAIRS_MAX;
    }
    return (uint16_t)(p < 1u ? 1u : p);
}

uint64_t engine_sweep_estimate_us(void)
{
    if (s_def.kind == SWEEP_NONE) {
        return 0;
    }
    const cfg_t *c = cfg_get();
    uint32_t n = engine_sweep_points();
    if (n == 0u) {
        return 0;
    }
    uint64_t conv = (uint64_t)c->n_avg * conv_pair_us();
    int32_t v0 = (s_def.kind == SWEEP_LIST) ? s_def.list[0].v_uv : s_def.wp_uv[0];
    int64_t dv = (int64_t)v0 - (int64_t)s_out.uv;
    uint64_t adv = (uint64_t)(dv < 0 ? -dv : dv);
    uint64_t t = adv * 1000000u / c->ramp_rate_uv_s + c->first_settle_us + conv;
    if (s_def.kind == SWEEP_LIST) {
        for (uint32_t i = 1; i < n; i++) {
            uint32_t st = s_def.list[i].settle_us ? s_def.list[i].settle_us : c->settle_us;
            t += st + conv;
        }
    } else {
        t += (uint64_t)(n - 1u) * (c->settle_us + conv);
    }
    return t;
}

static bool point_code_ok(int32_t v, int32_t limit)
{
    uint16_t code;
    return scaling_v_to_code(&s_cal, v, limit, &code) == E_OK;
}

err_t engine_sweep_run(uint16_t *id, uint32_t *points, uint64_t *est_us, const char **why)
{
    *why = "";
    if (s_def.kind == SWEEP_NONE) {
        *why = "no sweep defined";
        return E_ARG;
    }
#if FW_BRINGUP
    if (dac_raw_active()) {
        *why = "DAC.RAW active";
        return E_STATE;
    }
#endif
    if (s_job.kind != JOB_NONE) {
        *why = "measurement in progress";
        return E_BUSY;
    }
    const cfg_t *c = cfg_get();
    uint32_t n = 0, bad = 0;
    swg_status_t st = sweep_validate(&s_def, c->v_limit_uv, &n, &bad);
    if (st != SWG_OK) {
        *why = swg_status_name(st);
        return st == SWG_LIMIT ? E_RANGE : E_ARG;
    }
    if (s_def.kind == SWEEP_WAYPOINTS) {
        for (unsigned i = 0; i < s_def.n_wp; i++) {
            if (!point_code_ok(s_def.wp_uv[i], c->v_limit_uv)) {
                *why = "dac range";
                return E_RANGE;
            }
        }
    } else {
        for (uint32_t i = 0; i < s_def.list_n; i++) {
            if (!point_code_ok(s_def.list[i].v_uv, c->v_limit_uv)) {
                *why = "dac range";
                return E_RANGE;
            }
        }
    }
    *est_us = engine_sweep_estimate_us();

    memset(&s_sw, 0, sizeof s_sw);
    s_sw.def = s_def;
    sweep_iter_init(&s_sw.it, &s_sw.def);
    s_sw.total = n;
    s_sw.id = ++s_sweep_counter;
    s_sw.status = SWS_RUNNING;
    s_sw.t0 = port_now_us();
    s_sw.active = true;
    if (!sweep_iter_next(&s_sw.it, &s_sw.v, &s_sw.settle, &s_sw.seg, &s_sw.idx)) {
        s_sw.active = false;
        s_sw.status = SWS_NONE;
        *why = "empty";
        return E_ARG;
    }
    s_sw.first = true;
    s_sw.phase = SW_PRE;
    ramp_start(RAMP_PRE, s_sw.v, c->ramp_rate_uv_s, c->v_limit_uv);
    *id = s_sw.id;
    *points = n;
    return E_OK;
}

static void sweep_end(sweep_status_t st, bool have_trip, int64_t trip_pa)
{
    if (!s_sw.active) {
        return;
    }
    s_sw.active = false;
    s_sw.status = st;
    s_acq.active = false;

    char buf[160];
    lb_t b;
    lb_init(&b, buf, sizeof buf);
    lb_puts(&b, "EVT SWEEP_END");
    lb_kv_u(&b, "id", s_sw.id);
    lb_kv_s(&b, "status", sweep_status_name(st));
    lb_kv_u(&b, "points", s_sw.done);
    if (st == SWS_FAULT) {
        lb_kv_s(&b, "fault", fault_name(inst_fault_code()));
    }
    if (have_trip) {
        lb_kv_u(&b, "trip_idx", s_sw.idx);
        lb_kv_fix(&b, "trip_i_a", div_round_i64(trip_pa, 100), 10);
    }
    proto_event_ordered(&b);

    const cfg_t *c = cfg_get();
    if (s_out.ramp == RAMP_PRE) {
        s_out.ramp = RAMP_NONE;
    }
    switch (st) {
    case SWS_COMPLETE:
        if (c->end == END_ZERO) {
            ramp_start(RAMP_END, 0, c->ramp_rate_uv_s, V_LIMIT_MAX_UV);
        }
        break;
    case SWS_ABORTED:
    case SWS_OVERFLOW:
        ramp_start(RAMP_END, 0, c->ramp_rate_uv_s, V_LIMIT_MAX_UV);
        break;
    case SWS_COMPLIANCE:   /* already at the zero code */
    case SWS_SESSION_LOST: /* the disarm ramp follows   */
    case SWS_HOST_TIMEOUT:
    case SWS_FAULT:
    case SWS_NONE:
    case SWS_RUNNING:
    default:
        break;
    }
    inst_sweep_finished();
}

void engine_sweep_abort(sweep_status_t why)
{
    sweep_end(why, false, 0);
}

bool           engine_sweeping(void)           { return s_sw.active; }
uint16_t       engine_sweep_id(void)           { return s_sw.id; }
uint32_t       engine_sweep_done_points(void)  { return s_sw.done; }
sweep_status_t engine_sweep_status(void)       { return s_sw.status; }
uint64_t       engine_sweep_t0_us(void)        { return s_sw.t0; }
uint32_t       engine_warn_bits(void)          { return s_sw.warn_bits; }

static void sweep_point_warnings(uint16_t flags)
{
    char buf[48];
    lb_t b;
    if (flags & PF_KELVIN) {
        if (s_sw.kelvin_run < 255u) {
            s_sw.kelvin_run++;
        }
        if (s_sw.kelvin_run >= KELVIN_RUN_WARN && !s_sw.kelvin_warned) {
            s_sw.kelvin_warned = true;
            s_sw.warn_bits |= W_KELVIN;
            lb_init(&b, buf, sizeof buf);
            lb_puts(&b, "EVT WARN KELVIN");
            lb_kv_u(&b, "idx", s_sw.idx);
            proto_event_lb(&b);
        }
    } else {
        s_sw.kelvin_run = 0;
    }
    if ((flags & PF_NO_OUTPUT) && !s_sw.noout_warned) {
        s_sw.noout_warned = true;
        s_sw.warn_bits |= W_NO_OUTPUT;
        lb_init(&b, buf, sizeof buf);
        lb_puts(&b, "EVT WARN NO_OUTPUT");
        lb_kv_u(&b, "idx", s_sw.idx);
        proto_event_lb(&b);
    }
    if ((flags & PF_LATE) && !s_sw.late_warned) {
        s_sw.late_warned = true;
        lb_init(&b, buf, sizeof buf);
        lb_puts(&b, "EVT WARN LATE");
        lb_kv_u(&b, "idx", s_sw.idx);
        proto_event_lb(&b);
    }
}

static uint32_t rel32(uint64_t t, uint64_t t0)
{
    return (uint32_t)(t - t0);   /* wraps after 71.6 min by design (spec §7.8) */
}

static void sweep_finish_point(void)
{
    pt_rec_t r;
    memset(&r, 0, sizeof r);
    r.idx = s_sw.idx;
    r.seg = s_sw.seg;
    r.sweep_id = s_sw.id;
    r.dac_code = s_out.code;
    r.n = s_acq.pairs;
    r.i_raw = acq_mean(0);
    r.v_raw = acq_mean(1);
    r.i_sd = sd_deci(acq_sd(0));
    r.v_sd = sd_deci(acq_sd(1));
    r.t_set_us = rel32(s_acq.t_set, s_sw.t0);
    r.t_i_us = rel32(s_acq.tmid[0] / (s_acq.cnt[0] ? s_acq.cnt[0] : 1u), s_sw.t0);
    r.t_v_us = rel32(s_acq.tmid[1] / (s_acq.cnt[1] ? s_acq.cnt[1] : 1u), s_sw.t0);
    uint16_t flags = acq_flags(r.i_raw, r.v_raw, true, r.dac_code);
    if (s_sw.first) {
        flags |= PF_FIRST;
    }
    r.flags = flags;
    if (flags & PF_LATE) {
        s_stats.late_points++;
    }
    if (!results_push(&r)) {
        sweep_end(SWS_OVERFLOW, false, 0);
        return;
    }
    s_stats.points++;
    s_sw.done++;
    sweep_point_warnings(flags);
}

static void sweep_service(uint64_t now)
{
    if (!s_sw.active) {
        return;
    }
    const cfg_t *c = cfg_get();
    if (s_sw.phase == SW_PRE) {
        if (s_out.ramp != RAMP_NONE) {
            return;
        }
        /* pre-ramp finished: the first point's code is loaded */
        acq_begin(c->n_avg, c->first_settle_us, s_out.t_load);
        s_sw.phase = SW_ACQ;
        return;
    }
    acq_service(now);
    if (!acq_done()) {
        return;
    }
    sweep_finish_point();
    if (!s_sw.active) {
        return;
    }
    s_sw.first = false;
    if (!sweep_iter_next(&s_sw.it, &s_sw.v, &s_sw.settle, &s_sw.seg, &s_sw.idx)) {
        s_acq.active = false;
        sweep_end(SWS_COMPLETE, false, 0);
        return;
    }
    uint32_t epoch = s_epoch;
    if (!out_load(s_sw.v, c->v_limit_uv, true) || epoch != s_epoch) {
        return;
    }
    acq_begin(c->n_avg, s_sw.settle ? s_sw.settle : c->settle_us, s_out.t_load);
}

/* ================================================================================== */
/* jobs                                                                                  */
/* ================================================================================== */

bool engine_job_active(void)
{
    return s_job.kind != JOB_NONE && !s_job.done;
}

static void job_finish(err_t e, const char *msg)
{
    s_job.res.err = e;
    s_job.res.msg = msg;
    s_job.done = true;
    s_acq.active = false;
}

static void job_reset(void)
{
    memset(&s_job, 0, sizeof s_job);
}

static bool job_slot_free(void)
{
    return s_job.kind == JOB_NONE || (s_job.done && s_job.autoclear);
}

err_t engine_job_start(job_kind_t k, uint32_t arg)
{
    if (!job_slot_free()) {
        return E_BUSY;
    }
    const cfg_t *c = cfg_get();
    uint16_t pairs = 0;
    if (k == JOB_MEAS || k == JOB_ZERO) {
        if (!engine_adc_ready() || s_sw.active) {
            return E_BUSY;
        }
        if (k == JOB_MEAS) {
            pairs = (uint16_t)(arg ? arg : c->n_avg);
            if (engine_meas_estimate_us(pairs) > MEAS_MAX_DURATION_US) {
                return E_RANGE;
            }
        } else {
            pairs = engine_zero_pairs();
        }
    }
    job_reset();
    s_job.kind = k;
    s_job.res.kind = k;
    s_job.t0 = port_now_us();
    s_job.phase = 0;
    s_job.autoclear = (k == JOB_POST);
    if (k == JOB_MEAS || k == JOB_ZERO) {
        acq_begin(pairs, 0, s_job.t0);
    }
    return E_OK;
}

err_t engine_cal_save_start(uint32_t timestamp, const char *note)
{
    if (!job_slot_free()) {
        return E_BUSY;
    }
    job_reset();
    s_job.kind = JOB_CALSAVE;
    s_job.res.kind = JOB_CALSAVE;
    s_job.t0 = port_now_us();
    s_job.arg_ts = timestamp;
    s_job.arg_has_note = (note != NULL);
    if (note != NULL) {
        strncpy(s_job.arg_note, note, CAL_NOTE_LEN - 1u);
        s_job.arg_note[CAL_NOTE_LEN - 1u] = '\0';
    }
    return E_OK;
}

static void job_calsave(void)
{
    /* wait until no conversion is in flight (the monitor is held off) */
    if (s_owner != OWN_NONE || ads_state() == ADS_CONVERTING || ads_state() == ADS_INIT) {
        return;
    }
    uint32_t seq = 0;
    err_t e = engine_cal_save(s_job.arg_ts, s_job.arg_has_note ? s_job.arg_note : NULL, &seq);
    s_job.res.seq = seq;
    job_finish(e, e == E_RANGE ? "implausible coefficients" : "erase/program/verify failed");
}

err_t engine_adc_reconfigure(uint32_t rate_x10, uint8_t pga)
{
    if (!job_slot_free() || ads_rate_by_sps_x10(rate_x10) == NULL) {
        return E_BUSY;
    }
    job_reset();
    s_job.kind = JOB_RECONF;
    s_job.res.kind = JOB_RECONF;
    s_job.t0 = port_now_us();
    s_job.autoclear = true;
    s_job.arg_rate_x10 = rate_x10;
    s_job.arg_pga = pga;
    engine_zero_mark_stale();
    return E_OK;
}

bool engine_job_take(job_result_t *out)
{
    if (s_job.kind == JOB_NONE || !s_job.done) {
        return false;
    }
    *out = s_job.res;
    job_reset();
    return true;
}

void engine_job_cancel(void)
{
    if (s_job.kind == JOB_MEAS || s_job.kind == JOB_ZERO) {
        s_acq.active = false;
        job_reset();
        return;
    }
    /* ADC management and flash jobs run to completion (stopping them half-way
     * would leave the ADC or a sector in an unknown state); only their
     * response is dropped, and the slot frees itself when they finish. */
    if (s_job.done) {
        job_reset();
    } else {
        s_job.autoclear = true;
    }
}

/* Start a single job conversion; true once the sample is in s_job.smp. */
static bool job_convert(ads_mux_t mux, bool *started)
{
    if (s_job.have) {
        s_job.have = false;
        *started = false;
        return true;
    }
    if (!*started && s_owner == OWN_NONE && ads_start(mux)) {
        s_owner = OWN_JOB;
        *started = true;
    }
    return false;
}

static bool s_job_conv_started;

static void post_fail(const char *detail)
{
    s_post_passed = false;
    if (s_job.kind == JOB_POST) {
        job_finish(E_FAULT, detail);
        inst_post_done(false, detail);
    } else {
        job_finish(E_FAULT, "POST");
        inst_fault(F_POST, detail);
    }
}

/* POST / SELFTEST (spec §7.2). Phases:
 * 0 init at PGA 1, 1 wait idle, 2 NEGREF, 3 ZERO, 4 reconfigure PGA 2,
 * 5 wait idle, 6 AFE I, 7 AFE V. */
static void job_post(uint64_t now)
{
    ads_cfg_t c1 = adc_cfg(ADS_PGA_1);
    ads_cfg_t c2 = adc_cfg(s_pga);
    if (ads_state() == ADS_ERROR && s_job.phase != 0) {
        post_fail(s_job.phase <= 1 ? "post_init" : "post_adc");
        return;
    }
    switch (s_job.phase) {
    case 0:
        if (s_owner != OWN_NONE) {
            return;   /* let a monitor conversion finish */
        }
        ads_init_begin(&c1);
        s_job_conv_started = false;
        s_job.phase = 1;
        break;
    case 1:
        if (ads_idle()) {
            s_job.phase = 2;
        }
        break;
    case 2:
        if (job_convert(ADS_MUX_NEGREF, &s_job_conv_started)) {
            s_job.res.negref = s_job.smp.code;
            s_job.phase = 3;
        }
        break;
    case 3:
        if (job_convert(ADS_MUX_ZERO, &s_job_conv_started)) {
            s_job.res.zero_code = s_job.smp.code;
            if (iabs32(s_job.res.negref - NEGREF_EXPECT) > NEGREF_TOL) {
                post_fail("post_negref");
                return;
            }
            if (iabs32(s_job.res.zero_code) > ZERO_CODE_TOL) {
                post_fail("post_zero");
                return;
            }
            s_job.phase = 4;
        }
        break;
    case 4:
        if (ads_reconfigure(&c2)) {
            s_job.phase = 5;
        }
        break;
    case 5:
        if (ads_idle()) {
            s_job.phase = 6;
        }
        break;
    case 6:
        if (job_convert(ADS_MUX_I, &s_job_conv_started)) {
            s_mon.i_code = s_job.smp.code;
            s_mon.i_t_us = now;
            s_mon.i_valid = true;
            s_job.phase = 7;
        }
        break;
    case 7:
        if (job_convert(ADS_MUX_V, &s_job_conv_started)) {
            s_mon.v_code = s_job.smp.code;
            s_mon.v_t_us = now;
            s_mon.v_valid = true;
            bool ok = safety_afe_pair_ok(s_mon.i_code, s_mon.v_code);
            set_afe(ok, now);
            s_job.res.afe_ok = ok;
            s_post_passed = true;
            engine_zero_mark_stale();
            job_finish(E_OK, "");
            if (s_job.kind == JOB_POST) {
                inst_post_done(true, "");
            }
        }
        break;
    default:
        post_fail("post_phase");
        break;
    }
}

static void job_reconf(void)
{
    if (ads_state() == ADS_ERROR && s_job.phase != 0) {
        job_finish(E_FAULT, "adc");
        return;
    }
    if (s_job.phase == 0) {
        if (s_owner != OWN_NONE || !ads_idle()) {
            if (ads_state() == ADS_ERROR) {
                job_finish(E_FAULT, "adc");
            }
            return;
        }
        ads_cfg_t c;
        if (s_job.kind == JOB_RECONF) {
            const ads_rate_t *r = ads_rate_by_sps_x10(s_job.arg_rate_x10);
            s_pga = s_job.arg_pga;
            c.drate = r ? r->drate : rate()->drate;
            c.pga = s_pga;
        } else {
            c = adc_cfg(s_pga);
        }
        if (ads_reconfigure(&c)) {
            engine_zero_mark_stale();
            s_job.phase = 1;
        }
        return;
    }
    if (ads_idle()) {
        job_finish(E_OK, "");
    }
}

static void job_meas(void)
{
    acq_service(port_now_us());
    if (s_acq.tripped) {
        /* compliance zeroed the output; report what was measured */
    } else if (!acq_done()) {
        if (ads_state() == ADS_ERROR) {
            job_finish(E_FAULT, "adc");
        }
        return;
    }
    job_result_t *r = &s_job.res;
    r->n = s_acq.cnt[0] < s_acq.cnt[1] ? s_acq.cnt[1] : s_acq.cnt[0];
    r->i_raw = acq_mean(0);
    r->v_raw = acq_mean(1);
    r->i_sd = sd_deci(acq_sd(0));
    r->v_sd = sd_deci(acq_sd(1));
    r->v_nv = scaling_v_nv(&s_cal, zero_for_scaling(), (double)r->v_raw);
    r->i_pa = scaling_i_pa(&s_cal, zero_for_scaling(), (double)r->i_raw);
    bool classify = (inst_state() == ST_ARMED);
    r->flags = (uint16_t)(acq_flags(r->i_raw, r->v_raw, classify, s_out.code) | PF_MEAS);
    if (s_acq.tripped) {
        r->flags |= PF_COMPLIANCE;
    }
    job_finish(E_OK, "");
}

static void job_zero(uint64_t now)
{
    acq_service(now);
    if (!acq_done()) {
        if (ads_state() == ADS_ERROR) {
            job_finish(E_FAULT, "adc");
        } else if (s_acq.tripped) {
            job_finish(E_RANGE, "implausible");
        }
        return;
    }
    zero_t z;
    memset(&z, 0, sizeof z);
    z.o_i = (double)s_acq.sum[0] / (double)s_acq.cnt[0];
    z.o_v = (double)s_acq.sum[1] / (double)s_acq.cnt[1];
    z.sd_i = acq_sd(0) < 0.0 ? 0.0 : acq_sd(0);
    z.sd_v = acq_sd(1) < 0.0 ? 0.0 : acq_sd(1);
    z.t_us = now;
    z.sps_x10 = cfg_get()->adc_rate_x10;
    z.armed = hb_enabled();
    z.pairs = s_acq.pairs;
    z.valid = true;
    s_job.res.zero = z;
    if (s_acq.clip[0] || s_acq.clip[1]
        || fabs(z.o_i * s_cal.g_i) > ZERO_MAX_I_A || fabs(z.o_v * s_cal.g_v) > ZERO_MAX_V_V) {
        job_finish(E_RANGE, "implausible");
        return;
    }
    s_zero = z;
    s_zero_stale = false;
    job_finish(E_OK, "");
}

static void job_service(uint64_t now)
{
    if (s_job.kind != JOB_NONE && s_job.done && s_job.autoclear) {
        job_reset();
    }
    if (s_job.kind == JOB_NONE || s_job.done) {
        return;
    }
    if (now - s_job.t0 > JOB_TIMEOUT_US) {
        if (s_job.kind == JOB_POST || s_job.kind == JOB_SELFTEST) {
            post_fail("post_timeout");
        } else {
            job_finish(E_INTERNAL, "timeout");
        }
        return;
    }
    switch (s_job.kind) {
    case JOB_POST:
    case JOB_SELFTEST:
        job_post(now);
        break;
    case JOB_SELFCAL:
    case JOB_RECONF:
        job_reconf();
        break;
    case JOB_MEAS:
        job_meas();
        break;
    case JOB_ZERO:
        job_zero(now);
        break;
    case JOB_CALSAVE:
        job_calsave();
        break;
    case JOB_NONE:
    default:
        break;
    }
}

/* ================================================================================== */
/* disarm and fault                                                                      */
/* ================================================================================== */

void engine_disarm_begin(void)
{
    if (s_job.kind == JOB_MEAS || s_job.kind == JOB_ZERO) {
        if (!s_job.done) {
            job_finish(E_STATE, "disarming");
        }
    }
    s_out.disarm_done = false;
    uint32_t r = cfg_get()->ramp_rate_uv_s;
    if (r < (uint32_t)DISARM_MIN_RAMP_UV_PER_S) {
        r = (uint32_t)DISARM_MIN_RAMP_UV_PER_S;
    }
    bool raw = false;
#if FW_BRINGUP
    raw = dac_raw_active();
#endif
    if (raw || !s_out.known) {
        /* unknown or arbitrary codes: load the zero code at once */
        s_out.ramp = RAMP_NONE;
        if (!out_load(0, V_LIMIT_MAX_UV, true)) {
            return;
        }
    }
    ramp_start(RAMP_DISARM, 0, r, V_LIMIT_MAX_UV);
}

bool engine_disarm_done(void)
{
    return s_out.disarm_done;
}

void engine_fault_entry(fault_t f)
{
    s_epoch++;
    if (s_sw.active) {
        sweep_end(SWS_FAULT, false, 0);
    }
    if (s_job.kind != JOB_NONE && !s_job.done) {
        if (s_job.kind == JOB_POST || s_job.kind == JOB_SELFTEST) {
            s_post_passed = false;
        }
        job_finish(E_FAULT, fault_name(f));
    }
    s_acq.active = false;
    s_out.ramp = RAMP_NONE;
    s_out.uv = 0;
    s_out.code = dac_shadow_a();   /* dac_zero_safe() loaded the zero pair */
    s_out.known = dac_shadow_valid();
    s_out.disarm_done = false;
    if (f == F_ADC_TIMEOUT || f == F_ADC_COMM || f == F_STALE) {
        ads_cfg_t c = adc_cfg(s_pga);
        s_owner = OWN_NONE;
        ads_init_begin(&c);
    }
}

/* ================================================================================== */
/* lifecycle                                                                              */
/* ================================================================================== */

void engine_init(void)
{
    memset(&s_out, 0, sizeof s_out);
    memset(&s_mon, 0, sizeof s_mon);
    memset(&s_acq, 0, sizeof s_acq);
    memset(&s_sw, 0, sizeof s_sw);
    memset(&s_def, 0, sizeof s_def);
    memset(&s_zero, 0, sizeof s_zero);
    memset(&s_stats, 0, sizeof s_stats);
    job_reset();
    s_list_n = 0;
    s_sweep_counter = 0;
    s_owner = OWN_NONE;
    s_mon_due = 0;
    s_mon_v_next = false;
    s_mon_pair_open = false;
    s_last_conv = 0;
    s_afe = AFE_UNKNOWN;
    s_afe_t = 0;
    s_post_passed = false;
    s_zero_stale = false;
    s_pga = ADS_PGA_2;
    s_epoch = 0;
    s_job_conv_started = false;

    /* the early zero (dac_zero_safe) holds 0x8000 / 0xFFFF */
    s_out.uv = 0;
    s_out.code = dac_shadow_a();
    s_out.known = dac_shadow_valid();

    engine_cal_reload();
    (void)engine_job_start(JOB_POST, 0);
}

void engine_service(uint64_t now_us)
{
    uint32_t epoch = s_epoch;
    dispatch(now_us);
    if (epoch != s_epoch) {
        return;
    }
    job_service(now_us);
    if (epoch != s_epoch) {
        return;
    }
    sweep_service(now_us);
    if (epoch != s_epoch) {
        return;
    }
    ramp_service(now_us);
    if (epoch != s_epoch) {
        return;
    }
    monitor_service(now_us);

    /* DAC scrub (spec §7.4): re-send the shadow every 100 ms while armed */
    bool raw = false;
#if FW_BRINGUP
    raw = dac_raw_active();
#endif
    if (hb_enabled() && !raw && s_out.known
        && now_us - s_out.t_last_write >= DAC_SCRUB_PERIOD_US) {
        s_out.t_last_write = now_us;
        if (!dac_scrub()) {
            inst_fault(F_DAC_SPI, "dac_scrub");
        }
    }
}

void engine_get_stats(engine_stats_t *out)
{
    *out = s_stats;
}
