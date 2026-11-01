/*
 * proto.c — line protocol transport (contract: proto.h, protocol.md §1-§3,
 * §6, §7). No hardware facts; the host link is port_host_* (USB CDC on the
 * target, a pty in the simulator).
 *
 * Per pass (all bounded):
 *  - move up to PROTO_RX_RING free bytes from the port into the RX ring
 *    (every byte counts as host activity);
 *  - complete a deferred command, or parse and execute at most one line;
 *  - stream at most PT_PER_PASS records;
 *  - move up to TX_CHUNK bytes from the TX ring to the port.
 */
#include "proto.h"

#include "commands.h"
#include "decimal.h"
#include "engine.h"
#include "port.h"
#include "scaling.h"
#include "session.h"

#include <string.h>

#define PT_PER_PASS          2u
#define TX_CHUNK             512u
#define RX_DISCARD_CHUNK     256u
/* A command is parsed only with room for its response and held events. */
#define CMD_MIN_FREE         (PROTO_RESP_MAX + PROTO_HOLD_MAX)
/* PT lines leave room for a command and some events. */
#define PT_MIN_FREE          (CMD_MIN_FREE + PROTO_PT_LINE_MAX + PROTO_EVT_RESERVE)
#define EVT_LOST_LINE_MAX    48u
/* scanner for DISARM / SWP.ABORT lines: blanks, the verb (at most
 * URG_VERB_MAX characters), blanks; anything else is not urgent */
#define URG_VERB_MAX         12u
enum { URG_LEAD = 0, URG_VERB, URG_POST, URG_NO };

#if (PROTO_TX_RING < PT_MIN_FREE + PROTO_PT_LINE_MAX)
#error "PROTO_TX_RING too small for the output priorities"
#endif
#if (PROTO_AFTER_MAX < PROTO_AFTER_RESERVE + 2u * (PROTO_AFTER_HDR + 160u))
#error "PROTO_AFTER_MAX too small for its command reserve"
#endif

/* ---- line builder ------------------------------------------------------------ */

void lb_init(lb_t *b, char *buf, size_t cap)
{
    b->buf = buf;
    b->cap = cap;
    b->len = 0;
    b->overflow = false;
    if (cap > 0) {
        buf[0] = '\0';
    }
}

void lb_reset(lb_t *b)
{
    b->len = 0;
    b->overflow = false;
    if (b->cap > 0) {
        b->buf[0] = '\0';
    }
}

void lb_putn(lb_t *b, const char *s, size_t n)
{
    if (b->cap == 0) {
        b->overflow = true;
        return;
    }
    size_t room = b->cap - 1u - b->len;
    if (n > room) {
        n = room;
        b->overflow = true;
    }
    memcpy(b->buf + b->len, s, n);
    b->len += n;
    b->buf[b->len] = '\0';
}

void lb_putc(lb_t *b, char c)
{
    lb_putn(b, &c, 1);
}

void lb_puts(lb_t *b, const char *s)
{
    lb_putn(b, s, strlen(s));
}

void lb_fix(lb_t *b, int64_t v, unsigned frac)
{
    char tmp[32];
    size_t n = dec_format(tmp, sizeof tmp, v, frac);
    if (n == 0) {
        b->overflow = true;
        return;
    }
    lb_putn(b, tmp, n);
}

void lb_i64(lb_t *b, int64_t v)
{
    lb_fix(b, v, 0);
}

void lb_u64(lb_t *b, uint64_t v)
{
    char tmp[24];
    size_t i = sizeof tmp;
    do {
        tmp[--i] = (char)('0' + (char)(v % 10u));
        v /= 10u;
    } while (v != 0u && i > 0);
    lb_putn(b, tmp + i, sizeof tmp - i);
}

void lb_hex(lb_t *b, uint32_t v, unsigned digits)
{
    char tmp[16];
    size_t n = dec_format_hex(tmp, sizeof tmp, v, digits);
    lb_puts(b, "0x");
    if (n == 0) {
        b->overflow = true;
        return;
    }
    lb_putn(b, tmp, n);
}

void lb_key(lb_t *b, const char *key)
{
    lb_putc(b, ' ');
    lb_puts(b, key);
    lb_putc(b, '=');
}

void lb_kv_u(lb_t *b, const char *key, uint64_t v)
{
    lb_key(b, key);
    lb_u64(b, v);
}

void lb_kv_i(lb_t *b, const char *key, int64_t v)
{
    lb_key(b, key);
    lb_i64(b, v);
}

void lb_kv_fix(lb_t *b, const char *key, int64_t v, unsigned frac)
{
    lb_key(b, key);
    lb_fix(b, v, frac);
}

void lb_kv_s(lb_t *b, const char *key, const char *s)
{
    lb_key(b, key);
    lb_puts(b, s);
}

void lb_kv_hex(lb_t *b, const char *key, uint32_t v, unsigned digits)
{
    lb_key(b, key);
    lb_hex(b, v, digits);
}

/* ---- tokeniser ------------------------------------------------------------------ */

static bool is_space(char c)
{
    return c == ' ' || c == '\t';
}

static char lower(char c)
{
    return (c >= 'A' && c <= 'Z') ? (char)(c - 'A' + 'a') : c;
}

bool tok_eq(tok_t t, const char *s)
{
    size_t n = strlen(s);
    if (n != t.n) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        if (lower(t.s[i]) != lower(s[i])) {
            return false;
        }
    }
    return true;
}

tok_status_t proto_tokenize(const char *line, size_t len, cmdline_t *out)
{
    memset(out, 0, sizeof *out);
    size_t i = 0;
    bool have_verb = false;
    while (i < len) {
        while (i < len && is_space(line[i])) {
            i++;
        }
        if (i >= len) {
            break;
        }
        size_t start = i;
        size_t eq = SIZE_MAX;
        unsigned n_eq = 0;
        while (i < len && !is_space(line[i])) {
            if (line[i] == '=') {
                n_eq++;
                if (eq == SIZE_MAX) {
                    eq = i;
                }
            }
            i++;
        }
        tok_t t = { line + start, (uint16_t)(i - start) };
        if (!have_verb) {
            if (n_eq != 0) {
                return TOK_SYNTAX;
            }
            out->verb = t;
            have_verb = true;
            continue;
        }
        if (n_eq == 0) {
            if (out->npos >= CMD_MAX_POS) {
                return TOK_TOO_MANY;
            }
            out->pos[out->npos++] = t;
            continue;
        }
        if (n_eq > 1 || eq == start || eq + 1 == i) {
            return TOK_SYNTAX;
        }
        if (out->nkv >= CMD_MAX_KV) {
            return TOK_TOO_MANY;
        }
        out->key[out->nkv] = (tok_t){ line + start, (uint16_t)(eq - start) };
        out->val[out->nkv] = (tok_t){ line + eq + 1, (uint16_t)(i - eq - 1) };
        out->nkv++;
    }
    return have_verb ? TOK_OK : TOK_EMPTY;
}

/* ---- byte rings ---------------------------------------------------------------- */

typedef struct {
    uint8_t *buf;
    size_t   cap;
    size_t   head;   /* write index */
    size_t   used;
} ring_t;

static uint8_t s_rx_buf[PROTO_RX_RING];
static uint8_t s_tx_buf[PROTO_TX_RING];
static ring_t  s_rx = { s_rx_buf, PROTO_RX_RING, 0, 0 };
static ring_t  s_tx = { s_tx_buf, PROTO_TX_RING, 0, 0 };

static size_t ring_free(const ring_t *r)
{
    return r->cap - r->used;
}

static void ring_clear(ring_t *r)
{
    r->head = 0;
    r->used = 0;
}

static size_t ring_tail(const ring_t *r)
{
    return (r->head + r->cap - r->used) % r->cap;
}

static void ring_put(ring_t *r, const void *data, size_t n)
{
    const uint8_t *p = data;
    for (size_t k = 0; k < n; k++) {
        r->buf[r->head] = p[k];
        r->head = (r->head + 1u) % r->cap;
    }
    r->used += n;
}

/* Contiguous readable span at the tail. */
static size_t ring_span(const ring_t *r, const uint8_t **p)
{
    size_t tail = ring_tail(r);
    size_t n = r->cap - tail;
    if (n > r->used) {
        n = r->used;
    }
    *p = r->buf + tail;
    return n;
}

static void ring_drop(ring_t *r, size_t n)
{
    r->used -= n;
}

/* ---- state ------------------------------------------------------------------------ */

static struct {
    bool          session;
    /* line assembly */
    char          line[PROTO_LINE_MAX + 1];
    size_t        line_len;
    bool          line_long;
    bool          line_bad;
    /* events held while a command executes */
    bool          in_cmd;
    char          hold[PROTO_HOLD_MAX];
    size_t        hold_len;
    /* events ordered after the records pushed before them, released in FIFO
     * order; each entry is a header (uint16 len, uint32 until: its own
     * watermark, results_next_seq() when it was queued) and the line */
    char          after[PROTO_AFTER_MAX];
    size_t        after_len;
    uint16_t      after_n;
    /* DISARM / SWP.ABORT lines spotted on arrival (commands_urgent_kind):
     * per kind, lines in the RX ring not yet executed, and how many of the
     * oldest of them were already acted on while parsing was held back */
    char          urg_verb[URG_VERB_MAX];
    size_t        urg_verb_len;
    uint8_t       urg_phase;       /* URG_LEAD, URG_VERB, URG_POST, URG_NO */
    size_t        urg_count;       /* characters of the line (CR excluded) */
    uint16_t      urg_pending[URGENT_COUNT];
    uint16_t      urg_acted[URGENT_COUNT];   /* SWP.ABORT only */
    /* a finished deferred response waiting for TX space */
    char          resp[PROTO_RESP_MAX];
    size_t        resp_len;
    bool          resp_pending;
    uint32_t      evt_lost_pending;
    proto_stats_t stats;
} s_p;

void proto_init(void)
{
    ring_clear(&s_rx);
    ring_clear(&s_tx);
    memset(&s_p, 0, sizeof s_p);
}

bool proto_session_open(void)
{
    return s_p.session;
}

static void reset_io(void)
{
    ring_clear(&s_rx);
    ring_clear(&s_tx);
    s_p.line_len = 0;
    s_p.line_long = false;
    s_p.line_bad = false;
    s_p.hold_len = 0;
    s_p.after_len = 0;
    s_p.after_n = 0;
    s_p.urg_verb_len = 0;
    s_p.urg_phase = URG_LEAD;
    s_p.urg_count = 0;
    memset(s_p.urg_pending, 0, sizeof s_p.urg_pending);
    memset(s_p.urg_acted, 0, sizeof s_p.urg_acted);
    s_p.resp_pending = false;
    s_p.resp_len = 0;
    s_p.evt_lost_pending = 0;
    commands_cancel_deferred();
}

void proto_session_start(void)
{
    reset_io();
    results_skip_unsent();
    s_p.session = true;
}

void proto_session_end(void)
{
    reset_io();
    s_p.session = false;
}

void proto_get_stats(proto_stats_t *out)
{
    *out = s_p.stats;
}

void proto_clear_stats(void)
{
    s_p.stats = (proto_stats_t){0};
}

bool proto_tx_empty(void)
{
    return s_tx.used == 0 && !s_p.resp_pending && s_p.after_n == 0;
}

/* ---- output ---------------------------------------------------------------------- */

static bool tx_line(const char *s, size_t n)
{
    if (ring_free(&s_tx) < n + 1u) {
        return false;
    }
    ring_put(&s_tx, s, n);
    ring_put(&s_tx, "\n", 1);
    return true;
}

static void event_lost(void)
{
    s_p.stats.evt_lost++;
    s_p.evt_lost_pending++;
}

void proto_event(const char *line, size_t len)
{
    if (!s_p.session) {
        return;
    }
    if (s_p.in_cmd) {
        if (s_p.hold_len + len + 1u <= sizeof s_p.hold) {
            memcpy(s_p.hold + s_p.hold_len, line, len);
            s_p.hold_len += len;
            s_p.hold[s_p.hold_len++] = '\n';
        } else {
            event_lost();
        }
        return;
    }
    if (!tx_line(line, len)) {
        event_lost();
    }
}

void proto_event_lb(const lb_t *b)
{
    proto_event(b->buf, b->len);
}

static bool records_pending_before(uint32_t until)
{
    pt_rec_t r;
    return results_peek_unsent(&r) && r.seq < until;
}

static void after_hdr(size_t off, uint16_t *len, uint32_t *until)
{
    memcpy(len, s_p.after + off, sizeof *len);
    memcpy(until, s_p.after + off + sizeof *len, sizeof *until);
}

void proto_event_ordered(const lb_t *b)
{
    if (!s_p.session) {
        return;
    }
    uint32_t until = results_next_seq();
    if (s_p.after_n == 0 && !records_pending_before(until)) {
        proto_event(b->buf, b->len);
        return;
    }
    if (s_p.after_len + PROTO_AFTER_HDR + b->len <= sizeof s_p.after) {
        uint16_t len = (uint16_t)b->len;
        memcpy(s_p.after + s_p.after_len, &len, sizeof len);
        memcpy(s_p.after + s_p.after_len + sizeof len, &until, sizeof until);
        memcpy(s_p.after + s_p.after_len + PROTO_AFTER_HDR, b->buf, b->len);
        s_p.after_len += PROTO_AFTER_HDR + b->len;
        s_p.after_n++;
    } else {
        event_lost();
    }
}

/* Send queued ordered events, oldest first, each once every record pushed
 * before it has been sent. */
static void flush_after(void)
{
    size_t off = 0;
    uint16_t k = 0;
    while (k < s_p.after_n) {
        uint16_t len;
        uint32_t until;
        after_hdr(off, &len, &until);
        if (records_pending_before(until)
            || !tx_line(s_p.after + off + PROTO_AFTER_HDR, len)) {
            break;   /* keep it and the rest for the next pass (order matters) */
        }
        off += PROTO_AFTER_HDR + len;
        k++;
    }
    if (k == 0) {
        return;
    }
    memmove(s_p.after, s_p.after + off, s_p.after_len - off);
    s_p.after_len -= off;
    s_p.after_n = (uint16_t)(s_p.after_n - k);
}

/* true while a queued ordered event must go out before record `seq` */
static bool event_due_before(uint32_t seq)
{
    if (s_p.after_n == 0) {
        return false;
    }
    uint16_t len;
    uint32_t until;
    after_hdr(0, &len, &until);
    return seq >= until;
}

static void flush_hold(void)
{
    size_t i = 0;
    while (i < s_p.hold_len) {
        size_t j = i;
        while (j < s_p.hold_len && s_p.hold[j] != '\n') {
            j++;
        }
        if (!tx_line(s_p.hold + i, j - i)) {
            event_lost();
        }
        i = j + 1u;
    }
    s_p.hold_len = 0;
}

static void send_evt_lost(void)
{
    if (s_p.evt_lost_pending == 0 || ring_free(&s_tx) < EVT_LOST_LINE_MAX + PROTO_EVT_RESERVE) {
        return;
    }
    char buf[EVT_LOST_LINE_MAX];
    lb_t b;
    lb_init(&b, buf, sizeof buf);
    lb_puts(&b, "EVT WARN EVT_LOST");
    lb_kv_u(&b, "n", s_p.evt_lost_pending);
    if (tx_line(b.buf, b.len)) {
        s_p.evt_lost_pending = 0;
    }
}

/* ---- PT records ------------------------------------------------------------------- */

static void put_sd(lb_t *b, uint16_t sd)
{
    if (sd == 0xFFFFu) {
        lb_putc(b, '-');
    } else {
        lb_fix(b, (int64_t)sd, 1);
    }
}

/* nV -> uV and pA -> 0.1 nA, rounded half away from zero. */
static int64_t div_round(int64_t v, int64_t d)
{
    return (v >= 0) ? (v + d / 2) / d : -((-v + d / 2) / d);
}

size_t proto_format_pt(const pt_rec_t *r, char *buf, size_t cap)
{
    const cal_coeffs_t *c = engine_cal();
    const zero_t *z = engine_zero();
    lb_t b;
    lb_init(&b, buf, cap);
    lb_puts(&b, "PT ");
    lb_u64(&b, r->sweep_id);
    lb_putc(&b, ',');
    lb_u64(&b, r->seq);
    lb_putc(&b, ',');
    lb_u64(&b, r->idx);
    lb_putc(&b, ',');
    lb_u64(&b, r->seg);
    lb_putc(&b, ',');
    lb_u64(&b, r->t_set_us);
    lb_putc(&b, ',');
    lb_u64(&b, r->t_i_us);
    lb_putc(&b, ',');
    lb_u64(&b, r->t_v_us);
    lb_putc(&b, ',');
    lb_u64(&b, r->dac_code);
    lb_putc(&b, ',');
    lb_fix(&b, scaling_code_to_v_uv(c, r->dac_code), 6);
    lb_putc(&b, ',');
    lb_u64(&b, r->n);
    lb_putc(&b, ',');
    lb_i64(&b, r->i_raw);
    lb_putc(&b, ',');
    lb_i64(&b, r->v_raw);
    lb_putc(&b, ',');
    put_sd(&b, r->i_sd);
    lb_putc(&b, ',');
    put_sd(&b, r->v_sd);
    lb_putc(&b, ',');
    lb_fix(&b, div_round(scaling_v_nv(c, z, (double)r->v_raw), 1000), 6);
    lb_putc(&b, ',');
    lb_fix(&b, div_round(scaling_i_pa(c, z, (double)r->i_raw), 100), 10);
    lb_putc(&b, ',');
    lb_hex(&b, r->flags, 4);
    return b.overflow ? 0 : b.len;
}

static void stream_records(void)
{
    char buf[PROTO_PT_LINE_MAX];
    for (unsigned k = 0; k < PT_PER_PASS; k++) {
        if (ring_free(&s_tx) < PT_MIN_FREE) {
            return;
        }
        pt_rec_t r;
        if (!results_peek_unsent(&r)) {
            return;
        }
        if (event_due_before(r.seq)) {
            flush_after();   /* e.g. SWEEP_END of the previous sweep first */
            if (event_due_before(r.seq)) {
                return;
            }
        }
        size_t n = proto_format_pt(&r, buf, sizeof buf);
        if (n == 0 || !tx_line(buf, n)) {
            return;   /* cannot happen with PT_MIN_FREE; never drop the record */
        }
        results_mark_sent();
        s_p.stats.pt_sent++;
    }
}

/* ---- input -------------------------------------------------------------------------- */

/* Spots complete DISARM / SWP.ABORT lines as they enter the RX ring. */
static void urg_scan(const uint8_t *b, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        uint8_t c = b[i];
        if (c == '\n') {
            /* a line too long for next_line() is refused, never urgent */
            if ((s_p.urg_phase == URG_VERB || s_p.urg_phase == URG_POST)
                && s_p.urg_count <= PROTO_LINE_MAX) {
                urgent_t k = commands_urgent_kind(s_p.urg_verb, s_p.urg_verb_len);
                if (k != URGENT_NONE) {
                    s_p.urg_pending[k]++;
                }
            }
            s_p.urg_verb_len = 0;
            s_p.urg_phase = URG_LEAD;
            s_p.urg_count = 0;
            continue;
        }
        if (c == '\r') {
            continue;
        }
        s_p.urg_count++;
        bool blank = (c == ' ' || c == '\t');
        switch (s_p.urg_phase) {
        case URG_LEAD:
        case URG_VERB:
            if (blank) {
                if (s_p.urg_phase == URG_VERB) {
                    s_p.urg_phase = URG_POST;
                }
            } else if (s_p.urg_verb_len < sizeof s_p.urg_verb) {
                s_p.urg_verb[s_p.urg_verb_len++] = (char)c;
                s_p.urg_phase = URG_VERB;
            } else {
                s_p.urg_phase = URG_NO;
            }
            break;
        case URG_POST:
            if (!blank) {
                s_p.urg_phase = URG_NO;   /* arguments */
            }
            break;
        default:
            break;
        }
    }
}

/* Acts on the DISARM / SWP.ABORT lines waiting in the RX ring (every pass:
 * before parsing when it is held back, after the command otherwise); their
 * responses keep their place in the order. */
static void urgent_service(void)
{
    /* the abort first, so that "SWP.ABORT, DISARM" in one packet answers both
     * OK; one abort per sweep actually aborted */
    if (s_p.urg_acted[URGENT_ABORT] < s_p.urg_pending[URGENT_ABORT]
        && commands_urgent_act(URGENT_ABORT)) {
        s_p.urg_acted[URGENT_ABORT]++;
    }
    /* every pass while a DISARM waits (an ARM ahead of it may arm the output
     * later); it acts only when there is something to disarm */
    if (s_p.urg_pending[URGENT_DISARM] > 0u) {
        (void)commands_urgent_act(URGENT_DISARM);
    }
}

static void read_port(uint64_t now)
{
    uint8_t tmp[64];
    for (;;) {
        size_t room = ring_free(&s_rx);
        if (room == 0) {
            return;
        }
        size_t want = room < sizeof tmp ? room : sizeof tmp;
        size_t n = port_host_read(tmp, want);
        if (n == 0) {
            return;
        }
        session_note_rx(now);
        urg_scan(tmp, n);
        ring_put(&s_rx, tmp, n);
    }
}

static void discard_port_input(void)
{
    uint8_t tmp[64];
    size_t total = 0;
    while (total < RX_DISCARD_CHUNK) {
        size_t n = port_host_read(tmp, sizeof tmp);
        if (n == 0) {
            return;
        }
        total += n;
    }
}

/* Assemble the next line. Returns true when a line is complete. */
static bool next_line(void)
{
    while (s_rx.used > 0) {
        const uint8_t *p;
        (void)ring_span(&s_rx, &p);
        uint8_t c = p[0];
        ring_drop(&s_rx, 1);
        if (c == '\n') {
            return true;
        }
        if (c == '\r') {
            continue;
        }
        if (s_p.line_len >= PROTO_LINE_MAX) {
            s_p.line_long = true;
            continue;
        }
        if ((c < 0x20u && c != '\t') || c > 0x7Eu) {
            s_p.line_bad = true;
        }
        s_p.line[s_p.line_len++] = (char)c;
    }
    return false;
}

static void put_response(const lb_t *resp)
{
    /* Space was reserved before the command was parsed. */
    (void)tx_line(resp->buf, resp->len);
}

static void process_line(void)
{
    char rbuf[PROTO_RESP_MAX];
    lb_t resp;
    lb_init(&resp, rbuf, sizeof rbuf);

    bool too_long = s_p.line_long;
    bool bad = s_p.line_bad;
    size_t len = s_p.line_len;
    s_p.line_len = 0;
    s_p.line_long = false;
    s_p.line_bad = false;

    /* the same classification urg_scan() made when the line arrived */
    urgent_t uk = !too_long ? commands_urgent_kind(s_p.line, len) : URGENT_NONE;
    bool acted = false;
    if (uk != URGENT_NONE && s_p.urg_pending[uk] > 0u) {
        s_p.urg_pending[uk]--;
        if (s_p.urg_acted[uk] > 0u) {
            s_p.urg_acted[uk]--;
            acted = true;
        }
    }

    if (too_long) {
        s_p.stats.rx_overflows++;
        commands_format_error(&resp, E_SYNTAX, "line too long");
        put_response(&resp);
        return;
    }
    if (bad) {
        commands_format_error(&resp, E_SYNTAX, "bad character");
        put_response(&resp);
        return;
    }
    cmdline_t cl;
    tok_status_t ts = proto_tokenize(s_p.line, len, &cl);
    if (ts == TOK_EMPTY) {
        return;
    }
    s_p.stats.lines_in++;
    if (ts == TOK_SYNTAX) {
        commands_format_error(&resp, E_SYNTAX, "unbalanced =");
        put_response(&resp);
        return;
    }
    if (ts == TOK_TOO_MANY) {
        commands_format_error(&resp, E_ARG, "too many arguments");
        put_response(&resp);
        return;
    }
    if (acted && commands_urgent_settled(uk)) {
        lb_puts(&resp, "OK");   /* the sweep was aborted when the line arrived */
        put_response(&resp);
        return;
    }
    s_p.in_cmd = true;
    s_p.hold_len = 0;
    bool done = commands_execute(&cl, &resp);
    s_p.in_cmd = false;
    if (done) {
        put_response(&resp);
    }
    flush_hold();
}

static void complete_deferred(void)
{
    if (s_p.resp_pending) {
        if (tx_line(s_p.resp, s_p.resp_len)) {
            s_p.resp_pending = false;
        }
        return;
    }
    lb_t resp;
    lb_init(&resp, s_p.resp, sizeof s_p.resp);
    if (!commands_poll_deferred(&resp)) {
        return;
    }
    if (!tx_line(resp.buf, resp.len)) {
        s_p.resp_len = resp.len;
        s_p.resp_pending = true;
    }
}

/* ---- TX drain ------------------------------------------------------------------------- */

static void drain_tx(void)
{
    size_t budget = TX_CHUNK;
    bool wrote = false;
    while (budget > 0 && s_tx.used > 0) {
        size_t space = port_host_write_space();
        if (space == 0) {
            break;
        }
        const uint8_t *p;
        size_t n = ring_span(&s_tx, &p);
        if (n > space) {
            n = space;
        }
        if (n > budget) {
            n = budget;
        }
        size_t w = port_host_write(p, n);
        if (w == 0) {
            break;
        }
        ring_drop(&s_tx, w);
        budget -= w;
        wrote = true;
    }
    if (wrote) {
        port_host_flush();
    }
}

void proto_service(uint64_t now_us)
{
    if (!s_p.session) {
        discard_port_input();
        return;
    }
    read_port(now_us);
    if (commands_deferred_pending() || s_p.resp_pending || ring_free(&s_tx) < CMD_MIN_FREE
        || sizeof s_p.after - s_p.after_len < PROTO_AFTER_RESERVE) {
        urgent_service();
    }
    if (commands_deferred_pending() || s_p.resp_pending) {
        complete_deferred();
    } else if (ring_free(&s_tx) >= CMD_MIN_FREE
               && sizeof s_p.after - s_p.after_len >= PROTO_AFTER_RESERVE) {
        while (next_line()) {
            bool blank = (s_p.line_len == 0 && !s_p.line_long && !s_p.line_bad);
            process_line();
            if (!blank) {
                break;   /* at most one command per pass */
            }
        }
        /* after the command (so a DISARM reached in order is executed, not
         * pre-empted): undo at once an ARM / SWP.RUN that ran ahead of a
         * DISARM / SWP.ABORT still waiting in a backlog */
        urgent_service();
    }
    send_evt_lost();
    stream_records();
    flush_after();
    drain_tx();
}
