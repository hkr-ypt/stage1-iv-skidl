# USB identity

| Item | Value |
|---|---|
| Vendor ID | **0x1209** (pid.codes, the open-hardware VID) |
| Product ID (this repository's builds) | **0x0001** — the pid.codes PID reserved for *private in-house testing*. It is **not unique** and **must not ship**. |
| Planned product designation | **500S** — **not registered and not assigned**. It is a project designation, not a valid hexadecimal PID, and is never placed in a descriptor. |
| Serial number string | the STM32 96-bit unique ID as 24 upper-case hex characters (words UID[0], UID[1], UID[2]), identical to `*IDN? serial=` |
| Product string | `Stage1 IV`, or `Stage1 IV BRINGUP` in the bring-up variant |
| Manufacturer string | `Stage1 IV project` |
| bcdDevice | firmware version as 0xMMmp (0.1.0 → 0x0010) |
| Class | CDC-ACM (IAD composite), full speed |

Every build reports the identity it was built with: `*IDN?` returns
`usb_vid=0x1209 usb_pid=0x0001 usb_pid_status=dev-test planned_pid=500S`,
`STAT?` sets warning bit 9 (`DEV_USB_PID`), and the host library logs a
warning when it connects to a device with the development PID.

## Replacing the development PID

Once a PID has been obtained from pid.codes (an owner action outside the
software), build with:

```sh
cmake --preset fw-release -DFW_USB_PID=0xNNNN -DFW_USB_PID_REGISTERED=ON
```

* `FW_USB_PID` must be four hex digits; `FW_USB_PID_REGISTERED=ON` with
  0x0001 is refused at configure time.
* `*IDN?` then reports `usb_pid_status=registered` and the `DEV_USB_PID`
  warning disappears.
* Hosts find instruments by VID/PID and serial (`stage1iv.discovery`); set
  the PID there with `--pid 0xNNNN` or `STAGE1IV_PID=0xNNNN`.

Nothing in the firmware or the documentation claims that 500S, or any PID,
has been registered.
