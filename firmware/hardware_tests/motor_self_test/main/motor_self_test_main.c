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

    ESP_LOGI(TAG, "CENTER: LEFT + RIGHT together");
    motor_trigger(MOTOR_LEFT, intensity);
    motor_trigger(MOTOR_RIGHT, intensity);
    vTaskDelay(pdMS_TO_TICKS(CONFIG_MEIT_MOTOR_TEST_ON_MS));
    motor_all_off();
    vTaskDelay(pdMS_TO_TICKS(CONFIG_MEIT_MOTOR_TEST_GAP_MS));

    // Exercise the real pattern sequencer too, not just the direct per-motor
    // drive above: a bring-up failure in the queue/timer path is then caught
    // before BLE, the laptop and the AI are in the picture.
    ESP_LOGI(TAG, "CENTER pattern: two pulses through the sequencer");
    const motor_step_t pulses[2] = {
        {.on_ms = 250, .off_ms = 150},
        {.on_ms = 250, .off_ms = 0},
    };
    motor_play_pattern(MOTOR_MASK_BOTH, CONFIG_MEIT_MOTOR_TEST_INTENSITY_PCT,
                       pulses, 2);
    vTaskDelay(pdMS_TO_TICKS(900));

    motor_all_off();
    ESP_LOGI(TAG, "complete: all motors off");
}
