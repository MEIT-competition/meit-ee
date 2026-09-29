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
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>

static jmp_buf capture_done;
static int capture_running, capture_index, capture_reads, sample_phase, captured_n;
static int sent_dir = -2;
static int downmix_ok = 1;
static uint8_t captured_id;

// ---- stand-ins for everything else main.c calls (referenced only from
// capture_task / ble_task / app_main, none of which this test invokes --
// their bodies just need to exist so the whole file links) ----
QueueHandle_t xQueueCreate(int n, size_t item_size)
{ (void)n; (void)item_size; return (void *)1; }
QueueHandle_t xQueueCreateWithCaps(int n, size_t item_size, uint32_t caps)
{ (void)n; (void)item_size; (void)caps; return (void *)1; }
void *heap_caps_calloc(size_t n, size_t size, uint32_t caps)
{ (void)caps; return calloc(n, size); }
int xQueueSend(QueueHandle_t q, const void *item, TickType_t wait)
{
    (void)q; (void)wait;
    if (capture_running) {
        const clip_t *clip = item;
        captured_n = clip->n;
        captured_id = clip->event_id;
        longjmp(capture_done, 1);
    }
    return pdTRUE;
}
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
{
    (void)timeout_ms;
    if (++capture_reads > CLIP_FRAMES + 1) abort();
    for (int n=0; n<FRAME_LEN; ++n) {
        out->ch[CH_LEFT][n] = .75f;
        out->ch[CH_RIGHT][n] = .25f;
    }
    out->rms_dbfs[CH_LEFT] = out->rms_dbfs[CH_RIGHT] = -20;
    return ESP_OK;
}
void tdoa_init(void) {}
void vTaskDelay(TickType_t ticks) { (void)ticks; }
void tdoa_estimate(const audio_frame_t *f, direction_t *out)
{
    (void)f;
    memset(out, 0, sizeof(*out));
    out->index = capture_index;
    out->confidence = capture_index >= 0 ? 1.0f : 0.0f;
}
void resample_init(void) {}
void resample_reset(void) { sample_phase = 0; }
int resample_48k_to_16k(const float *in, int n_in, int16_t *out)
{
    int count = 0;
    for (int n=0; n<n_in; ++n) {
        if (in[n] != .5f) downmix_ok = 0;
        if (sample_phase == 0) out[count++] = (int16_t)(in[n] * 32767);
        sample_phase = (sample_phase + 1) % DECIM;
    }
    return count;
}
void ble_svc_init(void) {}
bool ble_svc_connected(void) { return false; }
int ble_svc_send_audio(uint8_t event_id, const int16_t *pcm, int n)
{ (void)event_id; (void)pcm; (void)n; return 0; }
int ble_svc_send_direction(uint8_t event_id, uint8_t dir_byte,
                           float confidence, float rms_dbfs)
{
    (void)event_id; (void)rms_dbfs;
    if (dir_byte == DIR_UNKNOWN && confidence != 0) abort();
    sent_dir = dir_byte;
    return 0;
}
void ble_svc_set_cmd_cb(ble_cmd_cb_t cb) { (void)cb; }
void motor_init(void) {}

// This is what every test case actually inspects: what on_cmd() decided to
// send into the motor layer.
static uint8_t last_mask, last_intensity, last_unknown_intensity;
static int play_calls, unknown_calls;
void motor_play_pattern(uint8_t motor_mask, uint8_t intensity_pct,
                        const motor_step_t *steps, int n_steps)
{
    (void)steps; (void)n_steps;
    last_mask = motor_mask;
    last_intensity = intensity_pct;
    play_calls++;
}
void motor_play_unknown_pattern(uint8_t intensity_pct)
{
    last_unknown_intensity = intensity_pct;
    unknown_calls++;
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
    last_unknown_intensity = 0xAB;
    play_calls = 0;
    unknown_calls = 0;
    log_w_count = log_e_count = 0;
    last_w_msg[0] = last_e_msg[0] = '\0';
}

static const motor_step_t STEP[1] = { { 100, 100 } };

int main(int argc, char **argv)
{
    // ---- Case 1: valid event_id -> existing motor mask, UNCHANGED ----
    // Retained wire RIGHT=2 selects only physical motor index 1.
    int case1_before = failures;
    reset_capture();
    record_event_direction(11, DIR_WIRE_RIGHT);
    on_cmd(11, 42, 0, STEP, 1);
    CHECK(play_calls == 1);
    CHECK(unknown_calls == 0);
    CHECK(last_mask == MOTOR_MASK_RIGHT);
    CHECK(last_intensity == 42);
    CHECK(log_w_count == 0 && log_e_count == 0);
    printf("Case 1 (valid event_id -> mask 0x%02X): %s\n",
           MOTOR_MASK_RIGHT, CASE_RESULT(case1_before));

    // ---- Case 2: never-recorded event_id (lookup miss) -> 0x00 ----
    int case2_before = failures;
    reset_capture();
    on_cmd(222 /* never recorded */, 77, 1, STEP, 1);
    CHECK(play_calls == 1);
    CHECK(unknown_calls == 0);
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
    record_event_direction(50, DIR_WIRE_RIGHT);          // slot 0
    for (int i = 0; i < EVENT_HISTORY; i++)
        record_event_direction((uint8_t)(150 + i), DIR_WIRE_LEFT);  // wraps exactly
                                                          // once; the last
                                                          // of these
                                                          // overwrites
                                                          // slot 0 again
    on_cmd(50 /* now evicted */, 30, 0, STEP, 1);
    CHECK(play_calls == 1);
    CHECK(unknown_calls == 0);
    CHECK(last_mask == 0x00);
    CHECK(log_w_count == 1 && log_e_count == 0);
    printf("Case 3 (evicted event_id -> mask 0x00): %s\n",
           CASE_RESULT(case3_before));

    // ---- Case 4: genuine TDoA-confirmed DIR_UNKNOWN (a real, recorded
    // event whose direction TDoA legitimately could not resolve) ->
    // dedicated alternating LEFT/RIGHT alert. This must NOT use the
    // normal motor_play_pattern() path or the old 0xFF all-motors mask. ----
    int case4_before = failures;
    reset_capture();
    record_event_direction(88, DIR_UNKNOWN);
    on_cmd(88, 60, 2, STEP, 1);
    CHECK(play_calls == 0);
    CHECK(unknown_calls == 1);
    CHECK(last_unknown_intensity == 60);
    CHECK(log_w_count == 0 && log_e_count == 0);
    printf("Case 4 (recorded DIR_UNKNOWN -> alternating LEFT/RIGHT alert): %s\n",
           CASE_RESULT(case4_before));

    // ---- Case 5: corrupted/invalid stored direction (neither DIR_UNKNOWN
    // nor a supported wire value) -> 0x00. record_event_direction() itself does not validate
    // its input, so this models the defensive branch directly. ----
    int case5_before = failures;
    reset_capture();
    record_event_direction(99, 42 /* not DIR_UNKNOWN, not a supported wire value */);
    on_cmd(99, 10, 0, STEP, 1);
    CHECK(play_calls == 1);
    CHECK(unknown_calls == 0);
    CHECK(last_mask == 0x00);
    CHECK(log_e_count == 1 && log_w_count == 0);   // this one is an ESP_LOGE
    printf("Case 5 (invalid stored direction -> mask 0x00): %s\n",
           CASE_RESULT(case5_before));

    // Exercise every supported wire value through the event lookup and CMD path.
    const uint8_t wires[] = {DIR_WIRE_LEFT, DIR_WIRE_RIGHT, DIR_WIRE_BACK};
    const uint8_t masks[] = {MOTOR_MASK_LEFT, MOTOR_MASK_RIGHT, MOTOR_MASK_BOTH};
    for (int d = 0; d < DIRECTION_COUNT; d++) {
        reset_capture();
        record_event_direction((uint8_t)(200 + d), wires[d]);
        on_cmd((uint8_t)(200 + d), 5, 0, STEP, 1);
        CHECK(last_mask == masks[d]);
        CHECK(unknown_calls == 0);
        CHECK(log_w_count == 0 && log_e_count == 0);
    }
    // Retired wire values must never alias compact vote indices.
    const uint8_t invalid[] = {0, 1, 3, 5, 7};
    for (unsigned d = 0; d < sizeof(invalid); ++d) {
        reset_capture();
        record_event_direction(100, invalid[d]);
        on_cmd(100, 50, 0, STEP, 1);
        CHECK(last_mask == 0);
        CHECK(log_e_count == 1);
        CHECK(unknown_calls == 0);
    }

    // Exercise real capture/vote/collect flow for each compact index and UNKNOWN.
    capture_index = argc > 1 ? atoi(argv[1]) : DIR_BACK;
    capture_running = 1;
    cap_clip = calloc(1, sizeof(*cap_clip));
    if (!cap_clip) abort();
    if (setjmp(capture_done) == 0) capture_task(NULL);
    capture_running = 0;
    CHECK(capture_reads == CLIP_FRAMES);
    CHECK(captured_n == CLIP_OUT_SAMPLES);
    CHECK(downmix_ok);
    CHECK(sent_dir == direction_to_wire(capture_index));
    uint8_t stored;
    CHECK(lookup_event_direction(captured_id, &stored));
    CHECK(stored == direction_to_wire(capture_index));
    free(cap_clip);
    printf("Capture/vote index=%d -> wire=%d, samples=%d\n",
           capture_index, sent_dir, captured_n);
    printf("\nRESULT: %d/%d checks passed, %d failing\n",
           checks - failures, checks, failures);
    return failures ? 1 : 0;
}
