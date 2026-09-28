# BLE protocol contract with meit-ai

This is the single source of truth for the byte layout. Both repos should
link here; there is no shared code between them, so this file is the only
thing keeping them in sync. If either side changes, update this file in the
same commit.

Checked against meit-ai as of the `main.py` / `decision/judge.py` /
`classifier/adapter.py` snapshot reviewed on the electronics side. If
meit-ai's `README.md` "출력 포맷" section changes, re-check this file.

## meit-ai's actual output (decision/judge.py)

```python
{
    "direction": 0,                        # 0-7, 0=front clockwise, -1=unknown
    "intensity": 85,                       # 40-100
    "pattern": [[100, 50], [100, 0]],       # [[on_ms, off_ms], ...], 1-3 pairs today
    "pattern_name": "siren",
    "sound_class": "siren",                # one of horn/siren/crash (never "normal")
    "confidence": 0.949,
}
```
`judge()` returns `None` for normal sound or below `THRESHOLD`/`DB_GATE` --
nothing is sent to the belt in that case.

## AUDIO (notify, MCU -> laptop)

16 kHz mono PCM16, little-endian, sent only when the loudness gate is
exceeded (loudest of the 4 mics, not just FRONT -- see `main.c`'s
`capture_task`).
**Chunked** -- a 2.56 s/40960-sample clip (~80 KB, 342 chunks at the 240-byte
payload cap) is far larger than one ATT
notification, so each notify is:

| byte | meaning |
|---|---|
| 0 | `event_id` (matches the DIR notify sent just before this clip started) |
| 1 | chunk index, 0-based, wraps past 255. **Use this to detect loss, not just order**: a chunk can be dropped after 3 failed retries (see `ble_svc.c`), so the receiver should check the sequence is contiguous (accounting for wraparound) and, on a gap, either discard the whole event or explicitly mark it as having missing audio before handing it to the classifier -- silently concatenating whatever arrived hands the model audio with unannounced holes in it. BLE still preserves per-characteristic notification order, so this is about detecting a hole, not reordering. |
| 2 | flags, bit0 = last chunk of this event |
| 3.. | PCM16 samples, little-endian |

Clip length is fixed at 2.56 s (40960 samples, `CLIP_FRAMES`=120 in
`config.h`); meit-ai `classifier/adapter.py` keeps the first 2.5 s
(`CLIP_SEC`). (Historical: this was ~0.5 s in earlier firmware.) **This was checked against the actual
deployed model, not assumed**: `model/saved_model/danger_sound_classifier`'s
signature takes `audio: shape=(None,)` (variable length), and feeding it a
short clip with or without zero-padding to 4.5 s gave the same result in a
synthetic test (not tracked in this repo -- re-run and record the result
here if this claim needs to be re-checked). If real horn/siren/crash
recordings later show a real difference, revisit this -- the synthetic test
is not a substitute for testing on the actual target sounds.

meit-ai's `classifier/adapter.py::predict()` takes a **file path**
(`librosa.load`) and is not used for live BLE buffers. The live path is
`laptop/ai_bridge.py::run_live_ai()`, which calls
`classifier.adapter.predict_array()` and then `decision.judge.judge()`.
That `judge()` result is authoritative for the live CMD path.
`model/inference.py::classify_clip()` still exists for benchmark/eval use and
has its own legacy intensity rule, but it is explicitly **not** used by the
live BLE pipeline.

## DIR (notify, MCU -> laptop)

4 bytes, sent once per event, after a short multi-frame direction vote
(~125 ms, see `TDOA_VOTE_FRAMES`) and right before the matching AUDIO clip
starts:

| byte | meaning |
|---|---|
| 0 | `event_id` -- the same value tags this event's AUDIO chunks and the CMD response for it |
| 1 | direction: 0-7, or `0xFF` = unknown (maps to meit-ai's `direction: -1`) |
| 2 | confidence, `uint8(conf * 255)`. Exactly 0 iff byte 1 is `0xFF`. |
| 3 | RMS loudness in dBFS (of whichever channel was loudest), `int8`, clamped to [-128, 127] |

**Direction-confidence and danger-detection are independent.** Byte 1 can be
`0xFF` while the belt still sends the audio clip and expects a vibration
command back -- an ambiguous direction must not suppress a real danger
sound. See firmware `main.c` for why (this used to be a bug: the MCU
silently dropped the whole event whenever TDoA confidence was low, which
fights meit-ai's own Recall-first design).

Whoever writes the laptop-side BLE receiver: decode byte 1 as `-1` when it
equals `0xFF`, else as the plain int, before calling `judge()`. Track
`event_id` and pass it back unchanged in the CMD write.

## CMD (write, laptop -> MCU)

Sent after `judge()` returns non-`None`. `direction` as a literal value is
**not** included -- the MCU looks it up locally from `event_id` (a small
ring buffer, see `main.c`; this replaced a single global that had a real
race -- see `main.c`'s `event_table` comment). `pattern_name` and the string form of
`sound_class` are also dropped; a numeric id carries the same information
for on-device logging.

| offset | field | notes |
|---|---|---|
| 0 | `event_id` | echo of the DIR/AUDIO this command responds to |
| 1 | `intensity` | 0-100, taken as-is from meit-ai's `intensity` |
| 2 | `sound_class` | 0=horn, 1=siren, 2=crash, `0xFF`=none. Order matches `classifier/adapter.py` `CLASSES` |
| 3 | `n_pairs` | 1..4 |
| 4.. | `n_pairs` x `{on_ms/10 : u8, off_ms/10 : u8}` | e.g. `[[100,50],[100,0]]` -> `10,5,10,0` |

Max size 4 + 2x4 = 12 bytes, fits one ATT packet without MTU negotiation.

If `event_id` doesn't match anything in the MCU's recent-event history
(evicted, never recorded, or a stale/duplicate write), firmware keeps all
motors **OFF** (`mask = 0x00`) as a fail-safe. An invalid stored direction
(neither `0xFF` nor 0..7) is handled the same way. These are fault/stale-state
cases and must not be conflated with a genuine TDoA `DIR_UNKNOWN` result.

### Unknown-direction behavior

If DIR reported `0xFF` and a CMD still arrives (danger sound, no resolved
direction), firmware plays a dedicated four-cardinal sweep instead of the
old `0xFF` all-motors-on fallback:

`FRONT (0) -> RIGHT (2) -> BACK (4) -> LEFT (6)`

Exactly one motor is active at a time. The current fixed sweep timing is
`UNKNOWN_SWEEP_ON_MS = 80 ms` with `UNKNOWN_SWEEP_OFF_MS = 40 ms` between
cardinal motors (the final step has no trailing gap). The CMD's `intensity`
is used for the sweep, while the CMD's normal vibration `pattern` steps are
not used for this special unknown-direction alert.

This behavior is implemented in `motor_play_unknown_pattern()` and avoids
the simultaneous eight-motor current spike of the previous fallback while
still giving the wearer a distinct "danger detected, direction unresolved"
alert.

## Units

Both sides use dBFS (digital full scale, negative, `20*log10(rms)` on a
[-1, 1] signal) -- verified to match, not assumed:
`classifier/adapter.py::measure_db()` computes it the same way the firmware
does in `audio_capture.c`. No conversion needed at the boundary.

**Gate levels must stay ordered.** The MCU's `RMS_GATE_DBFS` (`config.h`) is
a pre-gate only, to cut BLE traffic during real silence -- it must stay
LOOSER (more negative) than meit-ai's `DB_GATE` (`decision/judge.py`), or
the MCU silently drops sounds the AI would have accepted before the AI ever
sees them. This was actually backwards in an earlier snapshot (-45 vs
meit-ai's -50) and is fixed to -60 here, but neither value has been checked
against real hardware yet -- re-verify the relationship holds once either
side's number changes.

## Known open items

The current code-level EE↔AI contract is aligned for the MVP, but the following
items still require hardware validation or manual cross-checks:

- [x] Live AI path: `laptop/ai_bridge.py::run_live_ai()` uses meit-ai
      `classifier.adapter.predict_array()` + `decision.judge.judge()`.
      `classify_clip()` is not on the live path.
- [x] AI intensity policy: the live `decision/intensity.py` no longer applies
      the old `LOW_CONF_RATIO`; confidence gates alert/no-alert in `judge()`,
      while dBFS drives intensity.
- [ ] Both sides: `GATING_MS` is currently 250 ms on both repos, but the value
      is still duplicated rather than shared. If either side changes it,
      re-run the contract check and review MCU cooldown/pattern timing.
- [ ] Electronics side: **AUDIO chunk count depends on negotiated ATT MTU.**
      If MTU negotiation stays near the BLE default, a 40960-sample clip will
      require many more chunks and may be too slow for a live alert. Confirm on
      real hardware that `ble_att_set_preferred_mtu(247)` actually results in
      a larger negotiated MTU (`ble_att_mtu()`).
- [ ] Hardware end-to-end: validate real `DIR + AUDIO -> AI -> CMD -> motor`
      with the ESP32-S3 and laptop connected. The code path is implemented,
      but real BLE throughput, reconnect behavior, chunk loss, and latency are
      still unverified.
