/*
 * port_sim.c — the simulation implementation of port.h (contract: port.h,
 * port_sim.h, port_sim_ext.h), used by the system tests and by the
 * whole-firmware simulator (sim_main.c).
 *
 * Virtual time. A uint64 microsecond clock plus a double sub-microsecond
 * accumulator. It advances only through port_delay_us(), SPI transfers,
 * flash operations, the per-pass cost (port_pass_end), stalls and
 * psim_advance_us(). Every advance moves the models along in steps of at
 * most 100 us: the charge pump (board_advance), the ADS1256 model
 * (adsm_advance) and the EXTI emulation (the first /DRDY fall after
 * port_adc_drdy_arm() is latched with its own timestamp). A thread that
 * busy-polls port_now_us() or port_adc_drdy_low() without anything
 * advancing time is moved on by 1 us every 1000 polls (spin guard), so a
 * polling loop terminates as it would on the target.
 *
 * Datasheet facts used:
 *  - SPI1 (DAC8552) mode 1 at 6 MHz (APB2 96 MHz / 16, spec §4.2): 8/6 us
 *    per byte; SPI2 (ADS1256) at 1.5 MHz: period 0.667 us >= t1 = 4 tau
 *    = 520.8 ns (SBAS288K Table "Timing characteristics"). Bytes are clocked
 *    back to back (no inter-byte gap), so the ADS1256 model sees the
 *    strictest timing the driver can produce; t6/t10/t11 are the driver's
 *    explicit port_delay_us() calls.
 *  - /SYNC (PA4) has the R34 pull-up: after an MCU reset it is high, which
 *    aborts a frame of fewer than 24 bits (SLAS430A p.15). /RESET (PA9) and
 *    /DRDY (PA8) have 10 k pull-ups (blocks/adc.py).
 *  - IWDG: prescaler /32, reload 1500, LSI 17..47 kHz (DS10314 Table 40):
 *    the shortest timeout, 1.02 s, is emulated.
 *  - Flash (DS10314 Table 45, PSIZE x32): 16 KB sector erase 250 ms typ,
 *    word program 16 us typ. Programming can only clear bits (the result is
 *    old & new); erase sets 0xFF.
 *  - The dead-man (TIM5, spec §7.1/§7.9) fires when no pass has ended for
 *    more than DEADMAN_TIMEOUT_US while enabled: exactly like the target ISR
 *    it forces PC5 low, runs dac_zero_safe(), writes a CRASH_DEADMAN record
 *    and requests a software reset.
 *  - RCC_CSR causes are reported already decoded: RST_POR at power-on,
 *    RST_SFT after port_system_reset(), RST_IWDG after a watchdog reset,
 *    RST_PIN after a plain psim_mcu_reset() (NRST).
 *  - DBGMCU_APB1_FZ survives a system reset (RM0383 §23.16.4: reset by POR
 *    only), so the DBG_TIM2 flag is kept by psim_mcu_reset().
 *
 * Reset/hang emulation: see port_sim_ext.h (port_system_reset() RETURNS in
 * the simulator; the loop owner performs the reset).
 */
#include "port.h"
#include "port_sim.h"
#include "port_sim_ext.h"
#include "dac8552.h"

#include <math.h>
#include <string.h>

/* ---- constants ---------------------------------------------------------------- */
#define CAL_SECTOR_BYTES     16384u
#define STEP_MAX_US          100u
#define SPIN_LIMIT           1000u
#define SPI1_MHZ             6.0
#define SPI2_MHZ             1.5
#define SPI1_CALL_OVERHEAD_US 0.25   /* BSY polling after the last bit        */
#define SPI2_CALL_OVERHEAD_US 0.25   /* register set-up before the first SCLK */
#define SPI_REINIT_US        1.0
#define DELAY_BOUND_US       50u
#define TEMP_DEFAULT_CDEG    2500

/* Synthetic register snapshot values stored in crash records. */
#define CRASH_PC_DEADMAN     0x08006A10u
#define CRASH_PC_HARDFAULT   0x08004F22u
#define CRASH_LR_THREAD      0xFFFFFFFDu  /* EXC_RETURN: thread mode, PSP  */
#define CRASH_XPSR           0x61000000u
#define CRASH_CFSR_UNDEF     0x00010000u  /* UFSR.UNDEFINSTR               */
#define CRASH_CFSR_MSTKERR   0x00000092u  /* MMFSR: MMARVALID, MSTKERR, DACCVIOL */
#define CRASH_HFSR_FORCED    0x40000000u
#define CRASH_RCC_CR         0x030B0083u  /* HSI, HSE, CSS, PLL on/ready   */
#define CRASH_RCC_CFGR       0x0000100Au  /* SW = SWS = PLL, APB1 /2       */

static const uint32_t k_uid[3] = { 0x0034002Bu, 0x34365112u, 0x33353533u };

typedef struct {
    uint8_t buf[PSIM_HOST_RING_BYTES];
    size_t  head;    /* index of the oldest byte */
    size_t  count;
} ring_t;

typedef struct {
    bool     init;
    /* time */
    uint64_t now_us;
    double   frac_us;
    uint32_t spin;
    /* options */
    psim_options_t opt;
    /* models */
    dac_model_t    dac;
    board_model_t  board;
    adsm_t        *ads;
    /* PC5 */
    bool     hb_level;
    uint64_t hb_edges;
    uint64_t hb_last_edge_us;
    /* SPI1 */
    bool     dac_cs;
    bool     bitbang;
    uint32_t spi1_fail;
    /* SPI2 */
    bool     adc_cs;
    bool     adc_reset_high;
    uint32_t spi2_fail;
    /* EXTI */
    bool     exti_armed;
    bool     exti_latched;
    uint64_t exti_t;
    /* host link */
    uint32_t vbus_mv;
    bool     configured_flag;
    bool     dtr_flag;
    bool     connected;
    size_t   tx_limit;
    /* system */
    int      bor;
    bool     tim2_frozen;
    bool     stack_ok;
    bool     hse_ok;
    bool     led;
    bool     in_exception_flag;
    bool     in_isr;
    int32_t  temp_cdeg;
    uint32_t reset_flags;
    /* watchdogs */
    bool     deadman_enabled;
    uint64_t deadman_last;
    uint32_t deadman_reloads;
    uint64_t iwdg_last;
    uint32_t iwdg_kicks;
    /* reset / hang */
    bool     reset_requested;
    uint32_t next_reset_cause;
    uint32_t resets_requested;
    bool     hung;
    bool     hung_irqoff;
    uint64_t stall_pending_us;
    /* flash */
    bool     tear_next;
    uint32_t cal_erases, cal_programs;
    /* statistics */
    psim_ext_obs_t ext;
} psim_state_t;

static psim_state_t S;
/* Kept outside S on purpose: survive psim_mcu_reset(), not power-on. */
static uint8_t        s_cal[PORT_CAL_SECTORS][CAL_SECTOR_BYTES];
static crash_record_t s_crash;
static ring_t         s_rx, s_tx;

static void advance_to(uint64_t target);

/* ---- helpers --------------------------------------------------------------------- */

static void ensure(void)
{
    if (!S.init) {
        psim_power_on(NULL);
    }
}

static bool cpu_stopped(void)
{
    return (S.reset_requested || S.hung) && !S.in_isr;
}

static double now_d(void)
{
    return (double)S.now_us + S.frac_us;
}

static void advance_d(double us)
{
    if (!(us > 0.0)) {
        return;
    }
    S.frac_us += us;
    double whole = floor(S.frac_us);
    S.frac_us -= whole;
    if (whole > 0.0) {
        advance_to(S.now_us + (uint64_t)whole);
    }
}

static void spin_poll(void)
{
    if (++S.spin >= SPIN_LIMIT) {
        S.ext.spin_advances++;
        advance_to(S.now_us + 1u);
    }
}

static void ring_clear(ring_t *r)
{
    r->head = 0;
    r->count = 0;
}

static size_t ring_push(ring_t *r, const uint8_t *d, size_t n)
{
    size_t k = 0;
    while (k < n && r->count < PSIM_HOST_RING_BYTES) {
        r->buf[(r->head + r->count) % PSIM_HOST_RING_BYTES] = d[k++];
        r->count++;
    }
    return k;
}

static size_t ring_pop(ring_t *r, uint8_t *d, size_t n)
{
    size_t k = 0;
    while (k < n && r->count > 0) {
        d[k++] = r->buf[r->head];
        r->head = (r->head + 1u) % PSIM_HOST_RING_BYTES;
        r->count--;
    }
    return k;
}

static uint32_t crc32_words(const uint32_t *w, size_t nwords)
{
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < nwords; i++) {
        for (unsigned byte = 0; byte < 4u; byte++) {
            crc ^= (w[i] >> (8u * byte)) & 0xFFu;
            for (unsigned bit = 0; bit < 8u; bit++) {
                crc = (crc & 1u) ? (crc >> 1) ^ 0xEDB88320u : crc >> 1;
            }
        }
    }
    return ~crc;
}

#define CRASH_CRC_WORDS ((offsetof(crash_record_t, crc)) / sizeof(uint32_t))

static void crash_write(crash_cause_t cause, uint32_t pc, uint32_t cfsr, uint32_t hfsr)
{
    crash_record_t r;
    memset(&r, 0, sizeof(r));
    r.magic = CRASH_MAGIC;
    r.cause = (uint32_t)cause;
    r.pc = pc;
    r.lr = CRASH_LR_THREAD;
    r.xpsr = CRASH_XPSR;
    r.cfsr = cfsr;
    r.hfsr = hfsr;
    r.rcc_cr = CRASH_RCC_CR;
    r.rcc_cfgr = CRASH_RCC_CFGR;
    r.crc = crc32_words(&r.magic, CRASH_CRC_WORDS);
    s_crash = r;
}

static bool crash_valid(void)
{
    return s_crash.magic == CRASH_MAGIC && s_crash.cause != (uint32_t)CRASH_NONE &&
           s_crash.crc == crc32_words(&s_crash.magic, CRASH_CRC_WORDS);
}

static void request_reset(uint32_t cause)
{
    if (!S.reset_requested) {
        S.reset_requested = true;
        S.next_reset_cause = cause;
    }
}

/* ---- models and watchdogs --------------------------------------------------------------- */

static void models_update(void)
{
    board_advance(&S.board, S.now_us);
    adsm_advance(S.ads, S.now_us);
    uint64_t t;
    if (adsm_take_drdy_fall(S.ads, &t)) {
        if (S.exti_armed) {
            S.exti_armed = false;
            S.exti_latched = true;
            S.exti_t = t;
            S.ext.exti_captures++;
        }
    }
}

static bool deadman_armed(void)
{
    return S.deadman_enabled && !S.reset_requested && !S.in_isr && !S.hung_irqoff;
}

static bool iwdg_armed(void)
{
    return !S.reset_requested;
}

/* The TIM5 dead-man ISR, as on the target (spec §7.9). */
static void deadman_fire(void)
{
    S.in_isr = true;
    S.ext.deadman_fires++;
    port_hb_force_low();
    dac_zero_safe();
    crash_write(CRASH_DEADMAN, CRASH_PC_DEADMAN, 0u, 0u);
    S.deadman_enabled = false;
    port_system_reset();
    S.in_isr = false;
}

static void watchdogs_check(void)
{
    if (deadman_armed() && S.now_us - S.deadman_last > DEADMAN_TIMEOUT_US) {
        deadman_fire();
    }
    if (iwdg_armed() && S.now_us - S.iwdg_last >= PSIM_IWDG_TIMEOUT_US) {
        S.ext.iwdg_fires++;
        request_reset(RST_IWDG);
    }
}

static void advance_to(uint64_t target)
{
    S.spin = 0;
    while (S.now_us < target) {
        uint64_t next = target;
        if (next - S.now_us > STEP_MAX_US) {
            next = S.now_us + STEP_MAX_US;
        }
        if (deadman_armed()) {
            uint64_t dl = S.deadman_last + DEADMAN_TIMEOUT_US + 1u;
            if (dl > S.now_us && dl < next) {
                next = dl;
            }
        }
        if (iwdg_armed()) {
            uint64_t dl = S.iwdg_last + PSIM_IWDG_TIMEOUT_US;
            if (dl > S.now_us && dl < next) {
                next = dl;
            }
        }
        S.now_us = next;
        models_update();
        watchdogs_check();
    }
}

/* Advance in pieces, stopping early when a reset gets requested. */
static void advance_until_reset(uint64_t us)
{
    uint64_t end = S.now_us + us;
    while (S.now_us < end && !S.reset_requested) {
        uint64_t next = S.now_us + STEP_MAX_US;
        advance_to(next < end ? next : end);
    }
}

static void consume_stall(void)
{
    uint64_t us = S.stall_pending_us;
    S.stall_pending_us = 0;
    if (us > 0) {
        S.ext.stall_us_total += us;
        advance_until_reset(us);
    }
}

/* ---- simulation control (port_sim.h) ------------------------------------------------- */

void psim_default_options(psim_options_t *o)
{
    memset(o, 0, sizeof(*o));
    o->pass_cost_us = 20u;
    o->bor_level = 3u;
    o->vbus_mv = 5000u;
    o->configured = true;
    o->dtr = true;
    o->adc_noise_codes = 2.0;
    o->seed = 1u;
}

void psim_power_on(const psim_options_t *o)
{
    psim_options_t opt;
    if (o != NULL) {
        opt = *o;
    } else {
        psim_default_options(&opt);
    }
    adsm_t *ads = S.ads;
    memset(&S, 0, sizeof(S));
    S.init = true;
    S.opt = opt;
    /* a power-on restarts virtual time at 0: the ADC model (which needs
     * monotonic time) is rebuilt rather than reused */
    if (ads != NULL) {
        adsm_free(ads);
    }
    S.ads = adsm_new(board_ain, &S.board);
    dacm_power_on(&S.dac);
    board_init(&S.board, &S.dac);
    adsm_power_on(S.ads, 0u);
    adsm_set_noise(S.ads, opt.adc_noise_codes, opt.seed);
    adsm_clear_violations(S.ads);

    S.adc_reset_high = true;
    S.vbus_mv = opt.vbus_mv;
    S.configured_flag = opt.configured;
    S.dtr_flag = opt.dtr;
    S.tx_limit = PSIM_TX_NO_LIMIT;
    S.bor = (int)opt.bor_level;
    S.stack_ok = true;
    S.hse_ok = true;
    S.temp_cdeg = TEMP_DEFAULT_CDEG;
    S.reset_flags = RST_POR;

    memset(s_cal, 0xFF, sizeof(s_cal));
    memset(&s_crash, 0, sizeof(s_crash));
    ring_clear(&s_rx);
    ring_clear(&s_tx);
}

void psim_power_cycle(void)
{
    ensure();
    static uint8_t cal_keep[PORT_CAL_SECTORS][CAL_SECTOR_BYTES];
    memcpy(cal_keep, s_cal, sizeof(cal_keep));
    board_model_t board_keep = S.board;
    psim_options_t opt = S.opt;
    opt.bor_level = (uint32_t)(S.bor < 0 ? 0 : S.bor);
    opt.vbus_mv = S.vbus_mv;
    opt.configured = S.configured_flag;
    opt.dtr = S.dtr_flag;
    size_t tx_limit = S.tx_limit;

    psim_power_on(&opt);

    memcpy(s_cal, cal_keep, sizeof(s_cal));
    S.tx_limit = tx_limit;
    /* physical configuration survives; the pump starts empty */
    board_model_t *b = &S.board;
    b->vref = board_keep.vref;
    b->sweep_offset_v = board_keep.sweep_offset_v;
    b->r_top = board_keep.r_top;
    b->r_bot = board_keep.r_bot;
    b->r_sd = board_keep.r_sd;
    b->i_lim = board_keep.i_lim;
    b->ina_v_offset_v = board_keep.ina_v_offset_v;
    b->ina_i_offset_v = board_keep.ina_i_offset_v;
    b->rail_pos = board_keep.rail_pos;
    b->rail_neg = board_keep.rail_neg;
    b->sense_plus_open = board_keep.sense_plus_open;
    b->sense_minus_open = board_keep.sense_minus_open;
    b->force_open = board_keep.force_open;
    b->gain_err_v = board_keep.gain_err_v;
    b->gain_err_i = board_keep.gain_err_i;
    b->dut = board_keep.dut;
}

void psim_mcu_reset(void)
{
    ensure();
    S.reset_flags = S.reset_requested ? S.next_reset_cause : RST_PIN;
    S.reset_requested = false;
    S.next_reset_cause = 0;
    S.hung = false;
    S.hung_irqoff = false;
    S.in_isr = false;
    S.in_exception_flag = false;
    S.stall_pending_us = 0;
    S.ext.mcu_resets++;

    /* Pins float: PC5 undriven (the pump decays as with PC5 low), /SYNC
     * pulled high by R34, /RESET high by its pull-up, /CS released. */
    if (S.hb_level) {
        S.hb_level = false;
        board_hb(&S.board, false, S.now_us);
    }
    if (S.dac_cs) {
        S.dac_cs = false;
        dacm_sync(&S.dac, false);
    }
    S.bitbang = false;
    if (S.adc_cs) {
        S.adc_cs = false;
        adsm_cs(S.ads, false, S.now_us);
    }
    if (!S.adc_reset_high) {
        S.adc_reset_high = true;
        adsm_reset_pin(S.ads, true, S.now_us);
    }
    S.exti_armed = false;
    S.exti_latched = false;

    S.deadman_enabled = false;
    S.deadman_last = S.now_us;
    S.iwdg_last = S.now_us;
    S.led = false;
    S.stack_ok = true;
    S.connected = false;      /* D+ pull-up released by the reset */
    ring_clear(&s_rx);
    ring_clear(&s_tx);
}

uint64_t psim_time_us(void)
{
    ensure();
    return S.now_us;
}

void psim_advance_us(uint64_t us)
{
    ensure();
    advance_to(S.now_us + us);
}

void psim_set_pass_cost_us(uint32_t us)
{
    ensure();
    S.opt.pass_cost_us = us;
}

dac_model_t *psim_dac(void)
{
    ensure();
    return &S.dac;
}

adsm_t *psim_ads(void)
{
    ensure();
    return S.ads;
}

board_model_t *psim_board(void)
{
    ensure();
    return &S.board;
}

size_t psim_host_take_tx(uint8_t *buf, size_t max)
{
    ensure();
    return ring_pop(&s_tx, buf, max);
}

void psim_host_give_rx(const uint8_t *buf, size_t n)
{
    ensure();
    size_t k = ring_push(&s_rx, buf, n);
    S.ext.rx_dropped += (uint64_t)(n - k);
}

void psim_set_vbus_mv(uint32_t mv)
{
    ensure();
    S.vbus_mv = mv;
}

void psim_set_configured(bool c)
{
    ensure();
    if (c && !S.configured_flag) {
        /* SET_CONFIGURATION: TinyUSB's cdcd_open() clears the CDC TX FIFO
         * (CFG_TUD_CDC_TX_PERSISTENT 0), so an old tail does not survive a
         * re-enumeration. */
        ring_clear(&s_tx);
    }
    S.configured_flag = c;
}

void psim_set_dtr(bool d)
{
    ensure();
    S.dtr_flag = d;
}

bool psim_host_connected(void)
{
    ensure();
    return S.connected;
}

void psim_set_tx_space_limit(size_t bytes)
{
    ensure();
    S.tx_limit = bytes;
}

void psim_set_bor_level(int lvl)
{
    ensure();
    S.bor = lvl;
}

void psim_set_dbg_tim2_frozen(bool f)
{
    ensure();
    S.tim2_frozen = f;
}

void psim_set_stack_ok(bool ok)
{
    ensure();
    S.stack_ok = ok;
}

void psim_set_hse_ok(bool ok)
{
    ensure();
    S.hse_ok = ok;
}

void psim_fail_spi1(uint32_t n)
{
    ensure();
    S.spi1_fail = n;
}

void psim_fail_spi2(uint32_t n)
{
    ensure();
    S.spi2_fail = n;
}

void psim_observe(psim_obs_t *o)
{
    ensure();
    memset(o, 0, sizeof(*o));
    o->hb_edges = S.hb_edges;
    o->hb_last_edge_us = S.hb_last_edge_us;
    o->hb_level = S.hb_level;
    o->iwdg_kicks = S.iwdg_kicks;
    o->deadman_reloads = S.deadman_reloads;
    o->deadman_enabled = S.deadman_enabled;
    o->resets_requested = S.resets_requested;
    o->led = S.led;
    o->cal_erases = S.cal_erases;
    o->cal_programs = S.cal_programs;
}

uint8_t *psim_cal_sector_mut(unsigned idx)
{
    ensure();
    return idx < PORT_CAL_SECTORS ? s_cal[idx] : NULL;
}

void psim_cal_tear_next(void)
{
    ensure();
    S.tear_next = true;
}

/* ---- port_sim_ext.h --------------------------------------------------------------------- */

bool psim_reset_requested(void)
{
    ensure();
    return S.reset_requested;
}

uint32_t psim_pending_reset_cause(void)
{
    ensure();
    return S.reset_requested ? S.next_reset_cause : 0u;
}

bool psim_hung(void)
{
    ensure();
    return S.hung;
}

bool psim_cpu_stopped(void)
{
    ensure();
    return S.reset_requested || S.hung;
}

void psim_stall_next_pass(uint64_t us)
{
    ensure();
    S.stall_pending_us += us;
}

void psim_set_in_exception(bool on)
{
    ensure();
    S.in_exception_flag = on;
}

void psim_set_mcu_temp_cdeg(int32_t cdeg)
{
    ensure();
    S.temp_cdeg = cdeg;
}

size_t psim_host_rx_space(void)
{
    ensure();
    return PSIM_HOST_RING_BYTES - s_rx.count;
}

size_t psim_host_tx_pending(void)
{
    ensure();
    return s_tx.count;
}

void psim_observe_ext(psim_ext_obs_t *o)
{
    ensure();
    *o = S.ext;
    o->reset_flags = S.reset_flags;
    o->reset_requested = S.reset_requested;
    o->hung = S.hung;
    o->hung_irqoff = S.hung_irqoff;
}

/* ---- port.h: time ------------------------------------------------------------------------ */

uint64_t port_now_us(void)
{
    ensure();
    uint64_t t = S.now_us;
    spin_poll();
    return t;
}

void port_delay_us(uint32_t us)
{
    ensure();
    if (us > DELAY_BOUND_US) {
        S.ext.delay_over_50us++;
    }
    advance_to(S.now_us + us);
}

bool port_in_exception(void)
{
    ensure();
    return S.in_exception_flag || S.in_isr;
}

/* ---- port.h: main-loop hooks ------------------------------------------------------------- */

void port_pass_begin(void)
{
    ensure();
    S.ext.passes++;
}

void port_pass_end(bool healthy)
{
    ensure();
    if (cpu_stopped()) {
        return;
    }
    consume_stall();
    if (cpu_stopped()) {
        return;
    }
    advance_to(S.now_us + S.opt.pass_cost_us);
    if (cpu_stopped()) {
        return;
    }
    S.deadman_last = S.now_us;
    S.deadman_reloads++;
    if (healthy) {
        S.iwdg_last = S.now_us;
        S.iwdg_kicks++;
    }
}

void port_deadman_enable(bool enable)
{
    ensure();
    if (cpu_stopped()) {
        return;
    }
    if (enable && !S.deadman_enabled) {
        S.deadman_last = S.now_us; /* TIM5 restarts from zero */
    }
    S.deadman_enabled = enable;
}

/* ---- port.h: PC5 --------------------------------------------------------------------------- */

static void hb_set(bool high)
{
    if (high != S.hb_level) {
        S.hb_level = high;
        S.hb_edges++;
        S.hb_last_edge_us = S.now_us;
        board_hb(&S.board, high, S.now_us);
    }
}

void port_hb_write(bool high)
{
    ensure();
    if (cpu_stopped()) {
        return;
    }
    hb_set(high);
}

void port_hb_force_low(void)
{
    ensure();
    if (cpu_stopped()) {
        return;
    }
    hb_set(false);
}

/* ---- port.h: SPI1 -> DAC8552 ------------------------------------------------------------- */

void port_dac_cs(bool asserted)
{
    ensure();
    if (cpu_stopped()) {
        return;
    }
    S.dac_cs = asserted;
    dacm_sync(&S.dac, asserted);
}

bool port_dac_write(const uint8_t *tx, size_t n, uint32_t timeout_us)
{
    ensure();
    if (cpu_stopped()) {
        return true;
    }
    advance_d(SPI1_CALL_OVERHEAD_US);
    if (S.spi1_fail > 0u) {
        S.spi1_fail--;
        S.ext.spi1_failures++;
        advance_to(S.now_us + timeout_us);
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        if (!S.bitbang) {
            dacm_byte(&S.dac, tx[i]); /* in bit-bang mode SPI1 drives no pin */
        }
        S.ext.spi1_bytes++;
        advance_d(8.0 / SPI1_MHZ);
    }
    return true;
}

bool port_dac_wait_idle(uint32_t timeout_us)
{
    ensure();
    if (cpu_stopped()) {
        return true;
    }
    if (S.spi1_fail > 0u) {
        advance_to(S.now_us + timeout_us);
        return false;
    }
    return true;
}

void port_dac_spi_reinit(void)
{
    ensure();
    if (cpu_stopped()) {
        return;
    }
    advance_d(SPI_REINIT_US);
}

void port_dac_bitbang_begin(void)
{
    ensure();
    if (cpu_stopped()) {
        return;
    }
    S.bitbang = true;
    dacm_pins(&S.dac, false, false);
}

void port_dac_bitbang_pins(bool sclk, bool mosi)
{
    ensure();
    if (cpu_stopped() || !S.bitbang) {
        return;
    }
    dacm_pins(&S.dac, sclk, mosi);
}

void port_dac_bitbang_end(void)
{
    ensure();
    if (cpu_stopped()) {
        return;
    }
    if (S.bitbang) {
        dacm_pins(&S.dac, false, false); /* AF5 with SPI idle: SCLK low (mode 1) */
    }
    S.bitbang = false;
}

/* ---- port.h: SPI2 -> ADS1256 ----------------------------------------------------------------- */

void port_adc_cs(bool asserted)
{
    ensure();
    if (cpu_stopped()) {
        return;
    }
    S.adc_cs = asserted;
    adsm_cs(S.ads, asserted, S.now_us);
}

bool port_adc_xfer(const uint8_t *tx, uint8_t *rx, size_t n, uint32_t timeout_us)
{
    ensure();
    if (cpu_stopped()) {
        if (rx != NULL) {
            memset(rx, 0xFF, n);
        }
        return true;
    }
    advance_d(SPI2_CALL_OVERHEAD_US);
    if (S.spi2_fail > 0u) {
        S.spi2_fail--;
        S.ext.spi2_failures++;
        if (rx != NULL) {
            memset(rx, 0x00, n);
        }
        advance_to(S.now_us + timeout_us);
        return false;
    }
    const double period = 1.0 / SPI2_MHZ;
    for (size_t i = 0; i < n; i++) {
        double t0 = now_d();
        double t1 = t0 + 7.5 * period;
        uint8_t r = adsm_byte(S.ads, tx != NULL ? tx[i] : 0x00u, t0, t1);
        if (rx != NULL) {
            rx[i] = r;
        }
        S.ext.spi2_bytes++;
        advance_d(8.0 * period);
    }
    return true;
}

void port_adc_reset_write(bool high)
{
    ensure();
    if (cpu_stopped()) {
        return;
    }
    S.adc_reset_high = high;
    adsm_reset_pin(S.ads, high, S.now_us);
}

bool port_adc_drdy_low(void)
{
    ensure();
    bool low = adsm_drdy_low(S.ads);
    spin_poll();
    return low;
}

void port_adc_drdy_arm(void)
{
    ensure();
    if (cpu_stopped()) {
        return;
    }
    uint64_t t;
    (void)adsm_take_drdy_fall(S.ads, &t); /* discard a fall not yet latched */
    S.exti_latched = false;
    S.exti_armed = true;
}

bool port_adc_drdy_take(uint64_t *t_us)
{
    ensure();
    if (!S.exti_latched) {
        return false;
    }
    S.exti_latched = false;
    if (t_us != NULL) {
        *t_us = S.exti_t;
    }
    return true;
}

/* ---- port.h: host link ------------------------------------------------------------------------ */

static bool host_configured(void)
{
    return S.configured_flag && S.connected && S.vbus_mv >= VBUS_DETACH_MV;
}

static size_t tx_space(void)
{
    size_t free_bytes = PSIM_HOST_RING_BYTES - s_tx.count;
    if (S.tx_limit != PSIM_TX_NO_LIMIT) {
        size_t lim = S.tx_limit > s_tx.count ? S.tx_limit - s_tx.count : 0u;
        if (lim < free_bytes) {
            free_bytes = lim;
        }
    }
    return free_bytes;
}

size_t port_host_read(uint8_t *buf, size_t max)
{
    ensure();
    if (cpu_stopped() || !host_configured()) {
        return 0;
    }
    return ring_pop(&s_rx, buf, max);
}

size_t port_host_write(const uint8_t *buf, size_t n)
{
    ensure();
    if (cpu_stopped() || !host_configured()) {
        S.ext.tx_discarded += n;
        return n;
    }
    size_t space = tx_space();
    return ring_push(&s_tx, buf, n < space ? n : space);
}

size_t port_host_write_space(void)
{
    ensure();
    if (!host_configured()) {
        return PSIM_HOST_RING_BYTES;
    }
    return tx_space();
}

void port_host_flush(void)
{
    ensure();
}

void port_usb_service(void)
{
    ensure();
    if (cpu_stopped()) {
        return;
    }
    consume_stall();
}

uint32_t port_vbus_mv(void)
{
    ensure();
    return S.vbus_mv;
}

bool port_host_configured(void)
{
    ensure();
    return host_configured();
}

bool port_host_dtr(void)
{
    ensure();
    return host_configured() && S.dtr_flag;
}

void port_host_connect(bool connect)
{
    ensure();
    if (cpu_stopped()) {
        return;
    }
    S.connected = connect;
}

/* ---- port.h: calibration flash --------------------------------------------------------------------- */

size_t port_cal_sector_size(void)
{
    return CAL_SECTOR_BYTES;
}

const uint8_t *port_cal_sector(unsigned idx)
{
    ensure();
    return idx < PORT_CAL_SECTORS ? s_cal[idx] : NULL;
}

bool port_cal_erase(unsigned idx)
{
    ensure();
    if (idx >= PORT_CAL_SECTORS || cpu_stopped()) {
        return false;
    }
    S.cal_erases++;
    bool tear = S.tear_next;
    S.tear_next = false;
    advance_until_reset(tear ? PSIM_FLASH_ERASE_US / 2u : PSIM_FLASH_ERASE_US);
    if (tear || S.reset_requested) {
        S.ext.cal_tears++;
        memset(s_cal[idx], 0xFF, CAL_SECTOR_BYTES / 2u);
        return false;
    }
    memset(s_cal[idx], 0xFF, CAL_SECTOR_BYTES);
    return true;
}

bool port_cal_program(unsigned idx, size_t offset, const uint8_t *data, size_t n)
{
    ensure();
    if (idx >= PORT_CAL_SECTORS || offset > CAL_SECTOR_BYTES ||
        n > CAL_SECTOR_BYTES - offset || (n > 0u && data == NULL) || cpu_stopped()) {
        return false;
    }
    S.cal_programs++;
    bool tear = S.tear_next;
    S.tear_next = false;
    size_t todo = tear ? n / 2u : n;
    uint64_t words = (uint64_t)((todo + 3u) / 4u);
    advance_until_reset(words * PSIM_FLASH_WORD_US);
    if (S.reset_requested && !tear) {
        tear = true; /* reset in the middle: only what was programmed so far */
        todo = n / 2u;
    }
    for (size_t i = 0; i < todo; i++) {
        s_cal[idx][offset + i] &= data[i];
    }
    if (tear) {
        S.ext.cal_tears++;
    }
    return !tear;
}

/* ---- port.h: system ---------------------------------------------------------------------------------- */

void port_uid(uint32_t uid[3])
{
    uid[0] = k_uid[0];
    uid[1] = k_uid[1];
    uid[2] = k_uid[2];
}

int port_bor_level(void)
{
    ensure();
    return S.bor;
}

bool port_dbg_tim2_frozen(void)
{
    ensure();
    return S.tim2_frozen;
}

uint32_t port_reset_flags(void)
{
    ensure();
    return S.reset_flags;
}

bool port_hse_ok(void)
{
    ensure();
    return S.hse_ok;
}

bool port_crash_get(crash_record_t *out)
{
    ensure();
    if (!crash_valid()) {
        return false;
    }
    if (out != NULL) {
        *out = s_crash;
    }
    return true;
}

void port_crash_clear(void)
{
    ensure();
    memset(&s_crash, 0, sizeof(s_crash));
}

int32_t port_mcu_temp_cdeg(void)
{
    ensure();
    return S.temp_cdeg;
}

void port_led_write(bool on)
{
    ensure();
    if (cpu_stopped()) {
        return;
    }
    S.led = on;
}

bool port_stack_ok(void)
{
    ensure();
    return S.stack_ok;
}

/* Simulator: records the request and RETURNS (see port_sim_ext.h). */
void port_system_reset(void)
{
    ensure();
    dac_zero_safe();          /* as the target's system.c does */
    S.resets_requested++;
    request_reset(RST_SFT);
}

void port_crash_test(int kind)
{
    ensure();
    if (cpu_stopped()) {
        return;
    }
    switch (kind) {
    case 0: /* HardFault: the shared fault handler (spec §7.9 steps 1-4) */
        S.in_isr = true;
        S.ext.hardfaults++;
        port_hb_force_low();
        dac_zero_safe();
        crash_write(CRASH_HARDFAULT, CRASH_PC_HARDFAULT, CRASH_CFSR_UNDEF, CRASH_HFSR_FORCED);
        port_system_reset();
        S.in_isr = false;
        break;
    case 1: /* hang with interrupts on: dead-man (if enabled) or IWDG */
        S.hung = true;
        break;
    case 2: /* hang with interrupts off: only the IWDG */
        S.hung = true;
        S.hung_irqoff = true;
        break;
    case 3: /* thread-stack overflow into the MPU guard: MemManage, frame unread */
        S.in_isr = true;
        port_hb_force_low();
        dac_zero_safe();
        crash_write(CRASH_MEMMANAGE, 0xFFFFFFFFu, CRASH_CFSR_MSTKERR, 0u);
        port_system_reset();
        S.in_isr = false;
        break;
    default:
        break;
    }
}
