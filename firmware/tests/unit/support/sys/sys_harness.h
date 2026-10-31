/*
 * sys_harness.h — drives the whole firmware (app_init/app_pass) on the
 * simulation port for the test_sys_* system tests: runs passes, emulates
 * resets and hangs exactly like sim_main.c, and collects every line the
 * firmware sends to the host.
 */
#ifndef SYS_HARNESS_H
#define SYS_HARNESS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "fw_types.h"
#include "port_sim.h"
#include "port_sim_ext.h"

void        h_boot(void);                         /* power on, run to DISARMED */
void        h_boot_opts(const psim_options_t *o);
void        h_run_us(uint64_t us);
/* Run until pred() is true; false on timeout. */
bool        h_run_until(bool (*pred)(void), uint64_t timeout_us);
bool        h_run_until_state(inst_state_t st, uint64_t timeout_us);
void        h_send(const char *line);             /* line without newline */
/* Send a command and run until its OK/ERR line arrives (<= 15 s virtual).
 * Returns the response (static storage) or "" on timeout. */
const char *h_cmd(const char *line);
/* Lines received so far. */
size_t      h_lines(void);
const char *h_line(size_t i);
/* First line at index >= from starting with prefix; SIZE_MAX if none. */
size_t      h_find(const char *prefix, size_t from);
/* Run until a line starting with prefix arrives after index `from`. */
bool        h_wait_line(const char *prefix, size_t from, uint64_t timeout_us);
/* Value of key=value in a line (as text); false if absent. */
bool        h_kv(const char *line, const char *key, char *out, size_t cap);
long long   h_kv_ll(const char *line, const char *key);
double      h_kv_d(const char *line, const char *key);

double      h_vsweep(void);
bool        h_gate(void);
uint16_t    h_dac_a(void);
uint16_t    h_dac_b(void);
uint64_t    h_hb_edges(void);
inst_state_t h_state(void);
/* Run passes and return the largest heartbeat edge spacing seen (us). */
uint64_t    h_run_measure_hb(uint64_t us);
/* All four jacks open (DUT removed) or restored with a resistor DUT. */
void        h_jacks_open(bool open);

#endif /* SYS_HARNESS_H */
