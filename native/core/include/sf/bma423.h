// BMA423 accelerometer: accel-only headerless FIFO in stream mode (hal/bma423.py).
#pragma once
#include <stdint.h>

#include "sf/hal.h"

namespace sf {

class Bma423 {
 public:
  static constexpr uint8_t ADDR = 0x19, CHIP_ID = 0x13;
  static constexpr uint8_t REG_CHIP_ID = 0x00, REG_FIFO_LENGTH_0 = 0x24, REG_FIFO_DATA = 0x26,
                           REG_ACC_CONF = 0x40, REG_ACC_RANGE = 0x41, REG_FIFO_CONFIG_0 = 0x48,
                           REG_FIFO_CONFIG_1 = 0x49, REG_PWR_CONF = 0x7C, REG_PWR_CTRL = 0x7D, REG_CMD = 0x7E;
  static constexpr int FRAME_BYTES = 6;
  static constexpr int FIFO_BYTES = 1024;
  static constexpr int READ_CHUNK = 120;   // whole frames per burst (Arduino Wire buffers 128 B)

  Bma423(I2c& i2c, Clock& clock) : i2c_(i2c), clock_(clock) {}
  // Soft reset, then odr_hz (25..1600), +-range_g (2, 4, 8, 16), performance mode,
  // normal filter, FIFO on. False if the chip is missing or a setting is invalid.
  bool init(int odr_hz, int range_g);
  int fifo_bytes();                                   // fill level, -1 on a bus error
  // Whole frames read (at most max_frames), -1 on a bus error; *level = the fill it saw, bytes.
  int fifo_read(uint8_t* buf, int max_frames, int* level = nullptr);
  int range_mg() const { return range_mg_; }
  // Headerless frames -> x, y, z milli-g; stops at an all-0x8000 frame (read past the fill).
  static int decode(const uint8_t* buf, int n, int16_t* out, int range_mg);

 private:
  I2c& i2c_;
  Clock& clock_;
  int range_mg_ = 4000;
};

}  // namespace sf
