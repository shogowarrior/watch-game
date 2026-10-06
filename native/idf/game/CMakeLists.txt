cmake_minimum_required(VERSION 3.16.0)
# The shared core, the ESP32 layer and native/idf's components.
list(APPEND EXTRA_COMPONENT_DIRS ${CMAKE_CURRENT_LIST_DIR}/../../core ${CMAKE_CURRENT_LIST_DIR}/../../esp32_shared
     ${CMAKE_CURRENT_LIST_DIR}/../components)
# native/idf's settings, then the game's.
set(SDKCONFIG_DEFAULTS ${CMAKE_CURRENT_LIST_DIR}/../sdkconfig.defaults ${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(homing_game_idf)
