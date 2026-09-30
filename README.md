# meit-ee

The **EE / wearable-output** side of the MEIT directional hazard-sound haptic
system: everything from "the AI decided something" to "the wearer feels it".

The ESP32 does not listen and does not estimate direction. Four iPhones and the
`meit-ios` laptop bridge do that, `meit-ai` classifies the sound, and this
repository owns the last hop — deciding what the belt should feel like, and
delivering it over BLE to two vibration motors.

```text
iPhone  (stereo mic -> left / center / right)
   |  Wi-Fi
   v
meit-ai  (YAMNet + calibrated head + judge)   <- what the sound is, and whether it matters
   |
   v
laptop/haptic.py                              <- THIS REPO: what the wearer should feel
   |  BLE CMD v2
   v
ESP32-S3 "MEIT-BELT"                          <- THIS REPO: plays it
   |
   +--> LEFT motor  (GPIO21)
   +--> RIGHT motor (GPIO13)
```

Verified against the real `meit-ai` checkout: the model loads, classifies, and its
`judge()` gates drive the belt end to end. See
[`docs/AI_INTEGRATION.md`](docs/AI_INTEGRATION.md) for the contract and for which
layer owns which decision.

Neither `meit-ios` nor `meit-ai` is modified or vendored here.

- iOS: <https://github.com/MEIT-competition/meit-ios>
- AI: <https://github.com/MEIT-competition/meit-ai>

## What the wearer feels

> **Where** you feel it = where the sound is.
> **How many times** = what the sound was.
> **How hard** = how sure the AI is.

| Direction (iOS stereo) | LEFT motor | RIGHT motor | | Class | Pulses |
|---|---|---|---|---|---:|
| `left` | ■ | | | `crash` | 1 long |
| `right` | | ■ | | `horn` | 2 |
| `center` | ■ | ■ *together* | | `siren` | 3 |
| `unavailable` | — | — *(silent)* | | | |

iOS stereo reports exactly these three, so `center` (both motors together) is the
"not to either side" cue. There is no rear cue: a stereo pair cannot separate
front from back, and the system has no rear sensor.

**Loudness** sets the PWM duty, following meit-ai's own rule that confidence
decides *whether* to alert and dBFS decides *how hard*. Full reasoning, timings and
constraints: **[`docs/HAPTIC_DESIGN.md`](docs/HAPTIC_DESIGN.md)**.

See it without any hardware:

```bash
python -m laptop.haptic_preview
```

## Quick start

```bash
# 0. dependencies
python -m pip install -r requirements.txt

# 1. does the encoding look right?  (no belt, no Bluetooth needed)
python -m laptop.haptic_preview

# 2. flash the belt
cd firmware && idf.py set-target esp32s3 && idf.py build && idf.py -p COMx flash monitor

# 3. is the hardware alive?  (no iPhone, no AI)
python -m laptop.send_motor_test left   --raw
python -m laptop.send_motor_test center --raw
python -m laptop.send_motor_test right  --raw

# 4. do the real alert patterns feel distinguishable?  (9 combinations)
python -m laptop.send_motor_test all

# 5. verify the whole back half at once, on the real belt
python -m laptop.verify_pipeline --meit-ai /path/to/meit-ai

# 6. run the live pipeline (meit-ios bridge running)
#    single wearable iPhone (stereo left/center/right):
python -m laptop.ios_motor_bridge --status-path /wearable/status
#    or the four-iPhone coordination path (Auto enabled in the app):
python -m laptop.ios_motor_bridge
```

Step 5 is the one to reach for when you want a single answer to "does this
actually work?". It runs the real model, plays each verdict on the belt, and tells
you beforehand what you should feel — including the clips `meit-ai` rejects, where
a belt that stays still is the pass.

## Repository layout

```text
laptop/
  haptic.py             # AI result -> motor command. The core of this repo.
  haptic_profile.json   #   ...its tunable timings and intensities
  protocol.py           # BLE CMD v2/v3 encode, decode, validate
  belt_client.py        # BLE scan / connect / reconnect / sequence numbers
  ios_motor_bridge.py   # PRIMARY runtime: /auto/status -> haptic -> BLE
  ai_runner.py          # meit-ai loader (+ mock) for bench work
  ai_motor_bridge.py    # STANDALONE runtime: audio or result -> haptic -> BLE
  send_motor_test.py    # direct motor test, raw pulses or real patterns
  haptic_preview.py     # print every pattern offline
  verify_pipeline.py    # ONE command: real model -> belt, with expected feel
  test_*.py             # unit tests (no hardware required)
  test_meit_ai_integration.py  #   ...plus the real model, when MEIT_AI_PATH is set

firmware/
  main/
    main.c              # command -> motor pattern
    cmd_parse.c/.h      #   ...packet validation, host-testable
    ble_svc.c/.h        #   ...motor-only BLE GATT server
    motor.c/.h          #   ...2-motor PWM + per-step-mask sequencer
    config.h            #   ...GPIO, PWM, duty cap, protocol constants
  hardware_tests/
    motor_self_test/    # on-device LEFT -> RIGHT -> FRONT -> BACK check
  tests/
    run_cmd_parse_tests.py    # host test: C parser vs. Python encoder
    run_motor_host_tests.py   # host test: sequencer timing + sweep ordering
  PINMAP.md  PROTOCOL.md

docs/
  HAPTIC_DESIGN.md      # why the vibration patterns are what they are
  AI_INTEGRATION.md     # the meit-ai contract and the EE ingest API
  IOS_INTEGRATION.md    # the /auto/status contract
  ARCHITECTURE.md       # responsibility split
  MIGRATION_2026-09-30.md  hardware/

legacy/                 # previous microphone/TDoA implementation, reference only
```

Nothing under `legacy/` is built or imported.

## Tests

None of these need a belt, a board or a Bluetooth adapter.

```bash
python -m unittest discover -s laptop -p "test_*.py" -t . -v   # 190 tests
python firmware/tests/run_cmd_parse_tests.py                   # C parser vs Python encoder
python firmware/tests/run_motor_host_tests.py                  # sequencer, 2 tick rates
python -m compileall -q laptop
```

With a `meit-ai` checkout, 12 of those 190 additionally run the real model
end to end instead of skipping:

```bash
MEIT_AI_PATH=~/src/meit-ai python -m unittest laptop.test_meit_ai_integration -v
```

Three are worth knowing about:

- **`run_cmd_parse_tests.py`** compiles the real `firmware/main/cmd_parse.c` with
  `cc` and feeds it golden packets generated by `laptop/protocol.py`. The Python
  encoder and the C parser cannot drift apart without failing the build.
- **`run_motor_host_tests.py`** runs the real sequencer against a virtual clock at
  two RTOS tick rates, covering the timer races that only show up as a pattern
  that occasionally sticks on or cuts short.
- **`test_meit_ai_integration.py`** loads the real `meit-ai` SavedModel and asserts
  the whole chain, including that meit-ai's `judge()` gates actually suppress quiet
  and non-danger clips, and that every emitted burst clears the belt's
  perceptibility floor.

## Running the primary bridge

Start `meit-ios/bridge/server.py` as its own repository documents, then pick the
demo path:

```bash
# single wearable iPhone (stereo left/center/right) — the demoable-now path.
# Start listening in the app's Wearable mode, then:
python -m laptop.ios_motor_bridge --status-path /wearable/status

# four-iPhone coordination path — enable Auto in the app, then:
python -m laptop.ios_motor_bridge
python -m laptop.ios_motor_bridge --intensity-scale 1.2 --log-level DEBUG
```

Both paths publish the same event shape, so the bridge treats them identically.
Per completed event it: polls the chosen status endpoint, accepts only a **new**
completed dangerous event with a usable direction, builds the pattern, writes one
BLE command, and only **then** records the event as delivered — so a failed write
is retried after reconnecting instead of being silently lost.

Suppressed events are logged with the reason (`AI decided not dangerous`,
`direction 'unknown' is not usable`, …) rather than dropped silently, because at a
demo "nothing happened" needs an explanation.

## Standalone / bench runtime

For replaying a `.wav` through the real model, or for an iOS bridge running
without AI. Details in [`docs/AI_INTEGRATION.md`](docs/AI_INTEGRATION.md).

```bash
# feel a wav file's classification, no iPhones involved
export MEIT_AI_PATH=~/src/meit-ai
python -m laptop.ai_motor_bridge --wav siren.wav --direction center

# no meit-ai checkout yet? mock the verdict and test everything else
python -m laptop.ai_motor_bridge --wav siren.wav --direction center --mock-label siren

# ingest server: POST /ee/audio (PCM16LE + X-MEIT-Direction) or /ee/event
python -m laptop.ai_motor_bridge
```

## Firmware

ESP-IDF 5.2.x, target `esp32s3`.

```bash
cd firmware
idf.py set-target esp32s3
idf.py build
idf.py -p COMx flash monitor
```

Expected boot log:

```text
ready: iOS/AI -> laptop -> BLE CMD v2/v3 -> LEFT/RIGHT/FRONT/BACK haptics
```

Active sources: `main.c`, `cmd_parse.c`, `ble_svc.c`, `motor.c`.

### Reflashing

The wire format is CMD v2, which is what firmware built from this repo speaks and
what earlier builds already accepted. A belt flashed with any recent firmware
therefore works with the current laptop code without reflashing.

## Hardware

| Function | GPIO | Connection |
|---|---:|---|
| LEFT motor PWM | 21 | LEFT DRV8833 AIN1 |
| RIGHT motor PWM | 13 | RIGHT DRV8833 AIN1 |

See [`firmware/PINMAP.md`](firmware/PINMAP.md) and
[`docs/hardware/README.md`](docs/hardware/README.md).

`config.h` caps PWM duty from an assumed supply and motor rating. That is a
**software guard, not a measurement.** Before raising intensity, verify the real
pack voltage, motor rating, DRV8833 wiring, common ground, current draw and
temperature on the physical unit.

## BLE contract

Device `MEIT-BELT`, service `01000000-1d9e-218f-9a4b-9c4e302a9d11`, command
characteristic `04000000-1d9e-218f-9a4b-9c4e302a9d11`. Byte layout and every
rejection rule: [`firmware/PROTOCOL.md`](firmware/PROTOCOL.md).

## Change history

- [`CHANGELOG.md`](CHANGELOG.md)
- [`docs/MIGRATION_2026-09-30.md`](docs/MIGRATION_2026-09-30.md)
- [`GIT_PUSH_GUIDE.md`](GIT_PUSH_GUIDE.md)
