# State machine, faults and warnings

Implementation: `firmware/src/core/instrument.c` (states, sequences,
faults), `engine.c` (the actions), `session.c` (session triggers). Design:
[`firmware_spec.md` §6](firmware_spec.md#6-operating-state-model).

## 1. States

```
          POST ok                ARM (all preconditions)
  BOOT ───────────► DISARMED ──────────────────────────► ARMING
   │                  ▲   ▲                                │ T_ARM = 100 ms after
   │ POST fails       │   │ T_DISARM = 100 ms after         │ the first edge
   ▼                  │   │ the last edge                   ▼
 FAULT ──FAULT.CLR────┘   └──── DISARMING ◄──── ARMED ◄──► SWEEPING
   ▲  (cause gone, T_DISARM)        ▲      DISARM,  │ SWP.RUN / end, abort,
   │                                │      session  │ compliance(stop), overflow
   └──── any fault, any state ──────┴──── lost, host timeout
```

| State | Heartbeat | Output | Leaves to |
|---|---|---|---|
| `BOOT` | off | zero pair written at reset | `DISARMED` (POST passed), `FAULT` |
| `DISARMED` | off | zero pair, gate open | `ARMING` (`ARM` only) |
| `ARMING` | 2 kHz | zero code | `ARMED` 100 ms after the first edge; `DISARMING`; `FAULT` |
| `ARMED` | 2 kHz | held level (`SRC` ramps) | `SWEEPING`, `DISARMING`, `FAULT` |
| `SWEEPING` | 2 kHz | sweep engine | `ARMED`, `DISARMING`, `FAULT` |
| `DISARMING` | 2 kHz until the zero code is loaded, then off | ramp to 0 V at ≥ 10 V/s | `DISARMED` 100 ms after the last edge; `FAULT(DISARM_TIMEOUT)` after 1.1 s |
| `FAULT` | off | `dac_zero_safe()` at entry | `DISARMED` on `FAULT.CLR` |

Nothing re-arms the output except an `ARM` command in a live session. After
any reset the instrument is `DISARMED` (or `FAULT`), and `disarm_reason=reset`
is reported if the reset was not a plain power-on.

## 2. ARM preconditions (checked in this order)

`ERR FAULT <F>` in FAULT; `ERR STATE` outside DISARMED; then
`ERR PRECOND <name>`:

| Name | Condition |
|---|---|
| `session` | VBUS present (PA0 > 3.8 V for 20 ms), configured and not suspended, DTR |
| `post` | POST passed this boot (or after `FAULT.CLR`) |
| `adc` | ADS1256 initialised and not in a reconfiguration/self-test job |
| `hb` | (bring-up) no `HB.HOLD`, and ≥ 100 ms since it ended |
| `afe` | a gate-open front-end check within the last 1 s passed: no clip code, not both channels below −0.8 FS (±15 V off) |
| `config` | the configuration is inside its ranges |
| `cal` | the calibration maps 0 V to a DAC code |
| `bor` | option bytes at BOR level 3 |

## 3. Sequences

* **ARM:** load the zero code (both frames), `hb_enable()`, `ARMING`.
* **DISARM / session loss / host timeout:** abort a sweep
  (`aborted`, `session_lost`, `host_timeout`), ramp from the present level to
  0 V at max(`ramp_rate`, 10 V/s) on a 1 ms grid with the heartbeat running,
  load the zero code, `hb_disable()` (PC5 low), `DISARMED` after
  `T_DISARM`. If the ramp has not finished 1.1 s after entry:
  `FAULT(DISARM_TIMEOUT)`.
* **FAULT entry:** `dac_zero_safe()` (PC5 low, SPI1 idle-wait or reset,
  /SYNC high ≥ 1 µs, frames `00 80 00` + `34 FF FF`, bit-banged if SPI1 is
  dead), `hb_disable()`, end the sweep with `status=fault`, fail a running
  job, latch the fault, `EVT FAULT <F> detail=<d>`, `EVT STATE FAULT`. ADC
  faults also re-initialise the ADC.
* **Fault handlers and dead-man (target):** the same zero, a `.noinit` crash
  record, `NVIC_SystemReset()`; the next boot reports `crash=1`.

## 4. Faults (latched)

| Fault (wire name) | Raised by | `FAULT.CLR` needs |
|---|---|---|
| `POST` | boot or `SELFTEST`: ADC init, −VREF / zero self-checks | a passing re-run (started by `FAULT.CLR`, retry) |
| `ADC_TIMEOUT` | no /DRDY in time, or > 8 missed read windows | ADC re-initialised |
| `ADC_COMM` | register read-back mismatch (incl. scrub), stuck DOUT, SPI2 timeout | ADC re-initialised |
| `DAC_SPI` | SPI1 transfer or scrub timed out | a successful zero-pair write |
| `LOOP_OVERRUN` | heartbeat edge spacing > 1.0 ms while permitted | — |
| `INVARIANT` | B shadow ≠ 0xFFFF, `DBG_TIM2_STOP`, heartbeat state ≠ instrument state, `hb_service` in an exception, setpoint not mappable | the invariant holds again |
| `AFE` | a clip code while the heartbeat runs | — |
| `STALE` | no completed conversion for 2·t18 + 100 ms while armed | ADC ready |
| `DISARM_TIMEOUT` | the disarm ramp missed 1.1 s | — |
| `CLOCK` | HSE did not start at boot (USB unusable) | `RESET` |
| `STACK` | thread-stack canary damaged | `RESET` |
| `COMPLIANCE` | \|I\| > `i_compliance` with `on_compliance=fault` | — |

Every `FAULT.CLR` also needs `T_DISARM` (100 ms) since the last heartbeat
edge. In `FAULT` the IWDG is still kicked by healthy passes, so a latched
fault is not lost to a watchdog reset.

ADC errors are latched in **every** state, including `DISARMED`: an ADC that
stops responding blocks the next `ARM` (spec §6.4).

## 5. Aborts and warnings (not latched)

| Condition | Reaction | Reported as |
|---|---|---|
| compliance, `on_compliance=stop` | zero code at once, stay `ARMED`; a sweep ends; an `SRC` ramp ends (`SRC_DONE status=compliance`); in `DISARMING` the disarm is then complete (no `DISARM_TIMEOUT`) | `SWEEP_END status=compliance trip_idx= trip_i_a=` or `EVT WARN COMPLIANCE i_a=` (at 0 V once, until the output is driven again) |
| `SWP.ABORT` | sweep ends, ramp to 0 V | `status=aborted` |
| ring full of never-sent records | sweep ends, ramp to 0 V | `status=overflow` |
| session loss / host timeout | `DISARMING` | `disarm_reason`, `EVT WARN HOST_TIMEOUT`, next `HELLO` |
| Kelvin mismatch, current-limit plateau, no output, clip, late timing | per-record flags | `PT` flags, `EVT WARN KELVIN/NO_OUTPUT/LATE`, `STAT warn=` |

## 6. Disarm reasons

`none` (power-on), `command`, `session_lost`, `host_timeout`, `fault`,
`reset` (the last boot was not a plain power-on). Reported in `STAT?`,
`EVT STATE DISARMING/DISARMED reason=` and the next session's `EVT HELLO`.
