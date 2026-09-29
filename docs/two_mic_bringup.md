# 2-mic / 2-motor hardware bring-up

대상: `C:\meit-ee`, LOLIN S3 / ESP32-S3, ESP-IDF 5.2.5. 순서는 아래 1–8단계다.
명령은 Windows PowerShell 기준이며 COM 번호와 AI 환경 경로는 실제 값으로 바꾼다.
예시 로그의 시간·RMS·delay·confidence·event 번호는 설명용이며 실측 결과가 아니다.
이 문서는 `d2d8f51` 검수 결과이며, 실행 로직이나 BLE interface를 변경하지 않는다.

## 준비

- 마이크 LEFT L/R=GND, RIGHT L/R=3.3V. 두 SD=GPIO7, SCK=GPIO5, WS=GPIO6.
  마이크 전원과 공통 GND를 확인한다. CHIPEN이 노출된 모듈은 활성 상태를 확인한다.
- LEFT DRV8833 AIN1=GPIO13, RIGHT AIN1=GPIO1, 양쪽 AIN2=GND, SLP=3.3V.
  초기 마이크 시험 중에는 모터 VM 전원을 분리한다. 배선 변경은 전원을 끈 상태에서 한다.
- **TODO(power): `MOTOR_SUPPLY_MV=4200`은 provisional 값이다.** 배터리 종류,
  새 배터리/완충 시 최대 VM, 부하 중 VM, 모터 정격을 확인하기 전에는 다른 값으로
  추정 변경하지 않는다. 4xAA를 4.2 V로 가정해서는 안 된다. 5단계의 전압 조건을
  확인하기 전에는 실제 모터를 구동하지 않는다.
- 실제 장착 상태의 두 마이크 음향 포트 간 거리를 측정해 기록한다.
  `MIC_SPACING_M=0.16` 역시 실제 간격과 대조한다. 큰 bias로 잘못된 간격을 숨기지 않는다.

ESP-IDF용 PowerShell을 열거나, 설치된 환경을 다음처럼 불러온다.

```powershell
$Repo = 'C:\meit-ee'
$Stereo = "$Repo\firmware\hardware_tests\stereo_i2s"
$Motor = "$Repo\firmware\hardware_tests\motor_self_test"
$Capture = Join-Path $env:TEMP ('meit-bringup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $Capture | Out-Null
$env:IDF_TOOLS_PATH = 'C:\Espressif'
$env:PATH = 'C:\Espressif\python_env\idf5.2_py3.11_env\Scripts;' + $env:PATH
. C:\Espressif\frameworks\esp-idf-v5.2.5\export.ps1
idf.py --version
python -m serial.tools.list_ports
$Port = 'COM5' # 위 목록에서 실제 보드 포트로 변경
```

기대: IDF 5.2.5와 보드 COM 포트. 기존 로컬 SDK는 `v5.2.5-dirty`로 표시될 수 있다.
USB 재연결 후 포트가 바뀌면 `$Port`도 변경한다. 동시에 두 monitor를 열지 않는다.
PC 분석용 패키지는 저장소 밖의 별도 환경에 설치한다.

```powershell
python -m venv "$Capture\venv"
$Py = "$Capture\venv\Scripts\python.exe"
& $Py -m pip install -r "$Repo\requirements.txt"
Set-Location $Repo
```

## 1. stereo_i2s flash / monitor

```powershell
idf.py -C "$Stereo" build
idf.py -C "$Stereo" -p "$Port" flash monitor
```

기대 로그(ESP-IDF의 `I (...)` 접두사는 생략):

```text
audio: stereo LEFT/RIGHT @ 48000 Hz, BCLK=5 WS=6 DIN=7
stereo_i2s: capturing 8 contiguous frames (8192 samples/channel)
MEIT_DUMP_BEGIN,fs=48000,channels=LEFT|RIGHT,samples=8192
MEIT_RAW,0,0.001234,-0.000456
... (sample index 0부터 8191까지)
MEIT_DUMP_END,samples=8192
stereo_i2s: dump complete; reset to capture again
```

이 앱은 RMS/TDoA를 출력하지 않는다. 부팅마다 startup 5120 frames를 버린 뒤
8192 samples/channel(약 171 ms)을 **한 번만** 저장하고, 그 다음 UART로 출력한다.
로그를 본 뒤 박수치면 이미 capture가 끝났을 수 있다. 아래 절차로 측정한다.

1. 첫 dump가 `dump complete`로 끝날 때까지 기다린다.
2. `Ctrl+T`를 누르고 놓은 뒤 `Ctrl+L`로 monitor 파일 저장을 켠다.
   `Logging is enabled into file log.meit_stereo_i2s_test.<timestamp>.txt`를 확인한다.
3. 원하는 위치에서 연속 광대역 소리(예: 스피커의 잡음)를 먼저 재생한다.
   `Ctrl+T`, `Ctrl+R`로 보드를 reset한다. reset이 안 되면 보드 RESET/EN을 누른다.
4. 전체 dump와 `dump complete`까지 기다린다. `Ctrl+T`, `Ctrl+L`로 저장을 끈다.
   한 파일에는 **한 번의 완전한 dump만** 넣는다. 여러 reset의 index 0이 섞이면 parser가 거부한다.
5. `Ctrl+]`로 monitor를 종료하고 파일을 저장소 밖으로 복사한다.

```powershell
$Name = 'left_01' # 측정마다 quiet_01, right_01, center_01 등으로 변경
$Log = Get-ChildItem "$Stereo\log.*.txt" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$Log.FullName # monitor가 방금 표시한 파일과 같은지 확인
Copy-Item -LiteralPath $Log.FullName -Destination "$Capture\$Name.txt"
& $Py "$Repo\tdoa\parse_dump.py" "$Capture\$Name.txt" "$Capture\$Name.npy"
```

기대: `saved ...left_01.npy: shape=(2, 8192), dtype=float32`.
parser는 종료 마커/총 길이를 검사하지 않으므로 아래 분석에서 shape도 반드시 확인한다.
다음 측정은 `idf.py -C "$Stereo" -p "$Port" monitor`로 들어가 같은 저장 절차를 반복한다.
monitor 자체의 파일 저장은 UART bytes를 보존한다. PowerShell의 기본 UTF-16 리다이렉션으로
dump를 저장하지 않는다. 생성된 `log.*.txt`는 `.gitignore` 대상이다.

## 2. LEFT / RIGHT slot 및 RMS 확인

왼쪽 마이크 바로 가까이에서 소리를 내어 `left_near_01`, 오른쪽 가까이에서
`right_near_01`, 조용한 상태에서 `quiet_01`을 위 방식으로 수집한다.
가까운 소리는 slot 확인용이다. 이 파일을 원거리 방향 calibration에 섞지 않는다.
아래 PowerShell 함수는 현재 창에서 한 번 정의한다. 보드 코드는 수정하지 않는다.

```powershell
$Inspect = @'
import csv, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(sys.argv[1]) / 'tdoa'))
from direction_2mic import estimate_direction
p = Path(sys.argv[2]); bias = float(sys.argv[3]); threshold = float(sys.argv[4])
x = np.load(p)
assert x.shape == (2, 8192) and np.isfinite(x).all(), 'incomplete/invalid dump'
rms = 20*np.log10(np.sqrt(np.mean(x.astype(float)**2, axis=1)) + 1e-12)
peak = np.max(np.abs(x), axis=1)
print(f'[MIC-PC] L_rms={rms[0]:.1f} R_rms={rms[1]:.1f} dBFS; peak={peak}')
rows = []
for frame in range(8):
    d = estimate_direction(x[:, frame*1024:(frame+1)*1024],
                           bias_samples=bias, threshold=threshold)
    corrected = d.tau_lr_s*48000
    rows.append([frame, corrected+bias, corrected, d.confidence, d.name])
    print(f'[TDOA-PC] frame={frame} raw={corrected+bias:.3f} '
          f'corrected={corrected:.3f} conf={d.confidence:.2f} [DIR] {d.name}')
with p.with_suffix('.csv').open('w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['frame', 'raw_samples', 'corrected_samples', 'confidence', 'direction'])
    w.writerows(rows)
'@
function Inspect-Capture {
    param([string]$Name, [double]$Bias = 0, [double]$Threshold = 2)
    $Inspect | & $Py - "$Repo" "$Capture\$Name.npy" "$Bias" "$Threshold"
}
Inspect-Capture 'left_near_01'
Inspect-Capture 'right_near_01'
Inspect-Capture 'quiet_01'
```

예: 왼쪽 근접 소리에서 `[MIC-PC] L_rms=-25.0 R_rms=-42.0 dBFS`, 오른쪽에서는 크기 관계가 바뀐다.
이 dB 값은 고정 합격 기준이 아니다. 두 채널이 환경음에 반응하고 음원 위치를 바꾸면
해당 채널의 신호가 상대적으로 강해지는지 확인한다.
한쪽이 항상 0 / -240 dBFS이거나 두 배열이 완전히 같거나 지속적인 포화·평탄 파형이면 불합격이다.
이 dump는 DC 제거 후 데이터이므로 peak만으로 clipping 여부를 단정하지 않는다.

핀 측정이 가능하면 **각 마이크 단자**에서 WS 약 48 kHz, BCLK 약 3.072 MHz,
64 clocks/frame, WS low=LEFT / high=RIGHT, WS 전환 후 1-bit 지연의 Philips I2S를 확인한다.
32-bit slot의 상위 24-bit가 signed 음성 데이터다. L/R 스트랩이 같으면 공유 SD가 충돌할 수 있다.
startup 요구와 slot 동작은 [INMP441 datasheet](https://invensense.tdk.com/wp-content/uploads/2015/02/INMP441.pdf)를 참조한다.
정상 DMA read만으로 실제 마이크 핀의 클럭·배선이 정상이라고 확정하지 않는다.

## 3. TDoA LEFT / RIGHT / BACK 확인

실제 착용 간격/방향으로 마이크를 고정한다. 동일 거리(예: 1 m)의 LEFT, RIGHT,
뒤쪽 중앙에서 광대역 소리를 반복 재생하고 `left_01`, `right_01`, `center_01`로 수집한다.
현재 firmware 순서를 유지하기 위해 이 단계는 PC reference로 부호/분포를 먼저 확인한다.
ESP32 C TDoA와 투표 결과는 7–8단계에서 다시 검증한다.

```powershell
Inspect-Capture 'left_01' 0 2
Inspect-Capture 'right_01' 0 2
Inspect-Capture 'center_01' 0 2
```

여기서 `0`, `2`는 초기 후보일 뿐 최종 calibration 값이 아니다. 신뢰 가능한 frame의 예:

```text
[TDOA-PC] frame=0 raw=-12.000 corrected=-12.000 conf=0.80 [DIR] LEFT
[TDOA-PC] frame=0 raw=12.000 corrected=12.000 conf=0.80 [DIR] RIGHT
[TDOA-PC] frame=0 raw=0.000 corrected=0.000 conf=0.80 [DIR] BACK
```

LEFT가 먼저 도달하면 LEFT-minus-RIGHT delay는 음수다. 부호가 반대면 물리 slot/착용 방향을
먼저 확인한다. `BACK`은 중앙 delay를 뒤로 취급하는 정책이다. 이 2-mic 배치는 앞/뒤를
물리적으로 구분하지 못한다. 앞 중앙 소리도 BACK이 될 수 있으므로 FRONT 제외 조건을 확인한다.
무음·낮은 confidence의 UNKNOWN을 BACK 합격으로 세지 않는다.

## 4. bias / threshold calibration

1. 실제 간격이 다르면 실측 근거로 `firmware/main/config.h`와 PC reference의
   `tdoa/direction_2mic.py`에 있는 `MIC_SPACING_M`을 일치시킨다. 두 설정은 자동 공유되지 않는다.
   현재 0.16 m / margin 1.6의 검색 범위는 약 ±36 samples이다.
   추정치가 검색 경계에 몰리면 spacing·반사·신호부터 확인한다.
2. 최종 장착 상태에서 center/LEFT/RIGHT 각각 최소 10회 재부팅 capture를 모은다.
   파일명은 `center_01`…`center_10`, `left_01`…`left_10`, `right_01`…`right_10`으로 한다.
   거리/음량/방향, 실내외, 착용자, raw RMS와 confidence를 기록한다. 가까운 slot 시험 파일은 제외한다.
3. 각 파일에 `Inspect-Capture '<name>' 0 2`를 실행해 CSV를 만든다. 모든 frame을 기록하되
   calibration에는 confidence > 0인 frame만 쓴다. UNKNOWN 비율도 별도로 남긴다.
   단일 순음은 peak가 모호하므로 광대역으로 시작하고 실제 경적·사이렌으로 별도 검증한다.

기존 bias 도구도 1024-sample frame별 **bias=0**으로 동작한다:

```powershell
& $Py "$Repo\tdoa\calibration.py" "$Capture\center_01.npy"
```

예: `center bias_samples=0.650, range=[0.300,1.000], valid=8`.
한 번의 171 ms capture 결과를 최종값으로 채택하지 않는다. 여러 capture의 raw delay를 합쳐 계산한다.

```powershell
@'
import csv, sys
from pathlib import Path
import numpy as np
root = Path(sys.argv[1])
def load(group):
    files = sorted(root.glob(group + '_[0-9][0-9].csv'))
    assert len(files) >= 10, f'{group}: at least 10 captures required'
    vals = []; total = 0
    for p in files:
        with p.open(encoding='utf-8') as f:
            for r in csv.DictReader(f):
                total += 1
                if float(r['confidence']) > 0:
                    vals.append(float(r['raw_samples']))
    assert vals, f'{group}: no confident frames'
    print(f'{group}: valid={len(vals)}/{total}; captures={len(files)}')
    return np.asarray(vals)
c, l, r = load('center'), load('left'), load('right')
bias = np.median(c)
center95 = np.quantile(np.abs(c-bias), .95)
side05 = min(np.quantile(-(l-bias), .05), np.quantile(r-bias, .05))
print(f'B={bias:.3f}; center95={center95:.3f}; side05={side05:.3f} samples')
print('Choose measured margin M and test center95+M <= T < side05 on held-out captures.')
'@ | & $Py - "$Capture"
```

선정 규칙:

- `B = median(raw center delay)`를 `TDOA_LR_BIAS_SAMPLES` 후보로 쓴다.
  `corrected = raw - B`이며, 이미 보정된 값에서 B를 다시 빼면 안 된다.
- 중앙 residual의 `95th percentile(|raw-B|)`에 별도 반복/환경 변화에서 측정한 여유 M을
  더해 threshold 후보 T를 정한다. **M이나 T를 고정 2 samples로 가정하지 않는다.**
  95%는 시작용 평가 기준이며 서비스가 요구하는 오분류 허용률에 따라 percentile을 정한다.
- LEFT corrected가 음수, RIGHT corrected가 양수인지 확인하고,
  `center95 + M <= T < min(LEFT의 -(raw-B) 하위 5%, RIGHT의 (raw-B) 하위 5%)`
  를 만족하는지 본다. 범위가 없으면 calibration 실패다. T를 넓혀 BACK으로 몰지 않는다.
- `corrected < -T` → LEFT, `corrected > T` → RIGHT, `[-T,+T]` → BACK.
  1 sample은 20.833 µs이며 보간 때문에 B/T는 소수도 가능하다. T는 유한한 0 이상 값이어야 한다.
- 후보를 정한 데이터와 **별도**로 center/LEFT/RIGHT 반복 측정하고, 위치별 정답률,
  UNKNOWN 비율과 경계 근처 오분류를 기록한다. 같은 capture의 인접 8 frames만으로 안정성을 주장하지 않는다.
- 측정값을 `config.h`의 B/T에 수동 반영한다. PC 재검증 시 `Inspect-Capture '<name>' B T`에도
  동일 값을 전달한다. 예를 들어 B=0.65, T=3.1이 실제 선정된 경우에만
  `Inspect-Capture 'center_11' 0.65 3.1`을 실행한다. 이 수치는 권장값이 아니다.
- 7단계 production 로그의 `delay_samples`는 **이미 B가 빠진 값**이다.
  보정된 중앙 로그의 median이 R이면 새 B 후보는 `기존 B + R`이다.
  `MIN_CONFIDENCE=0.15`도 C firmware에서 별도 검증한다. Python은 median 기반, C는 mean 기반
  confidence여서 Python conf 숫자를 그대로 C 합격 기준으로 옮기지 않는다.

이번 검수에서는 실측이 없으므로 B=0 / T=2를 변경하지 않았다.

## 5. motor_self_test flash / monitor

VM과 모터 정격 확인을 먼저 끝낸다. `MOTOR_SUPPLY_MV`에는 순간 무부하 측정값 하나가 아니라
배터리 chemistry/셀 datasheet 또는 regulator 사양을 포함한 **최대 motor rail 값**을 반영한다.
`MOTOR_RATED_MV=3000`도 실제 부품 정격과 비교한다. 확인 전에는 VM을 분리한 채 GPIO만 검사한다.
PWM cap은 전류 제한이나 모터 안전 보증이 아니다. 실제 기동 전류·온도·전압 강하를 함께 본다.

```powershell
idf.py -C "$Motor" menuconfig
# MEIT motor self-test: intensity=15%, ON=700 ms, gap=500 ms 확인
idf.py -C "$Motor" build
idf.py -C "$Motor" -p "$Port" flash monitor
```

이 앱은 **부팅/reset 즉시** LEFT→RIGHT→BOTH를 자동 실행한다. flash 후와 monitor 접속 시
reset 때문에 반복될 수 있다. 출력 측정 준비를 먼저 끝내고 실행한다.
기본값/미변경 4200 설정에서의 예상 로그:

```text
motor: 2 motor channels ready @ 20000 Hz, duty capped at 182/255 (3000 mV motor on a 4200 mV rail)
motor_self_test: start: intensity=15%, LEFT then RIGHT then BOTH
motor_self_test: motor=0 gpio=13 intensity=15%
motor_self_test: motor=1 gpio=1 intensity=15%
motor_self_test: BACK: LEFT + RIGHT together
motor_self_test: complete: all motors off
```

전압을 실측 후 수정했다면 cap/rail 로그도 바뀌어야 한다. `4200` 로그는 전압 측정 결과가 아니다.
이 앱은 sequencer의 `[MOTOR]` 로그를 출력하지 않고 직접 `motor_trigger()`를 호출한다.

## 6. LEFT / RIGHT / BOTH 실제 모터 확인

```powershell
# 이전 monitor는 Ctrl+]로 종료한다. 다시 진입하면 자동 test가 실행된다.
idf.py -C "$Motor" -p "$Port" monitor
```

필요하면 monitor에서 `Ctrl+T`, `Ctrl+R`로 한 번 더 실행한다. 기대 로그는 5단계와 같다.

| 구간 | GPIO13 / LEFT | GPIO1 / RIGHT | 실물 확인 |
|---|---|---|---|
| motor=0, 약 700 ms | PWM | LOW | 착용자 기준 왼쪽만 진동 |
| gap, 약 500 ms | LOW | LOW | 양쪽 정지 |
| motor=1, 약 700 ms | LOW | PWM | 오른쪽만 진동 |
| 다음 gap | LOW | LOW | 양쪽 정지 |
| BACK, 약 700 ms | PWM | PWM | 두 모터 동시 진동 |
| complete 이후 | LOW | LOW | 계속 정지 |

두 PWM은 동일 LEDC timer, 20 kHz, 8 bit, hpoint=0이다. GPIO 쓰기는 순차 호출이므로
첫 edge가 원자적으로 동시에 바뀐다는 보장은 없다. BACK의 ON 구간이 겹치고 같은 duty인지
두 채널 scope/logic analyzer로 확인한다. 기본 15%는 cap의 15%로 raw duty 약 27,
전체 주기의 약 10.5%라서 정상 ERM도 기동하지 않을 수 있다. 이 결과만으로 고장 판정하지 않는다.
전압/정격 확인을 끝낸 뒤 menuconfig에서 단계적으로 올리고 매번 build/flash한다.
코드가 같은 PWM을 주어도 두 모터의 체감 세기는 다를 수 있다.

양쪽 동시 부하에서 VM/3V3 강하, ESP 재부팅/brownout, 드라이버 과열, OFF 후 멈춤을 확인한다.
AIN2=LOW에서 PWM OFF는 coast이므로 회전 관성에 따른 잔진동은 가능하다.
구동 방식 근거: [TI DRV8833 datasheet, §7.3.2](https://www.ti.com/lit/ds/symlink/drv8833.pdf).

## 7. production firmware flash

실측값만 config에 반영하고 `MEIT_FAKE_EVENTS=0`을 확인한다.

```powershell
Select-String -Path "$Repo\firmware\main\config.h" -Pattern '^#define (MEIT_FAKE_EVENTS |TDOA_LR_BIAS_SAMPLES|TDOA_THRESHOLD_SAMPLES|MOTOR_SUPPLY_MV)'
idf.py -C "$Repo\firmware" build
idf.py -C "$Repo\firmware" -p "$Port" flash monitor
```

기대 로그:

```text
audio: stereo LEFT/RIGHT @ 48000 Hz, BCLK=5 WS=6 DIN=7
ble: BLE service starting
main: running
main: [MIC] L_rms=-35.0 R_rms=-36.0 dBFS
main: [TDOA] delay_samples=-12.000 delay_us=-250.0 conf=0.80 [DIR] LEFT
```

MIC는 대략 1초마다, TDoA는 소리 이벤트 시작의 6 frames에 출력된다. 항상 연속 TDoA 로그가
나오는 구조가 아니다. 각 방향을 시험할 때 이전 clip(2.56 s)과 cooldown이 끝나도록 간격을 둔다.
calibrated T를 기준으로 실제 ESP32 로그에서 LEFT/RIGHT/BACK, UNKNOWN과 투표를 재확인한다.
`capture error`, `BLE queue full`, panic/brownout이 반복되면 다음 단계 합격으로 넘기지 않는다.
이 단계까지는 AI CMD가 없으므로 소리가 난다는 이유만으로 모터가 움직이지 않는다.

## 8. BLE DIR / AUDIO → AI → CMD → motor

production monitor는 유지하고 **다른 PowerShell**을 연다. 아래 Python은
meit-ai 모델과 의존성이 이미 동작하는 환경을 지정해야 한다. 경로는 예시다.

```powershell
$Repo = 'C:\meit-ee'
$AiRepo = 'C:\meit-ai' # 실제 meit-ai checkout으로 변경
$AiPython = 'C:\meit-ai\.venv\Scripts\python.exe' # 실제 AI 환경 Python으로 변경
Set-Location $Repo
& $AiPython -m pip install -r "$Repo\requirements.txt"
$env:PYTHONPATH = "$Repo;$AiRepo;" + $env:PYTHONPATH
& $AiPython -c "from classifier.adapter import SR, CLIP_SEC; from decision.judge import judge; print('AI SR=', SR, 'CLIP_SEC=', CLIP_SEC)"
& $AiPython -m laptop.ble_receiver --once
```

AI 설치/모델 경로가 준비되지 않아 import/warm-up이 실패하면 E2E 검증은 미완료다.
`--mock-ai`로 통과한 결과는 실제 AI 검증을 대체하지 않는다. 필요하면 동일 명령에
`--mock-ai`를 붙여 전송/모터 경로만 분리 시험한다(고정 intensity=60%, siren pattern이므로
모터 전압 검증 완료 후 실행). `--once`에서는 끊김 후 종료되므로 재시험할 때 다시 실행한다.

모델이 경보로 판정하는 녹음을 LEFT→RIGHT→뒤 중앙에 재생한다.
이벤트마다 CMD와 모터 종료까지 기다리고 번호를 대조한다. 출력 형식 예:

```text
[AI] loading model (warm-up)...
[AI] model ready
[SCAN] looking for 'MEIT-BELT'...
[BLE] connected=True
[GATT] services / characteristics
...
[BLE] subscribed to DIR + AUDIO
[DIR] event=7 dir=LEFT(6) conf=0.800 rms=-35 dBFS
[AUDIO] event=7: 40960 samples @ 16000 Hz (2.560 s)
[CMD] event=7 bytes=[7, 60, 1, 2, 10, 5, 10, 0]
```

CMD 예시는 intensity 60%, siren, `[100 ms ON,50 ms OFF],[100 ms ON,0 ms OFF]`인 경우다.
실제 AI의 숫자/패턴은 다를 수 있다. 정상/낮은 음량이면
`[AI] normal/below gate -> no CMD`와 모터 정지가 정상이다.
보드 대응 로그(미변경 cap=182 기준 60%의 raw duty=109):

```text
ble: connected
ble: event 7: sent 342 chunks (0 failed) in 900 ms, mtu=247
main: [DIR] LEFT event=7 [MOTOR] L=ON R=OFF
motor: [MOTOR] L=ON R=OFF duty=109 steps=2
```

900 ms는 예시일 뿐 목표/보장 시간이 아니다. 송신 요약과 CMD 로그 순서도 달라질 수 있다.
MTU=247일 때 PCM payload 최대 240 bytes로 342 chunks이며, chunk index는 255→0으로 wrap한다.
다른 MTU에서는 chunk 수가 달라져도 최종 **40960 samples**와 event 일치 여부를 확인한다.

| 방향 | PC DIR 값 | CMD 후 보드 mask / 실물 |
|---|---:|---|
| LEFT | 6 | L=ON, R=OFF / 왼쪽만 |
| RIGHT | 2 | L=OFF, R=ON / 오른쪽만 |
| BACK | 4 | L=ON, R=ON / 동일 패턴으로 양쪽 |
| UNKNOWN | wire 255, PC -1 | 경보 CMD가 오면 L→R 교대, BACK 동시 진동과 구별 |

DIR은 4 bytes `[event,direction,confidence,rms]`, AUDIO는 3-byte header 뒤 PCM16 LE,
CMD는 `[event,intensity,class,n_pairs,on/10,off/10,...]`로 6–12 bytes다.
UUID와 6/2/4 의미는 유지되었다. 내부 vote index 0/1/2를 BLE에 쓰면 안 된다.
`main: [DIR] ... [MOTOR]`는 선택 mask를 보여준다. intensity=0이면 실제 출력은 OFF이며
최종 판단은 `motor: [MOTOR] ... duty=...`와 핀/모터 측정으로 한다.

**TODO(AI contract): 현재 AI AUDIO는 mono 16 kHz, PCM16 little-endian,
40960 samples = 81920 PCM bytes = 2.56 s이다. AI팀이 정확히 2.50 s / 40000 samples를
요구하는지 확인하고, 960 samples의 crop/pad 처리 주체를 합의한다. 확인 전에는
firmware 길이, BLE layout, receiver 입력을 임의 변경하지 않는다.**
warm-up이 2.5 s를 사용한다는 사실만으로 live 40960 입력 호환성이 증명되지 않는다.

각 방향을 반복하며 실제 수신 길이, chunk gap/failed, MTU, 전송/AI/전체 지연을 기록한다.
소리 시작부터 CMD까지 2.56 s 수집 시간이 포함된다. 반복 경보·모터 구동 중 잡음,
BLE 끊김 후 재접속도 시험한다. `(L+R)/2` downmix는 지연/위상에 따라 AI 신호가 약해질 수 있으므로
개별 mic RMS뿐 아니라 실제 모델의 경보 판정도 확인한다.

## 검수 결과와 남은 제한 (2026-09-30)

| 항목 | 소스 검수 결과 / 남은 확인 |
|---|---|
| stereo slot | IDF 5.2.5 ESP32-S3 Philips defaults: BOTH, WS low first, bit_shift, left_align. `raw[2n]` / `raw[2n+1]` 처리와 일치. 실물 L/R 스트랩·핀 확인 필요. |
| GPIO | 활성 5/6/7/13/1 및 예약 42/41/12/14/18/21 사이 중복 없음. 코드상 USB/PSRAM/strapping 예약 핀과도 겹치지 않음. 실제 모듈·배선은 별도 확인. |
| BACK PWM | 두 LEDC channels가 동일 timer·duty·pattern을 사용함. 순차 update의 시작 edge, 동시 부하·기동·온도는 미측정. |
| 전압 / calibration | 4200 mV, bias=0, threshold=2 모두 실측 승인 전. commit 가능 여부와 실제 모터 구동 가능 여부를 구분. |
| I2S 연속성 | DMA descriptor 480 stereo frames=3840 bytes는 유효. 그러나 IDF RX 큐가 넘치면 오래된 DMA 항목을 버리며 다음 read가 성공할 수 있음. 현재 `on_recv_q_ovf` 관측이 없어 read 성공/정상 길이만으로 연속성을 보증하지 못함. 부하/로그 출력에 의한 지연은 하드웨어 stress 측정 대상. |
| BLE / CMD | 현 receiver와 6/2/4, UUID, byte layout 일치. DIR notify 실패를 capture 측에서 처리하지 않으며, AUDIO TX 완료는 peer 수신 ACK가 아님. gap은 receiver가 AI 전에 버리지만 마지막 chunk 손실은 완료 로그가 안 나올 수 있음. 성공 로그만 보지 말고 길이·event·실물까지 확인. |
| 지연 / event 이력 | 보드 방향 이력은 최근 8건. AI/전송이 그보다 뒤처지면 `CMD for unknown event_id=... -- motors OFF`. 8-bit event ID 재사용 전후 장기 지연도 보장하지 않음. 느린 MTU/지속 소리에서 queue full과 누락 여부 확인. |
| 방향 / AI 한계 | 앞/뒤 구분 불가, Python/C confidence 차이, mono 평균의 위상 상쇄, 2.50/2.56 s 계약 확인이 남음. |

이번 검수는 이 제한을 문서화하며 구조나 interface는 바꾸지 않는다.
I2S 근거: 로컬 IDF 5.2.5 `components/driver/i2s/include/driver/i2s_std.h`와
`components/driver/i2s/i2s_common.c`의 RX queue overflow 처리.

Git 검수 시 migration은 이미 `d2d8f51`에 있었고 작업 트리는 깨끗했다.
그 커밋에 새로 추적된 build/cache/ELF/BIN/NPY 산출물은 없다.
`motor_self_test/sdkconfig`는 재현용 설정이며 binary가 아니다.
`mic_bringup_log.txt`는 이전 커밋부터 있는 과거 진단 기록으로 이번 새 산출물이 아니다.
새 monitor 로그만 좁은 `log.*.txt` 패턴으로 ignore하며 분석 파일/환경은 `$Capture`에 보관한다.

commit 직전 확인 명령:

```powershell
git -C "$Repo" status --short --untracked-files=all
git -C "$Repo" diff --check
git -C "$Repo" diff --cached --check
git -C "$Repo" diff --cached --name-only
git -C "$Repo" ls-files | Select-String '(^|/)(build|__pycache__|\.pytest_cache|managed_components)/|\.(elf|bin|o|pyc|npy)$|(^|/)log\..*\.txt$'
```

마지막 검색은 이 저장소에서 결과가 없어야 한다. `git add -f`로 산출물을 강제 추가하지 않는다.
이번 문서/주석 보완은 commit할 수 있으나, 하드웨어 검증 완료 또는 배포 승인으로 해석하지 않는다.
