#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "tdoa.h"
#include "resample.h"
#include "motor.h"

static int checks;
#define CHECK(c) do { ++checks; if (!(c)) { \
    fprintf(stderr,"FAIL line %d: %s\n", __LINE__, #c); exit(1); } } while (0)
static audio_frame_t frame;
static float mono[FRAME_LEN], base[FRAME_LEN + 64];
static int32_t raw[FRAME_LEN * 2];
static int16_t pcm[CLIP_OUT_SAMPLES + 2];

int main(void)
{
    tdoa_init();
    float t = TDOA_THRESHOLD_SAMPLES;
    CHECK(tdoa_direction_from_delay(-t) == DIR_BACK);
    CHECK(tdoa_direction_from_delay(t) == DIR_BACK);
    CHECK(tdoa_direction_from_delay(0) == DIR_BACK);
    CHECK(tdoa_direction_from_delay(nextafterf(-t, -INFINITY)) == DIR_LEFT);
    CHECK(tdoa_direction_from_delay(nextafterf(t, INFINITY)) == DIR_RIGHT);
    CHECK(tdoa_direction_from_delay(NAN) == -1);
    CHECK(tdoa_direction_from_delay(INFINITY) == -1);

    uint32_t rng = 42;
    for (unsigned i=0; i<sizeof(base)/sizeof(base[0]); ++i) {
        rng = 1664525u * rng + 1013904223u;
        base[i] = ((float)(rng >> 8) / 16777216.0f - 0.5f) * 0.2f;
    }
    const int delays[] = {-8, 8, 0, -1, 1};
    const int expected[] = {DIR_LEFT, DIR_RIGHT, DIR_BACK, DIR_BACK, DIR_BACK};
    for (unsigned j=0; j<sizeof(delays)/sizeof(delays[0]); ++j) {
        for (int n=0; n<FRAME_LEN; ++n) {
            frame.ch[CH_LEFT][n] = base[32+n-delays[j]];
            frame.ch[CH_RIGHT][n] = base[32+n];
        }
        frame.rms_dbfs[CH_LEFT] = frame.rms_dbfs[CH_RIGHT] = -25;
        direction_t d;
        tdoa_estimate(&frame, &d);
        CHECK(d.index == expected[j]);
        CHECK(d.confidence > 0);
        CHECK(fabsf(d.tau_lr_s * TDOA_FS_HZ - delays[j]) < 0.25f);
        printf("delay=%d measured=%.3f -> %s conf=%.3f\n", delays[j],
               d.tau_lr_s*TDOA_FS_HZ, direction_name(d.index), d.confidence);
    }
    memset(&frame, 0, sizeof(frame));
    frame.rms_dbfs[0] = frame.rms_dbfs[1] = -240;
    direction_t d;
    tdoa_estimate(&frame, &d);
    CHECK(d.index == -1 && d.confidence == 0);

    // Distinct zero-mean signed sample patterns identify the two DMA slots.
    // This proves software ordering, not physical breakout L/R wiring.
    for (int n=0; n<FRAME_LEN; ++n) {
        raw[2*n] = (n & 1) ? -1073741824 : 1073741824;
        raw[2*n+1] = (n & 1) ? 536870912 : -536870912;
    }
    audio_decode_stereo(raw, &frame);
    CHECK(frame.ch[CH_LEFT][0] == .5f);
    CHECK(frame.ch[CH_LEFT][1] == -.5f);
    CHECK(frame.ch[CH_RIGHT][0] == -.25f);
    CHECK(frame.ch[CH_RIGHT][1] == .25f);
    CHECK(fabsf(frame.rms_dbfs[CH_LEFT] + 6.0206f) < .001f);
    CHECK(fabsf(frame.rms_dbfs[CH_RIGHT] + 12.0412f) < .001f);
    audio_downmix(&frame, mono);
    CHECK(mono[0] == .125f && mono[1] == -.125f);
    for (int n=0; n<FRAME_LEN; ++n)
        raw[2*n] = raw[2*n+1] = (n&1) ? INT32_MIN : INT32_MAX;
    audio_decode_stereo(raw, &frame);
    audio_downmix(&frame, mono);
    CHECK(mono[0] > .99f && mono[1] < -.99f); // no integer sum overflow

    // Real streaming downmix/resampler: exact clip length and no tail overwrite.
    for (int n=0; n<FRAME_LEN; ++n) {
        frame.ch[CH_LEFT][n] = .75f;
        frame.ch[CH_RIGHT][n] = .25f;
    }
    resample_init();
    pcm[0] = 1234; pcm[CLIP_OUT_SAMPLES+1] = 5678;
    int count = 0;
    for (int f=0; f<CLIP_FRAMES; ++f) {
        audio_downmix(&frame, mono);
        count += resample_48k_to_16k(mono, FRAME_LEN, pcm + 1 + count);
        CHECK(count <= CLIP_OUT_SAMPLES);
    }
    CHECK(count == 40960);
    CHECK(pcm[0] == 1234 && pcm[CLIP_OUT_SAMPLES+1] == 5678);
    CHECK(abs(pcm[CLIP_OUT_SAMPLES] - 16383) <= 2);
    resample_reset();
    for (int n=0; n<FRAME_LEN; ++n) mono[n] = 2.0f;
    count = resample_48k_to_16k(mono, FRAME_LEN, pcm);
    CHECK(pcm[count-1] > 32000 && pcm[count-1] <= 32767);
    CHECK(direction_to_wire(DIR_LEFT) == 6);
    CHECK(direction_to_wire(DIR_RIGHT) == 2);
    CHECK(direction_to_wire(DIR_BACK) == 4);
    CHECK(direction_to_wire(-1) == DIR_UNKNOWN);
    CHECK(motor_mask_for_direction(DIR_WIRE_BACK) == MOTOR_MASK_BOTH);
    printf("PASS: %d checks; stereo slots, delay polarity/boundaries, PCM16 40960 samples\n", checks);
    return 0;
}
