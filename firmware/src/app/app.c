/*
 * app.c — initialisation and the cooperative super-loop pass
 * (contract: app.h; firmware_spec.md §7.1).
 *
 * Pass order (each service bounded, no blocking):
 *   port_pass_begin   time extension, background VBUS/temperature sampling
 *   port_usb_service  tud_task(): queued USB events
 *   session_service   VBUS debounce, soft connect, DTR, host timeout
 *   proto_service     RX, <= 1 command, PT streaming, TX
 *   commands_service  RESET / CRASH once their OK has left
 *   ads_service       <= 1 ADS1256 SPI transaction
 *   engine_service    samples, jobs, sweep, ramp, monitor, DAC scrub
 *   inst_service      health checks (SI-9), state timing
 *   hb_service        the ONLY writer of PC5 edges (SI-2)
 *   deadman, LED
 *   port_pass_end     dead-man reload; IWDG kick if the pass was in budget
 *
 * PC13 LED (sinks: on = low, blocks/mcu.py): off in BOOT, a 50 ms blink every
 * 2 s in DISARMED, on while the heartbeat runs, 4 Hz in FAULT.
 */
#include "app.h"

#include "ads1256.h"
#include "commands.h"
#include "config.h"
#include "dac8552.h"
#include "engine.h"
#include "heartbeat.h"
#include "instrument.h"
#include "port.h"
#include "proto.h"
#include "results.h"
#include "session.h"

static app_stats_t s_stats;
static bool        s_long_op;
static bool        s_led;
static bool        s_led_known;

void app_get_stats(app_stats_t *out)
{
    *out = s_stats;
}

void app_clear_stats(void)
{
    s_stats = (app_stats_t){0};
}

void app_note_long_op(void)
{
    s_long_op = true;
}

void app_init(void)
{
    /* SI-3: the zero pair is written by this boot before anything else; on
     * the target early_safe_init() has already done it once. */
    dac_init();
    dac_zero_safe();
    hb_init();
    cfg_init();
    results_init();
    proto_init();
    commands_init();
    session_init();
    engine_init();      /* calibration load, POST job */
    inst_init();        /* BOOT (or FAULT(F_CLOCK)) */
    s_stats = (app_stats_t){0};
    s_long_op = false;
    s_led_known = false;
}

static void led_service(uint64_t now)
{
    bool on;
    switch (inst_state()) {
    case ST_FAULT:
        on = ((now / 125000u) % 2u) == 0u;
        break;
    case ST_DISARMED:
        on = (now % 2000000u) < 50000u;
        break;
    case ST_ARMING:
    case ST_ARMED:
    case ST_SWEEPING:
    case ST_DISARMING:
        on = hb_enabled();
        break;
    case ST_BOOT:
    case ST_COUNT_:
    default:
        on = false;
        break;
    }
    if (!s_led_known || on != s_led) {
        port_led_write(on);
        s_led = on;
        s_led_known = true;
    }
}

void app_pass(void)
{
    port_pass_begin();
    uint64_t t0 = port_now_us();
    s_long_op = false;

    port_usb_service();
    session_service(port_now_us());
    proto_service(port_now_us());
    commands_service(port_now_us());
    ads_service();
    engine_service(port_now_us());
    inst_service(port_now_us());

    hb_result_t hr = hb_service(inst_hb_permitted());
    if (hr == HB_GAP_FAULT) {
        inst_fault(F_LOOP_OVERRUN, "hb_gap");
    } else if (hr == HB_NOT_THREAD) {
        inst_fault(F_INVARIANT, "hb_thread");
    }
    port_deadman_enable(hb_enabled());
    uint64_t t1 = port_now_us();
    led_service(t1);

    uint64_t d = t1 - t0;
    uint32_t dur = d > UINT32_MAX ? UINT32_MAX : (uint32_t)d;
    s_stats.passes++;
    bool healthy = true;
    if (!s_long_op) {
        if (dur > s_stats.pass_max_us) {
            s_stats.pass_max_us = dur;
        }
        if (dur > PASS_OVERRUN_COUNT_US) {
            s_stats.pass_overruns++;
            healthy = false;
        }
    }
    port_pass_end(healthy);
}
