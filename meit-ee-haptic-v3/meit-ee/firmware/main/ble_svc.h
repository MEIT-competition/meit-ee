#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "motor.h"

// Motor-only BLE service. iPhone capture, 4-role direction selection and
// meit-ai inference all stay on the laptop side; this peripheral receives only
// finished haptic commands.
void ble_svc_init(void);
bool ble_svc_connected(void);

// One accepted CMD packet, already validated.
//
// `masks[i]` selects the motors for step `i` (MOTOR_MASK_LEFT / _RIGHT / _BOTH).
// For a CMD v2 packet the handler fills every entry from the direction byte, so
// this callback does not need to know which wire version delivered it.
// `direction` is kept for logging; under v3 the masks are authoritative.
// A STOP command arrives with `masks == NULL`, `steps == NULL`, `n_steps == 0`.
typedef void (*ble_cmd_cb_t)(uint8_t sequence, uint8_t direction,
                             uint8_t intensity_pct, const uint8_t *masks,
                             const motor_step_t *steps, int n_steps);
void ble_svc_set_cmd_cb(ble_cmd_cb_t cb);
