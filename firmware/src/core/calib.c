/*
 * calib.c — nominal coefficients, plausibility window, the persistent
 * calibration record and its two-sector flash storage (contract: calib.h;
 * docs/firmware/firmware_spec.md §7.10).
 *
 * Facts relied on:
 *  - Component values: nominal.h (checked against the SKiDL blocks,
 *    blocks/<name>.py, in CI by tools/check_nominal.py).
 *  - DAC8552 (SLAS430A): 16-bit, V_OUT = VREF * code / 65536, so the U3 level
 *    shift gives V_SWEEP = (R4/R3) VREF (code_A - code_B/2) / 65536; with
 *    code_B = 0xFFFF the zero is at c_dac = 65535/2 = 32767.5.
 *  - ADS1256 (SBAS288K): LSB = 2 VREF / (PGA (2^23 - 1)), PGA fixed at 2;
 *    with the input buffer off the source resistance R15/R17 (100 ohm) works
 *    against ZeffA || ZeffB = 59.6 kohm at PGA 2 (Table 10), a gain error of
 *    (1 + R/Zeff) that the ADC self-calibration does not remove.
 *  - INA128 (SBOS051G): G = 1 + 50 kohm / RG, RG = R11.
 *  - STM32F411 flash (RM0383 §3.5): erase sets every bit of a sector to 1,
 *    programming can only clear bits, x32 parallelism programs one aligned
 *    32-bit word per operation. Sectors 1 and 2 are 16 KB each.
 *
 * Storage protocol: a new record goes to the sector that does NOT hold the
 * newest valid record (calib_target_sector), is programmed word by word in
 * address order (the CRC word last) and is read back and verified. An
 * interruption at any point can only damage the target sector, which held
 * at most the older copy; the CRC (plus magic/version/size/UID and the
 * plausibility window) keeps a damaged record from ever being selected.
 */
#include "calib.h"

#include <string.h>

#include "nominal.h"
#include "port.h"

_Static_assert(sizeof(cal_record_t) == 128u, "calibration record must be 128 bytes");
_Static_assert(offsetof(cal_record_t, c) == 32u, "coefficients at offset 32");
_Static_assert(offsetof(cal_record_t, crc) == sizeof(cal_record_t) - 4u,
               "CRC must be the last word");
_Static_assert(sizeof(cal_record_t) % 4u == 0u, "record is programmed in words");

#define CAL_PGA            2.0
#define CRC_POLY_REFLECTED 0xEDB88320u

/* ---- CRC-32 ------------------------------------------------------------- */

uint32_t crc32_ieee(const void *data, size_t n)
{
    const uint8_t *p = (const uint8_t *)data;
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) {
        crc ^= p[i];
        for (unsigned b = 0; b < 8u; b++) {
            uint32_t mask = (uint32_t)0 - (crc & 1u);
            crc = (crc >> 1) ^ (CRC_POLY_REFLECTED & mask);
        }
    }
    return crc ^ 0xFFFFFFFFu;
}

/* ---- coefficients -------------------------------------------------------- */

void calib_nominal(cal_coeffs_t *c)
{
    const double lsb = 2.0 * NOM_VREF_V / (CAL_PGA * NOM_ADS_FULLSCALE_CODE);
    const double k_att = NOM_R32_OHM / (NOM_R12_OHM + NOM_R32_OHM);
    const double ina_gain = 1.0 + NOM_INA_RG_INTERNAL_OHM / NOM_R11_OHM;

    c->g_dac = (NOM_R4_OHM / NOM_R3_OHM) * NOM_VREF_V / NOM_DAC_CODES;
    c->c_dac = NOM_DAC_B_CODE / 2.0;
    c->g_v = lsb / k_att * (1.0 + NOM_R17_OHM / NOM_ADS_ZEFF_PGA2_OHM);
    c->g_i = lsb / (ina_gain * NOM_R10_OHM) * (1.0 + NOM_R15_OHM / NOM_ADS_ZEFF_PGA2_OHM);
    c->r_s = NOM_R30_OHM + 2.0 * NOM_PTC_OHM + NOM_U12_RON_OHM + NOM_R10_OHM;
    c->r_top = NOM_PTC_OHM + NOM_R30_OHM + NOM_U12_RON_OHM;
    c->i_plateau = NOM_I_PLATEAU_A;
}

/* lo <= x <= hi; false for NaN. */
static bool in_window(double x, double lo, double hi)
{
    return x >= lo && x <= hi;
}

static bool within_rel(double x, double nominal, double rel)
{
    return in_window(x, nominal * (1.0 - rel), nominal * (1.0 + rel));
}

/* The zero code moves with the DAC8552 errors (SLAS430, -40..105 C): zero-code
 * error 12 mV, gain error 0.2 % FSR at mid-scale 2.5 mV and half of channel
 * B's 0.5 % FSR full-scale error 6.25 mV, i.e. 20.75 mV or 544 codes of
 * 38.15 uV; with INL, the R1/R2 ratio, the difference amplifier's resistors
 * and the OPA2277 offsets the corner case is about 615 codes. A dropped or
 * extra digit still lands far outside (a thousands-digit slip can land
 * inside; iv-cal computes c_dac, and a calibrated board with a wrong c_dac
 * flags KELVIN on every point). */
#define C_DAC_WINDOW_CODES 1000.0

bool calib_plausible(const cal_coeffs_t *c)
{
    if (c == NULL) {
        return false;
    }
    cal_coeffs_t n;
    calib_nominal(&n);
    return within_rel(c->g_dac, n.g_dac, 0.02)
        && in_window(c->c_dac, n.c_dac - C_DAC_WINDOW_CODES, n.c_dac + C_DAC_WINDOW_CODES)
        && within_rel(c->g_v, n.g_v, 0.05)
        && within_rel(c->g_i, n.g_i, 0.05)
        && in_window(c->r_s, 80.0, 200.0)
        && in_window(c->r_top, 70.0, 180.0)
        && c->r_top < c->r_s
        && in_window(c->i_plateau, 0.020, 0.100);
}

/* ---- record ----------------------------------------------------------------- */

static uint32_t record_crc(const cal_record_t *r)
{
    return crc32_ieee(r, offsetof(cal_record_t, crc));
}

void calib_build_record(cal_record_t *r, const cal_coeffs_t *c, uint32_t measured, uint32_t seq,
                        const uint32_t uid[3], uint32_t timestamp, const char *note)
{
    memset(r, 0, sizeof *r);
    r->magic = CAL_RECORD_MAGIC;
    r->version = (uint16_t)CAL_RECORD_VERSION;
    r->size = (uint16_t)sizeof(cal_record_t);
    r->seq = seq;
    r->uid[0] = uid[0];
    r->uid[1] = uid[1];
    r->uid[2] = uid[2];
    r->timestamp = timestamp;
    r->measured = CAL_MEASURED_MARK | (measured & CAL_M_ALL);
    r->c = *c;
    if (note != NULL) {
        /* At most CAL_NOTE_LEN - 1 characters: the note is always a
         * NUL-terminated string; the rest of the field stays zero. */
        for (size_t i = 0; i + 1u < CAL_NOTE_LEN && note[i] != '\0'; i++) {
            r->note[i] = note[i];
        }
    }
    r->crc = record_crc(r);
}

static bool all_erased(const void *p, size_t n)
{
    const uint8_t *b = (const uint8_t *)p;
    for (size_t i = 0; i < n; i++) {
        if (b[i] != 0xFFu) {
            return false;
        }
    }
    return true;
}

/* Order: erased, magic, version, size (a size change is a format change and
 * is reported as CALREC_BAD_VERSION), CRC, UID, plausibility. */
calrec_status_t calib_check(const cal_record_t *r, const uint32_t uid[3])
{
    if (all_erased(r, sizeof *r)) {
        return CALREC_ERASED;
    }
    if (r->magic != CAL_RECORD_MAGIC) {
        return CALREC_BAD_MAGIC;
    }
    if (r->version != CAL_RECORD_VERSION) {
        return CALREC_BAD_VERSION;
    }
    if (r->size != sizeof(cal_record_t)) {
        return CALREC_BAD_VERSION;
    }
    if (r->crc != record_crc(r)) {
        return CALREC_BAD_CRC;
    }
    if (r->uid[0] != uid[0] || r->uid[1] != uid[1] || r->uid[2] != uid[2]) {
        return CALREC_WRONG_UID;
    }
    if (!calib_plausible(&r->c)) {
        return CALREC_IMPLAUSIBLE;
    }
    return CALREC_VALID;
}

/* Sector contents are copied into an aligned record before inspection: the
 * sector pointer is a byte pointer (memory-mapped flash or a test buffer). */
static calrec_status_t check_bytes(const uint8_t *sec, const uint32_t uid[3],
                                   cal_record_t *copy)
{
    if (sec == NULL) {
        memset(copy, 0, sizeof *copy);
        return CALREC_BAD_MAGIC;
    }
    memcpy(copy, sec, sizeof *copy);
    return calib_check(copy, uid);
}

void calib_scan(const uint8_t *sec0, const uint8_t *sec1, const uint32_t uid[3],
                calib_scan_t *out)
{
    const uint8_t *sec[2] = { sec0, sec1 };
    out->newest = -1;
    out->newest_seq = 0;
    for (int i = 0; i < 2; i++) {
        cal_record_t rec;
        out->status[i] = check_bytes(sec[i], uid, &rec);
        if (out->status[i] == CALREC_VALID &&
            (out->newest < 0 || rec.seq > out->newest_seq)) {
            out->newest = i;
            out->newest_seq = rec.seq;
        }
    }
}

unsigned calib_target_sector(const calib_scan_t *scan)
{
    return (scan->newest == 0) ? 1u : 0u;
}

/* ---- storage through the port ------------------------------------------------- */

static bool sectors_usable(void)
{
    return port_cal_sector_size() >= sizeof(cal_record_t);
}

static void scan_port(const uint32_t uid[3], calib_scan_t *scan)
{
    if (sectors_usable()) {
        calib_scan(port_cal_sector(0), port_cal_sector(1), uid, scan);
    } else {
        calib_scan(NULL, NULL, uid, scan);
    }
}

/* A record written before the mask existed (no CAL_MEASURED_MARK): a
 * coefficient CAL.SET never touched is still the exact nominal double, so it
 * is not counted as measured. The bring-up record (i_plateau alone) thereby
 * stays CAL_NOMINAL; a real calibration's fitted values are never bit-equal
 * to the nominal ones. */
static const double *coeff(const cal_coeffs_t *c, unsigned k)
{
    switch (k) {
    case 0:  return &c->g_dac;
    case 1:  return &c->c_dac;
    case 2:  return &c->g_v;
    case 3:  return &c->g_i;
    case 4:  return &c->r_s;
    case 5:  return &c->r_top;
    default: return &c->i_plateau;
    }
}

static uint32_t legacy_measured(const cal_coeffs_t *c)
{
    cal_coeffs_t nom;
    calib_nominal(&nom);
    uint32_t m = 0u;
    for (unsigned k = 0; k < 7u; k++) {
        if (memcmp(coeff(c, k), coeff(&nom, k), sizeof(double)) != 0) {
            m |= 1u << k;   /* CAL_M_* follow the field order */
        }
    }
    return m;
}

bool calib_load(cal_coeffs_t *out, calib_scan_t *scan_out, uint32_t *seq_out,
                uint32_t *measured_out)
{
    uint32_t uid[3];
    calib_scan_t scan;
    port_uid(uid);
    scan_port(uid, &scan);

    bool stored = false;
    uint32_t measured = 0u;
    if (scan.newest >= 0) {
        cal_record_t rec;
        /* Re-check the copy that is actually used. */
        if (check_bytes(port_cal_sector((unsigned)scan.newest), uid, &rec) == CALREC_VALID) {
            *out = rec.c;
            stored = true;
            measured = (rec.measured & CAL_MEASURED_MARK) ? (rec.measured & CAL_M_ALL)
                                                          : legacy_measured(&rec.c);
        }
    }
    if (!stored) {
        calib_nominal(out);
    }
    if (measured_out != NULL) {
        *measured_out = measured;
    }
    if (scan_out != NULL) {
        *scan_out = scan;
    }
    if (seq_out != NULL) {
        *seq_out = stored ? scan.newest_seq : 0u;
    }
    return stored;
}

err_t calib_save(const cal_coeffs_t *c, uint32_t measured, uint32_t timestamp, const char *note,
                 uint32_t *seq_out)
{
    if (c == NULL || !calib_plausible(c)) {
        return E_RANGE;                 /* nothing is written */
    }
    if (!sectors_usable()) {
        return E_FLASH;
    }

    uint32_t uid[3];
    calib_scan_t scan;
    port_uid(uid);
    scan_port(uid, &scan);
    if (scan.newest >= 0 && scan.newest_seq == UINT32_MAX) {
        return E_FLASH;                 /* the sequence cannot advance */
    }
    uint32_t seq = (scan.newest >= 0) ? scan.newest_seq + 1u : 1u;
    unsigned target = calib_target_sector(&scan);

    cal_record_t rec;
    calib_build_record(&rec, c, measured, seq, uid, timestamp, note);

    if (!port_cal_erase(target)) {
        return E_FLASH;
    }
    const uint8_t *sec = port_cal_sector(target);
    if (sec == NULL || !all_erased(sec, sizeof rec)) {
        return E_FLASH;                 /* the erase did not take */
    }

    const uint8_t *bytes = (const uint8_t *)&rec;
    for (size_t off = 0; off < sizeof rec; off += 4u) {
        if (!port_cal_program(target, off, bytes + off, 4u)) {
            return E_FLASH;
        }
    }

    cal_record_t back;
    if (check_bytes(port_cal_sector(target), uid, &back) != CALREC_VALID ||
        memcmp(&back, &rec, sizeof rec) != 0) {
        return E_FLASH;
    }
    if (seq_out != NULL) {
        *seq_out = seq;
    }
    return E_OK;
}
