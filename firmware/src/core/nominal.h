/*
 * nominal.h — the component values the nominal scaling constants are derived
 * from. Each value names its source in the SKiDL design (blocks/<name>.py); the CI
 * check firmware/tools/check_nominal.py parses this file and the SKiDL blocks
 * and fails if any value disagrees, so a hardware change cannot leave a stale
 * firmware constant behind.
 *
 * Format consumed by check_nominal.py, one per line:
 *   #define NOM_<NAME> <value>  /- @ref <REF> <file> -/   (with real comment marks)
 */
#ifndef NOMINAL_H
#define NOMINAL_H

/* Sweep level shift (blocks/dac_sweep.py): V_SWEEP = (R4/R3)(V_A - V_B/2). */
#define NOM_R3_OHM      2490.0     /* @ref R3 blocks/dac_sweep.py */
#define NOM_R4_OHM      20000.0    /* @ref R4 blocks/dac_sweep.py */
#define NOM_R5_OHM      2490.0     /* @ref R5 blocks/dac_sweep.py */
#define NOM_R6_OHM      20000.0    /* @ref R6 blocks/dac_sweep.py */
#define NOM_R1_OHM      10000.0    /* @ref R1 blocks/dac_sweep.py */
#define NOM_R2_OHM      10000.0    /* @ref R2 blocks/dac_sweep.py */

/* Kelvin attenuator (blocks/voltage_sense.py): k = R32/(R12+R32); U7 G = 1. */
#define NOM_R12_OHM     40200.0    /* @ref R12 blocks/voltage_sense.py */
#define NOM_R13_OHM     40200.0    /* @ref R13 blocks/voltage_sense.py */
#define NOM_R32_OHM     10000.0    /* @ref R32 blocks/voltage_sense.py */
#define NOM_R33_OHM     10000.0    /* @ref R33 blocks/voltage_sense.py */

/* Current sense (blocks/current_sense.py): G = 1 + 50k/R11, shunt R10. */
#define NOM_R10_OHM     10.0       /* @ref R10 blocks/current_sense.py */
#define NOM_R11_OHM     35700.0    /* @ref R11 blocks/current_sense.py */
#define NOM_INA_RG_INTERNAL_OHM 50000.0 /* INA128 gain equation, SBOS051G */

/* ADC input filter (blocks/adc.py) and the buffer-off input impedance. */
#define NOM_R15_OHM     100.0      /* @ref R15 blocks/adc.py */
#define NOM_R17_OHM     100.0      /* @ref R17 blocks/adc.py */
#define NOM_ADS_ZEFF_PGA2_OHM 59600.0 /* ZeffA || ZeffB at PGA 2, SBAS288K Table 10 */

/* Force path outside U5's loop (blocks/protection.py, current_sense.py). */
#define NOM_R30_OHM     100.0      /* @ref R30 blocks/protection.py */
#define NOM_PTC_OHM     5.0        /* F1, F2: 0ZRM0010FF1E, 3.0-7.5 ohm (Bel); mid value */
#define NOM_U12_RON_OHM 1.0        /* TLP176AM, <= 2 ohm (Toshiba); mid value */

/* Reference and converters. */
#define NOM_VREF_V      2.5        /* REF5025AI standard grade: 0.1 %, 8 ppm/C */
#define NOM_ADS_FULLSCALE_CODE 8388607.0 /* 2^23 - 1, SBAS288K LSB definition */
#define NOM_DAC_CODES   65536.0
#define NOM_DAC_B_CODE  65535.0

/* OPA548 current-limit plateau: 49.4 mA typical at R7 = 1.43 Mohm, NOT a
 * characterised limit (SBOS070D specifies ILIM only at 2.5 A). Replaced by
 * the bring-up measurement (i_plateau in the calibration record). */
#define NOM_I_PLATEAU_A 0.049

#endif /* NOMINAL_H */
