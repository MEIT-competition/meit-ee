#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

#include "config.h"
#include "motor.h"

static const char *TAG = "motor_self_test";

void app_main(void)
{
    const float intensity = CONFIG_MEIT_MOTOR_TEST_INTENSITY_PCT / 100.0f;

    motor_init();
    motor_all_off();
    ESP_LOGI(TAG, "start: intensity=%d%%, LEFT then RIGHT then BOTH",
             CONFIG_MEIT_MOTOR_TEST_INTENSITY_PCT);

    for (int motor = 0; motor < NUM_MOTORS; ++motor) {
        motor_all_off();
        ESP_LOGI(TAG, "motor=%d gpio=%d intensity=%d%%",
                 motor, MOTOR_GPIO[motor], CONFIG_MEIT_MOTOR_TEST_INTENSITY_PCT);
        motor_trigger(motor, intensity);
        vTaskDelay(pdMS_TO_TICKS(CONFIG_MEIT_MOTOR_TEST_ON_MS));
        motor_all_off();
        vTaskDelay(pdMS_TO_TICKS(CONFIG_MEIT_MOTOR_TEST_GAP_MS));
    }

    ESP_LOGI(TAG, "FRONT: LEFT + RIGHT together");
    motor_trigger(MOTOR_LEFT, intensity);
    motor_trigger(MOTOR_RIGHT, intensity);
    vTaskDelay(pdMS_TO_TICKS(CONFIG_MEIT_MOTOR_TEST_ON_MS));
    motor_all_off();
    vTaskDelay(pdMS_TO_TICKS(CONFIG_MEIT_MOTOR_TEST_GAP_MS));

    // The BACK sensation is a left-to-right sweep, and it is the one thing that
    // cannot be judged from a static per-motor check: it depends on the two
    // halves running back to back with no audible or tactile seam. Exercising
    // the real per-step-mask sequencer path here means a bring-up failure is
    // caught before BLE, the laptop and the AI are in the picture.
    ESP_LOGI(TAG, "BACK: LEFT half then RIGHT half, no gap");
    const uint8_t sweep_masks[2] = {MOTOR_MASK_LEFT, MOTOR_MASK_RIGHT};
    const motor_step_t sweep[2] = {
        {.on_ms = 250, .off_ms = 0},
        {.on_ms = 250, .off_ms = 0},
    };
    motor_play_masked_pattern(sweep_masks, CONFIG_MEIT_MOTOR_TEST_INTENSITY_PCT,
                              sweep, 2);
    vTaskDelay(pdMS_TO_TICKS(600));

    motor_all_off();
    ESP_LOGI(TAG, "complete: all motors off");
}
