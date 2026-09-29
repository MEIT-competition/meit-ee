idf_component_register(
    SRCS "main.c" "audio_capture.c" "audio_samples.c" "tdoa.c" "resample.c" "ble_svc.c" "motor.c"
    INCLUDE_DIRS "."
    REQUIRES driver esp_timer bt nvs_flash esp_rom soc espressif__esp-dsp)
