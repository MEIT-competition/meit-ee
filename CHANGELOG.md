# Changelog

All notable project-level changes are recorded here.

## [4.0.0] - 2026-09-30

Reduced the belt to the three directions iOS actually reports, and removed the
machinery that existed only to support a fourth.

### Removed

- **The `BACK` direction and its left-to-right sweep.** iOS estimates direction
  from a stereo microphone pair, which cannot separate front from back, and the
  system has no rear sensor — so `back` was never produced and the sweep never
  played. Keeping it meant carrying a wire format, a firmware code path and a
  test case for a sensation the product cannot generate.
- **CMD v3 and per-step motor masks.** Those existed *only* to render the sweep.
  With three directions, one motor mask covers a whole pattern, which is what CMD
  v2 already carries. The header drops from 8 bytes to 6, the parser loses its
  mask-bitfield handling, and `to_v2_command`, `encode_v3` and the
  `--protocol` flag are gone with it.
- `motor_play_masked_pattern()` from the firmware; `main.c` drives
  `motor_play_pattern()` with the direction's mask instead.

### Changed

- `laptop/protocol.py`: directions are `STOP`/`LEFT`/`CENTER`/`RIGHT`. `MotorStep`
  carries timing only, and the mask belongs to the command. `front` and `centre`
  are accepted as spellings of `center`; **`back` is rejected outright** and
  suppressed like `unavailable`, because the belt has no way to point behind the
  wearer and rendering it as anything else would mean something it does not.
- `DIR_FRONT` is now `DIR_CENTER` throughout, matching what iOS reports.
- Pattern budget 6 steps -> 4. A siren's three pulses is the longest pattern, so
  the worst-case packet is 12 bytes (14 at the cap) instead of 20.
- `verify_pipeline` checks `left`/`center`/`right` — seven stages instead of
  eight. It no longer asks the tester to confirm a sensation their setup cannot
  produce, which would only invite a false failure.
- Firmware host tests updated: the sequencer's sweep-ordering case became a
  two-pulse-with-gap case, and the host log stub now consumes its arguments so
  logging-only variables are not reported as unused and printf formats are
  actually checked.

### Compatibility

- **No reflash needed.** The wire format is CMD v2, which the firmware already
  flashed on the belt accepts. New laptop code drives an existing belt unchanged.
- BLE identity unchanged: name `MEIT-BELT`, same service and command UUIDs.
- Reflashing from this revision is still worth doing eventually, so the firmware
  on the device matches the repo — but nothing is broken until then.

## [3.2.0] - 2026-09-30

Verified the belt against the real `meit-ai` checkout and the real iOS stereo
direction values, and fixed what that turned up.

### Verified

- Loaded the actual `meit-ai` SavedModel and ran the full chain — audio ->
  classifier -> `judge()` -> haptic mapping -> CMD v3 packet. `laptop/ai_runner.py`
  needed no interface change: `predict_array`, `judge`, `CLASSES` and the
  16 kHz / 2.5 s contract all matched.
- meit-ai's own gates confirmed working through this code: `THRESHOLD = 0.4`,
  `DB_GATE = -50.0`, and `normal` rejection.
- Measured ~4 s to load the model, ~25 ms per inference.

### Added

- **`laptop/verify_pipeline.py`** — one command that verifies the whole back half on
  the real belt. It synthesises its own test clips, runs each through the real
  model, announces what the wearer should feel, then sends it; the clips meit-ai
  rejects are included on purpose, since a belt that stays silent for them is
  equally a pass. A final stage replays one hazard from each direction. Supports
  `--sounds` for real recordings, `--pause`, and `--dry-run` for no belt.
- `laptop/test_meit_ai_integration.py` — 12 tests that run the real model end to
  end, skipped automatically when `MEIT_AI_PATH` or TensorFlow is absent, so CI
  and model-less machines still pass.
- `laptop/test_verify_pipeline.py` — asserts the verification tool's announcements
  match the commands it actually sends, since a wrong announcement would make a
  real failure look like a pass.
- `haptic.intensity_from_dbfs()` — adopts meit-ai's deliberate rule that loudness,
  not confidence, sets vibration strength, re-scaled onto the belt's perceptible
  band. meit-ai's own 40..100 percent would bottom out at ~18 % duty, which bench
  testing showed cannot be felt.
- `build_command(..., dbfs=...)`: with a measured loudness the belt follows
  meit-ai's rule; without one (the `/auto/status` route publishes no dB) it falls
  back to confidence.

### Fixed

- **`--direction center` was rejected.** iOS stereo reports
  `left`/`center`/`right`/`unavailable`, but the CLI only accepted
  `front`/`right`/`back`/`left`, so the one direction iOS actually sends for
  "straight ahead" could not be used. `center` is now accepted by
  `ai_motor_bridge --direction` and by `send_motor_test`.

### Changed

- Raised every class's intensity floor after bench testing: ~28 % duty could not
  be felt on the real belt while ~47 % could, and the weakest pattern previously
  landed at 25 %. Floors are now 80 % (`horn`), 84 % (`siren`), 88 % (`crash`),
  putting every alert in the 37-46 % duty band. Laptop-side only, no reflash.
- `docs/AI_INTEGRATION.md` rewritten around the verified API, and now documents
  which layer owns which decision and why EE re-renders meit-ai's pattern rather
  than relaying it.

### Deliberately not changed

meit-ai's `decision/patterns.py` and `decision/intensity.py` outputs are not
forwarded to the motors. Their timings (80 ms bursts) and intensities (40 %) were
sized for the earlier 4-microphone design's 250 ms gating window and are
imperceptible on this belt. The class-to-pulse-count rule they encode — one pulse
for `crash`, two for `horn`, three for `siren` — is preserved exactly.

## [3.0.0] - 2026-09-30

The belt could receive a direction but could not *express* one properly: `front`
and `back` both collapsed into "both motors", and every alert played the same
fixed pattern at a fixed intensity regardless of which danger class the AI
reported or how confident it was. This release makes the vibration carry the
information that was already available.

### Added

- **`laptop/haptic.py` — the AI-result-to-sensation mapping.** Direction selects
  which motors and in what order, the danger class selects the pulse count, and
  confidence selects PWM duty. Three separate channels, so the cues do not
  interfere. Rationale, timings and constraints in `docs/HAPTIC_DESIGN.md`.
- **BLE CMD v3, with a motor mask per pattern step.** This is what makes `back`
  renderable as a left-to-right sweep and therefore distinguishable from `front`
  on a two-actuator belt. Worst case (a `back` `siren`) is exactly 20 bytes, the
  most an ATT Write Request carries on the default 23-byte MTU, so an alert never
  depends on MTU negotiation.
- `laptop/haptic_profile.json`, so timings and intensities can be tuned without
  touching code. A unit test asserts it never drifts from the Python default.
- `laptop/belt_client.py`: BLE scan/connect/reconnect, sequence numbering that
  survives reconnects, and CMD-characteristic verification so a belt on old
  firmware fails loudly instead of appearing healthy.
- `laptop/ai_runner.py`: loads the unmodified `meit-ai` checkout per the contract
  in `meit-ios/bridge/meit_ai_adapter.py`, plus a mock runner so the whole
  pipeline is testable before `meit-ai` is published.
- `laptop/ai_motor_bridge.py`: standalone runtime for bench replay
  (`--wav x.wav --direction back`) and for an iOS bridge running without AI
  (`POST /ee/audio`, `POST /ee/event`, `POST /ee/stop`, `GET /ee/health`).
- `laptop/haptic_preview.py`: print every direction x class pattern, its ASCII
  timeline and its encoded bytes, with no hardware at all.
- `firmware/main/cmd_parse.c/.h`: packet validation extracted from the NimBLE
  plumbing so it compiles and runs on a host.
- `firmware/tests/run_cmd_parse_tests.py`: compiles the real C parser and checks it
  against golden packets generated by `laptop/protocol.py`. The Python encoder and
  the firmware can no longer drift apart silently.
- `firmware/tests/run_motor_host_tests.py`: the sequencer host test, revived from
  `legacy/` for the current API, including a `back_sweep_is_sequential` case that
  fails if a sweep degrades into "both motors at once".
- 180 Python unit tests, none needing a belt, a board or a Bluetooth adapter.
- `docs/HAPTIC_DESIGN.md` and `docs/AI_INTEGRATION.md`.

### Changed

- `laptop/protocol.py` rewritten: four directions (`front`/`right`/`back`/`left`),
  `MotorStep`/`MotorCommand` value types with validation mirroring the firmware's,
  and `to_v2_command()` for an explicit, logged downgrade.
- `laptop/ios_motor_bridge.py` now delegates the pattern to `haptic.py` and the
  transport to `belt_client.py`, and logs **why** an event was suppressed instead
  of dropping it silently.
- `laptop/send_motor_test.py`: `--raw` for a plain wiring-check pulse, real alert
  patterns per class, `all` to walk every direction x class combination, and
  `--dry-run` to print them without BLE.
- Pattern budget raised from 4 to 6 steps, which is what a `back` `siren` needs.
- Firmware `motor.c` gained `motor_play_masked_pattern()` and now rejects a
  pattern containing a step that drives no motor; its log line prints every
  step's mask, since that sequence is what distinguishes `BACK` from `FRONT`.
- `motor_self_test` ends with a real sweep, so the one direction that cannot be
  judged from a per-motor check is still verified on the bench.
- CI runs the Python suite plus both firmware host tests.

### Compatibility

- BLE identity unchanged: name `MEIT-BELT`, same service and command UUIDs.
- **The firmware still accepts CMD v2**, so a laptop running `--protocol 2` works.
- **A belt must be reflashed to feel all four directions.** Until then
  `--protocol 2` keeps pulse counts and `left`/`right`; `back` is felt as `front`
  and every downgrade is logged as a warning naming both directions.
- `laptop.test_ios_motor_bridge` still exists; tests are now discovered with
  `python -m unittest discover -s laptop -p "test_*.py" -t .`

### Hardware validation still required

- ESP-IDF 5.2.x build and flash on the project workstation
- `send_motor_test left/right/front/back --raw` on the real belt
- `send_motor_test all` to confirm the twelve patterns feel distinguishable
- battery/motor voltage confirmation before raising intensity
- end-to-end iPhone -> meit-ai -> BLE -> vibration test

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
