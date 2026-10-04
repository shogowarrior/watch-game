// Arduino platform layer check: the devices on both I2C buses, the interrupt lines,
// one 100 ms motor buzz, then the 8 s loop watchdog: the loop stops feeding it and
// the watch must reboot. After that one reboot the check reports it and ends.
#include <Arduino.h>

#include "board_pins.h"
#include "framework.h"
#include "hm/axp202.h"
#include "hm/esp32_io.h"
#include "task_watchdog.h"
#include "wire_i2c.h"

namespace {

constexpr uint32_t WATCHDOG_S = 8;   // main.py: app.run(..., watchdog_ms=8000)
constexpr uint32_t FEED_MS = 3000;   // fed this long first, then starved
constexpr uint32_t BUZZ_MS = 100;    // one pulse, above the 60 ms floor (ui-spec)

WireI2c i2c0(Wire), i2c1(Wire1);
uint32_t fed_since = 0;
bool starving = false, ended = false;

// "HM i2c bus=N found=.. missing=..": every address that answers, and which of
// the board's devices did not (the BMA423 answers at one of two addresses).
void scan(int bus, WireI2c& i2c, const uint8_t* want, int n_want) {
  char found[96] = "", missing[48] = "";
  size_t f = 0, m = 0;
  for (uint8_t a = 0x08; a < 0x78; a++)
    if (i2c.probe(a)) f += snprintf(found + f, sizeof found - f, "%s0x%02x", f ? "," : "", a);
  for (int i = 0; i < n_want; i++) {
    const bool bma = want[i] == pins::BMA423_ADDR;
    if (!i2c.probe(want[i]) && !(bma && i2c.probe(pins::BMA423_ADDR_ALT)))
      m += snprintf(missing + m, sizeof missing - m, "%s0x%02x", m ? "," : "", want[i]);
  }
  hm::logf("HM i2c bus=%d found=%s missing=%s", bus, f ? found : "none", m ? missing : "none");
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);
  char framework[96];
  framework_name(framework, sizeof framework, "platform");
  hm::logf("HM hello variant=arduino-platform framework=%s", framework);
  if (task_watchdog::fired()) {
    hm::logf("HM watchdog rebooted=1 reset_reason=%d", task_watchdog::reset_reason());
    hm::logf("HM done");
    ended = true;
    return;
  }
  const bool bus0 = i2c0.begin(pins::I2C0_SDA, pins::I2C0_SCL, pins::I2C_HZ);
  const bool bus1 = i2c1.begin(pins::I2C1_SDA, pins::I2C1_SCL, pins::I2C_HZ);
  if (!bus0 || !bus1) hm::logf("HM error what=i2c_bus bus0=%d bus1=%d", bus0, bus1);
  if (bus0) {
    hm::logf("HM axp202 panel_power=%d", hm::axp202::panel_power_on(i2c0));   // as Board: the PMU before touch
    static const uint8_t want0[] = {pins::AXP202_ADDR, pins::BMA423_ADDR, pins::RTC_ADDR};
    scan(0, i2c0, want0, sizeof want0);
  }
  if (bus1) {
    static const uint8_t want1[] = {pins::TOUCH_ADDR};
    scan(1, i2c1, want1, sizeof want1);
  }
  using hm::esp::Line;
  const bool lines = hm::esp::lines_begin();
  hm::logf("HM lines ok=%d axp202=%d touch=%d bma423=%d", lines, hm::esp::asserted(Line::AXP202),
           hm::esp::asserted(Line::TOUCH), hm::esp::asserted(Line::BMA423));   // 1: asserted (pulled low)
  static hm::esp::Motor motor;
  const bool buzz = motor.begin();
  if (buzz) {
    motor.set(1);
    delay(BUZZ_MS);
    motor.set(0);
  }
  hm::logf("HM motor ok=%d buzz_ms=%u", buzz, (unsigned)BUZZ_MS);
  if (!task_watchdog::start(WATCHDOG_S)) {
    hm::logf("HM error what=watchdog");
    ended = true;
    return;
  }
  hm::logf("HM watchdog timeout_s=%u feed_ms=%u", (unsigned)WATCHDOG_S, (unsigned)FEED_MS);
  fed_since = millis();
}

void loop() {
  if (ended) {
    delay(1000);
    return;
  }
  if (millis() - fed_since < FEED_MS) {
    task_watchdog::feed();
  } else if (!starving) {
    starving = true;
    task_watchdog::starve();
    hm::logf("HM watchdog starving: a reboot should follow within %u s", (unsigned)WATCHDOG_S);
  }
  delay(10);
}
