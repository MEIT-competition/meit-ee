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
| `CENTER` | on | on, simultaneously |
| `RIGHT` | off | on |
| `STOP` / invalid | off | off |

Three directions, matching what iOS stereo reports. One motor mask applies to a
whole pattern, so no per-step switching is involved. See
[`../docs/HAPTIC_DESIGN.md`](../docs/HAPTIC_DESIGN.md).

The old microphone/I2S/TDoA pin map is archived under `legacy/old_hardware_docs/` and must not be used for the current build.
