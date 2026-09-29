# GitHub update guide

Use this package to update the existing `meit-ee` repository. Do not create a
second repository.

## 1. Branch

```bash
git checkout main
git pull origin main
git checkout -b feat/haptic-encoding-cmd-v3
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

# 180 unit tests
python -m unittest discover -s laptop -p "test_*.py" -t . -v
python -m compileall -q laptop

# the real firmware C, compiled and tested on the host
python firmware/tests/run_cmd_parse_tests.py
python firmware/tests/run_motor_host_tests.py
```

Expected tails:

```text
Ran 180 tests ... OK
887 checks, 0 failures
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
laptop/test_protocol.py               laptop/test_haptic.py
laptop/test_belt_client.py            laptop/test_ai_runner.py
laptop/test_ai_motor_bridge.py
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
git commit -m "feat: encode direction and danger class in the vibration pattern

Direction selects which motors and in what order, the danger class selects
the pulse count, and confidence selects PWM duty. BACK becomes a
left-to-right sweep, which needs a motor mask per pattern step, so add BLE
CMD v3 while keeping v2 accepted for a belt that has not been reflashed.

Add host tests that compile the real firmware parser and sequencer and check
the C parser against golden packets from the Python encoder."
git push -u origin feat/haptic-encoding-cmd-v3
```

Suggested pull-request title:

```text
Encode direction and danger class in the vibration pattern (BLE CMD v3)
```

Points worth calling out in the PR body:

- **A belt must be reflashed to feel all four directions.** CMD v3's per-step
  masks are what make `back` a sweep. `--protocol 2` keeps working against old
  firmware, with `back` felt as `front` and every downgrade logged.
- `meit-ios` and `meit-ai` are untouched; EE consumes `/auto/status` and, for bench
  work only, imports `meit-ai` from `$MEIT_AI_PATH`.
- Inference is **not** duplicated: the `meit-ios` bridge already runs the model.
- Still needs bench validation — see the "Hardware validation still required"
  list in `CHANGELOG.md`.
