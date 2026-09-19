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
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "config.h"
#include "audio_capture.h"
#include "tdoa.h"
#include "resample.h"
#include "ble_svc.h"
#include "motor.h"

static const char *TAG = "main";
const int MOTOR_GPIO[8] = {1, 2, 13, 4, 8, 9, 10, 11};
// GPIO3 deliberately avoided: it's an ESP32-S3 strapping/chip-boot-config
// pin (see firmware/PINMAP.md "Reserved / avoided GPIO" -- GPIO0/3/45/46).
// Using it for anything external risks boot issues if the DRV8833 or
// wiring pulls it during reset -- not worth the debugging time on a 5-day
// schedule. VERIFY the rest against the actual LOLIN S3 pinout regardless.

typedef struct { int16_t pcm[CLIP_OUT_SAMPLES]; int n; uint8_t event_id; } clip_t;
static QueueHandle_t q;

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
        ESP_LOGW(TAG, "CMD for unknown event_id=%u (evicted or stale?) -- "
                     "firing all motors as a safe fallback", event_id);
        dir = DIR_UNKNOWN;
    }

    uint8_t mask;
    if (dir == DIR_UNKNOWN) {
        // No reliable direction, but the AI still says this is a danger
        // sound. Default: alert on every motor rather than staying silent.
        // CONFIRM with the team -- an "unknown direction" pattern distinct
        // from a directional one may communicate better to the wearer.
        mask = 0xFF;
    } else {
        int bit = motor_bit_for_direction(dir);
        mask = (bit < 0) ? 0xFF : (uint8_t)(1 << bit);
    }
    motor_play_pattern(mask, intensity, steps, n_steps);
}

// ---- capture / vote / collect state machine ----------------------------
typedef enum { EV_IDLE, EV_VOTING, EV_COLLECTING, EV_COOLDOWN } ev_state_t;

static void capture_task(void *arg)
{
    static audio_frame_t f;
    static clip_t clip;
    static float dir_votes[8];
    static int dir_counts[8];
    static ev_state_t state = EV_IDLE;
    static int votes_left = 0;
    static int chosen_ch = CH_FRONT;
    static uint8_t event_id_ctr = 0;
    static uint8_t cur_event_id = 0;
    static int cooldown_left = 0;

    while (1) {
        if (audio_capture_read(&f, 200) != ESP_OK) continue;

        if (state == EV_COOLDOWN) {
            // Sustained loud sound (a real siren, say) must not re-trigger
            // a brand new event on the very next 21ms frame -- that would
            // flood the BLE queue and hand the AI a stream of near-
            // duplicate clips instead of one. See COOLDOWN_FRAMES.
            if (--cooldown_left <= 0) state = EV_IDLE;
            continue;
        }

        // Loudest of the 4 mics, not just FRONT -- a sound from behind can
        // be shadowed by the torso on the front mic alone, which would
        // both under-read the loudness gate and hand the AI a weaker
        // recording than the belt actually captured. Whichever channel
        // wins is fixed for the rest of THIS event so the AI clip doesn't
        // jump between mics mid-recording; it is re-evaluated fresh at the
        // next event.
        int loud_ch = 0; float loud_db = f.rms_dbfs[0];
        for (int c = 1; c < NUM_MICS; c++)
            if (f.rms_dbfs[c] > loud_db) { loud_db = f.rms_dbfs[c]; loud_ch = c; }

        if (state == EV_IDLE) {
            // This is a PRE-gate only (see RMS_GATE_DBFS in config.h) --
            // meit-ai's own DB_GATE makes the real call. Whether TDoA can
            // resolve a direction is irrelevant to whether a dangerous
            // sound is happening -- meit-ai's classifier decides that from
            // the audio itself, and their design explicitly prioritises
            // Recall over precision. See PROTOCOL.md.
            if (loud_db < RMS_GATE_DBFS) continue;
            cur_event_id = event_id_ctr++;
            chosen_ch = loud_ch;
            memset(dir_votes, 0, sizeof(dir_votes));
            memset(dir_counts, 0, sizeof(dir_counts));
            votes_left = TDOA_VOTE_FRAMES;
            clip.n = 0;
            resample_reset();   // don't let the previous event's FIR tail
                                // (possibly a different mic channel) leak in
            state = EV_VOTING;
            // fall through -- don't waste this frame's audio or TDoA read
        }

        if (state == EV_VOTING) {
            // A single 21ms frame's direction can be noisy (reflection-
            // heavy first instant of an onset, or a partial cycle of a
            // warbling siren). Vote over the first ~125ms instead of
            // trusting frame 1 alone -- cheap given the AI needs the full
            // ~0.5s clip regardless, so this adds little to end-to-end
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
            ESP_LOGI(TAG, "event %u frame %d: idx=%d conf=%.2f (lr=%.2f fb=%.2f) rms=%.1f",
                    cur_event_id, TDOA_VOTE_FRAMES - votes_left,
                    d.index, d.confidence, d.conf_lr, d.conf_fb, loud_db);
            if (d.confidence > 0.0f) {
                dir_votes[d.index & 7] += d.confidence;
                dir_counts[d.index & 7]++;
            }
            votes_left--;
            clip.n += resample_48k_to_16k(f.ch[chosen_ch], FRAME_LEN, clip.pcm + clip.n);

            if (votes_left <= 0) {
                int best = -1; float best_w = 0.0f;
                for (int i = 0; i < 8; i++)
                    if (dir_votes[i] > best_w) { best_w = dir_votes[i]; best = i; }

                uint8_t dir_byte; float conf;
                if (best < 0 || dir_counts[best] < TDOA_MIN_VALID_VOTES) {
                    dir_byte = DIR_UNKNOWN;
                    conf = 0.0f;
                } else {
                    dir_byte = (uint8_t)best;
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
        clip.n += resample_48k_to_16k(f.ch[chosen_ch], FRAME_LEN, clip.pcm + clip.n);
        if (clip.n >= CLIP_OUT_SAMPLES) {
            clip.event_id = cur_event_id;
            if (xQueueSend(q, &clip, 0) != pdTRUE)
                ESP_LOGW(TAG, "BLE queue full, dropping clip for event %u "
                             "(BLE task can't keep up)", cur_event_id);
            cooldown_left = COOLDOWN_FRAMES;
            state = EV_COOLDOWN;
        }
    }
}

static void ble_task(void *arg)
{
    static clip_t clip;
    while (1) {
        if (xQueueReceive(q, &clip, portMAX_DELAY) != pdTRUE) continue;
        if (!ble_svc_connected()) continue;
        ble_svc_send_audio(clip.event_id, clip.pcm, clip.n);
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

    q = xQueueCreate(2, sizeof(clip_t));
    motor_init();
    resample_init();
    tdoa_init();
    ESP_ERROR_CHECK(audio_capture_init());

    // Set after measuring with all four mics bundled together (see
    // tdoa/calibration.py : measure_sync_offsets() / firmware/SYNC_CHECK.md).
    // This corrects tau, not raw PCM -- see tdoa.c for why.
    tdoa_set_bus_skew_samples(0);

    ble_svc_init();
    ble_svc_set_cmd_cb(on_cmd);

    xTaskCreatePinnedToCore(capture_task, "cap", 8192, NULL, 6, NULL, 1);
    xTaskCreatePinnedToCore(ble_task,     "ble", 4096, NULL, 4, NULL, 0);
    ESP_LOGI(TAG, "running");
}
