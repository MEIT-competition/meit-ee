# Two-microphone hardware migration report

Target checkout: `C:\meit-ee`, baseline commit `1e69ab7`.
The checkout was clean before editing. No flash or physical motor actuation was performed.

## A. Conflicts found before editing

| Original file(s) | Conflict |
|---|---|
| `firmware/main/config.h` | NUM_MICS=4, FRONT/RIGHT/BACK/LEFT channel order, bus B GPIO15/16/17, 8-motor declaration |
| `firmware/main/audio_capture.c`, `.h` | Two RX handles/raw buffers, I2S1 slave/clock matrix loopback, four-channel deinterleave and bus-skew assumptions |
| `firmware/main/tdoa.c`, `.h` | LEFT/RIGHT plus BACK/FRONT correlation, bus-skew subtraction, atan2 azimuth and eight-way quantization |
| `firmware/main/main.c` | Eight vote bins, loudest single mic for AI, eight GPIO entries, direction-to-one-motor mapping, fake events cycling eight directions |
| `firmware/main/motor.c`, `.h` | Eight LEDC channels, identity direction-to-motor lookup, four-cardinal unknown sweep |
| `firmware/main/ble_svc.h`, `firmware/PROTOCOL.md` | DIR contract described all old values; AUDIO/CMD layout itself remains usable |
| `laptop/protocol.py` and direction/receiver tests | Eight-name list and acceptance of obsolete DIR values; receiver test default selected retired value 0 |
| `display.html` | Eight-direction labels/angle placement, all values other than LEFT/RIGHT (including unknown) displayed as BACK |
| `tdoa/direction_4mic.py`, `calibration.py`, `test_synthetic.py` | Four-sensor geometry, two/six pairs, dual-bus calibration, eight-direction tests |
| `tdoa/parse_dump.py`, `test_parse_dump.py` | Four-channel CSV and NumPy shape |
| `firmware/hardware_tests/dual_i2s_sync/` | Standalone four-channel dump target sharing production capture source |
| `firmware/hardware_tests/motor_self_test/sdkconfig` (generated) | ESP32-S3 test configuration generated from the existing shared defaults on first build |
| `firmware/hardware_tests/motor_self_test/main/motor_self_test_main.c` | Duplicated old GPIO array, eight-motor loop |
| `firmware/tests/*` | Host expectations used old motor masks/directions and unknown sweep |
| `README.md`, `firmware/PINMAP.md`, `firmware/SYNC_CHECK.md`, `docs/hardware/README.md` | Old hardware presented as current, including diagrams and obsolete bring-up instructions |

The wire packet was inspected before choosing the new mapping. AUDIO is chunked PCM16;
DIR is four bytes; CMD contains event_id, intensity, class and timed pattern pairs, **no direction field**.
The MCU retains event direction for CMD lookup. Local `C:\meit-ai\decision\judge.py` passes
the supplied direction integer through, so keeping existing LEFT=6 / RIGHT=2 / BACK=4
avoids reinterpreting values for AI. `ble_svc.c`, `laptop/ble_receiver.py`, `laptop/ai_bridge.py`,
production `sdkconfig`, `sdkconfig.defaults` and `resample.c` are unchanged.

## B. Changed files

Paths below are relative to the checkout; related C/header files have separate responsibilities noted.

| File | Change |
|---|---|
| `firmware/main/config.h` | Two channels, active GPIO definitions, microphone spacing, center threshold/bias, two-motor indices and masks |
| `firmware/main/direction.h` (new) | Three compact indices, retained wire enum, checked conversion and names |
| `firmware/main/audio_capture.c` | Only I2S0 master; one stereo buffer; drain startup samples; reject failed/short reads |
| `firmware/main/audio_capture.h` | Two-channel frame contract and shared decode/downmix APIs |
| `firmware/main/audio_samples.c` (new) | LEFT/RIGHT slot decode, existing DC/RMS processing, average using double intermediate |
| `firmware/main/tdoa.c` | Keep GCC-PHAT core/sign; remove second pair, angle and bus skew; apply calibrated delay threshold |
| `firmware/main/tdoa.h` | Remove angle/front-back fields and bus-skew API; expose threshold decision for testing |
| `firmware/main/main.c` | Three vote bins, explicit wire conversion, averaged AI input, partial-event discard on read failure, limited logs, fake mode cycles three directions and skips mic init |
| `firmware/main/motor.c` | Two GPIOs/LEDC channels, two-motor loops, existing sequencer/timing/intensity retained, L/R unknown alert |
| `firmware/main/motor.h` | Wire direction maps to mask: LEFT only, RIGHT only, BACK both; obsolete single-bit lookup removed |
| `firmware/main/ble_svc.h` | Document supported values without changing packet layout |
| `firmware/main/CMakeLists.txt` | Add shared `audio_samples.c` |
| `firmware/hardware_tests/stereo_i2s/CMakeLists.txt` | Rename former dual-bus target to stereo test |
| `firmware/hardware_tests/stereo_i2s/main/CMakeLists.txt` | Compile new stereo test with actual capture/decode sources |
| `firmware/hardware_tests/stereo_i2s/main/stereo_i2s_main.c` | LEFT/RIGHT dump only, 8192 samples per channel |
| `firmware/hardware_tests/stereo_i2s/sdkconfig` | Relocated former standalone test configuration; target remains ESP32-S3 |
| `firmware/hardware_tests/motor_self_test/sdkconfig` (generated) | ESP32-S3 test configuration generated from the existing shared defaults on first build |
| `firmware/hardware_tests/motor_self_test/main/motor_self_test_main.c` | Use central GPIO array, LEFT then RIGHT then BOTH |
| `firmware/tests/audio_host_test.c` (new) | Actual C delay polarity/threshold, stereo decode, overflow/clipping, 40960-sample boundary tests |
| `firmware/tests/run_audio_host_tests.py` (new) | Host build of actual audio/TDoA/resampler source with a portable FFT adapter |
| `firmware/tests/motor_host_test.c` | Retain timer regressions; verify two GPIOs and LEFT/RIGHT/BACK masks, updated unknown alert |
| `firmware/tests/run_motor_host_tests.py` | Two GPIO constants for stubs, 13 scenarios at each RTOS tick rate |
| `firmware/tests/event_direction_host_test.c` | Three retained wire mappings, invalid old values, real capture/vote/collect flow and averaged mono input |
| `firmware/tests/run_event_direction_host_tests.py` | Link real downmix source and run all three directions plus unknown |
| `firmware/tests/timer_race_model.py` | Two physical motor bits and two-bit all-off mask; historical race demonstrations retained |
| `laptop/protocol.py` | Canonical LEFT=6 / RIGHT=2 / BACK=4 mapping, reject retired direction bytes |
| `laptop/tests/test_direction_mapping.py` | Supported/rejected wire values, C/Python constant agreement, display unknown handling |
| `laptop/tests/test_ble_receiver_regressions.py` | Use supported direction for existing receiver regressions |
| `display_server.py` | Resolve direction name centrally; invalid/unknown becomes UNKNOWN |
| `display.html` | Two physical indicators, explicit BACK=both, unknown has no false BACK label |
| `tdoa/direction_2mic.py` (new) | Stereo GCC-PHAT reference with same sign, center band, bias and unresolved status |
| `tdoa/calibration.py` | Center-axis bias/spread measurement; no multi-peripheral skew or templates |
| `tdoa/parse_dump.py` | Parse LEFT/RIGHT CSV into `(2,N)` |
| `tdoa/test_parse_dump.py` | Verify new channel order and malformed sequence rejection |
| `tdoa/test_synthetic.py` | Left/right/near-zero, exact ±T and adjacent boundaries, silence and obsolete shape rejection |
| `legacy/four_mic/direction_4mic.py.txt` | Original uncalled algorithm preserved outside Python imports |
| `legacy/four_mic/calibration.py.txt` | Original calibration preserved as reference text |
| `legacy/four_mic/test_synthetic.py.txt` | Original synthetic/stress tests preserved outside test discovery |
| `legacy/README.md` | Explain exclusions and historical asset status |
| `README.md` | Current architecture, mapping, limitation, build/tests, calibration and bring-up |
| `firmware/PINMAP.md` | Current pins and channel/motor wiring |
| `firmware/PROTOCOL.md` | Retained wire values, unchanged packets, new motor/unknown semantics |
| `firmware/SYNC_CHECK.md` | Stereo slot/polarity/bias validation replaces dual-bus sync procedure |
| `docs/hardware/README.md` | Current two-mic/two-driver/two-motor wiring; mark old PNGs historical |
| `docs/two_mic_migration.md` | This report |

The former four `dual_i2s_sync` target files were replaced by the four `stereo_i2s`
files listed above. `tdoa/direction_4mic.py` was removed from the active tool directory
after verifying no production/laptop import depends on it. Its text and its companion
calibration/tests are retained under `legacy/`. Existing ignored build outputs were
not deleted; old binaries are not the current implementation and must not be flashed.

## C. Final architecture

```text
2× INMP441 → ESP32-S3 stereo I2S0, 48 kHz
  ├─ GCC-PHAT LEFT−RIGHT delay → bias correction → threshold → 3-bin vote → DIR
  └─ DC-removed stereo average → existing FIR ÷3 → 16 kHz PCM16 2.56 s → AUDIO
DIR + AUDIO → laptop AI → existing CMD(event_id, intensity, class, pattern)
event_id lookup → LEFT / RIGHT / BOTH → two motors
```

No second I2S RX channel/task/clock route remains. A reliable center-axis estimate
maps to BACK by the restricted operating condition. Two lateral microphones cannot
physically disambiguate front/back; FRONT is excluded from the domain. Silence or low
confidence remains an UNKNOWN status, not a fourth direction or an artificial BACK.

## D. GPIO final table

| Function | GPIO |
|---|---:|
| I2S BCLK | 5 |
| I2S WS | 6 |
| I2S DIN | 7 |
| LEFT DRV8833 AIN1 | 21 |
| RIGHT DRV8833 AIN1 | 13 |

Both AIN2 pins are grounded. LEFT mic L/R=GND; RIGHT mic L/R=3.3V.
Optional reserved pins remain IMU SDA/SCL=42/41 and SD SPI SCK/MOSI/MISO=12/14/18;
SD CS is unassigned because GPIO21 is the LEFT motor input.
GPIO15/16/17 and retired motor GPIO1/2/4/8/9/10/11 are not configured by this runtime.

## E. Direction mapping

| Direction | Internal index | BLE / AI | Motor mask |
|---|---:|---:|---:|
| LEFT | 0 | 6 | 0x01 |
| RIGHT | 1 | 2 | 0x02 |
| BACK | 2 | 4 | 0x03 |

With the provisional T=2 setting, calibrated LEFT-minus-RIGHT delay < −2 samples
maps to LEFT; > +2 to RIGHT; inclusive [-2,+2] to BACK. This is not a measured
final threshold. `TDOA_THRESHOLD_SAMPLES` and `TDOA_LR_BIAS_SAMPLES` must be
calibrated on hardware. At 48 kHz, 2 samples is 41.67 microseconds. No angle is fabricated.
UNKNOWN uses internal -1 / wire 255 / AI -1, confidence 0. It preserves the previous
danger-alert policy with an alternating L/R pattern instead of the retired sweep.
Invalid/absent event records request zero mask. Valid BACK uses the same timing and
intensity for both motors in the same sequencer ON step.

## F. Build and test results

Production `idf.py build` in `C:\meit-ee\firmware`: **PASS**, ESP-IDF 5.2.5,
esp-dsp 1.8.2, target ESP32-S3. Image `0x92520` bytes; 1 MB app partition has 43% free.
Production sdkconfig/defaults and dependency lock remain unchanged.

All three ESP-IDF targets completed successfully:

```powershell
idf.py -C C:\meit-ee\firmware build
idf.py -C C:\meit-ee\firmware\hardware_tests\stereo_i2s build
idf.py -C C:\meit-ee\firmware\hardware_tests\motor_self_test build
```

- Stereo I2S image: `0x348f0` bytes, 79% app partition free.
- Motor self-test: build and link PASS.
- Production and stereo were incrementally rebuilt after final comment cleanup.
- Installed framework build metadata reports `v5.2.5-dirty`; the task did not
  modify the SDK itself. Results refer to this existing local SDK installation.

Executed Python/host commands (Zig executable from the task-local `ziglang` installation):

```powershell
$env:PYTHONPATH = 'C:\Users\hi2ji\OneDrive\문서\ChatGPT\meit\.migration-tools'
$env:ZIG_GLOBAL_CACHE_DIR = 'C:\Users\hi2ji\OneDrive\문서\ChatGPT\meit\.zig-cache'
$zig = 'C:\Users\hi2ji\OneDrive\문서\ChatGPT\meit\.migration-tools\ziglang\zig.exe'
python -m pytest laptop/tests tdoa -q
python firmware/tests/run_audio_host_tests.py --cc "$zig" --zig
python firmware/tests/run_motor_host_tests.py --cc "$zig" --zig
python firmware/tests/run_event_direction_host_tests.py --cc "$zig" --zig
python firmware/tests/timer_race_model.py
```

| Check | Result |
|---|---|
| Python BLE/receiver/direction/display + synthetic/dump tests | 34 passed |
| Actual C audio/TDoA/resampler | 160 checks passed |
| Actual C motor sequencer | 13/13 at 100 Hz, 13/13 at 1000 Hz |
| Actual C main/event/capture/vote mapping | 52/52 for each of UNKNOWN/LEFT/RIGHT/BACK, 208 total |
| Timer race model | Current scenarios ALL PASS; deliberate pre-fix counterexamples still print FAIL |
| `git diff --check` | PASS |

Audio/motor suites ran on the staged source; corresponding production files were
copied byte-for-byte into the actual checkout. Python and expanded event/capture
tests were rerun against `C:\meit-ee` after applying the changes.
The host FFT adapter verifies C algorithm/sign/decision integration, not the ESP32
optimized FFT. ESP-IDF builds verify target compilation and linking, not physical timing.

Environment setup used the installed `C:\Espressif\frameworks\esp-idf-v5.2.5\export.ps1`
with `IDF_TOOLS_PATH=C:\Espressif` and its Python 3.11 environment first on PATH.
The exporter reported a missing optional `idf_version.txt` diagnostic but dependency
checks passed and the build used IDF 5.2.5. No code compilation error was reported.

## G. Hardware verification remaining

1. Confirm microphone L/R straps and real DMA slot correspondence, each channel's
   RMS and actual mic-side BCLK/WS/SD. Software test data cannot prove physical wiring.
2. Measure worn mic spacing, center bias, delay threshold and firmware confidence
   with real LEFT/RIGHT/center sounds, reflections and motor vibration.
3. Direct HIGH/LOW testing verified GPIO21=LEFT, GPIO13=RIGHT and simultaneous BOTH.
   Confirm the production PWM pattern, startup duty, motor rail sag, brownout and
   driver temperature on the final assembled board.
4. Determine AA chemistry/maximum pack voltage and set `MOTOR_SUPPLY_MV`; 4200 mV
   remains the pre-existing provisional value because the latest request gives no rail value.
5. Check average/downmix level/phase effects on the real model, BLE MTU/throughput,
   loss/reconnect behavior and complete AUDIO/DIR → AI → CMD → haptic latency.

## Final source audit

Searched the whole repository before editing and repeated searches after migration
for the requested direction/mic/I2S/motor/GPIO/TDoA/AUDIO/CMD terms. Inspected CMake
dependencies and Python imports before moving legacy tools.

Active firmware/laptop/tdoa/display source has no `CH_FRONT`, `CH_BACK`,
`I2S_NUM_1`, obsolete bus pins, `motor_bit_for_direction`, bus-skew API,
eight-direction quantizer/vote array or eight-motor mapping. Remaining eight-valued
constants such as EVENT_HISTORY=8, PWM resolution and byte widths are unrelated
to direction or motor counts and are intentionally retained.

Remaining matches for retired hardware are confined to this change report,
explicit limitation/unused-pin text, `legacy/*.txt`, old schematics and the historical
bring-up log. Generated `build/` and third-party `managed_components/` are excluded
from source-policy checks. The current main CMake target contains seven sources,
including the new `audio_samples.c`, and no legacy target.

## Pre-commit review and hardware procedure (2026-09-30)

The migration was already committed as `d2d8f51` when reviewed; the worktree
was clean. See [two_mic_bringup.md](two_mic_bringup.md) for the ordered
flash/monitor procedure, exact log formats, measured bias/threshold selection,
power prerequisites, BLE/AI end-to-end acceptance and remaining review risks.

**TODO(power): `MOTOR_SUPPLY_MV=4200` remains provisional until battery maximum
VM and actual motor rating are confirmed. No voltage value changed in this review.**

**TODO(AI contract): AUDIO remains 16 kHz PCM16, 40960 samples = 2.56 s.
Confirm with the AI team whether exactly 2.50 s / 40000 samples is required
and who performs any cropping. The interface has not changed.**

Final review validation rerun on this checkout: Python 34 tests PASS; actual C
audio 160 checks PASS; motor 13/13 at both 100/1000 Hz PASS; main/event/capture
52/52 for each of four statuses (208 total) PASS. The bring-up guide
PowerShell blocks parsed successfully and its Python snippets passed synthetic
LEFT/RIGHT/BACK captures, aggregate calibration and incomplete-dump rejection.
`config.h` differs only in comments; no firmware rebuild was needed for this
review. The earlier three target build results above are unchanged. No board
flash or physical hardware validation was performed.
