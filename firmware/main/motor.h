#pragma once
#include <stdint.h>
#include "config.h"

typedef struct { uint16_t on_ms; uint16_t off_ms; } motor_step_t;

void motor_init(void);
void motor_set(int idx, uint8_t duty);
void motor_all_off(void);
void motor_stop_pattern(void);
void motor_trigger(int idx, float intensity);
void motor_play_pattern(uint8_t motor_mask, uint8_t intensity_pct,
                        const motor_step_t *steps, int n_steps);

static inline uint8_t motor_mask_for_cmd_direction(uint8_t direction)
{
    switch (direction) {
    case CMD_DIR_LEFT:   return MOTOR_MASK_LEFT;
    case CMD_DIR_CENTER: return MOTOR_MASK_BOTH;
    case CMD_DIR_RIGHT:  return MOTOR_MASK_RIGHT;
    default:             return 0;
    }
}
