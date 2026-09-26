# Library dependencies

**GENERATED FILE — do not edit by hand.**
Regenerate with `python3 tools/gen_project_config.py`; verify with
`python3 tools/gen_project_config.py --check`. It is built from the live
circuit, so it cannot describe anything the design does not actually use.

Resolved against:

| | repository | pinned revision | commit |
|---|---|---|---|
| symbols | `https://gitlab.com/kicad/libraries/kicad-symbols.git` | `9.0.9.1` | `ad36cd14bcd1b1cd0484f629ccdd3481366f74f3` |
| footprints | `https://gitlab.com/kicad/libraries/kicad-footprints.git` | `9.0.9.1` | `2b941bf1d97862be429793b46fd52a5add09a2fc` |

Install exactly this revision with `tools/install_kicad_libs.sh`. The
build does not require it — see "Building without KiCad" below — but
every pin number below was read from it.

## Symbols the schematic instantiates

Every schematic symbol resolves to the **project** library, so KiCad
never has to find an upstream symbol to open or ERC this design.

| lib_id | symbols | resolves via |
|---|---|---|
| `stage1_iv:*` | 25 | `${KIPRJMOD}/libraries/stage1_iv.kicad_sym` (committed) |

## Upstream symbols the pin numbers were read from

These are the authoritative sources for pin numbering. They are recorded
as each part's `KiCad_Lib` field and verified by
`validation/footprint_check.py` whenever the libraries are installed.

| upstream lib_id | used by | symbol read from |
|---|---|---|
| `Amplifier_Instrumentation:INA128` | U6, U7 | `Amplifier_Instrumentation.kicad_sym:2285` |
| `Amplifier_Operational:OPA2277` | U3 | `Amplifier_Operational.kicad_sym:47360` |
| `Analog_DAC:DAC8552` | U4 | `Analog_DAC.kicad_sym:22608` |
| `Connector:Screw_Terminal_01x04` | J1 | `Connector.kicad_sym:326279` |
| `Connector:TestPoint` | TP1, TP2, TP3, TP4, TP5, TP6 +2 | `Connector.kicad_sym:339728` |
| `Connector:USB_C_Receptacle_USB2.0_16P` | J3 | `Connector.kicad_sym:348661` |
| `Connector_Generic:Conn_01x01` | J4, J5, J6, J7 | `Connector_Generic.kicad_sym:5` |
| `Connector_Generic:Conn_01x05` | J2 | `Connector_Generic.kicad_sym:631` |
| `Device:C` | C10, C11, C12, C13, C14, C15 +28 | `Device.kicad_sym:1761` |
| `Device:C_Polarized` | C1, C17, C18, C2, C29, C5 +3 | `Device.kicad_sym:5140` |
| `Device:Crystal` | Y1, Y2 | `Device.kicad_sym:9588` |
| `Device:D` | D1, D2, D3, D4 | `Device.kicad_sym:11674` |
| `Device:D_TVS` | D5 | `Device.kicad_sym:35674` |
| `Device:FerriteBead` | FB1, FB2 | `Device.kicad_sym:42596` |
| `Device:LED` | LED1 | `Device.kicad_sym:48335` |
| `Device:Polyfuse` | F1 | `Device.kicad_sym:94206` |
| `Device:R` | R1, R10, R11, R12, R13, R14 +27 | `Device.kicad_sym:104590` |
| `MCU_ST_STM32F4:STM32F411RETx` | U9 | `MCU_ST_STM32F4.kicad_sym:332580` |
| `Power_Protection:USBLC6-2SC6` | U10 | `Power_Protection.kicad_sym:35786` |
| `Reference_Voltage:REF5025ADGK` | U2 | `Reference_Voltage.kicad_sym:29304` |
| `Regulator_Linear:LD1117S33TR_SOT223` | U1 | `Regulator_Linear.kicad_sym:85573` |
| `Switch:SW_Push` | SW1 | `Switch.kicad_sym:11011` |
| `power:PWR_FLAG` | #FLG1, #FLG2, #FLG3, #FLG4, #FLG5, #FLG6 +2 | `power.kicad_sym:9842` |

## Footprints

Footprints are **not** vendored: they resolve through
`${KICAD9_FOOTPRINT_DIR}` via the committed `fp-lib-table`. This is a real
runtime dependency — laying out the PCB needs these libraries installed.

| footprint | used by |
|---|---|
| `Button_Switch_THT:SW_PUSH_6mm` | SW1 |
| `Capacitor_SMD:CP_Elec_6.3x5.4` | C8 |
| `Capacitor_SMD:CP_Elec_6.3x7.7` | C7 |
| `Capacitor_SMD:CP_Elec_8x10.5` | C5, C6 |
| `Capacitor_SMD:C_0603_1608Metric` | C10, C11, C12, C13, C19, C20 +22 |
| `Capacitor_SMD:C_0805_2012Metric` | C14, C15, C16, C3, C39, C4 |
| `Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B` | C1, C17, C18, C2, C29 |
| `Connector:Banana_Jack_1Pin` | J4, J5, J6, J7 |
| `Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical` | J2 |
| `Connector_USB:USB_C_Receptacle_GCT_USB4085` | J3 |
| `Crystal:Crystal_SMD_HC49-SD` | Y1, Y2 |
| `Diode_SMD:D_SMB` | D5 |
| `Diode_SMD:D_SOD-123` | D1, D2, D3, D4 |
| `Fuse:Fuse_Bourns_MF-RG300` | F1 |
| `Inductor_SMD:L_0805_2012Metric` | FB1, FB2 |
| `LED_SMD:LED_0805_2012Metric` | LED1 |
| `Package_DIP:DIP-8_W7.62mm` | U6, U7 |
| `Package_QFP:LQFP-64_10x10mm_P0.5mm` | U9 |
| `Package_SO:SOIC-8_3.9x4.9mm_P1.27mm` | U3 |
| `Package_SO:SSOP-28_5.3x10.2mm_P0.65mm` | U8 |
| `Package_SO:VSSOP-8_3x3mm_P0.65mm` | U2, U4 |
| `Package_TO_SOT_SMD:SOT-223-3_TabPin2` | U1 |
| `Package_TO_SOT_SMD:SOT-23-6` | U10 |
| `Package_TO_SOT_THT:TO-220-7_P2.54x3.7mm_StaggerOdd_Lead3.8mm_Vertical` | U5 |
| `Resistor_SMD:R_0603_1608Metric` | R1, R11, R12, R13, R14, R15 +22 |
| `Resistor_SMD:R_0805_2012Metric` | R31 |
| `Resistor_SMD:R_1206_3216Metric` | R30, R7, R9 |
| `Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal` | R10 |
| `TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-4-5.08_1x04_P5.08mm_Horizontal` | J1 |
| `TestPoint:TestPoint_Pad_D1.5mm` | TP1, TP2, TP3, TP4, TP5, TP6 +2 |

## Building without KiCad

`python3 main.py` runs with no KiCad installed. `libraries/custom_symbols.py`
falls back to the committed project library for pin data, and the build
banner reports **SKIPPED** for every KiCad-conformance check rather than
anything resembling a pass. Run `python3 tools/kicad_env.py --diagnose`
to see what resolved and whether the installation is complete.
