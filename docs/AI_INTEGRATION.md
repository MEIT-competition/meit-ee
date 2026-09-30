# meit-ai integration

Reference repository: <https://github.com/MEIT-competition/meit-ai>

**Verified against the real checkout on 2026-09-30.** The model was loaded, run and
driven all the way to a BLE packet; `laptop/test_meit_ai_integration.py` is that
verification, kept as a test. `meit-ai` itself is never modified or vendored — it is
imported from wherever it is checked out.

## Who decides what

This is the important part, because `meit-ai` and this repository both contain a
"decide the vibration" layer and they must not both be in charge.

| Decision | Owner | Where |
|---|---|---|
| what sound is it (`horn`/`siren`/`crash`/`normal`) | **meit-ai** | `classifier/adapter.py` |
| how confident, how loud (dBFS) | **meit-ai** | `classifier/adapter.py` |
| **is it worth alerting at all** | **meit-ai** | `decision/judge.py` |
| which direction | **iOS** (stereo) | `StereoDirectionEstimator.swift` |
| what the wearer actually feels on *this* belt | **EE** | `laptop/haptic.py` |

`meit-ai`'s verdict is authoritative: if `judge()` returns `None`, the belt stays
silent. EE never second-guesses that.

### Why EE re-renders instead of relaying meit-ai's pattern

`meit-ai`'s `decision/patterns.py` and `decision/intensity.py` also produce a
pattern and an intensity. EE deliberately does **not** forward those raw values,
because bench testing on the real belt showed they cannot be felt:

| meit-ai output | On this belt | Result |
|---|---|---|
| `horn` pattern `[[80, 60], [80, 0]]` | 80 ms bursts | shorter than a coin motor's spin-up — felt as nothing |
| `intensity = 40` (a quiet clip) | duty 47/255 ≈ **18 %** | measured as not perceptible (28 % already wasn't) |

Those numbers were chosen for the earlier 4-microphone TDoA design, whose 250 ms
gating period (`GATING_MS`) forced every pattern short. That constraint does not
exist on the current iOS-stereo path, and this belt's duty is capped at ~47 % to
keep a 3 V motor safe on a 6.4 V rail.

**The philosophies already agree**, which is why re-rendering loses nothing:
`meit-ai`'s `PATTERNS_FULL` uses one pulse for `crash`, two for `horn`, three for
`siren` — exactly the class-to-pulse-count rule in
[`HAPTIC_DESIGN.md`](HAPTIC_DESIGN.md). EE keeps that rule and only stretches the
timings to what the hardware can render.

### What EE adopts from meit-ai's decision layer

`decision/intensity.py` makes a deliberate choice worth keeping:

> 세기는 dBFS만으로 결정. 확신도는 알릴지 말지만 판단한다.

Loudness — not confidence — sets how hard the belt buzzes; confidence only gates
whether to alert, inside `judge()`. EE follows that, re-scaling meit-ai's
−40…−10 dBFS range onto each class's belt-tested intensity band instead of its
40…100 percent (which would bottom out at an imperceptible 18 % duty):

| dBFS | `horn` | `siren` | `crash` |
|---:|---:|---:|---:|
| ≤ −40 | 80 % (37 % duty) | 84 % (38 %) | 88 % (40 %) |
| −30 | 87 % (40 %) | 89 % (41 %) | 92 % (42 %) |
| −20 | 93 % (43 %) | 95 % (44 %) | 96 % (44 %) |
| ≥ −10 | 100 % (46 %) | 100 % (46 %) | 100 % (46 %) |

The band is narrow because of the 3 V duty cap, not by choice. Widening it means
changing the motor supply — see the electrical note in [`../README.md`](../README.md).

`build_command(..., dbfs=...)` selects this path. Without a `dbfs` (the
`/auto/status` route publishes none) it falls back to confidence, still inside the
same perceptible band.

## The verified contract

| Path in `$MEIT_AI_PATH` | What EE uses | Verified |
|---|---|---|
| `classifier/adapter.py` | `SR == 16000`, `CLIP_SEC == 2.5`, `CLASSES`, `load_model()`, `load_temperature()`, `predict_array(wav) -> (probs, dbfs)` | ✅ |
| `decision/judge.py` | `judge(probs, direction, db) -> dict \| None` | ✅ |
| `model/saved_model/danger_sound_classifier/saved_model.pb` | the SavedModel | ✅ |

`CLASSES` is `["horn", "siren", "crash", "normal"]`. `predict_array` takes a
float32 waveform in −1…1 and returns every class's probability plus the clip's
dBFS. Neither `classifier/` nor `decision/` ships an `__init__.py`; they import
as namespace packages, which EE's loader handles.

Measured on a laptop-class CPU: **~4 s** to load the model once, **~25 ms** per
inference after warm-up.

### meit-ai's own gates, observed

| Input | Outcome | Why |
|---|---|---|
| loud tone | `siren` conf 0.90, **danger** | above `THRESHOLD = 0.4` |
| same tone at −59 dBFS | **not dangerous** | `DB_GATE = -50.0` |
| digital silence | **not dangerous** | top confidence 0.31 < 0.4 |
| `normal` on top | **not dangerous** | `judge()` rejects `normal` |

### Audio payload

**Exactly 80 000 bytes of PCM16LE mono** — 40 000 samples at 16 kHz, 2.5 s.
`fit_clip()` pads short input with leading silence and trims long input **from the
front**, keeping the tail, because an event is triggered by a level rise and the
hazard is at the end of the buffer.

### Where EE is strict

| Check | Why |
|---|---|
| `SR`/`CLIP_SEC` must match exactly | a different contract is a different model; silently resampling would produce confident nonsense |
| module origin must resolve inside `$MEIT_AI_PATH` | an unrelated installed `classifier` package would otherwise load and fail confusingly later |
| probabilities must cover `CLASSES`, each finite and in 0…1 | a malformed distribution must not become a confident alert |
| `dbfs` must be finite | `judge()` takes it, and it now also sets the strength |

`judge()` is called with `direction=-1`, exactly as `meit-ios` calls it: direction
comes from iOS, never from the classifier, and `-1` keeps the decision layer from
applying direction logic of its own. If `decision/judge.py` is ever absent, EE
falls back to "`horn`/`siren`/`crash` are dangerous" and warns once.

## Direction, from iOS stereo

`StereoDirectionEstimator.swift` reports **`left`, `right`, `center`, `unavailable`** —
three usable directions, no `back`. EE maps them without any change:

| iOS | Belt |
|---|---|
| `left` | left motor |
| `right` | right motor |
| `center` | both motors together |
| `unavailable` | **silent** — nothing is sent |

`center` is accepted everywhere a direction is named, including
`--direction center` and `send_motor_test center`. The `BACK` sweep stays in the
protocol for the 4-phone role path and simply never fires on the stereo path.

Suppressing `unavailable` is deliberate: iOS already decided the direction is not
trustworthy, and a directionless buzz trains the wearer to ignore the belt.

## Setup

```powershell
git clone https://github.com/MEIT-competition/meit-ai.git
pip install tensorflow librosa numpy        # meit-ai's own runtime
```

`numpy` is required; TensorFlow's version is meit-ai's choice, so install what its
README or `model/requirements.txt` specifies rather than pinning it here. See
[`../requirements-ai.txt`](../requirements-ai.txt).

## Running it

```powershell
# bench: one wav through the real model, one vibration, exit
python -m laptop.ai_motor_bridge --wav siren.wav --direction center --meit-ai C:\src\meit-ai

# ingest server: iOS posts audio + direction, belt vibrates continuously
$env:MEIT_AI_PATH = "C:\src\meit-ai"
python -m laptop.ai_motor_bridge

# no meit-ai yet? exercise everything except the model
python -m laptop.ai_motor_bridge --mock-label siren
```

## Verifying it

Two levels, and they answer different questions.

**On the belt, by feel** — the one to use when you want to know whether the product
works:

```powershell
python -m laptop.verify_pipeline --meit-ai C:\src\meit-ai
```

It synthesises its own test clips (nothing to download), runs each through the real
model, announces what you should feel, then sends it. Loud clips must buzz with the
announced pulse count; the quiet and silent ones must produce **nothing**, which is
equally a pass — it proves meit-ai's gates are actually gating. A final stage
replays one hazard from `left`, `center` and `right` so you can confirm the right
motors move. Add `--sounds <folder>` to use real recordings, `--pause` to step
through one at a time, or `--dry-run` to see the verdicts with no belt attached.

**In CI, by assertion** — the one that stops a regression:

```powershell
$env:MEIT_AI_PATH = "C:\src\meit-ai"
python -m unittest laptop.test_meit_ai_integration -v
```

That runs the real model and asserts the whole chain: inference, meit-ai's gates,
the perceptibility floor on every burst, direction-to-motor mapping, and a
round-tripped CMD packet. Without `MEIT_AI_PATH` it skips instead of failing.

Neither checks the model's *accuracy* on real-world sounds — that is meit-ai's own
`eval/` scripts and `model/TRAINING_REPORT.md`.

## EE ingest API

Served on `127.0.0.1:8770` by default, for an iOS bridge that has audio and a
direction but no AI of its own.

### `POST /ee/audio` — run inference, then vibrate

```text
Content-Type: application/octet-stream
X-MEIT-Direction: left | center | right        (required)
X-MEIT-Event-Id:  any string                   (optional, for log correlation)

body: PCM16LE mono 16 kHz, ideally 80000 bytes (padded/trimmed otherwise)
```

### `POST /ee/event` — skip inference, caller already has a verdict

```json
{"direction": "center", "label": "siren", "confidence": 0.91,
 "danger": true, "event_id": "optional"}
```

`danger` defaults to `true`; only an explicit `false` suppresses. With no audio
there is no dBFS, so intensity comes from confidence.

### `POST /ee/stop`, `GET /ee/health`

`/ee/stop` cancels a running pattern and is never rate-limited. `/ee/health`
reports counters, which runner is loaded, and whether the belt is connected.

### Responses

```json
{"outcome": "queued", "label": "siren", "confidence": 0.888,
 "direction": "FRONT", "intensity": 100, "duration_ms": 1080}
{"outcome": "suppressed", "reason": "not_dangerous", "label": "crash"}
{"outcome": "cooldown"}
{"error": "invalid_request", "detail": "direction 'unavailable' must be left, center or right"}
```

`400` means the request was malformed — a missing or unusable direction is a client
error, because guessing one could point the wearer at the wrong side. `500` with
`inference_failed` means `meit-ai` raised.

### Concurrency

HTTP worker threads validate, run inference and build the command, then hand it to
an `asyncio.Queue`; **one** BLE task drains it, so there is a single writer to the
GATT characteristic. Inference is serialised behind a lock rather than assuming the
TensorFlow session is re-entrant.

The queue holds four and drops the **oldest** when full: if hazards arrive faster
than the belt can play them, the newest is the one that matters. `--cooldown-ms`
(default 1500) ignores further alerts just after one is accepted, since a second
alert 200 ms later is almost always the same physical event.

## If the contract changes

| Change | Edit | Tests |
|---|---|---|
| `meit-ai` module API | `laptop/ai_runner.py` | `test_ai_runner.py`, `test_meit_ai_integration.py` |
| `/auto/status` schema | `laptop/ios_motor_bridge.py` (`extract_event`) | `test_ios_motor_bridge.py` |
| how it should feel | `laptop/haptic.py`, `haptic_profile.json` | `test_haptic.py` |

Neither the BLE protocol nor the firmware is involved in any of those.
