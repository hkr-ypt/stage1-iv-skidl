/*
 * model_board.c — analog behavioural model of the Stage 1 board (contract:
 * model_board.h). It exists to exercise the firmware's logic in unit tests
 * and in the whole-firmware simulator; it does NOT predict analog accuracy.
 *
 * Hardware facts used (blocks/<name>.py at 8a84b55, docs/firmware/
 * firmware_spec.md §4.4, §4.6, §7.7):
 *  - Heartbeat pump (blocks/mcu.py): PC5 -> R36 1k -> C47 100 nF -> D6 BAT54S
 *    -> DAC_ARM = C48 470 nF || R37 100 k (tau 47 ms) -> TMUX1101 U11 SEL.
 *    Modelled as a charge share per PC5 rising edge,
 *        v_arm += 0.25 * (2.75 V - v_arm),
 *    and an exponential decay, tau 47 ms with PC5 low/floating and 58 ms
 *    while PC5 is held high (C47 feed-through slows the bleed). Fitted to
 *    the pump simulation (scratch agent_pump, spec §4.4 table): steady state
 *    ~2.65 V at 2 kHz (sim 2.70), ~2.37 V at 500 Hz (sim 2.42), disarm to
 *    0.87 V 52.5 ms from PC5 low (sim 52-54) and 65 ms from PC5 high
 *    (sim 64-66). The model arms after 3 rising edges (the circuit
 *    simulation needs 4-5); single-edge peak 0.69 V < V_IL.
 *  - TMUX1101 (TI SCDS410D Table 8-1): ON for SEL >= V_IH 1.49 V, OFF for
 *    SEL <= V_IL 0.87 V; no hysteresis is specified, so the model holds its
 *    state in between (transitions are counted).
 *  - blocks/dac_sweep.py: V_SWEEP = 8.0321 * (V_A - V_B/2),
 *    V_x = VREF_DAC * code_x / 65536 (DAC8552, TI SLAS430A eq. 1), clamped to
 *    U3B's swing +-13.8 V. A powered-down DAC channel (dacm_effective() < 0,
 *    high impedance) is taken as 0 V.
 *  - Force path (blocks/output_buffer.py, protection.py, current_sense.py):
 *    U5 (OPA548) follower, current limit ~49 mA both polarities, then
 *    R_top = F1 + R30 + U12 to FORCE+; FORCE- -> R_bot = F2 + R10 (10.0 ohm
 *    shunt) to AGND. U12 opens when either +-15 V rail is missing.
 *  - Kelvin attenuators R12+R32 and R13+R33 (R_sd = 50.2 k to AGND) on
 *    SENSE+ / SENSE-; the SENSE+ current flows through R_top only, the
 *    SENSE- current bypasses R10 (spec §7.7).
 *  - U7 (INA128, G = 1): V_SENSE_OUT = VREF + 0.199203 * (V(S+) - V(S-));
 *    an open sense lead leaves its attenuator pulling the input to AGND.
 *  - U6 (INA128, G = 2.4006) on R10: I_SENSE_OUT = VREF + 24.0056 V/A * I.
 *  - With a +-15 V rail missing both INA128 outputs sit near AGND
 *    (~0.2 V): the AFE signature, both channels below -0.8 full scale.
 *  - ADS1256 inputs (blocks/adc.py): AIN0 = I_SENSE_OUT, AIN2 = V_SENSE_OUT,
 *    AIN1/AIN3 = 100 ohm to AGND, AINCOM = VREF_2V5.
 *
 * Operating point: bisection on the DUT voltage Vd = V_hi - V_lo. For a DUT
 * with monotonic I(Vd) the residual
 *     F(Vd) = I_F_needed(Vd) - I_source(Vd)
 * is monotonic, where I_F_needed = I_dut + V_hi/R_sd (SENSE+ connected) and
 * I_source = clamp((V_SWEEP - V_hi)/R_top, -i_lim, +i_lim), or 0 when the
 * force path is open. V_lo = I_dut * (R_bot || R_sd) (SENSE- connected) or
 * I_dut * R_bot, and I(R10) = V_lo / R_bot. The last solution is cached
 * (keyed on every input) because the ADC model asks for the inputs at every
 * conversion.
 */
#include "model_board.h"

#include <math.h>
#include <string.h>

/* ---- pump and gate ----------------------------------------------------------- */
#define PUMP_V_ASYMPTOTE   2.75     /* V, charge-share target                  */
#define PUMP_SHARE         0.25     /* fraction of the gap closed per edge     */
#define PUMP_TAU_LOW_US    47000.0  /* C48 * R37                               */
#define PUMP_TAU_HIGH_US   58000.0  /* PC5 held high: C47 feed-through         */
#define GATE_V_ON          1.49     /* TMUX1101 V_IH                           */
#define GATE_V_OFF         0.87     /* TMUX1101 V_IL                           */

/* ---- sweep and front end ---------------------------------------------------------- */
#define SWEEP_GAIN         8.0321
#define SWEEP_CLAMP_V      13.8
#define DAC_FULL_SCALE     65536.0
#define INA_V_GAIN         0.199203
#define INA_I_GAIN         24.0056  /* V/A: G 2.4006 x R10 10.00 ohm           */
#define INA_SWING_V        13.6     /* INA128 output swing on +-15 V           */
#define AFE_DEAD_OUT_V     0.2      /* INA outputs with a rail missing         */
#define DIODE_VT           0.025852 /* kT/q at 300 K                           */
#define R_MIN_OHM          1e-6
#define EXP_ARG_MAX        700.0

/* ---- defaults ---------------------------------------------------------------------- */
#define DEF_VREF           2.5
#define DEF_R_TOP          106.0
#define DEF_R_BOT          15.0
#define DEF_R_SD           50200.0
#define DEF_I_LIM          0.049
#define DEF_SWEEP_OFFSET   0.002
#define DEF_INA_V_OFFSET   0.0003
#define DEF_INA_I_OFFSET   0.0001
#define DEF_DUT_R          1000.0

static void gate_update(board_model_t *b)
{
    if (!b->gate_on && b->v_arm >= GATE_V_ON) {
        b->gate_on = true;
        b->gate_on_events++;
    } else if (b->gate_on && b->v_arm <= GATE_V_OFF) {
        b->gate_on = false;
        b->gate_off_events++;
    }
}

void board_init(board_model_t *b, dac_model_t *dac)
{
    memset(b, 0, sizeof(*b));
    b->vref = DEF_VREF;
    b->sweep_offset_v = DEF_SWEEP_OFFSET;
    b->r_top = DEF_R_TOP;
    b->r_bot = DEF_R_BOT;
    b->r_sd = DEF_R_SD;
    b->i_lim = DEF_I_LIM;
    b->ina_v_offset_v = DEF_INA_V_OFFSET;
    b->ina_i_offset_v = DEF_INA_I_OFFSET;
    b->rail_pos = true;
    b->rail_neg = true;
    b->sense_plus_open = false;
    b->sense_minus_open = false;
    b->force_open = false;
    b->gain_err_v = 0.0;
    b->gain_err_i = 0.0;
    b->dut.kind = DUT_RESISTOR;
    b->dut.r_ohm = DEF_DUT_R;
    b->v_arm = 0.0;
    b->gate_on = false;
    b->hb_level = false;
    b->t_last_us = 0;
    b->dac = dac;
}

void board_advance(board_model_t *b, uint64_t t_us)
{
    if (t_us <= b->t_last_us) {
        return;
    }
    double dt = (double)(t_us - b->t_last_us);
    double tau = b->hb_level ? PUMP_TAU_HIGH_US : PUMP_TAU_LOW_US;
    b->v_arm *= exp(-dt / tau);
    b->t_last_us = t_us;
    gate_update(b);
}

void board_hb(board_model_t *b, bool level, uint64_t t_us)
{
    board_advance(b, t_us);
    if (level && !b->hb_level) {
        b->v_arm += PUMP_SHARE * (PUMP_V_ASYMPTOTE - b->v_arm);
    }
    b->hb_level = level;
    gate_update(b);
}

double board_vref_dac(const board_model_t *b)
{
    return b->gate_on ? b->vref : 0.0;
}

static double dac_volts(const board_model_t *b, int ch, double vref_dac)
{
    if (b->dac == NULL) {
        return 0.0;
    }
    int32_t code = dacm_effective(b->dac, ch);
    if (code < 0) {
        return 0.0; /* high impedance: taken as 0 V */
    }
    return vref_dac * (double)code / DAC_FULL_SCALE;
}

double board_vsweep(const board_model_t *b)
{
    double vr = board_vref_dac(b);
    double va = dac_volts(b, 0, vr);
    double vb = dac_volts(b, 1, vr);
    double v = SWEEP_GAIN * (va - vb / 2.0) + b->sweep_offset_v;
    if (v > SWEEP_CLAMP_V) {
        v = SWEEP_CLAMP_V;
    } else if (v < -SWEEP_CLAMP_V) {
        v = -SWEEP_CLAMP_V;
    }
    return v;
}

/* ---- DUT models ------------------------------------------------------------------- */

static double diode_current(const dut_t *d, double vd)
{
    double is = d->is_a > 0.0 ? d->is_a : 1e-14;
    double nvt = (d->n > 0.0 ? d->n : 1.0) * DIODE_VT;
    if (d->rs_ohm <= 0.0) {
        double x = vd / nvt;
        if (x > EXP_ARG_MAX) {
            x = EXP_ARG_MAX;
        }
        return is * expm1(x);
    }
    /* Junction voltage vj in [min(vd,0), max(vd,0)]:
     * h(vj) = vj + rs * is * (exp(vj/nvt) - 1) - vd is increasing. */
    double lo = vd < 0.0 ? vd : 0.0;
    double hi = vd < 0.0 ? 0.0 : vd;
    for (int i = 0; i < 200 && (hi - lo) > 1e-13; i++) {
        double mid = 0.5 * (lo + hi);
        double x = mid / nvt;
        if (x > EXP_ARG_MAX) {
            x = EXP_ARG_MAX;
        }
        double h = mid + d->rs_ohm * is * expm1(x) - vd;
        if (h > 0.0) {
            hi = mid;
        } else {
            lo = mid;
        }
    }
    double vj = 0.5 * (lo + hi);
    return (vd - vj) / d->rs_ohm;
}

static double dut_current(const dut_t *d, double vd)
{
    switch (d->kind) {
    case DUT_RESISTOR:
        return vd / (d->r_ohm > R_MIN_OHM ? d->r_ohm : R_MIN_OHM);
    case DUT_DIODE:
        return diode_current(d, vd);
    case DUT_CELL:
        return (vd - d->v_cell) / (d->r_ohm > R_MIN_OHM ? d->r_ohm : R_MIN_OHM);
    case DUT_OPEN:
    case DUT_CAPACITOR:
    default:
        return 0.0;
    }
}

/* ---- operating point ---------------------------------------------------------------- */

typedef struct {
    double vsweep, r_top, r_bot, r_sd, i_lim;
    double r_ohm, is_a, n, rs_ohm, v_cell;
    int32_t kind;
    int32_t force_path, sense_plus, sense_minus;
} solve_key_t;

typedef struct {
    double v_hi, v_lo, i_dut, i_shunt;
    bool in_limit;
} solve_out_t;

typedef struct {
    double v_hi, v_lo, i_dut, i_shunt, i_src_raw;
    double f;
} solve_eval_t;

static void eval_at(const solve_key_t *k, const dut_t *d, double vd, solve_eval_t *e)
{
    double r_bot_eff = k->sense_minus ? (k->r_bot * k->r_sd) / (k->r_bot + k->r_sd) : k->r_bot;
    e->i_dut = dut_current(d, vd);
    e->v_lo = e->i_dut * r_bot_eff;
    e->i_shunt = e->v_lo / k->r_bot;
    e->v_hi = vd + e->v_lo;
    double i_needed = e->i_dut + (k->sense_plus ? e->v_hi / k->r_sd : 0.0);
    double i_src = 0.0;
    e->i_src_raw = 0.0;
    if (k->force_path) {
        e->i_src_raw = (k->vsweep - e->v_hi) / k->r_top;
        i_src = e->i_src_raw;
        if (i_src > k->i_lim) {
            i_src = k->i_lim;
        } else if (i_src < -k->i_lim) {
            i_src = -k->i_lim;
        }
    }
    e->f = i_needed - i_src;
}

static void solve_uncached(const solve_key_t *k, const dut_t *d, solve_out_t *out)
{
    double bound = fabs(k->vsweep) + fabs(k->v_cell) + 1.0;
    double lo = -bound;
    double hi = bound;
    solve_eval_t e;
    double vd = 0.0;
    for (int i = 0; i < 300; i++) {
        vd = 0.5 * (lo + hi);
        eval_at(k, d, vd, &e);
        if (e.f == 0.0 || (hi - lo) < 1e-12) {
            break;
        }
        if (e.f > 0.0) {
            hi = vd;
        } else {
            lo = vd;
        }
    }
    eval_at(k, d, vd, &e);
    out->v_hi = e.v_hi;
    out->v_lo = e.v_lo;
    out->i_dut = e.i_dut;
    out->i_shunt = e.i_shunt;
    out->in_limit = k->force_path && fabs(e.i_src_raw) > k->i_lim;
}

static void make_key(const board_model_t *b, solve_key_t *k)
{
    memset(k, 0, sizeof(*k));
    k->vsweep = board_vsweep(b);
    k->r_top = b->r_top > R_MIN_OHM ? b->r_top : R_MIN_OHM;
    k->r_bot = b->r_bot > R_MIN_OHM ? b->r_bot : R_MIN_OHM;
    k->r_sd = b->r_sd > R_MIN_OHM ? b->r_sd : R_MIN_OHM;
    k->i_lim = b->i_lim > 0.0 ? b->i_lim : 0.0;
    k->kind = (int32_t)b->dut.kind;
    k->r_ohm = b->dut.r_ohm;
    k->is_a = b->dut.is_a;
    k->n = b->dut.n;
    k->rs_ohm = b->dut.rs_ohm;
    k->v_cell = (b->dut.kind == DUT_CELL) ? b->dut.v_cell : 0.0;
    k->force_path = (b->rail_pos && b->rail_neg && !b->force_open) ? 1 : 0;
    k->sense_plus = b->sense_plus_open ? 0 : 1;
    k->sense_minus = b->sense_minus_open ? 0 : 1;
}

/* One-entry cache: the simulator has one board, tests rarely alternate. */
static bool        s_cache_valid;
static solve_key_t s_cache_key;
static solve_out_t s_cache_out;

void board_solve(const board_model_t *b, double *v_hi, double *v_lo,
                 double *i_dut, double *i_shunt, bool *in_limit)
{
    solve_key_t k;
    make_key(b, &k);
    if (!s_cache_valid || memcmp(&k, &s_cache_key, sizeof(k)) != 0) {
        dut_t d = b->dut;
        solve_uncached(&k, &d, &s_cache_out);
        s_cache_key = k;
        s_cache_valid = true;
    }
    if (v_hi) {
        *v_hi = s_cache_out.v_hi;
    }
    if (v_lo) {
        *v_lo = s_cache_out.v_lo;
    }
    if (i_dut) {
        *i_dut = s_cache_out.i_dut;
    }
    if (i_shunt) {
        *i_shunt = s_cache_out.i_shunt;
    }
    if (in_limit) {
        *in_limit = s_cache_out.in_limit;
    }
}

static double ina_clamp(double v)
{
    if (v > INA_SWING_V) {
        return INA_SWING_V;
    }
    if (v < -INA_SWING_V) {
        return -INA_SWING_V;
    }
    return v;
}

double board_ain(void *ctx, int ain, uint64_t t_us)
{
    board_model_t *b = (board_model_t *)ctx;
    board_advance(b, t_us);
    if (ain == 8) {
        return b->vref;
    }
    if (ain != 0 && ain != 2) {
        return 0.0; /* AIN1/AIN3: 100 ohm to AGND; AIN4..7 unused */
    }
    if (!b->rail_pos || !b->rail_neg) {
        return AFE_DEAD_OUT_V;
    }
    double v_hi, v_lo, i_dut, i_shunt;
    bool lim;
    board_solve(b, &v_hi, &v_lo, &i_dut, &i_shunt, &lim);
    if (ain == 0) {
        return ina_clamp(b->vref + INA_I_GAIN * i_shunt * (1.0 + b->gain_err_i)
                         + b->ina_i_offset_v);
    }
    double vsp = b->sense_plus_open ? 0.0 : v_hi;
    double vsm = b->sense_minus_open ? 0.0 : v_lo;
    return ina_clamp(b->vref + INA_V_GAIN * (vsp - vsm) * (1.0 + b->gain_err_v)
                     + b->ina_v_offset_v);
}
