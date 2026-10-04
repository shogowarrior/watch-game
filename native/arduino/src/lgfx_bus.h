// LovyanGFX's SPI bus with DMA, standalone (no Panel class, so the bench's own
// ST7789 init keeps the MicroPython look). Used by the LovyanGFX and LVGL envs.
#pragma once
#include <Arduino.h>
#define LGFX_USE_V1
#include <LovyanGFX.hpp>

#include "bench_env.h"

// The panel is the bus's only device, so CS stays low and one transaction stays open.
class LgfxBus : public ArduinoLcd {
 public:
  bool begin(uint32_t hz) override {
    auto c = bus_.config();
    c.spi_host = HSPI_HOST;
    c.spi_mode = 0;
    c.spi_3wire = true;
    c.use_lock = false;
    c.freq_write = hz;
    c.pin_sclk = 18;
    c.pin_mosi = 19;
    c.pin_miso = -1;   // HSPI's default MISO is GPIO12, the backlight
    c.pin_dc = 27;
    bus_.config(c);
    if (!bus_.init()) return false;
    pinMode(5, OUTPUT);
    digitalWrite(5, LOW);
    bus_.beginTransaction();
    return true;
  }
  bool set_clock(uint32_t hz) override {
    bus_.wait();
    bus_.endTransaction();
    bus_.setClock(hz);
    bus_.beginTransaction();
    return true;
  }
  void command(uint8_t cmd, const uint8_t* d, size_t n) override {
    bus_.writeCommand(cmd, 8);
    if (n) bus_.writeBytes(d, n, true, false);
    bus_.wait();
  }
  void pixels(uint8_t cmd, const uint16_t* px, size_t count) override {
    bus_.writeCommand(cmd, 8);   // waits for the previous burst
    bus_.writeBytes((const uint8_t*)px, count * 2, true, true);
  }
  void wait() override { bus_.wait(); }

 private:
  lgfx::Bus_SPI bus_;
};

#define HM_STR(x) HM_STR2(x)
#define HM_STR2(x) #x
// "lovyangfx_1.2.32"
#define HM_LGFX_LIBRARY \
  "lovyangfx_" HM_STR(LGFX_VERSION_MAJOR) "." HM_STR(LGFX_VERSION_MINOR) "." HM_STR(LGFX_VERSION_PATCH)
