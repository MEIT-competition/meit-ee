"""Tests for the meit-ai adapter and its PCM plumbing.

``meit-ai`` itself is not installed here, so the real runner is exercised only
through its guard clauses; the parts these tests can fully cover are the audio
contract (exactly 80000 bytes of PCM16LE) and the mock runner that makes the
whole pipeline testable without a model.
"""
import math
import tempfile
import unittest
import wave
from pathlib import Path

from laptop.ai_runner import (
    CLIP_SAMPLES,
    ENV_AI_PATH,
    EXPECTED_SR,
    PAYLOAD_BYTES,
    AIError,
    AIResult,
    MeitAIRunner,
    MockAIRunner,
    build_runner,
    dbfs_of,
    fit_clip,
    float_to_pcm16,
    load_wav_clip,
    pcm16_to_float,
)


def tone_pcm(samples, frequency=440.0, amplitude=0.5, rate=EXPECTED_SR):
    return float_to_pcm16([amplitude * math.sin(2 * math.pi * frequency * n / rate)
                           for n in range(samples)])


def write_wav(path, pcm, rate=EXPECTED_SR, channels=1):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)


class ContractTests(unittest.TestCase):
    def test_clip_size_matches_the_documented_contract(self):
        # meit-ios's adapter asserts 16 kHz / 2.5 s, so these constants are the
        # shared agreement, not a local choice.
        self.assertEqual(CLIP_SAMPLES, 40_000)
        self.assertEqual(PAYLOAD_BYTES, 80_000)
        self.assertEqual(PAYLOAD_BYTES, CLIP_SAMPLES * 2)


class PcmHelperTests(unittest.TestCase):
    def test_round_trip(self):
        values = [0.0, 0.5, -0.5, 0.999, -0.999]
        decoded = pcm16_to_float(float_to_pcm16(values))
        for original, result in zip(values, decoded):
            self.assertAlmostEqual(original, result, places=4)

    def test_clipping(self):
        decoded = pcm16_to_float(float_to_pcm16([5.0, -5.0]))
        self.assertLessEqual(decoded[0], 1.0)
        self.assertGreaterEqual(decoded[1], -1.0)

    def test_dbfs_of_silence_and_tone(self):
        self.assertEqual(dbfs_of(bytes(2000)), float("-inf"))
        quiet = dbfs_of(tone_pcm(1000, amplitude=0.05))
        loud = dbfs_of(tone_pcm(1000, amplitude=0.9))
        self.assertLess(quiet, loud)
        self.assertLess(loud, 0.0)

    def test_dbfs_of_empty(self):
        self.assertEqual(dbfs_of(b""), float("-inf"))


class FitClipTests(unittest.TestCase):
    def test_exact_length_is_untouched(self):
        payload = bytes(PAYLOAD_BYTES)
        self.assertIs(fit_clip(payload), payload)

    def test_short_input_is_padded_at_the_front(self):
        payload = fit_clip(b"\x01\x02" * 100)
        self.assertEqual(len(payload), PAYLOAD_BYTES)
        self.assertEqual(payload[:10], bytes(10))
        self.assertEqual(payload[-2:], b"\x01\x02")

    def test_long_input_keeps_the_tail(self):
        # An auto event is triggered by a level rise, so the hazard is at the end
        # of the buffer. Trimming the head is the only correct choice.
        marker = b"\x7f\x7f"
        payload = fit_clip(bytes(PAYLOAD_BYTES) + marker)
        self.assertEqual(len(payload), PAYLOAD_BYTES)
        self.assertEqual(payload[-2:], marker)

    def test_odd_byte_count_is_rejected(self):
        with self.assertRaises(AIError):
            fit_clip(b"\x00\x01\x02")


class WavTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_mono_at_the_model_rate(self):
        path = self.dir / "mono.wav"
        write_wav(path, tone_pcm(CLIP_SAMPLES))
        self.assertEqual(len(load_wav_clip(path)), PAYLOAD_BYTES)

    def test_short_file_is_padded(self):
        path = self.dir / "short.wav"
        write_wav(path, tone_pcm(4000))
        payload = load_wav_clip(path)
        self.assertEqual(len(payload), PAYLOAD_BYTES)
        self.assertEqual(payload[:100], bytes(100))

    def test_stereo_is_downmixed(self):
        path = self.dir / "stereo.wav"
        mono = tone_pcm(CLIP_SAMPLES)
        interleaved = bytearray()
        for index in range(0, len(mono), 2):
            interleaved += mono[index:index + 2] * 2
        write_wav(path, bytes(interleaved), channels=2)
        self.assertEqual(len(load_wav_clip(path)), PAYLOAD_BYTES)

    def test_other_sample_rates_are_resampled(self):
        path = self.dir / "44k.wav"
        write_wav(path, tone_pcm(44_100, rate=44_100), rate=44_100)
        self.assertEqual(len(load_wav_clip(path)), PAYLOAD_BYTES)

    def test_eight_bit_is_rejected(self):
        path = self.dir / "8bit.wav"
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(1)
            handle.setframerate(EXPECTED_SR)
            handle.writeframes(bytes(1000))
        with self.assertRaises(AIError):
            load_wav_clip(path)

    def test_a_loud_clip_reads_back_loud(self):
        # Guards the int16 <-> float scaling in the wav path: a silent result
        # from a loud file would make every bench replay look like nothing.
        path = self.dir / "loud.wav"
        write_wav(path, tone_pcm(CLIP_SAMPLES, amplitude=0.8))
        self.assertGreater(dbfs_of(load_wav_clip(path)), -12.0)


class MockRunnerTests(unittest.TestCase):
    def test_returns_the_configured_label(self):
        result = MockAIRunner(label="crash", confidence=0.77).infer(bytes(PAYLOAD_BYTES))
        self.assertIsInstance(result, AIResult)
        self.assertEqual(result.label, "crash")
        self.assertAlmostEqual(result.confidence, 0.77)
        self.assertTrue(result.danger)

    def test_non_danger_labels_report_not_dangerous(self):
        self.assertFalse(MockAIRunner(label="normal").infer(bytes(PAYLOAD_BYTES)).danger)

    def test_danger_can_be_forced(self):
        self.assertTrue(
            MockAIRunner(label="normal", danger=True).infer(bytes(PAYLOAD_BYTES)).danger)

    def test_label_cycling(self):
        runner = MockAIRunner(labels=["horn", "siren", "crash"])
        payload = bytes(PAYLOAD_BYTES)
        self.assertEqual([runner.infer(payload).label for _ in range(4)],
                         ["horn", "siren", "crash", "horn"])

    def test_short_input_is_accepted(self):
        # The runner fits the clip itself, so bench tools can hand it anything.
        self.assertEqual(MockAIRunner().infer(b"\x00\x01").label, "siren")

    def test_reports_the_clip_level(self):
        result = MockAIRunner().infer(tone_pcm(CLIP_SAMPLES, amplitude=0.6))
        self.assertTrue(math.isfinite(result.dbfs))
        self.assertLess(result.dbfs, 0.0)

    def test_event_result_shape_matches_auto_status(self):
        # Both ingest paths must present the same shape downstream.
        result = MockAIRunner(label="horn", confidence=0.6).infer(bytes(PAYLOAD_BYTES))
        payload = result.as_event_result()
        self.assertEqual(set(payload), {"label", "confidence", "danger", "inference_ms"})


class RunnerSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_mock_label_wins(self):
        self.assertIsInstance(build_runner(None, mock_label="horn"), MockAIRunner)

    def test_missing_path_explains_both_options(self):
        import os
        previous = os.environ.pop(ENV_AI_PATH, None)
        self.addCleanup(lambda: os.environ.update({ENV_AI_PATH: previous})
                        if previous is not None else None)
        with self.assertRaises(AIError) as caught:
            build_runner(None)
        message = str(caught.exception)
        self.assertIn(ENV_AI_PATH, message)
        self.assertIn("--mock-label", message)

    def test_a_directory_without_the_adapter_is_rejected(self):
        with self.assertRaises(AIError) as caught:
            MeitAIRunner(self.dir)
        self.assertIn("classifier/adapter.py", str(caught.exception))

    def test_a_checkout_without_the_saved_model_is_rejected(self):
        (self.dir / "classifier").mkdir()
        (self.dir / "classifier" / "adapter.py").write_text("SR = 16000\n")
        with self.assertRaises(AIError) as caught:
            MeitAIRunner(self.dir)
        self.assertIn("saved_model.pb", str(caught.exception))


class AIResultTests(unittest.TestCase):
    def test_defaults(self):
        result = AIResult(label="horn", confidence=0.5, danger=True)
        self.assertTrue(math.isnan(result.dbfs))
        self.assertEqual(result.probabilities, {})


if __name__ == "__main__":
    unittest.main()
