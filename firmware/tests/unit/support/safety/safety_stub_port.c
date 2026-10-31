/*
 * safety_stub_port.c — the port.h calls reached by core/safety.c and
 * drivers/dac8552.c in test_safety: settable flags, inert transports.
 */
#include "port.h"

bool g_stub_stack_ok = true;
bool g_stub_tim2_frozen = false;

bool port_stack_ok(void) { return g_stub_stack_ok; }
bool port_dbg_tim2_frozen(void) { return g_stub_tim2_frozen; }
void port_delay_us(uint32_t us) { (void)us; }
void port_hb_force_low(void) {}
void port_dac_cs(bool asserted) { (void)asserted; }
bool port_dac_write(const uint8_t *tx, size_t n, uint32_t timeout_us)
{
    (void)tx;
    (void)n;
    (void)timeout_us;
    return true;
}
bool port_dac_wait_idle(uint32_t timeout_us) { (void)timeout_us; return true; }
void port_dac_spi_reinit(void) {}
void port_dac_bitbang_begin(void) {}
void port_dac_bitbang_pins(bool sclk, bool mosi) { (void)sclk; (void)mosi; }
void port_dac_bitbang_end(void) {}
