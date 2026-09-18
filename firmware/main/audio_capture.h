#pragma once
#include <stdint.h>
#include "esp_err.h"
#include "config.h"

// De-interleaved, DC-removed, float frames. ch[CH_FRONT][n] etc.
// rms_dbfs[c] is per-channel -- used both for the loudness gate (max across
// all 4, not just FRONT: a sound behind the wearer can be shadowed by the
// torso on the front mic alone) and to pick which channel's audio goes to
// the AI classifier.
typedef struct { float ch[NUM_MICS][FRAME_LEN]; float rms_dbfs[NUM_MICS]; } audio_frame_t;

esp_err_t audio_capture_init(void);
// Blocks until one aligned 4-channel frame is ready.
esp_err_t audio_capture_read(audio_frame_t *out, int timeout_ms);

// 4x INMP441 on two I2S peripherals sharing ONE BCLK/WS net.
//
//   bus A = MASTER : drives BCLK + WS, reads FRONT(L) / RIGHT(R)
//   bus B = SLAVE  : consumes the SAME BCLK + WS, reads BACK(L) / LEFT(R)
//
// Because both peripherals latch on the same clock edges, there is no sample
// CLOCK drift between them. What is NOT guaranteed is which WS frame each DMA
// starts on -> a CONSTANT integer offset may exist. Measure it once with all
// four mics bundled together (see tdoa/calibration.py) and pass the result
// to tdoa_set_bus_skew_samples() (tdoa.h) -- NOT handled here. Earlier code
// shifted the raw PCM arrays with edge-clamping to compensate, which
// duplicates a boundary sample and injects an artificial discontinuity
// right where GCC-PHAT looks for a peak; correcting the measured tau
// instead has no such side effect. This file no longer touches sample
// timing at all.
//
// UNVERIFIED: that ESP-IDF starts both DMA channels on the same frame, and
// that the offset is stable across reboots. Measure before trusting it.
