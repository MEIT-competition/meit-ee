cmake_minimum_required(VERSION 3.16)

set(IDF_TARGET "esp32s3" CACHE STRING "IDF target")
set(SDKCONFIG_DEFAULTS "${CMAKE_CURRENT_LIST_DIR}/../../sdkconfig.defaults")
set(COMPONENTS "main")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(meit_dual_i2s_sync_test)
