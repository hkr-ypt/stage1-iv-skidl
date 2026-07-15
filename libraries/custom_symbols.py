"""
custom_symbols.py — SKiDL-native symbol library for the Stage 1 I–V Characterization System
============================================================================================

WHY THIS FILE EXISTS
--------------------
The generation environment used to produce this project does **not** have the
official KiCad symbol/footprint libraries installed (no ``KICADn_SYMBOL_DIR``,
no ``fp-lib-table``). To keep the design *fully executable* — able to run ERC,
build the netlist, and validate connectivity — every component is declared here
as a **SKiDL-native part** (``tool=SKIDL``) with explicit pins.

Each factory records, as component *fields*, the **intended official KiCad
library identifier** and the manufacturer part number, so that when this design
is later opened in KiCad the parts map cleanly onto the real symbols.

PIN-NUMBER HONESTY POLICY  (see project spec: "Never guess package pin numbers")
--------------------------------------------------------------------------------
Connectivity in the block files is expressed **exclusively by pin function
name** (e.g. ``dac['VREF'] += VREF_2V5``), never by raw pin number. Pin numbers
exist here only so a KiCad netlist can be emitted and mapped to footprint pads.

Every part carries a ``Pins_Verified`` field describing the provenance of its
pin *numbers*:

    "STD"                 Universal / jelly-bean pinout, physically standard for
                          the package (R, C, D, LED, INA128 DIP-8, dual-op-amp
                          SOIC-8, LD1117 SOT-223). Safe to fabricate.
    "VERIFY-DATASHEET"    Pin *names* are authoritative; pin *numbers* are the
                          best-known datasheet mapping but MUST be checked
                          against the manufacturer datasheet / official KiCad
                          symbol before fabrication (multi-config small ICs:
                          DAC8552, REF5025, OPA548, USBLC6).
    "PLACEHOLDER-REMAP"   Fine-pitch part whose exact pin numbers are explicitly
                          NOT asserted from memory (per spec §0.7). Numbers here
                          are sequential placeholders. The pin NAME/NET mapping
                          is complete and correct; pull real pad numbers from the
                          official KiCad symbol at layout time. (STM32F411RET6
                          LQFP-64, ADS1256 TSSOP-28.)

Anything marked PLACEHOLDER-REMAP or VERIFY-DATASHEET is surfaced again in
``manifest.yaml`` and the component manifest table so it cannot be missed.
"""

from skidl import Part, Pin, SKIDL, TEMPLATE

# Convenient aliases for pin electrical types.
_IN = Pin.types.INPUT
_OUT = Pin.types.OUTPUT
_BIDIR = Pin.types.BIDIR
_TRI = Pin.types.TRISTATE
_PAS = Pin.types.PASSIVE
_PWRIN = Pin.types.PWRIN
_PWROUT = Pin.types.PWROUT
_NC = Pin.types.NOCONNECT
_OC = Pin.types.OPENCOLL
_UNSPEC = Pin.types.UNSPEC


def _part(name, ref_prefix, footprint, pins, kicad_lib, mpn,
          pins_verified, description, datasheet="", keywords=""):
    """Build a TEMPLATE SKiDL part and stamp it with documentation fields.

    `pins` is a list of (num, name, func) tuples. Returned part is a TEMPLATE;
    call it (e.g. ``DAC8552()(ref='U4')``) to instantiate an instance into the
    active circuit.
    """
    p = Part(
        name=name,
        tool=SKIDL,
        ref_prefix=ref_prefix,
        dest=TEMPLATE,
        footprint=footprint,
        pins=[Pin(num=str(n), name=nm, func=fn) for (n, nm, fn) in pins],
    )
    p.description = description
    p.keywords = keywords
    p.datasheet = datasheet
    # Documentation fields carried into the KiCad netlist / BOM.
    p.fields["MPN"] = mpn
    p.fields["KiCad_Lib"] = kicad_lib
    p.fields["Pins_Verified"] = pins_verified
    return p


# =====================================================================
#  GENERIC PASSIVES  (pinouts are physically universal → "STD")
# =====================================================================

def R():
    return _part("R", "R", "Resistor_SMD:R_0603_1608Metric",
                 [("1", "~", _PAS), ("2", "~", _PAS)],
                 "Device:R", "generic", "STD", "Resistor")


def C():
    return _part("C", "C", "Capacitor_SMD:C_0603_1608Metric",
                 [("1", "~", _PAS), ("2", "~", _PAS)],
                 "Device:C", "generic", "STD", "Unpolarized capacitor")


def C_POL():
    # KiCad Device:CP — pin 1 = +, pin 2 = -
    return _part("CP", "C", "Capacitor_THT:CP_Radial_D8.0mm_P3.50mm",
                 [("1", "+", _PAS), ("2", "-", _PAS)],
                 "Device:CP", "generic", "STD", "Polarized capacitor")


def L_FerriteBead():
    return _part("FerriteBead", "FB", "Inductor_SMD:L_0805_2012Metric",
                 [("1", "1", _PAS), ("2", "2", _PAS)],
                 "Device:FerriteBead", "BLM21PG221SN1D", "STD",
                 "Ferrite bead, 220R @ 100MHz")


def D():
    # KiCad footprint convention: cathode = pad 1.
    return _part("D", "D", "Diode_SMD:D_SOD-123",
                 [("1", "K", _PAS), ("2", "A", _PAS)],
                 "Device:D", "1N4148", "STD", "Diode (cathode=pad1)")


def D_TVS():
    # Bidirectional TVS — both pins passive, no polarity for the CA variant.
    return _part("D_TVS", "D", "Diode_SMD:D_SMB",
                 [("1", "A1", _PAS), ("2", "A2", _PAS)],
                 "Device:D_TVS", "SMBJ15CA", "STD", "Bidirectional TVS diode")


def LED():
    return _part("LED", "D", "LED_SMD:LED_0805_2012Metric",
                 [("1", "K", _PAS), ("2", "A", _PAS)],
                 "Device:LED", "generic", "STD", "Light emitting diode (cathode=pad1)")


def Fuse_PTC():
    return _part("Polyfuse", "F", "Fuse:Fuse_Bourns-MF-RG300",
                 [("1", "1", _PAS), ("2", "2", _PAS)],
                 "Device:Polyfuse", "0ZRM0010FF1E", "STD",
                 "PTC resettable fuse, 100mA hold")


def Crystal():
    return _part("Crystal", "Y", "Crystal:Crystal_SMD_HC49-SD",
                 [("1", "1", _PAS), ("2", "2", _PAS)],
                 "Device:Crystal", "generic", "STD", "Two pin crystal")


def TestPoint():
    return _part("TestPoint", "TP", "TestPoint:TestPoint_Pad_D1.5mm",
                 [("1", "1", _PAS)],
                 "Connector:TestPoint", "n/a", "STD", "Test point")


def SW_Push():
    return _part("SW_Push", "SW", "Button_Switch_THT:SW_PUSH_6mm",
                 [("1", "1", _PAS), ("2", "2", _PAS)],
                 "Switch:SW_Push", "generic", "STD", "Momentary push switch")


# =====================================================================
#  CONNECTORS
# =====================================================================

def TerminalBlock_4():
    return _part("Screw_Terminal_01x04", "J",
                 "TerminalBlock:TerminalBlock_bornier-4_P5.08mm",
                 [("1", "Pin_1", _PAS), ("2", "Pin_2", _PAS),
                  ("3", "Pin_3", _PAS), ("4", "Pin_4", _PAS)],
                 "Connector:Screw_Terminal_01x04", "generic-5.08mm", "STD",
                 "4-pos terminal block: +15V_RAW,-15V_RAW,+5V_RAW,GND")


def Header_1x05():
    return _part("Conn_01x05", "J",
                 "Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical",
                 [("1", "Pin_1", _PAS), ("2", "Pin_2", _PAS),
                  ("3", "Pin_3", _PAS), ("4", "Pin_4", _PAS),
                  ("5", "Pin_5", _PAS)],
                 "Connector_Generic:Conn_01x05", "generic-2.54mm", "STD",
                 "SWD header: SWDIO,SWCLK,GND,3V3,NRST")


def Conn_DUT_1x04():
    # J4 — physical DUT connector (banana / panel mount). Nets named by function.
    return _part("Conn_01x04", "J",
                 "Connector:Banana_Jack_1x04",
                 [("1", "FORCE+", _PAS), ("2", "SENSE+", _PAS),
                  ("3", "SENSE-", _PAS), ("4", "FORCE-", _PAS)],
                 "Connector_Generic:Conn_01x04", "panel-mount-banana", "STD",
                 "DUT connector: FORCE+,SENSE+,SENSE-,FORCE-")


def USB_C_Receptacle():
    # USB2.0-only USB-C receptacle. Reduced pin set (power/CC/data/shield).
    return _part("USB_C_Receptacle_USB2.0", "J",
                 "Connector_USB:USB_C_Receptacle_GCT_USB4085",
                 [("A1", "GND", _PWRIN), ("A4", "VBUS", _PWRIN),
                  ("A5", "CC1", _BIDIR), ("A6", "DP", _BIDIR),
                  ("A7", "DM", _BIDIR), ("B5", "CC2", _BIDIR),
                  ("S1", "SHIELD", _PAS)],
                 "Connector:USB_C_Receptacle_USB2.0", "USB-C 2.0 SMD",
                 "VERIFY-DATASHEET",
                 "USB-C receptacle (USB2.0 subset: VBUS,GND,CC1,CC2,DP,DM,SHIELD)")


# =====================================================================
#  POWER / REFERENCE
# =====================================================================

def LD1117S33():
    # SOT-223: pin1=GND(adj), pin2=VOUT(+tab), pin3=VIN. Universal LD1117 pinout.
    return _part("LD1117S33", "U", "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
                 [("1", "GND", _PWRIN), ("2", "VOUT", _PWROUT),
                  ("3", "VIN", _PWRIN)],
                 "Regulator_Linear:LD1117S33TR_SOT223", "LD1117S33TR", "STD",
                 "3.3V 800mA LDO", keywords="LDO regulator 3V3")


def REF5025():
    # VSSOP-8 (DGK). Names authoritative; numbers per datasheet, mark to verify.
    return _part("REF5025", "U", "Package_SO:VSSOP-8_3x3mm_P0.65mm",
                 [("1", "NC1", _NC), ("2", "VIN", _PWRIN), ("3", "TEMP", _OUT),
                  ("4", "GND", _PWRIN), ("5", "VOUT", _OUT),
                  ("6", "TRIM/NR", _PAS), ("7", "NC2", _NC), ("8", "NC3", _NC)],
                 "Reference_Voltage:REF5025AID", "REF5025AIDGKT",
                 "VERIFY-DATASHEET",
                 "2.500V precision voltage reference",
                 keywords="voltage reference 2.5V")


# =====================================================================
#  DAC / SWEEP
# =====================================================================

def DAC8552():
    # VSSOP-8 (DGK). Single shared VREF pin for both channels (per design note).
    return _part("DAC8552", "U", "Package_SO:VSSOP-8_3x3mm_P0.65mm",
                 [("1", "VDD", _PWRIN), ("2", "VOUTA", _OUT), ("3", "VOUTB", _OUT),
                  ("4", "VREF", _IN), ("5", "GND", _PWRIN), ("6", "SYNC", _IN),
                  ("7", "SCLK", _IN), ("8", "DIN", _IN)],
                 "Analog_DAC:DAC8552", "DAC8552IDGKR", "VERIFY-DATASHEET",
                 "16-bit dual voltage-output DAC, SPI",
                 keywords="DAC 16-bit dual SPI")


def OPA2277():
    # Dual precision op-amp, SOIC-8 — universal dual-op-amp pinout → STD.
    return _part("OPA2277", "U", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                 [("1", "OUTA", _OUT), ("2", "-INA", _IN), ("3", "+INA", _IN),
                  ("4", "V-", _PWRIN), ("5", "+INB", _IN), ("6", "-INB", _IN),
                  ("7", "OUTB", _OUT), ("8", "V+", _PWRIN)],
                 "Amplifier_Operational:OPA2277", "OPA2277UA", "STD",
                 "Dual precision op-amp",
                 keywords="opamp precision dual")


# =====================================================================
#  POWER BUFFER
# =====================================================================

def OPA548():
    # TO-220-7. Pin NAMES authoritative; numbers to verify against datasheet.
    return _part("OPA548", "U", "Package_TO_SOT_THT:TO-220-7_Vertical",
                 [("1", "E/S", _IN), ("2", "V-", _PWRIN), ("3", "-IN", _IN),
                  ("4", "+IN", _IN), ("5", "ILIM", _IN), ("6", "V+", _PWRIN),
                  ("7", "OUT", _PWROUT)],
                 "Amplifier_Operational:OPA548T", "OPA548T", "VERIFY-DATASHEET",
                 "High-current power op-amp with current limit",
                 keywords="power opamp current limit")


# =====================================================================
#  INSTRUMENTATION AMPLIFIERS (current + voltage sense)
# =====================================================================

def INA128P():
    # DIP-8. Numbers universal for the INA12x/INA114/AD620 family (design doc
    # asserts these exact numbers on Sheets 5/6) → STD.
    return _part("INA128P", "U", "Package_DIP:DIP-8_W7.62mm",
                 [("1", "RG1", _PAS), ("2", "-IN", _IN), ("3", "+IN", _IN),
                  ("4", "V-", _PWRIN), ("5", "REF", _IN), ("6", "OUT", _OUT),
                  ("7", "V+", _PWRIN), ("8", "RG2", _PAS)],
                 "Amplifier_Instrumentation:INA128P", "INA128P", "STD",
                 "Instrumentation amplifier, G=1+50k/RG",
                 keywords="instrumentation amplifier inamp")


# =====================================================================
#  ADC  (fine-pitch → PLACEHOLDER-REMAP pin numbers)
# =====================================================================

def ADS1256():
    # TSSOP-28. Pin NAME/NET mapping is complete; pin NUMBERS are sequential
    # placeholders to be remapped from the official symbol at layout time.
    names = [
        "AVDD", "AGND", "DVDD", "DGND", "VREFP", "VREFN",
        "AIN0", "AIN1", "AIN2", "AIN3", "AINCOM",
        "XTAL1", "XTAL2", "CLKIN",
        "CS", "SCLK", "DIN", "DOUT", "DRDY", "RESET", "PDWN", "SYNC",
    ]
    func = {
        "AVDD": _PWRIN, "AGND": _PWRIN, "DVDD": _PWRIN, "DGND": _PWRIN,
        "VREFP": _IN, "VREFN": _IN,
        "AIN0": _IN, "AIN1": _IN, "AIN2": _IN, "AIN3": _IN, "AINCOM": _IN,
        "XTAL1": _IN, "XTAL2": _OUT, "CLKIN": _IN,
        "CS": _IN, "SCLK": _IN, "DIN": _IN, "DOUT": _TRI,
        "DRDY": _OUT, "RESET": _IN, "PDWN": _IN, "SYNC": _IN,
    }
    pins = [(str(i + 1), nm, func[nm]) for i, nm in enumerate(names)]
    return _part("ADS1256", "U", "Package_SO:TSSOP-28_4.4x9.7mm_P0.65mm",
                 pins, "Analog_ADC:ADS1256", "ADS1256IDBR", "PLACEHOLDER-REMAP",
                 "24-bit delta-sigma ADC, 8ch, PGA, SPI",
                 keywords="ADC 24-bit delta-sigma SPI")


# =====================================================================
#  MCU  (fine-pitch → PLACEHOLDER-REMAP pin numbers)
# =====================================================================

def STM32F411RET6():
    # LQFP-64. Pin NAMES = STM32 port/function names actually used in the design.
    # Pin NUMBERS are sequential placeholders — remap from official
    # MCU_ST_STM32F4:STM32F411RETx symbol at layout time (per spec §0.7).
    named = [
        # power
        ("VBAT", _PWRIN), ("VDD", _PWRIN), ("VDDA", _PWRIN),
        ("VSS", _PWRIN), ("VSSA", _PWRIN), ("VCAP1", _PAS),
        # system
        ("NRST", _IN), ("BOOT0", _IN),
        ("PH0", _IN), ("PH1", _OUT),      # OSC_IN / OSC_OUT (HSE)
        # SPI1 -> DAC (write-only)
        ("PA4", _OUT),                    # CS_DAC (GPIO)
        ("PA5", _OUT),                    # SPI1_SCK  -> SCK1
        ("PA7", _OUT),                    # SPI1_MOSI -> MOSI1
        # SPI2 -> ADC (bidirectional)
        ("PB12", _OUT),                   # CS_ADC (GPIO)
        ("PB13", _OUT),                   # SPI2_SCK  -> SCK2
        ("PB14", _IN),                    # SPI2_MISO -> MISO2
        ("PB15", _OUT),                   # SPI2_MOSI -> MOSI2
        ("PA8", _IN),                     # DRDY (EXTI)
        ("PA9", _OUT),                    # RESET_ADC (GPIO)
        # USB OTG FS
        ("PA11", _BIDIR),                 # USB_DM
        ("PA12", _BIDIR),                 # USB_DP
        # SWD
        ("PA13", _BIDIR),                 # SWDIO
        ("PA14", _IN),                    # SWCLK
        # misc
        ("PA0", _IN),                     # VBUS_SENSE (ADC-capable)
        ("PC13", _OUT),                   # LED_STATUS
    ]
    pins = [(str(i + 1), nm, fn) for i, (nm, fn) in enumerate(named)]
    return _part("STM32F411RETx", "U", "Package_QFP:LQFP-64_10x10mm_P0.5mm",
                 pins, "MCU_ST_STM32F4:STM32F411RETx", "STM32F411RET6",
                 "PLACEHOLDER-REMAP",
                 "ARM Cortex-M4 MCU, 2x SPI, USB OTG FS",
                 keywords="STM32 MCU Cortex-M4")


# =====================================================================
#  USB ESD PROTECTION
# =====================================================================

def USBLC6_2SC6():
    # SOT-23-6. IO1 on pins 1&6, IO2 on pins 3&4, VBUS pin5, GND pin2.
    return _part("USBLC6-2SC6", "U", "Package_TO_SOT_SMD:SOT-23-6",
                 [("1", "IO1", _BIDIR), ("2", "GND", _PWRIN), ("3", "IO2", _BIDIR),
                  ("4", "IO2b", _BIDIR), ("5", "VBUS", _PWRIN), ("6", "IO1b", _BIDIR)],
                 "Power_Protection:USBLC6-2SC6", "USBLC6-2SC6", "VERIFY-DATASHEET",
                 "USB ESD protection array",
                 keywords="ESD USB protection")
