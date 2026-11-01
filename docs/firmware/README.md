# Stage 1 I–V firmware and host software — documentation

| Document | Contents |
|---|---|
| [`firmware_spec.md`](firmware_spec.md) | the specification: safety model and invariants, verified hardware interface, state model, architecture, tests; §14 settled decisions, §15 implementation differences |
| [`architecture.md`](architecture.md) | the code as built: layers, modules, execution model, where each invariant is enforced, data paths, memory |
| [`protocol.md`](protocol.md) | host protocol v1 with every command, record, event and error, and an example session |
| [`state_machine.md`](state_machine.md) | states, ARM preconditions, sequences, faults, aborts, warnings, disarm reasons |
| [`calibration.md`](calibration.md) | calibration model, procedure, flash storage, the PT record and the CSV/JSON files, provisional accuracy targets |
| [`build.md`](build.md) | toolchain, dependencies, presets, variants, reproducibility, option bytes, flashing |
| [`usb_identity.md`](usb_identity.md) | VID/PID (development PID 0x0001; 500S not registered), strings, how to replace the PID |
| [`simulator.md`](simulator.md) | the whole-firmware simulator, its control protocol and what it models |
| [`verification.md`](verification.md) | tests, CI, results, and the hardware tests required before any claim |
| [`bringup_firmware.md`](bringup_firmware.md) | the firmware bring-up sequence on a new board, with commands (complements `../bringup_checklist.md`) |
| [`limitations.md`](limitations.md) | known limitations, open items, residual risks, owner actions |

Code: `firmware/` (C11, STM32F411RET6, TinyUSB 0.21.0), `host/` (Python
package `stage1iv`), CI `.github/workflows/firmware.yml`.
