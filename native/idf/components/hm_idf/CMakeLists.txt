# ESP-IDF pieces the native/idf builds share. portable/ is plain C++ (host-tested).
file(GLOB srcs "${CMAKE_CURRENT_LIST_DIR}/src/*.cpp" "${CMAKE_CURRENT_LIST_DIR}/portable/*.cpp")
idf_component_register(SRCS ${srcs} INCLUDE_DIRS "include"
                       REQUIRES core esp_driver_i2c esp_driver_spi esp_driver_gpio esp_lcd esp_rom hal soc)
