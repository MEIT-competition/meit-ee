#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "motor.h"

// Motor-only BLE service. iPhone capture/direction/AI remain on the existing
// meit-ios Windows bridge; this peripheral receives only final haptic commands.
void ble_svc_init(void);
bool ble_svc_connected(void);

typedef void (*ble_cmd_cb_t)(uint8_t sequence, uint8_t direction,
                             uint8_t intensity_pct,
                             const motor_step_t *steps, int n_steps);
void ble_svc_set_cmd_cb(ble_cmd_cb_t cb);
