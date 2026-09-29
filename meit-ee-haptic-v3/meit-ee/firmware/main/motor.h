#pragma once
#include <stdint.h>
#include "config.h"

typedef struct { uint16_t on_ms; uint16_t off_ms; } motor_step_t;

void motor_init(void);
void motor_set(int idx, uint8_t duty);
void motor_all_off(void);
void motor_stop_pattern(void);
void motor_trigger(int idx, float intensity);

// Play a pattern where every step drives the same motors. Used by the CMD v2
// path, where the wire format carries one mask for the whole pattern.
void motor_play_pattern(uint8_t motor_mask, uint8_t intensity_pct,
                        const motor_step_t *steps, int n_steps);

// Play a pattern where each step drives its own motors. This is what makes a
// BACK sweep possible: step 0 is the left motor, step 1 the right, with no gap
// between them, so one pulse is felt travelling across the body. `masks` must
// hold `n_steps` entries.
void motor_play_masked_pattern(const uint8_t *masks, uint8_t intensity_pct,
                               const motor_step_t *steps, int n_steps);

// Mask for a CMD v2 direction byte. v3 carries explicit per-step masks and does
// not use this.
static inline uint8_t motor_mask_for_cmd_direction(uint8_t direction)
{
    switch (direction) {
    case CMD_DIR_LEFT:   return MOTOR_MASK_LEFT;
    case CMD_DIR_FRONT:  return MOTOR_MASK_BOTH;
    case CMD_DIR_RIGHT:  return MOTOR_MASK_RIGHT;
    default:             return 0;
    }
}
