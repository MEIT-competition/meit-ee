# Current hardware — 2-motor belt

This document describes the **active** hardware path only. The old INMP441 / TDoA / 8-motor materials are under `legacy/` and are not part of the runtime build.

## Active signal path

```text
Windows laptop BLE -> ESP32-S3 -> DRV8833 -> LEFT / RIGHT vibration motors
```

The iPhone side and the `meit-ios` laptop bridge perform audio collection, 4-role direction selection and AI inference. The ESP32 does not sample microphones and does not estimate direction; it receives a finished haptic command and plays it.

## GPIO

| Function | GPIO | Current use |
|---|---:|---|
| LEFT motor PWM | 21 | LEFT DRV8833 AIN1 |
| RIGHT motor PWM | 13 | RIGHT DRV8833 AIN1 |
| DRV8833 AIN2 | — | GND for the current one-direction drive scheme |
| DRV8833 nSLEEP | — | 3V3 when `MOTOR_SLEEP_GPIO=-1` |

Both motor-driver grounds and ESP32 ground must be common.

## Direction to motor behavior

| Direction command | LEFT motor | RIGHT motor |
|---|---|---|
| `LEFT` | ON | OFF |
| `FRONT` (v2 `CENTER`) | ON | ON, simultaneously |
| `RIGHT` | OFF | ON |
| `BACK` (CMD v3) | ON first | ON immediately after, no gap |
| `STOP` / unknown | OFF | OFF |

`BACK` is a sweep, not a static combination, so it is the one direction whose
wiring cannot be judged from a per-motor check. `firmware/hardware_tests/motor_self_test`
ends with a real sweep for exactly that reason.

## Electrical note

`firmware/main/config.h` contains the assumed motor supply voltage and nominal motor voltage used to cap PWM duty. These values are a software guard, **not** a substitute for verifying the real battery pack, motor rating, driver wiring, current draw and temperature.

Before increasing intensity, confirm the actual supply and motor specifications on the physical unit.
