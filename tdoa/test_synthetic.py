"""Synthetic TDoA tests.

Part 1 is the original 8-direction check (kept as-is).
Part 2 is the stress suite. Part 1 passing means "sign conventions are right".
Only Part 2 tells you anything about real-world behaviour.
"""
import math
import numpy as np

from direction_4mic import (
    BACK, FRONT, LEFT, RIGHT,
    MIC_POSITIONS_M, MIC_RADIUS_M, SPEED_OF_SOUND_MPS,
    PAIRS_2, PAIRS_6, estimate_direction,
)

FS = 48000
N = 4096


def fractional_delay(sig, delay_samples):
    n = len(sig)
    freqs = np.fft.rfftfreq(n)
    spectrum = np.fft.rfft(sig)
    return np.fft.irfft(spectrum * np.exp(-2j * np.pi * freqs * delay_samples), n=n)


def make_source(angle_deg, kind="white", f0=None, snr_db=None, seed=42,
                shadow=0.0, reverb=False, geom_err_m=0.0, positions=None):
    """Synthesize 4 far-field channels.

    shadow      : torso diffraction. 0.45 = path around the body is 45% longer
                  for the mic facing away, plus up to -8 dB shadowing.
    geom_err_m  : random per-mic placement error (belt is never a perfect cross).
    """
    rng = np.random.default_rng(seed)
    t = np.arange(N) / FS

    if kind == "white":
        base = rng.normal(0, 1, N)
    elif kind == "tone":
        base = np.sin(2 * np.pi * f0 * t)
    elif kind == "harmonic":                      # car horn / siren-like
        base = sum(np.sin(2 * np.pi * f0 * k * t + k) for k in (1, 2, 3))
    else:                                         # 300-3000 Hz band noise
        x = rng.normal(0, 1, N)
        X = np.fft.rfft(x)
        f = np.fft.rfftfreq(N, 1 / FS)
        X[(f < 300) | (f > 3000)] = 0
        base = np.fft.irfft(X, N)
    base = base / np.std(base) * np.hanning(N)

    a = math.radians(angle_deg)
    ux, uy = math.sin(a), math.cos(a)

    P = np.array(MIC_POSITIONS_M if positions is None else positions, float)
    if geom_err_m:
        P = P + np.random.default_rng(seed + 991).normal(0, geom_err_m, P.shape)

    proj = P @ np.array([ux, uy])
    pmax = proj.max()
    span = 2.0 * MIC_RADIUS_M

    channels = []
    for i in range(4):
        frac = (pmax - proj[i]) / span
        extra = shadow * frac * span / SPEED_OF_SOUND_MPS
        att = 10 ** (-8.0 * frac / 20.0) if shadow else 1.0
        s = fractional_delay(base, ((pmax - proj[i]) / SPEED_OF_SOUND_MPS + extra) * FS) * att
        if reverb:
            for _ in range(3):
                s = s + 0.4 * fractional_delay(base, rng.random() * 0.02 * FS) * rng.choice([-1, 1])
        if snr_db is not None:
            s = s + rng.normal(0, np.std(s) * 10 ** (-snr_db / 20), N)
        channels.append(s)
    return np.stack(channels)


# ---------------------------------------------------------------- Part 1
def test_eight_directions():
    expected = {0: "FRONT", 45: "FRONT_RIGHT", 90: "RIGHT", 135: "BACK_RIGHT",
                180: "BACK", 225: "BACK_LEFT", 270: "LEFT", 315: "FRONT_LEFT"}
    passed = 0
    for angle, name in expected.items():
        r = estimate_direction(make_source(angle), FS)
        ok = r.name == name
        passed += ok
        print(f"source={angle:3d}deg expected={name:12s} estimated={r.name:12s} "
              f"angle={r.angle_deg:6.1f}deg conf={r.confidence:.2f} "
              f"{'PASS' if ok else 'FAIL'}")
    print(f"\nPart 1: {passed}/8 passed\n")
    return passed == 8


# ---------------------------------------------------------------- Part 2
def _sweep(pairs=PAIRS_2, step=15, seeds=6, **kw):
    ok = tot = 0
    errs, confs = [], []
    for ang in range(0, 360, step):
        for s in range(seeds):
            r = estimate_direction(make_source(ang, seed=s, **kw), FS, pairs=pairs)
            e = abs((r.angle_deg - ang + 180) % 360 - 180)
            errs.append(e)
            confs.append(r.confidence)
            ok += e <= 22.5
            tot += 1
    return ok / tot * 100, float(np.median(errs)), float(np.mean(confs))


def test_stress():
    cases = [
        ("wideband, clean",                 dict(kind="band")),
        ("wideband, SNR 10 dB",             dict(kind="band", snr_db=10)),
        ("wideband, SNR 0 dB",              dict(kind="band", snr_db=0)),
        ("harmonic horn 400 Hz",            dict(kind="harmonic", f0=400, snr_db=20)),
        ("harmonic siren 700 Hz",           dict(kind="harmonic", f0=700, snr_db=20)),
        ("pure tone 1500 Hz (must be low-conf)", dict(kind="tone", f0=1500, snr_db=20)),
        ("torso shadow",                    dict(kind="band", shadow=0.45, snr_db=20)),
        ("torso shadow + reverb",           dict(kind="band", shadow=0.45, reverb=True, snr_db=20)),
        ("geometry error +/-2 cm",          dict(kind="band", geom_err_m=0.02, snr_db=20)),
        ("shadow + geom err + reverb",      dict(kind="band", shadow=0.45, geom_err_m=0.01,
                                                 reverb=True, snr_db=15)),
    ]
    print(f"{'case':40s} {'2-pair':>18s} {'6-pair':>18s}")
    for label, kw in cases:
        a2, m2, c2 = _sweep(PAIRS_2, **kw)
        a6, m6, c6 = _sweep(PAIRS_6, **kw)
        print(f"{label:40s} {a2:6.1f}% med{m2:5.1f} c{c2:.2f} "
              f"{a6:6.1f}% med{m6:5.1f} c{c6:.2f}")
    print("\nNote: a pure tone cannot be localised by a 16 cm array. The correct")
    print("behaviour is confidence ~0 (stay silent), not a confident wrong answer.")


if __name__ == "__main__":
    eight_ok = test_eight_directions()
    test_stress()
    # Make this script usable as a real regression gate: the previous version
    # printed FAIL but still exited 0, so CI/manual automation could miss a
    # broken 8-direction convention. Stress cases remain informational.
    raise SystemExit(0 if eight_ok else 1)
