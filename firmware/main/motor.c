// Two DRV8833 A channels -> two vibration motors via LEDC PWM.
//
// Pattern sequencing is owned by a dedicated FreeRTOS task. BLE commands and
// esp_timer callbacks only enqueue messages, so sequencer state and timer APIs
// are modified from one execution context. A new PLAY command replaces the
// active pattern, and timer-start failures fall back to all motors off.
#include <string.h>
#include <stdbool.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/ledc.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "config.h"
#include "motor.h"

static const char *TAG = "motor";
const int MOTOR_GPIO[NUM_MOTORS] = {MOTOR_LEFT_GPIO, MOTOR_RIGHT_GPIO};

// --- everything below this line is owned EXCLUSIVELY by motor_seq_task ---
// (except seq_queue and pattern_timer, which are handles other contexts
// post into / were created once at init -- never read/written for their
// pointed-to content by anyone but this task). No lock, atomic or volatile
// qualifier is needed on any of it: a single task touching its own local
// state needs none of those, by construction.
static esp_timer_handle_t pattern_timer;
static motor_step_t steps_buf[PATTERN_MAX_PAIRS];
static uint8_t step_masks_buf[PATTERN_MAX_PAIRS];
static int step_count, step_idx;
static uint8_t active_duty;
static bool step_is_on;
static bool timer_armed;
static int64_t step_deadline_us; // task-owned; TICK is only a wake-up hint

static QueueHandle_t seq_queue;
static TaskHandle_t  seq_task_handle;

#define SEQ_QUEUE_LEN 4   // generous: normal traffic never exceeds 1-2
                          // pending messages, see motor_seq_task's comment

typedef enum { SEQ_MSG_TICK, SEQ_MSG_PLAY, SEQ_MSG_STOP } seq_msg_type_t;
typedef struct {
    seq_msg_type_t type;
    uint8_t  intensity_pct;   // SEQ_MSG_PLAY only, already clamped 0..100
    int      n_steps;         // SEQ_MSG_PLAY only
    uint8_t  step_masks[PATTERN_MAX_PAIRS]; // one mask per ON step
    motor_step_t steps[PATTERN_MAX_PAIRS];  // timing for each ON/OFF step
} seq_msg_t;

static void motor_seq_task(void *arg);
static void handle_play(const seq_msg_t *m);
static void handle_stop(void);
static void advance_step(void);
static void motor_timer_cb(void *arg);

// Raw duty is capped by MOTOR_DUTY_CAP so every motor entry point shares the
// same configured drive limit.
static inline uint8_t duty_capped(uint8_t duty)
{
    return (duty > MOTOR_DUTY_CAP) ? (uint8_t)MOTOR_DUTY_CAP : duty;
}

// Scale normalized/percentage intensity across the full allowed duty range.
static inline uint8_t intensity_to_duty(uint32_t num, uint32_t den)
{
    return (uint8_t)((num * (uint32_t)MOTOR_DUTY_CAP) / den);
}

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
    for (int i = 0; i < NUM_MOTORS; i++) {
        ledc_channel_config_t c = {
            .gpio_num = MOTOR_GPIO[i], .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel = (ledc_channel_t)i, .timer_sel = LEDC_TIMER_0,
            .duty = 0, .hpoint = 0,
        };
        ESP_ERROR_CHECK(ledc_channel_config(&c));
    }

    // DRV8833 nSLEEP. Only driven if we actually wired it to a GPIO;
    // MOTOR_SLEEP_GPIO < 0 means "strapped to 3V3 on the board" and this is a
    // no-op. HARDWARE VERIFICATION REQUIRED either way -- see config.h.
    // #if, not if(): with the default -1 an `if` branch still has to compile
    // `1ULL << -1`, which is undefined behaviour the compiler may reject.
#if MOTOR_SLEEP_GPIO >= 0
    {
        gpio_config_t s = {
            .pin_bit_mask = 1ULL << MOTOR_SLEEP_GPIO,
            .mode = GPIO_MODE_OUTPUT,
            .pull_up_en = GPIO_PULLUP_DISABLE,
            .pull_down_en = GPIO_PULLDOWN_DISABLE,
            .intr_type = GPIO_INTR_DISABLE,
        };
        ESP_ERROR_CHECK(gpio_config(&s));
        ESP_ERROR_CHECK(gpio_set_level((gpio_num_t)MOTOR_SLEEP_GPIO, 1)); // awake
        ESP_LOGI(TAG, "DRV8833 nSLEEP driven HIGH on GPIO%d", MOTOR_SLEEP_GPIO);
    }
#endif

    // Create the sequencer resources during initialization so failures are
    // detected before BLE commands can reach the motor path.
    if (!seq_queue) {
        seq_queue = xQueueCreate(SEQ_QUEUE_LEN, sizeof(seq_msg_t));
        if (!seq_queue) {
            ESP_LOGE(TAG, "xQueueCreate(motor seq) failed (out of memory); "
                         "motor sequencer cannot start");
            abort();
        }
    }
    if (!pattern_timer) {
        esp_timer_create_args_t a = { .callback = motor_timer_cb,
                                      .name = "motor_seq" };
        ESP_ERROR_CHECK(esp_timer_create(&a, &pattern_timer));
    }
    if (!seq_task_handle) {
        // Keep the motor sequencer responsive without making BLE callbacks
        // manipulate timer/sequencer state directly.
        BaseType_t created = xTaskCreate(motor_seq_task, "motor_seq", 3072,
                                         NULL, 5, &seq_task_handle);
        if (created != pdPASS) {
            ESP_LOGE(TAG, "xTaskCreate(motor_seq) failed (rc=%d); motor "
                         "sequencer cannot start", (int)created);
            abort();
        }
    }

    motor_all_off();   // known-idle state at boot
    ESP_LOGI(TAG, "2 motor channels ready @ %d Hz, duty capped at %d/255 "
                  "(%d mV motor on a %d mV rail)", MOTOR_PWM_FREQ_HZ,
             MOTOR_DUTY_CAP, MOTOR_RATED_MV, MOTOR_SUPPLY_MV);
    // Log the configured drive limit at boot for hardware bring-up.
}

void motor_set(int idx, uint8_t duty)
{
    if (idx < 0 || idx >= NUM_MOTORS) return;
    ledc_set_duty(LEDC_LOW_SPEED_MODE, (ledc_channel_t)idx, duty_capped(duty));
    ledc_update_duty(LEDC_LOW_SPEED_MODE, (ledc_channel_t)idx);
}

// Normalized-intensity form of motor_set(). 1.0 maps to MOTOR_DUTY_CAP.
void motor_trigger(int idx, float intensity)
{
    if (idx < 0 || idx >= NUM_MOTORS) return;
    if (intensity < 0.0f) intensity = 0.0f;
    if (intensity > 1.0f) intensity = 1.0f;
    motor_set(idx, (uint8_t)(intensity * (float)MOTOR_DUTY_CAP + 0.5f));
}

void motor_all_off(void)
{
    for (int i = 0; i < NUM_MOTORS; i++) motor_set(i, 0);
}

static void apply_mask(uint8_t mask, uint8_t duty)
{
    for (int i = 0; i < NUM_MOTORS; i++)
        if (mask & (1 << i)) motor_set(i, duty);
}

// Called only from motor_seq_task.
static bool schedule(uint32_t ms)
{
    if (ms == 0) ms = 1;
    step_deadline_us = esp_timer_get_time() + (int64_t)ms * 1000;
    if (esp_timer_start_once(pattern_timer, (uint64_t)ms * 1000) == ESP_OK) {
        timer_armed = true;
        return true;
    }
    ESP_LOGE(TAG, "esp_timer_start_once failed (ms=%u)", (unsigned)ms);
    return false;
}

// Fail-safe terminal state for any sequencer/timer error.
static void force_all_off_and_reset(void)
{
    apply_mask(MOTOR_MASK_BOTH, 0);
    step_is_on  = false;
    step_count  = 0;
    step_idx    = 0;
    timer_armed = false;
}

// Keep the esp_timer callback short: enqueue one tick and return.
static void motor_timer_cb(void *arg)
{
    (void)arg;
    seq_msg_t m = { .type = SEQ_MSG_TICK };
    // Task-dispatch callback: use the normal queue API and never block here.
    if (xQueueSend(seq_queue, &m, 0) != pdTRUE)
        ESP_LOGE(TAG, "motor seq queue full, dropped a tick");
}

static void advance_step(void)
{
    if (step_count <= 0 || step_idx >= step_count) return;
    if (step_is_on) {
        apply_mask(step_masks_buf[step_idx], 0);         // turn current step off
        step_is_on = false;
        uint32_t off_ms = steps_buf[step_idx].off_ms;
        step_idx++;
        if (step_idx >= step_count) {                    // pattern done
            timer_armed = false;                         // nothing re-armed
            return;
        }
        if (!schedule(off_ms)) {
            force_all_off_and_reset();
        }
    } else {
        apply_mask(step_masks_buf[step_idx], active_duty); // turn this step on
        step_is_on = true;
        if (!schedule(steps_buf[step_idx].on_ms)) {
            force_all_off_and_reset();
        }
    }
}


static void handle_stop(void)
{
    if (timer_armed) {
        (void)esp_timer_stop(pattern_timer);
    }
    force_all_off_and_reset();
    ESP_LOGI(TAG, "[MOTOR] STOP");
}

// Install and start a new pattern. Called only from motor_seq_task.
static void handle_play(const seq_msg_t *m)
{
    if (timer_armed) {
        // An expired timer may still enqueue a late TICK. It cannot advance
        // the replacement before its own deadline, so no drain is needed.
        (void)esp_timer_stop(pattern_timer);
        timer_armed = false;
    }

    // Clear any outputs left active by the pattern being replaced.
    apply_mask(MOTOR_MASK_BOTH, 0);

    memcpy(steps_buf, m->steps, sizeof(motor_step_t) * m->n_steps);
    memcpy(step_masks_buf, m->step_masks, sizeof(uint8_t) * m->n_steps);
    step_count  = m->n_steps;
    step_idx    = 0;
    active_duty = intensity_to_duty(m->intensity_pct, 100);
    step_is_on  = false;
    ESP_LOGI(TAG, "[MOTOR] L=%s R=%s duty=%u steps=%d",
             (m->step_masks[0] & MOTOR_MASK_LEFT) && active_duty ? "ON" : "OFF",
             (m->step_masks[0] & MOTOR_MASK_RIGHT) && active_duty ? "ON" : "OFF",
             active_duty, step_count);
    advance_step();   // fires the first ON immediately, for its full on_ms
}

static TickType_t step_wait_ticks(void)
{
    if (!timer_armed) return portMAX_DELAY;
    int64_t remaining_us = step_deadline_us - esp_timer_get_time();
    if (remaining_us <= 0) return 0;
    // Round up: a lost/full-queue TICK must not leave an ON step stuck.
    return (TickType_t)((remaining_us * configTICK_RATE_HZ + 999999) / 1000000);
}

// Single owner of mutable sequencer state and timer start/stop operations.
static void motor_seq_task(void *arg)
{
    (void)arg;
    seq_msg_t msg;
    for (;;) {
        if (xQueueReceive(seq_queue, &msg, step_wait_ticks()) == pdTRUE) {
            if (msg.type == SEQ_MSG_PLAY) handle_play(&msg);
            else if (msg.type == SEQ_MSG_STOP) handle_stop();
        }
        // Old, duplicate and inactive TICKs carry no authority to advance.
        // A late wake-up may service a now-due step, but never an early one.
        if (timer_armed && esp_timer_get_time() >= step_deadline_us) {
            (void)esp_timer_stop(pattern_timer);
            timer_armed = false;
            advance_step();
        }
    }
}

static void queue_pattern(const uint8_t *step_masks, uint8_t intensity_pct,
                          const motor_step_t *steps, int n_steps)
{
    if (n_steps <= 0 || steps == NULL || step_masks == NULL) return;
    if (n_steps > PATTERN_MAX_PAIRS) n_steps = PATTERN_MAX_PAIRS;
    if (!seq_queue) {
        ESP_LOGE(TAG, "motor pattern requested before motor_init()");
        return;
    }
    // Treat BLE input as untrusted and clamp to the protocol range.
    if (intensity_pct > 100) {
        ESP_LOGW(TAG, "intensity %u out of range (0..100), clamping",
                 intensity_pct);
        intensity_pct = 100;
    }

    seq_msg_t m = {
        .type = SEQ_MSG_PLAY,
        .intensity_pct = intensity_pct, .n_steps = n_steps,
    };
    memcpy(m.step_masks, step_masks, sizeof(uint8_t) * n_steps);
    memcpy(m.steps, steps, sizeof(motor_step_t) * n_steps);

    // motor_seq_task owns execution; this API only queues the command.
    if (xQueueSend(seq_queue, &m, portMAX_DELAY) != pdTRUE)
        ESP_LOGE(TAG, "failed to queue motor pattern");
}

void motor_play_pattern(uint8_t motor_mask, uint8_t intensity_pct,
                        const motor_step_t *steps, int n_steps)
{
    if (n_steps <= 0 || steps == NULL) return;
    if (n_steps > PATTERN_MAX_PAIRS) n_steps = PATTERN_MAX_PAIRS;

    uint8_t step_masks[PATTERN_MAX_PAIRS];
    for (int i = 0; i < n_steps; i++) step_masks[i] = motor_mask & MOTOR_MASK_BOTH;
    queue_pattern(step_masks, intensity_pct, steps, n_steps);
}

void motor_stop_pattern(void)
{
    if (!seq_queue) {
        motor_all_off();
        return;
    }
    seq_msg_t m = { .type = SEQ_MSG_STOP };
    if (xQueueSend(seq_queue, &m, portMAX_DELAY) != pdTRUE)
        ESP_LOGE(TAG, "failed to queue motor stop");
}
