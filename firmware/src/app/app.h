/*
 * app.h — the portable application: initialisation and one super-loop pass.
 *
 * Both platforms run the same loop:
 *
 *   target main():  early_safe_init(); clocks; port_init(); app_init();
 *                   for (;;) app_pass();
 *   sim main():     psim_power_on(); app_init(); loop { app_pass(); io(); }
 *
 * app_init() (re)initialises EVERY module's state explicitly, so the
 * simulator can emulate an MCU reset by calling it again.
 */
#ifndef APP_H
#define APP_H

#include "fw_types.h"

void app_init(void);
void app_pass(void);

/* Diagnostics shared with DIAG? */
typedef struct {
    uint32_t passes;
    uint32_t pass_max_us;
    uint32_t pass_overruns;     /* passes longer than PASS_OVERRUN_COUNT_US */
} app_stats_t;
void app_get_stats(app_stats_t *out);
void app_clear_stats(void);
/* The current pass performs a permitted long operation (CAL.SAVE flash
 * erase, spec §7.1): it is not counted as an overrun. */
void app_note_long_op(void);

#endif /* APP_H */
