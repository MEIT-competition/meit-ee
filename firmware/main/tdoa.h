#pragma once
#include "audio_capture.h"

typedef struct {
    int index; // compact direction index, -1 when unresolved
    float confidence;
    float tau_lr_s; // calibrated LEFT-minus-RIGHT delay
    float conf_lr;
} direction_t;

void tdoa_init(void);
// tau>0 => sig arrived later than ref. Called with (LEFT, RIGHT).
float tdoa_gcc_phat(const float *sig, const float *ref, int len,
                    float max_tau_s, float *conf_out);
int tdoa_direction_from_delay(float delay_samples);
void tdoa_estimate(const audio_frame_t *f, direction_t *out);
