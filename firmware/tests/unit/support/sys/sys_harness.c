/*
 * sys_harness.c — see sys_harness.h. Host-only test support (malloc allowed).
 */
#include "sys_harness.h"

#include "app.h"
#include "instrument.h"
#include "model_board.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define LINE_MAX_LEN 1024

static char  **s_lines;
static size_t  s_n, s_cap;
static char    s_part[LINE_MAX_LEN];
static size_t  s_part_len;

static void add_line(const char *s, size_t n)
{
    if (s_n == s_cap) {
        s_cap = s_cap ? 2 * s_cap : 1024;
        s_lines = realloc(s_lines, s_cap * sizeof *s_lines);
    }
    char *c = malloc(n + 1);
    memcpy(c, s, n);
    c[n] = '\0';
    s_lines[s_n++] = c;
}

static void drain(void)
{
    uint8_t buf[4096];
    size_t n;
    while ((n = psim_host_take_tx(buf, sizeof buf)) > 0) {
        for (size_t i = 0; i < n; i++) {
            if (buf[i] == '\n') {
                add_line(s_part, s_part_len);
                s_part_len = 0;
            } else if (s_part_len < LINE_MAX_LEN - 1) {
                s_part[s_part_len++] = (char)buf[i];
            }
        }
    }
}

static void step(void)
{
    if (psim_hung()) {
        psim_advance_us(100);
    } else {
        app_pass();
    }
    drain();   /* bytes already sent reach the host before a reset */
    if (psim_reset_requested()) {
        psim_mcu_reset();
        app_init();
    }
}

void h_boot_opts(const psim_options_t *o)
{
    for (size_t i = 0; i < s_n; i++) {
        free(s_lines[i]);
    }
    s_n = 0;
    s_part_len = 0;
    psim_power_on(o);
    app_init();
    (void)h_run_until_state(ST_DISARMED, 2000000);
}

void h_boot(void)
{
    h_boot_opts(NULL);
}

void h_run_us(uint64_t us)
{
    uint64_t end = psim_time_us() + us;
    while (psim_time_us() < end) {
        step();
    }
}

bool h_run_until(bool (*pred)(void), uint64_t timeout_us)
{
    uint64_t end = psim_time_us() + timeout_us;
    while (!pred()) {
        if (psim_time_us() >= end) {
            return false;
        }
        step();
    }
    return true;
}

static inst_state_t s_want;
static bool state_is(void)
{
    return inst_state() == s_want;
}

bool h_run_until_state(inst_state_t st, uint64_t timeout_us)
{
    s_want = st;
    return h_run_until(state_is, timeout_us);
}

void h_send(const char *line)
{
    psim_host_give_rx((const uint8_t *)line, strlen(line));
    psim_host_give_rx((const uint8_t *)"\n", 1);
}

size_t h_lines(void)
{
    return s_n;
}

const char *h_line(size_t i)
{
    return i < s_n ? s_lines[i] : "";
}

size_t h_find(const char *prefix, size_t from)
{
    size_t pl = strlen(prefix);
    for (size_t i = from; i < s_n; i++) {
        if (strncmp(s_lines[i], prefix, pl) == 0) {
            return i;
        }
    }
    return SIZE_MAX;
}

bool h_wait_line(const char *prefix, size_t from, uint64_t timeout_us)
{
    uint64_t end = psim_time_us() + timeout_us;
    while (h_find(prefix, from) == SIZE_MAX) {
        if (psim_time_us() >= end) {
            return false;
        }
        step();
    }
    return true;
}

const char *h_cmd(const char *line)
{
    static char resp[LINE_MAX_LEN];
    size_t from = s_n;
    h_send(line);
    uint64_t end = psim_time_us() + 15000000u;
    for (;;) {
        for (size_t i = from; i < s_n; i++) {
            if (strncmp(s_lines[i], "OK", 2) == 0 || strncmp(s_lines[i], "ERR", 3) == 0) {
                snprintf(resp, sizeof resp, "%s", s_lines[i]);
                return resp;
            }
        }
        if (psim_time_us() >= end) {
            resp[0] = '\0';
            return resp;
        }
        step();
    }
}

bool h_kv(const char *line, const char *key, char *out, size_t cap)
{
    size_t kl = strlen(key);
    const char *p = line;
    while ((p = strstr(p, key)) != NULL) {
        bool start = (p == line) || p[-1] == ' ';
        if (start && p[kl] == '=') {
            const char *v = p + kl + 1;
            size_t n = strcspn(v, " ");
            if (n >= cap) {
                n = cap - 1;
            }
            memcpy(out, v, n);
            out[n] = '\0';
            return true;
        }
        p += kl;
    }
    return false;
}

long long h_kv_ll(const char *line, const char *key)
{
    char v[64];
    if (!h_kv(line, key, v, sizeof v)) {
        return -999999999LL;
    }
    return strtoll(v, NULL, 0);
}

double h_kv_d(const char *line, const char *key)
{
    char v[64];
    if (!h_kv(line, key, v, sizeof v)) {
        return -1e300;
    }
    return strtod(v, NULL);
}

double h_vsweep(void)
{
    return board_vsweep(psim_board());
}

bool h_gate(void)
{
    return psim_board()->gate_on;
}

uint16_t h_dac_a(void)
{
    return psim_dac()->dac[0];
}

uint16_t h_dac_b(void)
{
    return psim_dac()->dac[1];
}

uint64_t h_hb_edges(void)
{
    psim_obs_t o;
    psim_observe(&o);
    return o.hb_edges;
}

inst_state_t h_state(void)
{
    return inst_state();
}

uint64_t h_run_measure_hb(uint64_t us)
{
    uint64_t end = psim_time_us() + us;
    psim_obs_t o;
    psim_observe(&o);
    uint64_t last_n = o.hb_edges, last_t = o.hb_last_edge_us, max_gap = 0;
    while (psim_time_us() < end) {
        step();
        psim_observe(&o);
        if (o.hb_edges != last_n) {
            if (last_n != 0 && o.hb_last_edge_us - last_t > max_gap) {
                max_gap = o.hb_last_edge_us - last_t;
            }
            last_n = o.hb_edges;
            last_t = o.hb_last_edge_us;
        }
    }
    return max_gap;
}

void h_jacks_open(bool open)
{
    board_model_t *b = psim_board();
    b->sense_plus_open = open;
    b->sense_minus_open = open;
    b->force_open = open;
    if (open) {
        b->dut.kind = DUT_OPEN;
    } else {
        b->dut.kind = DUT_RESISTOR;
        b->dut.r_ohm = 1000.0;
    }
}
