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
    DIR_CENTER,
    DIR_LEFT,
    DIR_RIGHT,
    DIR_STOP,
    MASK_BOTH,
    MASK_LEFT,
    MASK_RIGHT,
    MAX_PACKET_BYTES,
    PATTERN_MAX_STEPS,
    encode,
)

DIRECTIONS = (DIR_LEFT, DIR_CENTER, DIR_RIGHT)


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

    def test_pulses_must_fit_one_packet(self):
        with self.assertRaises(HapticError):
            self.make(pulses=PATTERN_MAX_STEPS + 1).validate()
        self.make(pulses=PATTERN_MAX_STEPS).validate()

    def test_on_ms_must_sit_on_the_wire_grid(self):
        with self.assertRaises(HapticError):
            self.make(on_ms=245).validate()

    def test_pulses_must_stay_perceptible(self):
        # An ERM motor needs ~50-80 ms to spin up, so a pulse under the floor is
        # felt as nothing rather than as a short tap.
        with self.assertRaises(HapticError):
            self.make(on_ms=MIN_STEP_ON_MS - 10).validate()
        self.make(on_ms=MIN_STEP_ON_MS).validate()

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
        return len(build_command(label, 0.8, direction).steps)

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

    def test_crash_is_the_shortest_and_at_least_as_strong(self):
        # Class is carried by the pulse count, so at high confidence every class
        # is allowed to reach full (capped) duty. crash keeps the highest floor,
        # so it is never the weakest, and it is always the shortest (one pulse).
        crash_hi = build_command("crash", 1.0, DIR_CENTER)
        horn_hi = build_command("horn", 1.0, DIR_CENTER)
        self.assertEqual(len(crash_hi.steps), 1)
        self.assertGreaterEqual(crash_hi.intensity, horn_hi.intensity)
        # At low confidence crash's higher floor makes it the strongest.
        crash_lo = build_command("crash", 0.5, DIR_CENTER)
        horn_lo = build_command("horn", 0.5, DIR_CENTER)
        self.assertGreater(crash_lo.intensity, horn_lo.intensity)


class DirectionEncodingTests(unittest.TestCase):
    def test_left_and_right_use_one_motor(self):
        for direction, mask in ((DIR_LEFT, MASK_LEFT), (DIR_RIGHT, MASK_RIGHT)):
            self.assertEqual(build_command("siren", 0.8, direction).mask, mask)

    def test_center_uses_both_motors_together(self):
        self.assertEqual(build_command("siren", 0.8, DIR_CENTER).mask, MASK_BOTH)

    def test_the_three_directions_are_distinguishable(self):
        masks = {build_command("siren", 0.8, d).mask for d in DIRECTIONS}
        self.assertEqual(len(masks), 3, masks)

    def test_the_gap_between_pulses_is_real(self):
        # Without a gap, three pulses would be felt as one long buzz and the
        # class cue would be lost.
        command = build_command("siren", 0.8, DIR_CENTER)
        self.assertGreater(command.steps[0].off_ms, 0)
        # ...but the pattern must not end on a gap, or the belt waits for nothing.
        self.assertEqual(command.steps[-1].off_ms, 0)

    def test_duration_does_not_depend_on_direction(self):
        for label in DANGER_LABELS:
            durations = {build_command(label, 0.8, d).duration_ms for d in DIRECTIONS}
            self.assertEqual(len(durations), 1, f"{label}: {durations}")

    def test_every_pattern_fits_one_ble_write(self):
        for direction in DIRECTIONS:
            for label in DANGER_LABELS:
                packet = encode(1, build_command(label, 1.0, direction))
                self.assertLessEqual(len(packet), MAX_PACKET_BYTES,
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
    def test_timeline_shows_one_sided_patterns(self):
        text = render_timeline(build_command("horn", 0.8, DIR_LEFT))
        left, right = text.splitlines()[1], text.splitlines()[2]
        self.assertTrue(left.startswith("L |#"))
        # The right motor must stay idle for a left-only alert.
        self.assertNotIn("#", right)

    def test_timeline_shows_center_aligned(self):
        text = render_timeline(build_command("horn", 0.8, DIR_CENTER))
        left, right = text.splitlines()[1], text.splitlines()[2]
        self.assertEqual(left[2:], right[2:])

    def test_timeline_handles_stop(self):
        from laptop.protocol import STOP_COMMAND
        self.assertIn("STOP", render_timeline(STOP_COMMAND))

    def test_explain_reports_suppression(self):
        self.assertIn("suppressed", explain("normal", 0.9, DIR_LEFT))
        self.assertIn("CENTER", explain("siren", 0.9, DIR_CENTER))


if __name__ == "__main__":
    unittest.main()
