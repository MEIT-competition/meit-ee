"""Calibration for the real belt.

Two separate things live here.

1. sync offset   : constant integer-sample skew between the two I2S peripherals.
                   Must be measured once on real hardware and then subtracted.
2. tau templates : the analytic far-field model assumes free space. A belt on a
                   human torso is not free space (diffraction, shadowing). The
                   robust MVP answer is to record the measured tau vector for
                   each of the 8 directions and classify by nearest template.
                   This absorbs geometry error AND body effects at once.
"""
import json
import numpy as np

from direction_4mic import (DIRECTION_NAMES, PAIRS_2, SPEED_OF_SOUND_MPS,
                            MIC_POSITIONS_M, TAU_MARGIN)
from gcc_phat import estimate_delay


# ----------------------------------------------------------- 1. sync offset
def measure_sync_offsets(channels, fs, ref=0, max_tau=0.05):
    """Feed this 4 channels recorded with all mics BUNDLED TOGETHER.

    Physical TDoA is then ~0, so whatever lag comes out is pure I2S skew.
    Run it several times across reboots: if the numbers are stable, hardcode
    them; if they jitter per boot, you must re-sync at every boot.

    max_tau default is +/-50ms (wide) -- this first pass is meant to catch
    a badly-off starting DMA frame (a large, possibly multi-ms jump), not
    just fine sample-level skew. Once a first run comes back with small,
    stable offsets, narrow this to a few ms for routine re-checks; a wide
    window costs nothing but search time.
    """
    out = {}
    for i in range(channels.shape[0]):
        if i == ref:
            out[i] = 0.0
            continue
        tau, conf = estimate_delay(channels[i], channels[ref], fs,
                                   max_tau=max_tau, subsample=False)
        out[i] = round(tau * fs)
        print(f"ch{i} vs ch{ref}: {out[i]:+.0f} samples (conf {conf:.2f})")
    return out


def apply_sync_offsets(channels, offsets):
    return np.stack([np.roll(channels[i], -int(offsets.get(i, 0)))
                     for i in range(channels.shape[0])])


# -------------------------------------------------------- 2. tau templates
def tau_vector(channels, fs, pairs=PAIRS_2, positions=MIC_POSITIONS_M):
    P = np.asarray(positions, float)
    v, c = [], []
    for (i, j) in pairs:
        d = float(np.linalg.norm(P[j] - P[i]))
        tau, conf = estimate_delay(channels[i], channels[j], fs,
                                   max_tau=TAU_MARGIN * d / SPEED_OF_SOUND_MPS)
        v.append(tau)
        c.append(conf)
    return np.array(v), float(np.mean(c))


def build_templates(recordings, fs, pairs=PAIRS_2):
    """recordings: {direction_index: [ (4,N) array, ... ]}  (a few claps each)."""
    tpl = {}
    for idx, clips in recordings.items():
        vs = [tau_vector(c, fs, pairs)[0] for c in clips]
        tpl[int(idx)] = np.median(np.stack(vs), axis=0).tolist()
    return tpl


def classify(channels, fs, templates, pairs=PAIRS_2, min_conf=0.10):
    v, conf = tau_vector(channels, fs, pairs)
    if conf < min_conf:
        return -1, "UNKNOWN", conf
    keys = sorted(templates)
    T = np.array([templates[k] for k in keys])
    # scale-normalise: loudness/distance should not change the direction
    n = np.linalg.norm(v)
    vv = v / n if n > 1e-12 else v
    TT = T / np.maximum(np.linalg.norm(T, axis=1, keepdims=True), 1e-12)
    idx = keys[int(np.argmin(np.linalg.norm(TT - vv, axis=1)))]
    return idx, DIRECTION_NAMES[idx], conf


def save(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)


def load(path):
    with open(path) as f:
        d = json.load(f)
    return {int(k): v for k, v in d.items()}
