#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "config.h"
#include "motor.h"

// BLE CMD packet parsing, kept separate from the NimBLE GATT plumbing.
//
// This is the belt's trust boundary: every byte here arrives from off-device and
// decides what the motors do. Isolating it means it can be compiled and tested
// on a host with no ESP-IDF, board or Bluetooth stack — see
// firmware/tests/run_cmd_parse_tests.py — so malformed-input handling is
// verified on every commit rather than only on the bench.

typedef struct {
    uint8_t version;     // CMD_VERSION_V2 or CMD_VERSION_V3
    uint8_t sequence;    // trace value, echoed to logs only
    uint8_t direction;   // CMD_DIR_*; informational under v3
    uint8_t intensity;   // 0..100 percent; 0 only for STOP
    int n_steps;         // 0 only for STOP
    uint8_t masks[PATTERN_MAX_STEPS];    // motors per step, already expanded
    motor_step_t steps[PATTERN_MAX_STEPS];
} cmd_packet_t;

typedef enum {
    CMD_PARSE_OK = 0,
    CMD_PARSE_BAD_LENGTH,  // maps to BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN
    CMD_PARSE_BAD_FORMAT,  // maps to BLE_ATT_ERR_UNLIKELY
} cmd_parse_result_t;

// Validate `data` and fill `out`. On any non-OK result `out` must be ignored.
//
// A CMD v2 packet is expanded into per-step masks here, so callers have exactly
// one representation to handle regardless of which wire version arrived.
cmd_parse_result_t cmd_parse(const uint8_t *data, uint16_t len, cmd_packet_t *out);

// True when the packet asks for all motors off rather than for a pattern.
static inline bool cmd_is_stop(const cmd_packet_t *packet)
{
    return packet->direction == CMD_DIR_STOP;
}
