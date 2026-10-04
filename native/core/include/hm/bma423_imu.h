// The BMA423 as the game loop drives it (hal/bma423.py BMA423 as hal/board.py
// makes it): +-4 g at 100 Hz into the accel-only headerless FIFO, drained and
// decoded to milli-g every frame, the rate switched while running. The
// feature engine (steps, activity, wrist-wear) is not used (hm/platform.h).
// hm::Bma423 reads and decodes the FIFO. native/test/test_hal_bma423.cpp
// replays the Python's traffic on it.
#pragma once
#include <stdint.h>

#include "hm/bma423.h"
#include "hm/hal.h"
#include "hm/platform.h"

namespace hm {

constexpr int BMA423_Z_SIGN = -1;   // hal/pins.py: the chip's z points into the wrist (face-up reads -1 g)

class Bma423Imu final : public app::Imu {
 public:
  static constexpr uint8_t CMD_SOFTRESET = 0xB6, CMD_FIFO_FLUSH = 0xB0;
  static constexpr uint8_t ACC_PERF = 0x80, ACC_BWP_NORMAL = 0x20;   // continuous sampling, normal filter
  static constexpr uint8_t RANGE_4G = 1, FIFO_ACC_EN = 0x40, PWR_ACC_EN = 0x04;
  static constexpr int RANGE_MG = 4000;
  static constexpr int32_t RESET_MS = 2, RESET_TIMEOUT_MS = 100;   // then CHIP_ID answers again
  static constexpr uint32_t APS_WAIT_US = 450;                     // after clearing adv_power_save

  // At the T-Watch 2020 V1's address, 0x19 (Bma423::ADDR; hal probes 0x19,
  // then 0x18). z_sign: BMA423_Z_SIGN (samples stay in the chip's frame).
  Bma423Imu(I2c& i2c, Clock& clock, int z_sign_) : i2c_(i2c), clock_(clock), chip_(i2c, clock) { z_sign = z_sign_; }
  // The chip id, a soft reset, then the settings at odr. Blocks a few ms;
  // false on a bus error, another chip, or no answer 100 ms after the reset.
  bool init();
  int fifo_read_mg() override;
  bool set_odr(int32_t hz) override;   // 25, 50, 100, 200, 400, 800 or 1600; false on another

 private:
  bool write(uint8_t reg, uint8_t v) { return i2c_.write8(Bma423::ADDR, reg, v); }
  I2c& i2c_;
  Clock& clock_;
  Bma423 chip_;
  uint8_t raw_[FIFO_FRAMES * Bma423::FRAME_BYTES];
};

}  // namespace hm
