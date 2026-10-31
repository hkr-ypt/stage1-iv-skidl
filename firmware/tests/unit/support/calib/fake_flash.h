/*
 * fake_flash.h — test port for test_calib: two 16 KB calibration sectors
 * with STM32F411 flash semantics (RM0383 §3.5: erase sets every bit to 1,
 * programming can only clear bits, x32 parallelism programs aligned 32-bit
 * words), a settable MCU UID, and fault injection: refused or silently
 * ineffective erases, refused programs, and "torn" erases/programs that stop
 * part-way (power loss), after which every flash operation fails until
 * fake_flash_power_cycle().
 */
#ifndef FAKE_FLASH_H
#define FAKE_FLASH_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FAKE_SECTOR_SIZE  16384u
#define FAKE_SECTOR_WORDS (FAKE_SECTOR_SIZE / 4u)

/* Both sectors erased, default UID, no faults, counters cleared. */
void     fake_flash_reset(void);
/* Direct access to a sector's bytes (for corruption and inspection). */
uint8_t *fake_flash_mem(unsigned idx);
void     fake_flash_set_uid(uint32_t a, uint32_t b, uint32_t c);
/* Report a smaller sector size from port_cal_sector_size(). */
void     fake_flash_set_sector_size(size_t size);

/* One-shot faults, armed for the next matching operation. */
void     fake_flash_fail_next_erase(void);     /* returns false, no change   */
void     fake_flash_silent_next_erase(void);   /* returns true, no change    */
/* The next erase erases `n_words` words (from the start of the sector, or
 * backwards from its end), half-erases the next word, then power is lost. */
void     fake_flash_tear_erase(size_t n_words, bool from_end);
/* After `n_words_ok` more words have been programmed, the next program call
 * returns false without changing anything (power stays on). */
void     fake_flash_fail_program_after(size_t n_words_ok);
/* After `n_words_ok` more words have been programmed, the next word is
 * programmed only partly (if `partial_word`) or not at all, then power is
 * lost. */
void     fake_flash_tear_program_after(size_t n_words_ok, bool partial_word);
/* Power restored: faults cleared, contents kept. */
void     fake_flash_power_cycle(void);

/* Counters since the last reset. */
unsigned fake_flash_erase_count(unsigned idx);
unsigned fake_flash_program_words(unsigned idx);
unsigned fake_flash_misaligned_programs(void);  /* offset or length not x4 */
unsigned fake_flash_dirty_programs(void);       /* word programmed while not erased */

#endif /* FAKE_FLASH_H */
