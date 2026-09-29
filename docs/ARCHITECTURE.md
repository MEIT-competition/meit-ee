# Architecture

## Current runtime

```text
iPhone(s)
   |
   | Wi-Fi / HTTP
   v
meit-ios Windows bridge + existing meit-ai
   |
   | GET /auto/status
   v
laptop/ios_motor_bridge.py
   |
   | BLE CMD v2
   v
ESP32-S3 (MEIT-BELT)
   |
   +--> LEFT motor  (GPIO21)
   +--> RIGHT motor (GPIO13)
```

### Responsibility split

**iOS / meit-ios Windows bridge**
- microphone input and RMS reporting
- source/direction selection
- dangerous-sound AI inference through the existing `meit-ai`
- automatic-event lifecycle and `event_id`

**EE laptop bridge**
- polls `GET /auto/status`
- accepts only completed dangerous events (`horn`, `siren`, `crash`)
- normalizes direction to `LEFT`, `CENTER`, or `RIGHT`
- suppresses missing/unknown direction
- sends one BLE command per completed event
- reconnects when the ESP32 BLE link drops

**ESP32**
- BLE peripheral named `MEIT-BELT`
- validates CMD v2 packets
- translates direction into a two-motor mask
- executes a bounded vibration pattern
- stops active vibration on explicit STOP and BLE disconnect

## Direction compatibility

| Input string | EE output |
|---|---|
| `left` | `LEFT` |
| `center`, `centre` | `CENTER` |
| `right` | `RIGHT` |
| current iOS compatibility: `front`, `back` | `CENTER` |
| `unknown`, missing, other | suppressed / `STOP` |

`front` and `back` collapsing to `CENTER` is a compatibility layer only. When iOS `main` emits native `left/center/right`, no EE change is required.

## Event deduplication

`/auto/status` retains the latest event, so polling the endpoint without a cursor would repeatedly vibrate for the same AI result. The EE bridge stores the last successfully delivered `event_id` and sends a motor command only for a new completed dangerous event.

The cursor advances **after** a successful BLE GATT write. If the BLE write fails, the event remains eligible for retry after reconnect instead of being silently lost.
