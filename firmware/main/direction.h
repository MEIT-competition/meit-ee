#pragma once
#include <stdint.h>

// Compact runtime/vote indices. Never serialize these directly.
typedef enum { DIR_LEFT = 0, DIR_RIGHT, DIR_BACK, DIRECTION_COUNT } direction_index_t;
// Retain the existing AI/BLE numeric meaning for the supported directions.
enum { DIR_WIRE_LEFT = 6, DIR_WIRE_RIGHT = 2, DIR_WIRE_BACK = 4,
       DIR_UNKNOWN = 0xFF }; // invalid/unresolved status, not a fourth direction

static inline uint8_t direction_to_wire(int index)
{
    switch (index) {
    case DIR_LEFT: return DIR_WIRE_LEFT;
    case DIR_RIGHT: return DIR_WIRE_RIGHT;
    case DIR_BACK: return DIR_WIRE_BACK;
    default: return DIR_UNKNOWN;
    }
}

static inline int direction_from_wire(int value)
{
    switch (value) {
    case DIR_WIRE_LEFT: return DIR_LEFT;
    case DIR_WIRE_RIGHT: return DIR_RIGHT;
    case DIR_WIRE_BACK: return DIR_BACK;
    default: return -1;
    }
}

static inline const char *direction_name(int index)
{
    switch (index) {
    case DIR_LEFT: return "LEFT";
    case DIR_RIGHT: return "RIGHT";
    case DIR_BACK: return "BACK";
    default: return "UNKNOWN";
    }
}
