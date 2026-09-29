# meit-ee

MEIT 방향성 위험음 촉각 알림 시스템의 **EE / wearable output** 저장소입니다.

현재 버전은 ESP32가 마이크를 직접 읽어 방향을 계산하지 않습니다. iPhone과 기존 `meit-ios` Windows bridge가 방향 및 AI 결과를 결정하고, 이 저장소의 laptop bridge가 최종 방향을 BLE로 ESP32에 전달합니다.

## Current architecture

```text
iPhone(s)
   -> Wi-Fi / HTTP
meit-ios Windows bridge + existing meit-ai
   -> GET /auto/status
meit-ee laptop/ios_motor_bridge.py
   -> BLE CMD v2
ESP32-S3 (MEIT-BELT)
   -> LEFT motor / RIGHT motor
```

**iOS source는 수정하지 않습니다.** 공개 `meit-ios` `main`의 기존 Windows bridge interface를 소비하는 구조입니다.

Reference: https://github.com/MEIT-competition/meit-ios

## Direction behavior

| Final direction | LEFT motor | RIGHT motor |
|---|---|---|
| `LEFT` | vibration | off |
| `CENTER` | vibration | vibration |
| `RIGHT` | off | vibration |
| unknown / unavailable | off | off |

현재 iOS 호환을 위해 `front`와 `back`은 EE에서 `CENTER`로 정규화합니다. iOS가 이후 `left/center/right`를 직접 보내도록 업데이트되어도 EE protocol이나 ESP32 firmware는 그대로 사용할 수 있습니다.

## Active repository layout

```text
firmware/
  main/
    main.c            # final direction command -> motor mask
    ble_svc.c/.h      # motor-only BLE GATT server
    motor.c/.h        # 2-motor PWM + vibration pattern sequencer
    config.h          # GPIO / PWM / protocol constants
  hardware_tests/
    motor_self_test/  # physical LEFT -> RIGHT -> CENTER check
  PINMAP.md
  PROTOCOL.md

laptop/
  protocol.py             # CMD v2 encode/decode + direction normalization
  ios_motor_bridge.py     # meit-ios /auto/status -> BLE
  send_motor_test.py      # direct LEFT/CENTER/RIGHT/STOP check
  test_ios_motor_bridge.py

docs/
  ARCHITECTURE.md
  IOS_INTEGRATION.md
  MIGRATION_2026-09-30.md
  hardware/README.md

legacy/                    # previous microphone/TDoA/audio-upload implementation
CHANGELOG.md
GIT_PUSH_GUIDE.md
VERSION
```

Anything under `legacy/` is reference-only and is not part of the active build.

## 1. Python setup and tests

From repository root:

```powershell
python -m pip install -r requirements.txt
python -m unittest laptop.test_ios_motor_bridge -v
python -m compileall -q laptop
```

## 2. Build and flash ESP32

Required: ESP-IDF 5.2.x, target `esp32s3`.

```powershell
cd firmware
idf.py set-target esp32s3
idf.py build
idf.py -p COMx flash monitor
```

Expected boot log:

```text
ready: iOS/AI -> laptop -> BLE -> LEFT/CENTER/RIGHT motors
```

The active firmware component builds only:

```text
main.c
ble_svc.c
motor.c
```

## 3. Direct motor test first

Before involving iOS or AI:

```powershell
python -m laptop.send_motor_test left
python -m laptop.send_motor_test center
python -m laptop.send_motor_test right
python -m laptop.send_motor_test stop
```

Expected:

```text
left   -> LEFT motor only
center -> both motors
right  -> RIGHT motor only
stop   -> both motors off
```

## 4. Run meit-ios Windows bridge

Run the existing `meit-ios/bridge/server.py` exactly as documented in the iOS repository and enable Auto detection.

Verify locally:

```text
http://127.0.0.1:8765/auto/status
```

The current public `meit-ios` bridge exposes this endpoint and retains the latest event in `last_event`.

## 5. Run EE iOS-to-motor bridge

From the `meit-ee` root:

```powershell
python -m laptop.ios_motor_bridge
```

Optional settings:

```powershell
python -m laptop.ios_motor_bridge   --server http://127.0.0.1:8765   --intensity 75   --poll-ms 100
```

The process:

1. scans/reconnects to `MEIT-BELT`,
2. polls `/auto/status`,
3. accepts only new completed dangerous events,
4. normalizes direction,
5. sends one BLE haptic command,
6. records the event as delivered only after successful GATT write.

This prevents the same retained `/auto/status` event from vibrating repeatedly while still allowing retry when a BLE write actually fails.

## BLE contract

Device name: `MEIT-BELT`

Service UUID: `01000000-1d9e-218f-9a4b-9c4e302a9d11`

Command characteristic: `04000000-1d9e-218f-9a4b-9c4e302a9d11`

See [`firmware/PROTOCOL.md`](firmware/PROTOCOL.md) for CMD v2 byte layout.

## Hardware

Current motor GPIO:

```text
LEFT   = GPIO21
RIGHT  = GPIO13
CENTER = both
```

See [`firmware/PINMAP.md`](firmware/PINMAP.md) and [`docs/hardware/README.md`](docs/hardware/README.md).

Before increasing vibration intensity, verify the real motor supply voltage, motor rating, DRV8833 wiring, common ground, current draw and temperature. The firmware PWM cap is only a software limit.

## Change history

- Current migration details: [`docs/MIGRATION_2026-09-30.md`](docs/MIGRATION_2026-09-30.md)
- Project-level changelog: [`CHANGELOG.md`](CHANGELOG.md)
- Git branch/commit/push instructions: [`GIT_PUSH_GUIDE.md`](GIT_PUSH_GUIDE.md)
