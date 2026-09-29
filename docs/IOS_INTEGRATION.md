# meit-ios integration contract

Reference repository: https://github.com/MEIT-competition/meit-ios

Verified against public `main` on 2026-09-30. This EE change does **not** modify iOS source.

## Endpoint used by EE

The Windows bridge exposes:

```text
GET http://127.0.0.1:8765/auto/status
```

EE only consumes the automatic-event status. Relevant fields are:

```json
{
  "last_event": {
    "event_id": "...",
    "outcome": "completed",
    "direction": "LEFT|RIGHT|FRONT|BACK|CENTER|UNKNOWN|...",
    "result": {
      "label": "horn|siren|crash|...",
      "confidence": 0.0,
      "danger": true
    }
  }
}
```

The bridge sends a vibration command only when:

1. `last_event` exists,
2. `outcome == "completed"`,
3. AI label is one of `horn`, `siren`, `crash`,
4. `danger` is not false,
5. direction normalizes to LEFT/CENTER/RIGHT, and
6. the event has not already been successfully delivered to the ESP32.

## Why polling is deduplicated

`/auto/status` keeps the latest completed event. Therefore an event ID must be remembered locally; otherwise a 100 ms polling loop would replay the same vibration continuously.

## Compatibility policy

The iOS team can continue updating `main`. As long as `/auto/status` preserves the fields above or the native direction becomes `left/center/right`, EE does not require an iOS code change. If the endpoint schema changes, update only `laptop/ios_motor_bridge.py` and its tests.
