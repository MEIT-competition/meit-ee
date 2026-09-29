import unittest

from laptop.ios_motor_bridge import extract_alert
from laptop.protocol import (
    DIR_CENTER, DIR_LEFT, DIR_RIGHT, DIR_STOP,
    decode_motor_cmd, encode_motor_cmd, normalize_direction,
)


class ProtocolTests(unittest.TestCase):
    def test_direction_mapping(self):
        self.assertEqual(normalize_direction("left"), DIR_LEFT)
        self.assertEqual(normalize_direction("right"), DIR_RIGHT)
        self.assertEqual(normalize_direction("center"), DIR_CENTER)
        self.assertEqual(normalize_direction("CENTRE"), DIR_CENTER)
        self.assertEqual(normalize_direction("FRONT"), DIR_CENTER)
        self.assertEqual(normalize_direction("back"), DIR_CENTER)
        self.assertEqual(normalize_direction("unknown"), DIR_STOP)
        self.assertEqual(normalize_direction(None), DIR_STOP)

    def test_roundtrip(self):
        raw = encode_motor_cmd(7, DIR_LEFT, 75, [[220, 120], [220, 0]])
        cmd = decode_motor_cmd(raw)
        self.assertEqual(cmd.sequence, 7)
        self.assertEqual(cmd.direction, DIR_LEFT)
        self.assertEqual(cmd.intensity, 75)
        self.assertEqual(cmd.pattern, [[220, 120], [220, 0]])

    def test_stop_roundtrip(self):
        raw = encode_motor_cmd(8, DIR_STOP, 0, [])
        cmd = decode_motor_cmd(raw)
        self.assertEqual(cmd.sequence, 8)
        self.assertEqual(cmd.direction, DIR_STOP)
        self.assertEqual(cmd.intensity, 0)
        self.assertEqual(cmd.pattern, [])

    def test_completed_danger(self):
        status = {"last_event": {"event_id": "abc", "outcome": "completed",
                                 "direction": "right",
                                 "result": {"label": "siren", "confidence": .9, "danger": True}}}
        alert = extract_alert(status)
        self.assertIsNotNone(alert)
        self.assertEqual(alert.direction, DIR_RIGHT)

    def test_normal_or_unknown_suppressed(self):
        normal = {"last_event": {"event_id": "a", "outcome": "completed", "direction": "left",
                                  "result": {"label": "normal", "danger": False}}}
        unknown = {"last_event": {"event_id": "b", "outcome": "completed", "direction": "unknown",
                                   "result": {"label": "horn", "danger": True}}}
        self.assertIsNone(extract_alert(normal))
        self.assertIsNone(extract_alert(unknown))


if __name__ == "__main__":
    unittest.main()
