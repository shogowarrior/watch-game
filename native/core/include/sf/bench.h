// Display and motion-sensor benchmark shared by every native runtime, so the
// Arduino and ESP-IDF builds run the same steps and print the same lines
// ("SF <step> key=value ...", parsed by native/tools/bench_report.py).
#pragma once
#include <stdint.h>

#include "sf/bench_scene.h"
#include "sf/bma423.h"
#include "sf/hal.h"
#include "sf/imu_sampler.h"
#include "sf/st7789.h"
#include "sf/stats.h"

namespace sf {

struct BenchHost {
  const char* variant;      // e.g. "arduino-lovyangfx"
  const char* framework;    // e.g. "arduino-esp32 2.0.17"
  Clock& clock;
  LcdBus& lcd;
  I2c& i2c0;                // AXP202 + BMA423
  ImuTask& imu;
  void (*backlight)(bool on);
};

class Bench {
 public:
  static constexpr int SH = 24, NS = 10;          // strip height, strips per frame
  static constexpr uint32_t CLOCKS[3] = {26666667, 40000000, 80000000};

  explicit Bench(BenchHost& h);
  bool setup();               // power, panel, ring map; false (logged) if the hardware is missing
  void run();                 // every measurement, then "SF done"
  // Show the HOT field at target fps for seconds s (0 = unlocked); logs one "SF show" line.
  void show(uint32_t hz, int target, int s);

 private:
  struct Run {
    Stats interval, work;
    int frames, miss;
    uint32_t elapsed_us;
  };
  void frame(const FieldParams& p, ticks_t t, bool push, bool wait_each);
  void loop(Run& r, const FieldParams& p, int target, uint32_t dur_us, ImuSampler* inline_imu);
  void log_run(const char* step, uint32_t hz, int target, const Run& r);
  bool clock(uint32_t hz);
  void compose();
  void push(uint32_t hz);
  void locked(uint32_t hz, const int* targets, int n);
  void imu();
  void pause() { h_.clock.delay_ms(20); }   // let the idle task run (task watchdog)

  BenchHost& h_;
  St7789 panel_;
  RippleField field_;
  FieldScene scene_;
  Bma423 bma_;
  ImuSampler sampler_;
  Run run_;
};

}  // namespace sf
