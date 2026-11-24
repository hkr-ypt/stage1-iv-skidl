/*
 * calib.h — calibration coefficients, the session zero, and the persistent
 * calibration record (docs/firmware/calibration.md).
 *
 * Storage: two 16 KB flash sectors (STM32F411 sectors 1 and 2) used
 * alternately. A new record is written to the sector that does NOT hold the
 * newest valid record, so an interrupted erase or program can only destroy
 * the older copy. At boot the valid record (magic, version, size, CRC-32,
 * matching MCU UID, plausible coefficients) with the highest sequence number
 * wins; if none is valid the nominal constants are used and every record is
 * flagged CAL_NOMINAL.
 */
#ifndef CALIB_H
#define CALIB_H

#include "fw_types.h"

typedef struct {
    double g_dac;      /* V_SWEEP volts per DAC code                       */
    double c_dac;      /* DAC code at V_SWEEP = 0                            */
    double g_v;        /* DUT volts per ADC code (V channel)                 */
    double g_i;        /* DUT amperes per ADC code (I channel)               */
    double r_s;        /* ohm, series resistance outside U5's loop           */
    double r_top;      /* ohm, FORCE+ side part of r_s (F1 + R30 + U12)      */
    double i_plateau;  /* A, measured current-limit plateau                  */
} cal_coeffs_t;

/* Session zero: offsets in ADC codes, measured with all jacks open. */
typedef struct {
    bool     valid;
    double   o_v, o_i;       /* mean raw codes                           */
    double   sd_v, sd_i;     /* standard deviation of single conversions */
    uint64_t t_us;           /* when it was taken                        */
    uint32_t sps_x10;        /* data rate it was taken at                */
    bool     armed;          /* gate state during the measurement        */
    uint16_t pairs;
} zero_t;

#define CAL_RECORD_MAGIC    0x31414343u   /* "CCA1" little-endian */
#define CAL_RECORD_VERSION  1u
#define CAL_NOTE_LEN        32u

typedef struct {
    uint32_t     magic;
    uint16_t     version;
    uint16_t     size;          /* sizeof(cal_record_t)                    */
    uint32_t     seq;           /* increments on every save                */
    uint32_t     uid[3];        /* STM32 unique ID this record belongs to  */
    uint32_t     timestamp;     /* host-supplied UTC seconds               */
    uint32_t     measured;      /* CAL_MEASURED_MARK | CAL_M_* (was 0)     */
    cal_coeffs_t c;             /* 7 doubles, 8-byte aligned at offset 32  */
    char         note[CAL_NOTE_LEN];
    uint32_t     reserved1;
    uint32_t     crc;           /* CRC-32 (IEEE) of all preceding bytes    */
} cal_record_t;

/* Which coefficients were measured (set with CAL.SET) rather than nominal:
 * bit k is the k-th CAL.SET key. A record stores them with CAL_MEASURED_MARK;
 * a record written before the mask existed (field 0) loads as fully measured,
 * its old meaning. While any CAL_M_CORE coefficient is nominal, records carry
 * CAL_NOMINAL and the classifier keeps the nominal Kelvin tolerance: a
 * partial calibration (bring-up: i_plateau alone) is not a calibration. */
#define CAL_M_G_DAC        (1u << 0)
#define CAL_M_C_DAC        (1u << 1)
#define CAL_M_G_V          (1u << 2)
#define CAL_M_G_I          (1u << 3)
#define CAL_M_R_S          (1u << 4)
#define CAL_M_R_TOP        (1u << 5)
#define CAL_M_I_PLATEAU    (1u << 6)
#define CAL_M_ALL          0x7Fu
#define CAL_M_CORE         (CAL_M_G_DAC | CAL_M_C_DAC | CAL_M_G_V | CAL_M_G_I | CAL_M_R_S)
#define CAL_MEASURED_MARK  0x80000000u

/* Nominal coefficients computed from nominal.h. */
void calib_nominal(cal_coeffs_t *c);

/* Coefficients within the plausibility window around nominal (rejects typos
 * in CAL.SET and corrupted-but-CRC-valid nonsense):
 *   g_dac +-2 %, c_dac +-1000 codes, g_v and g_i +-5 %, r_s 80..200 ohm,
 *   r_top 70..180 ohm, r_top < r_s, i_plateau 20..100 mA. */
bool calib_plausible(const cal_coeffs_t *c);

/* Build a complete record (computes size and CRC). note may be NULL;
 * measured: CAL_M_* bits. */
void calib_build_record(cal_record_t *r, const cal_coeffs_t *c, uint32_t measured, uint32_t seq,
                        const uint32_t uid[3], uint32_t timestamp, const char *note);

typedef enum {
    CALREC_VALID = 0,
    CALREC_ERASED,       /* all 0xFF                       */
    CALREC_BAD_MAGIC,
    CALREC_BAD_VERSION,
    CALREC_BAD_CRC,
    CALREC_WRONG_UID,
    CALREC_IMPLAUSIBLE,
} calrec_status_t;

calrec_status_t calib_check(const cal_record_t *r, const uint32_t uid[3]);

typedef struct {
    calrec_status_t status[2];
    int             newest;       /* -1 if neither sector is valid */
    uint32_t        newest_seq;
} calib_scan_t;

/* Examine both sectors (each at least sizeof(cal_record_t) bytes, record at
 * offset 0). */
void calib_scan(const uint8_t *sec0, const uint8_t *sec1, const uint32_t uid[3],
                calib_scan_t *out);
/* The sector a new record must be written to: the one NOT holding the newest
 * valid record (sector 0 if neither is valid). */
unsigned calib_target_sector(const calib_scan_t *scan);

/* Boot-time load through the port: fills *out with the newest valid record's
 * coefficients, or nominal ones; returns true if a stored record was used.
 * *measured_out (may be NULL): that record's CAL_M_* bits, 0 if nominal. */
bool calib_load(cal_coeffs_t *out, calib_scan_t *scan_out, uint32_t *seq_out,
                uint32_t *measured_out);

/* Persist: erase the target sector, program, read back and verify. The caller
 * guarantees DISARMED with the heartbeat stopped (SI-8). Returns E_OK,
 * E_RANGE (implausible coefficients) or E_FLASH. */
err_t calib_save(const cal_coeffs_t *c, uint32_t measured, uint32_t timestamp, const char *note,
                 uint32_t *seq_out);

/* CRC-32 (IEEE 802.3, reflected, init/xorout 0xFFFFFFFF). */
uint32_t crc32_ieee(const void *data, size_t n);

#endif /* CALIB_H */
