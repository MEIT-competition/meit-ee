#include <math.h>
#include <string.h>
#include "esp_dsp.h"
#include "tdoa.h"

static float wind[FRAME_LEN];
static __attribute__((aligned(16))) float A[FFT_N * 2], B[FFT_N * 2];

// These were local to tdoa_gcc_phat() before -- 3 * (FFT_N/2+1) floats =
// ~12.3 KB, against capture_task's 8 KB task stack (see main.c). A single
// call would have overflowed the stack by ~4 KB, corrupting whatever else
// lives on it. tdoa_gcc_phat() is only ever called sequentially from
// capture_task, so static (not re-entrant) is fine here, same as A/B above.
static float re_buf[FFT_N / 2 + 1], im_buf[FFT_N / 2 + 1], mg_buf[FFT_N / 2 + 1];

void tdoa_init(void)
{
    dsps_fft2r_init_fc32(NULL, FFT_N);
    dsps_wind_hann_f32(wind, FRAME_LEN);
}

static void fwd(const float *x, float *buf)
{
    memset(buf, 0, sizeof(float) * FFT_N * 2);
    for (int i = 0; i < FRAME_LEN; i++) buf[2 * i] = x[i] * wind[i];
    dsps_fft2r_fc32(buf, FFT_N);
    dsps_bit_rev_fc32(buf, FFT_N);
}

float tdoa_gcc_phat(const float *sig, const float *ref, int len,
                    float max_tau_s, float *conf_out)
{
    (void)len;
    fwd(sig, A);
    fwd(ref, B);

    const int kmin = (int)(PHAT_FMIN_HZ * FFT_N / TDOA_FS_HZ);
    const int kmax = (int)(PHAT_FMAX_HZ * FFT_N / TDOA_FS_HZ);

    float *re = re_buf, *im = im_buf, *mg = mg_buf, mmax = 0;
    for (int k = 0; k <= FFT_N / 2; k++) {
        if (k < kmin || k > kmax) { re[k] = im[k] = mg[k] = 0; continue; }
        float ar = A[2*k], ai = A[2*k+1], br = B[2*k], bi = B[2*k+1];
        re[k] = ar * br + ai * bi;          // A * conj(B)
        im[k] = ai * br - ar * bi;
        mg[k] = sqrtf(re[k]*re[k] + im[k]*im[k]);
        if (mg[k] > mmax) mmax = mg[k];
    }
    // Bin floor: without this, PHAT whitens noise-only bins up to full
    // amplitude and tonal sources (horns, sirens) localise at random.
    float thr = mmax * PHAT_BIN_FLOOR;
    int nkeep = 0;
    memset(A, 0, sizeof(float) * FFT_N * 2);
    for (int k = 0; k <= FFT_N / 2; k++) {
        if (mg[k] < thr || mg[k] <= 0) continue;
        float r = re[k] / mg[k], i = im[k] / mg[k];
        A[2*k] = r; A[2*k+1] = i;
        if (k > 0 && k < FFT_N / 2) {       // hermitian mirror
            A[2*(FFT_N-k)] = r; A[2*(FFT_N-k)+1] = -i;
        }
        nkeep++;
    }
    if (nkeep < 8) { *conf_out = 0.0f; return 0.0f; }

    // ---- inverse FFT via a SECOND forward FFT (esp-dsp has no ifft) ----
    // For a Hermitian-symmetric spectrum X (guaranteed here: real signal),
    //   IFFT(X)[n] = (1/N) * Re( FFT(conj(X))[n] )
    // Calling dsps_fft2r_fc32() again on X directly (the old code) computes
    // Y[n] = FFT(X)[n] = N * IFFT(X)[(-n) mod N] -- i.e. the correct
    // correlation, but circularly TIME-REVERSED. Omitting conjugation reverses the LEFT/RIGHT delay sign. Verified by
    // reproducing this exact sequence in Python: a true +5 sample delay
    // came back as -5 without the conjugate step below, and correctly as
    // +5 with it (checked against +5,-7,0,+15).
    for (int k = 0; k < FFT_N; k++) A[2 * k + 1] = -A[2 * k + 1];

    dsps_fft2r_fc32(A, FFT_N);
    dsps_bit_rev_fc32(A, FFT_N);
    float scale = 1.0f / (2.0f * nkeep);    // perfect match -> ~1.0

    int max_lag = (int)ceilf(max_tau_s * TDOA_FS_HZ);
    if (max_lag > FFT_N / 4) max_lag = FFT_N / 4;

    int best = 0; float peak = -1e30f, sum = 0;
    for (int l = -max_lag; l <= max_lag; l++) {
        int idx = (l < 0) ? (FFT_N + l) : l;
        float v = A[2 * idx] * scale;       // positive peak, NOT fabsf()
        sum += fabsf(v);
        if (v > peak) { peak = v; best = l; }
    }
    float shift = (float)best;
    if (best > -max_lag && best < max_lag) {
        int im1 = ((best-1) < 0 ? FFT_N + best - 1 : best - 1);
        int ip1 = ((best+1) < 0 ? FFT_N + best + 1 : best + 1);
        float y0 = A[2*im1]*scale, y1 = peak, y2 = A[2*ip1]*scale;
        float den = y0 - 2*y1 + y2;
        if (fabsf(den) > 1e-12f) {
            float d = 0.5f * (y0 - y2) / den;
            if (d > 0.5f) d = 0.5f;
            if (d < -0.5f) d = -0.5f;
            shift += d;
        }
    }
    float mean = sum / (2 * max_lag + 1);
    float psr = peak / (mean > 1e-12f ? mean : 1e-12f);
    float c = (peak < 0 ? 0 : (peak > 1 ? 1 : peak)) * ((psr - 2.0f) / 6.0f);
    *conf_out = c < 0 ? 0 : (c > 1 ? 1 : c);
    return shift / (float)TDOA_FS_HZ;
}

int tdoa_direction_from_delay(float delay_samples)
{
    if (!isfinite(delay_samples)) return -1;
    if (delay_samples < -TDOA_THRESHOLD_SAMPLES) return DIR_LEFT;
    if (delay_samples > TDOA_THRESHOLD_SAMPLES) return DIR_RIGHT;
    return DIR_BACK;
}

void tdoa_estimate(const audio_frame_t *f, direction_t *out)
{
    const float max_tau = TAU_MARGIN * MIC_SPACING_M / SPEED_OF_SOUND;
    float conf;
    float tau = tdoa_gcc_phat(f->ch[CH_LEFT], f->ch[CH_RIGHT], FRAME_LEN,
                              max_tau, &conf);
    tau -= TDOA_LR_BIAS_SAMPLES / (float)TDOA_FS_HZ;
    out->tau_lr_s = tau;
    out->conf_lr = conf;
    out->confidence = conf;
    out->index = tdoa_direction_from_delay(tau * TDOA_FS_HZ);
    float loud = fmaxf(f->rms_dbfs[CH_LEFT], f->rms_dbfs[CH_RIGHT]);
    // BACK describes a valid center-axis estimate only. Silence/failure is UNKNOWN.
    if (loud < RMS_GATE_DBFS || !isfinite(conf) || conf < MIN_CONFIDENCE ||
        out->index < 0) {
        out->confidence = 0.0f;
        out->index = -1;
    }
}
