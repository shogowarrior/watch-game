// The bench through Arduino_GFX's ESP32 DMA bus. Its writes are blocking polling
// transactions, so a strip cannot be drawn while the previous one is on the wire.
#include <Arduino_GFX_Library.h>

#include "bench_env.h"
#include "hal/spi_ll.h"

namespace {

class ArduinoGfxBus : public ArduinoLcd {
 public:
  bool begin(uint32_t hz) override {
    digitalWrite(5, LOW);   // CS: the panel is the bus's only device
    pinMode(5, OUTPUT);
    if (!bus_.begin(hz)) return false;
    bus_.beginWrite();
    return true;
  }
  bool set_clock(uint32_t hz) override {
    // Arduino_GFX takes the clock once, in begin(). The IDF driver leaves the clock
    // register alone while its device is the bus's only one, so set it there.
    spi_ll_master_set_clock(&SPI2, APB_CLK_FREQ, hz, 128);
    return true;
  }
  void command(uint8_t cmd, const uint8_t* d, size_t n) override {
    bus_.writeCommand(cmd);
    if (n) bus_.writeBytes(const_cast<uint8_t*>(d), n);
  }
  void pixels(uint8_t cmd, const uint16_t* px, size_t count) override {
    bus_.writeCommand(cmd);
    bus_.writeBytes((uint8_t*)px, count * 2);   // byte-swapped already; returns once sent
  }
  void wait() override {}

 private:
  // DC 27, no CS, SCK 18, MOSI 19, no MISO (HSPI's default is GPIO12, the backlight)
  Arduino_ESP32SPIDMA bus_{27, GFX_NOT_DEFINED, 18, 19, GFX_NOT_DEFINED, HSPI, false};
};

}  // namespace

BenchEnv& bench_env() {
  static ArduinoGfxBus lcd;
  static BenchEnv env{"arduino-arduino_gfx", "arduino_gfx_" ARDUINO_GFX_VERSION, lcd, nullptr};
  return env;
}
