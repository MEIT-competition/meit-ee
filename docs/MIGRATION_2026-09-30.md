# Migration — 2026-09-30

## Goal

Simplify the prototype from the previous ESP32 microphone/TDoA path to the current design:

```text
OLD
ESP32 microphones -> TDoA -> BLE audio/direction -> laptop AI -> 2 motors

NEW
iPhone direction/audio -> meit-ios Windows bridge + AI -> EE laptop bridge -> BLE -> 2 motors
```

## Active-code changes

### Firmware
- runtime firmware reduced to `main.c`, `ble_svc.c`, `motor.c`
- microphone capture, resampling, TDoA and embedded PCM samples removed from the active build
- BLE command protocol changed to CMD v2 with `STOP / LEFT / CENTER / RIGHT`
- LEFT = GPIO21, RIGHT = GPIO13, CENTER = both
- explicit STOP cancels the active motor timer/pattern
- BLE disconnect also stops active vibration before advertising again
- malformed BLE direction/intensity/pattern packets are rejected

### Laptop
- old BLE audio receiver / AI path moved to `legacy/`
- `ios_motor_bridge.py` added to poll existing `meit-ios` `/auto/status`
- current iOS `front/back` strings temporarily normalize to `CENTER`
- new native `left/center/right` strings are accepted without firmware changes
- completed event IDs are deduplicated
- event ID is marked delivered only after a successful BLE write, enabling retry after a failed write
- `send_motor_test.py` provides direct `left / center / right / stop` testing

### Repository cleanup
- old microphone/TDoA source, tests, display tools and schematics moved under `legacy/`
- active hardware docs rewritten for the two-motor design
- stale generated ESP-IDF `sdkconfig` and old dependency lock removed from version control
- Python CI test workflow added

## Preserved compatibility

- BLE peripheral name stays `MEIT-BELT`
- service UUID stays `01000000-1d9e-218f-9a4b-9c4e302a9d11`
- command characteristic UUID stays `04000000-1d9e-218f-9a4b-9c4e302a9d11`

## Validation completed in this revision

- Python direction normalization
- CMD v2 encode/decode round-trip
- STOP encode/decode
- completed dangerous-event extraction
- normal/unknown event suppression
- Python syntax compilation

Hardware validation still required:
- ESP-IDF 5.2.x build on the project workstation
- flash to the actual ESP32-S3
- LEFT / CENTER / RIGHT motor check
- battery/motor voltage confirmation
- end-to-end iPhone -> AI -> BLE -> vibration test
