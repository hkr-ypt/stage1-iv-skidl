/*
 * ads1256.c — ADS1256 (U8, TI SBAS288K Rev. K) driver on SPI2: non-blocking
 * init / reconfigure / settled mux-cycling conversion / integrity checks.
 * Contract: ads1256.h; design: docs/firmware/firmware_spec.md §4.5, §7.4.
 *
 * Datasheet facts relied on (f_CLKIN 7.68 MHz, tau = 130.2 ns):
 *  - SBAS288K p.6 (Fig. 1 timing): t6 >= 50 tau = 6.51 us from the last SCLK
 *    of RDATA/RREG (incl. the RREG count byte) to the first data SCLK ->
 *    ADS_T6_US = 7; t10 >= 8 tau = 1.04 us /CS hold -> ADS_T10_US = 2;
 *    t11 >= 4 tau = 0.52 us after WREG/RREG/RDATA -> 1 us, >= 24 tau =
 *    3.13 us after SYNC -> 4 us (also used after WAKEUP, whose t11 is not
 *    specified); "wait for DRDY" after RESET and SELFCAL.
 *  - p.7 (Fig. 3): RESET pulse t16 >= 4 tau -> 2 us. Fig. 4: t17 = 16 tau =
 *    2.08 us update window -> 3 us in the read deadline.
 *  - p.20 Table 13: settling t18 per data rate; p.25 Table 21: SELFCAL time
 *    (PGA-independent at <= 3750 SPS); p.31: DRATE codes. All three verified
 *    against the extract for the v1 set below.
 *  - p.21 Fig. 19: WREG MUX -> SYNC -> WAKEUP -> wait for DRDY -> RDATA
 *    gives settled data of the new channel. p.27: SYNC happens on the first
 *    SCLK of WAKEUP; after reset a self-calibration runs automatically; the
 *    RESET command keeps CLK1:CLK0, only the pin restores them.
 *  - p.34 (RDATA): DRDY goes high after all 24 bits are shifted out.
 *  - p.23: 24-bit two's complement, clip codes 0x7FFFFF / 0x800000.
 *  - p.30-32: STATUS (ID 7:4, ORDER, ACAL, BUFEN, DRDY), MUX, ADCON (CLK 6:5,
 *    SDCS 4:3, PGA 2:0), DRATE, IO (DIR 7:4, DIO 3:0; reset 0xE0).
 *
 * Transaction framing (each <= ~47 us at 1.5 MHz SCLK; at most one per
 * ads_service() call): /CS low, bytes with the explicit waits, then a final
 * wait of max(t10, t11 of the last command) before /CS goes high, so the next
 * command can follow in the very next call. The conversion start is ONE
 * transaction: WREG MUX (51 00 mux), 1 us, SYNC, 4 us, WAKEUP, 4 us, /CS high;
 * t_start is taken just before the WAKEUP byte (synchronisation instant).
 *
 * Read deadline (ads_read_deadline_us): floor(1/DR) - (7 t6 + 16 for 24 SCLK
 * at 1.5 MHz + 3 t17 + 10 margin) = floor(1/DR) - 36 us. The 10 us margin
 * covers the RDATA command byte (5.33 us), /CS and call overhead and the 1 us
 * timestamp quantisation. 3750 SPS: 266 - 36 = 230 us.
 *
 * Timeouts: reset 100 ms (crystal start + automatic self-cal); calibration
 * Table 21 * 3/2 + 5 ms; conversion: first accepted edge within
 * t18 * 3/2 + 2 ms after WAKEUP; after a late (missed-deadline) edge the next
 * edge must come within 1/DR * 3/2 + 2 ms of the re-arm that follows it, at
 * most ADS_MAX_LATE times.
 *
 * Register scrub: every ADC_SCRUB_PERIOD_US / 2 (so that detection is within
 * ADC_SCRUB_PERIOD_US even when the scrub has to wait for a slot), or on
 * request; slots are IDLE, a completed sample not yet taken, and the dead time
 * of a conversion (before 0.9 t18 - 100 us after WAKEUP, or before the next
 * expected edge after a late one), where an RREG cannot delay the read.
 */
#include "ads1256.h"

#include "port.h"

#include <string.h>

/* ---- timing constants (see header comment) ------------------------------- */
#define ADS_T6_US                 7u
#define ADS_T11_SHORT_US          1u
#define ADS_T11_LONG_US           4u
#define ADS_T10_US                2u
#define ADS_RESET_PULSE_US        2u
#define ADS_SPI_TIMEOUT_US        200u
#define ADS_RESET_TIMEOUT_US      100000u
#define ADS_DRDY_POLL_MIN_US      100u    /* after RESET pulse / SELFCAL         */
#define ADS_CAL_TIMEOUT_EXTRA_US  5000u
#define ADS_CONV_TIMEOUT_EXTRA_US 2000u
#define ADS_MAX_LATE              8u
#define ADS_STUCK_RUN             32u
#define ADS_SCRUB_INTERVAL_US     (ADC_SCRUB_PERIOD_US / 2u)
#define ADS_SCRUB_GUARD_US        100u
/* read deadline budget: t6 + 24 SCLK @ 1.5 MHz + t17 + margin */
#define ADS_T17_US                3u
#define ADS_READ_BUDGET_US        (ADS_T6_US + 16u + ADS_T17_US + 10u)

/* ---- v1 data-rate table: SBAS288K p.31 codes, Table 13 t18, Table 21 SELFCAL */
static const ads_rate_t k_rates[] = {
    {    25u, 0x03u, 400180u, 1227200u },
    {    50u, 0x13u, 200180u,  613800u },
    {   100u, 0x23u, 100180u,  307200u },
    {   150u, 0x33u,  66840u,  202100u },
    {   250u, 0x43u,  40180u,  123200u },
    {   300u, 0x53u,  33510u,  101300u },
    {   500u, 0x63u,  20180u,   61800u },
    {   600u, 0x72u,  16840u,   50900u },
    {  1000u, 0x82u,  10180u,   31200u },
    {  5000u, 0x92u,   2180u,    6600u },
    { 10000u, 0xA1u,   1180u,    3600u },
    { 20000u, 0xB0u,    680u,    2000u },
    { 37500u, 0xC0u,    440u,    1300u },
};
#define K_RATE_COUNT (sizeof k_rates / sizeof k_rates[0])

typedef enum {
    PH_NONE = 0,
    /* init / reconfigure */
    PH_RESET_PULSE,
    PH_RESET_WAIT,
    PH_RECONF_WRITE,
    PH_SYNC_CAL,
    PH_CAL_WAIT,
    /* conversion */
    PH_START_PENDING,
    PH_WAIT_EDGE,
    PH_SAMPLE_READY,
} ads_phase_t;

static struct {
    ads_state_t       state;
    ads_err_t         err;
    ads_phase_t       ph;
    ads_cfg_t         cfg;
    const ads_rate_t *rate;
    uint8_t           shadow[5];
    uint8_t           last_id;
    uint64_t          t_phase;
    uint64_t          t_last_scrub;
    bool              scrub_req;
    /* conversion in flight */
    ads_mux_t         mux;
    uint64_t          t_start;
    uint64_t          t_wait_from;
    uint32_t          wait_limit_us;
    uint64_t          t_next_edge_min;   /* earliest acceptable edge          */
    bool              edge_ok;
    uint64_t          t_drdy;
    bool              late;
    uint32_t          late_count;
    uint8_t           cal_tries;         /* SYNC/WAKEUP/SELFCAL frames sent   */
    ads_sample_t      sample;
    /* stuck-DOUT detector */
    uint32_t          stuck_run;
    uint32_t          stuck_raw;
    bool              stuck_i, stuck_v;
    ads_stats_t       stats;
} s;

/* ---- rate table ------------------------------------------------------------ */

const ads_rate_t *ads_rate_by_sps_x10(uint32_t sps_x10)
{
    for (size_t i = 0; i < K_RATE_COUNT; i++) {
        if (k_rates[i].sps_x10 == sps_x10) {
            return &k_rates[i];
        }
    }
    return NULL;
}

const ads_rate_t *ads_rate_by_code(uint8_t drate)
{
    for (size_t i = 0; i < K_RATE_COUNT; i++) {
        if (k_rates[i].drate == drate) {
            return &k_rates[i];
        }
    }
    return NULL;
}

const ads_rate_t *ads_rate_table(size_t *count)
{
    if (count) {
        *count = K_RATE_COUNT;
    }
    return k_rates;
}

uint32_t ads_period_us(const ads_rate_t *r)
{
    if (!r || r->sps_x10 == 0u) {
        return 0u;
    }
    return (10000000u + r->sps_x10 - 1u) / r->sps_x10;
}

uint32_t ads_read_deadline_us(const ads_rate_t *r)
{
    if (!r || r->sps_x10 == 0u) {
        return 0u;
    }
    uint32_t period_floor = 10000000u / r->sps_x10;
    return period_floor > ADS_READ_BUDGET_US ? period_floor - ADS_READ_BUDGET_US : 0u;
}

int32_t ads_sign_extend(uint32_t raw24)
{
    int32_t v = (int32_t)(raw24 & 0x7FFFFFu);
    if (raw24 & 0x800000u) {
        v -= 0x800000;
    }
    return v;
}

/* ---- helpers ----------------------------------------------------------------- */

static bool mux_valid(ads_mux_t mux)
{
    return mux == ADS_MUX_I || mux == ADS_MUX_V || mux == ADS_MUX_ZERO || mux == ADS_MUX_NEGREF;
}

static void fail(ads_err_t e)
{
    s.state = ADS_ERROR;
    s.err = e;
    s.ph = PH_NONE;
    if (e == ADS_ERR_TIMEOUT) {
        s.stats.timeouts++;
    }
}

static uint64_t elapsed(uint64_t now, uint64_t since)
{
    return now >= since ? now - since : 0u;
}

/* Transfer inside an open transaction; false -> ADS_ERR_SPI (caller ends). */
static bool xfer(const uint8_t *tx, uint8_t *rx, size_t n)
{
    return port_adc_xfer(tx, rx, n, ADS_SPI_TIMEOUT_US);
}

/* Final wait max(t10, t11 of the last command), then /CS high. */
static void txn_end(uint32_t hold_us)
{
    port_delay_us(hold_us > ADS_T10_US ? hold_us : ADS_T10_US);
    port_adc_cs(false);
}

static bool txn_fail(void)
{
    txn_end(ADS_T10_US);
    fail(ADS_ERR_SPI);
    return false;
}

/* WREG of n consecutive registers starting at reg. */
static bool txn_wreg(uint8_t reg, const uint8_t *vals, uint8_t n)
{
    uint8_t tx[7];
    tx[0] = (uint8_t)(ADS_CMD_WREG | reg);
    tx[1] = (uint8_t)(n - 1u);
    memcpy(&tx[2], vals, n);
    port_adc_cs(true);
    if (!xfer(tx, NULL, (size_t)n + 2u)) {
        return txn_fail();
    }
    txn_end(ADS_T11_SHORT_US);
    return true;
}

/* RREG 0x00..0x04. */
static bool txn_rreg5(uint8_t rx[5])
{
    static const uint8_t cmd[2] = { ADS_CMD_RREG | ADS_REG_STATUS, 0x04u };
    static const uint8_t dummy[5] = { 0, 0, 0, 0, 0 };
    port_adc_cs(true);
    if (!xfer(cmd, NULL, sizeof cmd)) {
        return txn_fail();
    }
    port_delay_us(ADS_T6_US);
    if (!xfer(dummy, rx, 5u)) {
        return txn_fail();
    }
    txn_end(ADS_T11_SHORT_US);
    return true;
}

/* SYNC, WAKEUP, SELFCAL in one /CS frame. SYNC + WAKEUP is the documented
 * synchronisation (p.27: after a PGA or data-rate change "perform a
 * synchronization operation to force DRDY high"; Table 24: WAKEUP "completes
 * SYNC"). SELFCAL then starts from normal operation (p.25: "calibration can
 * be done at any time"; p.36: /DRDY high until it completes with settled
 * data). What SELFCAL does while SYNC holds the part stopped, with no WAKEUP,
 * the data sheet does not say. */
static bool txn_sync_selfcal(void)
{
    static const uint8_t sync = ADS_CMD_SYNC;
    static const uint8_t wakeup = ADS_CMD_WAKEUP;
    static const uint8_t cal = ADS_CMD_SELFCAL;
    port_adc_cs(true);
    if (!xfer(&sync, NULL, 1u)) {
        return txn_fail();
    }
    port_delay_us(ADS_T11_LONG_US);
    if (!xfer(&wakeup, NULL, 1u)) {
        return txn_fail();
    }
    port_delay_us(ADS_T11_LONG_US);
    if (!xfer(&cal, NULL, 1u)) {
        return txn_fail();
    }
    txn_end(ADS_T10_US);      /* t11 after SELFCAL = wait for /DRDY */
    return true;
}

/* WREG MUX, SYNC, WAKEUP in one /CS frame; *t_wakeup = time of WAKEUP. */
static bool txn_start(uint8_t mux, uint64_t *t_wakeup)
{
    const uint8_t wreg[3] = { (uint8_t)(ADS_CMD_WREG | ADS_REG_MUX), 0x00u, mux };
    static const uint8_t sync = ADS_CMD_SYNC;
    static const uint8_t wakeup = ADS_CMD_WAKEUP;
    port_adc_cs(true);
    if (!xfer(wreg, NULL, sizeof wreg)) {
        return txn_fail();
    }
    port_delay_us(ADS_T11_SHORT_US);
    if (!xfer(&sync, NULL, 1u)) {
        return txn_fail();
    }
    port_delay_us(ADS_T11_LONG_US);
    *t_wakeup = port_now_us();
    if (!xfer(&wakeup, NULL, 1u)) {
        return txn_fail();
    }
    txn_end(ADS_T11_LONG_US);
    return true;
}

static bool txn_rdata(uint32_t *raw)
{
    static const uint8_t cmd = ADS_CMD_RDATA;
    static const uint8_t dummy[3] = { 0, 0, 0 };
    uint8_t rx[3] = { 0, 0, 0 };
    port_adc_cs(true);
    if (!xfer(&cmd, NULL, 1u)) {
        return txn_fail();
    }
    port_delay_us(ADS_T6_US);
    if (!xfer(dummy, rx, sizeof rx)) {
        return txn_fail();
    }
    txn_end(ADS_T11_SHORT_US);
    *raw = ((uint32_t)rx[0] << 16) | ((uint32_t)rx[1] << 8) | (uint32_t)rx[2];
    return true;
}

/* STATUS compared on bits 3:1 (ORDER, ACAL, BUFEN); ID recorded; DRDY ignored. */
static bool regs_match(const uint8_t rx[5])
{
    s.last_id = (uint8_t)(rx[0] >> 4);
    if ((rx[0] & 0x0Eu) != (s.shadow[ADS_REG_STATUS] & 0x0Eu)) {
        return false;
    }
    for (unsigned i = 1; i < 5u; i++) {
        if (rx[i] != s.shadow[i]) {
            return false;
        }
    }
    return true;
}

static void stuck_reset(void)
{
    s.stuck_run = 0;
    s.stuck_i = false;
    s.stuck_v = false;
}

/* True when the stuck-DOUT signature is complete. */
static bool stuck_update(uint32_t raw, ads_mux_t mux)
{
    if (raw != 0x000000u && raw != 0xFFFFFFu) {
        stuck_reset();
        return false;
    }
    if (s.stuck_run == 0u || raw != s.stuck_raw) {
        stuck_reset();
        s.stuck_raw = raw;
    }
    s.stuck_run++;
    if (mux == ADS_MUX_I) {
        s.stuck_i = true;
    } else if (mux == ADS_MUX_V) {
        s.stuck_v = true;
    }
    return s.stuck_run >= ADS_STUCK_RUN && s.stuck_i && s.stuck_v;
}

static bool scrub_due(uint64_t now)
{
    return s.scrub_req || elapsed(now, s.t_last_scrub) >= ADS_SCRUB_INTERVAL_US;
}

static void do_scrub(uint64_t now)
{
    uint8_t rx[5] = { 0, 0, 0, 0, 0 };
    if (!txn_rreg5(rx)) {
        return;
    }
    s.stats.scrubs++;
    s.t_last_scrub = now;
    s.scrub_req = false;
    if (!regs_match(rx)) {
        fail(ADS_ERR_READBACK);
    }
}

/* ---- init / reconfigure ------------------------------------------------------- */

static void service_init(void)
{
    uint64_t now = port_now_us();

    switch (s.ph) {
    case PH_RESET_PULSE:
        port_adc_cs(false);
        port_adc_reset_write(false);
        port_delay_us(ADS_RESET_PULSE_US);
        port_adc_reset_write(true);
        s.t_phase = port_now_us();
        s.ph = PH_RESET_WAIT;
        break;

    case PH_RESET_WAIT:
        if (elapsed(now, s.t_phase) >= ADS_DRDY_POLL_MIN_US && port_adc_drdy_low()) {
            /* automatic self-calibration done: write STATUS..IO in one WREG */
            if (txn_wreg(ADS_REG_STATUS, s.shadow, 5u)) {
                s.ph = PH_SYNC_CAL;
            }
        } else if (elapsed(now, s.t_phase) > ADS_RESET_TIMEOUT_US) {
            fail(ADS_ERR_TIMEOUT);
        }
        break;

    case PH_RECONF_WRITE:
        if (txn_wreg(ADS_REG_ADCON, &s.shadow[ADS_REG_ADCON], 2u)) {
            s.ph = PH_SYNC_CAL;
        }
        break;

    case PH_SYNC_CAL:
        if (txn_sync_selfcal()) {
            s.t_phase = port_now_us();
            s.ph = PH_CAL_WAIT;
        }
        break;

    case PH_CAL_WAIT: {
        uint32_t limit = s.rate->selfcal_us * 3u / 2u + ADS_CAL_TIMEOUT_EXTRA_US;
        uint64_t el = elapsed(now, s.t_phase);
        if (el >= ADS_DRDY_POLL_MIN_US && port_adc_drdy_low()) {
            if (el < s.rate->selfcal_us / 2u) {
                /* /DRDY low in less than half the Table 21 time (> t18 at
                 * every v1 rate): the part did not calibrate; a SELFCAL it
                 * ignored leaves only old data or the WAKEUP's conversion.
                 * Send the frame once more, then fail as a calibration that
                 * never ends would, rather than run on an old calibration. */
                if (++s.cal_tries < 2u) {
                    s.ph = PH_SYNC_CAL;
                } else {
                    fail(ADS_ERR_TIMEOUT);
                }
                break;
            }
            uint8_t rx[5] = { 0, 0, 0, 0, 0 };
            if (!txn_rreg5(rx)) {
                break;
            }
            if (!regs_match(rx)) {
                fail(ADS_ERR_READBACK);
                break;
            }
            s.state = ADS_IDLE;
            s.ph = PH_NONE;
            s.t_last_scrub = port_now_us();
            stuck_reset();
        } else if (elapsed(now, s.t_phase) > limit) {
            fail(ADS_ERR_TIMEOUT);
        }
        break;
    }

    default:
        fail(ADS_ERR_TIMEOUT);   /* unreachable */
        break;
    }
}

/* ---- conversion ------------------------------------------------------------------ */

static void missed_edge(void);

/* The deadline is checked when RDATA starts; an interrupt during the read can
 * still push its end into t17 before the next update, where the output
 * register changes: such a read is discarded like a missed deadline. */
static bool read_ended_in_window(uint64_t t_end)
{
    uint32_t period_floor = 10000000u / s.rate->sps_x10;
    return elapsed(t_end, s.t_drdy) + ADS_T17_US <= period_floor;
}

static void do_read(void)
{
    uint32_t raw = 0;
    uint64_t t_read = port_now_us();
    if (!txn_rdata(&raw)) {
        return;
    }
    if (!read_ended_in_window(port_now_us())) {
        s.stats.read_overruns++;
        missed_edge();
        return;
    }
    if (stuck_update(raw, s.mux)) {
        fail(ADS_ERR_STUCK);
        return;
    }
    s.sample.code = ads_sign_extend(raw);
    s.sample.mux = s.mux;
    s.sample.t_start_us = s.t_start;
    s.sample.t_drdy_us = s.t_drdy;
    s.sample.t_read_us = t_read;
    s.sample.late = s.late;
    s.sample.clipped = raw == 0x7FFFFFu || raw == 0x800000u;
    s.stats.conversions++;
    if (s.late) {
        s.stats.late++;
    }
    s.ph = PH_SAMPLE_READY;
}

/* missed (or overrun) read: the next edge comes one data period later
 * (free-running) */
static void missed_edge(void)
{
    s.late = true;
    s.edge_ok = false;
    if (++s.late_count > ADS_MAX_LATE) {
        fail(ADS_ERR_TIMEOUT);
        return;
    }
    /* The timeout runs from the re-arm, not from the missed edge: after a
     * main-loop stall of more than one period the following edge has already
     * gone by and the re-arm clears it, so the next one can come up to one
     * period after now (from t_drdy that would exceed 3/2 P + 2 ms at every
     * rate up to 100 SPS and fault a recoverable late read). */
    uint64_t now = port_now_us();
    s.t_wait_from = (now > s.t_drdy) ? now : s.t_drdy;
    s.wait_limit_us = ads_period_us(s.rate) * 3u / 2u + ADS_CONV_TIMEOUT_EXTRA_US;
    s.t_next_edge_min = s.t_drdy + ads_period_us(s.rate) * 9u / 10u;
    port_adc_drdy_arm();
}

static void wait_edge(void)
{
    uint64_t t;

    if (!s.edge_ok && port_adc_drdy_take(&t)) {
        if (t < s.t_next_edge_min) {
            s.stats.stale_edges++;          /* earlier than 0.9 t18 after WAKEUP */
            port_adc_drdy_arm();
        } else {
            s.edge_ok = true;
            s.t_drdy = t;
        }
    }

    uint64_t now = port_now_us();
    if (s.edge_ok) {
        if (port_adc_drdy_low() && elapsed(now, s.t_drdy) <= ads_read_deadline_us(s.rate)) {
            do_read();
            return;
        }
        missed_edge();
        return;
    }

    if (elapsed(now, s.t_wait_from) > s.wait_limit_us) {
        fail(ADS_ERR_TIMEOUT);
        return;
    }
    /* dead time of the conversion: an RREG here cannot delay the read */
    if (scrub_due(now) && now + ADS_SCRUB_GUARD_US <= s.t_next_edge_min) {
        do_scrub(now);
    }
}

static void service_conv(void)
{
    uint64_t now = port_now_us();

    switch (s.ph) {
    case PH_START_PENDING: {
        uint64_t t_wakeup = 0;
        s.shadow[ADS_REG_MUX] = (uint8_t)s.mux;
        if (!txn_start((uint8_t)s.mux, &t_wakeup)) {
            return;
        }
        /* clear any pending edge AFTER WAKEUP, then capture the next one */
        port_adc_drdy_arm();
        s.t_start = t_wakeup;
        s.t_wait_from = t_wakeup;
        s.wait_limit_us = s.rate->t18_us * 3u / 2u + ADS_CONV_TIMEOUT_EXTRA_US;
        s.t_next_edge_min = t_wakeup + s.rate->t18_us * 9u / 10u;
        s.edge_ok = false;
        s.late = false;
        s.late_count = 0;
        s.ph = PH_WAIT_EDGE;
        break;
    }
    case PH_WAIT_EDGE:
        wait_edge();
        break;
    case PH_SAMPLE_READY:
        if (scrub_due(now)) {
            do_scrub(now);
        }
        break;
    default:
        fail(ADS_ERR_TIMEOUT);   /* unreachable */
        break;
    }
}

/* ---- public API ------------------------------------------------------------------- */

void ads_init_begin(const ads_cfg_t *cfg)
{
    ads_stats_t keep = s.stats;
    memset(&s, 0, sizeof s);
    s.stats = keep;
    s.stats.inits++;

    const ads_rate_t *r = cfg ? ads_rate_by_code(cfg->drate) : NULL;
    if (!r) {
        /* not a v1 rate: the configuration cannot be written and verified */
        s.state = ADS_ERROR;
        s.err = ADS_ERR_READBACK;
        return;
    }
    s.cfg = *cfg;
    s.rate = r;
    s.shadow[ADS_REG_STATUS] = 0x00u;                    /* MSB first, ACAL 0, BUFEN 0 */
    s.shadow[ADS_REG_MUX] = (uint8_t)ADS_MUX_ZERO;
    s.shadow[ADS_REG_ADCON] = (uint8_t)(cfg->pga & 7u);  /* CLK 00, SDCS 00            */
    s.shadow[ADS_REG_DRATE] = cfg->drate;
    s.shadow[ADS_REG_IO] = 0x00u;                        /* D0-D3 outputs, low         */
    s.mux = ADS_MUX_ZERO;
    s.state = ADS_INIT;
    s.ph = PH_RESET_PULSE;
}

bool ads_reconfigure(const ads_cfg_t *cfg)
{
    if (s.state != ADS_IDLE || !cfg) {
        return false;
    }
    const ads_rate_t *r = ads_rate_by_code(cfg->drate);
    if (!r) {
        return false;
    }
    s.cfg = *cfg;
    s.rate = r;
    s.shadow[ADS_REG_ADCON] = (uint8_t)(cfg->pga & 7u);
    s.shadow[ADS_REG_DRATE] = cfg->drate;
    s.state = ADS_INIT;
    s.ph = PH_RECONF_WRITE;
    s.cal_tries = 0;
    return true;
}

void ads_service(void)
{
    switch (s.state) {
    case ADS_INIT:
        service_init();
        break;
    case ADS_IDLE: {
        uint64_t now = port_now_us();
        if (scrub_due(now)) {
            do_scrub(now);
        }
        break;
    }
    case ADS_CONVERTING:
        service_conv();
        break;
    case ADS_OFF:
    case ADS_ERROR:
    default:
        break;
    }
}

ads_state_t ads_state(void)
{
    return s.state;
}

ads_err_t ads_error(void)
{
    return s.err;
}

bool ads_idle(void)
{
    return s.state == ADS_IDLE;
}

const ads_cfg_t *ads_config(void)
{
    return &s.cfg;
}

bool ads_start(ads_mux_t mux)
{
    if (!mux_valid(mux) || (s.state != ADS_IDLE && s.state != ADS_CONVERTING)) {
        return false;
    }
    s.mux = mux;
    s.state = ADS_CONVERTING;
    s.ph = PH_START_PENDING;
    return true;
}

bool ads_take(ads_sample_t *out)
{
    if (s.state != ADS_CONVERTING || s.ph != PH_SAMPLE_READY) {
        return false;
    }
    if (out) {
        *out = s.sample;
    }
    s.state = ADS_IDLE;
    s.ph = PH_NONE;
    return true;
}

void ads_request_scrub(void)
{
    s.scrub_req = true;
}

void ads_get_stats(ads_stats_t *out)
{
    if (out) {
        *out = s.stats;
    }
}

uint8_t ads_last_id(void)
{
    return s.last_id;
}

void ads_shadow_regs(uint8_t out[5])
{
    memcpy(out, s.shadow, sizeof s.shadow);
}

#if FW_BRINGUP
void ads_pulse_reset_unannounced(void)
{
    port_adc_reset_write(false);
    port_delay_us(ADS_RESET_PULSE_US);
    port_adc_reset_write(true);
}
#endif
