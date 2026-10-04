// I2C0 check for reading the BMA423 faster than 400 kHz. The AXP202 power chip
// and the PCF8563 RTC share those wires and are 400 kHz parts: a chip that
// misreads fast traffic can answer for the BMA423, latch a stray write or hold
// the bus. So while the motion-sensor task reads at the new clock, their
// registers are read back at 400 kHz and must keep their values, during the
// run and after it. Plain C++ on hm::I2c, so the host tests run it on fakes.
#pragma once
#include <stdint.h>

#include "hm/bma423.h"
#include "hm/hal.h"
#include "hm/imu_sampler.h"

namespace hm {

// An I2C bus whose chips can each run at their own clock.
struct I2cBus : I2c {
  virtual bool set_clock(uint8_t addr, uint32_t hz) = 0;
};

struct BusTally {
  uint32_t reads, errors, changed;
  uint32_t bad_time;     // RTC seconds that were not BCD 00..59
};

class BusCheck {
 public:
  static constexpr uint32_t SAFE_HZ = 400000;
  BusCheck(I2cBus& bus, Clock& clock, Bma423& bma, ImuSampler& sampler, ImuTask& task)
      : bus_(bus), clock_(clock), bma_(bma), sampler_(sampler), task_(task) {}
  bool baseline();               // remember every probe; false (logged) if a chip does not answer
  void probe(BusTally& t);       // read every probe once against the baseline
  // The sensor at 800 Hz read at bma_hz for dur_ms while the probes run, then
  // the probes again with it back at SAFE_HZ. Logs one "HM i2c" line; true if clean.
  bool run(uint32_t bma_hz, uint32_t dur_ms);

 private:
  static constexpr int N = 6;
  struct Probe {
    uint8_t addr, reg, mask;
  };
  static const Probe PROBES[N];
  I2cBus& bus_;
  Clock& clock_;
  Bma423& bma_;
  ImuSampler& sampler_;
  ImuTask& task_;
  uint8_t base_[N] = {};
};

}  // namespace hm
