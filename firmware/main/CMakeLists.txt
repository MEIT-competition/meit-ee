idf_component_register(
    SRCS "main.c" "ble_svc.c" "motor.c" "cmd_parse.c"
    INCLUDE_DIRS "."
    REQUIRES driver esp_timer bt nvs_flash)
