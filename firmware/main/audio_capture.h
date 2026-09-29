#pragma once
#include <stdint.h>
#include "esp_err.h"
#include "config.h"

// De-interleaved, DC-removed float frames, LEFT then RIGHT.
typedef struct { float ch[NUM_MICS][FRAME_LEN]; float rms_dbfs[NUM_MICS]; } audio_frame_t;
esp_err_t audio_capture_init(void);
esp_err_t audio_capture_read(audio_frame_t *out, int timeout_ms);
// Shared with host tests: signed 24-bit PCM in 32-bit Philips stereo slots.
void audio_decode_stereo(const int32_t *raw, audio_frame_t *out);
void audio_downmix(const audio_frame_t *frame, float *mono);
