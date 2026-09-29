"""Tests for the AI-result-to-sensation mapping.

The encoding's whole value is that two hazards never feel the same, so most of
these tests are about *distinguishability* rather than exact numbers: pulse count
separates the classes, and the motor/order pattern separates the directions.
"""
import json
import unittest

from laptop.haptic import (
    DANGER_LABELS,
    DEFAULT_PROFILE,
    DEFAULT_PROFILE_PATH,
    MIN_STEP_ON_MS,
    ClassProfile,
    HapticError,
    HapticProfile,
    build_command,
    explain,
    intensity_for,
    render_timeline,
    with_intensity_scale,
)
from laptop.protocol import (
    DIR_BACK,
    DIR_FRONT,
    DIR_LEFT,
    DIR_RIGHT,
    DIR_STOP,
    MASK_BOTH,
    MASK_LEFT,
    MASK_RIGHT,
    PATTERN_MAX_STEPS,
    V3_MAX_BYTES,
    encode_v3,
)

DIRECTIONS = (DIR_LEFT, DIR_RIGHT, DIR_FRONT, DIR_BACK)


class ProfileFileTests(unittest.TestCase):
    def test_shipped_json_matches_the_built_in_default(self):
        # The JSON is the tuning surface and the Python object is the source of
        # truth; if they drift, a team editing the JSON would silently change
        # nothing (or the reverse).
        on_disk = json.loads(DEFAULT_PROFILE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(HapticProfile.from_dict(on_disk).to_dict(),
                         DEFAULT_PROFILE.to_dict())

    def test_load_without_a_path_returns_the_default(self):
        self.assertIs(HapticProfile.load(None), DEFAULT_PROFILE)

    def test_load_from_file(self):
        self.assertEqual(HapticProfile.load(DEFAULT_PROFILE_PATH).to_dict(),
                         DEFAULT_PROFILE.to_dict())

    def test_malformed_profile_is_rejected(self):
        with self.assertRaises(HapticError):
            HapticProfile.from_dict({"classes": {"horn": {"pulses": 2}}})


class ProfileValidationTests(unittest.TestCase):
    def make(self, **overrides):
        entry = {"pulses": 2, "on_ms": 240, "gap_ms": 140,
                 "min_intensity": 50, "max_intensity": 90}
        entry.update(overrides)
        classes = {label: ClassProfile(**entry) for label in DANGER_LABELS}
        return HapticProfile(classes=classes)

    def test_default_profile_is_valid(self):
        DEFAULT_PROFILE.validate()

    def test_pulses_must_leave_room_for_a_sweep(self):
        # A BACK sweep costs two steps per pulse, so the packet budget caps the
        # pulse count at half the step budget.
        with self.assertRaises(HapticError):
            self.make(pulses=PATTERN_MAX_STEPS // 2 + 1).validate()

    def test_on_ms_must_split_evenly_for_a_sweep(self):
        with self.assertRaises(HapticError):
            self.make(on_ms=250).validate()

    def test_sweep_halves_must_stay_perceptible(self):
        # An ERM motor needs ~50-80 ms to spin up, so a half-pulse under the
        # floor is felt as nothing rather than as a short tap.
        with self.assertRaises(HapticError):
            self.make(on_ms=2 * MIN_STEP_ON_MS - 20).validate()
        self.make(on_ms=2 * MIN_STEP_ON_MS).validate()

    def test_gap_must_be_on_the_wire_grid(self):
        with self.assertRaises(HapticError):
            self.make(gap_ms=145).validate()

    def test_intensity_ordering(self):
        with self.assertRaises(HapticError):
            self.make(min_intensity=90, max_intensity=50).validate()
        with self.assertRaises(HapticError):
            self.make(min_intensity=0).validate()

    def test_missing_danger_class(self):
        with self.assertRaises(HapticError):
            HapticProfile(classes={"horn": DEFAULT_PROFILE.classes["horn"]}).validate()

    def test_confidence_bounds(self):
        with self.assertRaises(HapticError):
            HapticProfile(classes=dict(DEFAULT_PROFILE.classes),
                          confidence_floor=0.9, confidence_ceiling=0.5).validate()


class ClassEncodingTests(unittest.TestCase):
    def pulse_count(self, label, direction):
        command = build_command(label, 0.8, direction)
        steps = len(command.steps)
        # A sweep spends two steps per pulse; every other direction spends one.
        return steps // 2 if direction == DIR_BACK else steps

    def test_pulse_count_identifies_the_class(self):
        for direction in DIRECTIONS:
            counts = {label: self.pulse_count(label, direction)
                      for label in DANGER_LABELS}
            self.assertEqual(len(set(counts.values())), len(DANGER_LABELS),
                             f"classes are not distinguishable for {direction}: {counts}")

    def test_pulse_count_is_the_same_across_directions(self):
        # Class and direction must live on different channels, or "siren from
        # behind" would be ambiguous with "horn from the left".
        for label in DANGER_LABELS:
            counts = {direction: self.pulse_count(label, direction)
                      for direction in DIRECTIONS}
            self.assertEqual(len(set(counts.values())), 1,
                             f"{label} pulse count varies by direction: {counts}")

    def test_crash_is_the_shortest_and_strongest(self):
        crash = build_command("crash", 1.0, DIR_FRONT)
        horn = build_command("horn", 1.0, DIR_FRONT)
        self.assertEqual(len(crash.steps), 1)
        self.assertGreater(crash.intensity, horn.intensity)


class DirectionEncodingTests(unittest.TestCase):
    def test_left_and_right_use_one_motor(self):
        for direction, mask in ((DIR_LEFT, MASK_LEFT), (DIR_RIGHT, MASK_RIGHT)):
            command = build_command("siren", 0.8, direction)
            self.assertEqual({step.mask for step in command.steps}, {mask})

    def test_front_uses_both_motors_together(self):
        command = build_command("siren", 0.8, DIR_FRONT)
        self.assertEqual({step.mask for step in command.steps}, {MASK_BOTH})

    def test_back_alternates_left_then_right(self):
        command = build_command("siren", 0.8, DIR_BACK)
        self.assertEqual([step.mask for step in command.steps],
                         [MASK_LEFT, MASK_RIGHT] * 3)

    def test_back_halves_run_with_no_seam(self):
        # The gap inside a pulse must be zero, otherwise the sweep is felt as
        # two separate taps instead of one movement.
        command = build_command("horn", 0.8, DIR_BACK)
        self.assertEqual(command.steps[0].off_ms, 0)
        self.assertEqual(command.steps[2].off_ms, 0)

    def test_back_and_front_have_the_same_total_duration(self):
        # Splitting a pulse must not change how long the alert lasts, only how
        # it is distributed, so duration stays free to encode nothing.
        for label in DANGER_LABELS:
            self.assertEqual(build_command(label, 0.8, DIR_BACK).duration_ms,
                             build_command(label, 0.8, DIR_FRONT).duration_ms,
                             label)

    def test_back_and_front_are_distinguishable(self):
        back = build_command("siren", 0.8, DIR_BACK)
        front = build_command("siren", 0.8, DIR_FRONT)
        self.assertNotEqual([s.mask for s in back.steps],
                            [s.mask for s in front.steps])

    def test_every_pattern_fits_one_ble_write(self):
        for direction in DIRECTIONS:
            for label in DANGER_LABELS:
                packet = encode_v3(1, build_command(label, 1.0, direction))
                self.assertLessEqual(len(packet), V3_MAX_BYTES,
                                     f"{direction}/{label} is {len(packet)} bytes")


class SuppressionTests(unittest.TestCase):
    def test_unknown_direction_is_silent(self):
        # A directionless buzz teaches the wearer to ignore the belt.
        self.assertIsNone(build_command("siren", 0.9, DIR_STOP))

    def test_non_danger_labels_are_silent(self):
        for label in ("normal", "speech", "", "SIREN_LIKE"):
            self.assertIsNone(build_command(label, 0.9, DIR_LEFT), label)

    def test_label_case_and_whitespace_are_tolerated(self):
        self.assertIsNotNone(build_command("  SIREN ", 0.9, DIR_LEFT))

    def test_missing_class_in_a_custom_profile(self):
        profile = HapticProfile(classes={
            label: DEFAULT_PROFILE.classes[label] for label in ("horn", "siren", "crash")})
        self.assertIsNotNone(build_command("horn", 0.5, DIR_LEFT, profile))


class IntensityTests(unittest.TestCase):
    def test_confidence_scales_within_the_class_range(self):
        entry = DEFAULT_PROFILE.classes["siren"]
        self.assertEqual(intensity_for(entry, 0.0), entry.min_intensity)
        self.assertEqual(intensity_for(entry, 0.50), entry.min_intensity)
        self.assertEqual(intensity_for(entry, 0.90), entry.max_intensity)
        self.assertEqual(intensity_for(entry, 1.0), entry.max_intensity)
        middle = intensity_for(entry, 0.70)
        self.assertLess(entry.min_intensity, middle)
        self.assertLess(middle, entry.max_intensity)

    def test_monotonic(self):
        entry = DEFAULT_PROFILE.classes["horn"]
        values = [intensity_for(entry, c / 20) for c in range(21)]
        self.assertEqual(values, sorted(values))

    def test_malformed_confidence_degrades_to_the_minimum(self):
        # A hazard the AI reported is still worth feeling when its confidence
        # field arrived unusable.
        entry = DEFAULT_PROFILE.classes["crash"]
        for bad in (None, "high", float("nan"), float("inf"), float("-inf")):
            self.assertEqual(intensity_for(entry, bad), entry.min_intensity, bad)

    def test_scale_clamps_to_the_legal_range(self):
        command = build_command("siren", 1.0, DIR_LEFT)
        self.assertEqual(with_intensity_scale(command, 5.0).intensity, 100)
        self.assertEqual(with_intensity_scale(command, 0.001).intensity, 1)
        self.assertEqual(with_intensity_scale(command, 1.0), command)

    def test_scale_leaves_stop_alone(self):
        from laptop.protocol import STOP_COMMAND
        self.assertEqual(with_intensity_scale(STOP_COMMAND, 2.0), STOP_COMMAND)


class PerceptibilityTests(unittest.TestCase):
    def test_no_emitted_burst_is_below_the_floor(self):
        for direction in DIRECTIONS:
            for label in DANGER_LABELS:
                command = build_command(label, 0.8, direction)
                for step in command.steps:
                    self.assertGreaterEqual(step.on_ms, MIN_STEP_ON_MS,
                                            f"{direction}/{label}")

    def test_a_profile_that_would_emit_an_imperceptible_burst_is_refused(self):
        # Construct past ClassProfile.validate() to prove build_command has its
        # own guard: a hand-edited profile file must not reach the motors.
        entry = ClassProfile.__new__(ClassProfile)
        object.__setattr__(entry, "pulses", 1)
        object.__setattr__(entry, "on_ms", 20)
        object.__setattr__(entry, "gap_ms", 0)
        object.__setattr__(entry, "min_intensity", 50)
        object.__setattr__(entry, "max_intensity", 60)
        profile = HapticProfile.__new__(HapticProfile)
        object.__setattr__(profile, "classes", {"horn": entry})
        object.__setattr__(profile, "confidence_floor", 0.5)
        object.__setattr__(profile, "confidence_ceiling", 0.9)
        with self.assertRaises(HapticError):
            build_command("horn", 0.8, DIR_LEFT, profile)


class PresentationTests(unittest.TestCase):
    def test_timeline_shows_the_sweep_staircase(self):
        text = render_timeline(build_command("horn", 0.8, DIR_BACK))
        left, right = text.splitlines()[1], text.splitlines()[2]
        self.assertTrue(left.startswith("L |#"))
        # The right motor must be idle while the left one starts.
        self.assertTrue(right.startswith("R |."))

    def test_timeline_shows_front_aligned(self):
        text = render_timeline(build_command("horn", 0.8, DIR_FRONT))
        left, right = text.splitlines()[1], text.splitlines()[2]
        self.assertEqual(left[2:], right[2:])

    def test_timeline_handles_stop(self):
        from laptop.protocol import STOP_COMMAND
        self.assertIn("STOP", render_timeline(STOP_COMMAND))

    def test_explain_reports_suppression(self):
        self.assertIn("suppressed", explain("normal", 0.9, DIR_LEFT))
        self.assertIn("BACK", explain("siren", 0.9, DIR_BACK))


if __name__ == "__main__":
    unittest.main()
