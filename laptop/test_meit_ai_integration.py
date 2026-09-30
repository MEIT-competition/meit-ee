"""End-to-end test against the real ``meit-ai`` checkout.

This is the one test that proves the whole back half of the system:

    audio -> meit-ai (YAMNet + calibrated head) -> meit-ai judge()
          -> haptic mapping -> CMD packet -> (belt)

Everything except the final radio hop is exercised with the actual model, so a
pass here means the only thing left unverified is the BLE write itself, which
``test_belt_client`` covers with a fake adapter.

It is skipped unless ``MEIT_AI_PATH`` points at a checkout with the SavedModel
and TensorFlow is importable, so CI and machines without the model still pass::

    MEIT_AI_PATH=~/src/meit-ai python -m unittest laptop.test_meit_ai_integration -v
"""
from __future__ import annotations

import math
import os
import unittest
from pathlib import Path

from laptop.haptic import (
    DANGER_LABELS,
    DEFAULT_PROFILE,
    MIN_STEP_ON_MS,
    build_command,
    intensity_from_dbfs,
)
from laptop.protocol import (
    DIR_CENTER,
    DIR_LEFT,
    DIR_RIGHT,
    MAX_PACKET_BYTES,
    decode,
    encode,
    normalize_direction,
)

# iOS stereo reports exactly these; `unavailable` is the fourth and is suppressed
# before it reaches the belt. See StereoDirectionEstimator.swift.
STEREO_DIRECTIONS = ("left", "center", "right")


def _ai_root():
    raw = os.environ.get("MEIT_AI_PATH")
    if not raw:
        return None
    root = Path(raw).expanduser()
    if not (root / "classifier" / "adapter.py").is_file():
        return None
    if not (root / "model" / "saved_model" / "danger_sound_classifier"
            / "saved_model.pb").is_file():
        return None
    return root


def _importable(module: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


AI_ROOT = _ai_root()
SKIP_REASON = None
if AI_ROOT is None:
    SKIP_REASON = "set MEIT_AI_PATH to a meit-ai checkout containing the SavedModel"
elif not _importable("tensorflow"):
    SKIP_REASON = "meit-ai needs tensorflow (pip install tensorflow librosa)"


def tone_pcm(frequency: float, amplitude: float):
    """One model-length clip of a sine tone, as PCM16LE bytes."""
    from laptop.ai_runner import CLIP_SAMPLES, EXPECTED_SR, float_to_pcm16

    return float_to_pcm16([
        amplitude * math.sin(2 * math.pi * frequency * n / EXPECTED_SR)
        for n in range(CLIP_SAMPLES)
    ])


@unittest.skipIf(SKIP_REASON is not None, SKIP_REASON or "")
class RealModelTests(unittest.TestCase):
    """Uses the real model, so load it once for the whole class (~4 s)."""

    @classmethod
    def setUpClass(cls):
        from laptop.ai_runner import MeitAIRunner

        cls.runner = MeitAIRunner(AI_ROOT)
        # A loud tone the model classifies as a danger class with high confidence;
        # it is the fixture every downstream assertion builds on.
        cls.loud = cls.runner.infer(tone_pcm(440.0, 0.30))

    def test_contract_matches_what_the_bridge_assumes(self):
        # If meit-ai ever changes its audio contract, fail here with a clear
        # message rather than silently feeding the model mis-scaled audio.
        from laptop.ai_runner import EXPECTED_CLIP_SEC, EXPECTED_SR

        self.assertEqual(self.runner._api.SR, EXPECTED_SR)
        self.assertEqual(self.runner._api.CLIP_SEC, EXPECTED_CLIP_SEC)
        self.assertEqual(set(self.runner.classes), {"horn", "siren", "crash", "normal"})

    def test_inference_returns_a_usable_result(self):
        result = self.loud
        self.assertIn(result.label, self.runner.classes)
        self.assertTrue(0.0 <= result.confidence <= 1.0)
        self.assertTrue(math.isfinite(result.dbfs))
        # Probabilities must cover every class and form a distribution.
        self.assertEqual(set(result.probabilities), set(self.runner.classes))
        self.assertAlmostEqual(sum(result.probabilities.values()), 1.0, places=3)

    def test_a_loud_danger_sound_is_judged_dangerous(self):
        self.assertIn(self.loud.label, DANGER_LABELS,
                      f"expected a danger class, got {self.loud.label}")
        self.assertTrue(self.loud.danger)
        self.assertGreaterEqual(self.loud.confidence, 0.4)  # meit-ai THRESHOLD

    def test_meit_ai_db_gate_suppresses_a_quiet_clip(self):
        # Same waveform, far quieter: meit-ai's judge() has a -50 dBFS gate, so
        # this must come back not-dangerous even though the class is unchanged.
        quiet = self.runner.infer(tone_pcm(440.0, 0.0015))
        self.assertLess(quiet.dbfs, -50.0)
        self.assertFalse(quiet.danger)

    def test_digital_silence_is_not_dangerous(self):
        from laptop.ai_runner import PAYLOAD_BYTES

        silent = self.runner.infer(bytes(PAYLOAD_BYTES))
        self.assertFalse(silent.danger)

    def test_louder_audio_produces_a_stronger_buzz(self):
        # meit-ai's rule: loudness sets intensity. Verify it survives the mapping
        # into the belt's band rather than being flattened.
        entry = DEFAULT_PROFILE.classes[self.loud.label]
        soft = intensity_from_dbfs(entry, -40.0)
        hard = intensity_from_dbfs(entry, -10.0)
        self.assertLess(soft, hard)
        self.assertGreaterEqual(soft, entry.min_intensity)
        self.assertLessEqual(hard, entry.max_intensity)

    def test_full_chain_to_a_belt_packet(self):
        """audio -> meit-ai -> haptic -> CMD packet -> decode, per iOS direction."""
        for name in STEREO_DIRECTIONS:
            with self.subTest(direction=name):
                direction = normalize_direction(name)
                command = build_command(self.loud.label, self.loud.confidence,
                                        direction, dbfs=self.loud.dbfs)
                self.assertIsNotNone(command, "a judged hazard must produce a pattern")

                # Every burst must clear the belt's perceptibility floor, which is
                # exactly what meit-ai's own 80 ms patterns would have violated.
                for step in command.steps:
                    self.assertGreaterEqual(step.on_ms, MIN_STEP_ON_MS)

                packet = encode(1, command)
                self.assertLessEqual(len(packet), MAX_PACKET_BYTES)
                self.assertEqual(decode(packet).command, command,
                                 "the packet the belt receives must match what we built")

    def test_direction_reaches_the_right_motors(self):
        from laptop.protocol import MASK_BOTH, MASK_LEFT, MASK_RIGHT

        expected = {"left": (DIR_LEFT, MASK_LEFT),
                    "right": (DIR_RIGHT, MASK_RIGHT),
                    "center": (DIR_CENTER, MASK_BOTH)}
        for name, (code, mask) in expected.items():
            with self.subTest(direction=name):
                command = build_command(self.loud.label, self.loud.confidence,
                                        normalize_direction(name), dbfs=self.loud.dbfs)
                self.assertEqual(command.direction, code)
                self.assertEqual(command.mask, mask)

    def test_a_suppressed_result_sends_nothing(self):
        quiet = self.runner.infer(tone_pcm(440.0, 0.0015))
        self.assertFalse(quiet.danger)
        # The bridge checks `danger` before mapping; assert the guard exists so a
        # refactor cannot start vibrating for clips meit-ai rejected.
        from laptop.ai_motor_bridge import Ingest
        self.assertIn("danger", Ingest._dispatch.__code__.co_names)

    def test_unavailable_direction_is_suppressed(self):
        from laptop.protocol import DIR_STOP

        self.assertEqual(normalize_direction("unavailable"), DIR_STOP)
        self.assertIsNone(build_command(self.loud.label, self.loud.confidence,
                                        DIR_STOP, dbfs=self.loud.dbfs))


@unittest.skipIf(SKIP_REASON is not None, SKIP_REASON or "")
class BridgeWiringTests(unittest.TestCase):
    """The HTTP ingest path, with the real model behind it."""

    def test_audio_endpoint_produces_a_queued_command(self):
        import asyncio

        from laptop.ai_motor_bridge import CommandSink, Ingest
        from laptop.ai_runner import MeitAIRunner

        async def run():
            sink = CommandSink(asyncio.get_running_loop(), cooldown_ms=0)
            ingest = Ingest(MeitAIRunner(AI_ROOT), sink)
            outcome = ingest.handle_audio(tone_pcm(440.0, 0.30), "center", "evt-1")
            self.assertEqual(outcome["outcome"], "queued", outcome)
            self.assertIn(outcome["label"], DANGER_LABELS)
            command, _ = await sink.get()
            self.assertEqual(command.direction, DIR_CENTER)
            return command

        command = asyncio.run(run())
        self.assertGreaterEqual(command.intensity, 1)

    def test_quiet_audio_is_suppressed_by_the_bridge(self):
        import asyncio

        from laptop.ai_motor_bridge import CommandSink, Ingest
        from laptop.ai_runner import MeitAIRunner

        async def run():
            sink = CommandSink(asyncio.get_running_loop(), cooldown_ms=0)
            ingest = Ingest(MeitAIRunner(AI_ROOT), sink)
            outcome = ingest.handle_audio(tone_pcm(440.0, 0.0015), "left", None)
            self.assertEqual(outcome["outcome"], "suppressed")
            self.assertEqual(outcome["reason"], "not_dangerous")
            self.assertTrue(sink.queue.empty())

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
