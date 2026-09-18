# meit-ee

MEIT 5회 전공 융합 프로젝트 — 방향성 위험음 촉각 알림 시스템의 전자 파트

INMP441 마이크 4개로 주변 소리를 수음하고 TDoA로 8방향을 추정한 뒤, 노트북 AI와 BLE로 통신해 해당 방향의 진동모터를 구동합니다. AI 추론은 노트북에서 수행하고, ESP32-S3는 마이크 수음·방향 추정·BLE 통신·모터 제어를 담당합니다.

## 구조

```text
meit-ee/
├─ firmware/                  # ESP32-S3 펌웨어
│  ├─ CMakeLists.txt
│  ├─ PROTOCOL.md             # meit-ai ↔ meit-ee BLE 인터페이스 규격
│  ├─ SYNC_CHECK.md           # 듀얼 I2S 동기화 실물 검증 절차
│  ├─ main/
│  │  ├─ main.c               # 전체 파이프라인 및 이벤트 상태 관리
│  │  ├─ config.h             # 샘플레이트·GPIO·threshold 등 주요 설정
│  │  ├─ audio_capture.c/.h   # INMP441 4채널 I2S 수음
│  │  ├─ tdoa.c/.h            # GCC-PHAT 기반 TDoA 및 8방향 추정
│  │  ├─ resample.c/.h        # 48 kHz → 16 kHz AI용 오디오 변환
│  │  ├─ ble_svc.c/.h         # NimBLE 오디오/방향 송신 및 명령 수신
│  │  ├─ motor.c/.h           # DRV8833 + 진동모터 PWM/패턴 제어
│  │  ├─ CMakeLists.txt
│  │  └─ idf_component.yml    # ESP-IDF/esp-dsp 의존성
│  └─ tests/
│     └─ timer_race_model.py # 모터 패턴 시퀀서 검증 모델
├─ tdoa/                      # PC에서 사용하는 TDoA 검증·캘리브레이션 도구
│  ├─ gcc_phat.py             # Python GCC-PHAT 기준 구현
│  ├─ direction_4mic.py       # 4마이크 → 8방향 계산
│  ├─ calibration.py          # I2S sync offset / 착용 상태 캘리브레이션
│  └─ test_synthetic.py       # synthetic 8방향 및 스트레스 테스트
├─ CHANGES.md                 # 외부 리뷰 반영 및 수정 이력
├─ requirements.txt           # Python 테스트 의존성
└─ .gitignore
```

`tdoa/`는 PC에서 알고리즘을 검증하는 Python 도구이고, 실제 ESP32에서 실행되는 TDoA·BLE·모터 코드는 모두 `firmware/main/`에 있습니다.

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

- MCU: LOLIN S3 V1.0.0 / ESP32-S3 / 16 MB Flash + 8 MB PSRAM
- 마이크: INMP441 x4
- 모터 드라이버: Adafruit DRV8833 x4
- 진동모터: 3 V ERM coin motor x8
- 방향 추정 샘플레이트: 48 kHz
- AI 전송 오디오: 16 kHz mono PCM16
- 통신: ESP32-S3 내장 BLE

GPIO 값은 현재 `firmware/main/config.h`에 정리되어 있으며, **실제 LOLIN S3 핀맵과 배선 전 반드시 재확인해야 합니다.**

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

## Python TDoA 테스트

설치

```bash
pip install -r requirements.txt
```

실행

```bash
cd tdoa
python test_synthetic.py
```

현재 Python 기준 구현은 synthetic 8방향 테스트와 경적·사이렌·잔향·배치 오차 스트레스 테스트에 사용합니다. 이 결과는 실제 INMP441 하드웨어 정확도를 의미하지 않으며, 실물에서는 별도 검증이 필요합니다.

## ESP32-S3 펌웨어

ESP-IDF **5.2.x** 기준으로 구성되어 있습니다.

```bash
cd firmware
idf.py set-target esp32s3
idf.py build
```

실제 보드 flash 및 4마이크 동기 수음은 아직 실물 검증이 필요합니다. 펌웨어를 올리기 전에 `firmware/SYNC_CHECK.md`를 먼저 확인합니다.

## BLE 인터페이스

전자 파트와 AI 파트 사이에는 세 종류의 메시지를 사용합니다.

- `AUDIO`: ESP32-S3 → 노트북, 16 kHz mono PCM16 오디오 chunk
- `DIR`: ESP32-S3 → 노트북, event_id / 8방향 / TDoA confidence / dBFS
- `CMD`: 노트북 → ESP32-S3, event_id / intensity / class / vibration pattern

정확한 byte layout과 event_id 처리 방식은 `firmware/PROTOCOL.md`를 기준으로 합니다.

## 주요 설정값

실제 마이크와 허리띠 조립 후 실측 조정이 필요한 값입니다.

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
| `config.h` | `MOTOR_PWM_FREQ_HZ = 20000` | ERM PWM 캐리어 **초기값**. 실물에서 진동 세기·소음·저 duty 기동성 비교 후 확정 |
| `config.h` | `MOTOR_SLEEP_GPIO = -1` | Adafruit DRV8833 SLP를 3V3에 strap (펌웨어 미제어) |
| `config.h` | `MOTOR_SUPPLY_MV = 4200` | **VM 전원 경로 확인 필요.** raw LiPo면 4200 유지, 고정 regulated rail 확인 시에만 변경 |
| `config.h` | `MOTOR_RATED_MV = 3000` | coin ERM 정격 3 V |
| `config.h` | `MOTOR_DUTY_CAP` | 위 둘에서 계산 (기본 182/255 ≈ 71%). 보수적 초기 duty 상한, 실물 전류·온도 확인 필요 |

## 현재 상태

| 항목 | 상태 |
|---|---|
| Python GCC-PHAT / 8방향 계산 | 완료 |
| synthetic 8방향 테스트 | 통과 |
| 경적·사이렌 등 스트레스 테스트 | 통과 |
| ESP32-S3 펌웨어 구조 | 구현 |
| 48 kHz → 16 kHz resampling | 구현 |
| BLE chunking / event_id | 구현 |
| DRV8833 진동 패턴 제어 코드 | 구현 / 실물 미검증 |
| 실제 INMP441 2개/4개 수음 | 실물 검증 필요 |
| 듀얼 I2S sample sync | 실물 검증 필요 |
| BLE 실제 throughput | 실물 검증 필요 |
| 실제 8방향 정확도 | 실물 검증 필요 |
| meit-ai 실시간 BLE receiver 연동 | 통합 필요 |

## 실물 도착 후 우선순위

1. ESP-IDF build / ESP32-S3 flash
2. BLE advertising 및 노트북 연결 확인
3. INMP441 2개 동시 수음
4. INMP441 4개 동시 수음
5. `SYNC_CHECK.md` 기준 듀얼 I2S offset 측정 및 전원 재인가 반복 검증
6. 실제 8방향 TDoA 측정
7. BLE AUDIO 전송 시간·MTU·chunk loss 측정
8. meit-ai 실시간 receiver 연결
9. DRV8833 + 진동모터 8개 통합
10. 전체 end-to-end 테스트

