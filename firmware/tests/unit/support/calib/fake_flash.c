/*
 * fake_flash.c — test port for test_calib (see fake_flash.h): implements the
 * calibration-flash and UID calls of port.h over two RAM sectors with flash
 * semantics (erase -> 0xFF, program: new = old & data, aligned 32-bit words,
 * RM0383 §3.5) and injectable faults.
 */
#include "calib/fake_flash.h"

#include <string.h>

#include "port.h"

typedef enum {
    ERASE_OK = 0,
    ERASE_FAIL,
    ERASE_SILENT,
    ERASE_TEAR,
} erase_fault_t;

static uint8_t       s_mem[PORT_CAL_SECTORS][FAKE_SECTOR_SIZE];
static uint32_t      s_uid[3];
static size_t        s_reported_size;
static bool          s_power_lost;

static erase_fault_t s_erase_fault;
static size_t        s_tear_erase_words;
static bool          s_tear_erase_from_end;

static bool          s_prog_fault_armed;
static bool          s_prog_fault_tear;      /* tear (power loss) vs. plain refusal */
static bool          s_prog_fault_partial;
static size_t        s_prog_words_left;      /* words that still program normally */

static unsigned      s_erase_count[PORT_CAL_SECTORS];
static unsigned      s_program_words[PORT_CAL_SECTORS];
static unsigned      s_misaligned;
static unsigned      s_dirty;

void fake_flash_reset(void)
{
    memset(s_mem, 0xFF, sizeof s_mem);
    fake_flash_set_uid(0x0034002Bu, 0x34365112u, 0x33353533u);
    s_reported_size = FAKE_SECTOR_SIZE;
    fake_flash_power_cycle();
    memset(s_erase_count, 0, sizeof s_erase_count);
    memset(s_program_words, 0, sizeof s_program_words);
    s_misaligned = 0;
    s_dirty = 0;
}

uint8_t *fake_flash_mem(unsigned idx)
{
    return (idx < PORT_CAL_SECTORS) ? s_mem[idx] : NULL;
}

void fake_flash_set_uid(uint32_t a, uint32_t b, uint32_t c)
{
    s_uid[0] = a;
    s_uid[1] = b;
    s_uid[2] = c;
}

void fake_flash_set_sector_size(size_t size)
{
    s_reported_size = size;
}

void fake_flash_fail_next_erase(void)
{
    s_erase_fault = ERASE_FAIL;
}

void fake_flash_silent_next_erase(void)
{
    s_erase_fault = ERASE_SILENT;
}

void fake_flash_tear_erase(size_t n_words, bool from_end)
{
    s_erase_fault = ERASE_TEAR;
    s_tear_erase_words = n_words;
    s_tear_erase_from_end = from_end;
}

void fake_flash_fail_program_after(size_t n_words_ok)
{
    s_prog_fault_armed = true;
    s_prog_fault_tear = false;
    s_prog_fault_partial = false;
    s_prog_words_left = n_words_ok;
}

void fake_flash_tear_program_after(size_t n_words_ok, bool partial_word)
{
    s_prog_fault_armed = true;
    s_prog_fault_tear = true;
    s_prog_fault_partial = partial_word;
    s_prog_words_left = n_words_ok;
}

void fake_flash_power_cycle(void)
{
    s_power_lost = false;
    s_erase_fault = ERASE_OK;
    s_prog_fault_armed = false;
}

unsigned fake_flash_erase_count(unsigned idx)
{
    return (idx < PORT_CAL_SECTORS) ? s_erase_count[idx] : 0u;
}

unsigned fake_flash_program_words(unsigned idx)
{
    return (idx < PORT_CAL_SECTORS) ? s_program_words[idx] : 0u;
}

unsigned fake_flash_misaligned_programs(void)
{
    return s_misaligned;
}

unsigned fake_flash_dirty_programs(void)
{
    return s_dirty;
}

/* ---- port.h ----------------------------------------------------------------------- */

size_t port_cal_sector_size(void)
{
    return s_reported_size;
}

const uint8_t *port_cal_sector(unsigned idx)
{
    return fake_flash_mem(idx);
}

bool port_cal_erase(unsigned idx)
{
    if (idx >= PORT_CAL_SECTORS || s_power_lost) {
        return false;
    }
    erase_fault_t f = s_erase_fault;
    s_erase_fault = ERASE_OK;
    s_erase_count[idx]++;
    switch (f) {
    case ERASE_FAIL:
        return false;
    case ERASE_SILENT:
        return true;
    case ERASE_TEAR: {
        size_t n = s_tear_erase_words;
        if (n > FAKE_SECTOR_WORDS) {
            n = FAKE_SECTOR_WORDS;
        }
        size_t first = s_tear_erase_from_end ? FAKE_SECTOR_WORDS - n : 0u;
        memset(&s_mem[idx][first * 4u], 0xFF, n * 4u);
        /* the word being erased when power failed: some bits set */
        size_t half = s_tear_erase_from_end ? first - 1u : n;
        if (n < FAKE_SECTOR_WORDS) {
            for (size_t b = 0; b < 4u; b++) {
                s_mem[idx][half * 4u + b] |= 0x5Au;
            }
        }
        s_power_lost = true;
        return false;
    }
    case ERASE_OK:
    default:
        memset(s_mem[idx], 0xFF, FAKE_SECTOR_SIZE);
        return true;
    }
}

bool port_cal_program(unsigned idx, size_t offset, const uint8_t *data, size_t n)
{
    if (idx >= PORT_CAL_SECTORS || s_power_lost || data == NULL) {
        return false;
    }
    if ((offset % 4u) != 0u || (n % 4u) != 0u) {
        s_misaligned++;
        return false;
    }
    if (offset > FAKE_SECTOR_SIZE || n > FAKE_SECTOR_SIZE - offset) {
        return false;
    }
    for (size_t w = 0; w < n / 4u; w++) {
        uint8_t *dst = &s_mem[idx][offset + 4u * w];
        const uint8_t *src = &data[4u * w];
        if (s_prog_fault_armed) {
            if (s_prog_words_left == 0u) {
                s_prog_fault_armed = false;
                if (!s_prog_fault_tear) {
                    return false;
                }
                if (s_prog_fault_partial) {
                    /* only the bits under 0xF0 of each byte made it */
                    for (size_t b = 0; b < 4u; b++) {
                        dst[b] &= (uint8_t)(src[b] | 0x0Fu);
                    }
                }
                s_power_lost = true;
                return false;
            }
            s_prog_words_left--;
        }
        bool erased = dst[0] == 0xFFu && dst[1] == 0xFFu && dst[2] == 0xFFu && dst[3] == 0xFFu;
        if (!erased) {
            s_dirty++;
        }
        for (size_t b = 0; b < 4u; b++) {
            dst[b] &= src[b];
        }
        s_program_words[idx]++;
    }
    return true;
}

void port_uid(uint32_t uid[3])
{
    uid[0] = s_uid[0];
    uid[1] = s_uid[1];
    uid[2] = s_uid[2];
}
