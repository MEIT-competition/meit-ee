# Changelog

All notable project-level changes are recorded here.

## [2.0.0] - 2026-09-30

### Changed
- Replaced the active ESP32 2-microphone/TDoA pipeline with an iOS/Windows-driven direction pipeline.
- Reduced active haptic directions to `LEFT`, `CENTER`, `RIGHT` using two motors.
- Kept the existing `MEIT-BELT` BLE identity and service/command UUIDs.
- Introduced BLE CMD protocol version `0x02`.
- Rewrote active hardware and protocol documentation for the two-motor runtime.

### Added
- `laptop/ios_motor_bridge.py` for `meit-ios /auto/status -> BLE` integration.
- `laptop/send_motor_test.py` for direct `left`, `center`, `right`, `stop` motor checks.
- event-ID deduplication so polling does not replay the same completed event.
- retry-safe cursor behavior: an event is consumed only after successful GATT delivery.
- explicit motor STOP path and BLE-disconnect fail-safe stop.
- Python unit tests for direction mapping, CMD v2 and event filtering.
- architecture, iOS integration and migration documentation.
- GitHub Actions Python test workflow.

### Removed from active runtime
- ESP32 microphone capture.
- ESP32 TDoA direction estimation.
- ESP32-to-laptop BLE audio upload.
- old laptop BLE audio receiver / AI bridge path.
- active 4-mic / 8-motor assumptions.

The removed implementation is preserved under `legacy/` for reference and is not built or imported by the current runtime.

### Fixed
- STOP now cancels an in-progress motor pattern instead of allowing a stale timer to restart vibration.
- repeated standalone motor-test processes no longer depend on firmware-side sequence deduplication.
- BLE write failures no longer mark the AI event as delivered before the write succeeds.
- BLE disconnect stops any active haptic pattern.

## [1.x] - historical

Previous repository state used the ESP32 microphone/TDoA/audio-upload architecture. See Git history and `legacy/`.
