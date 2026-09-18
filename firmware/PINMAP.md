# Hardware Pin Map

LOLIN S3 V1.0.0 / ESP32-S3-WROOM-1 기준 MEIT belt 하드웨어 핀맵입니다.

실제 production GPIO 정의는 `firmware/main/config.h` 및 `firmware/main/main.c`를 기준으로 합니다.

## 1. INMP441 x4 / Dual I2S

4개의 INMP441은 두 개의 I2S peripheral로 수음합니다.

- I2S bus A: MASTER
- I2S bus B: SLAVE
- 두 bus는 동일한 electrical BCLK / WS clock을 사용
- bus A의 clock output과 bus B의 clock input은 jumper로 연결

| Function | GPIO |
|---|---:|
| I2S A BCLK | 5 |
| I2S A WS | 6 |
| I2S A DIN | 7 |
| I2S B BCLK | 16 |
| I2S B WS | 17 |
| I2S B DIN | 15 |

### Clock jumper

```text
GPIO5  ↔ GPIO16    BCLK
GPIO6  ↔ GPIO17    WS
```

GPIO5/6에서 생성된 master clock을 네 개의 INMP441이 공통으로 사용합니다.

GPIO16/17은 I2S bus B가 동일한 clock을 입력받기 위한 slave-side GPIO입니다.

### Microphone channel assignment

| Microphone | I2S bus | Slot | DIN | L/R wiring |
|---|---|---|---:|---|
| FRONT | A | Left | GPIO7 | GND |
| RIGHT | A | Right | GPIO7 | 3.3V |
| BACK | B | Left | GPIO15 | GND |
| LEFT | B | Right | GPIO15 | 3.3V |

Firmware channel order:

```text
0 = FRONT
1 = RIGHT
2 = BACK
3 = LEFT
```

## 2. MPU6050 / GY-521

LOLIN S3의 I2C 핀을 사용합니다.

| Signal | GPIO |
|---|---:|
| SDA | 42 |
| SCL | 41 |

권장 배선:

```text
GY-521 VCC → 3.3V
GY-521 GND → GND
GY-521 SDA → GPIO42
GY-521 SCL → GPIO41
GY-521 AD0 → GND
```

AD0를 GND에 연결하는 경우 기본 I2C address는 `0x68`을 사용합니다.

## 3. microSD

microSD logging 기능을 추가하기 위해 SPI GPIO를 미리 예약합니다.

현재 SD logging 코드는 아직 구현되지 않았습니다.

| Signal | GPIO |
|---|---:|
| SCK | 12 |
| MOSI | 14 |
| MISO | 18 |
| CS | 21 |

```text
microSD SCK  → GPIO12
microSD MOSI → GPIO14
microSD MISO → GPIO18
microSD CS   → GPIO21
```

실제 microSD module의 VCC 전압은 사용 모듈의 회로를 확인한 뒤 결정합니다.

## 4. DRV8833 x4 / ERM motor x8

4개의 DRV8833으로 총 8개의 ERM vibration motor를 제어합니다.

각 motor는 한 방향 구동만 사용하며, 한 input에 PWM을 입력하고 다른 input은 LOW로 고정합니다.

DRV8833의 `SLP`는 3.3V에 연결하여 항상 enable 상태로 사용합니다.

| Motor | Direction | PWM GPIO |
|---:|---|---:|
| 0 | FRONT | 1 |
| 1 | FRONT_RIGHT | 2 |
| 2 | RIGHT | 13 |
| 3 | BACK_RIGHT | 4 |
| 4 | BACK | 8 |
| 5 | BACK_LEFT | 9 |
| 6 | LEFT | 10 |
| 7 | FRONT_LEFT | 11 |

Physical arrangement:

```text
                    Motor 0
                     FRONT

          Motor 7               Motor 1
        FRONT_LEFT            FRONT_RIGHT


      Motor 6                     Motor 2
        LEFT                       RIGHT


          Motor 5               Motor 3
         BACK_LEFT             BACK_RIGHT

                    Motor 4
                     BACK
```

Current firmware motor order:

```c
const int MOTOR_GPIO[8] = {
    1, 2, 13, 4, 8, 9, 10, 11
};
```

Current direction mapping is identity mapping:

```text
direction 0 → motor 0
direction 1 → motor 1
direction 2 → motor 2
direction 3 → motor 3
direction 4 → motor 4
direction 5 → motor 5
direction 6 → motor 6
direction 7 → motor 7
```

## 5. GPIO summary

| GPIO | Assignment |
|---:|---|
| 1 | Motor 0 |
| 2 | Motor 1 |
| 4 | Motor 3 |
| 5 | I2S A BCLK |
| 6 | I2S A WS |
| 7 | I2S A DIN |
| 8 | Motor 4 |
| 9 | Motor 5 |
| 10 | Motor 6 |
| 11 | Motor 7 |
| 12 | microSD SCK |
| 13 | Motor 2 |
| 14 | microSD MOSI |
| 15 | I2S B DIN |
| 16 | I2S B BCLK |
| 17 | I2S B WS |
| 18 | microSD MISO |
| 21 | microSD CS |
| 41 | MPU6050 SCL |
| 42 | MPU6050 SDA |

## 6. Reserved / avoided GPIO

다음 GPIO는 현재 프로젝트에서 사용하지 않습니다.

```text
GPIO0, GPIO3
GPIO19, GPIO20
GPIO35, GPIO36, GPIO37
GPIO38
GPIO43, GPIO44
GPIO45, GPIO46
```

주요 이유:

- GPIO0/3/45/46: boot / strapping 관련
- GPIO19/20: native USB
- GPIO35/36/37: OPI PSRAM 관련
- GPIO38: LOLIN S3 onboard WS2812
- GPIO43/44: UART0

## 7. Hardware validation required

현재 핀맵은 production firmware 구조에 맞춰 배정되어 있지만 다음 항목은 실제 하드웨어에서 확인해야 합니다.

- 4개의 INMP441 정상 수음
- GPIO5 ↔ GPIO16 / GPIO6 ↔ GPIO17 shared-clock 동작
- dual-I2S bus skew 및 reboot 안정성
- MPU6050 I2C 통신
- microSD SPI 통신
- Motor 0~7 실제 물리 방향
- DRV8833 / motor 전원 안정성
- motor vibration이 microphone/TDoA에 미치는 영향
