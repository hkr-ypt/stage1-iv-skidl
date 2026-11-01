/*
 * flash.c — calibration storage in flash sectors 1 and 2 of the
 * STM32F411RET6 (port_cal_* of port.h; firmware_spec.md §7.10).
 *
 * Facts relied on (RM0383 Rev 4 §3.5-§3.8, Table 4, DS10314 Table 45):
 *  - sector 1 = 0x08004000, sector 2 = 0x08008000, 16 KiB each; the linker
 *    script places nothing there (stm32f411re.ld).
 *  - unlock with KEYR 0x45670123 then 0xCDEF89AB; PSIZE = 10 (x32) for
 *    2.7-3.6 V; sector erase SER + SNB + STRT; word program PG; wait for
 *    BSY = 0; error flags PGSERR, PGPERR, PGAERR, WRPERR, RDERR in FLASH_SR
 *    (write 1 to clear). Erase takes 250 ms typ / 500 ms max and stalls
 *    every flash read meanwhile: callers guarantee DISARMED (SI-8).
 *  - After an erase or program the data cache may hold stale lines: it is
 *    reset (ACR.DCRST with DCEN cleared) before the data are read back
 *    (RM0383 §3.4.2).
 */
#include "port_stm32.h"

#define CAL_SECTOR_BYTES   16384u
#define SECTOR_OF(idx)     ((idx) + 1u)          /* cal 0 -> sector 1     */
#define ERASE_TIMEOUT_US   2000000u
#define PROG_TIMEOUT_US    1000u
#define SR_ERRORS (FLASH_SR_PGSERR | FLASH_SR_PGPERR | FLASH_SR_PGAERR | FLASH_SR_WRPERR \
                   | FLASH_SR_RDERR)

size_t port_cal_sector_size(void)
{
    return CAL_SECTOR_BYTES;
}

const uint8_t *port_cal_sector(unsigned idx)
{
    if (idx >= PORT_CAL_SECTORS) {
        return NULL;
    }
    return __cal_flash_start + (size_t)idx * CAL_SECTOR_BYTES;
}

static void unlock(void)
{
    if (FLASH->CR & FLASH_CR_LOCK) {
        FLASH->KEYR = 0x45670123u;
        FLASH->KEYR = 0xCDEF89ABu;
    }
}

static void lock(void)
{
    FLASH->CR = FLASH_CR_LOCK;
}

static void dcache_reset(void)
{
    uint32_t acr = FLASH->ACR;
    FLASH->ACR = acr & ~FLASH_ACR_DCEN;
    FLASH->ACR = (acr & ~FLASH_ACR_DCEN) | FLASH_ACR_DCRST;
    FLASH->ACR = acr & ~FLASH_ACR_DCRST;
}

static bool wait_done(uint32_t timeout_us)
{
    bool ok = stm32_wait_reg(&FLASH->SR, FLASH_SR_BSY, 0u, timeout_us);
    return ok && (FLASH->SR & SR_ERRORS) == 0u;
}

bool port_cal_erase(unsigned idx)
{
    if (idx >= PORT_CAL_SECTORS) {
        return false;
    }
    unlock();
    if (!stm32_wait_reg(&FLASH->SR, FLASH_SR_BSY, 0u, ERASE_TIMEOUT_US)) {
        lock();
        return false;
    }
    FLASH->SR = SR_ERRORS | FLASH_SR_EOP;
    FLASH->CR = FLASH_CR_PSIZE_1 | FLASH_CR_SER | (SECTOR_OF(idx) << FLASH_CR_SNB_Pos);
    FLASH->CR |= FLASH_CR_STRT;
    bool ok = wait_done(ERASE_TIMEOUT_US);
    lock();
    dcache_reset();
    return ok;
}

bool port_cal_program(unsigned idx, size_t offset, const uint8_t *data, size_t n)
{
    if (idx >= PORT_CAL_SECTORS || (offset % 4u) != 0u || (n % 4u) != 0u
        || offset + n > CAL_SECTOR_BYTES || data == NULL) {
        return false;
    }
    unlock();
    FLASH->SR = SR_ERRORS | FLASH_SR_EOP;
    bool ok = true;
    volatile uint32_t *dst = (volatile uint32_t *)(uintptr_t)(port_cal_sector(idx) + offset);
    for (size_t i = 0; i < n; i += 4u) {
        uint32_t w = (uint32_t)data[i] | ((uint32_t)data[i + 1u] << 8)
                   | ((uint32_t)data[i + 2u] << 16) | ((uint32_t)data[i + 3u] << 24);
        FLASH->CR = FLASH_CR_PSIZE_1 | FLASH_CR_PG;
        dst[i / 4u] = w;
        __DSB();
        if (!wait_done(PROG_TIMEOUT_US)) {
            ok = false;
            break;
        }
    }
    FLASH->CR &= ~FLASH_CR_PG;
    lock();
    dcache_reset();
    return ok;
}
