// NimBLE GATT server: one custom service, 3 characteristics.
// AUDIO (notify), DIR (notify), CMD (write).
//
// UNTESTED: written against the ESP-IDF 5.x NimBLE host API pattern, but not
// run on hardware. NimBLE's exact API has shifted across ESP-IDF versions
// before -- if this doesn't compile against your installed IDF version,
// diff against `idf.py create-project` --> examples/bluetooth/nimble/blehr,
// which uses the same structure.

#include <string.h>
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_nimble_hci.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/util/util.h"   // ble_hs_util_ensure_addr -- UNVERIFIED header
                              // path across ESP-IDF/NimBLE versions, same
                              // caveat as the ble_att.h include below.
#include "host/ble_att.h"   // ble_att_set_preferred_mtu / ble_att_mtu --
                           // UNVERIFIED header path across ESP-IDF versions,
                           // some pull these in via host/ble_hs.h instead.
#include "host/ble_uuid.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include "config.h"
#include "ble_svc.h"

static const char *TAG = "ble";
static const char *DEVICE_NAME = "MEIT-BELT";

// Random 128-bit UUIDs. Generate your own before shipping past the MVP.
static const ble_uuid128_t SVC_UUID =
    BLE_UUID128_INIT(0x11,0x9d,0x2a,0x30,0x4e,0x9c,0x4b,0x9a,
                     0x8f,0x21,0x9e,0x1d,0x00,0x00,0x00,0x01);
static const ble_uuid128_t CHR_AUDIO_UUID =
    BLE_UUID128_INIT(0x11,0x9d,0x2a,0x30,0x4e,0x9c,0x4b,0x9a,
                     0x8f,0x21,0x9e,0x1d,0x00,0x00,0x00,0x02);
static const ble_uuid128_t CHR_DIR_UUID =
    BLE_UUID128_INIT(0x11,0x9d,0x2a,0x30,0x4e,0x9c,0x4b,0x9a,
                     0x8f,0x21,0x9e,0x1d,0x00,0x00,0x00,0x03);
static const ble_uuid128_t CHR_CMD_UUID =
    BLE_UUID128_INIT(0x11,0x9d,0x2a,0x30,0x4e,0x9c,0x4b,0x9a,
                     0x8f,0x21,0x9e,0x1d,0x00,0x00,0x00,0x04);

static uint16_t conn_handle = BLE_HS_CONN_HANDLE_NONE;
static uint16_t audio_val_handle, dir_val_handle;
static ble_cmd_cb_t cmd_cb = NULL;
static SemaphoreHandle_t audio_tx_sem;   // created in ble_svc_init()

static int cmd_write_cb(uint16_t ch, uint16_t vh, struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR)
        return BLE_ATT_ERR_REQ_NOT_SUPPORTED;

    uint8_t buf[4 + 2 * PATTERN_MAX_PAIRS] = {0};
    uint16_t len = OS_MBUF_PKTLEN(ctxt->om);
    if (len > sizeof(buf)) {
        ESP_LOGW(TAG, "CMD too long (%u bytes, max %u)", len, (unsigned)sizeof(buf));
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    if (ble_hs_mbuf_to_flat(ctxt->om, buf, len, NULL) != 0) {
        ESP_LOGW(TAG, "CMD mbuf flatten failed (%u bytes)", len);
        return BLE_ATT_ERR_UNLIKELY;
    }

    // Smallest valid packet is 4-byte header + one {on,off} pair = 6 bytes.
    if (len < 6) {
        ESP_LOGW(TAG, "CMD too short (%u bytes)", len);
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    uint8_t event_id = buf[0], intensity = buf[1], sound_class = buf[2], n = buf[3];
    uint16_t expected_len = (uint16_t)(4 + 2 * n);
    if (n < 1 || n > PATTERN_MAX_PAIRS || expected_len != len) {
        ESP_LOGW(TAG, "CMD bad n_pairs=%u for len=%u (expected=%u)",
                 n, len, expected_len);
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    motor_step_t steps[PATTERN_MAX_PAIRS];
    for (int i = 0; i < n; i++) {
        steps[i].on_ms  = (uint16_t)buf[4 + 2 * i] * 10;
        steps[i].off_ms = (uint16_t)buf[4 + 2 * i + 1] * 10;
    }
    if (cmd_cb) cmd_cb(event_id, intensity, sound_class, steps, n);
    return 0;
}

static int chr_access_cb(uint16_t ch, uint16_t vh, struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    // AUDIO/DIR are notify-only; nothing to serve on a read/write here.
    return 0;
}

static const struct ble_gatt_svc_def gatt_svcs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &SVC_UUID.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            { .uuid = &CHR_AUDIO_UUID.u, .access_cb = chr_access_cb,
              .val_handle = &audio_val_handle, .flags = BLE_GATT_CHR_F_NOTIFY },
            { .uuid = &CHR_DIR_UUID.u, .access_cb = chr_access_cb,
              .val_handle = &dir_val_handle, .flags = BLE_GATT_CHR_F_NOTIFY },
            { .uuid = &CHR_CMD_UUID.u, .access_cb = cmd_write_cb,
              .flags = BLE_GATT_CHR_F_WRITE },
            { 0 }
        },
    },
    { 0 }
};

static void start_advertising(void);
static uint8_t own_addr_type = BLE_OWN_ADDR_PUBLIC;   // overwritten in on_sync()

static int gap_event_cb(struct ble_gap_event *event, void *arg)
{
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status == 0) {
            conn_handle = event->connect.conn_handle;
            ESP_LOGI(TAG, "connected");
        } else {
            start_advertising();
        }
        return 0;
    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI(TAG, "disconnected, re-advertising");
        conn_handle = BLE_HS_CONN_HANDLE_NONE;
        start_advertising();
        return 0;
    case BLE_GAP_EVENT_NOTIFY_TX:
        // Only used to pace ble_svc_send_audio()'s chunk loop -- see there.
        // Filtered to the AUDIO characteristic so a DIR notify (sent from a
        // different task) can't be mistaken for an AUDIO chunk completing.
        // Simplification: this unblocks the next chunk regardless of
        // event->notify_tx.status, i.e. it treats "the stack processed
        // this" the same as "the peer actually got it". Good enough to
        // stop outrunning NimBLE's own buffers; a dropped connection mid-
        // clip is instead caught by notify()'s own conn_handle check on
        // the next chunk, not by inspecting status here.
        if (event->notify_tx.attr_handle == audio_val_handle)
            xSemaphoreGive(audio_tx_sem);
        return 0;
    default:
        return 0;
    }
}

static void start_advertising(void)
{
    struct ble_gap_adv_params adv = {0};
    struct ble_hs_adv_fields fields = {0};
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.name = (uint8_t *)DEVICE_NAME;
    fields.name_len = strlen(DEVICE_NAME);
    fields.name_is_complete = 1;
    ble_gap_adv_set_fields(&fields);

    adv.conn_mode = BLE_GAP_CONN_MODE_UND;
    adv.disc_mode = BLE_GAP_DISC_MODE_GEN;
    ble_gap_adv_start(own_addr_type, NULL, BLE_HS_FOREVER, &adv, gap_event_cb, NULL);
}

static void on_sync(void)
{
    // Official NimBLE peripheral examples call ble_hs_util_ensure_addr(0)
    // before ble_hs_id_infer_auto() -- it makes sure a usable identity
    // address (a random static one, if the chip has no public address
    // burned in) actually exists first. Skipping straight to infer_auto()
    // risks it having nothing valid to infer from.
    int rc = ble_hs_util_ensure_addr(0);
    if (rc != 0) {
        ESP_LOGE(TAG, "ble_hs_util_ensure_addr failed rc=%d -- not advertising", rc);
        return;
    }

    rc = ble_hs_id_infer_auto(0, &own_addr_type);
    if (rc != 0) {
        ESP_LOGE(TAG, "ble_hs_id_infer_auto failed rc=%d -- not advertising", rc);
        return;
    }
    start_advertising();
}

static void host_task(void *param)
{
    nimble_port_run();
    nimble_port_freertos_deinit();
}

void ble_svc_init(void)
{
    audio_tx_sem = xSemaphoreCreateBinary();
    xSemaphoreGive(audio_tx_sem);   // first chunk may proceed immediately

    nimble_port_init();
    ble_hs_cfg.sync_cb = on_sync;

    ble_svc_gap_init();
    ble_svc_gatt_init();
    ble_svc_gap_device_name_set(DEVICE_NAME);

    // Ask for a larger MTU so audio chunks don't shrink to the default
    // 20-byte payload. If negotiation fails or the peer doesn't ask, this
    // is only a request -- ble_svc_send_audio() reads back whatever MTU
    // actually got negotiated via ble_att_mtu() and sizes chunks to that.
    ble_att_set_preferred_mtu(247);

    ble_gatts_count_cfg(gatt_svcs);
    ble_gatts_add_svcs(gatt_svcs);

    nimble_port_freertos_init(host_task);
    ESP_LOGI(TAG, "BLE service starting");
}

bool ble_svc_connected(void) { return conn_handle != BLE_HS_CONN_HANDLE_NONE; }

void ble_svc_set_cmd_cb(ble_cmd_cb_t cb) { cmd_cb = cb; }

static int notify(uint16_t val_handle, const uint8_t *data, uint16_t len)
{
    if (conn_handle == BLE_HS_CONN_HANDLE_NONE) return -1;
    struct os_mbuf *om = ble_hs_mbuf_from_flat(data, len);
    if (!om) return -1;
    return ble_gatts_notify_custom(conn_handle, val_handle, om);
}

// A 0.5s @16kHz PCM16 clip is ~16KB -- far larger than any single ATT
// notification (MTU-3 bytes, a few hundred at best even fully negotiated).
// The old code tried to notify() the whole clip in one call, which the BLE
// stack cannot do; it would fail or silently truncate. This chunks it with
// a small header {event_id, chunk_index, flags} so the laptop-side receiver
// can reassemble in order and know when the clip is complete.
//
// Reliability, addressed two ways:
//  1. PACING -- wait for the PREVIOUS chunk's BLE_GAP_EVENT_NOTIFY_TX before
//     queueing the next one, instead of firing ~70 notify() calls back to
//     back. NimBLE has a limited pool of buffers for outgoing ATT data;
//     racing ahead of what it can actually drain is what was producing the
//     "notify queue full" failures this was already working around, not a
//     fundamental BLE limit.
//  2. RETRY -- a chunk that still fails gets up to 3 attempts with
//     increasing backoff before being skipped. A skipped chunk leaves a
//     ~10ms gap in that one clip rather than losing the whole clip, which
//     seemed the better trade for Recall -- but this is a judgment call,
//     not a settled one; if the team would rather discard the whole event
//     on any chunk failure than risk a classifier seeing gapped audio,
//     that's a reasonable alternative. Flag for discussion.
int ble_svc_send_audio(uint8_t event_id, const int16_t *pcm, int n)
{
    if (conn_handle == BLE_HS_CONN_HANDLE_NONE) return -1;
    int64_t t0_us = esp_timer_get_time();

    uint16_t mtu = ble_att_mtu(conn_handle);
    if (mtu < 23) mtu = 23;
    int payload_budget = (int)mtu - 3 - 3;      // ATT overhead(3) + our header(3)
    if (payload_budget > BLE_AUDIO_MAX_PAYLOAD) payload_budget = BLE_AUDIO_MAX_PAYLOAD;
    int max_samples = payload_budget / (int)sizeof(int16_t);
    if (max_samples < 1) max_samples = 1;

    uint8_t buf[3 + BLE_AUDIO_MAX_PAYLOAD];
    int sent = 0, chunk_idx = 0, failures = 0;
    while (sent < n) {
        int this_n = n - sent;
        if (this_n > max_samples) this_n = max_samples;
        buf[0] = event_id;
        buf[1] = (uint8_t)chunk_idx;
        buf[2] = (uint8_t)((sent + this_n >= n) ? 1 : 0);   // bit0: last chunk
        memcpy(buf + 3, pcm + sent, (size_t)this_n * sizeof(int16_t));
        uint16_t len = (uint16_t)(3 + this_n * (int)sizeof(int16_t));

        // Wait for the previous chunk to actually clear the stack before
        // adding another. Bounded so a missed/mis-filtered TX event can't
        // wedge this task forever -- proceeds anyway after the timeout,
        // just without the pacing benefit for that one chunk.
        xSemaphoreTake(audio_tx_sem, pdMS_TO_TICKS(200));

        int rc = -1;
        for (int attempt = 0; attempt < 3 && rc != 0; attempt++) {
            if (attempt > 0) vTaskDelay(pdMS_TO_TICKS(5 * attempt));   // 5ms, 10ms
            rc = notify(audio_val_handle, buf, len);
        }
        if (rc != 0) {
            failures++;
            ESP_LOGW(TAG, "audio chunk %d dropped after retries (event %u)",
                    chunk_idx, event_id);
            xSemaphoreGive(audio_tx_sem);   // no TX event will come for a
                                           // chunk that was never queued;
                                           // don't stall the NEXT chunk on it
        }
        sent += this_n;
        chunk_idx++;
    }

    // Reviewer explicitly asked these three be logged and checked on real
    // hardware, since none of them are guaranteed by ESP32-S3 supporting
    // BLE at all -- the laptop's own BLE stack, MTU, and connection
    // interval can just as easily be the bottleneck: negotiated MTU,
    // total send time for one clip, and how many chunks needed a retry.
    int64_t elapsed_ms = (esp_timer_get_time() - t0_us) / 1000;
    ESP_LOGI(TAG, "event %u: sent %d chunks (%d failed) in %lld ms, mtu=%u",
            event_id, chunk_idx, failures, (long long)elapsed_ms, mtu);
    return failures;   // 0 = all chunks sent
}

int ble_svc_send_direction(uint8_t event_id, uint8_t dir_byte,
                           float confidence, float rms_dbfs)
{
    uint8_t pkt[4];
    pkt[0] = event_id;
    pkt[1] = dir_byte;
    pkt[2] = (uint8_t)(confidence * 255.0f);
    int r = (int)rms_dbfs;
    pkt[3] = (uint8_t)(r < -128 ? -128 : (r > 127 ? 127 : r));
    return notify(dir_val_handle, pkt, sizeof(pkt));
}
