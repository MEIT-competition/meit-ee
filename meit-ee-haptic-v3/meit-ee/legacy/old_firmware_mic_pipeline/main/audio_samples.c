#include <math.h>
#include "audio_capture.h"

static inline float s24(int32_t v) { return (float)(v >> 8) / 8388608.0f; }

void audio_decode_stereo(const int32_t *raw, audio_frame_t *out)
{
    float mean[NUM_MICS] = {0};
    for (int n = 0; n < FRAME_LEN; n++) {
        out->ch[CH_LEFT][n] = s24(raw[2 * n]);
        out->ch[CH_RIGHT][n] = s24(raw[2 * n + 1]);
    }
    // DC removal (INMP441 has a noticeable DC term; PHAT hates it)
    for (int c = 0; c < NUM_MICS; c++) {
        for (int n = 0; n < FRAME_LEN; n++) mean[c] += out->ch[c][n];
        mean[c] /= FRAME_LEN;
        for (int n = 0; n < FRAME_LEN; n++) out->ch[c][n] -= mean[c];
    }
    for (int c = 0; c < NUM_MICS; c++) {
        double acc = 0;
        for (int n = 0; n < FRAME_LEN; n++)
            acc += (double)out->ch[c][n] * out->ch[c][n];
        out->rms_dbfs[c] = 20.0f * log10f(sqrtf((float)(acc / FRAME_LEN)) + 1e-12f);
    }
}

void audio_downmix(const audio_frame_t *frame, float *mono)
{
    // Convert before summing: no int32/PCM16 addition can overflow.
    // Double intermediate also preserves headroom after per-channel DC removal.
    // The existing FIR resampler saturates before conversion to PCM16.
    for (int n = 0; n < FRAME_LEN; ++n)
        mono[n] = (float)(((double)frame->ch[CH_LEFT][n] +
                          (double)frame->ch[CH_RIGHT][n]) * 0.5);
}
