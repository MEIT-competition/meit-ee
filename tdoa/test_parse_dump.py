import tempfile
import unittest
from pathlib import Path

import numpy as np

from parse_dump import parse_dump


class ParseDumpTest(unittest.TestCase):
    def test_monitor_prefix_and_channel_order(self):
        text = (
            "I (1) app: booted\n"
            "MEIT_DUMP_BEGIN,fs=48000,channels=FRONT|RIGHT|BACK|LEFT,samples=2\n"
            "monitor prefix MEIT_RAW,0,0.1,0.2,0.3,0.4\n"
            "MEIT_RAW,1,1.1,1.2,1.3,1.4\n"
            "MEIT_DUMP_END,samples=2\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.txt"
            path.write_text(text, encoding="utf-8")
            channels = parse_dump(path)
        self.assertEqual(channels.shape, (4, 2))
        np.testing.assert_allclose(channels[:, 0], [0.1, 0.2, 0.3, 0.4])
        np.testing.assert_allclose(channels[:, 1], [1.1, 1.2, 1.3, 1.4])

    def test_missing_sample_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.txt"
            path.write_text("MEIT_RAW,1,0,0,0,0\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected sample 0"):
                parse_dump(path)


if __name__ == "__main__":
    unittest.main()
