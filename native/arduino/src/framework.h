// The "HM hello framework=" field: Arduino core, IDF and library versions.
#pragma once
#include <stddef.h>
#include <stdio.h>

#include <esp_arduino_version.h>
#include <esp_idf_version.h>

inline void framework_name(char* out, size_t n, const char* library) {
  snprintf(out, n, "arduino-esp32_%d.%d.%d_idf_%s_%s", ESP_ARDUINO_VERSION_MAJOR, ESP_ARDUINO_VERSION_MINOR,
           ESP_ARDUINO_VERSION_PATCH, esp_get_idf_version(), library);
}
