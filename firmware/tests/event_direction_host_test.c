// Host test for the event_id -> motor mask fail-safe fix in main.c.
//
// Compiles the REAL, unmodified main.c (see run_event_direction_host_tests.py
// for how the include paths and stub headers are assembled) against
// deterministic stand-ins for every FreeRTOS / ESP-IDF / BLE / TDoA /
// audio-capture symbol main.c references but this test never exercises.
// Only on_cmd(), lookup_event_direction() and record_event_direction() --
// all file-scope `static` in main.c -- are actually driven by the cases
// below; capture_task/ble_task/app_main are compiled in (so the file
// links and type-checks as a whole, completely unmodified) but never
// invoked, exactly like production main.c is one file with three tasks
// that never all run in a unit test.
//
// Same structure as firmware/tests/motor_host_test.c: pull in the real
// production source first (so its own headers define every type this file
// needs), then supply stand-in bodies for the handful of external
// functions it calls -- their prototypes are already visible via the real
// headers main.c included, so the definitions can come later in this same
// translation unit.
#include "host_stub.h"
#include "main_under_test.c"

#include <stdio.h>
#include <string.h>

// ---- stand-ins for everything else main.c calls (referenced only from
// capture_task / ble_task / app_main, none of which this test invokes --
// their bodies just need to exist so the whole file links) ----
QueueHandle_t xQueueCreate(int n, size_t item_size)
{ (void)n; (void)item_size; return (void *)1; }
int xQueueSend(QueueHandle_t q, const void *item, TickType_t wait)
{ (void)q; (void)item; (void)wait; return pdTRUE; }
int xQueueReceive(QueueHandle_t q, void *item, TickType_t wait)
{ (void)q; (void)item; (void)wait; return 0; }
BaseType_t xTaskCreatePinnedToCore(void (*fn)(void *), const char *name,
                                   uint32_t stack, void *arg, int prio,
                                   TaskHandle_t *out, int core)
{
    (void)fn; (void)name; (void)stack; (void)arg; (void)prio; (void)core;
    if (out) *out = (void *)1;
    return pdPASS;
}
esp_err_t nvs_flash_init(void) { return ESP_OK; }
esp_err_t nvs_flash_erase(void) { return ESP_OK; }
esp_err_t audio_capture_init(void) { return ESP_OK; }
esp_err_t audio_capture_read(audio_frame_t *out, int timeout_ms)
{ (void)out; (void)timeout_ms; return ESP_OK; }
void tdoa_init(void) {}
void tdoa_set_bus_skew_samples(int samples) { (void)samples; }
void tdoa_estimate(const audio_frame_t *f, direction_t *out)
{ (void)f; if (out) memset(out, 0, sizeof(*out)); }
void resample_init(void) {}
void resample_reset(void) {}
int resample_48k_to_16k(const float *in, int n_in, int16_t *out)
{ (void)in; (void)n_in; (void)out; return 0; }
void ble_svc_init(void) {}
bool ble_svc_connected(void) { return false; }
int ble_svc_send_audio(uint8_t event_id, const int16_t *pcm, int n)
{ (void)event_id; (void)pcm; (void)n; return 0; }
int ble_svc_send_direction(uint8_t event_id, uint8_t dir_byte,
                           float confidence, float rms_dbfs)
{ (void)event_id; (void)dir_byte; (void)confidence; (void)rms_dbfs; return 0; }
void ble_svc_set_cmd_cb(ble_cmd_cb_t cb) { (void)cb; }
void motor_init(void) {}

// Mirrors motor.c's DIR_TO_MOTOR (identity mapping) -- see file header.
static const int TEST_DIR_TO_MOTOR[8] = {0, 1, 2, 3, 4, 5, 6, 7};
int motor_bit_for_direction(int dir_index)
{
    return (dir_index < 0 || dir_index >= 8) ? -1 : TEST_DIR_TO_MOTOR[dir_index];
}

// This is what every test case actually inspects: what on_cmd() decided to
// send into the motor layer.
static uint8_t last_mask, last_intensity;
static int play_calls;
void motor_play_pattern(uint8_t motor_mask, uint8_t intensity_pct,
                        const motor_step_t *steps, int n_steps)
{
    (void)steps; (void)n_steps;
    last_mask = motor_mask;
    last_intensity = intensity_pct;
    play_calls++;
}

// ---------------------------------------------------------------------

static int failures, checks;
#define CHECK(c) do { checks++; if (!(c)) { \
    printf("  FAIL: %s (line %d)\n", #c, __LINE__); failures++; } } while (0)
// Per-case status must compare against failures AT THE START of that case,
// not the cumulative total -- otherwise one early failure mislabels every
// later (individually passing) case as "SEE ABOVE" too.
#define CASE_RESULT(before) (failures == (before) ? "PASS" : "SEE ABOVE")

static void reset_capture(void)
{
    last_mask = 0xAB;      // a value no real path should ever leave behind
    last_intensity = 0xAB;
    play_calls = 0;
    log_w_count = log_e_count = 0;
    last_w_msg[0] = last_e_msg[0] = '\0';
}

static const motor_step_t STEP[1] = { { 100, 100 } };

int main(void)
{
    // ---- Case 1: valid event_id -> existing motor mask, UNCHANGED ----
    // Direction 3 (RIGHT) -> identity mapping -> motor 3 -> mask 1<<3.
    int case1_before = failures;
    reset_capture();
    record_event_direction(11, 3);
    on_cmd(11, 42, 0, STEP, 1);
    CHECK(play_calls == 1);
    CHECK(last_mask == (uint8_t)(1u << 3));
    CHECK(last_intensity == 42);
    CHECK(log_w_count == 0 && log_e_count == 0);
    printf("Case 1 (valid event_id -> mask 0x%02X): %s\n",
           (uint8_t)(1u << 3), CASE_RESULT(case1_before));

    // ---- Case 2: never-recorded event_id (lookup miss) -> 0x00 ----
    int case2_before = failures;
    reset_capture();
    on_cmd(222 /* never recorded */, 77, 1, STEP, 1);
    CHECK(play_calls == 1);
    CHECK(last_mask == 0x00);
    CHECK(last_intensity == 77);            // fail-safe only changes the
                                             // mask, not the rest of the
                                             // command
    CHECK(log_w_count == 1 && log_e_count == 0);
    printf("Case 2 (never-recorded event_id -> mask 0x00): %s\n",
           CASE_RESULT(case2_before));

    // ---- Case 3: event_id that WAS recorded, then evicted by ring wrap
    // (EVENT_HISTORY slots) before its CMD arrived -- the actual "evicted
    // or stale" scenario the log message describes -- -> 0x00 ----
    int case3_before = failures;
    reset_capture();
    record_event_direction(50, 2);          // slot 0
    for (int i = 0; i < EVENT_HISTORY; i++)
        record_event_direction((uint8_t)(150 + i), 0);  // wraps exactly
                                                          // once; the last
                                                          // of these
                                                          // overwrites
                                                          // slot 0 again
    on_cmd(50 /* now evicted */, 30, 0, STEP, 1);
    CHECK(play_calls == 1);
    CHECK(last_mask == 0x00);
    CHECK(log_w_count == 1 && log_e_count == 0);
    printf("Case 3 (evicted event_id -> mask 0x00): %s\n",
           CASE_RESULT(case3_before));

    // ---- Case 4: genuine TDoA-confirmed DIR_UNKNOWN (a real, recorded
    // event whose direction TDoA legitimately could not resolve) ->
    // mask 0xFF, UNCHANGED. This is the intentional use of 0xFF the fix
    // must NOT touch. ----
    int case4_before = failures;
    reset_capture();
    record_event_direction(88, DIR_UNKNOWN);
    on_cmd(88, 60, 2, STEP, 1);
    CHECK(play_calls == 1);
    CHECK(last_mask == 0xFF);
    CHECK(log_w_count == 0 && log_e_count == 0);   // not an error -- no log
    printf("Case 4 (recorded DIR_UNKNOWN -> mask 0xFF, unchanged): %s\n",
           CASE_RESULT(case4_before));

    // ---- Case 5: corrupted/invalid stored direction (neither DIR_UNKNOWN
    // nor 0..7) -> 0x00. record_event_direction() itself does not validate
    // its input, so this models the defensive branch directly. ----
    int case5_before = failures;
    reset_capture();
    record_event_direction(99, 42 /* not DIR_UNKNOWN, not 0..7 */);
    on_cmd(99, 10, 0, STEP, 1);
    CHECK(play_calls == 1);
    CHECK(last_mask == 0x00);
    CHECK(log_e_count == 1 && log_w_count == 0);   // this one is an ESP_LOGE
    printf("Case 5 (invalid stored direction -> mask 0x00): %s\n",
           CASE_RESULT(case5_before));

    // ---- Case 6: all 8 directions still map identically through the
    // normal path -- confirms the fix touches only the two error paths. ----
    int all_ok = 1;
    for (int d = 0; d < 8; d++) {
        reset_capture();
        record_event_direction((uint8_t)(200 + d), (uint8_t)d);
        on_cmd((uint8_t)(200 + d), 5, 0, STEP, 1);
        if (last_mask != (uint8_t)(1u << d) || log_w_count || log_e_count)
            all_ok = 0;
        CHECK(last_mask == (uint8_t)(1u << d));
        CHECK(log_w_count == 0 && log_e_count == 0);
    }
    printf("Case 6 (all 8 directions, identity mapping intact): %s\n",
           all_ok ? "PASS" : "SEE ABOVE");

    printf("\nRESULT: %d/%d checks passed, %d failing\n",
           checks - failures, checks, failures);
    return failures ? 1 : 0;
}
