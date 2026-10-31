/*
 * proto.h — the line protocol transport (docs/firmware/protocol.md §1-§3, §6,
 * §7): receive ring and line assembly, tokeniser, the transmit ring with its
 * priority rules, event delivery and PT record streaming.
 *
 * Output priorities (the sweep never waits for USB, protocol.md §7):
 *  1. Command responses are never dropped: a command is only parsed while
 *     the transmit ring has room for its response plus the events it may
 *     emit (PROTO_RESP_MAX + PROTO_HOLD_MAX).
 *  2. Events are dropped, counted and reported later (EVT WARN EVT_LOST)
 *     when the ring is full. Events emitted while a command executes are
 *     held and sent after its response, so "OK" always precedes the events
 *     the command caused.
 *  3. PT records stream from the result ring only while a margin for 1 and
 *     2 stays free; records are never dropped (results.h).
 * Nothing is sent while no host session exists (session.h); session start
 * discards stale input and output.
 */
#ifndef PROTO_H
#define PROTO_H

#include "fw_types.h"
#include "results.h"

/* ---- line builder (bounded, never overflows; sets `overflow` instead) ---- */
typedef struct {
    char  *buf;
    size_t cap;        /* including the NUL */
    size_t len;
    bool   overflow;
} lb_t;

void        lb_init(lb_t *b, char *buf, size_t cap);
void        lb_reset(lb_t *b);
void        lb_putc(lb_t *b, char c);
void        lb_puts(lb_t *b, const char *s);
void        lb_putn(lb_t *b, const char *s, size_t n);
void        lb_u64(lb_t *b, uint64_t v);
void        lb_i64(lb_t *b, int64_t v);
void        lb_fix(lb_t *b, int64_t v, unsigned frac);  /* v * 10^-frac          */
void        lb_hex(lb_t *b, uint32_t v, unsigned digits);/* "0x" + digits hex     */
void        lb_key(lb_t *b, const char *key);           /* " key="                */
/* Convenience: " key=<value>" */
void        lb_kv_u(lb_t *b, const char *key, uint64_t v);
void        lb_kv_i(lb_t *b, const char *key, int64_t v);
void        lb_kv_fix(lb_t *b, const char *key, int64_t v, unsigned frac);
void        lb_kv_s(lb_t *b, const char *key, const char *s);
void        lb_kv_hex(lb_t *b, const char *key, uint32_t v, unsigned digits);

/* ---- tokeniser (pure) ---------------------------------------------------- */
typedef struct {
    const char *s;
    uint16_t    n;
} tok_t;

#define CMD_MAX_POS 24u
#define CMD_MAX_KV  16u

typedef struct {
    tok_t   verb;
    tok_t   pos[CMD_MAX_POS];
    uint8_t npos;
    tok_t   key[CMD_MAX_KV];
    tok_t   val[CMD_MAX_KV];
    uint8_t nkv;
} cmdline_t;

typedef enum {
    TOK_OK = 0,
    TOK_EMPTY,      /* blank line: ignored                                    */
    TOK_SYNTAX,     /* unbalanced '=' (empty key or value, two '=' in a token) */
    TOK_TOO_MANY,   /* more than CMD_MAX_POS positional or CMD_MAX_KV keys     */
} tok_status_t;

/* Split `line[0..len)` at spaces/tabs into a verb, positional arguments and
 * key=value pairs. Tokens point into `line`. */
tok_status_t proto_tokenize(const char *line, size_t len, cmdline_t *out);
/* Case-insensitive comparison of a token with a NUL-terminated keyword. */
bool         tok_eq(tok_t t, const char *s);

/* ---- service -------------------------------------------------------------- */
#define PROTO_HOLD_MAX     1024u   /* events held during one command          */
#define PROTO_AFTER_MAX    1024u   /* events waiting for pending PT records   */
#define PROTO_PT_LINE_MAX  200u
#define PROTO_EVT_RESERVE  256u

void proto_init(void);
void proto_service(uint64_t now_us);
/* Session edges (called by session.c). Start: discard stale RX/TX, skip
 * unsent records, cancel a deferred command; end: discard RX/TX. */
void proto_session_start(void);
void proto_session_end(void);
bool proto_session_open(void);

/* Queue an event line (without the newline). Dropped silently when no
 * session is open; dropped and counted when the transmit ring is full. */
void proto_event(const char *line, size_t len);
void proto_event_lb(const lb_t *b);
/* Like proto_event(), but sent only after every PT record pushed so far has
 * been formatted: SWEEP_END and STATE events never overtake the records they
 * conclude. */
void proto_event_ordered(const lb_t *b);

/* Format one PT record (protocol.md §6) with the active calibration and
 * session zero. Returns the line length (0 if `cap` is too small). */
size_t proto_format_pt(const pt_rec_t *r, char *buf, size_t cap);

typedef struct {
    uint32_t evt_lost;       /* events dropped since the last clear          */
    uint32_t rx_overflows;   /* input lines discarded for being too long     */
    uint32_t lines_in;       /* command lines executed                       */
    uint32_t pt_sent;        /* PT lines formatted                           */
} proto_stats_t;
void proto_get_stats(proto_stats_t *out);
void proto_clear_stats(void);
/* True when nothing is waiting to be written to the host link. */
bool proto_tx_empty(void);

#endif /* PROTO_H */
