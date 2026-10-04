// What each Arduino env brings to bench_main.cpp: the display bus behind one
// graphics library and, for LVGL, its own frame drawer. Defined once per env,
// in env_<library>.cpp (platformio.ini picks the file).
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "sf/bench.h"

// sf::LcdBus plus bring-up: the SPI bus on HSPI at hz, the panel selected.
struct ArduinoLcd : sf::LcdBus {
  virtual bool begin(uint32_t hz) = 0;
};

struct BenchEnv {
  const char* variant;       // "SF hello variant=", e.g. "arduino-tft_espi"
  const char* library;       // name and version, e.g. "tft_espi_2.5.43"
  ArduinoLcd& lcd;
  sf::FrameDrawer* drawer;   // nullptr: the bench's own strip loop
};

BenchEnv& bench_env();
