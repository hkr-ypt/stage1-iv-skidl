/*
 * session.c — host session tracking (contract: session.h; protocol.md §1,
 * firmware_spec.md §4.7, §8.2).
 *
 * Facts relied on:
 *  - VBUS reaches PA0 through a 100 k / 100 k divider (blocks/mcu.py); the
 *    port reports VBUS itself (pin x 2) in mV.
 *  - Hardware VBUS sensing is disabled and TinyUSB 0.21.0 never reports an
 *    unplug, so the D+ pull-up follows the PA0 measurement: a self-powered
 *    device must not drive D+ without VBUS (USB 2.0 §7.1.5).
 *  - DTR comes from the CDC SET_CONTROL_LINE_STATE request (pyserial asserts
 *    it on open and drops it on close).
 */
#include "session.h"

#include "commands.h"
#include "config.h"
#include "engine.h"
#include "instrument.h"
#include "port.h"
#include "proto.h"

static struct {
    bool     vbus;            /* debounced VBUS present          */
    bool     above;           /* last reading above attach level */
    uint64_t above_since;
    bool     connected;       /* soft connect state we requested */
    bool     active;
    uint64_t last_rx;
    uint32_t count;
} s_s;

void session_init(void)
{
    s_s.vbus = false;
    s_s.above = false;
    s_s.above_since = 0;
    s_s.connected = false;
    s_s.active = false;
    s_s.last_rx = 0;
    s_s.count = 0;
    port_host_connect(false);
}

bool session_active(void)
{
    return s_s.active;
}

bool session_vbus_present(void)
{
    return s_s.vbus;
}

void session_note_rx(uint64_t now_us)
{
    s_s.last_rx = now_us;
}

uint64_t session_last_rx_us(void)
{
    return s_s.last_rx;
}

uint32_t session_count(void)
{
    return s_s.count;
}

static void update_vbus(uint64_t now)
{
    uint32_t mv = port_vbus_mv();
    if (mv < VBUS_DETACH_MV) {
        s_s.vbus = false;
        s_s.above = false;
        return;
    }
    if (mv > VBUS_ATTACH_MV) {
        if (!s_s.above) {
            s_s.above = true;
            s_s.above_since = now;
        } else if (!s_s.vbus && now - s_s.above_since >= VBUS_DEBOUNCE_US) {
            s_s.vbus = true;
        }
    } else {
        s_s.above = false;   /* between the thresholds: hold the state */
    }
}

void session_service(uint64_t now_us)
{
    update_vbus(now_us);
    /* No clock (F_CLOCK): USB was never started and stays disconnected. */
    bool want_connect = s_s.vbus && port_hse_ok();
    if (want_connect != s_s.connected) {
        port_host_connect(want_connect);
        s_s.connected = want_connect;
    }

    bool active = s_s.connected && port_host_configured() && port_host_dtr();
    if (active && !s_s.active) {
        s_s.active = true;
        s_s.count++;
        s_s.last_rx = now_us;
        engine_zero_mark_stale();    /* "no ZERO this session" (spec §8.5) */
        proto_session_start();
        commands_emit_hello();
    } else if (!active && s_s.active) {
        s_s.active = false;
        proto_session_end();
        inst_disarm(DR_SESSION_LOST);
    }

    uint32_t timeout_s = cfg_get()->host_timeout_s;
    if (s_s.active && timeout_s != 0u && inst_output_armed()
        && now_us - s_s.last_rx > (uint64_t)timeout_s * 1000000u) {
        inst_disarm(DR_HOST_TIMEOUT);
    }
}
