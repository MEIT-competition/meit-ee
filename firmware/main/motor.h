#pragma once
#include <stdint.h>
#include "config.h"
// 4x DRV8833 -> 8 ERM/LRA motors, one LEDC channel each.

typedef struct { uint16_t on_ms; uint16_t off_ms; } motor_step_t;

void motor_init(void);
void motor_set(int idx, uint8_t duty);            // 0..255
void motor_all_off(void);

// Which motor bit corresponds to a TDoA direction index (0..7).
// DIR_TO_MOTOR is a placeholder identity mapping -- fix once the belt's
// physical motor wiring order is known.
int  motor_bit_for_direction(int dir_index);

// Plays [on,off]*n on every motor set in motor_mask, at the given intensity
// (0..100, matches meit-ai's `intensity` field directly). Interrupts and
// replaces whatever pattern is currently playing (see PROTOCOL.md: only one
// clip is ever in flight, so this is not expected to matter for the MVP).
void motor_play_pattern(uint8_t motor_mask, uint8_t intensity_pct,
                        const motor_step_t *steps, int n_steps);
