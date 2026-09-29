# Current hardware pin map

LOLIN S3 V1.0.0 / ESP32-S3, ESP-IDF 5.2.5.
Definitions: `main/config.h`; motor GPIO array: `main/motor.c`.

| Function | GPIO | Connection |
|---|---:|---|
| I2S0 master BCLK | 5 | Both INMP441 SCK |
| I2S0 master WS | 6 | Both INMP441 WS |
| I2S0 stereo DIN | 7 | Both INMP441 SD |
| LEFT motor PWM | 13 | LEFT DRV8833 AIN1 |
| RIGHT motor PWM | 1 | RIGHT DRV8833 AIN1 |
| Optional IMU SDA / SCL | 42 / 41 | Reserved, driver not implemented |
| Optional microSD SCK / MOSI / MISO / CS | 12 / 14 / 18 / 21 | Reserved, logging not implemented |

LEFT mic L/R=GND, channel 0 (WS low); RIGHT mic L/R=3V3, channel 1 (WS high).
Single Philips stereo bus, 48 kHz, 32-bit slots. GPIO15/16/17 are unused;
there is no second I2S peripheral or inter-bus clock jumper requirement.

Each DRV8833 uses only channel A. AIN1 gets PWM, AIN2=GND,
AOUT1/2 connect to that motor, SLP=3V3, and grounds are common.
LEFT selects GPIO13; RIGHT selects GPIO1; BACK selects both at the same pattern step.

Reserved/avoided pins remain GPIO0/3/45/46 (strapping), GPIO19/20 (USB),
GPIO35/36/37 (OPI PSRAM), GPIO38 (board WS2812), GPIO43/44 (UART0).
Retired motor pins GPIO2/4/8/9/10/11 are no longer configured by motor_init.

Two laterally spaced microphones resolve left/right/center-axis only.
FRONT is excluded from the operating domain; near-zero delay maps to BACK.
Verify the physical L/R straps, board pin labels, motor positions, supply and
simultaneous motor load on hardware. See `SYNC_CHECK.md` and `../README.md`.
