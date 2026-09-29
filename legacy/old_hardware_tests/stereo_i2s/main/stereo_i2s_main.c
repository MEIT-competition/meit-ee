#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_log.h"

#include "audio_capture.h"
#include "config.h"

#define CAPTURE_FRAMES 8

static const char *TAG = "stereo_i2s";
static audio_frame_t frame;

void app_main(void)
{
    const size_t samples_per_channel = (size_t)CAPTURE_FRAMES * FRAME_LEN;
    const size_t total_samples = (size_t)NUM_MICS * samples_per_channel;
    float *capture = heap_caps_malloc(total_samples * sizeof(*capture),
                                  MALLOC_CAP_8BIT);
    if (capture == NULL) {
        ESP_LOGE(TAG, "capture allocation failed for %u samples",
                 (unsigned)total_samples);
        return;
    }

    ESP_ERROR_CHECK(audio_capture_init());
    ESP_LOGI(TAG, "capturing %d contiguous frames (%u samples/channel)",
             CAPTURE_FRAMES, (unsigned)samples_per_channel);

    for (int f = 0; f < CAPTURE_FRAMES; ++f) {
        esp_err_t err = audio_capture_read(&frame, 200);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "capture failed at frame %d: %s", f,
                     esp_err_to_name(err));
            free(capture);
            return;
        }
        for (int ch = 0; ch < NUM_MICS; ++ch) {
            memcpy(&capture[(size_t)ch * samples_per_channel +
                            (size_t)f * FRAME_LEN],
                   frame.ch[ch], FRAME_LEN * sizeof(float));
        }
    }

    printf("MEIT_DUMP_BEGIN,fs=%d,channels=LEFT|RIGHT,samples=%u\n",
           TDOA_FS_HZ, (unsigned)samples_per_channel);
    for (size_t n = 0; n < samples_per_channel; ++n) {
        printf("MEIT_RAW,%u,%.9g,%.9g\n", (unsigned)n,
               capture[CH_LEFT * samples_per_channel + n],
               capture[CH_RIGHT * samples_per_channel + n]);
    }
    printf("MEIT_DUMP_END,samples=%u\n", (unsigned)samples_per_channel);
    fflush(stdout);
    free(capture);
    ESP_LOGI(TAG, "dump complete; reset to capture again");
}
