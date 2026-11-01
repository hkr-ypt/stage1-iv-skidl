/*
 * usb_descriptors.c — USB device identity of the Stage 1 I-V instrument
 * (docs/firmware/usb_identity.md).
 *
 *  - VID 0x1209 (pid.codes). PID = FW_USB_PID from CMake. The development
 *    default 0x0001 is the pid.codes PID reserved for private in-house
 *    testing: it is NOT unique and must not ship. The project's planned
 *    designation "500S" is not registered, not assigned and not a valid
 *    hexadecimal PID, so it never appears in a descriptor.
 *  - bcdDevice = FW_VERSION_BCD (major.minor.patch as 0xMMmp).
 *  - Strings: manufacturer, product ("Stage1 IV", " BRINGUP" appended in
 *    the bring-up variant so the image cannot be mistaken for production),
 *    serial = the 96-bit UID as 24 upper-case hex characters, in the same
 *    word order as *IDN? serial=.
 *  - One configuration, one CDC-ACM function (notification EP 0x81, data
 *    OUT 0x02 / IN 0x82, 64-byte full-speed packets). Self-powered (the
 *    board runs from the bench supply at J1, never from VBUS), 2 mA from the
 *    bus at most (the 200 k VBUS divider); the D+ pull-up follows VBUS.
 */
#include "port_stm32.h"

#include "tusb.h"

#include <string.h>

#ifndef FW_VERSION_BCD
#define FW_VERSION_BCD 0x0000
#endif

#if FW_USB_PID_REGISTERED
#define USB_PID_NOTE "registered"
#else
/* 0x1209:0x0001 = pid.codes private testing PID: development only. */
#define USB_PID_NOTE "dev-test"
#endif

enum { ITF_CDC = 0, ITF_CDC_DATA, ITF_TOTAL };
enum { STR_LANG = 0, STR_MANUF, STR_PRODUCT, STR_SERIAL, STR_CDC };

#define EP_NOTIF  0x81
#define EP_OUT    0x02
#define EP_IN     0x82

static const tusb_desc_device_t k_device = {
    .bLength            = sizeof(tusb_desc_device_t),
    .bDescriptorType    = TUSB_DESC_DEVICE,
    .bcdUSB             = 0x0200,
    .bDeviceClass       = TUSB_CLASS_MISC,
    .bDeviceSubClass    = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol    = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0    = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor           = 0x1209,
    .idProduct          = FW_USB_PID,
    .bcdDevice          = FW_VERSION_BCD,
    .iManufacturer      = STR_MANUF,
    .iProduct           = STR_PRODUCT,
    .iSerialNumber      = STR_SERIAL,
    .bNumConfigurations = 1,
};

const uint8_t *tud_descriptor_device_cb(void)
{
    return (const uint8_t *)&k_device;
}

#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN)

static const uint8_t k_config[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_TOTAL, 0, CONFIG_TOTAL_LEN, TUSB_DESC_CONFIG_ATT_SELF_POWERED, 2),
    TUD_CDC_DESCRIPTOR(ITF_CDC, STR_CDC, EP_NOTIF, 8, EP_OUT, EP_IN, 64),
};

const uint8_t *tud_descriptor_configuration_cb(uint8_t index)
{
    (void)index;
    return k_config;
}

#if FW_BRINGUP
#define PRODUCT_STRING "Stage1 IV BRINGUP"
#else
#define PRODUCT_STRING "Stage1 IV"
#endif

static uint16_t s_str[32];

static uint8_t put_ascii(const char *s)
{
    size_t len = strlen(s);
    if (len > 31u) {
        len = 31u;                           /* s_str has 31 character slots */
    }
    for (size_t i = 0; i < len; i++) {
        s_str[1u + i] = (uint16_t)(uint8_t)s[i];
    }
    return (uint8_t)len;
}

const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t langid)
{
    (void)langid;
    uint8_t n;
    switch (index) {
    case STR_LANG:
        s_str[1] = 0x0409;
        n = 1;
        break;
    case STR_MANUF:
        n = put_ascii("Stage1 IV project");
        break;
    case STR_PRODUCT:
        n = put_ascii(PRODUCT_STRING);
        break;
    case STR_SERIAL: {
        static const char hex[] = "0123456789ABCDEF";
        uint32_t uid[3];
        port_uid(uid);
        n = 0;
        for (unsigned w = 0; w < 3u; w++) {
            for (int nib = 7; nib >= 0; nib--) {
                s_str[1u + n] = (uint16_t)hex[(uid[w] >> (4u * (unsigned)nib)) & 0xFu];
                n++;
            }
        }
        break;
    }
    case STR_CDC:
        n = put_ascii("Stage1 IV CDC");
        break;
    default:
        return NULL;
    }
    s_str[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2u * n + 2u));
    return s_str;
}
