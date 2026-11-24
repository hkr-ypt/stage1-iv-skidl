# Known limitations, open items and physical tests still required

## 1. Not yet verified on hardware

The firmware has run only in the simulator. The STM32 port
(`firmware/port_stm32/`) compiles without warnings, links, passes static
analysis and the link checks, and its register sequences were checked
against RM0383 / DS10314 / ES0287 and the CMSIS headers, but **it has not
executed on a board**. The first bench session must follow
[`bringup_firmware.md`](bringup_firmware.md) with a logic analyser before any
DUT is connected. The hardware tests that must pass before any claim are
listed in [`verification.md` §4](verification.md#4-hardware-tests-required-before-any-claim).

Specific items to confirm on the bench:
* clock tree (HSE start, PLL lock, VOS ready, 48 MHz USB), and `F_CLOCK`
  when the crystal is absent;
* TinyUSB 0.21.0 enumeration with VBUS sensing off and the soft-connect
  gated by PA0; behaviour on suspend/resume; device mode reached before
  `tusb_init()` (after init: OTG_FS GINTSTS.CMOD = 0 and MMIS = 0,
  DCFG.DSPD = 3, DCTL.SDIS = 1; PA10 is pulled up); the D+ pull-up released
  after a cable pull (VBUS detach below 3.6 V);
* the naked fault entry (stack switch, frame decode) and the dead-man ISR
  on real exceptions;
* the bit-banged DAC fallback timing (~1 MHz on PA5/PA7);
* the MPU stack guard: `CRASH STACK` (bring-up) must give `crash=memmanage`
  and a zeroed output;
* ADS1256 init and reconfiguration send SYNC, WAKEUP, SELFCAL (each as
  SBAS288K documents it). Confirm on the analyser that /DRDY goes high at the
  SYNC/WAKEUP, stays high through the calibration and falls after the
  Table 21 calibration time (a fall in under half that time is treated as a
  skipped calibration);
* ADC1 VBUS scaling (VDDA assumed 3.3 V) and the temperature reading;
* flash erase/program on sectors 1–2 and the D-cache reset;
* the pass-time budget (≤ 150 µs design, overruns counted above 400 µs):
  `DIAG? pass_max_us` under load. Long formatting work (SWEEP_BEGIN, STAT?)
  happens in one pass; if it exceeds the budget it must be split.

## 2. Performance

* Accuracy targets are **provisional** (V ±(0.05 % + 1 mV),
  I ±(0.1 % + 1 µA) with the offset term valid only within about ±1 °C of the
  session-zero temperature). The software tests verify arithmetic only.
* The **lowest measurable current is not declared**; it follows from the
  noise and drift measurements (verification H-10, H-11, H-13).
* PGA is fixed at 2 for both channels in production builds; a per-channel
  PGA is a future change justified only by bring-up noise data (spec §13).
* With nominal constants (`CAL_NOMINAL`) the Kelvin check cannot see an open
  SENSE− lead, nor an open SENSE+ on a DUT below about 35 Ω under the
  plateau: the wide tolerance's 15 Ω·|I| term equals the error those faults
  cause (spec §7.7). Calibrate first.
* A plateau point of an active DUT (cell, battery, charged capacitor) whose
  own voltage, on the side opposite V_set, happens to lie near I·15 Ω (about
  0.57…0.90 V at 49 mA) also gets `KELVIN`, a false sense-lead warning; it
  is still `CLIM` (not DUT data).
* The current-limit plateau (U5 OPA547T, R7 348 kΩ) is expected at
  40…65 mA, sinking up to ~8 mA above sourcing, falling ~0.2 mA/K as U5 warms;
  49 mA is the design centre, not a characterised value. `i_plateau` must be
  replaced by the measured plateau (smaller polarity); until then `CLIM`
  classification uses 49 mA, so a unit whose plateau sits below 39 mA
  (0.8 × 49 mA, a sagged or low unit) reports its plateau as `KELVIN` rather
  than `CLIM` until `i_plateau` is measured.
* The software compliance acts after a conversion (settle + one conversion,
  or one monitor conversion; during ramps the monitor converts the current
  back to back); it is not a current limit. The hardware plateau and the
  passive bounds are: V_SWEEP / (R30 + R10) ≤ 91.3 mA (92.6 mA worst case)
  with a working source, and 128 mA into a DUT short even with U5 at a rail
  (DESIGN_DOCUMENT §7). The default `i_compliance` (100 mA) is above the
  first, so by default the plateau limits; set it lower to protect a DUT.
* **Operating rule (hardware, `docs/final_audit.md` §3.7):** no inductive DUT
  in current limit. In TI's OPA547 model a fast fall of the command out of
  the negative limit with an inductive DUT can leave U5 oscillating; the
  firmware's immediate-zero reactions (a compliance stop, a fault) make such
  a fall, ramps do not. This is kept deliberately: zeroing at once is what
  protects a DUT; the bring-up checklist tests that exit.

## 3. Behaviour choices that differ from the specification

Listed with rationale in [`firmware_spec.md` §15](firmware_spec.md#15-implementation-notes-and-differences).
The user-visible ones:

* `SWP.ABORT` ends the sweep at once (the point in progress produces no
  record) instead of at the next point boundary.
* A compliance trip produces no record for the tripping point; the trip
  index and current are in `SWEEP_END`.
* ADC errors latch a fault in every state, `DISARMED` included.
* `CAL.SAVE` is a deferred command (it waits for an idle ADC).
* `SRC_DONE` carries `status=reached|compliance|aborted`; `SWP.RUN` answers
  `ERR BUSY` while an `SRC` ramp runs.
* A host that stops reading behind unread records also stops getting its
  commands parsed once the ordered-event queue is about a third full: they
  wait in the receive buffer rather than lose their events. `DISARM` and
  `SWP.ABORT` still take effect at once (their events can then precede
  their `OK`), provided they fit in the 1 KB receive buffer: behind more
  unparsed input, only the host timeout (30 s by default) disarms.
* The session zero is marked stale at the start of every new host session.
* `ZERO` accepts offsets up to the INA128P worst case to 85 °C (10 mV,
  100 µA referred to the DUT), so it catches a DUT or fixture left
  connected only when that carries more than this at 0 V output. Its
  procedure needs all four jacks open; the limits do not check it.
* Every session starts with an empty line before `EVT HELLO` (protocol.md §1).
  Output a host left unread when it closed the port (up to 4 KB plus one
  64-byte packet) is not discarded: it arrives ahead of the next session's
  `EVT HELLO`, and hosts skip it. If the session's `HELLO` reaches the host
  library more than 0.1 s after an old `HELLO` in that tail (a host too busy
  to read), its first command sees a restart (`SessionLost`): open the port
  again.
* `CAL_NOMINAL` stays set until `g_dac`, `c_dac`, `g_v`, `g_i` and `r_s` have
  all been set with `CAL.SET` to measured values (a value equal to the
  nominal one, as `CAL?` prints it, counts as nominal); a partial
  calibration keeps the wide Kelvin tolerance.

## 4. Residual risks (unchanged from spec §3.3)

* A hang with interrupts disabled (or a core lock-up) is not caught by the
  dead-man; the output decays with the gate (≤ 63 ms PC5 low, ≤ 77 ms high,
  simulated) and the IWDG resets the MCU after 1.0–2.8 s.
* A wrong but in-range setpoint from a running, logically faulty loop is not
  caught by hardware.
* A corrupted channel-B frame lives until the next update or scrub (≤ 100 ms).
* "Armed" is inferred from heartbeat timing, not measured (no DAC_ARM
  feedback; spec §11 H3/H4).

## 5. Host software

* Tested on Linux only (CI, simulator over a pty). Windows and macOS serial
  behaviour (DTR on open/close, port naming) is untested.
* The pty used by the simulator has no DTR line; `SerialTransport` tolerates
  that, the simulator emulates DTR from client presence.
* If writing a command line is interrupted after its bytes left (Ctrl-C at
  that instant, or a partial write when a serial write times out), the
  client forgets the command while the device still answers it; later
  responses are then attributed one command late until a tagged response
  shows the mismatch (`ProtocolError`, `resync()`). This predates the audit
  and needs an interruption at that instant.

## 6. Owner actions outside the software

* Register a USB PID with pid.codes (planned designation 500S is not
  registered); rebuild with `FW_USB_PID`/`FW_USB_PID_REGISTERED`.
* Program BOR level 3 on every board (`build.md` §5).
* Decide on the optional observability additions (spec §11 H3, H4) for a
  future board spin.
