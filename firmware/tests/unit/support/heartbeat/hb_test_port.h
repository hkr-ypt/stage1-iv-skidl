/*
 * hb_test_port.h — minimal test implementation of the port.h calls used by
 * drivers/heartbeat.c: a fake microsecond clock, PC5 level recording and a
 * controllable port_in_exception(), for tests/unit/test_heartbeat.c.
 */
#ifndef HB_TEST_PORT_H
#define HB_TEST_PORT_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint64_t now;
    bool     in_exception;
    bool     level;                 /* PC5 as last written                 */
    uint64_t writes;                /* port_hb_write calls                 */
    uint64_t writes_high;           /* port_hb_write(true) calls           */
    uint64_t rises, falls;          /* real level changes                  */
    uint64_t last_change_us;
    uint64_t writes_in_exception;   /* port_hb_write while in_exception    */
    uint64_t now_calls;
    /* called on every real level change */
    void   (*on_edge)(bool level, uint64_t t_us);
} htp_t;

extern htp_t htp;

/* Clock at 1 s, PC5 low, not in an exception, counters cleared, no hook. */
void htp_reset(void);

#endif /* HB_TEST_PORT_H */
