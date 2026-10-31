/*
 * commands.h — the command set of protocol v1 (docs/firmware/protocol.md §5):
 * argument validation, state gating, responses, deferred completion, and
 * the HELLO event.
 */
#ifndef COMMANDS_H
#define COMMANDS_H

#include "proto.h"

void commands_init(void);
/* Execute one tokenised command. Returns true with the complete response
 * line ("OK ..." or "ERR ...") in *out, or false if the command is deferred
 * (MEAS?, ZERO, ADC.SELFCAL, SELFTEST): its response comes from
 * commands_poll_deferred(). */
bool commands_execute(const cmdline_t *cl, lb_t *out);
bool commands_deferred_pending(void);
bool commands_poll_deferred(lb_t *out);
void commands_cancel_deferred(void);
/* Response line for a line-level error (bad characters, too long, ...). */
void commands_format_error(lb_t *out, err_t e, const char *msg);
/* EVT HELLO (session start). */
void commands_emit_hello(void);
/* Runs a pending RESET / CRASH once its response has left the TX ring. */
void commands_service(uint64_t now_us);

#endif /* COMMANDS_H */
