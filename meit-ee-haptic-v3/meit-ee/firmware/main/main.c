// MEIT belt - motor-only firmware.
//
// Direction estimation (4 iPhone roles) and danger-sound AI both live on the
// laptop side. This firmware receives a finished haptic command over BLE and
// plays it on two vibration motors. It makes no decisions about what to alert
// on; it only refuses to play a malformed pattern.
//
// Why the per-step masks matter: with two actuators, "in front" and "behind"
// cannot be distinguished by position, so they are distinguished by *order* —
// FRONT fires both motors together, BACK fires left then right as a sweep. That
// ordering arrives in the mask of each step. See docs/HAPTIC_DESIGN.md.

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
    case CMD_DIR_FRONT: return "FRONT";
    case CMD_DIR_RIGHT: return "RIGHT";
    case CMD_DIR_BACK: return "BACK";
    case CMD_DIR_STOP: return "STOP";
    default: return "INVALID";
    }
}

static void on_cmd(uint8_t sequence, uint8_t direction, uint8_t intensity,
                   const uint8_t *masks, const motor_step_t *steps, int n_steps)
{
    if (direction == CMD_DIR_STOP) {
        motor_stop_pattern();
        ESP_LOGI(TAG, "CMD seq=%u STOP", sequence);
        return;
    }

    if (masks == NULL || steps == NULL || n_steps <= 0) {
        motor_stop_pattern();
        ESP_LOGE(TAG, "invalid CMD seq=%u dir=%u", sequence, direction);
        return;
    }

    ESP_LOGI(TAG, "CMD seq=%u direction=%s intensity=%u steps=%d",
             sequence, cmd_direction_name(direction), intensity, n_steps);
    // The masks are authoritative, not the direction byte: the BLE handler has
    // already expanded a CMD v2 direction into per-step masks, so there is one
    // motor path for both wire versions.
    motor_play_masked_pattern(masks, intensity, steps, n_steps);
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

    ESP_LOGI(TAG, "ready: iOS/AI -> laptop -> BLE CMD v2/v3 -> "
                  "LEFT/RIGHT/FRONT/BACK haptics");
}
