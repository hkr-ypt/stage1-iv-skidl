/*
 * usb.c — USB CDC host link of the STM32F411RET6 port (TinyUSB 0.21.0).
 *
 * Facts relied on (firmware_spec.md §4.7, §8.1):
 *  - OTG_FS on PA11/PA12 (AF10, gpio_spi.c), 48 MHz from PLL Q (system.c),
 *    clock RCC_AHB2ENR.OTGFSEN. SystemCoreClock is set before tusb_init().
 *  - TinyUSB 0.21.0 dcd_init() connects (clears DCTL.SDIS) unconditionally:
 *    tud_disconnect() follows tusb_init() at once, and session.c connects
 *    only while PA0 shows VBUS (a self-powered device must not pull D+ up
 *    without VBUS).
 *  - The OTG_FS interrupt only queues events (dcd_int_handler); class
 *    callbacks run in tud_task() from the main loop. No other interrupt
 *    touches OTG registers (ES0287 2.12.1 FIFO corruption).
 *  - Without HSE there is no 48 MHz clock: USB stays off and every host
 *    call is a no-op (F_CLOCK, instrument.c).
 *  - Device mode is forced (GUSBCFG.FDMOD) and confirmed (GINTSTS.CMOD = 0)
 *    before tusb_init(); PA10 (OTG_FS_ID, unconnected) is pulled up so the
 *    core starts as a B-device anyway (gpio_spi.c). A core that does not
 *    reach device mode within 100 ms leaves USB off, as without HSE.
 */
#include "port_stm32.h"

#include "tusb.h"

#define USB_MODE_TIMEOUT_US 100000u   /* 4 x the 25 ms FDMOD settling time */

static bool s_usb_ok;

bool stm32_usb_init(void)
{
    if (!port_hse_ok()) {
        s_usb_ok = false;
        return false;
    }
    RCC->AHB2ENR |= RCC_AHB2ENR_OTGFSEN;
    (void)RCC->AHB2ENR;
    /* Device mode before TinyUSB's first device-register write: dcd_init()
     * writes DCFG (full speed) and DCTL (soft disconnect) before it sets
     * GUSBCFG.FDMOD itself, and a core still in host mode ignores them
     * (GINTSTS.MMIS). Force it here and wait until GINTSTS.CMOD shows device
     * mode (the change takes up to 25 ms, spec §4.7; the force bit persists
     * across the core soft reset in tusb_init). With PA10 pulled up the core
     * already starts as a B-device and the wait ends at once. */
    USB_OTG_FS->GUSBCFG = (USB_OTG_FS->GUSBCFG & ~USB_OTG_GUSBCFG_FHMOD) | USB_OTG_GUSBCFG_FDMOD;
    if (!stm32_wait_reg(&USB_OTG_FS->GINTSTS, USB_OTG_GINTSTS_CMOD, 0u, USB_MODE_TIMEOUT_US)) {
        s_usb_ok = false;
        return false;
    }
    NVIC_SetPriority(OTG_FS_IRQn, PRIO_USB);
    tusb_rhport_init_t init = { .role = TUSB_ROLE_DEVICE, .speed = TUSB_SPEED_FULL };
    s_usb_ok = tusb_init(0, &init)
            && stm32_wait_reg(&USB_OTG_FS->GINTSTS, USB_OTG_GINTSTS_CMOD, 0u, USB_MODE_TIMEOUT_US);
    if (s_usb_ok) {
        tud_disconnect();
    }
    return s_usb_ok;
}

void OTG_FS_IRQHandler(void)
{
    tud_int_handler(0);
}

uint32_t tusb_time_millis_api(void)
{
    return (uint32_t)(port_now_us() / 1000u);
}

void port_usb_service(void)
{
    if (s_usb_ok) {
        tud_task();
    }
}

size_t port_host_read(uint8_t *buf, size_t max)
{
    if (!s_usb_ok || max == 0u || tud_cdc_available() == 0u) {
        return 0u;
    }
    return (size_t)tud_cdc_read(buf, (uint32_t)max);
}

size_t port_host_write(const uint8_t *buf, size_t n)
{
    if (!s_usb_ok) {
        return 0u;
    }
    return (size_t)tud_cdc_write(buf, (uint32_t)n);
}

size_t port_host_write_space(void)
{
    return s_usb_ok ? (size_t)tud_cdc_write_available() : 0u;
}

void port_host_flush(void)
{
    if (s_usb_ok) {
        (void)tud_cdc_write_flush();
    }
}

/* The CDC stream copies from its FIFO into a 64-byte endpoint buffer in
 * thread context (tu_edpt_stream_write_xfer), so the FIFO is never touched by
 * the OTG interrupt and can be cleared here; a packet already in the endpoint
 * still goes out. */
void port_host_discard_tx(void)
{
    if (s_usb_ok) {
        (void)tud_cdc_write_clear();
    }
}

bool port_host_configured(void)
{
    return s_usb_ok && tud_mounted() && !tud_suspended();
}

bool port_host_dtr(void)
{
    return s_usb_ok && tud_cdc_connected();
}

void port_host_connect(bool connect)
{
    if (!s_usb_ok) {
        return;
    }
    if (connect) {
        (void)tud_connect();
    } else {
        (void)tud_disconnect();
    }
}
