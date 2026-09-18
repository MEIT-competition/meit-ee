#pragma once
#include "config.h"
// 48 kHz -> 16 kHz, integer /3 with a 33-tap anti-alias FIR. Streaming.
void resample_init(void);
// Clears the filter history and decimation phase without recomputing the
// FIR coefficients (unlike calling resample_init() again). Call this at the
// start of each new event -- otherwise up to ~33 stale samples from the
// PREVIOUS event's tail (possibly a different mic channel entirely, since
// the loudest channel is picked fresh per event) briefly leak into the
// start of the new one.
void resample_reset(void);
int  resample_48k_to_16k(const float *in, int n_in, int16_t *out);
