// Diagnostic only: runs the real tdoa.c on synthetic broadband / horn-like /
// siren-like two-mic frames. Delay error is a FAIL; confidence is printed
// only. MIN_CONFIDENCE, the PSR formula, TDOA_THRESHOLD_SAMPLES and
// TDOA_LR_BIAS_SAMPLES are NOT tuned from this: synthetic tones are not real
// mic + real siren data. Low siren confidence here means such frames can end
// up UNKNOWN on hardware -- measure real recordings before changing anything.
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include "tdoa.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
#define PAD 64
#define SIREN_FRAMES 8

enum { SIG_NOISE, SIG_HORN, SIG_SIREN, SIG_COUNT };
static const char *sig_names[SIG_COUNT] = {"broadband_noise", "horn_harmonics",
                                           "siren_sweep"};
static audio_frame_t frame;
static float src[FRAME_LEN + 2 * PAD];
static uint32_t rng = 42;

static float noise(void)
{
    rng = 1664525u * rng + 1013904223u;
    return ((float)(rng >> 8) / 16777216.0f - 0.5f) * 0.2f;
}

// t0: absolute start time (s) of src[0], so siren frames sample the sweep.
static void synth(int kind, double t0)
{
    for (int i = 0; i < FRAME_LEN + 2 * PAD; ++i) {
        double t = t0 + (double)i / TDOA_FS_HZ;
        double v = 0;
        if (kind == SIG_NOISE) {
            v = noise();
        } else if (kind == SIG_HORN) {
            // ~420 Hz car-horn-like fundamental with decaying harmonics.
            for (int h = 1; h <= 6; ++h)
                v += 0.08 / h * sin(2 * M_PI * 420.0 * h * t + 0.7 * h);
        } else {
            // Yelp-like siren: fundamental sweeps 600 -> 1500 Hz in 0.2 s
            // (triangular), plus two harmonics. Phase = integral of f(t).
            const double f0 = 600, f1 = 1500, half = 0.2;
            double tt = fmod(t, 2 * half), k = (f1 - f0) / half, ph;
            if (tt < half) ph = f0 * tt + 0.5 * k * tt * tt;
            else {
                double u = tt - half;
                ph = f0 * half + 0.5 * k * half * half + f1 * u - 0.5 * k * u * u;
            }
            double cycles = floor(t / (2 * half)) * (f0 + f1) * half;
            for (int h = 1; h <= 3; ++h)
                v += 0.1 / h * sin(2 * M_PI * h * (ph + cycles));
        }
        src[i] = (float)v;
    }
}

int main(void)
{
    tdoa_init();
    const int delays[] = {-8, 8, 0, -4, 4};
    const int nd = (int)(sizeof(delays) / sizeof(delays[0]));
    int failures = 0, runs = 0;
    printf("MIN_CONFIDENCE=%.3f threshold=%.1f bias=%.1f (unchanged)\n",
           MIN_CONFIDENCE, TDOA_THRESHOLD_SAMPLES, TDOA_LR_BIAS_SAMPLES);
    for (int kind = 0; kind < SIG_COUNT; ++kind) {
        int frames = kind == SIG_SIREN ? SIREN_FRAMES : 1;
        float cmin = 1, csum = 0, emax = 0;
        int unknown = 0, n = 0;
        for (int j = 0; j < nd; ++j) {
            for (int fr = 0; fr < frames; ++fr) {
                synth(kind, fr * 0.05);
                for (int i = 0; i < FRAME_LEN; ++i) {
                    frame.ch[CH_LEFT][i] = src[PAD + i - delays[j]];
                    frame.ch[CH_RIGHT][i] = src[PAD + i];
                }
                frame.rms_dbfs[CH_LEFT] = frame.rms_dbfs[CH_RIGHT] = -25;
                direction_t d;
                tdoa_estimate(&frame, &d);
                float measured = d.tau_lr_s * TDOA_FS_HZ;
                float err = fabsf(measured - (float)delays[j]);
                runs++; n++;
                if (!(err < 0.5f)) {
                    failures++;
                    printf("  FAIL %s delay=%d frame=%d measured=%.3f\n",
                           sig_names[kind], delays[j], fr, measured);
                }
                if (err > emax) emax = err;
                if (d.conf_lr < cmin) cmin = d.conf_lr;
                csum += d.conf_lr;
                unknown += d.index < 0;
            }
        }
        printf("%-16s delay_err_max=%.3f conf_min=%.3f conf_mean=%.3f "
               "below_MIN_CONFIDENCE->UNKNOWN=%d/%d\n",
               sig_names[kind], emax, cmin, csum / n, unknown, n);
    }
    printf("%s: %d/%d delay checks (confidence is informational only)\n",
           failures ? "FAIL" : "PASS", runs - failures, runs);
    return failures ? 1 : 0;
}
