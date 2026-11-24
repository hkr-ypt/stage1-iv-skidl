# Calibration and data format

## 1. Model

The firmware converts raw codes with a linear model (spec §7.10):

| Quantity | Model | Nominal (from `nominal.h`, checked against `blocks/*.py`) |
|---|---|---|
| setpoint | `V_set = g_dac · (code − c_dac)` | g_dac = 306.4014 µV/code, c_dac = 32767.5 |
| DUT voltage | `V_K = g_v · (code − o_v)` | g_v = 1.498587 µV/code (incl. ×1.00168 input-impedance factor) |
| DUT current | `I = g_i · (code − o_i)` | g_i = 12.435568 nA/code |
| series resistance | `R_s`, `R_top` | 121 Ω, 106 Ω |
| plateau | `i_plateau` | 0.049 A — a **nominal** value set by R7, **not** characterised; replace it with the measured plateau |

`o_v`, `o_i` come from the **session zero** (`ZERO`), not from flash.
The firmware keeps track of which coefficients were measured (set with
`CAL.SET`; `CAL?` `measured=`, saved with the record). While any of `g_dac`,
`c_dac`, `g_v`, `g_i`, `r_s` is still nominal, every record carries
`CAL_NOMINAL`, `STAT?` warns, and the Kelvin tolerance is the wide one. A
partial calibration, such as `i_plateau` alone after the bring-up plateau
test (checklist §7), therefore does not switch to the tight tolerance on
uncalibrated gains.

## 2. Procedure (bring-up, then periodically)

The guided tool `iv-cal` runs these steps and shows the resulting `CAL.SET`
line before sending it and saving it (`CAL.SAVE`):

1. **Zero** (`ZERO`): all four jacks open, DUT removed, output at 0 V
   (armed or disarmed). Up to 64 pairs, ≤ 10 s. Rejected if \|o_i\| > 100 µA or
   \|o_v\| > 10 mV equivalent: the INA128P worst-case offsets to 85 °C referred
   to the DUT are 79 µA and 8.9 mV (SBOS051G: (50 + 500/G) µV at 25 °C plus
   (0.5 + 20/G) µV/°C), so an in-spec board is never refused. Repeat each session, after a data-rate change or
   `ADC.SELFCAL`, and when the temperature has moved: the INA128 offset drift
   is up to 0.88 µA/°C, so a zero holds the ±1 µA offset term only within
   about ±1 °C.
2. **Gains** (`g_v`, `g_i`): a 0.01 % reference resistor, 4-wire, Kelvin
   leads on its terminals, a calibrated DMM across it; several setpoints of
   each polarity; least squares of the raw codes against the DMM values.
3. **DAC** (`g_dac`, `c_dac`): DUT open, Kelvin leads on the FORCE
   terminals; `V_SWEEP = V_K·(1 + R_top/R_sd)`; codes 485…64741 only.
4. **Series resistance** (`r_s`): `y = V_set − V_K·(1 + R_top/R_sd) = a + R_s·I`
   over ≥ 3 load currents of each polarity with \|I\| ≥ 5 mA.
5. **Plateau** (`i_plateau`): the measured current-limit plateau, smaller
   polarity (bring-up checklist §7), set with `CAL.SET i_plateau=<A>`.

All values are checked against the plausibility window before they are
accepted (g_dac ±2 %, c_dac ±1000 codes, g_v/g_i ±5 %, r_s 80–200 Ω, r_top
70–180 Ω and < r_s, i_plateau 20–100 mA). The `c_dac` window covers the
DAC8552 worst case (zero-code error 12 mV, gain error 0.2 % FSR at mid-scale,
half of channel B's 0.5 % FSR full-scale error: 544 codes, about 580 with the
divider and difference amplifier) and still refuses a dropped or extra digit.

## 3. Storage

A 128-byte record (magic `CCA1`, version 1, size, sequence, MCU UID,
host timestamp, the measured-coefficient bits (`0x80000000` | bit k for the
k-th `CAL.SET` key; a record written before this field existed holds 0 there
and loads as fully measured), the seven coefficients as IEEE doubles, a
32-byte note, CRC-32/IEEE) in flash sector 1 or 2 (16 KB each). A save erases the sector
that does **not** hold the newest valid record, programs, reads back and
verifies; an interrupted erase or program therefore never destroys the
previous record (tested with torn erases/programs at every word). At boot the
valid record with the highest sequence wins; a record of another MCU (UID)
is ignored (`W_CAL_UID`), and so is a damaged one (bad magic, version or CRC,
implausible coefficients: `W_CAL_DAMAGED`, since an older record may then be
in use; the next `CAL.SAVE` overwrites that sector). `CAL.SAVE` is legal only in `DISARMED`, 100 ms after
the last heartbeat edge, and waits until no ADC conversion is in flight (the
erase stalls the bus for up to 500 ms).

## 4. Data format

### PT record (wire)

See [`protocol.md` §6](protocol.md#6-measurement-records). The raw mean codes
(`i_raw`, `v_raw`, rounded to whole codes) are authoritative; `v_V`/`i_A` are
computed with the calibration and zero active **when the line is formatted**
(a `DATA.FETCH` after `CAL.SET` re-sends the same raw codes with the new
conversion), from the unrounded means: the device keeps a 1/256-code fraction
with each record, so `v_V`/`i_A` resolve below one code where averaging
allows, while a reprocessing from `i_raw`/`v_raw` alone is limited to whole
codes. Standard deviations are sample σ in codes (1 decimal), at most 6553.4
(flag `SD_SAT` when that ceiling is reached).
Timestamps are µs from the sweep start (u32, unwrap monotonically).

### Files written by `iv-sweep` / `stage1iv.writers`

* `<base>.csv`: one row per record: the 17 protocol fields, `flag_names`
  (decoded flags, `|`-separated), and the unwrapped timestamps.
* `<base>.json` (format `stage1iv-sweep/1`): `*IDN?`, the full `SWEEP_BEGIN`
  fields (configuration, calibration record and session zero), `SWEEP_END`,
  `CFG?`, `CAL?`, host metadata (time, user, host, note), the CSV file name,
  and a statement that accuracy figures are provisional until verified at
  bring-up.

Together they let any run be reprocessed from raw codes with a different
calibration or zero.

## 5. Accuracy targets (provisional)

After calibration, the **targets** are V ±(0.05 % of reading + 1 mV) and
I ±(0.1 % of reading + 1 µA), the 1 µA term valid only within about ±1 °C of
the session-zero temperature. They are **not** established: the software
tests verify arithmetic only. The lowest reliably measurable current is not
declared. The measurements required before any of this can be claimed are
listed in [`verification.md`](verification.md) §4.
