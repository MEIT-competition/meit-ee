# meit-ai integration

Reference repository: <https://github.com/MEIT-competition/meit-ai>

`meit-ai` is owned by another part of the team and is never modified or vendored
here. It is imported from wherever it is checked out, via `$MEIT_AI_PATH`.

## Where inference actually runs

**Normally: in the `meit-ios` laptop bridge, not here.**

`meit-ios/bridge/meit_ai_adapter.py` already loads `meit-ai` and the bridge
publishes the finished verdict on `GET /auto/status`. `laptop/ios_motor_bridge.py`
consumes that. Running the model a second time on the EE side would burn a
TensorFlow session for no new information.

```text
iPhone x4  --Wi-Fi-->  meit-ios bridge  --> meit-ai (inference happens here)
                              |
                              |  GET /auto/status  (direction + label + confidence + danger)
                              v
                    laptop/ios_motor_bridge.py  --> haptic mapping --> BLE --> motors
```

`laptop/ai_runner.py` and `laptop/ai_motor_bridge.py` exist for the two cases that
path cannot cover:

1. **Bench replay.** Run the real model on a `.wav` and feel the resulting
   pattern — no iPhones, no Wi-Fi, no acoustics to set up.
2. **An AI-less iOS bridge.** If the iOS side is run without `meit-ai` and hands
   EE raw audio plus a direction, EE owns inference instead.

## The contract

Mirrored from `meit-ios/bridge/meit_ai_adapter.py`, verified against public `main`
on 2026-09-30, so both consumers expect the same interface and `meit-ai` needs no
per-consumer shim.

| Path in `$MEIT_AI_PATH` | What EE uses |
|---|---|
| `classifier/adapter.py` | `SR` (must be `16000`), `CLIP_SEC` (must be `2.5`), `CLASSES`, `load_model()`, `load_temperature()`, `predict_array(waveform)` |
| `decision/judge.py` | `judge(probabilities, direction, db)` — truthy alert or `None` |
| `model/saved_model/danger_sound_classifier/saved_model.pb` | the SavedModel |

`predict_array` takes a float32 waveform in −1..1 and returns
`(probabilities, dbfs)` where `probabilities` covers every entry of `CLASSES`.

**Audio payload: exactly 80 000 bytes of PCM16LE mono** — 40 000 samples at
16 kHz, i.e. 2.5 seconds. `fit_clip()` pads short input with leading silence and
trims long input **from the front**, keeping the tail: an automatic event is
triggered by a level rise, so the hazard is at the end of the buffer.

### Where the EE side is strict

| Check | Why |
|---|---|
| `SR`/`CLIP_SEC` must match exactly | a different input contract is a different model; resampling into it silently would produce confident nonsense |
| module origin must resolve inside `$MEIT_AI_PATH` | an unrelated installed `classifier` package would otherwise load and fail much later, confusingly |
| probabilities must cover `CLASSES`, each finite and in 0..1 | a malformed distribution must not become a confident alert |
| `dbfs` must be finite | `judge()` takes it as an argument |

### `judge` and direction

`judge()` is called with `direction=-1`, exactly as `meit-ios` calls it. Direction
comes from the iPhone role registry, never from the classifier, and `-1` keeps
the decision layer from applying its own direction logic on top.

If `decision/judge.py` is absent — `meit-ai` may ship the classifier first — EE
falls back to "`horn`/`siren`/`crash` are dangerous" and logs a warning once, so
nobody assumes the tuned thresholds are in play.

## Configuring the path

```powershell
$env:MEIT_AI_PATH = "C:\path\to\meit-ai"
python -m laptop.ai_motor_bridge
```

or per-invocation:

```bash
python -m laptop.ai_motor_bridge --meit-ai ~/src/meit-ai
```

`numpy` and the model's own runtime (TensorFlow) come from the `meit-ai`
checkout's requirements; see [`requirements-ai.txt`](../requirements-ai.txt).

## Testing before meit-ai exists

`meit-ai` was still unpublished when this code was written, which is why
`MockAIRunner` exists: it returns a fixed label so the whole pipeline — ingest,
haptic mapping, BLE, motors — is verifiable without a model. Both runners return
the same `AIResult`, so nothing downstream changes when the real one arrives.

```bash
# one-shot: a wav file, a direction, one vibration, exit
python -m laptop.ai_motor_bridge --wav horn.wav --direction back --mock-label horn

# the same with the real model
python -m laptop.ai_motor_bridge --wav horn.wav --direction back --meit-ai ~/src/meit-ai
```

## EE ingest API

`laptop/ai_motor_bridge.py` serves this on `127.0.0.1:8770` by default. It exists
for an iOS bridge that has a direction and audio but no AI.

### `POST /ee/audio`

Run inference, then vibrate.

```text
Content-Type: application/octet-stream
X-MEIT-Direction: front | right | back | left     (required)
X-MEIT-Event-Id:  any string                      (optional, for log correlation)

body: PCM16LE mono 16 kHz, ideally 80000 bytes (padded/trimmed otherwise)
```

### `POST /ee/event`

Skip inference; the caller already has a verdict.

```json
{"direction": "back", "label": "siren", "confidence": 0.91,
 "danger": true, "event_id": "optional"}
```

`danger` defaults to `true` when absent; only an explicit `false` suppresses.

### `POST /ee/stop` and `GET /ee/health`

`/ee/stop` cancels any pattern still running and is never rate-limited.
`/ee/health` reports counters, which runner is loaded, and whether the belt is
connected.

### Responses

```json
{"outcome": "queued",  "label": "siren", "confidence": 0.91,
 "direction": "BACK", "intensity": 89, "duration_ms": 1080}
{"outcome": "cooldown"}
{"outcome": "suppressed", "reason": "not_dangerous"}
{"error": "invalid_request", "detail": "direction 'unknown' must be front, right, back or left"}
```

`400` means the request was malformed — a missing or unusable direction is a
client error, because guessing one could point the wearer at the wrong side.
`500` with `inference_failed` means `meit-ai` raised.

### Concurrency

HTTP worker threads validate, run inference and build the command, then hand it
to an `asyncio.Queue`. **One** BLE task drains that queue, so there is a single
writer to the GATT characteristic. Inference is serialised behind a lock rather
than assuming the TensorFlow session is re-entrant.

The queue is bounded at four and drops the **oldest** pending command when full:
if hazards arrive faster than the belt can play them, the newest is the one that
matters. `--cooldown-ms` (default 1500) ignores further alerts shortly after one
is accepted, since a second alert 200 ms later is almost always the same physical
event and replaying it would only cut the first pattern short.

## If the contract changes

Only two files need editing, and each has tests:

| Change | Edit |
|---|---|
| `/auto/status` schema | `laptop/ios_motor_bridge.py` (`extract_event`) |
| `meit-ai` module API | `laptop/ai_runner.py` (`MeitAIRunner`) |

Neither the haptic mapping, the BLE protocol nor the firmware is involved.
