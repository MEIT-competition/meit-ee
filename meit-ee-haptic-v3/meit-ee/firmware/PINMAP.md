# Active firmware pin map

Target: LOLIN S3 V1.0.0 / ESP32-S3, ESP-IDF 5.2.x.

Only the two vibration-motor outputs are active in the current runtime firmware.

| Function | GPIO | Connection |
|---|---:|---|
| LEFT motor PWM | 21 | LEFT DRV8833 AIN1 |
| RIGHT motor PWM | 13 | RIGHT DRV8833 AIN1 |

Current DRV8833 wiring assumption:

- AIN1: ESP32 PWM
- AIN2: GND
- AOUT1/AOUT2: vibration motor
- nSLEEP: 3V3 unless `MOTOR_SLEEP_GPIO` is changed to a valid GPIO
- ESP32 and motor-driver grounds: common

Direction mapping:

| Direction | GPIO21 (LEFT) | GPIO13 (RIGHT) |
|---|---|---|
| `LEFT` | on | off |
| `FRONT` (v2 `CENTER`) | on | on, simultaneously |
| `RIGHT` | off | on |
| `BACK` (CMD v3 only) | on first | on immediately after, no gap |
| `STOP` / invalid | off | off |

`BACK` is a left-to-right sweep rather than a third static combination: with two
actuators, `front` and `back` cannot be separated by position, so they are
separated by order. It needs CMD v3's per-step motor masks, which is why a belt
must be reflashed to feel all four directions. See
[`../docs/HAPTIC_DESIGN.md`](../docs/HAPTIC_DESIGN.md).

The old microphone/I2S/TDoA pin map is archived under `legacy/old_hardware_docs/` and must not be used for the current build.
