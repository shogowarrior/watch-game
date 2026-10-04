// The ESP32 pieces both native runtimes share: clock, serial log, backlight and
// the motion-sensor task. Plain ESP-IDF calls, so the Arduino build (IDF 4.4)
// and the ESP-IDF build (IDF 5.5) run the same code.
#pragma once
#include <stdint.h>

#include "hm/hal.h"
#include "hm/imu_sampler.h"

namespace hm {
namespace esp {

struct EspClock : Clock {
  uint32_t now_us() override;
  ticks_t now_ms() override;
  void delay_ms(uint32_t ms) override;
  void sleep_until_us(uint32_t t) override;
};

// ImuSampler::poll every poll_ms from a task pinned to core 0; the renderer runs on core 1.
class CoreImuTask : public ImuTask {
 public:
  void start(ImuSampler& s, int poll_ms) override;
  void stop() override;

 private:
  static void body(void* self);
  ImuSampler* s_ = nullptr;
  int poll_ms_ = 20;
  volatile bool run_ = false, done_ = true;
};

// GPIO12 PWM at 1 kHz and BACKLIGHT_NORMAL, as hal/st7789.py does. The panel
// supply (AXP202 LDO2) must be on: see hm::axp202::panel_power_on.
void backlight(bool on);

}  // namespace esp
}  // namespace hm
