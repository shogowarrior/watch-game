# hmlcd: the ST7789 push from a core-0 task through internal DMA buffers, built
# from the shared C++ core (native/core) and ESP32 layer (native/esp32_shared).
set(HM_NATIVE ${CMAKE_CURRENT_LIST_DIR}/../..)
add_library(usermod_hmlcd INTERFACE)
target_sources(usermod_hmlcd INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/hmlcd.c
    ${CMAKE_CURRENT_LIST_DIR}/pusher.cpp
    ${HM_NATIVE}/core/src/st7789.cpp
    ${HM_NATIVE}/core/src/bounce_push.cpp
    ${HM_NATIVE}/esp32_shared/src/esp32.cpp
    ${HM_NATIVE}/esp32_shared/src/spi_lcd_bus.cpp
)
target_include_directories(usermod_hmlcd INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${HM_NATIVE}/core/include
    ${HM_NATIVE}/esp32_shared/include
)
# User modules build in the firmware's own target, outside any IDF component,
# so ESP_PLATFORM (which FreeRTOS.h needs for xTaskCreatePinnedToCore) is unset.
target_compile_definitions(usermod_hmlcd INTERFACE ESP_PLATFORM)
target_link_libraries(usermod INTERFACE usermod_hmlcd)
