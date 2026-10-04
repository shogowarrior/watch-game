cmake_minimum_required(VERSION 3.16.0)
# The shared C++ core and ESP32 layer are ESP-IDF components next to this project.
# Append: PlatformIO passes src/ in EXTRA_COMPONENT_DIRS.
list(APPEND EXTRA_COMPONENT_DIRS ${CMAKE_CURRENT_LIST_DIR}/../core ${CMAKE_CURRENT_LIST_DIR}/../esp32_shared)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(sf_bench_idf)
