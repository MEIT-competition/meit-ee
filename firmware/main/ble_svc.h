#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "motor.h"

// Motor-only BLE service. iPhone capture, the stereo direction estimate and
// meit-ai inference all stay on the laptop side; this peripheral receives only
// finished haptic commands.
void ble_svc_init(void);
bool ble_svc_connected(void);

// One accepted CMD packet, already validated.
//
// `motor_mask` selects the motors the whole pattern drives (MOTOR_MASK_LEFT /
// _RIGHT / _BOTH). A STOP command arrives with `motor_mask == 0`,
// `steps == NULL` and `n_steps == 0`.
typedef void (*ble_cmd_cb_t)(uint8_t sequence, uint8_t direction,
                             uint8_t intensity_pct, uint8_t motor_mask,
                             const motor_step_t *steps, int n_steps);
void ble_svc_set_cmd_cb(ble_cmd_cb_t cb);
