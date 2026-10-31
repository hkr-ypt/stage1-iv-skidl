/*
 * session.h — the host session (docs/firmware/protocol.md §1,
 * firmware_spec.md §8.2): VBUS debounce from the PA0 measurement, soft
 * connect, DTR, and liveness.
 *
 * A session exists while VBUS is present (> VBUS_ATTACH_MV for
 * VBUS_DEBOUNCE_US; absent as soon as a reading is < VBUS_DETACH_MV), the
 * device is configured and not suspended, and DTR is asserted. Hardware VBUS
 * sensing is off and TinyUSB never sees an unplug, so PA0 is the truth for
 * attach/detach. Any received byte is host activity; in the armed states the
 * instrument disarms when none arrives for host_timeout_s (SI-7).
 */
#ifndef SESSION_H
#define SESSION_H

#include "fw_types.h"

void     session_init(void);
void     session_service(uint64_t now_us);
bool     session_active(void);
bool     session_vbus_present(void);
void     session_note_rx(uint64_t now_us);
uint64_t session_last_rx_us(void);
uint32_t session_count(void);           /* sessions started since boot */

#endif /* SESSION_H */
