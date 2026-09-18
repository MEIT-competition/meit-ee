// C port of tdoa/gcc_phat.py + tdoa/direction_4mic.py, using esp-dsp.
// Differences from Python, all deliberate:
//   * no 16x zero-pad upsampling. At 48 kHz, max lag is ~22 samples for a
//     16 cm spacing, which already gives ~3 deg worst-case quantisation for
//     an 8-way decision. Parabolic interpolation covers the rest.
//   * float32 instead of float64.

#include <math.h>
#include <string.h>
#include "esp_dsp.h"
#include "tdoa.h"

static float wind[FRAME_LEN];
static __attribute__((aligned(16))) float A[FFT_N * 2], B[FFT_N * 2];
static int bus_skew_samples = 0;

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

// Set from measure_sync_offsets() in tdoa/calibration.py -- see
// firmware/SYNC_CHECK.md. Replaces the old audio_capture_set_sync_offset(),
// which shifted raw PCM; this corrects the tau instead (see tdoa_estimate).
void tdoa_set_bus_skew_samples(int samples) { bus_skew_samples = samples; }

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
    // correlation, but circularly TIME-REVERSED. Because both tau_lr and
    // tau_fb go through this same path, both flip sign identically, which
    // rotates every direction estimate by exactly 180 degrees. Verified by
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

void tdoa_estimate(const audio_frame_t *f, direction_t *out)
{
    const float d = 2.0f * MIC_RADIUS_M;
    const float max_tau = TAU_MARGIN * d / SPEED_OF_SOUND;
    float c1, c2;

    // tau>0 => LEFT later than RIGHT => source to the RIGHT
    float tau_lr = tdoa_gcc_phat(f->ch[CH_LEFT], f->ch[CH_RIGHT], FRAME_LEN, max_tau, &c1);
    // tau>0 => BACK later than FRONT => source to the FRONT
    float tau_fb = tdoa_gcc_phat(f->ch[CH_BACK], f->ch[CH_FRONT], FRAME_LEN, max_tau, &c2);

    // Correct for the constant bus-A/bus-B skew in the tau domain instead of
    // shifting raw PCM. Shifting PCM with edge-clamping (the old approach)
    // duplicates a boundary sample and injects an artificial discontinuity
    // right where GCC-PHAT looks; subtracting a known constant from tau
    // has no such side effect. LEFT and BACK are both bus B, RIGHT and
    // FRONT are both bus A, so the same skew applies to both pairs with the
    // same sign (see tdoa_set_bus_skew_samples()).
    float skew_s = bus_skew_samples / (float)TDOA_FS_HZ;
    tau_lr -= skew_s;
    tau_fb -= skew_s;

    float norm = d / SPEED_OF_SOUND;
    float vx = tau_lr / norm, vy = tau_fb / norm;
    float ang = atan2f(vx, vy) * 180.0f / (float)M_PI;
    if (ang < 0) ang += 360.0f;

    float loud_dbfs = f->rms_dbfs[0];
    for (int c = 1; c < NUM_MICS; c++)
        if (f->rms_dbfs[c] > loud_dbfs) loud_dbfs = f->rms_dbfs[c];

    out->angle_deg  = ang;
    out->index      = ((int)((ang + 22.5f) / 45.0f)) & 7;
    out->confidence = (c1 < c2 ? c1 : c2);
    out->conf_lr    = c1;
    out->conf_fb    = c2;
    out->tau_lr_s   = tau_lr;
    out->tau_fb_s   = tau_fb;
    if (loud_dbfs < RMS_GATE_DBFS || out->confidence < MIN_CONFIDENCE)
        out->confidence = 0.0f;             // report "unknown", stay silent
}
