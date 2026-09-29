"""Lateral pair only. Center-axis delay maps to BACK by operating policy.

TDoA alone cannot disambiguate front/back. Do not infer an azimuth here.
"""
from dataclasses import dataclass
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from laptop.protocol import DIR_LEFT, DIR_RIGHT, DIR_BACK, direction_name
from gcc_phat import estimate_delay

CH_LEFT, CH_RIGHT = 0, 1
FS = 48000
MIC_SPACING_M = 0.16
SPEED_OF_SOUND_MPS = 343.0
TAU_MARGIN = 1.6
TDOA_THRESHOLD_SAMPLES = 2.0
TDOA_LR_BIAS_SAMPLES = 0.0

def direction_from_delay(delay_samples, threshold=TDOA_THRESHOLD_SAMPLES):
    if not np.isfinite(threshold) or threshold < 0:
        raise ValueError("threshold must be finite and nonnegative")
    if not np.isfinite(delay_samples):
        return -1
    if delay_samples < -threshold:
        return DIR_LEFT
    if delay_samples > threshold:
        return DIR_RIGHT
    return DIR_BACK

@dataclass
class DirectionResult:
    index: int  # BLE/AI value (6,2,4), -1 for unresolved
    name: str
    tau_lr_s: float
    confidence: float

def estimate_direction(channels, fs=FS, threshold=TDOA_THRESHOLD_SAMPLES,
                       bias_samples=TDOA_LR_BIAS_SAMPLES, min_conf=0.15, **kw):
    x = np.asarray(channels, dtype=np.float64)
    if x.ndim != 2 or x.shape[0] != 2 or x.shape[1] < 2:
        raise ValueError("channels must have shape (2, N), N >= 2")
    # Match firmware preprocessing. Confidence still uses the Python median
    # noise statistic; firmware uses a mean, so calibrate firmware separately.
    x = x - x.mean(axis=1, keepdims=True)
    loud = 20 * np.log10(np.sqrt(np.mean(x*x, axis=1)).max() + 1e-12)
    tau, conf = estimate_delay(x[CH_LEFT] * np.hanning(x.shape[1]),
                               x[CH_RIGHT] * np.hanning(x.shape[1]), fs,
                               max_tau=TAU_MARGIN * MIC_SPACING_M / SPEED_OF_SOUND_MPS,
                               **kw)
    tau -= bias_samples / fs
    idx = direction_from_delay(tau * fs, threshold)
    if loud < -60 or conf < min_conf or not np.isfinite(conf):
        idx, conf = -1, 0.0
    return DirectionResult(idx, direction_name(idx), tau, conf)
