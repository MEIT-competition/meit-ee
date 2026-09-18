// 4x DRV8833 -> 8 motors via LEDC PWM, plus a small timer-driven sequencer
// for meit-ai's variable-length [[on_ms,off_ms], ...] vibration patterns.
//
// Only one pattern plays at a time (single static state). If a new pattern
// arrives mid-playback it interrupts the current one. Acceptable for the
// MVP because main.c only ever has one BLE clip in flight; revisit if that
// changes (e.g. overlapping danger events).
#include <string.h>
#include <stdbool.h>
#include "driver/ledc.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "config.h"
#include "motor.h"

static const char *TAG = "motor";
static const int DIR_TO_MOTOR[8] = {0, 1, 2, 3, 4, 5, 6, 7};   // VERIFY wiring

static esp_timer_handle_t pattern_timer;
static motor_step_t steps_buf[PATTERN_MAX_PAIRS];
static int step_count, step_idx;
static uint8_t active_mask, active_duty;
static bool step_is_on;

void motor_init(void)
{
    ledc_timer_config_t t = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = MOTOR_PWM_RES,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = MOTOR_PWM_FREQ_HZ,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&t));
    for (int i = 0; i < 8; i++) {
        ledc_channel_config_t c = {
            .gpio_num = MOTOR_GPIO[i], .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel = (ledc_channel_t)i, .timer_sel = LEDC_TIMER_0,
            .duty = 0, .hpoint = 0,
        };
        ESP_ERROR_CHECK(ledc_channel_config(&c));
    }
    ESP_LOGI(TAG, "8 motor channels ready");
}

void motor_set(int idx, uint8_t duty)
{
    if (idx < 0 || idx >= 8) return;
    ledc_set_duty(LEDC_LOW_SPEED_MODE, (ledc_channel_t)idx, duty);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, (ledc_channel_t)idx);
}

void motor_all_off(void)
{
    for (int i = 0; i < 8; i++) motor_set(i, 0);
}

int motor_bit_for_direction(int dir_index)
{
    if (dir_index < 0 || dir_index >= 8) return -1;
    return DIR_TO_MOTOR[dir_index];
}

static void apply_mask(uint8_t mask, uint8_t duty)
{
    for (int i = 0; i < 8; i++)
        if (mask & (1 << i)) motor_set(i, duty);
}

static void advance(void *arg);

static void schedule(uint32_t ms)
{
    if (ms == 0) ms = 1;
    esp_timer_start_once(pattern_timer, (uint64_t)ms * 1000);
}

static void advance(void *arg)
{
    if (step_is_on) {
        apply_mask(active_mask, 0);                    // turn off
        step_is_on = false;
        uint32_t off_ms = steps_buf[step_idx].off_ms;
        step_idx++;
        if (step_idx >= step_count) return;             // pattern done
        schedule(off_ms);
    } else {
        apply_mask(active_mask, active_duty);           // turn on
        step_is_on = true;
        schedule(steps_buf[step_idx].on_ms);
    }
}

void motor_play_pattern(uint8_t motor_mask, uint8_t intensity_pct,
                        const motor_step_t *steps, int n_steps)
{
    if (n_steps <= 0) return;
    if (n_steps > PATTERN_MAX_PAIRS) n_steps = PATTERN_MAX_PAIRS;

    if (pattern_timer) esp_timer_stop(pattern_timer);
    else {
        esp_timer_create_args_t a = { .callback = advance, .name = "motor_seq" };
        esp_timer_create(&a, &pattern_timer);
    }
    apply_mask(0xFF, 0);   // clear whatever the previous pattern left on

    memcpy(steps_buf, steps, sizeof(motor_step_t) * n_steps);
    step_count = n_steps;
    step_idx = 0;
    active_mask = motor_mask;
    active_duty = (uint8_t)((int)intensity_pct * 255 / 100);
    step_is_on = false;
    advance(NULL);   // fires the first ON immediately
}
