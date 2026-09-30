# Architecture

## Runtime

```text
iPhone  (stereo mic -> left / center / right / unavailable)
   |
   |  Wi-Fi / HTTP: RMS reports, then one 2.5 s audio snapshot per event
   v
meit-ios laptop bridge
   |     |
   |     +-- meit-ai  (classifier + decision layer)
   |
   |  GET /auto/status : direction + {label, confidence, danger}
   v
laptop/ios_motor_bridge.py
   |     |
   |     +-- laptop/haptic.py       : result -> motor pattern
   |     +-- laptop/belt_client.py  : BLE scan / connect / reconnect
   |
   |  BLE CMD v2 (one 8-14 byte write per alert)
   v
ESP32-S3 "MEIT-BELT"
   |     |
   |     +-- cmd_parse.c : validate untrusted bytes
   |     +-- motor.c     : pattern sequencer
   |
   +--> LEFT motor  (GPIO21)
   +--> RIGHT motor (GPIO13)
```

## Responsibility split

**iPhones + `meit-ios` laptop bridge**
- microphone capture and RMS reporting
- the stereo direction estimate, including its own margin gate
- danger-sound inference through `meit-ai`
- the automatic-event lifecycle and `event_id`

**EE laptop bridge** (this repository)
- polls `GET /auto/status`
- accepts only completed dangerous events with a usable direction
- **translates the result into a sensation** — which motors, how many pulses, how
  hard (`laptop/haptic.py`)
- encodes and writes one BLE command per event
- deduplicates retained events and reconnects when the link drops

**ESP32** (this repository)
- BLE peripheral named `MEIT-BELT`
- validates CMD packets and refuses anything malformed
- drives two motors from the direction's mask
- plays a bounded pattern, stops on explicit STOP and on BLE disconnect

The belt makes **no decisions** about what to alert on. That keeps every policy
question — thresholds, classes, direction confidence — on the laptop, where it can
be changed without reflashing.

## Where each decision lives

| Decision | Owner | Change it in |
|---|---|---|
| is this sound dangerous? | `meit-ai` decision layer | `meit-ai` |
| which direction is it from? | `meit-ios` role registry | `meit-ios` |
| should the belt alert at all? | EE | `ios_motor_bridge.suppression_reason` |
| what should it feel like? | EE | `laptop/haptic.py`, `haptic_profile.json` |
| how is that transmitted? | EE | `laptop/protocol.py` + `firmware/main/cmd_parse.c` |
| how are the motors driven? | firmware | `firmware/main/motor.c` |

## Direction mapping

| iOS reports | Belt |
|---|---|
| `left` | left motor |
| `right` | right motor |
| `center` / `centre` | both motors together |
| `front` | both motors — accepted as a spelling of `center` |
| `back`, `unavailable`, missing, other | suppressed, nothing sent |

`unavailable` is suppressed rather than turned into a generic buzz. iOS has
already decided the direction is not trustworthy, and a directionless alert trains
the wearer to ignore the belt.

## Event deduplication

`/auto/status` retains its most recent event indefinitely, so a 100 ms poll loop
without a cursor would vibrate continuously for one hazard. The bridge remembers
the last **successfully delivered** `event_id`.

Two details matter:

- The first successful status read **adopts** whatever event is already retained,
  so starting the bridge never replays a hazard from before it was running.
- The cursor advances **after** the GATT write succeeds. A failed write leaves the
  event eligible, so it is retried after reconnecting rather than silently lost.
- A *suppressed* event is consumed immediately — no write is needed — otherwise
  the same retained event would be re-evaluated and re-logged every poll.

## Concurrency

The primary bridge is single-tasked: one asyncio loop polls HTTP (via a thread for
the blocking `urlopen`) and writes BLE.

The standalone `ai_motor_bridge` has HTTP worker threads, so it separates them
strictly: threads validate, run inference and build commands, then hand them to an
`asyncio.Queue`; **one** BLE task drains it. Inference is serialised behind a lock
rather than assuming the TensorFlow session is re-entrant.

On the ESP32, one FreeRTOS task owns all sequencer state and every timer
start/stop. BLE callbacks and `esp_timer` callbacks only enqueue messages, so no
sequencer field needs a lock, an atomic or a `volatile` qualifier.

## Testing strategy

Everything runs without hardware:

| Layer | Test |
|---|---|
| haptic mapping | `laptop/test_haptic.py` — classes and directions stay mutually distinguishable |
| wire format | `laptop/test_protocol.py` — round trips, v2 downgrade, rejections |
| BLE transport | `laptop/test_belt_client.py` — reconnect, sequence continuity, fake adapter |
| primary bridge | `laptop/test_ios_motor_bridge.py` — cursor and suppression rules |
| ingest + HTTP | `laptop/test_ai_motor_bridge.py` — cooldown, queue policy, endpoints |
| meit-ai adapter | `laptop/test_ai_runner.py` — audio contract, guard clauses, mock |
| **C parser** | `firmware/tests/run_cmd_parse_tests.py` — real `cmd_parse.c` vs. Python golden packets |
| **C sequencer** | `firmware/tests/run_motor_host_tests.py` — real `motor.c` on a virtual clock, 2 tick rates |

The two firmware host tests are what make the ESP32's behaviour reviewable in CI
instead of only on the bench. The cross-language golden-packet test is the one
that keeps `laptop/protocol.py` and `firmware/main/cmd_parse.c` honest about each
other.

`laptop/verify_pipeline.py` is the human-in-the-loop counterpart: one command that
runs the real model, announces what the wearer should feel, and plays it on the
belt — including the clips meit-ai rejects, where staying silent is the pass.
