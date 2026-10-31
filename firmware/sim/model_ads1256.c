/*
 * model_ads1256.c — register- and timing-level behavioural model of the
 * ADS1256 (TI SBAS288K, Rev. K) as wired on this board (f_CLKIN 7.68 MHz,
 * VREF 2.500 V). Contract: model_ads1256.h.
 *
 * Datasheet facts used (SBAS288K page / table):
 *  - tau = 1/f_CLKIN = 130.2 ns. Timing (p.6-7, Fig. 1-4): t1 SCLK period
 *    >= 4 tau; t6 >= 50 tau from the last SCLK of RDATA/RREG (incl. the RREG
 *    count byte) to the first data SCLK; t10 /CS low >= 8 tau after the final
 *    SCLK; t11 >= 4 tau after RREG/WREG/RDATA, >= 24 tau after SYNC (and
 *    RDATAC), "wait for DRDY" after RESET/STANDBY/calibration commands;
 *    t16 RESET pulse >= 4 tau; t17 = 16 tau update window.
 *    WAKEUP has no t11 in the table; the firmware convention (spec §4.5) is
 *    24 tau, which the model enforces.
 *  - Data rates and DRATE codes (p.31), settling t18 (Table 13, p.20),
 *    self-calibration time (Table 21, p.25; PGA-dependent above 3750 SPS).
 *  - Mux cycling WREG MUX -> SYNC -> WAKEUP (p.21, Fig. 19); synchronisation
 *    happens on the first CLKIN edge after the first SCLK of WAKEUP (p.27,
 *    p.37 Fig. 36); first data t18 later, then continuous conversion at 1/DR
 *    (Table 15, p.22). A mux change without SYNC gives a mixed result
 *    (Fig. 21) — modelled as the mean of the two channels.
 *  - DRDY (p.27, Fig. 24): low when new data is available; high during the
 *    t17 update; high after all 24 bits were read until the next update.
 *    RDATA returns the latest completed conversion (p.34). Per Fig. 19 the old
 *    result stays readable across SYNC/WAKEUP (the model does not force DRDY
 *    high at WAKEUP; it goes high only for the update window of the first new
 *    conversion) — the adversarial reading for a driver that polls the level.
 *  - Data format (p.23, Table 16): 24-bit two's complement,
 *    code = (V(AINP) - V(AINN)) * PGA / (2 VREF) * (2^23 - 1), clipping at
 *    0x7FFFFF / 0x800000.
 *  - Registers (Table 23, p.30-33): STATUS x1h (ID read-only, DRDY bit 0
 *    mirrors the pin), MUX 01h, ADCON 20h (bit 7 reads 0), DRATE F0h, IO E0h
 *    (DIO bits read the pin state: outputs read the written value, inputs and
 *    D0 while CLKOUT runs read 0 here because D0-D3 are unconnected).
 *    OFC/FSC are stored but do not affect the result.
 *  - Commands (Table 24, p.34-37): WREG/RREG = cmd|reg, then count-1; the
 *    register address wraps after 0Ah. RESET command keeps CLK1:CLK0, the
 *    RESET pin and power-up restore them (p.27, p.31). Every reset is
 *    followed by a self-calibration (p.27): reset defaults 30 kSPS, PGA 1 ->
 *    596 us (Table 21). Power-up adds 30 ms crystal start (p.27, "typically
 *    requires 30ms"). ACAL (p.30): WREG changing PGA, DR or BUFEN starts a
 *    self-calibration.
 *  - ORDER = 1 sends conversion data LSB first within each byte (p.30).
 *  - After a calibration completes, DRDY falls with settled data and the part
 *    keeps converting at 1/DR (also after SYNC + SELFCAL).
 *
 * Not modelled: RDATAC (counted as unknown_cmd so a test catches its use),
 * the SCLK reset pattern and the 32-DRDY-period SCLK timeout, BUFEN/SDCS
 * analog effects, SYSOCAL/SYSGCAL/SELFOCAL/SELFGCAL (run as a calibration of
 * the Table 21 duration, an upper bound), invalid DRATE codes (treated as
 * 30 kSPS). Times are double microseconds internally; edges are reported in
 * whole microseconds (floor, an ideal TIM2 capture).
 */
#include "model_ads1256.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#define TAU_US          (1.0 / 7.68)          /* 130.2 ns                         */
#define T1_MIN_US       (4.0 * TAU_US)        /* SCLK period                      */
#define T6_MIN_US       (50.0 * TAU_US)       /* 6.51 us                          */
#define T10_MIN_US      (8.0 * TAU_US)        /* 1.04 us                          */
#define T11_SHORT_US    (4.0 * TAU_US)        /* after WREG/RREG/RDATA            */
#define T11_LONG_US     (24.0 * TAU_US)       /* after SYNC, WAKEUP: 3.13 us      */
#define T16_MIN_US      (4.0 * TAU_US)        /* RESET pin pulse                  */
#define T17_US          (16.0 * TAU_US)       /* 2.08 us update window            */
#define ONESHOT_EXTRA_US (256.0 * TAU_US)     /* wake from STANDBY (p.22)         */
#define XTAL_START_US   30000.0               /* p.27                             */
#define VREF_V          2.5
#define FS_CODE         8388607.0             /* 2^23 - 1                         */

#define REG_COUNT 11

typedef struct {
    uint8_t code;
    double  sps;
    double  t18_us;       /* Table 13                               */
    double  cal_us[5];    /* Table 21: PGA 1, 2, 4, 8, 16/32/64     */
} rate_row_t;

static const rate_row_t k_rates[] = {
    { 0xF0, 30000.0,    210.0, {  596.0,  596.0,  692.0,  696.0,  892.0 } },
    { 0xE0, 15000.0,    250.0, {  696.0,  696.0,  696.0,  762.0,  896.0 } },
    { 0xD0,  7500.0,    310.0, {  896.0,  896.0,  896.0,  896.0, 1029.0 } },
    { 0xC0,  3750.0,    440.0, { 1300.0, 1300.0, 1300.0, 1300.0, 1300.0 } },
    { 0xB0,  2000.0,    680.0, { 2000.0, 2000.0, 2000.0, 2000.0, 2000.0 } },
    { 0xA1,  1000.0,   1180.0, { 3600.0, 3600.0, 3600.0, 3600.0, 3600.0 } },
    { 0x92,   500.0,   2180.0, { 6600.0, 6600.0, 6600.0, 6600.0, 6600.0 } },
    { 0x82,   100.0,  10180.0, { 31200.0, 31200.0, 31200.0, 31200.0, 31200.0 } },
    { 0x72,    60.0,  16840.0, { 50900.0, 50900.0, 50900.0, 50900.0, 50900.0 } },
    { 0x63,    50.0,  20180.0, { 61800.0, 61800.0, 61800.0, 61800.0, 61800.0 } },
    { 0x53,    30.0,  33510.0, { 101300.0, 101300.0, 101300.0, 101300.0, 101300.0 } },
    { 0x43,    25.0,  40180.0, { 123200.0, 123200.0, 123200.0, 123200.0, 123200.0 } },
    { 0x33,    15.0,  66840.0, { 202100.0, 202100.0, 202100.0, 202100.0, 202100.0 } },
    { 0x23,    10.0, 100180.0, { 307200.0, 307200.0, 307200.0, 307200.0, 307200.0 } },
    { 0x13,     5.0, 200180.0, { 613800.0, 613800.0, 613800.0, 613800.0, 613800.0 } },
    { 0x03,     2.5, 400180.0, { 1227200.0, 1227200.0, 1227200.0, 1227200.0, 1227200.0 } },
};

typedef enum {
    P_CMD = 0,      /* expecting a command byte          */
    P_WREG_N,       /* WREG second byte                  */
    P_WREG_DATA,
    P_RREG_N,       /* RREG second byte                  */
    P_RREG_DATA,
    P_RDATA_DATA,
} parse_t;

struct adsm {
    adsm_input_fn fn;
    void         *ctx;

    /* registers (STATUS without the DRDY bit; IO as written) */
    uint8_t status, mux, adcon, drate, io_w;
    uint8_t calreg[6];

    /* power, reset, calibration */
    bool    powered;
    double  osc_ready;        /* crystal running from this time */
    bool    reset_low;
    double  reset_low_t;
    bool    busy;
    double  busy_end;

    /* conversion engine */
    double  now;
    bool    running;
    bool    standby;
    double  next_update;
    uint8_t mux_at_start;
    bool    data_valid;
    bool    data_read;
    int32_t data;

    /* /DRDY falling-edge report */
    bool    edge_pending;
    double  edge_t;

    /* serial interface */
    bool    cs;
    parse_t ps;
    uint8_t reg_ptr;
    uint8_t left;
    uint32_t frame_bytes;
    double  last_byte_end;
    bool    have_prev_cmd;
    double  cmd_end_t;
    double  t11_need;
    double  data_cmd_end;     /* t6 reference */
    uint8_t rd_idx;
    bool    rd_bad;
    uint8_t rd_buf[3];
    uint8_t wr_old_pga, wr_old_drate, wr_old_bufen;

    /* faults */
    adsm_stuck_t stuck;
    bool    drdy_dead;
    double  noise_sigma;
    uint64_t rng;
    bool    gauss_have;
    double  gauss_next;

    /* observation */
    adsm_violations_t v;
    uint32_t conversions;
    uint32_t selfcals;
};

/* ---- helpers ------------------------------------------------------------------ */

static const rate_row_t *rate_row(uint8_t drate)
{
    for (size_t i = 0; i < sizeof k_rates / sizeof k_rates[0]; i++) {
        if (k_rates[i].code == drate) {
            return &k_rates[i];
        }
    }
    return &k_rates[0];   /* invalid code: "unpredictable" — treated as 30 kSPS */
}

static double period_us(const adsm_t *m)
{
    return 1.0e6 / rate_row(m->drate)->sps;
}

static int pga_index(uint8_t adcon)
{
    int p = adcon & 7;
    return p >= 4 ? 4 : p;   /* Table 21 columns: 1, 2, 4, 8, 16-64 */
}

static double pga_gain(uint8_t adcon)
{
    static const double g[8] = { 1, 2, 4, 8, 16, 32, 64, 64 };
    return g[adcon & 7];
}

static double cal_time_us(const adsm_t *m)
{
    return rate_row(m->drate)->cal_us[pga_index(m->adcon)];
}

static uint64_t rng_next(adsm_t *m)
{
    /* splitmix64 */
    uint64_t z = (m->rng += 0x9E3779B97F4A7C15ull);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
}

static double rng_uniform(adsm_t *m)
{
    /* (0, 1] */
    return ((double)(rng_next(m) >> 11) + 1.0) / 9007199254740992.0;
}

static double gauss(adsm_t *m)
{
    if (m->gauss_have) {
        m->gauss_have = false;
        return m->gauss_next;
    }
    double u1 = rng_uniform(m);
    double u2 = rng_uniform(m);
    double r = sqrt(-2.0 * log(u1));
    double a = 6.283185307179586 * u2;
    m->gauss_next = r * sin(a);
    m->gauss_have = true;
    return r * cos(a);
}

static double ain(adsm_t *m, int sel, double t)
{
    int idx = (sel & 0x8) ? 8 : (sel & 0x7);
    if (!m->fn) {
        return 0.0;
    }
    return m->fn(m->ctx, idx, (uint64_t)(t < 0.0 ? 0.0 : t));
}

static double channel_codes(adsm_t *m, uint8_t mux, double t)
{
    double vdiff = ain(m, mux >> 4, t) - ain(m, mux & 0x0F, t);
    return vdiff * pga_gain(m->adcon) / (2.0 * VREF_V) * FS_CODE;
}

static int32_t to_code(adsm_t *m, double x)
{
    if (m->noise_sigma > 0.0) {
        x += m->noise_sigma * gauss(m);
    }
    x = floor(x + 0.5);
    if (x > FS_CODE) {
        return 0x7FFFFF;
    }
    if (x < -FS_CODE - 1.0) {
        return -0x800000;
    }
    return (int32_t)x;
}

static void record_edge(adsm_t *m, double t)
{
    if (m->drdy_dead || m->reset_low) {
        return;
    }
    if (!m->edge_pending) {
        m->edge_pending = true;
        m->edge_t = t;
    }
}

static void latch_conversion(adsm_t *m, double t)
{
    uint8_t mux_end = m->mux;
    double x = channel_codes(m, mux_end, t);
    if (m->mux_at_start != mux_end) {
        x = 0.5 * (x + channel_codes(m, m->mux_at_start, t));   /* Fig. 21 mix */
    }
    m->data = to_code(m, x);
    m->data_valid = true;
    m->data_read = false;
    m->mux_at_start = mux_end;
    record_edge(m, t);
}

static void start_cal(adsm_t *m, double t_begin, double dur)
{
    m->busy = true;
    m->busy_end = t_begin + dur;
    m->running = false;
    m->standby = false;
}

static void cal_complete(adsm_t *m, double t)
{
    m->busy = false;
    /* settled data ready (filter restarted on the current mux), then
     * continuous conversion */
    m->mux_at_start = m->mux;
    latch_conversion(m, t);
    m->running = true;
    m->next_update = t + period_us(m);
}

static void conversion_update(adsm_t *m, double t)
{
    latch_conversion(m, t);
    m->conversions++;
    m->next_update = t + period_us(m);
}

static void advance_to(adsm_t *m, double t)
{
    if (!(t > m->now)) {
        return;
    }
    for (;;) {
        if (m->busy && m->busy_end <= t) {
            m->now = m->busy_end;
            cal_complete(m, m->busy_end);
            continue;
        }
        if (!m->busy && m->running && m->next_update <= t) {
            m->now = m->next_update;
            conversion_update(m, m->next_update);
            continue;
        }
        break;
    }
    m->now = t;
}

/* Internal DRDY level at m->now (true = high). */
static bool drdy_high(const adsm_t *m)
{
    if (!m->powered || m->reset_low || m->busy) {
        return true;
    }
    if (!m->data_valid || m->data_read) {
        return true;
    }
    if (m->running && m->now >= m->next_update - T17_US) {
        return true;   /* t17 update window */
    }
    return false;
}

static void do_reset(adsm_t *m, bool keep_clk)
{
    uint8_t clk = (uint8_t)(m->adcon & 0x60u);
    m->status = 0x30;                       /* ID 3, ORDER 0, ACAL 0, BUFEN 0 */
    m->mux = 0x01;
    m->adcon = keep_clk ? clk : 0x20;
    m->drate = 0xF0;
    m->io_w = 0xE0;
    m->running = false;
    m->standby = false;
    m->busy = false;
    m->data_valid = false;
    m->data_read = false;
    m->mux_at_start = m->mux;
    m->ps = P_CMD;
}

static void reset_calregs(adsm_t *m)
{
    /* plausible post-calibration values; they do not affect the result */
    static const uint8_t init[6] = { 0x3A, 0x00, 0x00, 0x9C, 0x5D, 0x45 };
    memcpy(m->calreg, init, sizeof init);
}

static void auto_cal(adsm_t *m, double t_begin)
{
    reset_calregs(m);
    start_cal(m, t_begin, cal_time_us(m));
    m->selfcals++;
}

static uint8_t io_read(const adsm_t *m)
{
    uint8_t dir = (uint8_t)(m->io_w & 0xF0u);
    uint8_t dio = 0;
    for (unsigned i = 0; i < 4; i++) {
        bool output = (dir & (0x10u << i)) == 0;
        if (!output) {
            continue;                        /* unconnected input reads 0 */
        }
        if (i == 0 && (m->adcon & 0x60u) != 0) {
            continue;                        /* D0 is CLKOUT */
        }
        dio = (uint8_t)(dio | (m->io_w & (1u << i)));
    }
    return (uint8_t)(dir | dio);
}

static uint8_t read_reg(const adsm_t *m, uint8_t r)
{
    switch (r) {
    case 0: return (uint8_t)(m->status | (drdy_high(m) ? 1u : 0u));
    case 1: return m->mux;
    case 2: return m->adcon;
    case 3: return m->drate;
    case 4: return io_read(m);
    default: return m->calreg[r - 5];
    }
}

static void write_reg(adsm_t *m, uint8_t r, uint8_t v)
{
    switch (r) {
    case 0: m->status = (uint8_t)(0x30u | (v & 0x0Eu)); break;
    case 1: m->mux = v; break;
    case 2: m->adcon = (uint8_t)(v & 0x7Fu); break;
    case 3: m->drate = v; break;
    case 4: m->io_w = v; break;
    default: m->calreg[r - 5] = v; break;
    }
}

static uint8_t bitrev(uint8_t b)
{
    uint8_t r = 0;
    for (unsigned i = 0; i < 8u; i++) {
        r = (uint8_t)(((unsigned)r << 1) | (((unsigned)b >> i) & 1u));
    }
    return r;
}

static void end_command(adsm_t *m, double t_end, double t11)
{
    m->ps = P_CMD;
    m->have_prev_cmd = true;
    m->cmd_end_t = t_end;
    m->t11_need = t11;
}

static bool device_busy(const adsm_t *m)
{
    return !m->powered || m->reset_low || m->busy;
}

/* ---- command byte -------------------------------------------------------------- */

static uint8_t command_byte(adsm_t *m, uint8_t b, double ts, double te)
{
    if (m->have_prev_cmd && ts - m->cmd_end_t < m->t11_need - 1e-9) {
        m->v.t11++;
    }
    if (device_busy(m)) {
        m->v.busy++;          /* ignored: calibration / reset in progress */
        m->have_prev_cmd = true;
        m->cmd_end_t = te;
        m->t11_need = 0.0;
        return 0xFF;
    }
    if (b == 0x00 || b == 0xFF) {                         /* WAKEUP */
        if (!m->running) {
            double first = rate_row(m->drate)->t18_us + (m->standby ? ONESHOT_EXTRA_US : 0.0);
            m->running = true;
            m->standby = false;
            m->mux_at_start = m->mux;     /* filter restarts on the current mux */
            m->next_update = ts + TAU_US + first;
        }
        advance_to(m, te);
        end_command(m, te, T11_LONG_US);
    } else if (b == 0x01) {                               /* RDATA */
        advance_to(m, te);
        m->ps = P_RDATA_DATA;
        m->data_cmd_end = te;
        m->rd_idx = 0;
        m->rd_bad = false;
    } else if ((b & 0xF0u) == 0x10u) {                    /* RREG */
        advance_to(m, te);
        m->reg_ptr = (uint8_t)((b & 0x0Fu) % REG_COUNT);
        m->ps = P_RREG_N;
    } else if ((b & 0xF0u) == 0x50u) {                    /* WREG */
        advance_to(m, te);
        m->reg_ptr = (uint8_t)((b & 0x0Fu) % REG_COUNT);
        m->ps = P_WREG_N;
    } else if (b == 0xF0) {                               /* SELFCAL */
        advance_to(m, te);
        reset_calregs(m);
        start_cal(m, te, cal_time_us(m));
        m->selfcals++;
        end_command(m, te, T11_SHORT_US);
    } else if (b >= 0xF1 && b <= 0xF4) {                  /* other calibrations */
        advance_to(m, te);
        start_cal(m, te, cal_time_us(m));
        end_command(m, te, T11_SHORT_US);
    } else if (b == 0xFC) {                               /* SYNC */
        advance_to(m, te);
        m->running = false;
        end_command(m, te, T11_LONG_US);
    } else if (b == 0xFD) {                               /* STANDBY */
        advance_to(m, te);
        m->running = false;
        m->standby = true;
        end_command(m, te, T11_SHORT_US);
    } else if (b == 0xFE) {                               /* RESET command */
        advance_to(m, te);
        do_reset(m, true);
        auto_cal(m, te);
        end_command(m, te, T11_SHORT_US);
    } else if (b == 0x0F) {                               /* SDATAC: no-op */
        advance_to(m, te);
        end_command(m, te, T11_SHORT_US);
    } else {                                              /* RDATAC and undefined */
        m->v.unknown_cmd++;
        advance_to(m, te);
        end_command(m, te, T11_LONG_US);
    }
    return 0xFF;   /* DOUT high impedance during command bytes */
}

/* ---- public API ----------------------------------------------------------------- */

adsm_t *adsm_new(adsm_input_fn fn, void *ctx)
{
    adsm_t *m = calloc(1, sizeof *m);
    if (!m) {
        return NULL;
    }
    m->fn = fn;
    m->ctx = ctx;
    m->rng = 1;
    do_reset(m, false);
    reset_calregs(m);
    return m;
}

void adsm_free(adsm_t *m)
{
    free(m);
}

void adsm_power_on(adsm_t *m, uint64_t now_us)
{
    double t = (double)now_us;
    if (t > m->now) {
        m->now = t;
    }
    m->powered = true;
    m->reset_low = false;
    m->cs = false;
    m->have_prev_cmd = false;
    m->edge_pending = false;
    m->osc_ready = t + XTAL_START_US;
    do_reset(m, false);
    auto_cal(m, m->osc_ready);
}

void adsm_reset_pin(adsm_t *m, bool high, uint64_t now_us)
{
    double t = (double)now_us;
    advance_to(m, t);
    if (!high && !m->reset_low) {
        m->reset_low = true;
        m->reset_low_t = t;
    } else if (high && m->reset_low) {
        m->reset_low = false;
        if (m->powered && t - m->reset_low_t >= T16_MIN_US) {
            /* before the crystal runs, the calibration waits for it */
            double begin = t > m->osc_ready ? t : m->osc_ready;
            do_reset(m, false);
            auto_cal(m, begin);
        }
    }
}

void adsm_cs(adsm_t *m, bool asserted, uint64_t now_us)
{
    double t = (double)now_us;
    advance_to(m, t);
    if (asserted && !m->cs) {
        m->cs = true;
        m->ps = P_CMD;
        m->frame_bytes = 0;
    } else if (!asserted && m->cs) {
        m->cs = false;
        if (m->frame_bytes > 0 && t - m->last_byte_end < T10_MIN_US - 1e-9) {
            m->v.t10++;
        }
        if (m->ps != P_CMD) {
            m->v.short_read++;            /* command sequence cut short by /CS */
            end_command(m, m->last_byte_end, T11_SHORT_US);
        }
        m->ps = P_CMD;
    }
}

uint8_t adsm_byte(adsm_t *m, uint8_t mosi, double t_start_us, double t_end_us)
{
    double ts = t_start_us, te = t_end_us;
    uint8_t miso = 0xFF;

    if (!(te > ts) || (te - ts) / 7.5 < T1_MIN_US - 1e-9) {
        m->v.sclk++;
    }
    advance_to(m, ts);
    if (!m->cs || !m->powered) {
        advance_to(m, te);
        return 0xFF;   /* not selected: DOUT high impedance (pulled up) */
    }

    switch (m->ps) {
    case P_CMD:
        miso = command_byte(m, mosi, ts, te);
        break;
    case P_WREG_N:
    case P_RREG_N: {
        if (mosi & 0xF0u) {
            m->v.unknown_cmd++;
        }
        m->left = (uint8_t)((mosi & 0x0Fu) + 1u);
        advance_to(m, te);
        if (m->ps == P_WREG_N) {
            m->wr_old_pga = (uint8_t)(m->adcon & 7u);
            m->wr_old_drate = m->drate;
            m->wr_old_bufen = (uint8_t)(m->status & 0x02u);
            m->ps = P_WREG_DATA;
        } else {
            m->ps = P_RREG_DATA;
            m->data_cmd_end = te;
            m->rd_idx = 0;
        }
        break;
    }
    case P_WREG_DATA:
        advance_to(m, te);
        write_reg(m, m->reg_ptr, mosi);
        m->reg_ptr = (uint8_t)((m->reg_ptr + 1u) % REG_COUNT);
        if (--m->left == 0) {
            end_command(m, te, T11_SHORT_US);
            bool changed = (m->adcon & 7u) != m->wr_old_pga || m->drate != m->wr_old_drate ||
                           (m->status & 0x02u) != m->wr_old_bufen;
            if ((m->status & 0x04u) && changed) {   /* ACAL */
                reset_calregs(m);
                start_cal(m, te, cal_time_us(m));
                m->selfcals++;
            }
        }
        break;
    case P_RREG_DATA:
        if (m->rd_idx == 0 && ts - m->data_cmd_end < T6_MIN_US - 1e-9) {
            m->v.t6++;
        }
        m->rd_idx++;
        miso = read_reg(m, m->reg_ptr);
        m->reg_ptr = (uint8_t)((m->reg_ptr + 1u) % REG_COUNT);
        advance_to(m, te);
        if (--m->left == 0) {
            end_command(m, te, T11_SHORT_US);
        }
        break;
    case P_RDATA_DATA:
        if (m->rd_idx == 0) {
            if (ts - m->data_cmd_end < T6_MIN_US - 1e-9) {
                m->v.t6++;
            }
            if (drdy_high(m)) {
                m->rd_bad = true;     /* no new data or inside t17 */
            }
            uint32_t raw = (uint32_t)m->data & 0xFFFFFFu;
            if (!m->data_valid) {
                raw = 0;
            }
            m->rd_buf[0] = (uint8_t)(raw >> 16);
            m->rd_buf[1] = (uint8_t)(raw >> 8);
            m->rd_buf[2] = (uint8_t)raw;
        }
        /* the update window must not start before this byte has been shifted */
        if (!m->busy && m->running && m->next_update - T17_US <= te) {
            m->rd_bad = true;
        }
        miso = m->rd_buf[m->rd_idx];
        if (m->status & 0x08u) {
            miso = bitrev(miso);
        }
        m->rd_idx++;
        advance_to(m, te);
        if (m->rd_idx == 3) {
            if (m->rd_bad) {
                m->v.read_not_ready++;
            }
            m->data_read = true;          /* DRDY high until the next update */
            end_command(m, te, T11_SHORT_US);
        }
        break;
    }
    m->frame_bytes++;
    m->last_byte_end = te;

    if (m->stuck == ADSM_STUCK_LOW) {
        miso = 0x00;
    } else if (m->stuck == ADSM_STUCK_HIGH) {
        miso = 0xFF;
    }
    return miso;
}

void adsm_advance(adsm_t *m, uint64_t now_us)
{
    advance_to(m, (double)now_us);
}

bool adsm_drdy_low(const adsm_t *m)
{
    return !m->drdy_dead && !drdy_high(m);
}

bool adsm_take_drdy_fall(adsm_t *m, uint64_t *t_us)
{
    if (!m->edge_pending) {
        return false;
    }
    m->edge_pending = false;
    if (t_us) {
        *t_us = (uint64_t)floor(m->edge_t);
    }
    return true;
}

void adsm_get_violations(const adsm_t *m, adsm_violations_t *out)
{
    *out = m->v;
}

void adsm_clear_violations(adsm_t *m)
{
    memset(&m->v, 0, sizeof m->v);
}

void adsm_get_regs(const adsm_t *m, uint8_t regs[11])
{
    for (uint8_t r = 0; r < REG_COUNT; r++) {
        regs[r] = read_reg(m, r);
    }
}

void adsm_set_noise(adsm_t *m, double sigma_codes, uint32_t seed)
{
    m->noise_sigma = sigma_codes;
    m->rng = ((uint64_t)seed << 1) | 1u;
    m->gauss_have = false;
}

void adsm_set_stuck(adsm_t *m, adsm_stuck_t s)
{
    m->stuck = s;
}

void adsm_set_drdy_dead(adsm_t *m, bool dead)
{
    m->drdy_dead = dead;
    if (dead) {
        m->edge_pending = false;
    }
}

void adsm_spontaneous_reset(adsm_t *m, uint64_t now_us)
{
    double t = (double)now_us;
    advance_to(m, t);
    do_reset(m, false);       /* brown-out: like the pin, CLK bits restored */
    auto_cal(m, t);
}

uint32_t adsm_conversions(const adsm_t *m)
{
    return m->conversions;
}

uint32_t adsm_selfcals(const adsm_t *m)
{
    return m->selfcals;
}
