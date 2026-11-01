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
  gated by PA0; behaviour on suspend/resume;
* the naked fault entry (stack switch, frame decode) and the dead-man ISR
  on real exceptions;
* the bit-banged DAC fallback timing (~1 MHz on PA5/PA7);
* the MPU stack guard: `CRASH STACK` (bring-up) must give `crash=memmanage`
  and a zeroed output;
* ADS1256 init and reconfiguration send SELFCAL right after SYNC, with no
  WAKEUP between them. The simulator assumes SELFCAL runs from that state;
  if the silicon ignores it, every init times out (safe, but it blocks
  bring-up). Confirm on the analyser that /DRDY falls after the Table 21
  calibration time;
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
* The ~49 mA current-limit plateau is a **nominal** value (R7, not
  characterised by TI). `i_plateau` must be replaced by the measured plateau;
  until then `CLIM` classification uses 49 mA.
* The software compliance acts after a conversion (settle + one conversion,
  or one monitor conversion during a long settle); it is not a current
  limit. The hardware plateau and the 128 mA passive bound are.

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
  wait in the receive buffer rather than lose their events.
* The session zero is marked stale at the start of every new host session.

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

## 6. Owner actions outside the software

* Register a USB PID with pid.codes (planned designation 500S is not
  registered); rebuild with `FW_USB_PID`/`FW_USB_PID_REGISTERED`.
* Program BOR level 3 on every board (`build.md` §5).
* Decide on the optional observability additions (spec §11 H3, H4) for a
  future board spin.
