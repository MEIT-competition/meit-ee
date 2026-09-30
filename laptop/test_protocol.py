"""CMD v2 wire-format tests.

The firmware parser is tested separately against golden packets generated from
this module (``firmware/tests/run_cmd_parse_tests.py``), so these tests cover the
Python side's own invariants: the encoding is reversible, invalid commands are
refused before they reach BLE, and the packet stays inside the single-write
budget the belt depends on.
"""
import unittest

from laptop.protocol import (
    CMD_MAGIC,
    CMD_VERSION,
    DIR_CENTER,
    DIR_LEFT,
    DIR_RIGHT,
    DIR_STOP,
    MASK_BOTH,
    MASK_LEFT,
    MASK_RIGHT,
    MAX_PACKET_BYTES,
    PATTERN_MAX_STEPS,
    STOP_COMMAND,
    MotorCommand,
    MotorStep,
    ProtocolError,
    decode,
    direction_name,
    encode,
    mask_for_direction,
    normalize_direction,
    steps_from_pairs,
)


class DirectionTests(unittest.TestCase):
    def test_three_way_mapping(self):
        self.assertEqual(normalize_direction("left"), DIR_LEFT)
        self.assertEqual(normalize_direction("RIGHT"), DIR_RIGHT)
        self.assertEqual(normalize_direction(" Center "), DIR_CENTER)

    def test_spelling_variants(self):
        # iOS says `center`; `centre` and `front` are accepted so no caller has
        # to translate.
        for value in ("center", "centre", "front"):
            self.assertEqual(normalize_direction(value), DIR_CENTER, value)

    def test_back_is_not_a_direction(self):
        # The belt cannot point behind the wearer, so `back` is suppressed rather
        # than quietly rendered as something it does not mean.
        self.assertEqual(normalize_direction("back"), DIR_STOP)

    def test_unavailable_suppresses(self):
        # iOS reports `unavailable` when its own margin gate is not satisfied.
        for value in ("unavailable", "unknown", "", "sideways", None, 42):
            self.assertEqual(normalize_direction(value), DIR_STOP, value)

    def test_direction_name(self):
        self.assertEqual(direction_name(DIR_CENTER), "CENTER")
        with self.assertRaises(ProtocolError):
            direction_name(99)

    def test_masks(self):
        self.assertEqual(mask_for_direction(DIR_LEFT), MASK_LEFT)
        self.assertEqual(mask_for_direction(DIR_RIGHT), MASK_RIGHT)
        self.assertEqual(mask_for_direction(DIR_CENTER), MASK_BOTH)

    def test_stop_drives_no_motors(self):
        with self.assertRaises(ProtocolError):
            mask_for_direction(DIR_STOP)
        self.assertEqual(STOP_COMMAND.mask, 0)


class RoundTripTests(unittest.TestCase):
    def test_single_step(self):
        command = MotorCommand(DIR_LEFT, 75, (MotorStep(500, 0),))
        decoded = decode(encode(7, command))
        self.assertEqual(decoded.version, CMD_VERSION)
        self.assertEqual(decoded.sequence, 7)
        self.assertEqual(decoded.command, command)

    def test_multi_step(self):
        command = MotorCommand(DIR_CENTER, 92,
                               steps_from_pairs([[260, 150], [260, 150], [260, 0]]))
        self.assertEqual(decode(encode(1, command)).command, command)

    def test_every_direction(self):
        for direction in (DIR_LEFT, DIR_CENTER, DIR_RIGHT):
            command = MotorCommand(direction, 80, steps_from_pairs([[240, 0]]))
            decoded = decode(encode(1, command)).command
            self.assertEqual(decoded.direction, direction)
            self.assertEqual(decoded.mask, mask_for_direction(direction))

    def test_header_fields(self):
        packet = encode(3, MotorCommand(DIR_RIGHT, 90, steps_from_pairs([[240, 0]])))
        self.assertEqual(packet[0], CMD_MAGIC)
        self.assertEqual(packet[1], CMD_VERSION)
        self.assertEqual(packet[2], 3)
        self.assertEqual(packet[3], DIR_RIGHT)
        self.assertEqual(packet[4], 90)
        self.assertEqual(packet[5], 1)

    def test_stop(self):
        packet = encode(9, STOP_COMMAND)
        self.assertEqual(len(packet), 6)
        decoded = decode(packet)
        self.assertEqual(decoded.command, STOP_COMMAND)
        self.assertTrue(decoded.command.is_stop)

    def test_largest_pattern_fits_one_ble_write(self):
        command = MotorCommand(DIR_CENTER, 100,
                               steps_from_pairs([[260, 150]] * (PATTERN_MAX_STEPS - 1)
                                                + [[260, 0]]))
        packet = encode(1, command)
        self.assertEqual(len(packet), MAX_PACKET_BYTES)
        # 20 bytes is the most an ATT Write Request carries on the default
        # 23-byte MTU, so the belt never depends on MTU negotiation.
        self.assertLessEqual(MAX_PACKET_BYTES, 20)


class ValidationTests(unittest.TestCase):
    def test_step_time_grid(self):
        with self.assertRaises(ProtocolError):
            MotorStep(205, 0).validate()
        with self.assertRaises(ProtocolError):
            MotorStep(200, 15).validate()

    def test_step_time_bounds(self):
        with self.assertRaises(ProtocolError):
            MotorStep(0, 0).validate()
        with self.assertRaises(ProtocolError):
            MotorStep(2560, 0).validate()

    def test_intensity_bounds(self):
        for bad in (0, 101, -1):
            with self.assertRaises(ProtocolError):
                MotorCommand(DIR_LEFT, bad, steps_from_pairs([[200, 0]]))

    def test_stop_must_be_empty(self):
        with self.assertRaises(ProtocolError):
            MotorCommand(DIR_STOP, 0, steps_from_pairs([[200, 0]]))
        with self.assertRaises(ProtocolError):
            MotorCommand(DIR_STOP, 50, ())

    def test_pattern_length_bounds(self):
        with self.assertRaises(ProtocolError):
            MotorCommand(DIR_LEFT, 50, ())
        with self.assertRaises(ProtocolError):
            MotorCommand(DIR_LEFT, 50,
                         steps_from_pairs([[100, 0]] * (PATTERN_MAX_STEPS + 1)))

    def test_invalid_direction(self):
        with self.assertRaises(ProtocolError):
            MotorCommand(9, 50, steps_from_pairs([[200, 0]]))

    def test_sequence_must_be_uint8(self):
        command = MotorCommand(DIR_LEFT, 50, steps_from_pairs([[200, 0]]))
        for bad in (-1, 256, "1"):
            with self.assertRaises(ProtocolError):
                encode(bad, command)


class DecodeRejectionTests(unittest.TestCase):
    """Rejections mirror the firmware's, so these say something about it too."""

    def setUp(self):
        self.valid = bytearray(encode(1, MotorCommand(DIR_LEFT, 80,
                                                      (MotorStep(300, 0),))))

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
            decode(self.mutate(1, 0x03))

    def test_direction_out_of_range(self):
        with self.assertRaises(ProtocolError):
            decode(self.mutate(3, 4))

    def test_intensity_out_of_range(self):
        with self.assertRaises(ProtocolError):
            decode(self.mutate(4, 101))

    def test_zero_on_time(self):
        with self.assertRaises(ProtocolError):
            decode(self.mutate(6, 0))

    def test_length_disagreement(self):
        with self.assertRaises(ProtocolError):
            decode(bytes(self.valid)[:-1])
        with self.assertRaises(ProtocolError):
            decode(bytes(self.valid) + b"\x00\x00")

    def test_too_short(self):
        with self.assertRaises(ProtocolError):
            decode(b"\xa5\x02\x01\x01\x50")

    def test_non_stop_with_zero_steps(self):
        with self.assertRaises(ProtocolError):
            decode(bytes([CMD_MAGIC, CMD_VERSION, 1, DIR_LEFT, 80, 0]))

    def test_stop_with_intensity_or_pattern(self):
        with self.assertRaises(ProtocolError):
            decode(bytes([CMD_MAGIC, CMD_VERSION, 1, DIR_STOP, 50, 0]))
        with self.assertRaises(ProtocolError):
            decode(bytes([CMD_MAGIC, CMD_VERSION, 1, DIR_STOP, 0, 1, 30, 0]))

    def test_too_many_steps(self):
        payload = bytes([CMD_MAGIC, CMD_VERSION, 1, DIR_LEFT, 80,
                         PATTERN_MAX_STEPS + 1]) + bytes([20, 5] * (PATTERN_MAX_STEPS + 1))
        with self.assertRaises(ProtocolError):
            decode(payload)


class HelperTests(unittest.TestCase):
    def test_duration_includes_gaps(self):
        command = MotorCommand(DIR_LEFT, 50, steps_from_pairs([[200, 100], [200, 0]]))
        self.assertEqual(command.duration_ms, 500)

    def test_describe_names_the_motors(self):
        self.assertIn("L[", MotorCommand(DIR_LEFT, 50,
                                         steps_from_pairs([[200, 0]])).describe())
        self.assertIn("LR[", MotorCommand(DIR_CENTER, 50,
                                          steps_from_pairs([[200, 0]])).describe())
        self.assertEqual(STOP_COMMAND.describe(), "STOP")


if __name__ == "__main__":
    unittest.main()
