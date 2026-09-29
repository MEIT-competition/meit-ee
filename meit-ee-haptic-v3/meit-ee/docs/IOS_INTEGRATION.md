# meit-ios integration contract

Reference repository: <https://github.com/MEIT-competition/meit-ios>

Verified against public `main` on 2026-09-30. **No iOS source is modified.** EE
consumes the existing laptop-bridge interface.

## Endpoint EE consumes

```text
GET http://127.0.0.1:8765/auto/status
```

Only the automatic-event status is used. The fields EE reads:

```json
{
  "last_event": {
    "event_id": "uuid",
    "outcome": "completed",
    "direction": "front|right|back|left|unknown",
    "direction_margin_db": 4.2,
    "result": {
      "label": "horn|siren|crash|normal|...",
      "confidence": 0.0,
      "danger": true
    }
  }
}
```

`direction` is the **trigger-time** direction, and EE prefers it over any live
direction elsewhere in the response. The wearer must be told where the sound
*was*, not where the loudest phone is now. `result.direction` is used only as a
fallback when the event-level field is absent.

`direction_margin_db` is read for logging only. EE does **not** apply its own
direction gate: the bridge already reports `unknown` whenever its role/margin
conditions are not met, so re-gating here would double-suppress.

## When a vibration is sent

All of these must hold:

1. `last_event` exists and is an object,
2. `outcome == "completed"`,
3. `result.danger` is not `false` — this is `meit-ai`'s decision-layer verdict,
4. `result.label` is one of `horn`, `siren`, `crash`,
5. `direction` normalizes to `front`, `right`, `back` or `left`, and
6. the `event_id` has not already been delivered successfully.

Anything else is **logged with its reason** and consumed without a write. Silent
drops were removed deliberately: at a demo, "the belt did nothing" has to be
explainable from the log.

## Why polling needs a cursor

`/auto/status` retains its most recent event indefinitely. Polling it every 100 ms
without remembering anything would replay the same vibration continuously.

EE stores the last successfully delivered `event_id`, and:

- the **first** successful read adopts whatever is already retained, so starting
  the bridge never fires for a hazard from before it was running;
- the cursor advances only **after** the GATT write succeeds, so a write that fails
  mid-drop leaves the event eligible for retry after reconnecting;
- a **suppressed** event is consumed immediately, since no write is needed and
  re-evaluating it every poll would only flood the log.

## Four roles, two motors

`meit-ios` registers four iPhones as `front`, `right`, `back` and `left`, and
reports the loudest as the direction. The belt has two actuators, so:

| iOS role | Belt rendering | CMD version |
|---|---|---|
| `left` / `right` | that motor only | v2 and v3 |
| `front` | both motors, simultaneously | v2 and v3 |
| `back` | left→right sweep | **v3 only** |

Under `--protocol 2` (a belt that has not been reflashed) `back` folds into
`front` and the downgrade is logged as a warning naming both directions. See
[`HAPTIC_DESIGN.md`](HAPTIC_DESIGN.md).

## Compatibility policy

The iOS team can keep changing `main`. As long as `/auto/status` keeps the fields
above, EE needs no change. If the schema moves, only
[`laptop/ios_motor_bridge.py`](../laptop/ios_motor_bridge.py) (`extract_event`) and
its tests need editing — not the haptic mapping, not the BLE protocol, not the
firmware.

`normalize_direction` also already accepts `center`/`centre`, so a future
three-way estimator needs no EE change either.

## Where inference runs

In the `meit-ios` bridge, not here — it loads `meit-ai` itself and publishes the
verdict. EE deliberately does not re-run the model. For the cases where EE does
need its own inference (bench replay, or an AI-less iOS bridge), see
[`AI_INTEGRATION.md`](AI_INTEGRATION.md).
