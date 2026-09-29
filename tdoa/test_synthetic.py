"""Regression checks for two-microphone delay polarity and center band."""
import unittest
import numpy as np
from direction_2mic import (estimate_direction, direction_from_delay,
    DIR_LEFT, DIR_RIGHT, DIR_BACK, TDOA_THRESHOLD_SAMPLES, FS)

class TwoMicTest(unittest.TestCase):
    def test_delay_sign_and_center(self):
        rng = np.random.default_rng(42)
        base = rng.normal(0, .1, 1200)
        for delay, expected in [(-8, DIR_LEFT), (8, DIR_RIGHT), (0, DIR_BACK),
                                (-1, DIR_BACK), (1, DIR_BACK)]:
            # LEFT = base shifted by delay relative to RIGHT; no circular wrap.
            x = np.stack([base[80-delay:1104-delay], base[80:1104]])
            result = estimate_direction(x)
            self.assertEqual(result.index, expected, result)
            self.assertGreater(result.confidence, 0)
            self.assertAlmostEqual(result.tau_lr_s * FS, delay, delta=.25)

    def test_threshold_boundaries(self):
        t = TDOA_THRESHOLD_SAMPLES
        for delay, expected in [(-t, DIR_BACK), (t, DIR_BACK), (0, DIR_BACK),
                                (np.nextafter(-t, -np.inf), DIR_LEFT),
                                (np.nextafter(t, np.inf), DIR_RIGHT)]:
            self.assertEqual(direction_from_delay(delay), expected)

    def test_silence_is_unknown(self):
        result = estimate_direction(np.zeros((2, 1024)))
        self.assertEqual(result.index, -1)
        self.assertEqual(result.confidence, 0)

    def test_old_channel_shape_rejected(self):
        with self.assertRaises(ValueError):
            estimate_direction(np.zeros((4, 1024)))

if __name__ == '__main__':
    unittest.main()
