/*
 * sim_main.c — the whole-firmware simulator executable `stage1_iv_sim`.
 *
 * Runs the real portable firmware (app_init / app_pass, firmware/src) on the
 * simulation port (port_sim.c: virtual time, DAC8552/ADS1256 models, board
 * model) and connects it to the outside world:
 *   - the host link (USB CDC on the target) is a pseudo-terminal; a host
 *     program opens the slave like a serial port;
 *   - a UNIX stream socket accepts line-based control commands (fault
 *     injection, DUT selection, observation), documented in
 *     docs/firmware/simulator.md.
 *
 * Usage:
 *   stage1_iv_sim [--pty-link PATH] [--ctl PATH] [--speed X] [--seed N]
 *                 [--pass-cost-us N]
 * Before anything else it prints exactly two lines on stdout:
 *   PTY <slave path>
 *   CTL <control socket path>
 *
 * Loop: app_pass() (or, while the emulated MCU is hung, 100 us of virtual
 * time), then the reset the port may have requested (psim_mcu_reset() +
 * app_init(), see port_sim_ext.h), then every 500 us of virtual time the
 * pty and control-socket pumps and the wall-clock pacing (virtual time runs
 * at `speed` x wall time; --speed 0 runs as fast as possible).
 *
 * DTR: a pty has no modem lines, so the firmware sees
 *   DTR = (control flag `dtr`, default 1) AND (a client has the slave open).
 * Client presence is POLLHUP on the master, which Linux reports while no
 * file descriptor refers to the slave (after the slave was opened once,
 * which this program does itself at start-up to put it in raw mode).
 *
 * Host-side code only (POSIX + Linux pty semantics); not part of the
 * firmware image.
 */
#define _GNU_SOURCE

#include "app.h"
#include "port.h"
#include "port_sim.h"
#include "port_sim_ext.h"
#include "fw_types.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#define IO_PERIOD_US        500u      /* virtual time between I/O services   */
#define IO_EVERY_ITER       64u       /* ... or at least every N iterations   */
#define HUNG_STEP_US        100u
#define LAG_REBASE_US       250000.0  /* behind by more: stop catching up     */
#define SLEEP_MAX_US        1000.0
#define SLEEP_MIN_US        50.0
#define CTL_MAX_CLIENTS     8
#define CTL_LINE_MAX        512
#define CTL_MAX_ARGS        8
#define PTY_CHUNK           4096
#define SIM_CDC_TX_FIFO_BYTES 4096u   /* CFG_TUD_CDC_TX_BUFSIZE */
#define SHORT_DUT_OHM       0.001

typedef struct {
    int    fd;
    char   buf[CTL_LINE_MAX];
    size_t len;
    bool   overflow;
} ctl_client_t;

static volatile sig_atomic_t g_signal;

static struct {
    int      master;
    char     slave_path[256];
    char     link_path[PATH_MAX];
    bool     have_link;
    int      listen_fd;
    char     ctl_path[sizeof(((struct sockaddr_un *)0)->sun_path)];
    ctl_client_t clients[CTL_MAX_CLIENTS];
    bool     client_present;
    bool     ctl_dtr;
    bool     quit;
    double   speed;
    uint32_t seed;
    double   wall0_us;
    uint64_t v0_us;
    uint8_t  txp[PTY_CHUNK];
    size_t   txp_len;
    uint64_t pty_rx_discarded;
    uint32_t lag_rebases;
} G;

/* ---- utilities --------------------------------------------------------------------------- */

static void on_signal(int sig)
{
    g_signal = sig;
}

static double mono_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1e6 + (double)ts.tv_nsec / 1e3;
}

static void pace_rebase(void)
{
    G.wall0_us = mono_us();
    G.v0_us = psim_time_us();
}

static void set_nonblock(int fd)
{
    int fl = fcntl(fd, F_GETFL, 0);
    if (fl >= 0) {
        (void)fcntl(fd, F_SETFL, fl | O_NONBLOCK);
    }
}

static void usage(FILE *f)
{
    fprintf(f,
            "usage: stage1_iv_sim [--pty-link PATH] [--ctl PATH] [--speed X] [--seed N]\n"
            "                     [--pass-cost-us N]\n"
            "  --pty-link PATH   also create a symlink PATH -> the pty slave\n"
            "  --ctl PATH        control socket path (default $TMPDIR or the current\n"
            "                    directory, stage1_iv_sim.<pid>.ctl)\n"
            "  --speed X         virtual time = X * wall time (default 1; 0 = unpaced)\n"
            "  --seed N          ADC noise seed (default 1)\n"
            "  --pass-cost-us N  virtual time added per main-loop pass (default 20)\n"
            "Prints 'PTY <slave>' and 'CTL <socket>' on stdout, then runs until the\n"
            "control command 'quit', SIGINT or SIGTERM.\n");
}

static bool parse_u64(const char *s, uint64_t *out)
{
    if (s == NULL || *s == '\0' || *s == '-') {
        return false;
    }
    char *end = NULL;
    errno = 0;
    unsigned long long v = strtoull(s, &end, 0);
    if (errno != 0 || end == s || *end != '\0') {
        return false;
    }
    *out = (uint64_t)v;
    return true;
}

static bool parse_u32(const char *s, uint32_t *out)
{
    uint64_t v;
    if (!parse_u64(s, &v) || v > UINT32_MAX) {
        return false;
    }
    *out = (uint32_t)v;
    return true;
}

static bool parse_double(const char *s, double *out)
{
    if (s == NULL || *s == '\0') {
        return false;
    }
    char *end = NULL;
    errno = 0;
    double v = strtod(s, &end);
    if (errno != 0 || end == s || *end != '\0' || !isfinite(v)) {
        return false;
    }
    *out = v;
    return true;
}

static bool parse_bool01(const char *s, bool *out)
{
    if (s != NULL && strcmp(s, "0") == 0) {
        *out = false;
        return true;
    }
    if (s != NULL && strcmp(s, "1") == 0) {
        *out = true;
        return true;
    }
    return false;
}

/* ---- pty --------------------------------------------------------------------------------- */

static bool pty_open(void)
{
    G.master = posix_openpt(O_RDWR | O_NOCTTY);
    if (G.master < 0 || grantpt(G.master) != 0 || unlockpt(G.master) != 0) {
        perror("stage1_iv_sim: posix_openpt");
        return false;
    }
    const char *name = ptsname(G.master);
    if (name == NULL || strlen(name) >= sizeof(G.slave_path)) {
        fprintf(stderr, "stage1_iv_sim: ptsname failed\n");
        return false;
    }
    snprintf(G.slave_path, sizeof(G.slave_path), "%s", name);
    /* Raw mode on the slave side; opening and closing it once also makes
     * POLLHUP on the master mean "no client" from now on. */
    int s = open(G.slave_path, O_RDWR | O_NOCTTY);
    if (s < 0) {
        perror("stage1_iv_sim: open pty slave");
        return false;
    }
    struct termios t;
    if (tcgetattr(s, &t) == 0) {
        cfmakeraw(&t);
        (void)tcsetattr(s, TCSANOW, &t);
    }
    close(s);
    set_nonblock(G.master);
    return true;
}

static bool pty_make_link(const char *path)
{
    struct stat st;
    if (lstat(path, &st) == 0) {
        if (!S_ISLNK(st.st_mode)) {
            fprintf(stderr, "stage1_iv_sim: --pty-link %s exists and is not a symlink\n", path);
            return false;
        }
        (void)unlink(path);
    }
    if (symlink(G.slave_path, path) != 0) {
        perror("stage1_iv_sim: symlink");
        return false;
    }
    snprintf(G.link_path, sizeof(G.link_path), "%s", path);
    G.have_link = true;
    return true;
}

static void pty_service(void)
{
    struct pollfd p = { .fd = G.master, .events = POLLIN | POLLOUT, .revents = 0 };
    (void)poll(&p, 1, 0);
    bool client = (p.revents & POLLHUP) == 0;
    if (client != G.client_present) {
        G.client_present = client;
        if (!client) {
            /* The departed client's pty queues and the staging buffer are the
             * host side, dropped like a host's tty buffers on close. The
             * device side (the TX ring: TinyUSB's CDC TX FIFO) keeps what
             * was not taken, as on the target: the next client gets it first
             * (protocol.md section 1). */
            (void)tcflush(G.master, TCIOFLUSH);
            G.txp_len = 0;
        }
    }
    psim_set_dtr(G.ctl_dtr && client);

    uint8_t buf[PTY_CHUNK];
    if (client && (p.revents & POLLIN) != 0) {
        size_t space = psim_host_rx_space();
        if (space > sizeof(buf)) {
            space = sizeof(buf);
        }
        if (space > 0) {
            ssize_t n = read(G.master, buf, space);
            if (n > 0) {
                if (port_host_configured()) {
                    psim_host_give_rx(buf, (size_t)n);
                } else {
                    G.pty_rx_discarded += (uint64_t)n; /* no USB link: lost */
                }
            }
        }
    }

    if (!client) {
        return;   /* nobody reading: the TX ring keeps its content */
    }
    for (int round = 0; round < 4; round++) {
        if (G.txp_len < sizeof(G.txp)) {
            G.txp_len += psim_host_take_tx(G.txp + G.txp_len, sizeof(G.txp) - G.txp_len);
        }
        if (G.txp_len == 0) {
            break;
        }
        ssize_t w = write(G.master, G.txp, G.txp_len);
        if (w <= 0) {
            break; /* EAGAIN: the client is not reading (stalled host) */
        }
        memmove(G.txp, G.txp + w, G.txp_len - (size_t)w);
        G.txp_len -= (size_t)w;
    }
}

/* ---- control socket -------------------------------------------------------------------------- */

static bool ctl_open(const char *path)
{
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    if (strlen(path) >= sizeof(addr.sun_path)) {
        fprintf(stderr, "stage1_iv_sim: control socket path too long: %s\n", path);
        return false;
    }
    snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", path);
    struct stat st;
    if (lstat(path, &st) == 0) {
        if (!S_ISSOCK(st.st_mode)) {
            fprintf(stderr, "stage1_iv_sim: %s exists and is not a socket\n", path);
            return false;
        }
        (void)unlink(path);
    }
    G.listen_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (G.listen_fd < 0) {
        perror("stage1_iv_sim: socket");
        return false;
    }
    if (bind(G.listen_fd, (struct sockaddr *)&addr, sizeof(addr)) != 0 ||
        listen(G.listen_fd, 4) != 0) {
        perror("stage1_iv_sim: bind/listen");
        return false;
    }
    set_nonblock(G.listen_fd);
    snprintf(G.ctl_path, sizeof(G.ctl_path), "%s", path);
    return true;
}

static void ctl_send(ctl_client_t *c, const char *fmt, ...)
{
    char line[2048];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line, sizeof(line) - 1, fmt, ap);
    va_end(ap);
    if (n < 0) {
        return;
    }
    size_t len = (size_t)n < sizeof(line) - 1 ? (size_t)n : sizeof(line) - 2;
    line[len++] = '\n';
    size_t off = 0;
    int tries = 0;
    while (off < len && tries < 1000) {
        ssize_t w = send(c->fd, line + off, len - off, MSG_NOSIGNAL);
        if (w > 0) {
            off += (size_t)w;
        } else if (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
            tries++;
            struct timespec ts = { 0, 100000 };
            nanosleep(&ts, NULL);
        } else {
            break;
        }
    }
}

static void obs_line(ctl_client_t *c)
{
    board_model_t *b = psim_board();
    dac_model_t *d = psim_dac();
    adsm_t *a = psim_ads();
    psim_obs_t o;
    psim_ext_obs_t x;
    psim_observe(&o);
    psim_observe_ext(&x);
    adsm_violations_t v;
    adsm_get_violations(a, &v);
    uint32_t vtot = v.sclk + v.t6 + v.t11 + v.t10 + v.read_not_ready + v.busy +
                    v.unknown_cmd + v.short_read + v.after_sync;
    double v_hi, v_lo, i_dut, i_shunt;
    bool lim;
    board_solve(b, &v_hi, &v_lo, &i_dut, &i_shunt, &lim);
    crash_record_t cr;
    bool crash = port_crash_get(&cr);
    ctl_send(c,
             "OK t_us=%llu v_sweep=%.6f vref_dac=%.4f v_arm=%.4f gate=%d"
             " gate_on_events=%u gate_off_events=%u"
             " v_dut=%.6f i_dut=%.9f v_hi=%.6f v_lo=%.6f i_shunt=%.9f in_limit=%d"
             " dac_a=%u dac_b=%u dac_pd_a=%u dac_pd_b=%u dac_frames=%u dac_aborted=%u"
             " dac_reserved_violations=%u dac_b_not_fs_loads=%u"
             " hb_edges=%llu hb_level=%d hb_last_edge_us=%llu led=%d"
             " passes=%llu resets=%u resets_requested=%u reset_flags=0x%02x"
             " crash=%d crash_cause=%s iwdg_kicks=%u iwdg_fires=%u"
             " deadman_enabled=%d deadman_fires=%u"
             " adc_conversions=%u adc_selfcals=%u adc_viol_total=%u adc_viol_sclk=%u"
             " adc_viol_t6=%u adc_viol_t11=%u adc_viol_t10=%u adc_viol_read_not_ready=%u"
             " adc_viol_busy=%u adc_viol_unknown_cmd=%u adc_viol_short_read=%u"
             " adc_viol_after_sync=%u"
             " vbus_mv=%u dtr=%d configured=%d connected=%d client=%d"
             " cal_erases=%u cal_programs=%u hung=%d",
             (unsigned long long)psim_time_us(), board_vsweep(b), board_vref_dac(b),
             b->v_arm, b->gate_on ? 1 : 0, b->gate_on_events, b->gate_off_events,
             v_hi - v_lo, i_dut, v_hi, v_lo, i_shunt, lim ? 1 : 0,
             (unsigned)d->dac[0], (unsigned)d->dac[1], (unsigned)d->dac_pd[0],
             (unsigned)d->dac_pd[1], d->frames_executed, d->frames_aborted,
             d->reserved_violations, d->b_not_fullscale_loads,
             (unsigned long long)o.hb_edges, o.hb_level ? 1 : 0,
             (unsigned long long)o.hb_last_edge_us, o.led ? 1 : 0,
             (unsigned long long)x.passes, x.mcu_resets, o.resets_requested,
             (unsigned)x.reset_flags, crash ? 1 : 0,
             crash ? crash_cause_name(cr.cause) : "none", o.iwdg_kicks, x.iwdg_fires,
             o.deadman_enabled ? 1 : 0, x.deadman_fires,
             adsm_conversions(a), adsm_selfcals(a), vtot, v.sclk, v.t6, v.t11, v.t10,
             v.read_not_ready, v.busy, v.unknown_cmd, v.short_read, v.after_sync,
             port_vbus_mv(), port_host_dtr() ? 1 : 0, port_host_configured() ? 1 : 0,
             psim_host_connected() ? 1 : 0, G.client_present ? 1 : 0,
             o.cal_erases, o.cal_programs, psim_hung() ? 1 : 0);
}

static int split_args(char *line, char *argv[], int max)
{
    int argc = 0;
    char *save = NULL;
    for (char *tok = strtok_r(line, " \t", &save); tok != NULL && argc < max;
         tok = strtok_r(NULL, " \t", &save)) {
        argv[argc++] = tok;
    }
    return argc;
}

#define ERR_ARGS() do { ctl_send(c, "ERR args"); return; } while (0)

static void ctl_bool_cmd(ctl_client_t *c, int argc, char *argv[], bool *field)
{
    bool v;
    if (argc != 2 || !parse_bool01(argv[1], &v)) {
        ctl_send(c, "ERR args");
        return;
    }
    *field = v;
    ctl_send(c, "OK");
}

static void ctl_dut(ctl_client_t *c, int argc, char *argv[])
{
    board_model_t *b = psim_board();
    dut_t d;
    memset(&d, 0, sizeof(d));
    if (argc == 2 && strcmp(argv[1], "open") == 0) {
        d.kind = DUT_OPEN;
    } else if (argc == 2 && strcmp(argv[1], "short") == 0) {
        d.kind = DUT_RESISTOR;
        d.r_ohm = SHORT_DUT_OHM;
    } else if (argc == 3 && strcmp(argv[1], "resistor") == 0) {
        if (!parse_double(argv[2], &d.r_ohm) || !(d.r_ohm > 0.0)) {
            ERR_ARGS();
        }
        d.kind = DUT_RESISTOR;
    } else if (argc == 5 && strcmp(argv[1], "diode") == 0) {
        if (!parse_double(argv[2], &d.is_a) || !parse_double(argv[3], &d.n) ||
            !parse_double(argv[4], &d.rs_ohm) || !(d.is_a > 0.0) || !(d.n > 0.0) ||
            d.rs_ohm < 0.0) {
            ERR_ARGS();
        }
        d.kind = DUT_DIODE;
    } else if (argc == 4 && strcmp(argv[1], "cell") == 0) {
        if (!parse_double(argv[2], &d.v_cell) || !parse_double(argv[3], &d.r_ohm) ||
            !(d.r_ohm > 0.0)) {
            ERR_ARGS();
        }
        d.kind = DUT_CELL;
    } else if (argc == 3 && strcmp(argv[1], "capacitor") == 0) {
        if (!parse_double(argv[2], &d.c_f) || d.c_f < 0.0) {
            ERR_ARGS();
        }
        d.kind = DUT_CAPACITOR;
    } else {
        ERR_ARGS();
    }
    b->dut = d;
    ctl_send(c, "OK");
}

static void ctl_command(ctl_client_t *c, char *line)
{
    char *argv[CTL_MAX_ARGS];
    int argc = split_args(line, argv, CTL_MAX_ARGS);
    if (argc == 0) {
        return;
    }
    const char *cmd = argv[0];
    board_model_t *b = psim_board();
    uint32_t u32;
    uint64_t u64;
    double dv;
    bool bv;

    if (strcmp(cmd, "vbus") == 0) {
        if (argc != 2 || !parse_u32(argv[1], &u32)) {
            ERR_ARGS();
        }
        psim_set_vbus_mv(u32);
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "dtr") == 0) {
        if (argc != 2 || !parse_bool01(argv[1], &bv)) {
            ERR_ARGS();
        }
        G.ctl_dtr = bv;
        psim_set_dtr(G.ctl_dtr && G.client_present);
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "configured") == 0) {
        if (argc != 2 || !parse_bool01(argv[1], &bv)) {
            ERR_ARGS();
        }
        psim_set_configured(bv);
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "dut") == 0) {
        ctl_dut(c, argc, argv);
    } else if (strcmp(cmd, "rails") == 0) {
        bool pos, neg;
        if (argc != 3 || !parse_bool01(argv[1], &pos) || !parse_bool01(argv[2], &neg)) {
            ERR_ARGS();
        }
        b->rail_pos = pos;
        b->rail_neg = neg;
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "sense_plus_open") == 0) {
        ctl_bool_cmd(c, argc, argv, &b->sense_plus_open);
    } else if (strcmp(cmd, "sense_minus_open") == 0) {
        ctl_bool_cmd(c, argc, argv, &b->sense_minus_open);
    } else if (strcmp(cmd, "force_open") == 0) {
        ctl_bool_cmd(c, argc, argv, &b->force_open);
    } else if (strcmp(cmd, "ilim") == 0) {
        if (argc != 2 || !parse_double(argv[1], &dv) || dv < 0.0) {
            ERR_ARGS();
        }
        b->i_lim = dv;
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "noise") == 0) {
        if (argc != 2 || !parse_double(argv[1], &dv) || dv < 0.0) {
            ERR_ARGS();
        }
        adsm_set_noise(psim_ads(), dv, G.seed);
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "fail_spi1") == 0 || strcmp(cmd, "fail_spi2") == 0) {
        if (argc != 2 || !parse_u32(argv[1], &u32)) {
            ERR_ARGS();
        }
        if (cmd[8] == '1') {
            psim_fail_spi1(u32);
        } else {
            psim_fail_spi2(u32);
        }
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "adc_stuck") == 0) {
        adsm_stuck_t s = ADSM_STUCK_NONE;
        if (argc == 2 && strcmp(argv[1], "none") == 0) {
            s = ADSM_STUCK_NONE;
        } else if (argc == 2 && strcmp(argv[1], "low") == 0) {
            s = ADSM_STUCK_LOW;
        } else if (argc == 2 && strcmp(argv[1], "high") == 0) {
            s = ADSM_STUCK_HIGH;
        } else {
            ERR_ARGS();
        }
        adsm_set_stuck(psim_ads(), s);
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "adc_drdy_dead") == 0) {
        if (argc != 2 || !parse_bool01(argv[1], &bv)) {
            ERR_ARGS();
        }
        adsm_set_drdy_dead(psim_ads(), bv);
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "adc_reset") == 0) {
        if (argc != 1) {
            ERR_ARGS();
        }
        adsm_spontaneous_reset(psim_ads(), psim_time_us());
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "stall") == 0) {
        if (argc != 2 || !parse_u64(argv[1], &u64)) {
            ERR_ARGS();
        }
        psim_stall_next_pass(u64);
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "bor") == 0) {
        if (argc != 2 || !parse_u32(argv[1], &u32) || u32 > 3u) {
            ERR_ARGS();
        }
        psim_set_bor_level((int)u32);
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "tim2_frozen") == 0) {
        if (argc != 2 || !parse_bool01(argv[1], &bv)) {
            ERR_ARGS();
        }
        psim_set_dbg_tim2_frozen(bv);
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "tx_limit") == 0) {
        if (argc == 2 && strcmp(argv[1], "-1") == 0) {
            psim_set_tx_space_limit(SIM_CDC_TX_FIFO_BYTES);   /* the default */
        } else if (argc == 2 && parse_u64(argv[1], &u64)) {
            psim_set_tx_space_limit((size_t)u64);
        } else {
            ERR_ARGS();
        }
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "mcu_reset") == 0) {
        if (argc != 1) {
            ERR_ARGS();
        }
        psim_mcu_reset();
        app_init();
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "power_cycle") == 0) {
        if (argc != 1) {
            ERR_ARGS();
        }
        psim_power_cycle();
        app_init();
        G.txp_len = 0;
        pace_rebase();
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "crash") == 0) {
        int kind;
        if (argc == 2 && strcmp(argv[1], "hardfault") == 0) {
            kind = 0;
        } else if (argc == 2 && strcmp(argv[1], "hang") == 0) {
            kind = 1;
        } else if (argc == 2 && strcmp(argv[1], "hang_irqoff") == 0) {
            kind = 2;
        } else {
            ERR_ARGS();
        }
        if (psim_cpu_stopped()) {
            ctl_send(c, "ERR state cpu stopped");
            return;
        }
        port_crash_test(kind);
        ctl_send(c, "OK");
    } else if (strcmp(cmd, "obs?") == 0) {
        obs_line(c);
    } else if (strcmp(cmd, "time?") == 0) {
        ctl_send(c, "OK t_us=%llu", (unsigned long long)psim_time_us());
    } else if (strcmp(cmd, "quit") == 0) {
        G.quit = true;
        ctl_send(c, "OK");
    } else {
        ctl_send(c, "ERR unknown %s", cmd);
    }
}

static void ctl_close(ctl_client_t *c)
{
    close(c->fd);
    c->fd = -1;
    c->len = 0;
    c->overflow = false;
}

static void ctl_service(void)
{
    for (;;) {
        int fd = accept(G.listen_fd, NULL, NULL);
        if (fd < 0) {
            break;
        }
        set_nonblock(fd);
        bool placed = false;
        for (int i = 0; i < CTL_MAX_CLIENTS; i++) {
            if (G.clients[i].fd < 0) {
                G.clients[i].fd = fd;
                G.clients[i].len = 0;
                G.clients[i].overflow = false;
                placed = true;
                break;
            }
        }
        if (!placed) {
            static const char msg[] = "ERR too many clients\n";
            (void)send(fd, msg, sizeof(msg) - 1, MSG_NOSIGNAL);
            close(fd);
        }
    }
    for (int i = 0; i < CTL_MAX_CLIENTS && !G.quit; i++) {
        ctl_client_t *c = &G.clients[i];
        if (c->fd < 0) {
            continue;
        }
        char tmp[256];
        ssize_t n = recv(c->fd, tmp, sizeof(tmp), 0);
        if (n == 0 || (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) {
            ctl_close(c);
            continue;
        }
        for (ssize_t k = 0; k < n && c->fd >= 0 && !G.quit; k++) {
            char ch = tmp[k];
            if (ch == '\n') {
                if (c->overflow) {
                    ctl_send(c, "ERR line too long");
                } else {
                    c->buf[c->len] = '\0';
                    if (c->len > 0 && c->buf[c->len - 1] == '\r') {
                        c->buf[c->len - 1] = '\0';
                    }
                    ctl_command(c, c->buf);
                }
                c->len = 0;
                c->overflow = false;
            } else if (c->len + 1 < sizeof(c->buf)) {
                c->buf[c->len++] = ch;
            } else {
                c->overflow = true;
            }
        }
    }
}

/* ---- pacing ----------------------------------------------------------------------------------- */

static void pace(void)
{
    if (!(G.speed > 0.0)) {
        return;
    }
    double target = (double)G.v0_us + (mono_us() - G.wall0_us) * G.speed;
    double vt = (double)psim_time_us();
    if (vt > target) {
        double sleep_us = (vt - target) / G.speed;
        if (sleep_us > SLEEP_MAX_US) {
            sleep_us = SLEEP_MAX_US;
        }
        if (sleep_us >= SLEEP_MIN_US) {
            struct timespec ts = { 0, (long)(sleep_us * 1000.0) };
            nanosleep(&ts, NULL);
        }
    } else if (target - vt > LAG_REBASE_US) {
        G.lag_rebases++;
        pace_rebase(); /* the host is slower than real time: do not burst */
    }
}

/* ---- main ---------------------------------------------------------------------------------------- */

int main(int argc, char **argv)
{
    const char *opt_link = NULL;
    const char *opt_ctl = NULL;
    psim_options_t opt;
    psim_default_options(&opt);
    G.speed = 1.0;
    G.ctl_dtr = true;
    G.master = -1;
    G.listen_fd = -1;
    for (int i = 0; i < CTL_MAX_CLIENTS; i++) {
        G.clients[i].fd = -1;
    }

    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        const char *val = (i + 1 < argc) ? argv[i + 1] : NULL;
        if (strcmp(a, "--help") == 0 || strcmp(a, "-h") == 0) {
            usage(stdout);
            return 0;
        } else if (strcmp(a, "--pty-link") == 0 && val != NULL) {
            opt_link = val;
            i++;
        } else if (strcmp(a, "--ctl") == 0 && val != NULL) {
            opt_ctl = val;
            i++;
        } else if (strcmp(a, "--speed") == 0 && val != NULL) {
            if (!parse_double(val, &G.speed) || G.speed < 0.0) {
                fprintf(stderr, "stage1_iv_sim: bad --speed %s\n", val);
                return 2;
            }
            i++;
        } else if (strcmp(a, "--seed") == 0 && val != NULL) {
            if (!parse_u32(val, &opt.seed)) {
                fprintf(stderr, "stage1_iv_sim: bad --seed %s\n", val);
                return 2;
            }
            i++;
        } else if (strcmp(a, "--pass-cost-us") == 0 && val != NULL) {
            if (!parse_u32(val, &opt.pass_cost_us)) {
                fprintf(stderr, "stage1_iv_sim: bad --pass-cost-us %s\n", val);
                return 2;
            }
            i++;
        } else {
            usage(stderr);
            return 2;
        }
    }
    G.seed = opt.seed;

    /* Default control socket: $TMPDIR, else the current directory; a UNIX
     * socket path must fit sun_path (108 bytes), so a too-long directory is
     * skipped and, as a last resort, a path relative to the current
     * directory is used (clients must then connect from the same directory). */
    char ctl_default[sizeof(G.ctl_path)];
    if (opt_ctl == NULL) {
        char cwd[PATH_MAX];
        const char *dirs[2] = { getenv("TMPDIR"), getcwd(cwd, sizeof(cwd)) };
        bool found = false;
        for (int k = 0; k < 2 && !found; k++) {
            if (dirs[k] == NULL || dirs[k][0] == '\0') {
                continue;
            }
            int n = snprintf(ctl_default, sizeof(ctl_default), "%s/stage1_iv_sim.%ld.ctl",
                             dirs[k], (long)getpid());
            found = n > 0 && (size_t)n < sizeof(ctl_default);
        }
        if (!found) {
            snprintf(ctl_default, sizeof(ctl_default), "stage1_iv_sim.%ld.ctl", (long)getpid());
            fprintf(stderr, "stage1_iv_sim: directory paths too long for a UNIX socket; "
                            "using %s relative to the current directory\n", ctl_default);
        }
        opt_ctl = ctl_default;
    }

    if (!pty_open()) {
        return 1;
    }
    if (opt_link != NULL && !pty_make_link(opt_link)) {
        return 1;
    }
    if (!ctl_open(opt_ctl)) {
        if (G.have_link) {
            (void)unlink(G.link_path);
        }
        return 1;
    }

    printf("PTY %s\nCTL %s\n", G.slave_path, G.ctl_path);
    fflush(stdout);

    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_signal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);
    signal(SIGPIPE, SIG_IGN);

    psim_power_on(&opt);
    /* the TX ring stands for TinyUSB's 4 KB CDC TX FIFO (tusb_config.h) */
    psim_set_tx_space_limit(SIM_CDC_TX_FIFO_BYTES);
    app_init();
    pace_rebase();

    uint64_t last_io = 0;
    uint32_t iter = 0;
    while (g_signal == 0 && !G.quit) {
        if (!psim_hung()) {
            app_pass();
        } else {
            psim_advance_us(HUNG_STEP_US);
        }
        if (psim_reset_requested()) {
            pty_service();   /* bytes already sent reach the host first */
            psim_mcu_reset();
            app_init();
        }
        uint64_t now = psim_time_us();
        if (now < last_io || now - last_io >= IO_PERIOD_US || ++iter >= IO_EVERY_ITER) {
            iter = 0;
            last_io = now;
            pty_service();
            ctl_service();
            pace();
        }
    }

    for (int i = 0; i < CTL_MAX_CLIENTS; i++) {
        if (G.clients[i].fd >= 0) {
            ctl_close(&G.clients[i]);
        }
    }
    close(G.listen_fd);
    (void)unlink(G.ctl_path);
    if (G.have_link) {
        (void)unlink(G.link_path);
    }
    close(G.master);
    if (g_signal != 0) {
        fprintf(stderr, "stage1_iv_sim: stopped by signal %d\n", (int)g_signal);
    }
    return 0;
}
