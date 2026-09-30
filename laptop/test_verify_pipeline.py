"""Tests for the end-to-end verification tool's own logic.

The tool's job is to tell a person holding the belt what they should feel, so the
thing worth testing is that its announcements match the commands it actually
sends. A wrong announcement is worse than no tool: it would make a real failure
look like a pass.
"""
import tempfile
import unittest
import wave
from pathlib import Path

from laptop.ai_runner import CLIP_SAMPLES, PAYLOAD_BYTES, AIResult
from laptop.haptic import build_command
from laptop.protocol import (
    DIR_CENTER,
    DIR_LEFT,
    DIR_RIGHT,
    normalize_direction,
)
from laptop.verify_pipeline import (
    MOTOR_WORDS,
    Clip,
    builtin_clips,
    clips_from_folder,
    describe_expectation,
)


def result(label="siren", confidence=0.9, danger=True, dbfs=-12.0):
    return AIResult(label=label, confidence=confidence, danger=danger, dbfs=dbfs)


class BuiltinClipTests(unittest.TestCase):
    def test_clips_are_model_sized(self):
        # Every clip must be exactly one model input, or the runner would pad or
        # trim it and the test would no longer be the audio we described.
        for clip in builtin_clips():
            with self.subTest(clip=clip.name):
                self.assertEqual(len(clip.pcm), PAYLOAD_BYTES)

    def test_set_covers_both_outcomes(self):
        # A verification pass is only meaningful if it includes cases the belt
        # must stay silent for, not just ones it must buzz for.
        intents = " ".join(clip.intent for clip in builtin_clips())
        self.assertIn("진동해야 함", intents)
        self.assertIn("진동 없어야 함", intents)

    def test_deterministic(self):
        # Two runs must produce identical audio, otherwise a re-run could give a
        # different verdict and there would be nothing to compare against.
        self.assertEqual([c.pcm for c in builtin_clips()],
                         [c.pcm for c in builtin_clips()])

    def test_loud_and_quiet_clips_really_differ_in_level(self):
        from laptop.ai_runner import dbfs_of

        clips = {c.name: c.pcm for c in builtin_clips()}
        loud = dbfs_of(clips["큰 소리 (음높이 변하는 톤)"])
        quiet = dbfs_of(clips["같은 소리, 아주 작게"])
        self.assertGreater(loud, -20.0)
        # Must sit below meit-ai's own DB_GATE of -50 dBFS for the case to test
        # what it claims to test.
        self.assertLess(quiet, -50.0)

    def test_silence_clip_is_silent(self):
        clips = {c.name: c.pcm for c in builtin_clips()}
        self.assertEqual(clips["완전 무음"], bytes(PAYLOAD_BYTES))


class ExpectationTests(unittest.TestCase):
    def test_silence_is_announced_as_no_vibration(self):
        text = describe_expectation(None, result(danger=False))
        self.assertIn("진동 없음", text)

    def test_pulse_count_matches_the_command(self):
        for label, pulses in (("crash", 1), ("horn", 2), ("siren", 3)):
            with self.subTest(label=label):
                command = build_command(label, 0.9, DIR_CENTER, dbfs=-12.0)
                text = describe_expectation(command, result(label=label))
                self.assertIn(f"**{pulses}번**", text)

    def test_each_direction_names_the_right_motors(self):
        for name, code in (("left", DIR_LEFT), ("right", DIR_RIGHT),
                           ("center", DIR_CENTER)):
            with self.subTest(direction=name):
                command = build_command("siren", 0.9, code, dbfs=-12.0)
                text = describe_expectation(command, result())
                self.assertIn(MOTOR_WORDS[
                    {DIR_LEFT: "LEFT", DIR_RIGHT: "RIGHT",
                     DIR_CENTER: "CENTER"}[code]], text)

    def test_announced_intensity_matches_the_command(self):
        command = build_command("siren", 0.9, DIR_CENTER, dbfs=-12.0)
        self.assertIn(f"세기 {command.intensity}%", describe_expectation(command, result()))
        self.assertIn(f"{command.duration_ms}ms", describe_expectation(command, result()))

    def test_every_motor_word_is_defined(self):
        # A missing entry would raise KeyError mid-test-run, after the tester has
        # already started feeling patterns.
        self.assertEqual(set(MOTOR_WORDS), {"LEFT", "CENTER", "RIGHT"})


class FolderTests(unittest.TestCase):
    def test_reads_wavs_in_sorted_order(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            for name in ("b.wav", "a.wav"):
                with wave.open(str(folder / name), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(16000)
                    handle.writeframes(bytes(CLIP_SAMPLES * 2))
            clips = clips_from_folder(folder)
            self.assertEqual([c.name for c in clips], ["a.wav", "b.wav"])
            self.assertEqual(len(clips[0].pcm), PAYLOAD_BYTES)

    def test_empty_folder_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(SystemExit):
                clips_from_folder(Path(raw))


class ClipTests(unittest.TestCase):
    def test_clip_carries_its_purpose(self):
        clip = Clip("x", bytes(PAYLOAD_BYTES), "왜 이 케이스가 있는지")
        self.assertEqual(clip.intent, "왜 이 케이스가 있는지")

    def test_unavailable_direction_yields_no_command(self):
        self.assertIsNone(build_command("siren", 0.9,
                                        normalize_direction("unavailable"), dbfs=-12.0))


if __name__ == "__main__":
    unittest.main()
