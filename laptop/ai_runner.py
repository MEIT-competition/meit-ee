"""Load and call the separately maintained ``meit-ai`` repository.

Why this file exists at all
--------------------------
The primary runtime does **not** need it: the ``meit-ios`` laptop bridge already
loads ``meit-ai`` and publishes the finished result on ``/auto/status``, and
``ios_motor_bridge`` consumes that.  Running the model twice would waste a
TensorFlow session for no new information.

This module is for the two cases where EE needs inference of its own:

* bench work — replay a ``.wav`` through the real model and feel the resulting
  pattern, with no iPhones, no Wi-Fi and no acoustic setup, and
* a deployment where the iOS bridge is run without AI and hands EE raw audio
  plus a direction (``ai_motor_bridge``).

The contract
------------
Mirrored from ``meit-ios``'s ``bridge/meit_ai_adapter.py``, verified against
public ``main`` on 2026-09-30, so both consumers agree on the same interface
and ``meit-ai`` needs no per-consumer shim:

``$MEIT_AI_PATH/classifier/adapter.py``
    ``SR`` (16000), ``CLIP_SEC`` (2.5), ``CLASSES``, ``load_model()``,
    ``load_temperature()``, ``predict_array(waveform) -> (probabilities, dbfs)``
    where ``waveform`` is float32 in -1..1 and ``probabilities`` maps every
    entry of ``CLASSES`` to a probability.
``$MEIT_AI_PATH/decision/judge.py``
    ``judge(probabilities, direction, db)`` returning a truthy alert or ``None``.
``$MEIT_AI_PATH/model/saved_model/danger_sound_classifier/saved_model.pb``
    the SavedModel itself.

``meit-ai`` source is never copied or modified; it is imported from wherever it
is checked out.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import math
import os
import struct
import sys
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from laptop.haptic import DANGER_LABELS

LOGGER = logging.getLogger("meit.ai")

# The audio contract. Anything else is a different model and must be rejected
# loudly rather than silently resampled into nonsense.
EXPECTED_SR = 16_000
EXPECTED_CLIP_SEC = 2.5
CLIP_SAMPLES = int(EXPECTED_SR * EXPECTED_CLIP_SEC)  # 40000
PAYLOAD_BYTES = CLIP_SAMPLES * 2  # 80000, PCM16LE mono

# meit-ios passes -1 so `judge` cannot apply its own 4-role direction logic:
# direction is decided by the iPhone role registry, never by the classifier.
JUDGE_DIRECTION_SENTINEL = -1

ENV_AI_PATH = "MEIT_AI_PATH"


class AIError(RuntimeError):
    """meit-ai is missing, incompatible, or returned something unusable."""


@dataclass(frozen=True)
class AIResult:
    """One inference outcome, in the shape the haptic layer consumes."""

    label: str
    confidence: float
    danger: bool
    dbfs: float = float("nan")
    inference_ms: float = 0.0
    probabilities: Mapping[str, float] = field(default_factory=dict)

    def as_event_result(self) -> Dict[str, Any]:
        """The ``/auto/status`` ``result`` shape, so both paths look identical."""
        return {"label": self.label, "confidence": self.confidence,
                "danger": self.danger, "inference_ms": self.inference_ms}


# ------------------------------------------------------------------ PCM helpers
def pcm16_to_float(payload: bytes) -> Sequence[float]:
    """Decode PCM16LE to floats in -1..1 without requiring numpy.

    The real model path hands numpy a buffer directly; this exists for the mock
    runner and the unit tests, which must work on a bare Python install.
    """
    count = len(payload) // 2
    return [sample / 32768.0 for sample in struct.unpack(f"<{count}h", payload[:count * 2])]


def float_to_pcm16(samples: Sequence[float]) -> bytes:
    out = bytearray()
    for value in samples:
        clamped = min(1.0, max(-1.0, float(value)))
        out += struct.pack("<h", int(round(clamped * 32767.0)))
    return bytes(out)


def dbfs_of(payload: bytes) -> float:
    """RMS level in dBFS, matching the convention meit-ios reports."""
    samples = pcm16_to_float(payload)
    if not samples:
        return float("-inf")
    mean_square = sum(value * value for value in samples) / len(samples)
    if mean_square <= 0.0:
        return float("-inf")
    return 10.0 * math.log10(mean_square)


def fit_clip(payload: bytes) -> bytes:
    """Pad with silence or trim to exactly :data:`PAYLOAD_BYTES`.

    Trimming keeps the *tail*: an auto event is triggered by a level rise, so
    the hazard is at the end of the buffer, not the start.
    """
    if len(payload) % 2:
        raise AIError("PCM16 payload has an odd byte count")
    if len(payload) == PAYLOAD_BYTES:
        return payload
    if len(payload) > PAYLOAD_BYTES:
        return payload[-PAYLOAD_BYTES:]
    return bytes(PAYLOAD_BYTES - len(payload)) + payload


def load_wav_clip(path: Path | str) -> bytes:
    """Read a ``.wav`` into exactly one model input clip.

    Mono is taken by averaging channels.  A sample rate other than 16 kHz is
    linearly resampled, which is good enough for bench replay but is *not* how
    production audio arrives — the iPhone already records at the model's rate.
    """
    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise AIError(f"{path}: only 16-bit PCM wav files are supported")
        channels = handle.getnchannels()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())

    total = len(frames) // 2
    flat = struct.unpack(f"<{total}h", frames[:total * 2])
    if channels > 1:
        mono = [sum(flat[i:i + channels]) / channels
                for i in range(0, total - total % channels, channels)]
    else:
        mono = list(flat)

    if rate != EXPECTED_SR:
        LOGGER.warning("%s is %d Hz; linearly resampling to %d Hz for bench replay",
                       path, rate, EXPECTED_SR)
        ratio = EXPECTED_SR / rate
        length = max(1, int(len(mono) * ratio))
        resampled = []
        for index in range(length):
            position = index / ratio
            low = int(position)
            high = min(low + 1, len(mono) - 1)
            weight = position - low
            resampled.append(mono[low] * (1.0 - weight) + mono[high] * weight)
        mono = resampled

    return fit_clip(float_to_pcm16([value / 32768.0 for value in mono]))


# -------------------------------------------------------------------- runners
class MockAIRunner:
    """Deterministic stand-in for ``meit-ai``.

    ``meit-ai`` was still unpublished when this was written, so the whole EE
    pipeline — ingest, haptic mapping, BLE, motors — has to be verifiable
    without it.  Swapping this for :class:`MeitAIRunner` changes nothing
    downstream because both return an :class:`AIResult`.
    """

    def __init__(self, label: str = "siren", confidence: float = 0.85,
                 danger: Optional[bool] = None,
                 labels: Optional[Sequence[str]] = None) -> None:
        self.labels = list(labels) if labels else [label]
        self.confidence = float(confidence)
        self.forced_danger = danger
        self._index = 0

    def infer(self, payload: bytes) -> AIResult:
        payload = fit_clip(payload)
        label = self.labels[self._index % len(self.labels)]
        self._index += 1
        danger = (label in DANGER_LABELS) if self.forced_danger is None else self.forced_danger
        return AIResult(label=label, confidence=self.confidence, danger=danger,
                        dbfs=dbfs_of(payload), inference_ms=0.0,
                        probabilities={label: self.confidence})

    def close(self) -> None:  # symmetry with MeitAIRunner
        return None


class MeitAIRunner:
    """Import and call the real, unmodified ``meit-ai`` checkout."""

    def __init__(self, repository: Path | str) -> None:
        root = Path(repository).expanduser().resolve()
        entry = root / "classifier" / "adapter.py"
        model = root / "model" / "saved_model" / "danger_sound_classifier" / "saved_model.pb"
        if not entry.is_file():
            raise AIError(f"{root} does not contain classifier/adapter.py; point "
                          f"{ENV_AI_PATH} at the meit-ai checkout")
        if not model.is_file():
            raise AIError(f"{root} has no model/saved_model/danger_sound_classifier/"
                          "saved_model.pb")

        self.root = root
        self._judge = None
        self._api = self._import("classifier.adapter", entry)

        sample_rate = getattr(self._api, "SR", None)
        clip_seconds = getattr(self._api, "CLIP_SEC", None)
        if sample_rate != EXPECTED_SR or clip_seconds != EXPECTED_CLIP_SEC:
            raise AIError(f"meit-ai input contract is {sample_rate} Hz / {clip_seconds} s; "
                          f"this bridge encodes {EXPECTED_SR} Hz / {EXPECTED_CLIP_SEC} s")
        self.classes = tuple(getattr(self._api, "CLASSES", ()))
        if not self.classes:
            raise AIError("meit-ai exposes no CLASSES")

        self._numpy = importlib.import_module("numpy")
        self._api.load_model()
        self._api.load_temperature()
        LOGGER.info("meit-ai loaded from %s (classes=%s)", root, ",".join(self.classes))

    def _import(self, module: str, expected_origin: Path) -> Any:
        """Import a module from the meit-ai checkout and nowhere else.

        Verifying the resolved origin stops an unrelated ``classifier`` package
        that happens to be installed in the environment from being loaded as
        the model, which would fail in a confusing way much later.
        """
        if str(self.root) not in sys.path:
            sys.path.insert(0, str(self.root))
        importlib.invalidate_caches()
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True  # do not litter someone else's repo
        try:
            spec = importlib.util.find_spec(module)
            if spec is None or Path(spec.origin or "").resolve() != expected_origin:
                raise AIError(f"{module} resolves outside {ENV_AI_PATH}")
            return importlib.import_module(module)
        finally:
            sys.dont_write_bytecode = previous

    def _judge_fn(self):
        """Resolve ``decision.judge`` lazily, tolerating its absence.

        ``meit-ai`` may ship the classifier before the decision layer.  Falling
        back to "a danger class is dangerous" keeps the belt working, but says
        so once so nobody assumes the tuned thresholds are in play.
        """
        if self._judge is not None:
            return self._judge
        target = self.root / "decision" / "judge.py"
        if not target.is_file():
            LOGGER.warning("meit-ai has no decision/judge.py; treating %s as dangerous",
                           "/".join(DANGER_LABELS))
            self._judge = False
            return self._judge
        self._judge = self._import("decision.judge", target).judge
        return self._judge

    def infer(self, payload: bytes) -> AIResult:
        payload = fit_clip(payload)
        waveform = self._numpy.frombuffer(payload, dtype="<i2").astype(self._numpy.float32)
        waveform = waveform / 32768.0

        started = time.perf_counter()
        probabilities, dbfs = self._api.predict_array(waveform)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        probabilities = dict(probabilities)
        if set(probabilities) != set(self.classes) or not probabilities:
            raise AIError("meit-ai returned an unexpected class set")
        if any(not math.isfinite(value) or not 0.0 <= value <= 1.0
               for value in probabilities.values()):
            raise AIError("meit-ai returned a non-probability confidence")
        if not math.isfinite(dbfs):
            raise AIError("meit-ai returned a non-finite dBFS")

        label = max(probabilities, key=probabilities.__getitem__)
        judge = self._judge_fn()
        if judge is False:
            danger = label in DANGER_LABELS
        else:
            danger = judge(probabilities, direction=JUDGE_DIRECTION_SENTINEL,
                           db=dbfs) is not None

        return AIResult(label=str(label), confidence=float(probabilities[label]),
                        danger=bool(danger), dbfs=float(dbfs),
                        inference_ms=elapsed_ms, probabilities=probabilities)

    def close(self) -> None:
        return None


def build_runner(repository: Optional[Path | str] = None, *,
                 mock_label: Optional[str] = None,
                 mock_confidence: float = 0.85):
    """Pick a runner: explicit mock, else ``meit-ai``, else a clear error.

    ``repository`` falls back to ``$MEIT_AI_PATH`` so the path is configured the
    same way ``meit-ios`` configures it.
    """
    if mock_label:
        LOGGER.warning("using MOCK inference (label=%s); no real model is loaded", mock_label)
        return MockAIRunner(label=mock_label, confidence=mock_confidence)
    target = repository or os.environ.get(ENV_AI_PATH)
    if not target:
        raise AIError(f"set {ENV_AI_PATH} (or pass --meit-ai) to the meit-ai checkout, "
                      "or use --mock-label to bench-test without the model")
    return MeitAIRunner(target)
