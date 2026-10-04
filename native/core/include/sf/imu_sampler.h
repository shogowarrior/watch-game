// The motion-sensor task's body: drain the BMA423 FIFO and keep cost and loss
// counters. A runtime calls poll() from a task on the other core (ImuTask).
#pragma once
#include <stdint.h>

#include "sf/bma423.h"

namespace sf {

struct ImuStats {
  uint32_t polls, samples, errors;
  uint32_t full;        // polls that found the FIFO full: older samples were overwritten
  uint32_t fifo_max;    // largest fill seen, bytes
  uint32_t read_us;     // time spent in poll(), the I2C reads included
  uint32_t peak_mg;     // largest |a| seen
};

class ImuSampler {
 public:
  ImuSampler(Bma423& imu, Clock& clock) : imu_(imu), clock_(clock) {}
  void poll();
  void reset() { stats = ImuStats(); }
  ImuStats stats = ImuStats();

 private:
  Bma423& imu_;
  Clock& clock_;
  uint8_t raw_[Bma423::FIFO_BYTES];
  int16_t mg_[3 * (Bma423::FIFO_BYTES / Bma423::FRAME_BYTES)];
};

// Runs ImuSampler::poll every poll_ms on the core the renderer does not use.
struct ImuTask {
  virtual ~ImuTask() = default;
  virtual void start(ImuSampler& s, int poll_ms) = 0;
  virtual void stop() = 0;      // returns once no poll is running
};

}  // namespace sf
