# ESP-IDF component for native/idf. PlatformIO's Arduino build reads library.json instead.
idf_component_register(SRCS "src/esp32.cpp" INCLUDE_DIRS "include"
                       REQUIRES core esp_timer esp_driver_ledc freertos)
