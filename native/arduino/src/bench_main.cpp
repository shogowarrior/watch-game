// Display and motion-sensor benchmark on Arduino-ESP32, one env per graphics
// library (bench_env.h). Steps and log lines are the shared ones in native/core (hm::Bench).
#include <Arduino.h>

#include "bench_env.h"
#include "board_pins.h"
#include "framework.h"
#include "hm/esp32.h"
#include "wire_i2c.h"

namespace {

char framework[96];
hm::esp::EspClock clock_;
WireI2c i2c0(Wire);
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
  if (!i2c0.begin(pins::I2C0_SDA, pins::I2C0_SCL, pins::I2C_HZ)) {
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
