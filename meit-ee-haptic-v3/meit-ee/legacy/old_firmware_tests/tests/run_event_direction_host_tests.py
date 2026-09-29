"""Compile the real main.c against deterministic FreeRTOS/ESP-IDF/BLE/TDoA/
audio-capture/motor stand-ins, and run the on_cmd() fail-safe test cases.

Run: python run_event_direction_host_tests.py --cc gcc
Or:  python run_event_direction_host_tests.py --cc /path/to/zig --zig
--source may select an unmodified main.c for before/after comparison, the
same way run_motor_host_tests.py's --source does for motor.c.
No ESP-IDF, board, or Python third-party packages are required.

This mirrors run_motor_host_tests.py's approach (real production source,
compiled against minimal stand-in headers, in a throwaway temp dir) applied
to main.c's on_cmd()/event table and capture/vote/collect logic. The real
audio_downmix() is linked; acquisition, TDoA and resampling have deterministic
stand-ins here. Their actual implementations are covered by the audio suite.
"""
import argparse
import pathlib
import subprocess
import tempfile

STUB = r'''
#ifndef HOST_STUB_H
#define HOST_STUB_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdarg.h>

typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL (-1)
#define ESP_ERR_NVS_NO_FREE_PAGES 0x1101
#define ESP_ERR_NVS_NEW_VERSION_FOUND 0x1102
#define ESP_ERROR_CHECK(x) do { (void)(x); } while (0)

typedef int BaseType_t;
typedef uint32_t TickType_t;
typedef void *QueueHandle_t;
typedef void *TaskHandle_t;
#define pdTRUE 1
#define pdPASS 1
#define pdMS_TO_TICKS(ms) (ms)
void vTaskDelay(TickType_t ticks);
#define portMAX_DELAY UINT32_MAX

typedef struct { int dummy; } portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED { 0 }
#define portENTER_CRITICAL(mux) ((void)(mux))
#define portEXIT_CRITICAL(mux)  ((void)(mux))

// Unused in translation units that never log (e.g. audio_samples.c); the
// attribute keeps gcc -Wall -Werror from rejecting them there.
__attribute__((unused)) static int log_w_count = 0, log_e_count = 0;
__attribute__((unused)) static char last_w_msg[256] = {0}, last_e_msg[256] = {0};
static inline void meit_test_vlog(char *dst, size_t n, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(dst, n, fmt, ap);
    va_end(ap);
}
#define ESP_LOGI(tag, ...) ((void)(tag))
#define ESP_LOGW(tag, ...) do { \
    log_w_count++; \
    meit_test_vlog(last_w_msg, sizeof last_w_msg, __VA_ARGS__); \
} while (0)
#define ESP_LOGE(tag, ...) do { \
    log_e_count++; \
    meit_test_vlog(last_e_msg, sizeof last_e_msg, __VA_ARGS__); \
} while (0)

QueueHandle_t xQueueCreate(int n, size_t item_size);
#define MALLOC_CAP_8BIT   (1u << 2)
#define MALLOC_CAP_SPIRAM (1u << 10)
QueueHandle_t xQueueCreateWithCaps(int n, size_t item_size, uint32_t caps);
void *heap_caps_calloc(size_t n, size_t size, uint32_t caps);
int xQueueSend(QueueHandle_t q, const void *item, TickType_t wait);
int xQueueReceive(QueueHandle_t q, void *item, TickType_t wait);
BaseType_t xTaskCreatePinnedToCore(void (*fn)(void *), const char *name,
                                   uint32_t stack, void *arg, int prio,
                                   TaskHandle_t *out, int core);
esp_err_t nvs_flash_init(void);
esp_err_t nvs_flash_erase(void);

#endif
'''

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cc', default='cc')
    p.add_argument('--zig', action='store_true')
    p.add_argument('--source', type=pathlib.Path)
    args = p.parse_args()
    here = pathlib.Path(__file__).resolve().parent
    main_dir = here.parent / 'main'
    source = (args.source or main_dir / 'main.c').resolve()
    result = 0
    with tempfile.TemporaryDirectory(prefix='meit-eventdir-') as tmp:
        d = pathlib.Path(tmp)
        (d / 'host_stub.h').write_text(STUB)
        for header in ['freertos/FreeRTOS.h', 'freertos/task.h',
                       'freertos/queue.h', 'freertos/idf_additions.h',
                       'esp_heap_caps.h', 'esp_log.h', 'esp_err.h',
                       'nvs_flash.h']:
            f = d / header
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text('#include "host_stub.h"\n')
        # config.h / audio_capture.h / tdoa.h / resample.h / ble_svc.h /
        # motor.h are the real, unmodified production headers (found via
        # -I main_dir below). Only main.c itself is copied, to name it
        # main_under_test.c for the #include in event_direction_host_test.c.
        (d / 'main_under_test.c').write_bytes(source.read_bytes())
        exe = d / ('event_direction_tests.exe'
                   if __import__('os').name == 'nt' else 'event_direction_tests')
        command = [args.cc] + (['cc'] if args.zig else [])
        command += ['-std=c11', '-O0', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-D_GNU_SOURCE',
                    '-I', str(d), '-I', str(main_dir),
                    str(here / 'event_direction_host_test.c'),
                    str(main_dir / 'audio_samples.c'),
                    '-o', str(exe), '-lm']
        subprocess.run(command, check=True)
        print(f'source: {source}', flush=True)
        for index in range(-1, 3):
            run = subprocess.run([str(exe), str(index)], timeout=30)
            if run.returncode:
                print(f'event_direction_host_test: exit={run.returncode}', flush=True)
                result = 1
    return result

if __name__ == '__main__':
    raise SystemExit(main())
