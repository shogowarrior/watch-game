// Display and motion-sensor benchmark shared by every native runtime, so the
// Arduino and ESP-IDF builds run the same steps and print the same lines
// ("HM <step> key=value ...", parsed by native/tools/bench_report.py).
#pragma once
#include <stdint.h>

#include "hm/bench_scene.h"
#include "hm/bma423.h"
#include "hm/hal.h"
#include "hm/imu_sampler.h"
#include "hm/st7789.h"
#include "hm/stats.h"

namespace hm {

struct BenchHost {
  const char* variant;      // e.g. "arduino-lovyangfx"
  const char* framework;    // e.g. "arduino-esp32 2.0.17"
  Clock& clock;
  LcdBus& lcd;
  I2c& i2c0;                // AXP202 + BMA423
  ImuTask& imu;
  void (*backlight)(bool on);
};

// One timed run of frames: intervals between frame starts, CPU per frame, misses.
struct RunStats {
  Stats interval, work;
  int frames, miss;
  uint32_t elapsed_us;
};

// Frames paced to target fps (0 = as fast as possible) for dur_us, frame(ctx)
// drawing each; a frame that overruns its slot is a miss and the schedule
// restarts from now. Shared by every renderer, so their "HM run" lines compare.
void pace(Clock& c, RunStats& r, int target, uint32_t dur_us, void (*frame)(void* ctx), void* ctx);
// One "HM <step> hz= target= fps= p50_us= ..." line for a run.
void log_run(const char* step, uint32_t hz, int target, const RunStats& r);

class Bench {
 public:
  static constexpr int SH = 24, NS = 10;          // strip height, strips per frame
  static constexpr uint32_t CLOCKS[3] = {26666667, 40000000, 80000000};

  explicit Bench(BenchHost& h);
  bool setup();               // power, panel, ring map; false (logged) if the hardware is missing
  void run();                 // every measurement, then "HM done"
  // Show the HOT field at target fps for seconds s (0 = unlocked); logs one "HM show" line.
  void show(uint32_t hz, int target, int s);

 private:
  void frame(const FieldParams& p, ticks_t t, bool push, bool wait_each);
  void loop(RunStats& r, const FieldParams& p, int target, uint32_t dur_us, ImuSampler* inline_imu);
  bool clock(uint32_t hz);
  void compose();
  void push(uint32_t hz);
  void windows(uint32_t hz);
  void locked(uint32_t hz, const int* targets, int n);
  void imu();
  void pause() { h_.clock.delay_ms(20); }   // let the idle task run (task watchdog)

  BenchHost& h_;
  St7789 panel_;
  RippleField field_;
  FieldScene scene_;
  Bma423 bma_;
  ImuSampler sampler_;
  RunStats run_;
};

}  // namespace hm
