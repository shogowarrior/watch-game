// The FT6336 touch panel (hal/ft6336.py FT6336) on I2C1 (SDA 23, SCL 32):
// one 5-byte burst from TD_STATUS per read, mapped into the display frame.
// At MADCTL 0xC0 the raw coordinates already match the screen, so rotation 0
// is right for this watch (hal/README.md). INT gating is not ported (hal has
// it off: unverified on the watch). native/test/test_hal_ft6336.cpp replays
// the Python's traffic on it.
#pragma once
#include <stdint.h>

#include "hm/hal.h"
#include "hm/platform.h"

namespace hm {

class Ft6336 final : public app::Touch {
 public:
  static constexpr uint8_t ADDR = 0x38, REG_TD_STATUS = 0x02, REG_G_MODE = 0xA4;
  static constexpr int SIZE = 240;   // the panel is square: a quarter turn keeps the size

  // rotation: quarter turns clockwise, then the mirrors flip the result.
  explicit Ft6336(I2c& i2c, int rotation = 0, bool mirror_x = false, bool mirror_y = false)
      : i2c_(i2c), rotation_(rotation & 3), mirror_x_(mirror_x), mirror_y_(mirror_y) {}
  // G_MODE 0 (INT held low while touched), as hal's FT6336 sets it when given
  // its INT pin (hal/board.py does); a NACK is ignored there too.
  void begin();
  const app::TouchPoint& read() override;

 private:
  const app::TouchPoint& untouched();
  I2c& i2c_;
  int rotation_;
  bool mirror_x_, mirror_y_;
  app::TouchPoint p_;
};

}  // namespace hm
