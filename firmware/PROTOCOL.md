# MEIT EE BLE protocol — CMD v2

Current data path:

```text
iPhone(s) -> meit-ios Windows bridge + meit-ai
           -> laptop/ios_motor_bridge.py
           -> BLE
           -> ESP32-S3
           -> LEFT / RIGHT vibration motors
```

The ESP32 no longer captures microphone audio or estimates direction.

## BLE identity

- Device name: `MEIT-BELT`
- Service UUID: `01000000-1d9e-218f-9a4b-9c4e302a9d11`
- CMD characteristic UUID: `04000000-1d9e-218f-9a4b-9c4e302a9d11`
- characteristic access: write

## CMD v2 packet

| Byte | Field | Value |
|---:|---|---|
| 0 | magic | `0xA5` |
| 1 | version | `0x02` |
| 2 | sequence | uint8 trace value |
| 3 | direction | `0=STOP`, `1=LEFT`, `2=CENTER`, `3=RIGHT` |
| 4 | intensity | `0..100` percent |
| 5 | timing-pair count | `1..4`; `0` only for STOP |
| 6... | timing pairs | repeated `{on_ms/10, off_ms/10}` uint8 |

Timing values are multiples of 10 ms and each encoded element is at most 2550 ms.

## Direction mapping

| CMD direction | Motor mask |
|---|---|
| STOP | both off; cancel active pattern |
| LEFT | LEFT motor only |
| CENTER | both motors simultaneously |
| RIGHT | RIGHT motor only |

Invalid direction, intensity, length or pattern fields are rejected by the GATT write handler.

## Laptop compatibility normalization

`laptop/protocol.py` accepts:

- `left` -> LEFT
- `right` -> RIGHT
- `center`, `centre` -> CENTER
- temporary current-iOS compatibility: `front`, `back` -> CENTER
- missing/`unknown`/other -> STOP/suppress

## Event semantics

`ios_motor_bridge.py` only sends a motor command for a **new** automatic event that is:

- `outcome == "completed"`
- dangerous label `horn`, `siren`, or `crash`
- not explicitly `danger == false`
- a valid normalized direction

The Windows `/auto/status` endpoint retains its most recent event. EE therefore remembers the last successfully delivered `event_id` to prevent replay on every poll.

The event cursor advances only after a successful GATT write. A failed write causes reconnection and permits retry of the same event.
