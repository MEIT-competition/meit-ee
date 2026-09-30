# GitHub update guide

Use this package to update the existing `meit-ee` repository. Do not create a
second repository.

## 1. Branch

```bash
git checkout main
git pull origin main
git checkout -b feat/three-direction-haptics
```

Copy this package's files over the working tree, then review before committing:

```bash
git status
git diff --stat
git diff
```

## 2. Run every check

None of these need a belt, a board or a Bluetooth adapter, so run them all before
pushing.

```bash
python -m pip install -r requirements.txt

# 190 unit tests (12 skip without MEIT_AI_PATH)
python -m unittest discover -s laptop -p "test_*.py" -t . -v
python -m compileall -q laptop

# the real firmware C, compiled and tested on the host
python firmware/tests/run_cmd_parse_tests.py
python firmware/tests/run_motor_host_tests.py

# with the model on hand, the 12 skipped tests run the real thing
MEIT_AI_PATH=~/src/meit-ai python -m unittest laptop.test_meit_ai_integration -v
```

Expected tails:

```text
Ran 190 tests ... OK (skipped=12)
275 checks, 0 failures
RESULT: 14/14 passed at 100 Hz
RESULT: 14/14 passed at 1000 Hz
```

Sanity-check the encoding visually too — this needs nothing installed:

```bash
python -m laptop.haptic_preview
```

## 3. Build the firmware when ESP-IDF is available

```bash
cd firmware
idf.py set-target esp32s3
idf.py build
```

The first build regenerates `sdkconfig` and `dependencies.lock`. Both are
intentionally git-ignored: the source-of-truth settings live in
`sdkconfig.defaults` and `main/idf_component.yml`.

## 4. Files this change touches

New:

```text
laptop/haptic.py                      laptop/haptic_profile.json
laptop/belt_client.py                 laptop/haptic_preview.py
laptop/ai_runner.py                   laptop/ai_motor_bridge.py
laptop/verify_pipeline.py             laptop/test_verify_pipeline.py
laptop/test_protocol.py               laptop/test_haptic.py
laptop/test_belt_client.py            laptop/test_ai_runner.py
laptop/test_ai_motor_bridge.py        laptop/test_meit_ai_integration.py
firmware/main/cmd_parse.c             firmware/main/cmd_parse.h
firmware/tests/run_cmd_parse_tests.py firmware/tests/cmd_parse_host_test.c
firmware/tests/run_motor_host_tests.py firmware/tests/motor_host_test.c
docs/HAPTIC_DESIGN.md                 docs/AI_INTEGRATION.md
requirements-ai.txt
```

Rewritten: `laptop/protocol.py`, `laptop/ios_motor_bridge.py`,
`laptop/send_motor_test.py`, `laptop/test_ios_motor_bridge.py`,
`firmware/main/{config.h,main.c,motor.c,motor.h,ble_svc.c,ble_svc.h}`,
`firmware/PROTOCOL.md`, `README.md`, `docs/ARCHITECTURE.md`,
`docs/IOS_INTEGRATION.md`, `.github/workflows/python-tests.yml`, `CHANGELOG.md`,
`VERSION`.

Touched: `firmware/PINMAP.md`, `docs/hardware/README.md`,
`firmware/hardware_tests/motor_self_test/main/motor_self_test_main.c`,
`requirements.txt`.

Unchanged: everything under `legacy/`, `docs/MIGRATION_2026-09-30.md`.

## 5. Commit and push

```bash
git add -A
git commit -m "feat: drive the belt from meit-ai over three iOS stereo directions

Direction selects which motors, the danger class selects the pulse count, and
loudness selects PWM duty, following meit-ai's own rule that confidence only
decides whether to alert.

Verified against the real meit-ai checkout end to end. Adds host tests that
compile the real firmware parser and sequencer, and check the C parser against
golden packets from the Python encoder."
git push -u origin feat/three-direction-haptics
```

Suggested pull-request title:

```text
Drive the belt from meit-ai over three iOS stereo directions
```

Points worth calling out in the PR body:

- **No reflash needed.** The wire format is CMD v2, which the firmware already on
  the belt accepts, so new laptop code drives an existing belt unchanged.
- Three directions only (`left`/`center`/`right`), because iOS estimates direction
  from a stereo pair and cannot separate front from back.
- `meit-ios` and `meit-ai` are untouched; EE consumes `/auto/status` and, for bench
  work only, imports `meit-ai` from `$MEIT_AI_PATH`.
- Inference is **not** duplicated: the `meit-ios` bridge already runs the model.
- Still needs bench validation — see the "Hardware validation still required"
  list in `CHANGELOG.md`.
