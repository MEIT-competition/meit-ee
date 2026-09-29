import numpy as np

def estimate_delay(sig, ref, fs, max_tau=None, interp=1, eps=1e-12,
                   fmin=200.0, fmax=4000.0, beta=1.0, bin_floor_db=-30.0,
                   subsample=True):
    """GCC-PHAT delay estimate (v2).

    tau > 0  =>  `sig` arrived LATER than `ref`   (same convention as v1)

    beta         : whitening exponent. 1.0 = pure PHAT, 0.0 = plain cross-corr.
                   0.6~0.8 is much safer for narrowband sources.
    bin_floor_db : bins whose cross-power is below (peak + bin_floor_db) are
                   discarded. This is what stops PHAT from amplifying
                   noise-only bins on tonal sources (horns, sirens).
    subsample    : parabolic interpolation around the integer peak
                   (cheap; this is what the firmware will do).
    """
    sig = np.asarray(sig, dtype=np.float64)
    ref = np.asarray(ref, dtype=np.float64)
    n = len(sig) + len(ref)

    S = np.fft.rfft(sig, n=n)
    R = np.fft.rfft(ref, n=n)
    cross = S * np.conj(R)
    mag = np.abs(cross)

    freqs = np.fft.rfftfreq(n, 1.0 / fs)
    keep = (freqs >= fmin) & (freqs <= fmax)
    if mag[keep].size:
        thr = mag[keep].max() * (10.0 ** (bin_floor_db / 20.0))
        keep &= (mag >= thr)
    nkeep = int(keep.sum())
    if nkeep < 8:
        return 0.0, 0.0

    w = np.zeros_like(cross)
    w[keep] = cross[keep] / np.maximum(mag[keep], eps) ** beta
    if beta < 1.0:                       # keep scale comparable across blocks
        w[keep] /= np.maximum(np.abs(w[keep]).max(), eps)

    corr = np.fft.irfft(w, n=n) * (n / (2.0 * nkeep))   # perfect match -> ~1.0

    max_lag = n // 2 - 2
    if max_tau is not None:
        max_lag = min(max_lag, max(1, int(np.ceil(fs * max_tau))))
    lags = np.arange(-max_lag, max_lag + 1)
    win = corr[lags]                      # negative indices wrap = negative lags

    k = int(np.argmax(win))               # positive peak, NOT abs()
    peak = float(win[k])
    shift = float(lags[k])

    if subsample and 0 < k < len(win) - 1:
        y0, y1, y2 = win[k - 1], win[k], win[k + 1]
        den = y0 - 2.0 * y1 + y2
        if abs(den) > 1e-12:
            shift += np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5)

    noise = np.median(np.abs(win))
    psr = float(peak / max(noise, 1e-12))
    conf = float(np.clip(peak, 0.0, 1.0)) * float(np.clip((psr - 2.0) / 6.0, 0.0, 1.0))
    return shift / float(fs), conf
