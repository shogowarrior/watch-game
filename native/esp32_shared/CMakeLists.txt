# ESP-IDF component for native/idf. PlatformIO's Arduino build reads library.json instead.
idf_component_register(SRCS "src/esp32.cpp" "src/esp32_io.cpp" "src/spi_lcd_bus.cpp" "src/esp32_game.cpp" INCLUDE_DIRS "include"
                       REQUIRES core esp_timer esp_driver_ledc esp_driver_spi esp_driver_gpio freertos esp_wifi esp_event
                                esp_netif)
