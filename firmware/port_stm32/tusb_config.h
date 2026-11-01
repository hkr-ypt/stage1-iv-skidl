/*
 * tusb_config.h — TinyUSB 0.21.0 configuration for the Stage 1 I-V firmware:
 * one CDC-ACM interface, device mode, full speed, OTG_FS of the STM32F411.
 *
 * Facts relied on (TinyUSB 0.21.0 src/tusb_option.h, src/device/usbd.h,
 * src/portable/synopsys/dwc2/dwc2_stm32.h; firmware_spec.md §4.7, §7.1):
 *  - CFG_TUD_VBUS_DETECT_HW = 0 makes dcd_init() set GCCFG.NOVBUSSENS and
 *    clear VBUSASEN/VBUSBSEN (core ID 0x1200 < 0x2000) and force B-valid:
 *    PA9 stays a GPIO (RESET_ADC).
 *  - CFG_TUD_TASK_EVENTS_PER_RUN bounds the work of one tud_task() call.
 *  - OPT_OS_NONE: tud_task() never blocks (osal_none queue receive ignores
 *    the timeout); tusb_time_millis_api() is provided by usb.c.
 */
#ifndef TUSB_CONFIG_H
#define TUSB_CONFIG_H

#define CFG_TUSB_MCU                 OPT_MCU_STM32F4
#define CFG_TUSB_OS                  OPT_OS_NONE
#define CFG_TUSB_DEBUG               0
#define CFG_TUSB_MEM_SECTION
#define CFG_TUSB_MEM_ALIGN           __attribute__((aligned(4)))

#define CFG_TUD_ENABLED              1
#define CFG_TUD_MAX_SPEED            OPT_MODE_FULL_SPEED
#define CFG_TUSB_RHPORT0_MODE        (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)
#define CFG_TUD_ENDPOINT0_SIZE       64
#define CFG_TUD_VBUS_DETECT_HW       0
#define CFG_TUD_TASK_EVENTS_PER_RUN  2

#define CFG_TUD_CDC                  1
#define CFG_TUD_MSC                  0
#define CFG_TUD_HID                  0
#define CFG_TUD_MIDI                 0
#define CFG_TUD_VENDOR               0
#define CFG_TUD_CDC_RX_BUFSIZE       512
#define CFG_TUD_CDC_TX_BUFSIZE       4096
#define CFG_TUD_CDC_EP_BUFSIZE       64

#endif /* TUSB_CONFIG_H */
