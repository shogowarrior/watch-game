#include "hm/axp202.h"

namespace hm {
namespace axp202 {

bool panel_power_on(I2c& i2c) {
  uint8_t mv, pw;
  if (!i2c.read(ADDR, REG_LDO24_MV, &mv, 1) || !i2c.write8(ADDR, REG_LDO24_MV, (uint8_t)((mv & 0x0F) | 0xF0)))
    return false;
  if (!i2c.read(ADDR, REG_POWER, &pw, 1)) return false;
  return i2c.write8(ADDR, REG_POWER, (uint8_t)(pw | BIT_LDO2 | BIT_DCDC3));
}

}  // namespace axp202
}  // namespace hm
