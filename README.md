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
│     ├─ timer_race_model.py               # 모터 패턴 시퀀서 상태 모델
│     ├─ motor_host_test.c                 # 실제 motor.c host 회귀 테스트
│     ├─ run_motor_host_tests.py           # motor host test runner
│     ├─ event_direction_host_test.c       # event_id → motor mask fail-safe 테스트
│     └─ run_event_direction_host_tests.py # event-direction host test runner
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
- 전원: 현재 실물 bring-up에서는 **4×AA 배터리팩을 모터 전원용으로 사용**하고 ESP32-S3는 USB-C로 별도 전원 공급. 배터리팩의 `+`는 DRV8833 x4의 VM 공통선, `-`는 공통 GND에 연결하며 ESP32 GND도 같은 GND를 공유합니다. 기존 3.7 V 2000 mAh LiPo + TP4056 구성은 현재 조립본에서는 사용하지 않습니다.
- 방향 추정 샘플레이트: 48 kHz
- AI 전송 오디오: 16 kHz mono PCM16
- 통신: ESP32-S3 내장 BLE

### Optional / debug

메인 파이프라인과 분리된 보조 하드웨어입니다. 구매했으므로 코드/PINMAP에서 핀은 예약해 두었지만, 아직 driver/logging 구현체는 없습니다 (아래 "현재 상태" 참고).

- IMU: MPU-6050 / GY-521 x2 (I2C) — 착용체 진동/움직임 측정 또는 보정 실험용
- microSD: SPI microSD 모듈 1개 + microSD 8GB — 디버깅/CSV 로깅용

`firmware/sdkconfig.defaults`에는 16 MB Flash, Octal PSRAM, 80 MHz, NimBLE 설정이 들어 있습니다. 최신 로컬 빌드에서는 ESP-IDF 5.2.5 / `esp32s3` target으로 전체 build가 성공했고, flash args에서 16 MB Flash 설정과 `sdkconfig`에서 Octal PSRAM 80 MHz 설정을 확인했습니다. 다만 실제 8 MB PSRAM 용량 감지와 안정적인 부팅은 보드 도착 후 실물에서 확인해야 합니다.

현재 GPIO 값은 `firmware/main/config.h`에 있으며, 전체 하드웨어 배선과 GPIO 할당은 `firmware/PINMAP.md`에 정리되어 있습니다. 칩 레벨 GPIO 충돌 검토는 완료했지만, 실제 LOLIN S3 실크스크린/핀아웃과 배선은 조립 전에 다시 대조합니다.

### 전원 설계 상태

현재 실물 bring-up에서는 **ESP32와 모터 전원을 분리**해서 사용합니다.

```text
Laptop USB-C
    │
    └──► LOLIN S3 / ESP32-S3
             │
             ├── 3V3 ──► INMP441 x4
             └── GND ─────────────┐
                                  │ common GND
4×AA battery pack                 │
    ├── (+) ──► DRV8833 x4 VM     │
    └── (-) ──────────────────────┘

ESP32 GPIO ──► DRV8833 AIN/BIN
ESP32 3V3 ──► DRV8833 SLP x4
```

- **현재 배터리 변경**: 기존 1S LiPo + TP4056 후보 구조 대신, 조립/bring-up 단계에서는 4×AA 배터리팩을 DRV8833 motor rail에 사용합니다.
- **ESP32 전원**: ESP32-S3는 USB-C로 전원을 공급하며 배터리 `+`를 ESP32의 3V3/5V에 연결하지 않습니다. ESP32와 motor rail은 **GND만 공통**으로 사용합니다.
- **DRV8833**: 4개 드라이버의 VM은 하나의 motor supply node로 묶고, GND도 공통으로 묶습니다. SLP는 3V3에 strap합니다.
- **모터 실물 검증 전**: 회로 배선은 완료했지만 모터 8개 실제 구동은 아직 확인하지 않았습니다.
- **중요 — firmware 전원값 미확정**: 현재 `config.h`의 `MOTOR_SUPPLY_MV = 4200` / duty cap은 기존 LiPo 가정값입니다. AA 셀 종류와 실제 pack voltage를 확인하기 전에는 이 값을 최종값으로 보지 않으며, motor self-test 전에 반드시 맞춰야 합니다.
- **전압 실측 미완료**: 현재 멀티미터가 없어 pack voltage, motor rail sag, driver 발열/brownout은 아직 측정하지 못했습니다.

### 회로 설계 요약

현재 회로는 **4-mic dual-I2S front end / ESP32-S3 controller / DRV8833 x4 motor stage / power distribution**의 네 블록으로 정리되어 있습니다.

#### Microphone front end

| 기능 | 연결 |
|---|---|
| Shared BCLK | GPIO5 → INMP441 x4 + GPIO16 |
| Shared WS | GPIO6 → INMP441 x4 + GPIO17 |
| Pair A data | FRONT + RIGHT SD → GPIO7 |
| Pair B data | BACK + LEFT SD → GPIO15 |
| FRONT / BACK `L/R` | GND (Left slot) |
| RIGHT / LEFT `L/R` | 3V3 (Right slot) |
| Mic supply | 3V3 / common GND |

I2S0가 master로 GPIO5(BCLK) / GPIO6(WS)을 생성합니다. 초기 slave-clock 구조에서는 I2S1이 timeout 되었고, 현재 firmware는 **GPIO Matrix를 사용해 GPIO5/6의 clock을 I2S1 입력으로 내부 loopback**하는 방식으로 수정했습니다. 이 수정 후 I2S0/I2S1 양쪽 DMA read는 정상 동작합니다. 따라서 현재 firmware는 I2S1 clock 수신을 위해 GPIO16/17 물리 점퍼에 의존하지 않습니다.

다만 실제 INMP441 4개를 연결한 수음은 아직 해결되지 않았습니다. 48 kHz와 16 kHz에서 모두 실제 mic RAW가 `0x00000000`으로 유지되었고, 내부 constant-one/constant-zero 주입 테스트는 두 I2S 모두 정상 통과했습니다. 즉 ESP32 내부 `GPIO Matrix → I2S → DMA` 경로는 확인되었지만, 실제 mic SD 출력은 아직 관측되지 않았습니다. 전원/clock/SD 외부 경로는 측정 장비가 없어 추가 확인이 필요합니다.

#### Motor stage

DRV8833 한 개가 모터 두 개를 담당하며 총 4개를 사용합니다. 각 motor channel은 한 input에 PWM을 넣고 반대 input은 GND에 고정합니다. `SLP`는 3V3에 strap합니다.

| Driver | Motor A | Motor B |
|---|---|---|
| U1 | FRONT / GPIO1 | FRONT_RIGHT / GPIO2 |
| U2 | RIGHT / GPIO13 | BACK_RIGHT / GPIO4 |
| U3 | BACK / GPIO8 | BACK_LEFT / GPIO9 |
| U4 | LEFT / GPIO10 | FRONT_LEFT / GPIO11 |

정상 direction 0~7은 해당 motor 하나만 구동하고, 실제로 기록된 `DIR_UNKNOWN`은 four-cardinal sweep을 사용합니다. event_id lookup 실패/stale 또는 invalid direction은 motors OFF fail-safe로 처리합니다.

현재 실물에서는 DRV8833 x4 + ERM motor x8 배선까지 완료했으며, **실제 motor self-test는 아직 실행하지 않았습니다.** 따라서 GPIO-to-motor 위치, 기동 duty, 진동 세기, driver 발열, battery rail 안정성은 모두 미검증 상태입니다.

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

방향을 안정적으로 판별하지 못하면 wire value로 `0xFF`(unknown)를 사용합니다. 이 경우 모터 8개를 동시에 켜지 않고 **앞 → 오른쪽 → 뒤 → 왼쪽(0 → 2 → 4 → 6)** cardinal motor를 하나씩 순차 진동시키는 전용 unknown sweep을 사용합니다. 현재 설정은 motor당 **80 ms ON**, cardinal motor 사이 **40 ms OFF**입니다. AI가 보낸 intensity는 사용하지만 일반 CMD vibration pattern 대신 이 고정 sweep을 실행합니다.

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
17 passed
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

`motor_host_test.c`는 production `motor.c`를 host stub 환경에서 직접 컴파일해 late/inactive tick, queue full, 연속 PLAY, timer failure와 `DIR_UNKNOWN` four-cardinal sweep을 확인합니다. 현재 기준 100 Hz / 1000 Hz 모두 **10/10 통과**하며 timer race model도 `ALL PASS`입니다. 실물 PWM/DRV8833 동작을 검증하는 테스트는 아닙니다.

### Event-direction fail-safe host 테스트

```bash
python firmware/tests/run_event_direction_host_tests.py --cc gcc
```

production `main.c`를 host stub 위에서 그대로 컴파일해 `event_id → direction → motor output` 경로를 검증합니다. 현재 6개 시나리오, **46/46 checks 통과**입니다.

- 정상 event_id → 기존 방향 mask 유지
- 기록되지 않은 event_id → `0x00` (motors OFF)
- EVENT_HISTORY에서 evict된 event_id → `0x00`
- 정상적으로 기록된 `DIR_UNKNOWN` → 8모터 동시 `0xFF` 출력 대신 four-cardinal sequential sweep 호출
- 비정상 direction 값 → `0x00` + error log
- 방향 0~7 → 기존 identity mapping 유지

즉 lookup/timing 오류는 motors OFF로 fail-safe 처리하고, 정상 `DIR_UNKNOWN`은 **앞 → 오른쪽 → 뒤 → 왼쪽을 한 개씩 순차 진동**하도록 분리했습니다. 한 번에 하나의 모터만 구동하므로 기존 `0xFF` 8모터 동시 출력보다 peak current 부담도 줄였습니다.

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

4개의 INMP441과 dual-I2S 구조를 검증하기 위한 별도 test app이 있습니다. 현재 bring-up에서 I2S1 slave timeout은 GPIO5/6 clock을 GPIO Matrix로 I2S1에 내부 loopback하는 방식으로 해결했으며, 두 버스 모두 DMA read가 되는 것까지 확인했습니다. 다만 실제 microphone sample은 아직 0으로 들어오므로 sample skew/TDoA 실측 단계까지는 진행하지 못했습니다.

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
- `run_live_ai()`는 meit-ai의 `classifier.adapter.predict_array()` + `decision.judge.judge()`를 호출하는 실제 live adapter로 구현됨
- 시작 시 `warmup_live_ai()`로 모델을 미리 로딩해 첫 실제 이벤트의 모델 로딩 지연을 줄임
- AI 내부 결과값은 일반 Python 값으로 유지하고, BLE 송신 직전에 기존 `CMD` binary packet으로 encode
- 남은 검증은 **실물 BLE 연결에서 AUDIO/DIR → AI → CMD end-to-end가 정상인지 확인하는 것**

현재 laptop-side BLE protocol/unit/regression test는 실제 하드웨어 없이 **17개 모두 통과**했습니다.

BLE production 코드는 현재 baseline이 구현되어 있고 CMD malformed packet validation까지 보강된 상태입니다. 실제 다음 항목은 실물에서 확인해야 합니다.

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

## meit-ai 연동 기준

AI팀 최신 진행 기준으로 위험음 분류 모델과 진동 결정 로직은 준비되어 있습니다.

- 분류 대상: 경적 / 사이렌 / 충돌 / 일상
- 검증 정확도 공유값: 경적 96.6%, 사이렌 95.3%
- 1회 추론 시간 공유값: 약 26.8 ms
- AI팀 공유 기준 게이팅 주기: 250 ms
- `meit-ee`와 `meit-ai`의 `GATING_MS`는 현재 **250 ms로 일치**함. 두 repo가 별도 상수를 사용하므로 이후 한쪽 값을 바꾸면 다시 cross-check 필요
- AI팀은 위험음 종류 판단 후 진동 세기·패턴까지 결정하는 로직을 보유
- `meit-ee` 쪽에서는 MCU가 TDoA 방향을 계산하고 AUDIO/DIR을 노트북으로 전송
- 노트북은 AI 판단 결과를 기존 `CMD` binary packet으로 encode해 MCU로 전송
- MCU는 `event_id`로 기존 방향을 찾아 해당 방향의 모터를 구동

회의에서 문자열 형태의 `3,1,95` 예시가 제안되었지만, 현재 `meit-ee`에는 이미 binary `CMD` protocol과 MCU parser, host/unit/regression test가 구현되어 있으므로 실제 통합 기준은 기존 binary protocol을 유지하는 쪽으로 정리합니다. AI 코드 내부에서는 direction/class/intensity/pattern을 일반 값으로 다루고, BLE 송신 직전에 `laptop/protocol.py`의 CMD encoder를 사용하는 방식입니다.

현재 핵심 미완료 항목은 **실물 BLE에서 `AUDIO/DIR → run_live_ai() → CMD → ESP32` end-to-end를 검증하는 작업**입니다. live adapter 자체는 구현되어 있으므로 repo 전체를 합치거나 firmware protocol을 다시 설계할 필요는 없습니다.

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
| `config.h` | `CLIP_FRAMES = 120` | 이벤트당 2.56초 오디오(16 kHz 변환 후 40960 samples, AI는 앞 2.5초 사용) |
| `config.h` | `TDOA_VOTE_FRAMES = 6` | 방향 voting 프레임 수 |
| `config.h` | `MOTOR_PWM_FREQ_HZ = 20000` | ERM PWM 초기값. 실물에서 진동/소음/저 duty 기동성 비교 필요 |
| `config.h` | `MOTOR_SLEEP_GPIO = -1` | Adafruit DRV8833 SLP를 3V3에 strap, 펌웨어 미제어 |
| `config.h` | `MOTOR_SUPPLY_MV = 4200` | **현재 4×AA bring-up에는 미확정 임시값**. 셀 종류/최대 pack voltage 확인 후 motor test 전에 수정 필요 |
| `config.h` | `MOTOR_RATED_MV = 3000` | coin ERM 정격 3 V |
| `config.h` | `MOTOR_DUTY_CAP` | 현재 4200 mV 임시값으로 계산됨. **4×AA 실제 전압 확인 전에는 최종 cap으로 사용하지 않음** |
| `config.h` | `UNKNOWN_SWEEP_ON_MS = 80` | `DIR_UNKNOWN` cardinal motor 1개당 ON 시간 |
| `config.h` | `UNKNOWN_SWEEP_OFF_MS = 40` | cardinal motor 사이 OFF gap |

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
| Laptop BLE unit/regression test | 17 tests 통과 |
| Live AI bridge | 인터페이스 구현 / meit-ai 모델·decision 코드와 adapter 연결 필요 |
| DRV8833 진동 패턴 sequencer | 구현 / host 회귀 테스트 10/10 @100 Hz·10/10 @1000 Hz / unknown cardinal sweep 포함 / 실물 미검증 |
| event_id → motor fail-safe / unknown sweep | 구현 / host test 46/46 checks 통과 |
| dual-I2S sync test app | 구현 / build 통과 / 실물 측정 필요 |
| motor self-test app | 구현 / build 통과 / **회로 배선 완료, 실제 모터 구동은 아직 미확인** |
| serial dump parser / calibration CLI | 구현 / parser 테스트 통과 |
| 실제 INMP441 4채널 수음 | **미해결** — dual-I2S DMA는 정상, 실제 mic RAW는 48/16 kHz 모두 0 |
| 실제 dual-I2S sample sync | **대기** — 두 DMA read 성공, 실제 mic signal 확보 후 skew 측정 필요 |
| 실제 8방향 정확도 | 실물 검증 필요 |
| BLE 실제 throughput / MTU / loss | 실물 검증 필요 |
| meit-ai live inference 연동 | AI 측 모델/decision 로직 준비됨 / `meit-ee` BLE live path와 adapter 연결 필요 |
| 전체 end-to-end | 실물 통합 필요 |
| 회로 설계 | mic + DRV8833 x4 + motor x8 실물 배선 완료 / mic 수음 미해결 / motor 구동 미확인 |
| 전원 구조 | **현재 motor rail은 4×AA pack으로 변경 / ESP32는 USB-C / common GND / firmware motor supply 값 재설정 필요** |
| Hardware pin map | `firmware/PINMAP.md` 작성 / 코드·문서 간 GPIO 대조 완료 / 실물 배선 검증 필요 |
| MPU6050 (optional/debug) | GPIO 예약(I2C 41/42)만 되어 있음 / driver 미구현 |
| microSD logging (optional/debug) | GPIO 예약(SPI 12/14/18/21)만 되어 있음 / logging 코드 미구현 |

## 최신 스냅샷 요약

현재는 **실물 회로 bring-up 단계**입니다. ESP32 내부 dual-I2S 구조와 DMA는 동작하며, I2S1 slave timeout은 GPIO Matrix clock loopback으로 해결했습니다. 하지만 실제 INMP441 데이터는 아직 들어오지 않아 mic/TDoA 실측은 대기 상태입니다. 모터 쪽은 DRV8833 x4, motor x8, SLP/GND/VM 배선까지 완료했지만 실제 motor self-test는 아직 실행하지 않았습니다. motor power는 기존 LiPo 후보에서 **4×AA battery pack**으로 변경했으며, 셀 종류/pack voltage 확인 후 firmware의 motor supply/duty cap을 맞춰야 합니다.

## 2026-09-23 실물 bring-up / troubleshooting

### Microphone / dual-I2S

- 초기 문제: I2S0 master는 동작하지만 I2S1 slave `i2s_channel_read()`가 timeout.
- 해결: GPIO5(BCLK) / GPIO6(WS)을 GPIO Matrix로 I2S1 clock input에 내부 loopback. 이후 양쪽 I2S DMA read 성공.
- 내부 입력 검증: `CONST_ONE` 주입 시 A/B 모두 `0xFFFFFFFF`, `CONST_ZERO` 주입 시 A/B 모두 `0x00000000`으로 정상 수신.
- 실제 microphone: FRONT/RIGHT/BACK/LEFT 모두 RAW 0, RMS -240 dBFS.
- 48 kHz뿐 아니라 16 kHz에서도 동일하게 RAW 0.
- SD pull test에서 pull-up 시 raw가 all-ones, pull-down 시 all-zero로 따라가 실제 mic의 능동 SD 구동은 아직 관측되지 않음.
- 결론: ESP32 내부 I2S/DMA 문제는 상당 부분 배제했지만, 실제 mic power/clock/data 외부 경로는 측정 장비 없이 확정하지 못함.

### Motor / power

- DRV8833 x4와 ERM motor x8 회로 배선 완료.
- DRV8833 SLP x4는 ESP32 3V3, GND는 ESP32와 motor battery common GND.
- motor supply는 기존 1S LiPo 후보에서 **4×AA battery pack**으로 변경. battery `+`는 DRV8833 VM common node, battery `-`는 common GND. ESP32는 USB-C로 별도 전원 공급.
- **실제 모터 구동은 아직 확인하지 않음.** 회로 작업까지만 완료.
- 셀 종류/pack voltage를 아직 실측하지 못했으므로 `MOTOR_SUPPLY_MV`와 duty cap은 motor self-test 전에 재설정 필요.

## 다음 작업 순서

1. **AA pack 사양 확인 + motor firmware 전원값 수정** — cell type / fresh-cell 기준 최대 pack voltage를 확인한 뒤 `MOTOR_SUPPLY_MV`, duty cap을 맞춥니다.
2. **Motor self-test** — 한 번에 motor 1개씩 0→7 순서로 구동해 위치 mapping, 최소 기동 duty, driver/battery 이상 여부를 확인합니다.
3. **Microphone hardware 재검증** — 멀티미터/logic analyzer 확보 후 VDD, BCLK, WS, SD를 실제 mic 단자 기준으로 확인합니다.
4. **실제 TDoA calibration** — mic signal 확보 후 dual-I2S sample skew와 8방향 정확도를 측정합니다.
5. **BLE hardware validation** — advertising/connection/MTU/AUDIO/DIR/CMD를 실물에서 확인합니다.
6. **meit-ai end-to-end integration** — AUDIO/DIR → AI → CMD → motor까지 통합합니다.

실물에서 문제가 확인되기 전에는 이미 통과한 TDoA/resampler/motor host/BLE protocol 구조를 추측으로 크게 변경하지 않는 것을 원칙으로 합니다.
