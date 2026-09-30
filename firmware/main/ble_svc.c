// MEIT belt motor-only BLE GATT server.
// Service UUID and CMD UUID are kept identical to the old EE firmware so the
// Windows side does not need device-specific discovery logic.

#include <string.h>
#include <stdlib.h>
#include "esp_log.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "host/ble_uuid.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include "config.h"
#include "cmd_parse.h"
#include "ble_svc.h"

static const char *TAG = "ble";
static const char *DEVICE_NAME = "MEIT-BELT";

static const ble_uuid128_t SVC_UUID =
    BLE_UUID128_INIT(0x11,0x9d,0x2a,0x30,0x4e,0x9c,0x4b,0x9a,
                     0x8f,0x21,0x9e,0x1d,0x00,0x00,0x00,0x01);
static const ble_uuid128_t CHR_CMD_UUID =
    BLE_UUID128_INIT(0x11,0x9d,0x2a,0x30,0x4e,0x9c,0x4b,0x9a,
                     0x8f,0x21,0x9e,0x1d,0x00,0x00,0x00,0x04);

static uint16_t conn_handle = BLE_HS_CONN_HANDLE_NONE;
static ble_cmd_cb_t cmd_cb = NULL;

// GATT write handler. Byte-level validation lives in cmd_parse.c so it can be
// host-tested without ESP-IDF; this function is only transport.
static int cmd_write_cb(uint16_t ch, uint16_t vh,
                        struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)ch; (void)vh; (void)arg;
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR)
        return BLE_ATT_ERR_REQ_NOT_SUPPORTED;

    // Zero-initialised so a short packet can never expose stack bytes to the
    // parser, even though cmd_parse only reads inside the declared length.
    uint8_t buf[CMD_HEADER + 2 * PATTERN_MAX_STEPS] = {0};
    uint16_t len = OS_MBUF_PKTLEN(ctxt->om);
    if (len < CMD_HEADER || len > sizeof(buf))
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    if (ble_hs_mbuf_to_flat(ctxt->om, buf, len, NULL) != 0)
        return BLE_ATT_ERR_UNLIKELY;

    cmd_packet_t packet;
    switch (cmd_parse(buf, len, &packet)) {
    case CMD_PARSE_OK:
        break;
    case CMD_PARSE_BAD_LENGTH:
        ESP_LOGW(TAG, "reject CMD: bad length %u", (unsigned)len);
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    default:
        // len >= CMD_HEADER here, so buf[1] and buf[3] are always populated.
        ESP_LOGW(TAG, "reject CMD: malformed (len=%u ver=%02x dir=%u)",
                 (unsigned)len, (unsigned)buf[1], (unsigned)buf[3]);
        return BLE_ATT_ERR_UNLIKELY;
    }

    if (cmd_cb) {
        if (cmd_is_stop(&packet))
            cmd_cb(packet.sequence, packet.direction, 0, 0, NULL, 0);
        else
            cmd_cb(packet.sequence, packet.direction, packet.intensity,
                   packet.mask, packet.steps, packet.n_steps);
    }
    return 0;
}

static const struct ble_gatt_svc_def gatt_svcs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &SVC_UUID.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            { .uuid = &CHR_CMD_UUID.u, .access_cb = cmd_write_cb,
              .flags = BLE_GATT_CHR_F_WRITE },
            { 0 }
        },
    },
    { 0 }
};

static void start_advertising(void);
static uint8_t own_addr_type = BLE_OWN_ADDR_PUBLIC;

static int gap_event_cb(struct ble_gap_event *event, void *arg)
{
    (void)arg;
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
        conn_handle = BLE_HS_CONN_HANDLE_NONE;
        // Fail-safe: a dropped laptop connection must not leave a long
        // haptic pattern running. A normal command pattern is finite, but
        // stopping here makes disconnect behavior deterministic.
        if (cmd_cb) cmd_cb(0, CMD_DIR_STOP, 0, 0, NULL, 0);
        ESP_LOGI(TAG, "disconnected; motors stopped; advertising again");
        start_advertising();
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
    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "set advertising fields failed rc=%d", rc);
        return;
    }

    adv.conn_mode = BLE_GAP_CONN_MODE_UND;
    adv.disc_mode = BLE_GAP_DISC_MODE_GEN;
    rc = ble_gap_adv_start(own_addr_type, NULL, BLE_HS_FOREVER,
                           &adv, gap_event_cb, NULL);
    if (rc != 0) ESP_LOGE(TAG, "advertising failed rc=%d", rc);
}

static void on_sync(void)
{
    int rc = ble_hs_util_ensure_addr(0);
    if (rc == 0) rc = ble_hs_id_infer_auto(0, &own_addr_type);
    if (rc != 0) {
        ESP_LOGE(TAG, "BLE address init failed rc=%d", rc);
        return;
    }
    start_advertising();
}

static void host_task(void *param)
{
    (void)param;
    nimble_port_run();
    nimble_port_freertos_deinit();
}

void ble_svc_init(void)
{
    nimble_port_init();
    ble_hs_cfg.sync_cb = on_sync;
    ble_svc_gap_init();
    ble_svc_gatt_init();

    int rc = ble_svc_gap_device_name_set(DEVICE_NAME);
    if (rc != 0) {
        ESP_LOGE(TAG, "set GAP device name failed rc=%d", rc);
        abort();
    }
    rc = ble_gatts_count_cfg(gatt_svcs);
    if (rc != 0) {
        ESP_LOGE(TAG, "GATT count config failed rc=%d", rc);
        abort();
    }
    rc = ble_gatts_add_svcs(gatt_svcs);
    if (rc != 0) {
        ESP_LOGE(TAG, "GATT add services failed rc=%d", rc);
        abort();
    }

    nimble_port_freertos_init(host_task);
    ESP_LOGI(TAG, "motor-only BLE service starting as %s", DEVICE_NAME);
}

bool ble_svc_connected(void)
{
    return conn_handle != BLE_HS_CONN_HANDLE_NONE;
}

void ble_svc_set_cmd_cb(ble_cmd_cb_t cb)
{
    cmd_cb = cb;
}
