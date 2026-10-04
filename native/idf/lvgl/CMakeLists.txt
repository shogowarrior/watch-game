cmake_minimum_required(VERSION 3.16.0)
# The shared core, the ESP32 layer and native/idf's components; LVGL and esp_lvgl_port
# come from the ESP component registry (src/idf_component.yml, pinned in dependencies.lock).
list(APPEND EXTRA_COMPONENT_DIRS ${CMAKE_CURRENT_LIST_DIR}/../../core ${CMAKE_CURRENT_LIST_DIR}/../../esp32_shared
     ${CMAKE_CURRENT_LIST_DIR}/../components)
# native/idf's settings, then the LVGL ones.
set(SDKCONFIG_DEFAULTS ${CMAKE_CURRENT_LIST_DIR}/../sdkconfig.defaults ${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(homing_bench_lvgl)
