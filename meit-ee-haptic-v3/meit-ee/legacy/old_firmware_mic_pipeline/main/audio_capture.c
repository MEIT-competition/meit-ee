// Two INMP441 microphones sharing one I2S0 Philips stereo bus.
#include "driver/i2s_std.h"
#include "esp_log.h"
#include "audio_capture.h"

static const char *TAG = "audio";
static i2s_chan_handle_t rx;
static int32_t raw[FRAME_LEN * NUM_MICS];
_Static_assert(NUM_MICS == 2, "capture requires stereo");
_Static_assert(I2S_WARMUP_FRAMES * FRAME_LEN > 4096, "allow INMP441 startup");

static esp_err_t read_raw(int timeout_ms)
{
    size_t got = 0;
    esp_err_t err = i2s_channel_read(rx, raw, sizeof(raw), &got, timeout_ms);
    if (err != ESP_OK) return err;
    if (got != sizeof(raw)) {
        ESP_LOGW(TAG, "short stereo read: %u/%u", (unsigned)got, (unsigned)sizeof(raw));
        return ESP_ERR_INVALID_SIZE;
    }
    return ESP_OK;
}

esp_err_t audio_capture_init(void)
{
    i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    cc.dma_desc_num = 6;
    cc.dma_frame_num = I2S_DMA_FRAMES;
    ESP_ERROR_CHECK(i2s_new_channel(&cc, NULL, &rx));
    i2s_std_config_t sc = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(TDOA_FS_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
            I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = { .mclk = I2S_GPIO_UNUSED,
            .bclk = I2S_BCLK, .ws = I2S_WS, .dout = I2S_GPIO_UNUSED,
            .din = I2S_DIN, .invert_flags = {false, false, false} },
    };
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(rx, &sc));
    ESP_ERROR_CHECK(i2s_channel_enable(rx));
    // Drain startup samples while clocks run; sleeping would retain old DMA data.
    for (int i = 0; i < I2S_WARMUP_FRAMES; ++i) {
        esp_err_t err = read_raw(200);
        if (err != ESP_OK) return err;
    }
    ESP_LOGI(TAG, "stereo LEFT/RIGHT @ %d Hz, BCLK=%d WS=%d DIN=%d",
             TDOA_FS_HZ, I2S_BCLK, I2S_WS, I2S_DIN);
    return ESP_OK;
}

esp_err_t audio_capture_read(audio_frame_t *out, int timeout_ms)
{
    esp_err_t err = read_raw(timeout_ms);
    if (err != ESP_OK) return err;
    audio_decode_stereo(raw, out);
    return ESP_OK;
}
