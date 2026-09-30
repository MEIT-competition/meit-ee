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
// A CMD packet is a 6-byte header plus 2 bytes per step. The longest pattern in
// use is a siren's three pulses, so 4 leaves headroom and the worst case is 14
// bytes — well inside the 20 payload bytes an ATT Write Request carries on the
// default 23-byte MTU. A haptic alert therefore never depends on MTU
// negotiation succeeding.
// ================================================================
#define PATTERN_MAX_STEPS   4

// ================================================================
// BLE CMD protocol (v2).
//
// One motor mask applies to the whole pattern and is derived from the direction
// byte. That is all the belt needs: iOS reports left/right/center, so there are
// three sensations — left motor, right motor, or both together. There is no rear
// cue, because a stereo microphone pair cannot separate front from back and the
// system has no rear sensor.
// ================================================================
#define CMD_MAGIC     0xA5
#define CMD_VERSION   0x02
#define CMD_HEADER    6

enum {
    CMD_DIR_STOP = 0,
    CMD_DIR_LEFT = 1,
    CMD_DIR_CENTER = 2,   // both motors, simultaneously
    CMD_DIR_RIGHT = 3,
};
#define CMD_DIR_MAX   CMD_DIR_RIGHT

extern const int MOTOR_GPIO[NUM_MOTORS];
