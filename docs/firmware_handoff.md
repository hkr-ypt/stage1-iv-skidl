# Firmware handoff — what the hardware requires of the firmware

No firmware exists yet, and this document is not firmware. It lists the
behaviour the **hardware** depends on, so that whoever writes the firmware
inherits the requirements, not just the pinout. Nothing here is hardware
protection. Where the hardware *does* protect, this document says so and
what limits that protection has.

Sources: the SKiDL blocks (`blocks/*.py`), the data sheets cited inline, and
the hardware-audit corrections (`docs/audit_corrections.md`).

---

## 1. The safe state, and what firmware must do to leave it

The sweep output is **forced to zero by hardware** whenever the firmware is
not running normally:

| Condition | What the hardware does | Time to zero |
|---|---|---|
| Power-up | DAC8552 powers up at zero scale on both channels, and the midpoint now comes from channel B, so V_SWEEP = 8.03·(0 − 0/2) = 0 | immediate (POR) |
| MCU reset, debugger halt, crash, PC5 stuck high or low, MCU unpowered | No heartbeat → DAC_ARM decays → U11 opens → DAC VREF pin pulled to 0 V by R35 → both DAC outputs 0 V whatever codes they hold → V_SWEEP ≈ 0 | ≤ ~52 ms (calculated) |

The residual at "zero" is offsets only: DAC zero-code error and U3/U5 offsets
times the level-shift gain, estimated ≤ ~0.1–0.15 V (not a guaranteed value;
it is measured at bring-up).

To **arm** the output the firmware must:

1. **Load the DAC codes first.** After *every* reset (power-on, NRST,
   watchdog, brown-out, debugger reset), write channel B = `0xFFFF`, then
   channel A = the desired start code (mid-scale `0x8000` = 0 V). The DAC
   keeps whatever codes it held before an MCU reset. The gate hides those
   codes only while disarmed, so they must be rewritten before arming.
2. **Then start the heartbeat on PC5** (§2).
3. Treat the output as live about **10 ms** after the heartbeat starts. The
   hardware needs several edges to arm (calculated: ≥ 5 at 1 kHz). Do not
   rely on being armed until about 100 ms (margin).

To **disarm**, stop toggling PC5 and leave it static (low preferred). The
output reaches zero within the ~52 ms decay. For an immediate zero, also
write channel A = channel B/2 (A = `0x8000` with B = `0xFFFF`).

## 2. The heartbeat (PC5)

| Requirement | Value | Why |
|---|---|---|
| Waveform | square wave, ~50 % duty | the pump transfers charge on rising edges (R36/C47/D6/C48/R37) |
| Frequency | **≥ 1 kHz** (toggle at least every 0.5 ms); 1–5 kHz recommended | DAC_ARM ≈ 2.4 V nominal and ≥ 1.9 V worst case at 1 kHz against U11's guaranteed V_IH of 1.49 V. At 500 Hz the worst-case margin falls to ~0.1 V (simulated) |
| Source | **software only.** Toggle from the main control loop after its health checks, never from a timer ISR, timer output compare/PWM, DMA or MCO | PC5 has no timer output function on the STM32F411 (DS10314: only EVENTOUT/ADC), so a peripheral cannot keep it toggling after the CPU stops. That protection is lost if a healthy ISR keeps toggling while the main loop is hung |
| Debugging | any halt over ~50 ms disarms the output | intended: a halted controller must not hold a voltage on the DUT |

The disarm time (~52 ms) and arm margins are **calculated** from a transient
simulation of the pump. Bring-up measures them (`docs/bringup_checklist.md`).

## 3. DAC8552 (U4) — sweep range is a firmware contract on its positive side

V_SWEEP = (R4/R3)·(V_A − V_B/2) = 8.0321·(V_A − V_B/2), with V_A, V_B in 0…2.500 V.

* **Channel B must be at full scale (`0xFFFF`) whenever the output is armed.**
  With B = full scale, V_SWEEP spans ±10.04 V.
* The **negative** bound, −10.04 V, is hardware: no code can go lower.
* The **positive** bound holds only while B is at full scale. With B lower,
  the ceiling rises (B = 0, A = full scale commands +20.1 V). The real ceiling
  is U3B's swing, guaranteed at least +13.8 V and at most the +15 V rail
  (OPA2277, SBOS079C). That is above U5's specified input range
  ((V+) − 3 V = +12 V, SBOS070D), and above the ±11.8 V the voltage channel
  is guaranteed to measure. **Firmware must clamp commands to ±10.0 V and
  must never write B < `0xFFFF` while armed.**
* **Write B once per boot; trim the zero with A.** Changing B moves the
  midpoint and the zero together.
* **Simultaneous load** (SLAS430A, Table 1 and "Example 1"; control byte DB23…DB16 =
  `0 0 LDB LDA X BufSel PD1 PD0`):
  1. `0x00` + data → write buffer A, load nothing;
  2. `0x34` + data → write buffer B, load DAC A *and* DAC B.

  Use this for the boot write (A and B change together, no intermediate code
  pair). Single-channel updates of A during a sweep can use `0x10` (write
  buffer A, load DAC A).
* SPI1: SYNC (CS_DAC, PA4) idles high and is pulled up by R34 (100 k to +5VA)
  while the MCU's pins float. Logic levels: the DAC runs at 5 V, V_INH = 2.4 V,
  so 3.3 V drive is valid.

## 4. ADS1256 (U8)

* **SCLK ≤ f_CLKIN/4 = 7.68 MHz/4 = 1.92 MHz** (SBAS288K, t1 ≥ 4·τ_CLKIN).
  Wait **t6 ≥ 50·τ_CLKIN ≈ 6.5 µs** between RDATA/RREG and reading data.
* **D0/CLKOUT** is enabled at reset (ADCON reset value `0x20`: CLK1:CLK0 = 01,
  CLKOUT = f_CLKIN) and its pin is unconnected on this board. Write
  ADCON CLK1:CLK0 = 00 at init: "When not using CLKOUT, it is recommended that
  it be turned off". Only the RESET pin restores these bits.
* **D1–D3** reset as inputs (IO register `0xE0`) on unconnected pins. Set them
  as outputs driven low (IO register DIR3…DIR0 = 0, DIO = 0) so no CMOS input
  floats.
* Inputs: AIN0 (current, I_SENSE_OUT), AIN2 (voltage, V_SENSE_OUT), both
  against **AINCOM = VREF_2V5**. PGA = 2 with the input buffer off is the
  setting the measurement-range analysis assumes. Higher PGA is legal for small
  signals (both inputs near 2.5 V), but re-check the input window.
* Run SELFCAL after power-up and after any PGA or data-rate change.

## 5. Calibration and measurement validity

* **Zero:** with the gate disarmed (heartbeat stopped), V_SWEEP ≈ 0 and no
  current flows. Record both ADC channels as the offset. Repeat with the
  DUT jacks open and shorted.
* **Gain:** against a calibrated DMM and a precision resistor across the
  Kelvin jacks (e.g. 100 Ω, 0.01 %), at several sweep points of each polarity.
  The current gain is set by R10 (0.1 %) and R11. The voltage gain is set by
  the R12/R32, R13/R33 dividers (0.1 %). Both calibrate out. Drift is what the
  low-TCR parts limit.
* **Open-Kelvin detection.** If a SENSE lead is open, the R32/R33 bottom arms
  pull that INA128 input to AGND. The voltage channel then reads a
  plausible-looking but wrong value while current flows. Firmware must
  cross-check: with current flowing, the Kelvin voltage must agree with the
  force-side estimate V_SWEEP − I·(R30 + R10 + R_F1 + R_F2 + R_U12) within a
  tolerance (F1, F2: 3.0–7.5 Ω each; U12: ≤ 2 Ω). Treat a persistent mismatch
  as an open or swapped sense lead.
* **Output disconnected (U12 open).** U12 disconnects FORCE+ whenever a
  ±15 V rail is missing or low (guaranteed open with either rail absent,
  guaranteed closed at ≥ ±14.45 V; `final_corrections.md` §2.5). The MCU
  has no signal for it and cannot command it. Firmware sees it as: I ≈ 0
  however V_SWEEP is set, with the Kelvin voltage near 0 V (or wherever the
  DUT's own charge leaves it) instead of following the command. Report that
  as "output disconnected / analog supply missing", not as an open DUT, and
  do not adapt any calibration to it. An open DUT reads the same way, so the
  distinction needs a known load at start-up (or the operator's word).
* **Current-limit plateau** (~49 mA nominal, set by R7, not characterised by TI
  at this setting) is normal operation, not a fault. Firmware may detect it
  (I flat while V_SWEEP keeps moving) and must not report the plateau region as
  DUT behaviour.

## 6. USB

* **VBUS sensing is on PA0** (VBUS_SENSE, R28/R29 = 100 k/100 k, so 2.5 V at
  5 V VBUS). The OTG_FS peripheral's dedicated VBUS pin, **PA9, is used here
  as RESET_ADC**. Firmware must therefore **disable the OTG_FS hardware VBUS
  sensing** (STM32F411 OTG_FS GCCFG: VBUS sensing off) and must never select
  PA9's OTG_FS_VBUS alternate function. It must detect cable attach and detach
  from PA0 itself and drive the soft connect (device disconnect bit)
  accordingly.
* The board is powered from the bench supply at J1, never from VBUS.

## 7. Other pins

* **PC13 / LED1:** the LED is lit by driving PC13 **low** (it sinks about
  1 mA through R23). PC13 must never be used to source current (DS10314).
  Push-pull or open-drain low both work. Keep it at ≤ 2 MHz (the PC13 limit).
* **PA9 (RESET_ADC)** drives the ADS1256 RESET. It floats during MCU reset,
  and R20 (10 k to +3V3D) holds the ADC out of reset meanwhile. Firmware
  drives it high (inactive) at init and pulses it low to reset the ADC. Only
  that pulse restores CLK1:CLK0.
* Unused GPIOs are unconnected. Configure them as analog or as driven outputs
  per normal practice.
