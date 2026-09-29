# GitHub update guide

Use this package to update the existing `meit-ee` repository rather than creating a second unrelated repository.

## Recommended branch

```bash
git checkout main
git pull origin main
git checkout -b refactor/ios-2motor-bridge
```

Copy the files from this package over the repository, then review:

```bash
git status
git diff --stat
git diff
```

Run the Python checks:

```bash
python -m pip install -r requirements.txt
python -m unittest laptop.test_ios_motor_bridge -v
python -m compileall -q laptop
```

When ESP-IDF 5.2.x is available:

```bash
cd firmware
idf.py set-target esp32s3
idf.py build
```

The first ESP-IDF build may regenerate `sdkconfig` and `dependencies.lock`; both are intentionally ignored because the source-of-truth settings are in `sdkconfig.defaults` and `main/idf_component.yml`.

## Recommended commit

```bash
git add -A
git commit -m "refactor: integrate iOS direction with 2-motor BLE belt"
git push -u origin refactor/ios-2motor-bridge
```

Suggested pull-request title:

```text
Refactor EE pipeline for iOS direction and 2-motor haptic output
```

Suggested pull-request summary:

```text
- move microphone/TDoA runtime to legacy
- consume meit-ios /auto/status on the Windows laptop
- send LEFT/CENTER/RIGHT CMD v2 over BLE
- drive two vibration motors (LEFT, BOTH, RIGHT)
- deduplicate event_id and retry failed BLE writes
- add STOP/disconnect fail-safe behavior and tests
- update active hardware/protocol/migration docs
```
