/*
 * tp_ads.c — virtual-clock test port for test_ads1256 (see tp_ads.h).
 * SPI2 timing per docs/firmware/firmware_spec.md §4.5: mode 1, SCLK 1.5 MHz.
 */
#include "ads1256/tp_ads.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "ads1256.h"
#include "port.h"

#define SCLK_PERIOD_US (1.0 / 1.5)
#define BYTE_US        (8.0 * SCLK_PERIOD_US)

static adsm_t  *g_m;
static uint64_t g_now;
static bool     g_pending, g_armed, g_latched, g_arm_clears = true;
static uint64_t g_latched_t;
static uint32_t g_fail;
static uint32_t g_pass_lo = 5, g_pass_hi = 150;
static uint64_t g_rng = 1;
static uint64_t g_reset_low_t;
static tp_obs_t g_obs;
static uint32_t g_read_preempt_us;
static uint32_t g_drdy_latency_us;

static void edge(uint64_t t)
{
    g_pending = true;
    if (g_armed && !g_latched) {
        g_latched = true;           /* ISR: timestamp, mask, clear pending */
        g_latched_t = t + g_drdy_latency_us;
        g_armed = false;
        g_pending = false;
    }
}

static void sync_model(void)
{
    uint64_t t;
    adsm_advance(g_m, g_now);
    if (adsm_take_drdy_fall(g_m, &t)) {
        edge(t);
    }
}

void tp_setup(adsm_input_fn fn, void *ctx)
{
    if (g_m) {
        adsm_free(g_m);
    }
    g_m = adsm_new(fn, ctx);
    g_now = 0;
    g_pending = g_armed = g_latched = false;
    g_arm_clears = true;
    g_fail = 0;
    g_read_preempt_us = 0;
    g_drdy_latency_us = 0;
    memset(&g_obs, 0, sizeof g_obs);
    g_obs.min_reset_low_us = UINT64_MAX;
    adsm_power_on(g_m, 0);
}

void tp_teardown(void)
{
    adsm_free(g_m);
    g_m = NULL;
}

adsm_t *tp_model(void)
{
    return g_m;
}

uint64_t tp_now(void)
{
    return g_now;
}

void tp_advance(uint64_t us)
{
    g_now += us;
    sync_model();
}

void tp_set_pass(uint32_t lo_us, uint32_t hi_us, uint32_t seed)
{
    g_pass_lo = lo_us;
    g_pass_hi = hi_us;
    g_rng = ((uint64_t)seed << 1) | 1u;
}

uint32_t tp_rand(uint32_t lo, uint32_t hi)
{
    g_rng ^= g_rng << 13;
    g_rng ^= g_rng >> 7;
    g_rng ^= g_rng << 17;
    if (hi <= lo) {
        return lo;
    }
    return lo + (uint32_t)(g_rng % (uint64_t)(hi - lo + 1u));
}

void tp_service(void)
{
    uint64_t t0 = g_now;
    uint32_t c0 = g_obs.cs_asserts;
    ads_service();
    if (g_now - t0 > g_obs.max_service_us) {
        g_obs.max_service_us = g_now - t0;
    }
    if (g_obs.cs_asserts - c0 > g_obs.max_txn_per_service) {
        g_obs.max_txn_per_service = g_obs.cs_asserts - c0;
    }
}

void tp_pass(void)
{
    tp_service();
    tp_advance(tp_rand(g_pass_lo, g_pass_hi));
}

void tp_fail_xfers(uint32_t n)
{
    g_fail = n;
}

void tp_set_arm_clears_pending(bool clears)
{
    g_arm_clears = clears;
}

void tp_set_read_preempt_us(uint32_t us)
{
    g_read_preempt_us = us;
}

void tp_set_drdy_latency_us(uint32_t us)
{
    g_drdy_latency_us = us;
}

void tp_inject_edge(void)
{
    edge(g_now);
}

bool tp_edge_latched(uint64_t *t)
{
    if (g_latched && t) {
        *t = g_latched_t;
    }
    return g_latched;
}

void tp_observe(tp_obs_t *o)
{
    *o = g_obs;
}

/* ---- port.h ------------------------------------------------------------------- */

uint64_t port_now_us(void)
{
    return g_now;
}

void port_delay_us(uint32_t us)
{
    if (us > g_obs.max_delay_arg) {
        g_obs.max_delay_arg = us;
    }
    g_now += us;
    sync_model();
}

void port_adc_cs(bool asserted)
{
    if (asserted && !g_obs.cs_asserted) {
        g_obs.cs_asserts++;
    }
    g_obs.cs_asserted = asserted;
    adsm_cs(g_m, asserted, g_now);
}

bool port_adc_xfer(const uint8_t *tx, uint8_t *rx, size_t n, uint32_t timeout_us)
{
    g_obs.xfers++;
    if (!g_obs.cs_asserted) {
        g_obs.xfer_without_cs++;
    }
    if (g_fail > 0) {
        g_fail--;
        g_obs.spi_fail_injected++;
        g_now += timeout_us;
        sync_model();
        return false;
    }
    if (n == 3u && rx != NULL && g_read_preempt_us > 0u) {
        g_now += g_read_preempt_us;   /* an ISR between RDATA and the data bytes */
        sync_model();
    }
    double t0 = (double)g_now;
    for (size_t i = 0; i < n; i++) {
        double ts = t0 + (double)i * BYTE_US;
        double te = ts + 7.5 * SCLK_PERIOD_US;
        uint8_t r = adsm_byte(g_m, tx ? tx[i] : 0u, ts, te);
        if (rx) {
            rx[i] = r;
        }
    }
    g_now = (uint64_t)ceil(t0 + (double)n * BYTE_US - 1e-9);
    sync_model();
    return true;
}

void port_adc_reset_write(bool high)
{
    if (!high) {
        g_reset_low_t = g_now;
    } else {
        g_obs.reset_pulses++;
        if (g_now - g_reset_low_t < g_obs.min_reset_low_us) {
            g_obs.min_reset_low_us = g_now - g_reset_low_t;
        }
    }
    adsm_reset_pin(g_m, high, g_now);
    sync_model();
}

bool port_adc_drdy_low(void)
{
    sync_model();
    return adsm_drdy_low(g_m);
}

void port_adc_drdy_arm(void)
{
    g_obs.arms++;
    if (g_arm_clears) {
        g_pending = false;
    }
    g_latched = false;
    g_armed = true;
    if (g_pending) {
        edge(g_now);               /* pending at unmask: ISR runs now */
    }
}

bool port_adc_drdy_take(uint64_t *t_us)
{
    if (!g_latched) {
        return false;
    }
    g_latched = false;
    *t_us = g_latched_t;
    return true;
}
