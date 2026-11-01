/*
 * cal_port_stub.c — link-only port for test_scaling: the scaling tests use
 * calib_nominal() and calib_plausible(), which live in core/calib.c next to
 * the flash code, so calib.o brings in references to the calibration-flash
 * and UID port calls. None of them is exercised here; each reports "no
 * flash" so an accidental call cannot pass silently.
 */
#include "port.h"

size_t port_cal_sector_size(void)
{
    return 0;
}

const uint8_t *port_cal_sector(unsigned idx)
{
    (void)idx;
    return NULL;
}

bool port_cal_erase(unsigned idx)
{
    (void)idx;
    return false;
}

bool port_cal_program(unsigned idx, size_t offset, const uint8_t *data, size_t n)
{
    (void)idx;
    (void)offset;
    (void)data;
    (void)n;
    return false;
}

void port_uid(uint32_t uid[3])
{
    uid[0] = 0;
    uid[1] = 0;
    uid[2] = 0;
}
