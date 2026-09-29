#include "resample.h"
#include <string.h>
#include <math.h>
// 33-tap low-pass, cutoff ~7 kHz @48k, then keep every 3rd sample.
// Replace with a designed FIR if you have time; this is good enough for AI input.
#define NT 33
static float h[NT], hist[NT];

// Decimation phase MUST persist across calls (across frame boundaries).
// FRAME_LEN=1024 is not a multiple of DECIM=3, so restarting the phase at 0
// on every call (the previous bug) drops/duplicates samples at every frame
// boundary and, worse, produces MORE output samples per frame (342, since
// 1024/3 rounds up once) than a naive `FRAME_LEN/DECIM` allocation expects
// (341) -- an overflow of 24 samples over a 24-frame clip. Verified in
// Python: floor(1024/3) sampled per-frame-reset over 24 frames gives 8208
// outputs against an 8184-sample buffer. A persistent phase instead gives
// exactly floor(N*1024/3) for N frames (40960 at the current CLIP_FRAMES=120),
// matching CLIP_OUT_SAMPLES in config.h. (The 24-frame figures above are the
// original worked example from when the clip was 24 frames / 0.5 s.)
static int phase = 0;

void resample_init(void)
{
    const float fc = 7000.0f / TDOA_FS_HZ;   // normalised
    float sum = 0;
    for (int i = 0; i < NT; i++) {
        int n = i - NT / 2;
        float s = (n == 0) ? 2 * fc : sinf(2 * (float)M_PI * fc * n) / ((float)M_PI * n);
        float w = 0.54f - 0.46f * cosf(2 * (float)M_PI * i / (NT - 1));
        h[i] = s * w; sum += h[i];
    }
    for (int i = 0; i < NT; i++) h[i] /= sum;
    memset(hist, 0, sizeof(hist));
    phase = 0;
}

void resample_reset(void)
{
    memset(hist, 0, sizeof(hist));
    phase = 0;
}

int resample_48k_to_16k(const float *in, int n_in, int16_t *out)
{
    int o = 0;
    for (int n = 0; n < n_in; n++) {
        memmove(hist + 1, hist, sizeof(float) * (NT - 1));
        hist[0] = in[n];
        if (phase == 0) {
            float acc = 0;
            for (int k = 0; k < NT; k++) acc += h[k] * hist[k];
            if (acc > 0.999f) acc = 0.999f;
            if (acc < -0.999f) acc = -0.999f;
            out[o++] = (int16_t)(acc * 32767.0f);
        }
        phase = (phase + 1) % DECIM;
    }
    return o;
}
