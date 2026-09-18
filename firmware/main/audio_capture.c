// 4x INMP441 on two I2S peripherals sharing ONE electrical BCLK/WS net.
//
//   bus A = MASTER : drives BCLK + WS, reads FRONT(L) / RIGHT(R)
//   bus B = SLAVE  : consumes the SAME BCLK + WS, reads BACK(L) / LEFT(R)
//
// "Same net" is electrical, not the same GPIO number -- bus A's BCLK/WS
// outputs and bus B's BCLK/WS inputs use DIFFERENT ESP32 GPIOs, jumpered
// together on the board. See config.h for which pins and why.
//
// Because both peripherals latch on the same clock edges, there is no sample
// CLOCK drift between them. What is NOT guaranteed is which WS frame each DMA
// starts on -> a CONSTANT integer offset may exist. Measure it once with all
// four mics bundled together (see tdoa/calibration.py) and set it here.
//
// UNVERIFIED: that ESP-IDF starts both DMA channels on the same frame, and
// that the offset is stable across reboots. Measure before trusting it.

#include <string.h>
#include <math.h>
#include "driver/i2s_std.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "audio_capture.h"

static const char *TAG = "audio";
static i2s_chan_handle_t rx_a, rx_b;
static int32_t raw_a[FRAME_LEN * 2], raw_b[FRAME_LEN * 2];

static esp_err_t make_bus(i2s_port_t port, i2s_role_t role,
                          int bclk, int ws, int din, i2s_chan_handle_t *out)
{
    i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(port, role);
    cc.dma_desc_num = 6;
    cc.dma_frame_num = I2S_DMA_FRAMES;   // NOT FRAME_LEN -- see config.h
    cc.auto_clear = true;
    ESP_ERROR_CHECK(i2s_new_channel(&cc, NULL, out));

    i2s_std_config_t sc = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(TDOA_FS_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
                        I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = { .mclk = I2S_GPIO_UNUSED,
                      .bclk = bclk, .ws = ws,
                      .dout = I2S_GPIO_UNUSED, .din = din,
                      .invert_flags = {false, false, false} },
    };
    return i2s_channel_init_std_mode(*out, &sc);
}

esp_err_t audio_capture_init(void)
{
    ESP_ERROR_CHECK(make_bus(I2S_NUM_0, I2S_ROLE_MASTER,
                             I2S_A_BCLK, I2S_A_WS, I2S_A_DIN, &rx_a));
    ESP_ERROR_CHECK(make_bus(I2S_NUM_1, I2S_ROLE_SLAVE,
                             I2S_B_BCLK, I2S_B_WS, I2S_B_DIN, &rx_b));

    // Enable the SLAVE first so it is already armed when clocks start.
    ESP_ERROR_CHECK(i2s_channel_enable(rx_b));
    ESP_ERROR_CHECK(i2s_channel_enable(rx_a));
    ESP_LOGI(TAG, "I2S up: 4ch @ %d Hz", TDOA_FS_HZ);
    return ESP_OK;
}

static inline float s24(int32_t v) { return (float)(v >> 8) / 8388608.0f; }

esp_err_t audio_capture_read(audio_frame_t *out, int timeout_ms)
{
    size_t got_a = 0, got_b = 0;
    esp_err_t e;
    if ((e = i2s_channel_read(rx_a, raw_a, sizeof(raw_a), &got_a,
                              timeout_ms)) != ESP_OK) return e;
    if ((e = i2s_channel_read(rx_b, raw_b, sizeof(raw_b), &got_b,
                              timeout_ms)) != ESP_OK) return e;

    // i2s_channel_read()'s bytes-read out-param can legitimately be less
    // than requested (e.g. a timeout landing mid-transfer). Reading that
    // without checking would run TDoA on a buffer whose tail is stale data
    // from the PREVIOUS read -- and the result would look like a TDoA
    // accuracy problem, not the I2S read problem it actually was. Treat a
    // short read the same as a hardware error rather than silently using
    // partial/stale data.
    //
    // KNOWN LIMITATION, deliberately not handled further: dropping this
    // one frame does not re-align bus A and bus B with each other if only
    // ONE of them was short. i2s_channel_read() consumes whatever it
    // copies out -- a short read doesn't get replayed -- so e.g. bus A
    // fully satisfied at 1024 frames while bus B only delivered 500 before
    // timing out leaves bus B's stream permanently ~524 frames further
    // behind its own hardware timeline than bus A's, and every frame
    // after this one inherits that skew. At FRAME_LEN=1024 samples
    // requested against a 200ms timeout (~10x the ~21ms a frame actually
    // takes at 48kHz), this should be rare enough to only show up as an
    // actual hardware fault, not routine jitter -- if bring-up logs show
    // this warning even occasionally, the fix is either (a) explicitly
    // re-synchronizing both channels, or (b) for the MVP, just
    // esp_restart() on repeated occurrences rather than limping along
    // desynced. Not implemented here on the reasoning that adding either
    // one now, before it's known to actually happen, is more likely to
    // introduce a new bug than to prevent a real one.
    if (got_a != sizeof(raw_a) || got_b != sizeof(raw_b)) {
        ESP_LOGW(TAG, "short I2S read: got_a=%u/%u got_b=%u/%u",
                (unsigned)got_a, (unsigned)sizeof(raw_a),
                (unsigned)got_b, (unsigned)sizeof(raw_b));
        return ESP_ERR_INVALID_SIZE;
    }

    // No index shifting here -- bus-to-bus skew is corrected on tau in
    // tdoa_estimate() instead. See audio_capture.h for why.
    float mean[NUM_MICS] = {0};
    for (int n = 0; n < FRAME_LEN; n++) {
        out->ch[CH_FRONT][n] = s24(raw_a[2 * n + 0]);
        out->ch[CH_RIGHT][n] = s24(raw_a[2 * n + 1]);
        out->ch[CH_BACK ][n] = s24(raw_b[2 * n + 0]);
        out->ch[CH_LEFT ][n] = s24(raw_b[2 * n + 1]);
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
    return ESP_OK;
}
