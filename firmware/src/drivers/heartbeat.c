/*
 * heartbeat.c — the only generator of edges on PC5 (SAFE_HB); implements
 * heartbeat.h.
 *
 * Facts relied on (docs/firmware/firmware_spec.md §4.4, §7.1, §7.4):
 *  - PC5 drives the R36/C47/D6/C48/R37 charge pump that holds the TMUX1101
 *    reference gate (U11) closed; only edges transfer charge. Even toggling
 *    at 2 kHz keeps DAC_ARM >= 2.15 V worst case; an uneven duty (toggling
 *    once per pass) starves it, so edges come from a time base: a fixed
 *    HB_HALF_PERIOD_US (250 us) grid, next_edge += half, which keeps the
 *    fundamental at exactly 2 kHz (a sinc notch of every ADS1256 rate used).
 *  - The worst-case trough falls below V_IH 1.49 V (TMUX1101 SCDS410D
 *    Table 8-1) only for sustained half periods above 1.55 ms, so any spacing
 *    above HB_GAP_FAULT_US (1.0 ms) while enabled is a fault, checked BEFORE
 *    toggling (a late pass never produces the edge that would hide the gap).
 *  - The gate opens 52-63 ms after the last edge with PC5 low, up to 77 ms
 *    with PC5 left high: disable always drives PC5 low and leaves it low.
 *  - SI-2/SI-10: edges only in thread mode; an exception-context call
 *    (port_in_exception()) touches nothing and returns HB_NOT_THREAD.
 *
 * Scheduling details:
 *  - hb_enable(): gap reference := now, first edge on the next hb_service();
 *    the grid is anchored at that first edge (next_edge := edge + half), so
 *    the first half period is a full one and is never counted as a resync.
 *  - Afterwards an edge is made in the first pass with now >= next_edge,
 *    then next_edge += half. If now >= next_edge + half (more than a half
 *    period late) the edge is still made but next_edge := now + half and the
 *    resync is counted: at most one edge per pass, never a catch-up burst.
 *  - hb_disable(): PC5 low; if PC5 was high that falling edge is the last
 *    edge (counted, last_edge_us := now); if it was already low the last
 *    edge time is kept, so repeated hb_service(false) passes never move it.
 *  - Spacing statistics cover scheduled edge-to-edge spacings only (not the
 *    enable -> first edge interval and not the falling edge of a disable).
 *  - Bring-up HB.EDGE / HB.HOLD (FW_BRINGUP only): hb_bringup_hold() drives a
 *    static PC5 level while the heartbeat is disabled (refused while
 *    enabled). A low->high change is one counted edge (last_edge_us := now);
 *    a static level cannot pump (one edge peaks at 0.54 V < V_IL 0.87 V,
 *    spec §4.4). While holding, hb_service(false) leaves PC5 alone;
 *    hb_bringup_hold(false), hb_disable(), hb_init() and hb_enable() end the
 *    hold with PC5 low (hb_enable then starts normally).
 */
#include "heartbeat.h"

#include "port.h"

#define HB_HZ_MIN            500u
#define HB_HZ_MAX            5000u
#define HB_GAP_MARGIN_US     500u

static struct {
    bool       enabled;
    bool       started;
    bool       level;          /* last level written to PC5 */
    uint64_t   next_edge_us;
    uint64_t   gap_ref_us;     /* last edge, or the hb_enable() time */
    uint32_t   half_us;
    uint32_t   gap_limit_us;
    hb_stats_t stats;
#if FW_BRINGUP
    bool       holding;        /* HB.EDGE / HB.HOLD static level active */
#endif
} s_hb;

void hb_init(void)
{
    port_hb_write(false);
    s_hb.enabled = false;
    s_hb.started = false;
    s_hb.level = false;
    s_hb.next_edge_us = 0;
    s_hb.gap_ref_us = 0;
    s_hb.half_us = HB_HALF_PERIOD_US;
    s_hb.gap_limit_us = HB_GAP_FAULT_US;
    s_hb.stats = (hb_stats_t){0};
#if FW_BRINGUP
    s_hb.holding = false;
#endif
}

void hb_enable(void)
{
    if (s_hb.enabled) {
        return; /* re-enabling must not reset the gap reference */
    }
#if FW_BRINGUP
    if (s_hb.holding) {
        hb_disable(); /* end the static hold first: PC5 low */
    }
#endif
    uint64_t now = port_now_us();
    s_hb.enabled = true;
    s_hb.started = false;
    s_hb.gap_ref_us = now;
    s_hb.next_edge_us = now;
}

void hb_disable(void)
{
    bool was_high = s_hb.level;
    port_hb_write(false);
    s_hb.level = false;
    if (was_high) {
        uint64_t now = port_now_us();
        s_hb.stats.edges++;
        s_hb.stats.last_edge_us = now;
    }
    s_hb.enabled = false;
    s_hb.started = false;
#if FW_BRINGUP
    s_hb.holding = false;
#endif
}

bool hb_enabled(void)
{
    return s_hb.enabled;
}

bool hb_started(void)
{
    return s_hb.started;
}

static void hb_edge(uint64_t now)
{
    s_hb.level = !s_hb.level;
    port_hb_write(s_hb.level);
    s_hb.stats.edges++;
    if (s_hb.started) {
        uint64_t spacing = now - s_hb.gap_ref_us;
        uint32_t sp32 = (spacing > UINT32_MAX) ? UINT32_MAX : (uint32_t)spacing;
        if (sp32 > s_hb.stats.max_spacing_us) {
            s_hb.stats.max_spacing_us = sp32;
        }
        if (sp32 > HB_SPACING_WARN_US) {
            s_hb.stats.late_spacings++;
        }
    } else {
        s_hb.started = true;
        s_hb.stats.first_edge_us = now;
    }
    s_hb.gap_ref_us = now;
    s_hb.stats.last_edge_us = now;
}

hb_result_t hb_service(bool permitted)
{
    if (port_in_exception()) {
        return HB_NOT_THREAD;
    }
    if (!permitted) {
#if FW_BRINGUP
        if (s_hb.holding) {
            return HB_OK; /* HB.HOLD: the static level stays */
        }
#endif
        hb_disable();
        return HB_OK;
    }
    if (!s_hb.enabled) {
        return HB_OK;
    }
    uint64_t now = port_now_us();
    if (now - s_hb.gap_ref_us > s_hb.gap_limit_us) {
        return HB_GAP_FAULT;
    }
    if (!s_hb.started) {
        hb_edge(now);
        s_hb.next_edge_us = now + s_hb.half_us;
    } else if (now >= s_hb.next_edge_us) {
        if (now >= s_hb.next_edge_us + s_hb.half_us) {
            s_hb.next_edge_us = now + s_hb.half_us;
            s_hb.stats.resyncs++;
        } else {
            s_hb.next_edge_us += s_hb.half_us;
        }
        hb_edge(now);
    }
    return HB_OK;
}

void hb_get_stats(hb_stats_t *out)
{
    *out = s_hb.stats;
}

uint32_t hb_half_period_us(void)
{
    return s_hb.half_us;
}

uint32_t hb_gap_limit_us(void)
{
    return s_hb.gap_limit_us;
}

#if FW_BRINGUP
bool hb_set_frequency_hz(uint32_t hz)
{
    if (hz < HB_HZ_MIN || hz > HB_HZ_MAX) {
        return false;
    }
    /* half period = 1 / (2 hz), rounded to the nearest microsecond */
    uint32_t half = (500000u + hz / 2u) / hz;
    uint32_t gap = half + HB_GAP_MARGIN_US;
    s_hb.half_us = half;
    s_hb.gap_limit_us = (gap > HB_GAP_FAULT_US) ? gap : HB_GAP_FAULT_US;
    return true;
}

bool hb_bringup_hold(bool high)
{
    if (s_hb.enabled) {
        return false;
    }
    if (high) {
        if (!s_hb.level) {
            uint64_t now = port_now_us();
            s_hb.level = true;
            port_hb_write(true);
            s_hb.stats.edges++;
            s_hb.stats.last_edge_us = now;
        }
        s_hb.holding = true;
    } else {
        hb_disable(); /* PC5 low, a high->low change counted; ends the hold */
    }
    return true;
}

bool hb_bringup_holding(void)
{
    return s_hb.holding;
}
#endif
