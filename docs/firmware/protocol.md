# Host protocol, version 1

This is the contract between the firmware (`firmware/src/core/proto.c`,
`firmware/src/core/commands.c`) and the Python host library
(`host/stage1iv/`). Both are tested against this document; a change to any
command, field or error code is a protocol change and increments
`proto=` in `*IDN?`.

## 1. Transport and session

- USB full-speed CDC-ACM (virtual serial port). Baud rate, parity and stop
  bits are ignored.
- Device identity: see [`usb_identity.md`](usb_identity.md). Hosts find the
  device by VID/PID and serial string, never by a fixed device path.
- **Host session.** The firmware considers a host present only while all of
  the following hold:
  1. VBUS is present (PA0 measured above 3.8 V for 20 ms; absent below 3.6 V,
     because after a cable pull the D+ pull-up can back-feed the sensed node
     to about 3 V);
  2. the device is enumerated (configured) and not suspended;
  3. the host asserts **DTR** (pyserial does so when it opens the port).
- **Liveness.** Any received byte counts as host activity. While the output is
  armed (`ARMING`, `ARMED`, `SWEEPING`) the firmware disarms if no byte
  arrives for `host_timeout_s` seconds (default 30; `0` disables the timeout).
  The host library sends `PING` only while one of its calls is in progress
  or a `with inst.hold():` block runs, every `min(5 s, host_timeout_s / 3)`;
  a program that stops calling it is disarmed, as the timeout intends.
- **Losing the session** (any of 1–3 false, or the liveness timeout) while
  armed ends any sweep with `status=session_lost` (or `host_timeout`) and
  disarms: the output ramps to 0 V at ≥ 10 V/s (deadline 1.1 s), the DAC is set
  to the zero pair, and the heartbeat stops. The instrument never re-arms by
  itself: a new `ARM` command in a new session is required. The reason is
  reported in `STAT?` (`disarm_reason=`) and in the `HELLO` event of the next
  session.
- **Session start** (all three conditions become true): the firmware discards
  its own unsent output, skips unsent records (they remain fetchable with
  `DATA.FETCH`) and sends an empty line, then `EVT HELLO`. Output the USB
  stack already holds cannot be withdrawn (TinyUSB may be sending from it):
  a host that stopped reading and closed the port receives the old session's
  unread output ahead of that empty line when it opens the port again: up to
  4 KB (the CDC transmit FIFO) plus one 64-byte USB packet, possibly
  beginning and ending mid-line. The empty line ends a cut-off line, so
  `HELLO` always starts a line. **Hosts ignore every line before the
  session's `EVT HELLO`**, skipping by content, not by a byte count. The old
  tail can itself hold an earlier session's `HELLO` (a session that ended
  before its `HELLO` was read). The tail and the new `HELLO` are queued
  together and arrive back to back, so a host waits until its input pauses
  after a `HELLO` and takes the last `HELLO` as the session's; only then does
  it send its first command. A `HELLO` that arrives after a command was sent
  means a new session: the device restarted or the session was lost, and
  commands in flight may be lost. The host library does exactly this when
  it waits for `HELLO` at open (`Instrument.open_serial`, or a
  `hello_timeout` above 0): it waits until no line has arrived for 0.1 s
  after a `HELLO`, and drops the events before the last `HELLO` as well.

## 2. Framing

- ASCII text, one message per line, terminated by `\n` (a preceding `\r` is
  ignored). Bytes outside 0x20–0x7E (other than `\r`, `\n`, `\t`) make the line
  invalid (`ERR SYNTAX`).
- Host → device lines are at most 256 characters; a longer line is discarded
  up to its newline and answered with `ERR SYNTAX line too long`. Empty lines
  are ignored.
- Device → host lines begin with one of the tokens below; the only other
  line is the empty line that starts every session (§1). Hosts ignore empty
  lines.

  | Token | Meaning |
  |---|---|
  | `OK` | successful response to the current command, optional payload |
  | `ERR` | failed response: `ERR <CODE> <message>` |
  | `EVT` | asynchronous event: `EVT <TYPE> key=value ...` |
  | `PT` | one measurement record (§6) |
  | `#` | log line (debug builds only); hosts ignore it |

- **Exactly one `OK` or `ERR` per command, in command order.** Commands are
  processed strictly one at a time. Most respond immediately. *Deferred*
  commands (`MEAS?`, `ZERO`, `ADC.SELFCAL`, `SELFTEST`, `CAL.SAVE`) respond
  when the work completes (bounded to 10 s; `CAL.SAVE` waits for the ADC to be
  idle and then erases and programs a flash sector, typically < 1 s); further
  commands wait in the receive buffer meanwhile. `EVT` and `PT` lines may
  appear between a command and its response.
- **Ordering guarantees.** Events a command causes always follow its `OK`
  (for example `OK`, then `EVT STATE ARMING`), with one exception: `DISARM`
  and `SWP.ABORT` without arguments (blanks around the verb do not matter)
  take effect as soon as their line is in the device's 1 KB receive buffer,
  even while earlier commands are still waiting (a deferred command, or a
  host that is not reading, see *Flow control*). Their response keeps its
  place in the command order, so their events can then precede it, and
  commands sent before them that were still waiting see the disarmed (or no
  longer sweeping) state; a waiting `DISARM` also undoes an `ARM` that was
  waiting ahead of it, and a sweep that a waiting `SWP.RUN` starts is ended
  by the `SWP.ABORT` sent after it. A line behind more than 1 KB of
  unparsed input is not seen until the input ahead of it is; unread input
  does not count as host activity, so the host timeout still disarms. `EVT SWEEP_BEGIN`,
  `EVT SWEEP_END` and `EVT STATE` are never sent before the `PT` records
  pushed before them, and no record pushed after one of them is sent before
  it, even while the host is not reading: a host may stop reading a sweep at
  `SWEEP_END`.
- **Flow control.** The device never stops measuring for the host. It parses
  a command only when its transmit buffer has room for the response and the
  events the command can lead to (including the `SWEEP_BEGIN`, `SWEEP_END`
  and `STATE` events waiting behind unread records), so a host that does not
  read will not get answers either; further commands wait in the receive
  buffer. Records are kept in a 1024-record ring (§6); events beyond the
  buffers are dropped and counted (§7).

## 3. Tokens and numbers

- A command line is `VERB arg arg ...`, tokens separated by one or more spaces
  or tabs. Verbs and keywords are case-insensitive; key names are
  case-insensitive; values of type *word* are case-insensitive.
- Arguments are positional or `key=value`. Unknown keys and duplicate keys are
  errors (`ERR ARG`).
- **Numbers** are strict decimals with an optional exponent: `-2.5`, `1e-3`,
  `0.000125`, `+10`. No hex, no `inf`/`nan`, no units. The firmware converts
  them to fixed-point integers (µV, nA, µs, ...) and **rejects values that are
  not exactly representable** at that resolution (`ERR ARG ... inexact`)
  rather than rounding. Resolutions: voltages 1 µV, currents 1 nA, times 1 µs,
  data rate 0.1 SPS, ramp rate 1 µV/s.
- Output numbers are printed from integers with fixed decimals: volts with 6
  decimals, amperes with 10 decimals (0.1 nA), times as integer µs, codes as
  integers, flags as `0x` + 4 hex digits.

## 4. Error codes

| Code | Meaning |
|---|---|
| `SYNTAX` | the line could not be tokenised (bad characters, too long, unbalanced `=`) |
| `UNKNOWN` | unknown verb (also every bring-up command in non-bring-up builds) |
| `ARG` | wrong number/name/format of arguments, inexact number |
| `RANGE` | a value outside its allowed range (or a setpoint outside `v_limit` or the DAC range) |
| `STATE` | the command is not allowed in the current state |
| `BUSY` | a resource is temporarily unavailable (e.g. the ADC is initialising) |
| `FAULT` | a latched fault blocks the command (clear it with `FAULT.CLR`) |
| `PRECOND` | an `ARM` precondition failed; the message names it: `session`, `post`, `adc`, `busy` (a deferred job is still running, e.g. a `CAL.SAVE` that outlived its session), `afe`, `bor`, `config`, `cal` |
| `FLASH` | calibration flash erase/program/verify failed |
| `INTERNAL` | an internal invariant failed (also latches `F_INVARIANT` where applicable) |

## 5. Commands

States: `BOOT`, `DISARMED`, `ARMING`, `ARMED`, `SWEEPING`, `DISARMING`,
`FAULT` ([`state_machine.md`](state_machine.md)). "Any" means every state.

### 5.1 Identity, status, diagnostics

| Command | States | Response |
|---|---|---|
| `*IDN?` | any | `OK IDN model=stage1-iv fw=<ver> rev=<git> proto=2 hw=<rev> build=<release\|debug\|bringup> serial=<24 hex> usb_vid=0x1209 usb_pid=<0xNNNN> usb_pid_status=<dev-test\|registered> planned_pid=500S` |
| `PING` | any | `OK PONG t_us=<uptime µs>` |
| `STAT?` | any | `OK STAT state=<S> fault=<F> warn=0x<hex> disarm_reason=<R> vset_v=<V> dac=<code> vk_v=<V\|-> i_a=<A\|-> mon_age_ms=<n\|-> cal=<stored\|nominal\|ram> cal_seq=<n> zero=<valid\|none\|stale> zero_age_s=<n\|-> afe=<ok\|fail\|unknown> bor=<0..3> vbus_mv=<n> session=<0\|1> host_timeout_s=<n> reset=<causes> crash=<0\|1> sweep_id=<n> sweep_pts=<n> sweep_status=<none\|running\|complete\|aborted\|compliance\|overflow\|session_lost\|host_timeout\|fault> hb_edges=<n> mcu_temp_c=<x.xx\|na> uptime_s=<n>` |
| `DIAG?` | any | `OK DIAG passes=<n> pass_max_us=<n> pass_overruns=<n> hb_max_spacing_us=<n> hb_late=<n> hb_resyncs=<n> adc_state=<off\|init\|idle\|converting\|error> adc_err=<none\|timeout\|readback\|spi\|stuck> adc_conversions=<n> adc_late=<n> adc_stale_edges=<n> adc_read_overruns=<n> adc_scrubs=<n> adc_timeouts=<n> adc_inits=<n> adc_id=0x<h> dac_updates=<n> dac_scrubs=<n> dac_failures=<n> dac_zero_safe=<n> evt_lost=<n> rx_overflows=<n> points=<n> late_points=<n> compliance_trips=<n> ramp_resyncs=<n> sessions=<n> crash=<cause\|none> crash_pc=0x<hex> crash_lr=0x<hex> crash_cfsr=0x<hex>` |
| `DIAG.CLR` | any | `OK` — clears the counters (details below) and the crash record |

`warn=` bits: 0 BOR_OFF, 1 CAL_NOMINAL, 2 ZERO_STALE, 3 CRASH, 4 KELVIN,
5 EVT_LOST, 6 AFE, 7 NO_OUTPUT, 8 CAL_UID, 9 DEV_USB_PID, 10 CAL_DAMAGED.
`CAL_NOMINAL`: one of `g_dac_uv`, `c_dac`, `g_v_uv`, `g_i_na`, `r_s` has not
been measured (set with `CAL.SET` to a value other than its nominal one;
`CAL?` `measured=`), so nominal constants are in use for it. `CAL_DAMAGED`: a
calibration sector holds a damaged record (bad magic, version or CRC, or
implausible coefficients), so an older record or nominal constants may be in
use; the next `CAL.SAVE` overwrites it, or at most the one after it (when no
sector held a valid record, a save goes to the first sector).

`reset=` lists the causes joined by `+`: `por`, `bor`, `sft`, `iwdg`, `wwdg`,
`lpwr`, `pin` (`pin` only when it is the sole cause: every internal reset
also pulses NRST), or `none`. `crash=` names: `hardfault`, `memmanage`,
`busfault`, `usagefault`, `nmi`, `css`, `deadman`, `soft`.

`DIAG.CLR`: event, line and crash counters are reset; the driver counters
(`hb_*`, `adc_*`, `dac_*`) are reported relative to the last clear;
`hb_max_spacing_us` is the maximum since boot.

`disarm_reason=` values: `none`, `command`, `session_lost`, `host_timeout`,
`fault`, `reset`.

### 5.2 Output control

| Command | States | Effect / response |
|---|---|---|
| `ARM` | DISARMED | Checks the preconditions (session, POST passed, ADC idle-able, no deferred job running, a gate-open front-end check within 1 s, BOR level 3, valid configuration). `OK`, then `EVT STATE ARMING`, then `EVT STATE ARMED` 100 ms after the first heartbeat edge. Errors: `ERR STATE` (also `ERR STATE reset pending` between the `OK` of `RESET`, or of a bring-up `CRASH`, and the reset; it comes before the FAULT check), `ERR FAULT` (also `ERR FAULT <F>` when the zero write at arming fails and latches the fault), `ERR PRECOND <name>` |
| `DISARM` | ARMING, ARMED, SWEEPING; DISARMED, DISARMING (no-op) | `OK`; takes effect on arrival even when earlier commands wait (§2); aborts any sweep (`status=aborted`), ramps to 0 V at ≥ 10 V/s, zero pair, heartbeat off, `EVT STATE DISARMING reason=command` ... `EVT STATE DISARMED reason=command` (100 ms after the last edge) |
| `SRC V=<volts>` | ARMED | Ramps the output to the level at `ramp_rate`; `OK`, then `EVT SRC_DONE v_v=<V> status=reached` when reached. A compliance trip during the ramp zeroes the output and ends it with `EVT SRC_DONE v_v=0.000000 status=compliance` (and `EVT WARN COMPLIANCE`); bring-up `DAC.RAW OFF` ends it with `status=aborted`. `DISARM` or a fault ends a ramp with `EVT STATE` instead. `ERR RANGE` beyond `v_limit` or the DAC range. A new `SRC` replaces the target of a ramp in progress |
| `FAULT.CLR` | FAULT (`ERR STATE` otherwise) | Clears the latched fault if its cause is gone and `T_DISARM` (100 ms) has elapsed since the last edge: `OK`, then `EVT STATE DISARMED`. Otherwise `ERR PRECOND <reason>`: `wait` (T_DISARM), `adc` (ADC not re-initialised and self-tested yet, or a self-test the fault interrupted must run again; the check is started, retry), `dac` (SPI1 still failing), `invariant`, `stack` and `clock` (these two need `RESET`) |
| `RESET` | DISARMED, FAULT | `OK`, then a software reset 50–200 ms later (after the `OK` has left); the session ends |

### 5.3 Measurement

| Command | States | Effect / response |
|---|---|---|
| `MEAS? [N=<pairs>]` | DISARMED, ARMED | Deferred. Measures `N` (default `n_avg`, 1–256) I/V pairs at the present output, without touching the DAC. `OK MEAS n=<n> i_raw=<code> v_raw=<code> i_sd=<codes\|-> v_sd=<codes\|-> v_v=<V> i_a=<A> flags=0x<hex>` (fields and flags as in §6, with `MEAS`; `v_v`/`i_a` from the unrounded means; the Kelvin/plateau/no-output classification only in ARMED). `ERR RANGE duration above 10 s`, `ERR BUSY` if the ADC is not ready or (ARMED) a ramp is in progress. In ARMED a conversion above `i_compliance` zeroes the output and sets `COMPLIANCE` |
| `ZERO` | DISARMED; ARMED at 0 V (`ERR STATE output not at 0 V` otherwise) | Deferred. **All four jacks open** (DUT removed). Measures both offsets (up to 64 pairs, at most 10 s): `OK ZERO o_v=<codes> o_i=<codes> sd_v=<codes> sd_i=<codes> pairs=<n> sps=<rate>` (codes with 2 decimals). Rejected with `ERR RANGE implausible o_v=<codes> o_i=<codes>` if \|o_i\| > 100 µA or \|o_v\| > 10 mV equivalent, beyond the INA128P worst-case offsets to 85 °C (79 µA / 8.9 mV referred to the DUT): fixture not open?; the previous zero is kept |

### 5.4 Configuration

| Command | States | Effect / response |
|---|---|---|
| `CFG key=value ...` | DISARMED, ARMED (`adc_rate` only in DISARMED) | Validates all given keys together, then applies them: `OK`. Nothing changes on error |
| `CFG?` | any | `OK CFG adc_rate=<sps> n_avg=<n> settle_us=<us> first_settle_us=<us> ramp_rate=<V/s> v_limit=<V> i_compliance=<A> on_compliance=<stop\|fault> end=<zero\|hold> line_hz=<50\|60> host_timeout_s=<s>`; `adc_rate` with 1 decimal, `ramp_rate`/`v_limit` with 6, `i_compliance` with 9 |

| Key | Type | Default | Range |
|---|---|---|---|
| `adc_rate` | SPS | 1000 | 2.5, 5, 10, 15, 25, 30, 50, 60, 100, 500, 1000, 2000, 3750 |
| `n_avg` | integer | 1 | 1–256 |
| `settle_us` | µs | 1000 | 100–60000000 |
| `first_settle_us` | µs | 50000 | 100–60000000 |
| `ramp_rate` | V/s | 10 | 0.01–10 |
| `v_limit` | V | 10 | 0.001–10 (in ARMED, not below the held level) |
| `i_compliance` | A | 0.1 | 0.000001–0.1 |
| `on_compliance` | word | `stop` | `stop`, `fault` |
| `end` | word | `zero` | `zero`, `hold` |
| `line_hz` | integer | 50 | 50, 60 |
| `host_timeout_s` | integer | 30 | 0–3600 |

Changing `adc_rate` re-runs the ADC self-calibration and marks the session
zero stale. The `i_compliance` default is its maximum: above the 92.6 mA
worst-case passive current bound, so the hardware plateau (expected 40…65 mA)
is what limits by default and is flagged `CLIM`; set it lower to protect a
DUT.

### 5.5 Sweeps

| Command | States | Effect / response |
|---|---|---|
| `SWP.WP <v0> <v1> ... STEP=<V> [CYCLES=<k>]` | DISARMED, ARMED | Defines a waypoint sweep (2–16 waypoints, volts). Every segment must be a non-zero integer multiple of `STEP` (≥ 0.5 mV); otherwise `ERR ARG not divisible suggest=<V\|none>` (the largest valid step ≤ the requested one). `CYCLES` (1–1000) > 1 needs the last waypoint equal to the first and repeats segments 1…n. Points beyond `v_limit`: `ERR RANGE beyond v_limit`. `OK SWP kind=wp points=<N> est_s=<s>` |
| `SWP.LIST.CLR` | DISARMED, ARMED | Clears the list buffer: `OK` |
| `SWP.LIST.ADD <v>[:<settle_us>] ...` | DISARMED, ARMED | Appends 1–8 points (≤ 2048 total; `ERR RANGE list full`, nothing appended): `OK n=<total>`. `settle_us` 100–60 000 000; omitted = `settle_us` of the configuration |
| `SWP.LIST.USE` | DISARMED, ARMED | Selects the list as the sweep: `OK SWP kind=list points=<N> est_s=<s>` |
| `SWP?` | any | `OK SWP kind=<none\|wp\|list> points=<N> est_s=<s>` |
| `SWP.RUN` | ARMED | Validates the definition against the current `v_limit` and calibration, then `OK id=<sweep_id> points=<N> est_s=<s>`, then `EVT SWEEP_BEGIN ...`, `PT` lines, and `EVT SWEEP_END ...`. `ERR BUSY ramp in progress` while an `SRC` ramp runs (wait for its `SRC_DONE`) |
| `SWP.ABORT` | SWEEPING | `OK`; the sweep ends at once (the point in progress is discarded, no partial record), ramps to 0 V and reports `status=aborted`. Takes effect on arrival even when earlier commands wait (§2); it is then answered `OK` in its place even though the state is no longer SWEEPING |
| `DATA.FETCH FROM=<seq>` | any | Re-sends retained records from `seq`: `OK first=<seq> last=<seq>` (`first > last` when none are retained) |

Sweep execution: pre-ramp from the present output to the first point at
`ramp_rate` (not recorded), `first_settle_us`, then for every point: DAC load,
settle (`settle_us` or the list point's own), conversions I,V,V,I,...
(`n_avg` pairs), checks, one `PT` record. End action: `end=zero` ramps to
0 V, `end=hold` keeps the last point; `SWP.ABORT` and `status=overflow`
always ramp to 0 V. The state returns to `ARMED`.

Compliance during a sweep (`on_compliance=stop`): the output goes to the zero
code at once (no ramp), the tripping point produces no record, and the sweep
ends with `status=compliance trip_idx=<idx> trip_i_a=<A>`; with
`on_compliance=fault` the instrument enters `FAULT(COMPLIANCE)`.

### 5.6 Calibration

| Command | States | Effect / response |
|---|---|---|
| `CAL?` | any | `OK CAL source=<stored\|nominal\|ram> seq=<n> measured=0x<hh> g_dac_uv=<x> c_dac=<x> g_v_uv=<x> g_i_na=<x> r_s=<x> r_top=<x> i_plateau=<x>` `seq`: the newest stored record (0: none). `source`: `stored` = RAM holds that record; `ram` = RAM was changed by `CAL.SET` since the last load or save (its values may still equal the record), or holds an older record reloaded while a `CAL.SAVE` from an earlier session was still pending; `nominal` = the nominal constants. `measured`: which coefficients were set with `CAL.SET` to a value other than the nominal one, bit k = the k-th key in this order (0x7F: all); saved with the record. While any of bits 0–4 is clear, records carry `CAL_NOMINAL` and the nominal Kelvin tolerance applies |
| `CAL.SET key=value ...` | DISARMED | Sets RAM coefficients (keys as in `CAL?`, units and resolutions below) and marks them measured, except a key set to its nominal value at that resolution (as `CAL?` prints it), which counts as nominal: a `CAL?` round trip keeps the mask (a measured value that happens to equal the nominal one at that resolution is entered one step away; `iv-cal` does this). Rejected with `ERR RANGE implausible coefficients` if the merged set is outside the plausibility window, `ERR ARG <key> inexact` beyond the resolution. `OK` |
| `CAL.SAVE TS=<unix seconds> [NOTE=<word>]` | DISARMED, ≥ 100 ms after the last heartbeat edge | Deferred. Waits for the ADC to be idle, writes the coefficients RAM held when the command was accepted (also if the job outlives its session) to the alternate flash sector and verifies: `OK seq=<n>`, `ERR FLASH erase/program/verify failed`, `ERR RANGE implausible coefficients`. `NOTE` ≤ 31 characters |
| `CAL.DEFAULT` | DISARMED | RAM coefficients ← nominal, none marked measured (not saved): `OK` |
| `CAL.RELOAD` | DISARMED | RAM coefficients ← newest valid stored record (or nominal): `OK source=<stored\|nominal> seq=<n>` |
| `ADC.SELFCAL` | DISARMED | Deferred: re-runs the ADS1256 self-calibration: `OK` |
| `SELFTEST` | DISARMED | Deferred: re-runs POST (register read-back, self-check conversions, front-end check): `OK SELFTEST negref=<code> zero=<code> afe=<ok\|fail>` or `ERR FAULT POST` (and `FAULT(POST)` is latched) |

Calibration values on the wire (`CAL?`, `CAL.SET`, `SWEEP_BEGIN`) are exact at
these resolutions, so `CAL?` output can be fed back to `CAL.SET` unchanged:

| Key | Unit | Decimals |
|---|---|---|
| `g_dac_uv` | µV per DAC code | 6 |
| `c_dac` | DAC code at V_SWEEP = 0 | 4 |
| `g_v_uv` | µV per ADC code | 6 |
| `g_i_na` | nA per ADC code | 6 |
| `r_s`, `r_top` | Ω | 4 |
| `i_plateau` | A | 9 |

### 5.7 Bring-up build only

Present only when `*IDN?` reports `build=bringup`; in every other build these
verbs return `ERR UNKNOWN`. They exist for `docs/bringup_checklist.md` §6–§8.

| Command | States | Effect |
|---|---|---|
| `HB.FREQ <hz>` | ARMED | Heartbeat 500–5000 Hz; gap limit becomes max(1 ms, half period + 0.5 ms). `HB.FREQ 2000` restores the default |
| `HB.EDGE` | DISARMED | One rising edge on PC5, then held high (pump single-edge test). Ends with `HB.HOLD OFF` |
| `HB.HOLD <HIGH\|LOW\|OFF>` | DISARMED | Holds PC5 static (no edges); `OFF` returns it to low |
| `DAC.RAW <a> <b> CONFIRM=UNSAFE` | ARMED | **Unsafe:** loads arbitrary codes into both channels. With `b < 65535` the positive output bound no longer holds. Suspends the channel-B invariant and the DAC scrub until `DAC.RAW OFF`, which reloads the zero pair |
| `ADC.PGA <1\|2\|4\|8\|16\|32\|64>` | DISARMED | Sets the ADC PGA (self-calibrates); the scaling constants are NOT adjusted (noise characterisation only) |
| `ADC.RESETPULSE` | DISARMED | Pulses the ADS1256 /RESET without informing the driver (tests the register scrub) |
| `CRASH <HARDFAULT\|HANG\|HANG_IRQOFF\|STACK>` | any | Provokes the named failure to test the fault handler, dead-man and IWDG paths; `STACK` overflows the thread stack into its MPU guard (MemManage) |

## 6. Measurement records

```
PT <sweep_id>,<seq>,<idx>,<seg>,<t_set_us>,<t_i_us>,<t_v_us>,<dac_code>,<v_set_V>,<n>,<i_raw>,<v_raw>,<i_sd>,<v_sd>,<v_V>,<i_A>,<flags>
```

| Field | Meaning |
|---|---|
| `sweep_id` | sweep number (per boot) |
| `seq` | global record sequence number (gap ⇒ lost lines; use `DATA.FETCH`) |
| `idx` | point index within the sweep (0-based) |
| `seg` | waypoint segment (1-based; 0 for the first point and list sweeps) |
| `t_set_us` | DAC load time, µs from the sweep start |
| `t_i_us`, `t_v_us` | mean time of the I and V conversions (the centre of each conversion's input window, also for a conversion read late), µs from the sweep start (wraps after 71.6 min; unwrap monotonically) |
| `dac_code` | channel-A code loaded |
| `v_set_V` | calibrated V_SWEEP for that code (not the DUT voltage) |
| `n` | conversions per channel |
| `i_raw`, `v_raw` | mean raw ADC codes, rounded to whole codes; the authoritative data |
| `i_sd`, `v_sd` | sample standard deviation in codes (1 decimal), `-` if `n < 2`; at most 6553.4, where `SD_SAT` is set (the true value is larger) |
| `v_V`, `i_A` | DUT voltage and current from the unrounded means (the sub-code resolution of averaging is kept), the active calibration and the session zero (a stale zero is still applied and flagged), computed when the line is formatted |
| `flags` | `0x` + 4 hex digits, bits below |

| Bit | Flag | Meaning |
|---|---|---|
| 0 | `OVR_V` | voltage conversion clipped |
| 1 | `OVR_I` | current conversion clipped |
| 2 | `CLIM` | current-limit plateau: not DUT data (with `KELVIN` as well when V_K also shows a lifted SENSE+ lead) |
| 3 | `KELVIN` | Kelvin consistency check failed (open/swapped sense lead, tripped PTC, ...) |
| 4 | `NO_OUTPUT` | force path open (U12 open / rails missing / PTC open / FORCE leads open) |
| 5 | `COMPLIANCE` | software compliance tripped at this point |
| 6 | `LATE` | settle or read timing missed its deadline (a first conversion more than two 150 µs passes after the settle, or a conversion read after its deadline) |
| 7 | `FIRST` | first point after the pre-ramp |
| 8 | `CAL_NOMINAL` | nominal (uncalibrated) constants in use for one of `g_dac_uv`, `c_dac`, `g_v_uv`, `g_i_na`, `r_s` (§5.6) |
| 9 | `ZERO_STALE` | no valid session zero |
| 10 | `MEAS` | (MEAS? only) |
| 11 | `SD_SAT` | `i_sd` or `v_sd` reached its 6553.4-code ceiling; the true value is larger |

## 7. Events

| Event | When |
|---|---|
| `EVT HELLO model=... fw=... proto=2 build=... serial=... state=<S> fault=<F> disarm_reason=<R> reset=<causes> crash=<0\|1>` | session start |
| `EVT STATE <S> [fault=<F>] [reason=<R>]` | every state change |
| `EVT FAULT <F> detail=<word>` | a fault is latched; `detail` names the check (`hb_gap`, `dac_spi`, `dac_scrub`, `adc_timeout`, `adc_readback`, `adc_stuck`, `adc_spi`, `stale`, `clip_i`, `clip_v`, `afe_signature` (both front-end outputs near AGND while armed: a ±15 V rail is missing and U12 has opened FORCE+), `b_shadow`, `tim2_frozen`, `hb_state`, `hb_thread`, `canary`, `deadline`, `compliance`, `hse`, `post_init`, `post_negref`, `post_zero`, `post_adc`, `post_timeout`, `setpoint`) |
| `EVT WARN <W> ...` | `KELVIN idx=<i>` (3 consecutive points, once per sweep), `NO_OUTPUT idx=<i>`, `LATE idx=<i>` (first in a sweep), `COMPLIANCE i_a=<A>` (output zeroed outside a sweep; with the output already at 0 V only the first trip, until the output is driven again), `HOST_TIMEOUT`, `SESSION_LOST` (dropped: no session), `AFE` (front-end check failed), `EVT_LOST n=<k>` |
| `EVT SRC_DONE v_v=<V> status=<reached\|compliance\|aborted>` | an `SRC` ramp ended: at its target, or at 0 V after a compliance trip (`aborted`: bring-up `DAC.RAW OFF`) |
| `EVT SWEEP_BEGIN id=<n> kind=<wp\|list> points=<N> t0_us=<µs> adc_rate=... n_avg=... settle_us=... first_settle_us=... ramp_rate=... v_limit=... i_compliance=... on_compliance=... end=... cal_source=... cal_seq=... g_dac_uv=... c_dac=... g_v_uv=... g_i_na=... r_s=... r_top=... i_plateau=... zero=<valid\|none\|stale> o_v=... o_i=... zero_sps=... zero_age_s=...` | sweep start |
| `EVT SWEEP_END id=<n> status=<complete\|aborted\|compliance\|overflow\|session_lost\|host_timeout\|fault> points=<n> [fault=<F>] [trip_idx=<i> trip_i_a=<A>]` | sweep end, after every record of the sweep |

Events are never allowed to block measurement: if the transmit buffer (or,
for `SWEEP_BEGIN`/`SWEEP_END`/`STATE` waiting behind records, the ordered
queue) is full, an event is dropped and counted; `EVT WARN EVT_LOST n=<k>`
reports the loss once space is available. Because commands wait while the
ordered queue lacks room for their events, a stalled host loses none of the
events its own commands cause. Only `SWEEP_BEGIN`, `SWEEP_END` and `STATE`
are ordered behind records; every other event (`FAULT`, `WARN`, `SRC_DONE`,
...) is sent at once and can precede records measured before it. Measurement records are never dropped: if the
host falls 1024 records behind, the sweep ends with `status=overflow`.

## 8. Example session

```
> *IDN?
< OK IDN model=stage1-iv fw=0.1.0 rev=0b5c000-dirty proto=2 hw=stage1-iv@c4e7592 build=release serial=0034002B3436511233353533 usb_vid=0x1209 usb_pid=0x0001 usb_pid_status=dev-test planned_pid=500S
> ZERO
< OK ZERO o_v=-112.40 o_i=387.10 sd_v=2.10 sd_i=2.60 pairs=64 sps=1000
> CFG settle_us=2000 n_avg=2
< OK
> SWP.WP 0 1 -1 0 STEP=0.5
< OK SWP kind=wp points=9 est_s=0.12
> ARM
< OK
< EVT STATE ARMING
< EVT STATE ARMED
> SWP.RUN
< OK id=1 points=9 est_s=0.12
< EVT SWEEP_BEGIN id=1 kind=wp points=9 ...
< PT 1,1,0,0,51234,52500,53700,32768,0.000153,2,387,-112,1.4,0.7,0.000000,0.0000000000,0x0380
< PT 1,2,1,1,55100,56300,57500,34400,0.500085,2,...
< ...
< EVT SWEEP_END id=1 status=complete points=9
> DISARM
< OK
< EVT STATE DISARMING
< EVT STATE DISARMED
```
