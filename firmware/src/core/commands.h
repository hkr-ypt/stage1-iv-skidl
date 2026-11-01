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

/* DISARM and SWP.ABORT (without arguments) take effect as soon as their line
 * is in the RX ring while command parsing is held back (a deferred command,
 * or no room for responses and events): proto.c spots such lines; they are
 * still executed and answered later, in order. */
typedef enum { URGENT_NONE = 0, URGENT_DISARM, URGENT_ABORT, URGENT_COUNT } urgent_t;
urgent_t commands_urgent_kind(const char *line, size_t len);
/* Acts on one such line now. True if it did, for SWP.ABORT only when a sweep
 * was aborted: that line is then answered OK without executing it again. */
bool     commands_urgent_act(urgent_t k);

#endif /* COMMANDS_H */
