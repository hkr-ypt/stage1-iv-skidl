/*
 * model_board.h — analog behavioural model of the Stage 1 board, from the
 * DAC registers to the ADC input pins (blocks/<name>.py at 8a84b55):
 *
 *   PC5 edges -> charge pump DAC_ARM (C48 || R37, tau 47 ms) -> TMUX1101 gate
 *     gate ON at DAC_ARM >= 1.49 V, OFF at <= 0.87 V, holds in between
 *   VREF_DAC = gate ? VREF : 0
 *   V_SWEEP  = 8.0321 * (V_A - V_B/2), V_x = VREF_DAC * code_x / 65536,
 *              clamped to U3B's swing (+-13.8 V)
 *   U5 follower with current limit i_lim (both polarities), only if both
 *     +-15 V rails are up (otherwise U12 is open: no force current)
 *   FORCE+ : R_top = F1 + R30 + U12 ; FORCE- : R_bot = F2 + R10 to AGND
 *   Kelvin attenuators R12/R32, R13/R33 (50.2 k to AGND) on SENSE+/SENSE-
 *   U7: V_SENSE_OUT = VREF + k * (V(SENSE+) - V(SENSE-)) + offset
 *   U6: I_SENSE_OUT = VREF + 24.0056 V/A * I(R10) + offset
 *   ADC pins: AIN0 = I_SENSE_OUT, AIN2 = V_SENSE_OUT, AIN1 = AIN3 = AGND,
 *             AINCOM = VREF
 *
 * The DUT is solved for its operating point by bisection on the DUT voltage
 * (works for any monotonic I(V) DUT). The model is deliberately simple; it
 * exists to exercise the firmware's logic, not to predict analog accuracy.
 */
#ifndef MODEL_BOARD_H
#define MODEL_BOARD_H

#include <stdbool.h>
#include <stdint.h>
#include "model_dac8552.h"

typedef enum {
    DUT_OPEN = 0,
    DUT_RESISTOR,   /* r_ohm                                           */
    DUT_DIODE,      /* is_a, n (ideality), rs_ohm ; anode on FORCE+     */
    DUT_CELL,       /* v_cell, r_ohm (internal) : a battery/PV-like source */
    DUT_CAPACITOR,  /* treated as open at DC (c_f kept for future use)  */
} dut_kind_t;

typedef struct {
    dut_kind_t kind;
    double r_ohm;
    double is_a, n, rs_ohm;
    double v_cell;
    double c_f;
} dut_t;

typedef struct {
    /* configuration (tests may change any of these) */
    double vref;              /* 2.500                                   */
    double sweep_offset_v;    /* residual offset at V_SWEEP              */
    double r_top, r_bot, r_sd;
    double i_lim;             /* current-limit plateau, A                */
    double ina_v_offset_v;    /* U7 output-referred offset               */
    double ina_i_offset_v;    /* U6 output-referred offset               */
    bool   rail_pos, rail_neg;/* +-15 V present                         */
    bool   sense_plus_open, sense_minus_open, force_open;
    double gain_err_v, gain_err_i; /* multiplicative errors (e.g. 0.00168) */
    dut_t  dut;
    /* gate / charge pump state */
    double v_arm;
    bool   gate_on;
    bool   hb_level;
    uint64_t t_last_us;
    uint32_t gate_on_events, gate_off_events;
    /* links */
    dac_model_t *dac;
} board_model_t;

void   board_init(board_model_t *b, dac_model_t *dac);
/* PC5 level at time t (rising edges pump charge). */
void   board_hb(board_model_t *b, bool level, uint64_t t_us);
/* Advance the charge pump decay to time t. */
void   board_advance(board_model_t *b, uint64_t t_us);
double board_vref_dac(const board_model_t *b);
double board_vsweep(const board_model_t *b);
/* Operating point: DUT voltage (V_hi - V_lo), DUT current, shunt current. */
void   board_solve(const board_model_t *b, double *v_hi, double *v_lo,
                   double *i_dut, double *i_shunt, bool *in_limit);
/* adsm_input_fn-compatible callback (ctx = board_model_t *). */
double board_ain(void *ctx, int ain, uint64_t t_us);

#endif /* MODEL_BOARD_H */
