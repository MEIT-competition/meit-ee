# meit-ee

MEIT 5회 전공 융합 프로젝트 — 방향성 위험음 촉각 알림 시스템의 전자 파트

INMP441 마이크 4개로 주변 소리를 수음하고 TDoA로 8방향을 추정한 뒤, 노트북 AI와 BLE로 통신해 해당 방향의 진동모터를 구동합니다. AI 추론은 노트북에서 수행하고, ESP32-S3는 마이크 수음·방향 추정·BLE 통신·모터 제어를 담당합니다.

## 구조

```text
meit-ee/
├─ firmware/
│  ├─ CMakeLists.txt
│  ├─ PROTOCOL.md                 # meit-ai ↔ meit-ee BLE 인터페이스 규격
│  ├─ SYNC_CHECK.md               # 듀얼 I2S 동기화 검증 개념/절차
│  ├─ sdkconfig.defaults          # LOLIN S3 / NimBLE 기본 빌드 설정
│  ├─ main/
│  │  ├─ main.c                   # 전체 파이프라인 및 이벤트 상태 관리
│  │  ├─ config.h                 # 샘플레이트·GPIO·threshold 등 주요 설정
│  │  ├─ audio_capture.c/.h       # INMP441 4채널 I2S 수음
│  │  ├─ tdoa.c/.h                # GCC-PHAT 기반 TDoA 및 8방향 추정
│  │  ├─ resample.c/.h            # 48 kHz → 16 kHz AI용 오디오 변환
│  │  ├─ ble_svc.c/.h             # NimBLE 오디오/방향 송신 및 명령 수신
│  │  ├─ motor.c/.h               # DRV8833 + 진동모터 PWM/패턴 제어
│  │  ├─ CMakeLists.txt
│  │  └─ idf_component.yml        # ESP-IDF / esp-dsp 의존성
│  ├─ hardware_tests/
│  │  ├─ dual_i2s_sync/           # 4채널 capture → I2S bus skew 측정용 test app
│  │  └─ motor_self_test/         # BLE 없이 모터 0~7 순차 구동하는 test app
│  └─ tests/
│     ├─ timer_race_model.py      # 모터 패턴 시퀀서 상태 모델
│     ├─ motor_host_test.c        # 실제 motor.c host 회귀 테스트
│     └─ run_motor_host_tests.py  # host test runner
├─ tdoa/
│  ├─ gcc_phat.py                 # Python GCC-PHAT 기준 구현
│  ├─ direction_4mic.py           # 4마이크 → 8방향 계산
│  ├─ calibration.py              # I2S sync offset / tau template 도구 + CLI
│  ├─ parse_dump.py               # serial dump → NumPy (4, N)
│  ├─ test_parse_dump.py          # dump parser 테스트
│  └─ test_synthetic.py           # synthetic 8방향 및 스트레스 테스트
├─ laptop/
│  ├─ __init__.py
│  ├─ protocol.py                 # AUDIO / DIR / CMD packet encode/decode
│  ├─ ble_receiver.py             # BLE scan/connect/notify/write
│  ├─ ai_bridge.py                # BLE audio ↔ meit-ai 연결 인터페이스
│  └─ tests/
│     ├─ test_ble_protocol.py
│     ├─ test_ble_audio_chunks.py
│     ├─ test_ble_cmd_packet.py
│     ├─ test_direction_mapping.py
│     └─ test_resample_reference.py
└─ requirements.txt
```

`tdoa/`는 PC에서 알고리즘과 실측 데이터를 검증하는 Python 도구이고, 실제 ESP32에서 실행되는 production 코드는 `firmware/main/`에 있습니다. `firmware/hardware_tests/`는 production 파이프라인을 바꾸지 않고 실물 bring-up을 하기 위한 별도 테스트 앱입니다.

`laptop/`은 노트북 측 BLE 통신 코드입니다. ESP32에서 전송되는 `DIR`/`AUDIO` notify를 수신하고 AUDIO chunk를 재조립하며, AI 결과를 `CMD` packet으로 변환해 ESP32로 다시 전송합니다. `laptop/tests/`에서는 실제 하드웨어 없이 BLE protocol, AUDIO chunk 재조립, CMD packet, 방향 index 및 resampling 관련 로직을 검증합니다.

## 시스템 구조

```text
INMP441 x4
   ↓
ESP32-S3
- 4채널 수음
- TDoA 8방향 추정
- 48 kHz → 16 kHz 변환
   ↓ BLE
노트북 AI (meit-ai)
- 위험음 분류
- confidence / dBFS 판단
- 진동 세기·패턴 결정
   ↓ BLE
ESP32-S3
   ↓
DRV8833 x4
   ↓
진동모터 x8
```

## 하드웨어

실제 구매한 부품 기준입니다. Core는 메인 파이프라인(수음 → TDoA → BLE → 모터)에 쓰이고, Optional/debug는 구매는 했지만 메인 경로와는 분리된 보조 기능입니다 — 아래 "현재 상태" 표 참고.

### Core

- MCU: LOLIN S3 V1.0.0 / ESP32-S3, 16 MB Flash (Quad SPI) / 8 MB PSRAM (Octal SPI), USB-C, 1개
- 마이크: INMP441 x4 실사용 (+ 예비 2개, 총 6개 구매)
- 모터 드라이버: Adafruit DRV8833 x4
- 진동모터: 3 V ERM coin motor x8 실사용 (+ 예비 2개, 총 10개 구매)
- 전원: TP4056 USB-C LiPo 충전 모듈 + 3.7V 2000mAh LiPo 배터리 (DTP634169), 모터 전원 노이즈 억제용 세라믹 커패시터 0.1uF/50V 다수
- 방향 추정 샘플레이트: 48 kHz
- AI 전송 오디오: 16 kHz mono PCM16
- 통신: ESP32-S3 내장 BLE

### Optional / debug

메인 파이프라인과 분리된 보조 하드웨어입니다. 구매했으므로 코드/PINMAP에서 핀은 예약해 두었지만, 아직 driver/logging 구현체는 없습니다 (아래 "현재 상태" 참고).

- IMU: MPU-6050 / GY-521 x2 (I2C) — 착용체 진동/움직임 측정 또는 보정 실험용
- microSD: SPI microSD 모듈 1개 + microSD 8GB — 디버깅/CSV 로깅용

`firmware/sdkconfig.defaults`에는 16 MB Flash, Octal PSRAM, 80 MHz, NimBLE 설정이 들어 있습니다. 실제 보드에서 16 MB Flash / 8 MB PSRAM이 정상 감지되고 안정적으로 부팅하는지는 실물에서 확인해야 합니다.

현재 GPIO 값은 `firmware/main/config.h`에 있으며, 전체 하드웨어 배선과 GPIO 할당은 `firmware/PINMAP.md`에 정리되어 있습니다. 칩 레벨 GPIO 충돌 검토는 완료했지만, 실제 LOLIN S3 실크스크린/핀아웃과 배선은 조립 전에 다시 대조합니다.

## 방향 인덱스

0을 정면으로 두고 시계방향으로 증가합니다.

| index | 방향 |
|---:|---|
| 0 | 앞 |
| 1 | 오른쪽 앞 |
| 2 | 오른쪽 |
| 3 | 오른쪽 뒤 |
| 4 | 뒤 |
| 5 | 왼쪽 뒤 |
| 6 | 왼쪽 |
| 7 | 왼쪽 앞 |

방향을 안정적으로 판별하지 못하면 `0xFF`(unknown)를 사용합니다.

## Python / host 테스트

의존성 설치:

```bash
pip install -r requirements.txt
```

### Laptop BLE protocol 테스트

노트북 측 BLE protocol과 packet 처리 로직은 실제 ESP32 없이도 테스트할 수 있습니다.

```bash
python -m pytest laptop/tests -q
```

현재 기준:

```text
11 passed
```

검증 항목:

- `DIR` packet decode
- `0xFF` unknown direction 처리
- `AUDIO` chunk 재조립
- `AUDIO` chunk 누락 감지
- chunk index wraparound
- PCM16 little-endian → float32 변환
- `CMD` packet encode/decode
- 진동 pattern 10 ms 단위 검증
- 방향 index 0~7 convention
- 48 kHz → 16 kHz streaming resampling sample count

BLE receiver 자체의 import/CLI 확인:

```bash
python -m laptop.ble_receiver --help
```

실제 ESP32 연결 후:

```bash
python -m laptop.ble_receiver
```

실제 AI 연결 전 BLE 경로만 확인할 경우:

```bash
python -m laptop.ble_receiver --mock-ai
```

`--mock-ai`는 실제 위험음 분류기가 아니라 다음 통신 경로만 검증하기 위한 테스트 모드입니다.

```text
ESP32 AUDIO/DIR
   ↓
Laptop BLE receiver
   ↓
AUDIO chunk reassembly
   ↓
고정 mock AI 결과
   ↓
CMD encode
   ↓
ESP32 CMD write
```

### TDoA synthetic 테스트

```bash
python tdoa/test_synthetic.py
```

현재 기준 synthetic 8방향 테스트는 8/8 통과합니다. 경적·사이렌·잔향·배치 오차 등의 스트레스 케이스도 검토용으로 포함되어 있습니다. 이 결과는 실제 INMP441/착용 상태 정확도를 의미하지 않습니다.

### Dump parser 테스트

```bash
python tdoa/test_parse_dump.py
```

### 모터 host 회귀 테스트

```bash
python firmware/tests/run_motor_host_tests.py --cc gcc
python firmware/tests/timer_race_model.py
```

`motor_host_test.c`는 production `motor.c`를 host stub 환경에서 직접 컴파일해 late/inactive tick, queue full, 연속 PLAY, timer failure 등의 시나리오를 확인합니다. 실물 PWM/DRV8833 동작을 검증하는 테스트는 아닙니다.

## ESP32-S3 펌웨어 빌드

ESP-IDF **5.2.x** 기준입니다. 현재 기준본은 ESP-IDF **5.2.5 전체 build 성공**을 확인했습니다.

처음 target을 설정하는 경우:

```bash
cd firmware
idf.py set-target esp32s3
idf.py build
```

같은 build 환경에서 이후에는 보통 incremental build만 사용합니다.

```bash
idf.py build
```

매 수정마다 `fullclean`이나 `set-target`을 다시 실행할 필요는 없습니다.

현재 기본 partition table은 factory app 1 MB 구성 그대로이며 production 이미지가 들어갈 공간은 남아 있습니다. Flash 전체가 16 MB라고 해서 partition table을 자동으로 16 MB 전부 사용하도록 확장한 상태는 아닙니다.

## Dual-I2S sync bring-up

4개의 INMP441을 실제로 연결한 뒤 두 I2S peripheral 사이의 고정 sample skew를 측정하기 위한 별도 test app이 있습니다.

### 1. test app 빌드/flash

```bash
cd firmware/hardware_tests/dual_i2s_sync
idf.py build
idf.py -p COM_PORT flash monitor
```

Windows PowerShell에서 monitor 출력을 파일로 저장하는 예:

```powershell
idf.py -p COM_PORT flash monitor | Tee-Object capture.txt
```

Test app은 FRONT, RIGHT, BACK, LEFT 순서의 4채널 float sample 8192개를 `MEIT_RAW,...` 형식으로 출력합니다. production `audio_capture.c/.h`를 그대로 재사용하므로 production과 동일한 channel ordering/DC removal 경로를 탑니다.

### 2. serial dump → NumPy

repo root 기준:

```bash
python tdoa/parse_dump.py capture.txt capture.npy
```

결과 shape은 `(4, 8192)`입니다.

### 3. bus skew 계산

```bash
python tdoa/calibration.py capture.npy
```

실제 sync 확인에서는 네 마이크를 최대한 가깝게 묶은 상태로 impulse를 수음하고, 전원을 여러 번 재인가하여 offset이 부팅마다 안정적인지 확인합니다. 측정 결과가 불안정하면 production TDoA 값부터 임의로 보정하지 말고 I2S 동기 구조를 먼저 재검토합니다.

## Motor self-test

BLE/AI 없이 DRV8833과 진동모터를 순서대로 확인하는 별도 test app입니다.

```bash
cd firmware/hardware_tests/motor_self_test
idf.py build
idf.py -p COM_PORT flash monitor
```

기본값:

- intensity: 15%
- ON: 700 ms
- motor 사이 OFF: 500 ms
- motor 0 → 7 순차 구동
- 한 번에 하나만 구동
- 종료 시 `motor_all_off()`

강도와 시간은 `idf.py menuconfig`의 `MEIT motor self-test` 메뉴에서 조정할 수 있습니다.

이 테스트는 실제 GPIO→모터 위치, 최소 기동 intensity, VM 전압 강하, ESP32 reset/brownout, DRV8833 온도 등을 확인하기 위한 bring-up 도구입니다.

## BLE 인터페이스

전자 파트와 AI 파트 사이에는 세 종류의 메시지를 사용합니다.

- `AUDIO`: ESP32-S3 → 노트북, 16 kHz mono PCM16 오디오 chunk
- `DIR`: ESP32-S3 → 노트북, event_id / 8방향 / TDoA confidence / dBFS
- `CMD`: 노트북 → ESP32-S3, event_id / intensity / class / vibration pattern

정확한 byte layout은 `firmware/PROTOCOL.md`를 기준으로 합니다.

### Laptop BLE code

노트북 측 BLE 코드는 `laptop/`에 있습니다.

`laptop/protocol.py`

- `DIR` packet decode
- `AUDIO` chunk decode / reassembly
- PCM16 little-endian → float32 변환
- `CMD` packet encode/decode
- direction index / sound class mapping

`laptop/ble_receiver.py`

- `MEIT-BELT` scan
- ESP32 BLE 연결
- GATT service / characteristic 확인
- `DIR` notify subscribe
- `AUDIO` notify subscribe
- event_id별 AUDIO chunk 재조립
- chunk 누락 감지
- PCM16 audio array 변환
- AI bridge 호출
- `CMD` characteristic write

`laptop/ai_bridge.py`

- BLE receiver와 meit-ai live inference 사이의 연결 인터페이스
- `run_mock_ai()`는 BLE 통합 테스트용 고정 결과
- 실제 AI decision path는 아직 미연결
- AI 측 `classify_clip()` / `judge()` live-path 결정 후 `run_live_ai()`에 연결

현재 laptop-side BLE protocol/unit test는 실제 하드웨어 없이 **11개 모두 통과**했습니다.

BLE production 코드는 현재 baseline이 구현되어 있지만, 실제 다음 항목은 실물에서 확인해야 합니다.

- advertising / scan
- 실제 connection
- AUDIO / DIR / CMD characteristic discovery
- notify subscribe
- negotiated ATT MTU
- AUDIO throughput
- chunk loss
- CMD write callback
- end-to-end latency

BLE 관련 코드를 수정한 뒤에는 production `idf.py build`를 다시 확인합니다.

## 주요 설정값

실제 마이크와 벨트 조립 후 실측 조정이 필요한 값입니다.

| 위치 | 값 | 설명 |
|---|---:|---|
| `config.h` | `TDOA_FS_HZ = 48000` | TDoA용 샘플레이트 |
| `config.h` | `AI_FS_HZ = 16000` | AI 전송용 샘플레이트 |
| `config.h` | `FRAME_LEN = 1024` | TDoA/FFT 처리 프레임 |
| `config.h` | `MIC_RADIUS_M = 0.08` | 임시 마이크 반경, 실제 벨트 실측 필요 |
| `config.h` | `MIN_CONFIDENCE = 0.15` | 임시 TDoA confidence threshold |
| `config.h` | `RMS_GATE_DBFS = -60` | BLE 트래픽 감소용 MCU pre-gate |
| `config.h` | `CLIP_FRAMES = 24` | 이벤트당 약 0.5초 오디오 |
| `config.h` | `TDOA_VOTE_FRAMES = 6` | 방향 voting 프레임 수 |
| `config.h` | `MOTOR_PWM_FREQ_HZ = 20000` | ERM PWM 초기값. 실물에서 진동/소음/저 duty 기동성 비교 필요 |
| `config.h` | `MOTOR_SLEEP_GPIO = -1` | Adafruit DRV8833 SLP를 3V3에 strap, 펌웨어 미제어 |
| `config.h` | `MOTOR_SUPPLY_MV = 4200` | raw LiPo 기준 worst-case motor rail 가정. 실제 VM 경로 확인 후 판단 |
| `config.h` | `MOTOR_RATED_MV = 3000` | coin ERM 정격 3 V |
| `config.h` | `MOTOR_DUTY_CAP` | 기본 182/255 ≈ 71%. 보수적 초기 duty 상한, 실물 검증 필요 |

## 현재 상태

| 항목 | 상태 |
|---|---|
| Python GCC-PHAT / 8방향 계산 | 구현 |
| synthetic 8방향 테스트 | 8/8 통과 |
| ESP32-S3 production firmware | ESP-IDF 5.2.5 build 통과 |
| LOLIN S3 16 MB Flash / 8 MB OPI PSRAM build 설정 | 적용 / 실물 감지 확인 필요 |
| 48 kHz → 16 kHz resampling | 구현 |
| BLE AUDIO / DIR / CMD baseline | 구현 / 실물 검증 필요 |
| Laptop BLE protocol encode/decode | 구현 |
| Laptop BLE receiver | 구현 / 실물 ESP32 연결 필요 |
| Laptop BLE unit test | 11 tests 통과 |
| Live AI bridge | 인터페이스 구현 / 실제 AI 연결 필요 |
| DRV8833 진동 패턴 sequencer | 구현 / host 회귀 테스트 통과 / 실물 미검증 |
| dual-I2S sync test app | 구현 / build 통과 / 실물 측정 필요 |
| motor self-test app | 구현 / build 통과 / 실물 측정 필요 |
| serial dump parser / calibration CLI | 구현 / parser 테스트 통과 |
| 실제 INMP441 4채널 수음 | 실물 검증 필요 |
| 실제 dual-I2S sample sync | 실물 검증 필요 |
| 실제 8방향 정확도 | 실물 검증 필요 |
| BLE 실제 throughput / MTU / loss | 실물 검증 필요 |
| meit-ai live inference 연동 | AI live path 확정 후 통합 필요 |
| 전체 end-to-end | 실물 통합 필요 |
| Hardware pin map | `firmware/PINMAP.md` 작성 / 실물 배선 검증 필요 |
| MPU6050 (optional/debug) | GPIO 예약(I2C 41/42)만 되어 있음 / driver 미구현 |
| microSD logging (optional/debug) | GPIO 예약(SPI 12/14/18/21)만 되어 있음 / logging 코드 미구현 |

## 실물 도착 후 bring-up 순서

1. **ESP32-S3 단독 부팅/flash** — Flash/PSRAM 감지, 로그, reset 여부 확인

2. **INMP441 2개 → 4개 수음** — channel ordering, L/R slot, short read 여부 확인

3. **dual-I2S sync 측정** — `hardware_tests/dual_i2s_sync` + `parse_dump.py` + `calibration.py`

4. **실제 TDoA 8방향 측정** — `MIC_RADIUS_M`, channel 위치, confidence 분포 실측

5. **DRV8833 + 모터 1개 → 8개** — `hardware_tests/motor_self_test`, 최소 기동 intensity/전원 안정성 확인

6. **BLE 실측** — `firmware/main/ble_svc.c`는 이미 production build에 merge/포함되어 있습니다 (build만 통과, 실물 미검증 상태). 남은 작업은 코드 병합이 아니라 advertising, connection, MTU, AUDIO 전송 시간, chunk loss 등 실측입니다.
   - 노트북 측은 먼저 `python -m laptop.ble_receiver --mock-ai`로 AUDIO/DIR 수신 → chunk 재조립 → CMD write 경로를 검증합니다.

7. **meit-ai 통합** — AUDIO/DIR → AI 판단 → CMD → 해당 방향 진동 end-to-end 테스트

실물에서 문제가 확인되기 전에는 TDoA/resampler/motor production 구조를 추측으로 크게 변경하지 않는 것을 원칙으로 합니다.
