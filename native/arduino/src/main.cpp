// Display and motion-sensor benchmark on Arduino-ESP32 with LovyanGFX's SPI bus
// (DMA). Steps and log lines are the shared ones in native/core (hm::Bench).
#include <Arduino.h>
#include <Wire.h>
#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <esp_arduino_version.h>

#include "hm/bench.h"
#include "hm/esp32.h"

namespace {

// I2C0 (AXP202, BMA423) through Wire; reads stay within Wire's 128-byte buffer.
struct WireI2c : hm::I2c {
  bool write(uint8_t addr, uint8_t reg, const uint8_t* d, size_t n) override {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.write(d, n);
    return Wire.endTransmission() == 0;
  }
  bool read(uint8_t addr, uint8_t reg, uint8_t* d, size_t n) override {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom((uint16_t)addr, n, true) != n) return false;
    for (size_t i = 0; i < n; i++) d[i] = (uint8_t)Wire.read();
    return true;
  }
};

// The panel is the bus's only device, so CS stays low and one transaction stays open.
class LgfxBus : public hm::LcdBus {
 public:
  bool begin(uint32_t hz) {
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

char framework[80];
hm::esp::EspClock clock_;
WireI2c i2c0;
LgfxBus lcd;
hm::esp::CoreImuTask imu;
hm::BenchHost host{"arduino-lovyangfx", framework, clock_, lcd, i2c0, imu, hm::esp::backlight};
hm::Bench bench(host);
bool ready = false;

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);
  snprintf(framework, sizeof framework, "arduino-esp32_%d.%d.%d_idf_%s_lovyangfx_%d.%d.%d", ESP_ARDUINO_VERSION_MAJOR,
           ESP_ARDUINO_VERSION_MINOR, ESP_ARDUINO_VERSION_PATCH, esp_get_idf_version(), LGFX_VERSION_MAJOR,
           LGFX_VERSION_MINOR, LGFX_VERSION_PATCH);
  // Install the I2C driver from core 0 so its interrupt runs beside the sensor
  // task, not the renderer (as the ESP-IDF build does).
  static volatile bool wire_ok = false, wire_done = false;
  xTaskCreatePinnedToCore(
      [](void*) {
        wire_ok = Wire.begin(21, 22, 400000);
        wire_done = true;
        vTaskDelete(nullptr);
      },
      "hm_wire", 4096, nullptr, 5, nullptr, 0);
  while (!wire_done) delay(1);
  if (!wire_ok) {
    hm::logf("HM error what=i2c_bus");
    return;
  }
  if (!lcd.begin(hm::Bench::CLOCKS[0])) {
    hm::logf("HM error what=spi_bus");
    return;
  }
  ready = bench.setup();
  if (ready) bench.run();
}

void loop() {
  // After the run: the HOT field at 20, 30 and 60 fps in turn, 10 s each, for the eye.
  static const int targets[] = {20, 30, 60};
  static int i = 0;
  if (!ready) {
    delay(1000);
    return;
  }
  bench.show(hm::Bench::CLOCKS[1], targets[i++ % 3], 10);
}
