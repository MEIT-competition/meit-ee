"""Compile the real motor.c against deterministic RTOS/timer/LEDC stubs.

Run: python run_motor_host_tests.py --cc gcc
Or:  python run_motor_host_tests.py --cc /path/to/zig --zig
--source may select an unmodified motor.c for before/after comparison.
No ESP-IDF, board, or Python third-party packages are required.
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
#include <stdlib.h>
typedef int esp_err_t;
typedef int BaseType_t;
typedef uint32_t TickType_t;
typedef void *QueueHandle_t;
typedef void *TaskHandle_t;
typedef void *esp_timer_handle_t;
#define ESP_OK 0
#define ESP_ERR_INVALID_STATE 1
#define pdTRUE 1
#define pdPASS 1
#define portMAX_DELAY UINT32_MAX
#ifndef configTICK_RATE_HZ
#define configTICK_RATE_HZ 100
#endif
#define pdMS_TO_TICKS(ms) ((ms) * configTICK_RATE_HZ / 1000)
#define ESP_ERROR_CHECK(x) do { if ((x) != ESP_OK) abort(); } while (0)
#define ESP_LOGI(tag,...) ((void)(tag))
#define ESP_LOGW(tag,...) ((void)(tag))
#define ESP_LOGE(tag,...) ((void)(tag))
#define LEDC_LOW_SPEED_MODE 0
#define LEDC_TIMER_8_BIT 8
#define LEDC_TIMER_0 0
#define LEDC_AUTO_CLK 0
typedef int ledc_channel_t;
typedef struct { int speed_mode,duty_resolution,timer_num,freq_hz,clk_cfg; } ledc_timer_config_t;
typedef struct { int gpio_num,speed_mode,channel,timer_sel,duty,hpoint; } ledc_channel_config_t;
typedef struct { void (*callback)(void *); const char *name; } esp_timer_create_args_t;
QueueHandle_t xQueueCreate(int n, size_t size);
int xQueueSend(QueueHandle_t q, const void *m, TickType_t wait);
int xQueueReceive(QueueHandle_t q, void *m, TickType_t wait);
int xTaskCreate(void (*fn)(void *),const char *name,int stack,void *arg,int priority,TaskHandle_t *out);
int ledc_timer_config(const ledc_timer_config_t *t);
int ledc_channel_config(const ledc_channel_config_t *c);
int ledc_set_duty(int mode,int channel,uint32_t duty);
int ledc_update_duty(int mode,int channel);
int esp_timer_create(const esp_timer_create_args_t *a,esp_timer_handle_t *out);
int esp_timer_start_once(esp_timer_handle_t timer,uint64_t us);
int esp_timer_stop(esp_timer_handle_t timer);
int64_t esp_timer_get_time(void);
#endif
'''

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cc', default='cc')
    p.add_argument('--zig', action='store_true')
    p.add_argument('--source', type=pathlib.Path)
    args = p.parse_args()
    here = pathlib.Path(__file__).resolve().parent
    source = (args.source or here.parent / 'main' / 'motor.c').resolve()
    result = 0
    with tempfile.TemporaryDirectory(prefix='meit-motor-') as tmp:
        d = pathlib.Path(tmp)
        (d / 'host_stub.h').write_text(STUB)
        for header in ['freertos/FreeRTOS.h', 'freertos/task.h', 'freertos/queue.h',
                       'driver/ledc.h', 'driver/gpio.h', 'esp_timer.h', 'esp_log.h']:
            f = d / header
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text('#include "host_stub.h"\n')
        # motor.h/config.h are the real, unmodified production headers.
        (d / 'motor_under_test.c').write_bytes(source.read_bytes())
        for hz in (100, 1000):
            exe = d / ('motor_tests.exe' if __import__('os').name == 'nt' else 'motor_tests')
            command = [args.cc] + (['cc'] if args.zig else [])
            command += ['-std=c11', '-O0', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', f'-DconfigTICK_RATE_HZ={hz}',
                        '-I', str(d), '-I', str(here.parent / 'main'),
                        str(here / 'motor_host_test.c'), '-o', str(exe)]
            subprocess.run(command, check=True)
            print(f'RTOS tick rate: {hz} Hz; source: {source}', flush=True)
            passed = 0
            for case in range(9):
                run = subprocess.run([str(exe), str(case)], timeout=30)
                passed += run.returncode == 0
                if run.returncode:
                    print(f'Case {case}: exit={run.returncode}', flush=True)
                    result = 1
            print(f'RESULT: {passed}/9 passed at {hz} Hz', flush=True)
    return result

if __name__ == '__main__':
    raise SystemExit(main())
