/*
 * hb_test_port.c — fake clock, PC5 recording and port_in_exception() for
 * the heartbeat unit test. See hb_test_port.h.
 */
#include "heartbeat/hb_test_port.h"

#include <string.h>

#include "port.h"

htp_t htp;

void htp_reset(void)
{
    memset(&htp, 0, sizeof(htp));
    htp.now = 1000000u;
}

uint64_t port_now_us(void)
{
    htp.now_calls++;
    return htp.now;
}

bool port_in_exception(void)
{
    return htp.in_exception;
}

void port_hb_write(bool high)
{
    htp.writes++;
    if (high) {
        htp.writes_high++;
    }
    if (htp.in_exception) {
        htp.writes_in_exception++;
    }
    if (high != htp.level) {
        htp.level = high;
        htp.last_change_us = htp.now;
        if (high) {
            htp.rises++;
        } else {
            htp.falls++;
        }
        if (htp.on_edge != NULL) {
            htp.on_edge(high, htp.now);
        }
    }
}

void port_hb_force_low(void)
{
    port_hb_write(false);
}
