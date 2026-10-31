/*
 * engine.h — the output, measurement and sweep engine
 * (docs/firmware/firmware_spec.md §7.5-§7.8, §7.10).
 *
 * The engine owns:
 *  - the output: the DAC code in force, ramps (SRC, sweep pre-ramp, end
 *    ramp, disarm ramp) on a 1 ms grid of <= 10 mV steps, and the 100 ms DAC
 *    scrub while the heartbeat runs. Every code comes from scaling_v_to_code()
 *    (SI-5) or is the zero code it returns for 0 V;
 *  - the ADC schedule: the background monitor (one I/V pair per >= 100 ms,
 *    I only during a settle, never recorded), point acquisitions
 *    (I,V,V,I,... with compliance on every I conversion), and the ADC
 *    management jobs (POST, SELFTEST, SELFCAL, reconfiguration);
 *  - sweeps: pre-ramp, first settle, points, end action, records into the
 *    result ring (never waiting for USB), SWEEP_BEGIN/SWEEP_END events;
 *  - deferred measurement jobs (MEAS?, ZERO, SELFTEST, ADC.SELFCAL);
 *  - the calibration in RAM and the session zero.
 * State transitions and fault entry belong to instrument.c; the engine asks
 * for them through instrument.h.
 */
#ifndef ENGINE_H
#define ENGINE_H

#include "ads1256.h"
#include "calib.h"
#include "results.h"
#include "sweep_gen.h"

/* ---- calibration ------------------------------------------------------- */
typedef enum { CALSRC_NOMINAL = 0, CALSRC_STORED, CALSRC_RAM } cal_source_t;

const cal_coeffs_t *engine_cal(void);
cal_source_t        engine_cal_source(void);
const char         *engine_cal_source_name(void);
uint32_t            engine_cal_seq(void);           /* 0 if none stored      */
const calib_scan_t *engine_cal_scan(void);
void                engine_cal_set(const cal_coeffs_t *c);   /* source := ram */
void                engine_cal_default(void);
void                engine_cal_reload(void);
err_t               engine_cal_save(uint32_t timestamp, const char *note, uint32_t *seq);

/* ---- session zero --------------------------------------------------------- */
typedef enum { ZERO_NONE = 0, ZERO_VALID, ZERO_STALE } zero_state_t;
#define ZERO_STALE_AGE_US  3600000000ull           /* 1 h                    */

const zero_t *engine_zero(void);
zero_state_t  engine_zero_state(uint64_t now_us);
const char   *engine_zero_state_name(zero_state_t z);
void          engine_zero_mark_stale(void);        /* new session, rate change, SELFCAL */

/* ---- output ------------------------------------------------------------------ */
int32_t  engine_out_uv(void);
uint16_t engine_out_code(void);
uint16_t engine_zero_code(void);                    /* scaling_v_to_code(0 V)  */
bool     engine_ramping(void);
int32_t  engine_ramp_target_uv(void);
/* |held level| that v_limit may not go below in ARMED. */
int32_t  engine_held_abs_uv(void);
/* SRC: validate against v_limit and the DAC range, then ramp at ramp_rate. */
err_t    engine_src(int32_t v_uv);
/* ARM: target := 0 V and load the zero code. False if SPI1 failed. */
bool     engine_arm_prepare(void);
/* DISARMING: ramp to 0 V at max(ramp_rate, 10 V/s); done() once the zero
 * code is loaded. */
void     engine_disarm_begin(void);
bool     engine_disarm_done(void);
/* FAULT entry (after dac_zero_safe): forget the output, abort the sweep with
 * status=fault, fail a running job. No DAC access. */
void     engine_fault_entry(fault_t f);
/* Compliance with on_compliance=stop: immediate zero code, no ramp. */
void     engine_zero_output_now(void);

/* ---- ADC, monitor and front-end check ----------------------------------------- */
typedef struct {
    bool     i_valid, v_valid;
    int32_t  i_code, v_code;
    uint64_t i_t_us, v_t_us;
} monitor_t;
typedef enum { AFE_UNKNOWN = 0, AFE_OK, AFE_FAIL } afe_t;

const monitor_t *engine_monitor(void);
afe_t            engine_afe(void);
uint64_t         engine_afe_time_us(void);
const char      *engine_afe_name(afe_t a);
uint64_t         engine_last_conversion_us(void);
bool             engine_post_passed(void);
/* ADC initialised and free of management jobs (ARM precondition `adc`). */
bool             engine_adc_ready(void);
/* Pure front-end check of one I/V pair taken with the gate open. */
bool             engine_afe_pair_ok(int32_t i_code, int32_t v_code);

/* ---- sweeps ------------------------------------------------------------------- */
typedef enum {
    SWS_NONE = 0, SWS_RUNNING, SWS_COMPLETE, SWS_ABORTED, SWS_COMPLIANCE,
    SWS_OVERFLOW, SWS_SESSION_LOST, SWS_HOST_TIMEOUT, SWS_FAULT,
} sweep_status_t;
const char *sweep_status_name(sweep_status_t s);

/* SWP.WP: on SWG_NOT_DIVISIBLE *suggest receives sweep_suggest_step(). */
swg_status_t engine_sweep_define_wp(const int32_t *wp_uv, uint8_t n_wp, int32_t step_uv,
                                    uint16_t cycles, uint32_t *points, int32_t *suggest);
void         engine_list_clear(void);
/* Appends; false (nothing appended) if the list would exceed SWEEP_LIST_MAX. */
bool         engine_list_add(const sweep_point_t *pts, uint32_t n, uint32_t *total);
uint32_t     engine_list_len(void);
swg_status_t engine_list_use(uint32_t *points);
sweep_kind_t engine_sweep_kind(void);
uint32_t     engine_sweep_points(void);
/* Estimated duration of the defined sweep from the present output. */
uint64_t     engine_sweep_estimate_us(void);
/* SWP.RUN: validate against v_limit and the calibrated DAC range. On E_OK
 * the sweep is started (the caller emits SWEEP_BEGIN and enters SWEEPING);
 * *why names a failure. */
err_t        engine_sweep_run(uint16_t *id, uint32_t *points, uint64_t *est_us,
                              const char **why);
/* End the running sweep (SWP.ABORT, session loss, host timeout). */
void         engine_sweep_abort(sweep_status_t why);
bool         engine_sweeping(void);
uint16_t     engine_sweep_id(void);
uint32_t     engine_sweep_done_points(void);
sweep_status_t engine_sweep_status(void);

/* ---- deferred jobs ------------------------------------------------------------ */
typedef enum {
    JOB_NONE = 0,
    JOB_POST,       /* boot POST: init at PGA 1, self-checks, PGA 2, AFE      */
    JOB_SELFTEST,   /* SELFTEST: the same, reports                            */
    JOB_SELFCAL,    /* ADC.SELFCAL                                            */
    JOB_RECONF,     /* data-rate or (bring-up) PGA change                     */
    JOB_MEAS,       /* MEAS?                                                   */
    JOB_ZERO,       /* ZERO                                                    */
    JOB_CALSAVE,    /* CAL.SAVE: waits for an idle ADC, then erases/programs   */
} job_kind_t;

typedef struct {
    job_kind_t  kind;
    err_t       err;          /* E_OK or the error to report                */
    const char *msg;          /* error message word(s)                       */
    /* MEAS / ZERO */
    uint16_t    n;
    int32_t     i_raw, v_raw;
    uint16_t    i_sd, v_sd;   /* 0.1 code units; 0xFFFF = n/a              */
    int64_t     v_nv, i_pa;
    uint16_t    flags;
    zero_t      zero;
    /* SELFTEST */
    int32_t     negref, zero_code;
    bool        afe_ok;
    /* CAL.SAVE */
    uint32_t    seq;
} job_result_t;

/* Start a job; arg = pairs for MEAS (0 = n_avg). E_BUSY if the ADC or the
 * engine is busy, E_RANGE if the duration would exceed 10 s. */
err_t engine_job_start(job_kind_t k, uint32_t arg);
/* CAL.SAVE as a job: no ADC conversion may be in flight while the sector
 * erase stalls the bus (it would time out), so the job waits for the ADC to
 * go idle, holds the monitor off, then saves. */
err_t engine_cal_save_start(uint32_t timestamp, const char *note);
/* Data rate / PGA change: starts JOB_RECONF (E_BUSY if not possible now). */
err_t engine_adc_reconfigure(uint32_t rate_x10, uint8_t pga);
bool  engine_job_active(void);
/* Collect the result of a finished job (true once per job). */
bool  engine_job_take(job_result_t *out);
void  engine_job_cancel(void);

/* MEAS? duration estimate for `pairs` at the active rate. */
uint64_t engine_meas_estimate_us(uint32_t pairs);
/* Pairs ZERO will use at the active rate (<= 64, within 10 s). */
uint16_t engine_zero_pairs(void);

/* ---- lifecycle and diagnostics -------------------------------------------------- */
void engine_init(void);
void engine_service(uint64_t now_us);

typedef struct {
    uint32_t points;            /* sweep records pushed           */
    uint32_t monitor_pairs;
    uint32_t compliance_trips;
    uint32_t late_points;
    uint32_t ramp_resyncs;
} engine_stats_t;
void engine_get_stats(engine_stats_t *out);
/* W_KELVIN / W_NO_OUTPUT from the current or last sweep. */
uint32_t engine_warn_bits(void);
/* Start time of the running/last sweep (64-bit, for SWEEP_BEGIN t0_us). */
uint64_t engine_sweep_t0_us(void);
/* Bring-up ADC.PGA: the PGA the ADC uses (ADS_PGA_x); 2 in production. */
uint8_t  engine_adc_pga(void);

#endif /* ENGINE_H */
