#pragma once

#include "driver/gpio.h"
#include "driver/ledc.h"

// ================================================================
// Current hardware: TWO vibration motors only.
// LEFT  DRV8833 AIN1 -> GPIO21
// RIGHT DRV8833 AIN1 -> GPIO13
// Each motor's other DRV8833 input is held LOW. nSLEEP is HIGH.
// ================================================================
enum { MOTOR_LEFT = 0, MOTOR_RIGHT = 1, NUM_MOTORS = 2 };
#define MOTOR_LEFT_GPIO   GPIO_NUM_21
#define MOTOR_RIGHT_GPIO  GPIO_NUM_13
#define MOTOR_MASK_LEFT   (1u << MOTOR_LEFT)
#define MOTOR_MASK_RIGHT  (1u << MOTOR_RIGHT)
#define MOTOR_MASK_BOTH   (MOTOR_MASK_LEFT | MOTOR_MASK_RIGHT)
#define MOTOR_SLEEP_GPIO  (-1)

// Four AA cells can be about 6.4 V when fresh if alkaline. Keep the 3 V
// vibration motors below their nominal average drive by capping PWM duty.
// If the actual cells/motors differ, update these two values from labels/
// datasheets before increasing intensity.
#define MOTOR_SUPPLY_MV   6400
#define MOTOR_RATED_MV    3000
#define MOTOR_DUTY_MAX    255
#define MOTOR_DUTY_CAP_RAW ((MOTOR_DUTY_MAX * MOTOR_RATED_MV) / MOTOR_SUPPLY_MV)
#define MOTOR_DUTY_CAP     (MOTOR_DUTY_CAP_RAW > MOTOR_DUTY_MAX ? MOTOR_DUTY_MAX : MOTOR_DUTY_CAP_RAW)
_Static_assert(MOTOR_SUPPLY_MV > 0, "MOTOR_SUPPLY_MV must be > 0");
_Static_assert(MOTOR_RATED_MV > 0, "MOTOR_RATED_MV must be > 0");
_Static_assert(MOTOR_DUTY_CAP > 0, "MOTOR_DUTY_CAP must be > 0");
#define MOTOR_PWM_FREQ_HZ 20000
#define MOTOR_PWM_RES     LEDC_TIMER_8_BIT

// ================================================================
// Pattern budget.
//
// A CMD v3 packet is an 8-byte header plus 2 bytes per step, so 6 steps is
// exactly 20 bytes: the largest payload an ATT Write Request carries on the
// default 23-byte MTU. Staying inside that means a haptic alert never depends
// on MTU negotiation succeeding.
//
// 6 is also what the longest pattern needs: a BACK sweep spends two steps per
// pulse and the longest class (siren) is three pulses.
// ================================================================
#define PATTERN_MAX_STEPS   6
#define PATTERN_MAX_PAIRS_V2 4   // CMD v2's older, smaller limit

// ================================================================
// BLE CMD protocol.
//
// v2: one motor mask for the whole pattern, derived from the direction byte.
//     Directions STOP/LEFT/CENTER/RIGHT only.
// v3: a motor mask *per step*, which is what makes BACK (a left-to-right
//     sweep) distinguishable from FRONT (both motors together) on a belt that
//     has only two actuators. The direction byte becomes informational.
//
// Both are accepted, so a laptop running the v2 fallback still drives a belt
// flashed with this firmware.
// ================================================================
#define CMD_MAGIC       0xA5
#define CMD_VERSION_V2  0x02
#define CMD_VERSION_V3  0x03

#define CMD_HEADER_V2   6
#define CMD_HEADER_V3   8

enum {
    CMD_DIR_STOP = 0,
    CMD_DIR_LEFT = 1,
    CMD_DIR_FRONT = 2,   // v2 called this CENTER: both motors, simultaneously
    CMD_DIR_RIGHT = 3,
    CMD_DIR_BACK = 4,    // v3 only: left motor then right motor, no gap
};
#define CMD_DIR_CENTER  CMD_DIR_FRONT
#define CMD_DIR_MAX     CMD_DIR_BACK

extern const int MOTOR_GPIO[NUM_MOTORS];
