#include "hm/ft6336.h"

namespace hm {

void Ft6336::begin() { i2c_.write8(ADDR, REG_G_MODE, 0x00); }

const app::TouchPoint& Ft6336::untouched() {
  p_.touching = false;   // x and y keep their last value
  p_.contacts = 0;
  return p_;
}

// TD_STATUS: touch count (low nibble); P1_XH: event flag (bits 7:6) and x
// bits 11:8; P1_XL; P1_YH: y bits 11:8; P1_YL.
const app::TouchPoint& Ft6336::read() {
  uint8_t b[5];
  if (!i2c_.read(ADDR, REG_TD_STATUS, b, sizeof b)) {
    errors++;
    return untouched();
  }
  const int n = b[0] & 0x0F;
  if (n == 0 || n > 2 || b[1] >> 6 == 1) return untouched();   // event 01: lift up
  int32_t x = (b[1] & 0x0F) << 8 | b[2], y = (b[3] & 0x0F) << 8 | b[4];
  if (x >= SIZE) x = SIZE - 1;
  if (y >= SIZE) y = SIZE - 1;
  const int32_t x0 = x;
  switch (rotation_) {
    case 1: x = SIZE - 1 - y; y = x0; break;
    case 2: x = SIZE - 1 - x; y = SIZE - 1 - y; break;
    case 3: x = y; y = SIZE - 1 - x0; break;
  }
  if (mirror_x_) x = SIZE - 1 - x;
  if (mirror_y_) y = SIZE - 1 - y;
  p_.touching = true;
  p_.x = x;
  p_.y = y;
  p_.contacts = n;
  return p_;
}

}  // namespace hm
