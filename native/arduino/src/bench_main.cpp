// Display and motion-sensor benchmark on Arduino-ESP32, one env per graphics
// library (bench_env.h). Steps and log lines are the shared ones in native/core (hm::Bench).
#include <Arduino.h>
#include <Wire.h>

#include "bench_env.h"
#include "framework.h"
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

char framework[96];
hm::esp::EspClock clock_;
WireI2c i2c0;
hm::esp::CoreImuTask imu;
bool ready = false;

hm::Bench& bench() {
  BenchEnv& env = bench_env();
  static hm::BenchHost host{env.variant, framework, clock_, env.lcd, i2c0, imu, hm::esp::backlight, env.drawer};
  static hm::Bench b(host);
  return b;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);
  framework_name(framework, sizeof framework, bench_env().library);
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
  if (!bench_env().lcd.begin(hm::Bench::CLOCKS[0])) {
    hm::logf("HM error what=spi_bus");
    return;
  }
  ready = bench().setup();
  if (ready) bench().run();
}

void loop() {
  // After the run: the HOT field at 20, 30 and 60 fps in turn, 10 s each, for the eye.
  static const int targets[] = {20, 30, 60};
  static int i = 0;
  if (!ready) {
    delay(1000);
    return;
  }
  bench().show(hm::Bench::CLOCKS[1], targets[i++ % 3], 10);
}
