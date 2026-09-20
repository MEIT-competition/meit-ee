# MEIT 하드웨어 아키텍처

*방향성 위험음 촉각 알림 시스템*

---

## 1. 시스템 개요

MEIT는 위험음의 발생 방향을 감지해 착용자에게 위치 기반 진동 피드백으로 전달하는 웨어러블 벨트입니다. 방향 추정과 오디오 캡처는 기기 내부에서 처리되며, 소리 분류는 페어링된 노트북에서 수행됩니다.

```text
INMP441 ×4
   │  dual-I2S, 48 kHz
   ▼
ESP32-S3 (LOLIN S3)
   │  TDoA 방향 추정 + 16 kHz PCM16 오디오
   ▼
BLE  ── AUDIO, DIR ──►  Laptop (AI 추론)
   ◄────── CMD ─────────┘
   │
   ▼
DRV8833 ×4
   │
   ▼
ERM Motors ×8
```

노트북 AI는 보드 위에 실장된 부품이 아닙니다. BLE로 연결되는 외부 시스템 블록입니다.

---

## 2. 하드웨어 구성

| 분류 | 부품 | 수량 |
|---|---|---:|
| MCU | LOLIN S3 (ESP32-S3), 16 MB Flash / 8 MB OPI PSRAM | 1 |
| 마이크 | INMP441 (I2S MEMS) | 4 |
| 모터 드라이버 | DRV8833 dual H-bridge (Adafruit breakout) | 4 |
| 햅틱 액추에이터 | ERM coin vibration motor, 3 V 정격 | 8 |
| 배터리 | 1S LiPo, 3.7 V / 2000 mAh | 1 |
| 충전 모듈 | TP4056 USB-C 충전 모듈 | 1 |
| 통신 | ESP32-S3 내장 BLE | — |
| 옵션 모듈 | MPU-6050 (IMU), microSD 모듈 | 2 / 1 |

---

## 3. 마이크 프론트엔드

INMP441 마이크 4개는 두 개의 I2S 페리페럴을 공유합니다. 각 버스는 두 개의 마이크가 하나의 데이터 라인을 공유하며, `L/R` slot 설정으로 채널을 구분합니다.

| 마이크 | 위치 | I2S 버스 | Slot | `L/R` | DIN |
|---|---|---|---|---|---:|
| 0 | FRONT | I2S0 (master) | Left | GND | GPIO7 |
| 1 | RIGHT | I2S0 (master) | Right | 3V3 | GPIO7 |
| 2 | BACK | I2S1 (slave) | Left | GND | GPIO15 |
| 3 | LEFT | I2S1 (slave) | Right | 3V3 | GPIO15 |

```text
                 GPIO5  BCLK (out) ──┬──────────────► 공유 BCLK
                 GPIO6  WS   (out) ──┼─┬────────────► 공유 WS
                 GPIO7  DIN  (in)  ◄─┘ │
                                       │
                 GPIO16 BCLK (in)  ◄───┘  (GPIO5에서 점퍼)
                 GPIO17 WS   (in)  ◄──────(GPIO6에서 점퍼)
                 GPIO15 DIN  (in)  ◄── Pair B 공유 데이터 라인

   Pair A (I2S0, master): FRONT + RIGHT   →  GPIO7
   Pair B (I2S1, slave) : BACK + LEFT     →  GPIO15
```

- TDoA 샘플레이트: **48 kHz**, 버스당 32-bit stereo slot
- 노트북 전송 오디오: **16 kHz mono PCM16**
- 로컬 디커플링 및 SD 라인 pull-down 값은 INMP441 데이터시트 권장값을 따릅니다. 구매한 breakout에 해당 수동소자가 실장되어 있는지 여부는 **모듈 단위 검증 대기** 상태입니다.

---

## 4. 햅틱 출력

DRV8833 dual H-bridge 드라이버 4개가 ERM 모터 8개를 제어합니다 — 드라이버 1개당 모터 2개, 모터 1개당 방향 1개입니다.

```text
GPIO (PWM) ──► AIN1              AOUT1 ──┐
GND        ──► AIN2                      ├─► Motor A
3V3        ──► SLP               AOUT2 ──┘
VBAT       ──► VM
GND        ──► GND               BOUT1 ──┐
GPIO (PWM) ──► BIN1                       ├─► Motor B
GND        ──► BIN2              BOUT2 ──┘

×4 동일 모듈
```

| 방향 인덱스 | 모터 | PWM GPIO |
|---:|---|---:|
| 0 | FRONT | 1 |
| 1 | FRONT_RIGHT | 2 |
| 2 | RIGHT | 13 |
| 3 | BACK_RIGHT | 4 |
| 4 | BACK | 8 |
| 5 | BACK_LEFT | 9 |
| 6 | LEFT | 10 |
| 7 | FRONT_LEFT | 11 |

**현재 동작**

- 유효한 방향(0–7)은 해당 모터만 구동합니다.
- `DIR_UNKNOWN`은 순차 sweep을 트리거합니다: **앞 → 오른쪽 → 뒤 → 왼쪽**, 한 번에 하나씩.
- event lookup이 유효하지 않거나 만료된 경우 모든 모터가 꺼집니다 (fail-safe).

---

## 5. 전원 아키텍처

### 후보 전원 아키텍처

```text
                        1S LiPo 3.7 V 2000 mAh
                                │
                         TP4056 충전 모듈
                                │
                          메인 전원 스위치
                 ┌──────────────┴──────────────┐
                 │                             │
            로직 계통                       모터 계통
        (전원 변환, TBD)              (후보: LiPo 직결)
                 │                             │
            LOLIN S3                      DRV8833 ×4
                 │
               3V3
                 │
           INMP441 ×4

상태: 하드웨어 검증 대기
```

- **로직 rail**: LOLIN S3는 `+5V` 핀으로 5 V를 입력받아 온보드에서 3.3 V로 레귤레이션합니다. 이 rail을 LiPo에서 공급하려면 별도의 변환 단이 필요하며, 모듈 선정 및 정격은 **미정(TBD)** 입니다.
- **모터 rail**: DRV8833의 공급전압 범위는 LiPo 전압을 그대로 수용합니다. 3 V 정격 모터를 이 rail로 구동하는 것은 펌웨어의 PWM duty 제한으로 평균 전압을 제한하는 방식에 의존합니다. 이는 **후보 아키텍처**이며, 모터 전기적 특성에 대한 확인이 필요합니다.
- **공통 GND**: 로직 계통과 모터 계통 전체에 걸쳐 단일 GND net을 유지합니다. 모터 리턴 전류와 마이크/로직 리턴 전류는 가능한 한 물리적으로 분리하여 배선하고, 전원 소스 근처에서 합류시킵니다.
- **로컬 디커플링**: 각 DRV8833 모듈은 자체 supply-side 디커플링을 갖추어 모터 스위칭 노이즈를 해당 위치에서 흡수합니다.
- **USB 주의사항**: 외부 5 V와 USB 전원은 동시에 인가하지 않습니다.
- 충전 중 보호 동작(과방전 / 과전류)과 load-sharing 여부는 사용 중인 TP4056 모듈 실물 기준으로 **검증 대기** 상태입니다.

---

## 6. GPIO / 핀맵

| 기능 | GPIO |
|---|---:|
| Motor 0 (FRONT) | 1 |
| Motor 1 (FRONT_RIGHT) | 2 |
| Motor 2 (RIGHT) | 13 |
| Motor 3 (BACK_RIGHT) | 4 |
| Motor 4 (BACK) | 8 |
| Motor 5 (BACK_LEFT) | 9 |
| Motor 6 (LEFT) | 10 |
| Motor 7 (FRONT_LEFT) | 11 |
| I2S0 BCLK / WS / DIN | 5 / 6 / 7 |
| I2S1 BCLK / WS / DIN | 16 / 17 / 15 |
| I2C SCL / SDA (옵션, IMU) | 41 / 42 |
| microSD SCK / MOSI / MISO / CS (옵션, 예약) | 12 / 14 / 18 / 21 |

---

## 7. 소프트웨어 / 하드웨어 인터페이스

```text
ESP32 → Laptop   :  AUDIO (chunked PCM16), DIR (event_id, direction, confidence, dBFS)
Laptop → ESP32   :  CMD (event_id, intensity, sound class, vibration pattern)
```

- `event_id`는 수신된 `CMD`를 해당 이벤트에 저장된 방향 정보와 연결합니다.
- `event_id`가 유효하지 않거나 만료된 경우 모든 모터가 꺼집니다 (fail-safe).
- 소리 분류 및 진동 결정 로직은 MCU가 아닌 노트북에서 실행됩니다.

---

## 8. 현재 검증 상태

| 서브시스템 | 상태 |
|---|---|
| ESP-IDF 빌드 | 검증됨 |
| GPIO 매핑 | 검토 완료 |
| TDoA synthetic 테스트 | 통과 |
| 모터 host 테스트 | 통과 |
| Dual-I2S 하드웨어 동기화 | 보류 |
| 실제 마이크 캡처 | 보류 |
| 모터 하드웨어 | 보류 |
| BLE 하드웨어 | 보류 |
| 전원 아키텍처 | 검증 진행 중 |
| End-to-end 통합 | 보류 |

---

## 9. 회로도

### System Overview
![System Overview](schematics/system-overview.png)

### Microphone Front End
![Microphone Front End](schematics/microphone-front-end.png)

### Motor Driver
![Motor Driver](schematics/motor-driver.png)

### Power Distribution
![Power Distribution](schematics/power-distribution.png)
