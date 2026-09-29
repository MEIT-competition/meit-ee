#pragma once
#include <stdint.h>
#include "config.h"

// Two DRV8833 A channels -> LEFT and RIGHT vibration motors, one LEDC channel per motor.

typedef struct { uint16_t on_ms; uint16_t off_ms; } motor_step_t;

void motor_init(void);

// Raw duty uses the 0..MOTOR_DUTY_MAX scale and is capped internally at
// MOTOR_DUTY_CAP. The cap is a conservative bring-up limit, not a hardware
// safety guarantee; validate the real motor supply, current and temperature.
void motor_set(int idx, uint8_t duty);
void motor_all_off(void);

// Normalized intensity. 1.0f maps to MOTOR_DUTY_CAP.
void motor_trigger(int idx, float intensity);

// Argument is a BLE wire direction, not a compact vote index.
static inline uint8_t motor_mask_for_direction(int wire_dir)
{
    switch (wire_dir) {
    case DIR_WIRE_LEFT: return MOTOR_MASK_LEFT;
    case DIR_WIRE_RIGHT: return MOTOR_MASK_RIGHT;
    case DIR_WIRE_BACK: return MOTOR_MASK_BOTH;
    default: return 0;
    }
}

// Plays [on_ms, off_ms] steps on all motors selected by motor_mask.
// intensity_pct is 0..100. A new pattern replaces the active pattern.
void motor_play_pattern(uint8_t motor_mask, uint8_t intensity_pct,
                        const motor_step_t *steps, int n_steps);

// Unresolved status: LEFT then RIGHT, one at a time (distinct from BACK).
void motor_play_unknown_pattern(uint8_t intensity_pct);
