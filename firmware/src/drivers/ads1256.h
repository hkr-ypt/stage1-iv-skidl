/*
 * ads1256.h — the only code that talks to U8 (ADS1256, TI SBAS288K) on SPI2.
 *
 * Board facts (blocks/adc.py): f_CLKIN 7.68 MHz (tau = 130.2 ns), VREF 2.500 V,
 * AINCOM on VREF_2V5, current = AIN0-AINCOM, voltage = AIN2-AINCOM,
 * AIN1/AIN3 = 100 ohm to AGND (self-check inputs), SYNC/PDWN tied high,
 * /RESET on PA9, /DRDY on PA8, D0/CLKOUT and D1-D3 unconnected.
 *
 * Driver model (docs/firmware/firmware_spec.md §7.4):
 *  - Non-blocking state machine; ads_service() performs at most ONE SPI
 *    transaction per call (each <= ~40 us at 1.5 MHz SCLK).
 *  - Every conversion is a mux-cycling settled conversion:
 *      WREG MUX -> (t11 4 tau) -> SYNC -> (24 tau) -> WAKEUP -> (24 tau),
 *    then arm /DRDY capture (EXTI pending cleared AFTER WAKEUP), then RDATA
 *    after the first valid falling edge.
 *  - Stale edge rejection: an edge earlier than 0.9 * t18(DR) after WAKEUP is
 *    ignored and capture re-armed.
 *  - Read deadline: RDATA only while /DRDY is low and
 *      now - t_drdy <= 1/DR - (t6 + 24 SCLK + t17 + 10 us);
 *    otherwise wait for the next edge and mark the sample `late`.
 *  - t6 (50 tau = 6.51 us) between RDATA/RREG and the data bytes; t10
 *    (8 tau = 1.04 us) /CS hold after the last SCLK; explicit delays.
 *  - Init after every RESET-pin pulse: STATUS = 0x00 (MSB first, ACAL off,
 *    BUFEN off), MUX, ADCON = CLK 00 (CLKOUT off) | SDCS 00 | PGA,
 *    DRATE, IO = 0x00 (D0-D3 outputs driven low); SYNC; SELFCAL; wait /DRDY;
 *    read back registers 0x00-0x04 and compare.
 *  - Integrity while running: register scrub (RREG 0x00-0x04 vs shadow,
 *    STATUS masked to bits 3:1) at least every ADC_SCRUB_PERIOD_US when idle
 *    and on request; stuck-DOUT check (32 consecutive raw 0x000000 or
 *    0xFFFFFF codes covering both measurement channels).
 *  - Never used: RDATAC/SDATAC, SYSOCAL/SYSGCAL, ACAL, BUFEN = 1, SDCS != 00,
 *    OFC/FSC writes.
 */
#ifndef ADS1256_H
#define ADS1256_H

#include "fw_types.h"

/* ---- commands (SBAS288K Table 24) ---------------------------------------- */
#define ADS_CMD_WAKEUP   0x00u
#define ADS_CMD_RDATA    0x01u
#define ADS_CMD_RREG     0x10u   /* | reg */
#define ADS_CMD_WREG     0x50u   /* | reg */
#define ADS_CMD_SELFCAL  0xF0u
#define ADS_CMD_SYNC     0xFCu
#define ADS_CMD_STANDBY  0xFDu
#define ADS_CMD_RESET    0xFEu

/* ---- registers --------------------------------------------------------------- */
#define ADS_REG_STATUS   0x00u
#define ADS_REG_MUX      0x01u
#define ADS_REG_ADCON    0x02u
#define ADS_REG_DRATE    0x03u
#define ADS_REG_IO       0x04u

/* ---- input mux selections used on this board ---------------------------------- */
typedef enum {
    ADS_MUX_I      = 0x08,  /* AIN0 - AINCOM : current channel                */
    ADS_MUX_V      = 0x28,  /* AIN2 - AINCOM : Kelvin voltage channel         */
    ADS_MUX_ZERO   = 0x13,  /* AIN1 - AIN3   : ADC zero self-check            */
    ADS_MUX_NEGREF = 0x18,  /* AIN1 - AINCOM : -VREF self-check (PGA 1 only)  */
} ads_mux_t;

/* ---- PGA codes (ADCON bits 2:0) ------------------------------------------------ */
#define ADS_PGA_1   0u
#define ADS_PGA_2   1u
#define ADS_PGA_64  6u

/* ---- data rates supported in v1 (<= 3750 SPS) ---------------------------------- */
typedef struct {
    uint16_t sps_x10;     /* data rate x 10 (2.5 SPS -> 25)                      */
    uint8_t  drate;       /* DRATE register code                                  */
    uint32_t t18_us;      /* settling time with SYNC (SBAS288K Table 13)          */
    uint32_t selfcal_us;  /* SELFCAL duration at PGA 1..64 (Table 21; <=3750 SPS) */
} ads_rate_t;

/* Look up a rate by SPS x 10 (e.g. 10000 for 1000 SPS); NULL if not in v1 set. */
const ads_rate_t *ads_rate_by_sps_x10(uint32_t sps_x10);
const ads_rate_t *ads_rate_by_code(uint8_t drate);
const ads_rate_t *ads_rate_table(size_t *count);
uint32_t          ads_period_us(const ads_rate_t *r);     /* 1/DR, rounded up   */
/* Latest RDATA start after a /DRDY edge that still finishes before t17:
 * 1/DR - (t6 + 24 SCLK + t17 + 10 us). */
uint32_t          ads_read_deadline_us(const ads_rate_t *r);

typedef struct {
    uint8_t drate;        /* DRATE code from the v1 table */
    uint8_t pga;          /* ADS_PGA_x                     */
} ads_cfg_t;

typedef struct {
    int32_t   code;        /* sign-extended 24-bit two's complement             */
    ads_mux_t mux;
    uint64_t  t_start_us;  /* WAKEUP issued                                      */
    uint64_t  t_drdy_us;   /* accepted /DRDY edge                                 */
    uint64_t  t_read_us;   /* RDATA issued                                        */
    bool      late;        /* read was not within the deadline of the first edge */
    bool      clipped;     /* code == 0x7FFFFF or 0x800000                        */
} ads_sample_t;

typedef enum {
    ADS_OFF = 0,     /* not initialised                                      */
    ADS_INIT,        /* reset / configure / self-calibrate in progress       */
    ADS_IDLE,        /* ready: ads_start() accepted                           */
    ADS_CONVERTING,  /* conversion in flight                                  */
    ADS_ERROR,       /* see ads_error(); needs ads_init_begin()               */
} ads_state_t;

typedef enum {
    ADS_ERR_NONE = 0,
    ADS_ERR_TIMEOUT,    /* no /DRDY within the timeout (init, cal or conversion) */
    ADS_ERR_READBACK,   /* register read-back differs from the shadow           */
    ADS_ERR_SPI,        /* SPI2 transfer timed out                               */
    ADS_ERR_STUCK,      /* stuck-DOUT pattern                                    */
} ads_err_t;

/* Start (or restart) the full init sequence with the given configuration. */
void        ads_init_begin(const ads_cfg_t *cfg);
/* Reconfigure (PGA and/or rate) from IDLE: WREG, SYNC, SELFCAL, read-back.
 * Returns false (and does nothing) unless the driver is IDLE. */
bool        ads_reconfigure(const ads_cfg_t *cfg);
void        ads_service(void);
ads_state_t ads_state(void);
ads_err_t   ads_error(void);
bool        ads_idle(void);
const ads_cfg_t *ads_config(void);

/* Begin a settled conversion of `mux`. Accepted in IDLE, and in CONVERTING,
 * where it preempts the conversion in flight (SYNC discards it). The WREG/
 * SYNC/WAKEUP transaction is issued on the next ads_service() call. */
bool        ads_start(ads_mux_t mux);
/* If a completed sample is available, copy it out, return true and go IDLE. */
bool        ads_take(ads_sample_t *out);
/* Request a register scrub at the next idle opportunity. */
void        ads_request_scrub(void);

/* Diagnostics. */
typedef struct {
    uint32_t conversions;   /* samples delivered                              */
    uint32_t late;          /* samples read after the first-edge deadline     */
    uint32_t stale_edges;   /* /DRDY edges rejected as too early              */
    uint32_t read_overruns; /* reads that ran into t17 (preempted): discarded */
    uint32_t scrubs;        /* register scrubs performed                      */
    uint32_t timeouts;      /* conversion / calibration timeouts              */
    uint32_t inits;         /* full init sequences started                    */
} ads_stats_t;
void        ads_get_stats(ads_stats_t *out);
uint8_t     ads_last_id(void);              /* STATUS[7:4] from the last read-back */
void        ads_shadow_regs(uint8_t out[5]);
int32_t     ads_sign_extend(uint32_t raw24);

#if FW_BRINGUP
/* ADC.RESETPULSE: pulse /RESET without updating the driver (tests the scrub). */
void        ads_pulse_reset_unannounced(void);
#endif

#endif /* ADS1256_H */
