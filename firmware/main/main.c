// MEIT belt - motor-only firmware.
// Direction estimation and danger-sound AI are handled by iPhone(s) + the
// existing meit-ios Windows bridge. The ESP32 only receives a final 3-way
// direction command and drives two vibration motors.

#include "esp_log.h"
#include "nvs_flash.h"
#include "config.h"
#include "ble_svc.h"
#include "motor.h"

static const char *TAG = "main";

static const char *cmd_direction_name(uint8_t d)
{
    switch (d) {
    case CMD_DIR_LEFT: return "LEFT";
    case CMD_DIR_CENTER: return "CENTER";
    case CMD_DIR_RIGHT: return "RIGHT";
    case CMD_DIR_STOP: return "STOP";
    default: return "INVALID";
    }
}

static void on_cmd(uint8_t sequence, uint8_t direction, uint8_t intensity,
                   const motor_step_t *steps, int n_steps)
{
    if (direction == CMD_DIR_STOP) {
        motor_stop_pattern();
        ESP_LOGI(TAG, "CMD seq=%u STOP", sequence);
        return;
    }

    uint8_t mask = motor_mask_for_cmd_direction(direction);
    if (mask == 0 || steps == NULL || n_steps <= 0) {
        motor_stop_pattern();
        ESP_LOGE(TAG, "invalid CMD seq=%u dir=%u", sequence, direction);
        return;
    }

    ESP_LOGI(TAG, "CMD seq=%u direction=%s intensity=%u L=%s R=%s",
             sequence, cmd_direction_name(direction), intensity,
             (mask & MOTOR_MASK_LEFT) ? "ON" : "OFF",
             (mask & MOTOR_MASK_RIGHT) ? "ON" : "OFF");
    motor_play_pattern(mask, intensity, steps, n_steps);
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    motor_init();
    ble_svc_set_cmd_cb(on_cmd);
    ble_svc_init();

    ESP_LOGI(TAG, "ready: iOS/AI -> laptop -> BLE -> LEFT/CENTER/RIGHT motors");
}
