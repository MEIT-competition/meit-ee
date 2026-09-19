#pragma once
#include "audio_capture.h"

typedef struct { int index; float angle_deg; float confidence;
                 float tau_lr_s; float tau_fb_s;
                 float conf_lr; float conf_fb;   // component confidences,
                                                 // before min() -- exposed
                                                 // for serial logging during
                                                 // real-mic bring-up. See
                                                 // main.c -- confidence is
                                                 // not yet validated against
                                                 // real hardware.
                } direction_t;

void  tdoa_init(void);
void  tdoa_set_bus_skew_samples(int samples);
// Direct port of tdoa/gcc_phat.py. tau>0 => sig arrived later than ref.
float tdoa_gcc_phat(const float *sig, const float *ref, int len,
                    float max_tau_s, float *conf_out);
// Direct port of tdoa/direction_4mic.py (2-pair version). Gates confidence
// to 0 using the loudest of the 4 channels, not just FRONT.
void  tdoa_estimate(const audio_frame_t *f, direction_t *out);
