// MEIT belt - ESP32-S3 main.
//
// Task layout (why): capture must never block on BLE, so BLE lives on its
// own task behind a queue. TDoA runs in the capture task because it is only
// a few ms per 21 ms frame.
//
//   capture_task : I2S read -> loudness gate -> TDoA vote -> decimate -> queue
//   ble_task     : drain queue -> notify laptop (chunked)
//   cmd callback : laptop reply -> look up event's direction -> motor
//
// See PROTOCOL.md for the exact wire contract this file implements against
// meit-ai's decision/judge.py output.

#include <string.h>
#include <stdbool.h>
#include <stdlib.h>
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/idf_additions.h"   // xQueueCreateWithCaps
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "config.h"
#include "audio_capture.h"
#include "tdoa.h"
#include "resample.h"
#include "ble_svc.h"
#include "motor.h"

static const char *TAG = "main";
typedef struct { int16_t pcm[CLIP_OUT_SAMPLES]; int n; uint8_t event_id; } clip_t;
static QueueHandle_t q;

// clip_t is ~82 KB (2.56 s of 16 kHz PCM16). Two static working copies plus a
// 2-deep queue would need ~330 KB of INTERNAL RAM: static .bss is internal,
// and plain xQueueCreate() allocates via pvPortMalloc(), which ESP-IDF 5.2
// pins to MALLOC_CAP_INTERNAL (components/freertos/heap_idf.c) regardless of
// CONFIG_SPIRAM_USE_MALLOC. That does not fit alongside NimBLE. All clip
// storage therefore lives in the 8 MB PSRAM: allocated once in app_main(),
// before any task that uses it is created. Nothing here is touched by DMA.
static clip_t *cap_clip;   // capture_task's working clip (only writer)
static clip_t *ble_clip;   // ble_task's receive buffer (only reader)

static clip_t *alloc_clip_psram(const char *what)
{
    clip_t *c = heap_caps_calloc(1, sizeof(clip_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!c) {
        ESP_LOGE(TAG, "PSRAM alloc for %s (%u bytes) failed -- is PSRAM enabled?",
                 what, (unsigned)sizeof(clip_t));
        abort();
    }
    return c;
}

// ---- event_id -> direction lookup -------------------------------------
// Replaces a single `last_dir` global (the previous version's actual bug):
// capture_task can start a new event before the AI has replied to the
// previous one -- collecting=0 happens as soon as one clip's audio is
// queued, not after its CMD comes back -- so a single global gets
// overwritten mid-flight and a CMD can end up firing on the WRONG
// direction. A small ring keyed by event_id fixes this without having to
// serialize events (which would mean silently ignoring a second danger
// sound that arrives while the first is still being classified -- exactly
// what meit-ai's Recall-first design says not to do).
static struct { uint8_t event_id; uint8_t dir; bool used; } event_table[EVENT_HISTORY];
static int event_table_next = 0;
static portMUX_TYPE event_mux = portMUX_INITIALIZER_UNLOCKED;

static void record_event_direction(uint8_t event_id, uint8_t dir)
{
    portENTER_CRITICAL(&event_mux);
    event_table[event_table_next].event_id = event_id;
    event_table[event_table_next].dir = dir;
    event_table[event_table_next].used = true;
    event_table_next = (event_table_next + 1) % EVENT_HISTORY;
    portEXIT_CRITICAL(&event_mux);
}

static bool lookup_event_direction(uint8_t event_id, uint8_t *dir_out)
{
    bool found = false;
    portENTER_CRITICAL(&event_mux);
    for (int i = 0; i < EVENT_HISTORY; i++)
        if (event_table[i].used && event_table[i].event_id == event_id) {
            *dir_out = event_table[i].dir; found = true; break;
        }
    portEXIT_CRITICAL(&event_mux);
    return found;
}

static void on_cmd(uint8_t event_id, uint8_t intensity, uint8_t sound_class,
                   const motor_step_t *steps, int n_steps)
{
    (void)sound_class;   // not needed to drive the motors; log it if/when
                         // SD-card event logging is added on this side.
    uint8_t dir;
    if (!lookup_event_direction(event_id, &dir)) {
        ESP_LOGW(TAG, "CMD for unknown event_id=%u -- motors OFF", event_id);
        motor_play_pattern(0, intensity, steps, n_steps);
        return;
    }
    if (dir == DIR_UNKNOWN) {
        ESP_LOGI(TAG, "[DIR] UNKNOWN event=%u: alternating LEFT/RIGHT alert", event_id);
        motor_play_unknown_pattern(intensity);
        return;
    }
    uint8_t mask = motor_mask_for_direction(dir);
    if (mask == 0)
        ESP_LOGE(TAG, "event %u invalid direction=%u -- motors OFF", event_id, dir);
    ESP_LOGI(TAG, "[DIR] %s event=%u [MOTOR] L=%s R=%s",
             direction_name(direction_from_wire(dir)), event_id,
             (mask & MOTOR_MASK_LEFT) ? "ON" : "OFF",
             (mask & MOTOR_MASK_RIGHT) ? "ON" : "OFF");
    motor_play_pattern(mask, intensity, steps, n_steps);
}

// ---- capture / vote / collect state machine ----------------------------
typedef enum { EV_IDLE, EV_VOTING, EV_COLLECTING, EV_COOLDOWN } ev_state_t;

#if !MEIT_FAKE_EVENTS   // not built in fake mode (would be an unused static)
static void capture_task(void *arg)
{
    static audio_frame_t f;
    clip_t *clip = cap_clip;   // PSRAM, allocated in app_main()
    static float dir_votes[DIRECTION_COUNT];
    static int dir_counts[DIRECTION_COUNT];
    static ev_state_t state = EV_IDLE;
    static int votes_left = 0;
    static float mono[FRAME_LEN];
    static unsigned log_frames;
    static unsigned read_errors;
    static uint8_t event_id_ctr = 0;
    static uint8_t cur_event_id = 0;
    static int cooldown_left = 0;

    while (1) {
        esp_err_t read_err = audio_capture_read(&f, 200);
        if (read_err != ESP_OK) {
            if ((read_errors++ % 50) == 0)
                ESP_LOGW(TAG, "[MIC] capture error=%d; dropping partial event", (int)read_err);
            // Drop a partial event: never present discontinuous audio as a full clip.
            state = EV_IDLE;
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        read_errors = 0;
        if (++log_frames >= TDOA_FS_HZ / FRAME_LEN) {
            ESP_LOGI(TAG, "[MIC] L_rms=%.1f R_rms=%.1f dBFS",
                     f.rms_dbfs[CH_LEFT], f.rms_dbfs[CH_RIGHT]);
            log_frames = 0;
        }

        if (state == EV_COOLDOWN) {
            // Sustained loud sound (a real siren, say) must not re-trigger
            // a brand new event on the very next 21ms frame -- that would
            // flood the BLE queue and hand the AI a stream of near-
            // duplicate clips instead of one. See COOLDOWN_FRAMES.
            if (--cooldown_left <= 0) state = EV_IDLE;
            continue;
        }

        float loud_db = fmaxf(f.rms_dbfs[CH_LEFT], f.rms_dbfs[CH_RIGHT]);

        if (state == EV_IDLE) {
            // This is a PRE-gate only (see RMS_GATE_DBFS in config.h) --
            // meit-ai's own DB_GATE makes the real call. Whether TDoA can
            // resolve a direction is irrelevant to whether a dangerous
            // sound is happening -- meit-ai's classifier decides that from
            // the audio itself, and their design explicitly prioritises
            // Recall over precision. See PROTOCOL.md.
            if (loud_db < RMS_GATE_DBFS) continue;
            cur_event_id = event_id_ctr++;
            memset(dir_votes, 0, sizeof(dir_votes));
            memset(dir_counts, 0, sizeof(dir_counts));
            votes_left = TDOA_VOTE_FRAMES;
            clip->n = 0;
            resample_reset();   // don't let the previous event's FIR tail
                                // leak into the next clip
            state = EV_VOTING;
            // fall through -- don't waste this frame's audio or TDoA read
        }

        if (state == EV_VOTING) {
            // A single 21ms frame's direction can be noisy (reflection-
            // heavy first instant of an onset, or a partial cycle of a
            // warbling siren). Vote over the first ~125ms instead of
            // trusting frame 1 alone -- cheap given the AI needs the full
            // 2.56 s clip regardless, so this adds little to end-to-end
            // latency. Confidence-weighted so a few strong reads outvote
            // several weak/ambiguous ones. TDOA_MIN_VALID_VOTES requires
            // the WINNING bin specifically to have that many frames behind
            // it (dir_counts[best]), not just that many valid frames
            // total across any bins -- e.g. RIGHT once (conf 0.3) + LEFT
            // once (conf 0.2) + 4 unknown frames must NOT confirm RIGHT
            // just because 2 frames were valid somewhere. An earlier draft
            // checked the total count instead of the per-bin count, which
            // let exactly that case through.
            direction_t d;
            tdoa_estimate(&f, &d);
            // Bring-up aid, not a decision input: this threshold (config.h
            // MIN_CONFIDENCE) has only been checked against synthetic
            // signals, and the C confidence formula isn't numerically the
            // same as Python's reference (see config.h). Once real mics
            // are wired up, watch this line for horn/siren/crash/normal
            // and pick a real threshold from what actually shows up here.
            ESP_LOGI(TAG, "[TDOA] delay_samples=%.3f delay_us=%.1f conf=%.2f [DIR] %s",
                     d.tau_lr_s * TDOA_FS_HZ, d.tau_lr_s * 1e6f,
                     d.confidence, direction_name(d.index));
            if (d.confidence > 0.0f && d.index >= 0 && d.index < DIRECTION_COUNT) {
                dir_votes[d.index] += d.confidence;
                dir_counts[d.index]++;
            }
            votes_left--;
            audio_downmix(&f, mono);
            clip->n += resample_48k_to_16k(mono, FRAME_LEN, clip->pcm + clip->n);

            if (votes_left <= 0) {
                int best = -1; float best_w = 0.0f;
                for (int i = 0; i < DIRECTION_COUNT; i++)
                    if (dir_votes[i] > best_w) { best_w = dir_votes[i]; best = i; }

                uint8_t dir_byte; float conf;
                if (best < 0 || dir_counts[best] < TDOA_MIN_VALID_VOTES) {
                    dir_byte = DIR_UNKNOWN;
                    conf = 0.0f;
                } else {
                    dir_byte = direction_to_wire(best);
                    conf = best_w / TDOA_VOTE_FRAMES;
                    if (conf > 1.0f) conf = 1.0f;
                }

                record_event_direction(cur_event_id, dir_byte);
                ble_svc_send_direction(cur_event_id, dir_byte, conf, loud_db);
                state = EV_COLLECTING;
            }
            continue;
        }

        // EV_COLLECTING
        audio_downmix(&f, mono);
        clip->n += resample_48k_to_16k(mono, FRAME_LEN, clip->pcm + clip->n);
        if (clip->n >= CLIP_OUT_SAMPLES) {
            clip->event_id = cur_event_id;
            if (xQueueSend(q, clip, 0) != pdTRUE)
                ESP_LOGW(TAG, "BLE queue full, dropping clip for event %u "
                             "(BLE task can't keep up)", cur_event_id);
            cooldown_left = COOLDOWN_FRAMES;
            state = EV_COOLDOWN;
        }
    }
}

#endif  // !MEIT_FAKE_EVENTS

#if MEIT_FAKE_EVENTS
// ---- bring-up only: synthetic events, no microphones needed -------------
// Exercises everything AFTER the microphones exactly as production does:
// record_event_direction() -> DIR notify -> the same clip queue/ble_task ->
// chunked AUDIO -> laptop -> CMD -> on_cmd() -> motor. Direction cycles
// LEFT / RIGHT / BACK so each CMD checks the corresponding motor mask. The clip is a fixed
// 1 kHz tone at about -20 dBFS (loud enough to pass meit-ai's DB_GATE).
// Measured 2026-09-23 with the current meit-ai model: this tone is
// classified "siren" (~0.56 > THRESHOLD 0.4), so the REAL AI path also
// sends a CMD -- but that is a model quirk, not a guarantee. For a
// deterministic motor-path test use `ble_receiver.py --mock-ai`.
// capture_task is not started in this mode (see app_main).
static void fake_event_task(void *arg)
{
    clip_t *clip = cap_clip;   // capture_task does not run in this mode
    for (int i = 0; i < CLIP_OUT_SAMPLES; i++)
        clip->pcm[i] = (int16_t)(3277.0f * sinf(2.0f * (float)M_PI * 1000.0f * i / AI_FS_HZ));
    clip->n = CLIP_OUT_SAMPLES;

    uint8_t event_id = 0;
    int index = DIR_LEFT;
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(MEIT_FAKE_EVENT_PERIOD_MS));
        if (!ble_svc_connected()) continue;
        uint8_t dir = direction_to_wire(index);
        record_event_direction(event_id, dir);
        ble_svc_send_direction(event_id, dir, 1.0f, -20.0f);
        clip->event_id = event_id;
        if (xQueueSend(q, clip, 0) != pdTRUE)
            ESP_LOGW(TAG, "FAKE: BLE queue full, dropped event %u", event_id);
        else
            ESP_LOGI(TAG, "FAKE event %u dir=%u queued", event_id, dir);
        event_id++;
        index = (index + 1) % DIRECTION_COUNT;
    }
}
#endif

static void ble_task(void *arg)
{
    clip_t *clip = ble_clip;   // PSRAM, allocated in app_main()
    while (1) {
        if (xQueueReceive(q, clip, portMAX_DELAY) != pdTRUE) continue;
        if (!ble_svc_connected()) continue;
        ble_svc_send_audio(clip->event_id, clip->pcm, clip->n);
    }
}

void app_main(void)
{
    // Required before initializing NimBLE (Espressif's own docs are
    // explicit about this ordering) -- NimBLE stores bonding/identity data
    // in NVS and its init sequence expects the partition to already be
    // ready. Without this, ble_svc_init() further down would either fail
    // or (worse, depending on IDF version) silently misbehave.
    esp_err_t nvs_rc = nvs_flash_init();
    if (nvs_rc == ESP_ERR_NVS_NO_FREE_PAGES || nvs_rc == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_rc = nvs_flash_init();
    }
    ESP_ERROR_CHECK(nvs_rc);

    // Queue storage (2 x ~82 KB) in PSRAM too -- see alloc_clip_psram().
    q = xQueueCreateWithCaps(2, sizeof(clip_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!q) {
        ESP_LOGE(TAG, "clip queue alloc (2 x %u bytes, PSRAM) failed",
                 (unsigned)sizeof(clip_t));
        abort();
    }
    cap_clip = alloc_clip_psram("capture clip");
    ble_clip = alloc_clip_psram("BLE clip");
    motor_init();
    resample_init();
#if !MEIT_FAKE_EVENTS
    tdoa_init();
    ESP_ERROR_CHECK(audio_capture_init());
#endif

    ble_svc_init();
    ble_svc_set_cmd_cb(on_cmd);

#if MEIT_FAKE_EVENTS
    ESP_LOGW(TAG, "MEIT_FAKE_EVENTS=1: synthetic events every %d ms, "
                  "microphone capture task NOT started", MEIT_FAKE_EVENT_PERIOD_MS);
    xTaskCreatePinnedToCore(fake_event_task, "fake", 4096, NULL, 6, NULL, 1);
#else
    xTaskCreatePinnedToCore(capture_task, "cap", 8192, NULL, 6, NULL, 1);
#endif
    xTaskCreatePinnedToCore(ble_task,     "ble", 4096, NULL, 4, NULL, 0);
    ESP_LOGI(TAG, "running");
}
