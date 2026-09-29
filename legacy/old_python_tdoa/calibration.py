"""Measure center-axis LEFT-minus-RIGHT bias from a stereo .npy capture.

Record repeated broadband sounds on the center axis with the final geometry.
This measures channel/acoustic bias, not inter-peripheral DMA skew.
"""
import argparse
import numpy as np
from direction_2mic import estimate_direction

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('capture')
    p.add_argument('--fs', type=int, default=48000)
    args = p.parse_args()
    channels = np.load(args.capture)
    if channels.ndim != 2 or channels.shape[0] != 2:
        p.error('expected (2,N): LEFT, RIGHT')
    delays = []
    for n in range(0, channels.shape[1] - 1023, 1024):
        d = estimate_direction(channels[:, n:n+1024], args.fs, bias_samples=0)
        if d.confidence > 0:
            delays.append(d.tau_lr_s * args.fs)
    if not delays:
        p.error('no confident frames; check stereo signal before calibration')
    print(f'center bias_samples={np.median(delays):.3f}, '
          f'range=[{min(delays):.3f},{max(delays):.3f}], valid={len(delays)}')
    print('Review TDOA_LR_BIAS_SAMPLES and TDOA_THRESHOLD_SAMPLES manually; '
          'repeat with LEFT/RIGHT sources before accepting the center band.')

if __name__ == '__main__':
    main()
