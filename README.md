# meit-ee

MEIT 방향성 위험음 촉각 알림 시스템의 전자 파트입니다.
현재 구성은 **INMP441 ×2, single I2S stereo, LEFT / RIGHT / BACK,
vibration motor ×2**입니다. ESP-IDF 5.2.5 / LOLIN S3 설정을 사용합니다.

## Signal flow

```text
INMP441 LEFT + RIGHT → I2S0 48 kHz stereo (32-bit slots, signed 24-bit data)
  ├─ DC removal → GCC-PHAT(LEFT, RIGHT) → 3-direction vote → BLE DIR
  └─ float (LEFT + RIGHT)/2 → existing FIR 48k→16k → mono PCM16 → BLE AUDIO
                                                          ↓
                                                Laptop meit-ai
                                                          ↓ CMD(event_id)
Event direction lookup → LEFT / RIGHT / BOTH → two DRV8833 A channels
```

AI audio는 기존 **16 kHz, mono, little-endian PCM16, 40960 samples / 2.56초**를
유지합니다. AI adapter는 기존대로 앞 2.5초를 사용합니다. 두 채널을 float로 변환하고
double intermediate로 평균내므로 정수 덧셈 overflow가 없습니다. 기존 resampler의
FIR, streaming decimation phase, PCM16 saturation을 유지합니다. 두 채널의 위상차로
평균 신호가 약해질 수 있으므로 착용 상태의 녹음으로 AI 정확도와 gate를 재확인해야 합니다.

## GPIO / hardware

| 기능 | GPIO / 연결 |
|---|---|
| I2S BCLK | GPIO5 → 두 마이크 SCK |
| I2S WS/LRCLK | GPIO6 → 두 마이크 WS |
| I2S DIN | GPIO7 ← 두 마이크 SD 공통 |
| LEFT microphone | L/R=GND → left slot → `CH_LEFT=0` |
| RIGHT microphone | L/R=3.3V → right slot → `CH_RIGHT=1` |
| LEFT motor | GPIO13 → 해당 DRV8833 AIN1 |
| RIGHT motor | GPIO1 → 해당 DRV8833 AIN1 |
| 각 DRV8833 | AIN2=GND, AOUT1/2에 모터, SLP=3V3 |

두 드라이버는 각각 A channel만 사용합니다. GPIO15/16/17, 두 번째 I2S와 clock
loopback은 사용하지 않습니다. 확정 핀은 [PINMAP](firmware/PINMAP.md), 회로 설명은
[hardware README](docs/hardware/README.md)를 참조하세요.

LOLIN S3 V1.0.0의 **16 MB Quad SPI flash / 8 MB Octal PSRAM, 80 MHz, NimBLE**
설정은 유지했습니다. Clip 작업 버퍼와 BLE queue는 PSRAM을 사용합니다.
IMU GPIO42/41, microSD GPIO12/14/18/21은 기존 예약만 유지하며 driver는 미구현입니다.

전원 구성은 기존 문서의 USB-C ESP32 / 별도 4×AA motor rail / common GND를 유지합니다.
최신 요청에는 배터리 종류·전압 변경 정보가 없으므로 이를 새로 가정하지 않았습니다.
`MOTOR_SUPPLY_MV=4200`은 이전 LiPo 기준 임시값입니다. AA 셀 종류와 최대 pack 전압을
확인하고 실제 motor rail에 맞춰야 합니다. PWM duty cap은 전류·발열 보증이 아닙니다.

## Direction convention and limitation

| 방향 | 내부 index | BLE byte / AI integer | Motor mask | 출력 |
|---|---:|---:|---:|---|
| LEFT | 0 | 6 | 0x01 | LEFT only |
| RIGHT | 1 | 2 | 0x02 | RIGHT only |
| BACK | 2 | 4 | 0x03 | LEFT + RIGHT simultaneously |

내부 index는 3개 투표 배열에만 사용하며 그대로 BLE에 보내지 않습니다.
`firmware/main/direction.h`에서 wire 변환을 관리하고, Python은 `laptop/protocol.py`에서
동일한 값만 허용합니다. 숫자 6/2/4는 기존 AI 의미를 유지하므로 packet size, UUID,
AUDIO와 CMD 구조를 변경하지 않습니다. 이전 방향 값 0/1/3/5/7은 수신 시 거부됩니다.

기존 GCC-PHAT의 `sig * conj(ref)`와 inverse FFT 부호를 유지합니다.
`gcc_phat(LEFT, RIGHT)`의 delay는 **LEFT 도착시간 − RIGHT 도착시간**입니다.

- delay < −T: LEFT가 먼저 도착 → LEFT
- delay > +T: RIGHT가 먼저 도착 → RIGHT
- −T ≤ delay ≤ +T: center-axis → BACK

`config.h`의 `TDOA_THRESHOLD_SAMPLES=2.0`은 초기값(48 kHz에서 약 41.67 µs)입니다.
실제 측정으로 조정해야 하며 `TDOA_LR_BIAS_SAMPLES=0.0`, `MIC_SPACING_M=0.16`,
`MIN_CONFIDENCE=0.15`도 실측 calibration 대상입니다. 먼저 bias를 빼고 threshold를 적용합니다.

**With two laterally spaced microphones, front/back cannot be physically disambiguated
from TDoA alone. The current prototype excludes FRONT from its operating domain,
therefore near-zero inter-microphone delay is mapped to BACK.**

즉 BACK은 제한된 사용 조건에서 중앙축 소리에 붙이는 이름이며, 실제 앞뒤 식별 결과가
아닙니다. 별도의 FRONT runtime enum, 패턴 또는 분기점은 없습니다.

무음·낮은 confidence·투표 실패는 방향이 아닌 **UNKNOWN 상태**로 유지합니다.
wire `0xFF`, AI `-1`, confidence=0이며 audio는 계속 AI로 보냅니다.
위험음으로 확인되면 기존 unknown 알림 정책을 두 모터용으로 축소하여
**LEFT 80 ms → OFF 40 ms → RIGHT 80 ms**를 실행합니다. BACK의 동시 진동과 구분됩니다.
유효한 세 방향은 AI의 기존 intensity / pattern을 그대로 적용합니다.
기록되지 않았거나 evict된 event_id 및 비정상 방향은 motors OFF입니다.

## Repository

- `firmware/main/`: single I2S 수집, `audio_samples.c` slot decode/downmix,
  TDoA, 이벤트 관리, 기존 BLE transport, 모터 sequencer.
- `firmware/hardware_tests/stereo_i2s/`: production 수집 코드를 사용하는 stereo dump.
- `firmware/hardware_tests/motor_self_test/`: LEFT → RIGHT → BOTH 실물 검사.
- `firmware/tests/`: 실제 C 소스를 컴파일하는 host tests와 timer model.
- `tdoa/direction_2mic.py`, `calibration.py`, `parse_dump.py`: stereo PC 기준 도구.
- `laptop/`: 기존 BLE receiver와 AI bridge, 3방향 protocol mapping.
- `display_server.py`, `display.html`: 두 모터 및 방향 표시; UNKNOWN을 BACK으로 표시하지 않음.
- `legacy/`: 호출되지 않는 이전 알고리즘을 `.py.txt`로 보존. 현재 build/test에 포함되지 않음.

## Build and tests

ESP-IDF **5.2.5** 환경에서:

```sh
cd firmware
idf.py build
# 새 checkout에 target이 없는 경우에만 idf.py set-target esp32s3
```

`sdkconfig`와 `sdkconfig.defaults`의 LOLIN S3 설정을 유지합니다.
반복 빌드에 fullclean/set-target은 필요하지 않습니다. Factory app partition은 기존 1 MB입니다.

Repo root에서:

```sh
python -m pip install -r requirements.txt
python -m pytest laptop/tests tdoa -q
python tdoa/test_synthetic.py
python tdoa/test_parse_dump.py
python firmware/tests/run_audio_host_tests.py --cc gcc
python firmware/tests/run_motor_host_tests.py --cc gcc
python firmware/tests/run_event_direction_host_tests.py --cc gcc
python firmware/tests/timer_race_model.py
```

Windows에서 gcc 대신 `--cc /path/to/zig.exe --zig`도 지원합니다.
Audio host test는 실제 `tdoa.c`/`audio_samples.c`/`resample.c`를 사용하며 esp-dsp FFT만
portable host adapter로 대체합니다. 타겟 FFT와 물리 배선 검증은 보드에서 필요합니다.
테스트 범위: 좌우 도착시간 부호, ±T/경계 바깥/zero, silence, stereo slot 순서,
downmix/PCM16 clipping 및 40960 sample 경계, 3방향 모터 선택, 타이머 경합,
unknown/stale event, BLE packet과 재연결입니다.

## Hardware bring-up

```sh
cd firmware/hardware_tests/stereo_i2s
idf.py build
idf.py -p COM_PORT flash monitor
# repo root에서 monitor 파일 변환:
python tdoa/parse_dump.py capture.txt capture.npy
python tdoa/calibration.py capture.npy
```

Dump 순서는 LEFT, RIGHT이고 shape은 `(2,8192)`입니다. 5개 startup frame을 drain한 뒤
캡처하며, 모든 sample을 출력하는 작업은 이 별도 테스트에서만 합니다.
Production은 약 1초마다 `[MIC] L_rms / R_rms`를 출력하고 이벤트 초기 6개 투표 frame에만
`[TDOA] delay_samples / delay_us / confidence [DIR]`를 출력합니다.
CMD 및 모터 sequencer에서 `[MOTOR] L / R / duty`를 확인할 수 있습니다.

[Stereo validation 절차](firmware/SYNC_CHECK.md)를 따라 좌우 각각 가까운 소리로
slot 배선과 부호를 확인하고 실제 착용 상태에서 중심 threshold를 결정하세요.
DMA 성공만으로 실제 마이크 단자의 clock/data 정상 여부를 입증할 수는 없습니다.

```sh
cd firmware/hardware_tests/motor_self_test
idf.py menuconfig
idf.py build
idf.py -p COM_PORT flash monitor
```

기본 intensity 15%, ON 700 ms, gap 500 ms입니다. LEFT와 RIGHT를 따로 확인한 뒤
BACK용 동시 출력을 확인하고 종료 시 OFF로 둡니다. 15%에서 모터가 기동하지 않을 수 있습니다.
전원값 확인 후 최소 기동 duty, 동시에 구동할 때 rail sag/reset/발열을 실측하세요.

## BLE / AI

```sh
python -m laptop.ble_receiver --help
python -m laptop.ble_receiver --mock-ai
python -m laptop.ble_receiver
```

실제 AI 모드는 meit-ai를 설치하거나 PYTHONPATH에 추가해야 합니다.
`classifier.adapter.predict_array()` → `decision.judge.judge()`의 기존 경로를 사용하며
warm-up, thread offload, event_id, chunk-loss 검출과 reconnect 처리를 유지합니다.
`MEIT_FAKE_EVENTS=1`은 LEFT/RIGHT/BACK을 순환하는 통합 검사 모드로 마이크 초기화와
capture task를 실행하지 않습니다. 기본은 0입니다.

정확한 packet layout은 [PROTOCOL](firmware/PROTOCOL.md)을 참조하세요.
실물에서 MTU/throughput, chunk loss, reconnect 및 `DIR+AUDIO → AI → CMD → motor`를
검증해야 합니다. 현재 코드를 build/test한 결과와 변경 영향은
[migration report](docs/two_mic_migration.md)에 기록합니다.
