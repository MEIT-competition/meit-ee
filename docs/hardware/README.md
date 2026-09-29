# MEIT current hardware

Current: INMP441 ×2 → single I2S stereo → LEFT / RIGHT / BACK → vibration motor ×2.
Board: LOLIN S3 V1.0.0, ESP32-S3, 16 MB QSPI flash, 8 MB OPI PSRAM.

| Part | Active quantity | Use |
|---|---:|---|
| INMP441 | 2 | Laterally spaced LEFT / RIGHT |
| DRV8833 | 2 | Each board uses A channel only |
| ERM vibration motor | 2 | LEFT / RIGHT |

```text
GPIO5 BCLK → both microphones SCK
GPIO6 WS   → both microphones WS
GPIO7 DIN  ← both microphones SD
LEFT L/R=GND; RIGHT L/R=3.3V

GPIO21 → LEFT driver AIN1;  LEFT AIN2=GND
GPIO13 → RIGHT driver AIN1; RIGHT AIN2=GND
Each motor → its driver's AOUT1/2; SLP=3V3
```

LEFT activates the LEFT motor, RIGHT activates the RIGHT motor, BACK activates both
together. UNKNOWN is an alternating LEFT/RIGHT alert after AI confirms danger.
No dedicated BACK motor is used. GPIO15/16/17 are unused.

Two laterally spaced microphones cannot physically distinguish front/back using
TDoA alone. FRONT is excluded from the operating domain; near-zero reliable
inter-microphone delay is mapped to BACK. This is an operating policy.

## Power and optional peripherals

ESP32 uses USB-C. The previously recorded motor supply is a separate 4×AA pack,
with pack positive to driver VM and common ground with ESP32. Do not connect pack
positive to ESP32 3V3/5V. The latest hardware request did not specify a new rail.
`MOTOR_SUPPLY_MV=4200` remains the old provisional value; confirm cell chemistry,
maximum pack voltage and motor rating before calibrating PWM duty.

IMU SDA/SCL=42/41 and SD SCK/MOSI/MISO=12/14/18 remain reserved and unimplemented.
SD CS is unassigned because the previously reserved GPIO21 is now the verified LEFT
motor input. Assign a non-conflicting CS pin only when SD logging is implemented.
See [PINMAP](../../firmware/PINMAP.md) for pin exclusions and [README](../../README.md)
for build, BLE and bring-up commands.

## Historical drawings

The existing `schematics/01-*` through `schematics/06-*` PNG files depict the
**retired four-microphone / eight-motor design**. They are retained as historical
assets, not current wiring instructions. They are intentionally not embedded as
the current circuit. Optional module drawings 07/08 remain reference material.
`../../mic_bringup_log.txt` is likewise an old capture, not validation of this build.

## Remaining measurements

Verify L/R strap-to-slot mapping, microphone-side clocks/data and worn TDoA
threshold; production-PWM minimum startup duty, VM sag/brownout/driver temperature;
and BLE/AI latency. The direct HIGH/LOW hardware test has already verified LEFT
GPIO21, RIGHT GPIO13 and both motors together with the stated two-DRV8833 wiring;
the production LEDC pattern and full BLE end-to-end path still need board validation.
Host tests/build success are not evidence that these physical checks passed.

INMP441 timing/channel reference: [TDK datasheet](https://product.tdk.com/system/files/dam/doc/product/sw_piezo/mic/mems-mic/data_sheet/inmp441.pdf).
