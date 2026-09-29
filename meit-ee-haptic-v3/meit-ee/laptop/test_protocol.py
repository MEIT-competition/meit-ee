"""CMD v2/v3 wire-format tests.

The firmware parser is tested separately against golden packets generated from
this module (``firmware/tests/run_cmd_parse_tests.py``), so these tests cover the
Python side's own invariants: the encoding is reversible, invalid commands are
refused before they reach BLE, and the packet stays inside the single-write
budget the belt depends on.
"""
import unittest

from laptop.protocol import (
    CMD_MAGIC,
    CMD_VERSION_V2,
    CMD_VERSION_V3,
    DIR_BACK,
    DIR_CENTER,
    DIR_FRONT,
    DIR_LEFT,
    DIR_RIGHT,
    DIR_STOP,
    MASK_BOTH,
    MASK_LEFT,
    MASK_RIGHT,
    PATTERN_MAX_PAIRS_V2,
    PATTERN_MAX_STEPS,
    STOP_COMMAND,
    V3_MAX_BYTES,
    MotorCommand,
    MotorStep,
    ProtocolError,
    decode,
    direction_name,
    encode,
    encode_v2,
    encode_v3,
    normalize_direction,
    steps_from_pairs,
    to_v2_command,
)


def sweep(pulses=1, on_ms=200, gap_ms=100, intensity=80):
    """A BACK-shaped command: alternating half-pulses with no internal gap."""
    steps = []
    for index in range(pulses):
        last = index == pulses - 1
        steps.append(MotorStep(MASK_LEFT, on_ms // 2, 0))
        steps.append(MotorStep(MASK_RIGHT, on_ms // 2, 0 if last else gap_ms))
    return MotorCommand(DIR_BACK, intensity, tuple(steps))


class DirectionTests(unittest.TestCase):
    def test_four_way_mapping(self):
        self.assertEqual(normalize_direction("left"), DIR_LEFT)
        self.assertEqual(normalize_direction("RIGHT"), DIR_RIGHT)
        self.assertEqual(normalize_direction(" Front "), DIR_FRONT)
        self.assertEqual(normalize_direction("back"), DIR_BACK)

    def test_center_is_front(self):
        # v2 called "both motors" CENTER; it is the same code and the same
        # sensation, so old callers keep working.
        self.assertEqual(DIR_CENTER, DIR_FRONT)
        self.assertEqual(normalize_direction("center"), DIR_FRONT)
        self.assertEqual(normalize_direction("centre"), DIR_FRONT)

    def test_unknown_suppresses(self):
        for value in ("unknown", "", "sideways", None, 42):
            self.assertEqual(normalize_direction(value), DIR_STOP, value)

    def test_back_folds_into_front_without_v3(self):
        self.assertEqual(normalize_direction("back", allow_back=False), DIR_FRONT)
        self.assertEqual(normalize_direction("left", allow_back=False), DIR_LEFT)

    def test_direction_name_rejects_garbage(self):
        self.assertEqual(direction_name(DIR_BACK), "BACK")
        with self.assertRaises(ProtocolError):
            direction_name(99)


class V3RoundTripTests(unittest.TestCase):
    def test_single_step(self):
        command = MotorCommand(DIR_LEFT, 75, (MotorStep(MASK_LEFT, 500, 0),))
        decoded = decode(encode_v3(7, command))
        self.assertEqual(decoded.version, CMD_VERSION_V3)
        self.assertEqual(decoded.sequence, 7)
        self.assertEqual(decoded.command, command)

    def test_sweep_preserves_step_order(self):
        command = sweep(pulses=3, on_ms=260, gap_ms=150)
        decoded = decode(encode_v3(1, command)).command
        self.assertEqual(decoded, command)
        # The alternation is the BACK cue; losing it would make BACK feel like
        # FRONT, which is the specific failure this asserts against.
        self.assertEqual([step.mask for step in decoded.steps],
                         [MASK_LEFT, MASK_RIGHT] * 3)

    def test_largest_pattern_fits_one_ble_write(self):
        command = sweep(pulses=PATTERN_MAX_STEPS // 2)
        packet = encode_v3(1, command)
        self.assertEqual(len(packet), V3_MAX_BYTES)
        # 20 bytes is the most an ATT Write Request carries on the default
        # 23-byte MTU, so the belt never depends on MTU negotiation.
        self.assertEqual(V3_MAX_BYTES, 20)

    def test_header_fields(self):
        packet = encode_v3(3, MotorCommand(DIR_BACK, 90, sweep().steps))
        self.assertEqual(packet[0], CMD_MAGIC)
        self.assertEqual(packet[1], CMD_VERSION_V3)
        self.assertEqual(packet[2], 3)
        self.assertEqual(packet[3], DIR_BACK)
        self.assertEqual(packet[4], 90)

    def test_stop(self):
        packet = encode_v3(9, STOP_COMMAND)
        decoded = decode(packet)
        self.assertEqual(decoded.command, STOP_COMMAND)
        self.assertTrue(decoded.command.is_stop)
        self.assertEqual(len(packet), 8)


class V2RoundTripTests(unittest.TestCase):
    def test_uniform_pattern(self):
        command = MotorCommand(DIR_RIGHT, 60, steps_from_pairs(MASK_RIGHT,
                                                               [[240, 140], [240, 0]]))
        decoded = decode(encode_v2(2, command))
        self.assertEqual(decoded.version, CMD_VERSION_V2)
        self.assertEqual(decoded.command, command)

    def test_stop(self):
        packet = encode_v2(1, STOP_COMMAND)
        self.assertEqual(len(packet), 6)
        self.assertEqual(decode(packet).command, STOP_COMMAND)

    def test_refuses_back(self):
        # Silently downgrading here would hide a wrong-direction alert, so the
        # caller has to make the decision (and log it) via to_v2_command.
        with self.assertRaises(ProtocolError):
            encode_v2(1, sweep())

    def test_refuses_mixed_masks(self):
        mixed = MotorCommand(DIR_FRONT, 70, (MotorStep(MASK_LEFT, 200, 0),
                                             MotorStep(MASK_RIGHT, 200, 0)))
        with self.assertRaises(ProtocolError):
            encode_v2(1, mixed)

    def test_refuses_too_many_pairs(self):
        long_pattern = MotorCommand(
            DIR_LEFT, 70,
            steps_from_pairs(MASK_LEFT, [[100, 100]] * (PATTERN_MAX_PAIRS_V2 + 1)))
        with self.assertRaises(ProtocolError):
            encode_v2(1, long_pattern)


class DowngradeTests(unittest.TestCase):
    def test_sweep_keeps_its_pulse_count(self):
        # Pulse count is what encodes the danger class, so it must survive the
        # downgrade even though the direction cannot.
        original = sweep(pulses=3, on_ms=260, gap_ms=150)
        downgraded = to_v2_command(original)
        self.assertEqual(downgraded.direction, DIR_FRONT)
        self.assertEqual(len(downgraded.steps), 3)
        self.assertEqual([step.mask for step in downgraded.steps], [MASK_BOTH] * 3)
        self.assertEqual([step.on_ms for step in downgraded.steps], [260, 260, 260])
        self.assertEqual([step.off_ms for step in downgraded.steps], [150, 150, 0])
        self.assertEqual(downgraded.duration_ms, original.duration_ms)

    def test_single_sided_is_unchanged(self):
        command = MotorCommand(DIR_LEFT, 80, steps_from_pairs(MASK_LEFT, [[240, 0]]))
        self.assertEqual(to_v2_command(command), command)

    def test_stop_is_unchanged(self):
        self.assertEqual(to_v2_command(STOP_COMMAND), STOP_COMMAND)

    def test_truncates_to_the_v2_budget_without_a_trailing_gap(self):
        long_pattern = MotorCommand(
            DIR_LEFT, 70,
            steps_from_pairs(MASK_LEFT, [[100, 100]] * PATTERN_MAX_STEPS))
        downgraded = to_v2_command(long_pattern)
        self.assertEqual(len(downgraded.steps), PATTERN_MAX_PAIRS_V2)
        # A truncated tail must not leave the sequencer waiting out a gap.
        self.assertEqual(downgraded.steps[-1].off_ms, 0)
        encode_v2(1, downgraded)  # must be encodable

    def test_encode_downgrades_for_version_2(self):
        packet = encode(1, sweep(), CMD_VERSION_V2)
        self.assertEqual(decode(packet).command.direction, DIR_FRONT)


class ValidationTests(unittest.TestCase):
    def test_step_time_grid(self):
        with self.assertRaises(ProtocolError):
            MotorStep(MASK_LEFT, 205, 0).validate()
        with self.assertRaises(ProtocolError):
            MotorStep(MASK_LEFT, 200, 15).validate()

    def test_step_time_bounds(self):
        with self.assertRaises(ProtocolError):
            MotorStep(MASK_LEFT, 0, 0).validate()
        with self.assertRaises(ProtocolError):
            MotorStep(MASK_LEFT, 2560, 0).validate()

    def test_step_mask_must_select_a_motor(self):
        with self.assertRaises(ProtocolError):
            MotorStep(0, 200, 0).validate()

    def test_intensity_bounds(self):
        for bad in (0, 101, -1):
            with self.assertRaises(ProtocolError):
                MotorCommand(DIR_LEFT, bad, steps_from_pairs(MASK_LEFT, [[200, 0]]))

    def test_stop_must_be_empty(self):
        with self.assertRaises(ProtocolError):
            MotorCommand(DIR_STOP, 0, steps_from_pairs(MASK_LEFT, [[200, 0]]))
        with self.assertRaises(ProtocolError):
            MotorCommand(DIR_STOP, 50, ())

    def test_pattern_length_bounds(self):
        with self.assertRaises(ProtocolError):
            MotorCommand(DIR_LEFT, 50, ())
        with self.assertRaises(ProtocolError):
            MotorCommand(DIR_LEFT, 50,
                         steps_from_pairs(MASK_LEFT, [[100, 0]] * (PATTERN_MAX_STEPS + 1)))

    def test_sequence_must_be_uint8(self):
        command = MotorCommand(DIR_LEFT, 50, steps_from_pairs(MASK_LEFT, [[200, 0]]))
        for bad in (-1, 256, "1"):
            with self.assertRaises(ProtocolError):
                encode_v3(bad, command)


class DecodeRejectionTests(unittest.TestCase):
    """Rejections mirror the firmware's, so these tests say something about it."""

    def setUp(self):
        self.valid = bytearray(encode_v3(1, MotorCommand(
            DIR_LEFT, 80, (MotorStep(MASK_LEFT, 300, 0),))))

    def mutate(self, index, value):
        data = bytearray(self.valid)
        data[index] = value
        return bytes(data)

    def test_baseline_parses(self):
        decode(bytes(self.valid))

    def test_bad_magic(self):
        with self.assertRaises(ProtocolError):
            decode(self.mutate(0, 0x5A))

    def test_unknown_version(self):
        with self.assertRaises(ProtocolError):
            decode(self.mutate(1, 0x04))

    def test_direction_out_of_range(self):
        with self.assertRaises(ProtocolError):
            decode(self.mutate(3, 5))

    def test_intensity_out_of_range(self):
        with self.assertRaises(ProtocolError):
            decode(self.mutate(4, 101))

    def test_zero_mask(self):
        with self.assertRaises(ProtocolError):
            decode(self.mutate(6, 0x00))

    def test_mask_bits_beyond_step_count(self):
        with self.assertRaises(ProtocolError):
            decode(self.mutate(6, MASK_LEFT | (MASK_RIGHT << 2)))

    def test_zero_on_time(self):
        with self.assertRaises(ProtocolError):
            decode(self.mutate(8, 0))

    def test_length_disagreement(self):
        with self.assertRaises(ProtocolError):
            decode(bytes(self.valid)[:-1])
        with self.assertRaises(ProtocolError):
            decode(bytes(self.valid) + b"\x00\x00")

    def test_too_short(self):
        with self.assertRaises(ProtocolError):
            decode(b"\xa5\x03\x01\x01\x50")

    def test_non_stop_with_zero_steps(self):
        with self.assertRaises(ProtocolError):
            decode(bytes([CMD_MAGIC, CMD_VERSION_V3, 1, DIR_LEFT, 80, 0, 0, 0]))

    def test_back_in_a_v2_packet(self):
        with self.assertRaises(ProtocolError):
            decode(bytes([CMD_MAGIC, CMD_VERSION_V2, 1, DIR_BACK, 80, 1, 30, 0]))

    def test_v2_pattern_over_its_own_budget(self):
        payload = bytes([CMD_MAGIC, CMD_VERSION_V2, 1, DIR_LEFT, 80,
                         PATTERN_MAX_PAIRS_V2 + 1]) + bytes([20, 5] * 5)
        with self.assertRaises(ProtocolError):
            decode(payload)


class HelperTests(unittest.TestCase):
    def test_duration_includes_gaps(self):
        command = MotorCommand(DIR_LEFT, 50,
                               steps_from_pairs(MASK_LEFT, [[200, 100], [200, 0]]))
        self.assertEqual(command.duration_ms, 500)

    def test_uniform_mask_detection(self):
        self.assertEqual(
            MotorCommand(DIR_FRONT, 50,
                         steps_from_pairs(MASK_BOTH, [[200, 0]])).uniform_mask,
            MASK_BOTH)
        self.assertIsNone(sweep().uniform_mask)

    def test_unsupported_version(self):
        command = MotorCommand(DIR_LEFT, 50, steps_from_pairs(MASK_LEFT, [[200, 0]]))
        with self.assertRaises(ProtocolError):
            encode(1, command, 4)


if __name__ == "__main__":
    unittest.main()
