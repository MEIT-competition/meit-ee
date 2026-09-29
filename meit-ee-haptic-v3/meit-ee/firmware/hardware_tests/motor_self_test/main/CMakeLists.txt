idf_component_register(
    SRCS "motor_self_test_main.c" "../../../main/motor.c"
    INCLUDE_DIRS "." "../../../main"
    REQUIRES driver esp_timer)
