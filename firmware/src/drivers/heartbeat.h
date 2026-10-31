/*
 * heartbeat.h — the only generator of edges on PC5 (SAFE_HB).
 *
 * PC5 drives the R36/C47/D6/C48/R37 charge pump whose output (DAC_ARM) holds
 * the TMUX1101 reference gate closed. Only edges transfer charge; the gate
 * opens 52-77 ms after the last edge (docs/firmware/firmware_spec.md §4.4).
 *
 * Timing model (simulated, see the spec):
 *  - edges on a FIXED 250 us grid (next_edge += 250 us): the fundamental stays
 *    exactly 2 kHz whatever the pass length; never "toggle once per pass"
 *    (uneven duty starves the pump);
 *  - if the loop falls more than one period behind, resynchronise
 *    (next_edge = now + half) and count it, never burst to catch up;
 *  - any spacing between edges above the gap limit (1.0 ms, or half + 0.5 ms
 *    in the bring-up HB.FREQ mode) is a fault: hb_service() returns
 *    HB_GAP_FAULT and the caller enters FAULT.
 *
 * hb_service() must be called once per main-loop pass, in thread mode, from
 * app_pass() only. It asserts thread mode itself.
 */
#ifndef HEARTBEAT_H
#define HEARTBEAT_H

#include "fw_types.h"

typedef enum {
    HB_OK = 0,
    HB_GAP_FAULT,      /* edge spacing exceeded the gap limit while enabled */
    HB_NOT_THREAD,     /* called from an exception handler                   */
} hb_result_t;

typedef struct {
    uint64_t edges;            /* edges generated since boot                  */
    uint32_t max_spacing_us;   /* largest edge spacing while enabled          */
    uint32_t late_spacings;    /* spacings above HB_SPACING_WARN_US           */
    uint32_t resyncs;          /* grid resynchronisations                     */
    uint64_t first_edge_us;    /* time of the first edge after hb_enable      */
    uint64_t last_edge_us;
} hb_stats_t;

void        hb_init(void);           /* PC5 low, disabled */
/* Start generating edges: the first edge happens on the next hb_service()
 * call; last_edge := now so the first spacing check cannot fire spuriously. */
void        hb_enable(void);
/* Stop: PC5 low and left low; records last_edge_us. */
void        hb_disable(void);
bool        hb_enabled(void);
/* One pass. `permitted` false => hb_disable(). Returns HB_GAP_FAULT if the
 * spacing since the last edge exceeded the limit (checked before toggling). */
hb_result_t hb_service(bool permitted);
/* True once the first edge after hb_enable has been generated. */
bool        hb_started(void);
void        hb_get_stats(hb_stats_t *out);
uint32_t    hb_half_period_us(void);
uint32_t    hb_gap_limit_us(void);

#if FW_BRINGUP
/* HB.FREQ: 500 Hz .. 5 kHz -> half period 1000 .. 100 us; the gap limit
 * becomes max(1000, half + 500) us. Returns false if out of range. */
bool        hb_set_frequency_hz(uint32_t hz);
/* HB.EDGE / HB.HOLD: with the heartbeat DISABLED, drive PC5 to a static
 * level through this module (it stays the single PC5 writer). A low->high
 * change is one rising edge: counted in stats.edges, last_edge_us := now.
 * Refused (returns false, nothing written) while enabled. While a hold is
 * active hb_service(false) leaves PC5 at the held level (it is static, so
 * it cannot pump: one edge peaks at 0.54 V < V_IL); hb_bringup_hold(false)
 * or hb_disable() end the hold with PC5 low. The app refuses ARM while a
 * hold is active and for T_DISARM after it ends. */
bool        hb_bringup_hold(bool high);
bool        hb_bringup_holding(void);
#endif

#endif /* HEARTBEAT_H */
