// The T-Watch 2020 V1 pins the Arduino platform layer uses (hal/pins.py).
#pragma once
#include <stdint.h>

namespace pins {

constexpr int I2C0_SDA = 21, I2C0_SCL = 22;   // AXP202 0x35, BMA423 0x19 (or 0x18), PCF8563 RTC 0x51
constexpr int I2C1_SDA = 23, I2C1_SCL = 32;   // FT6336 touch 0x38
constexpr uint32_t I2C_HZ = 400000;
constexpr uint8_t AXP202_ADDR = 0x35, BMA423_ADDR = 0x19, BMA423_ADDR_ALT = 0x18, RTC_ADDR = 0x51, TOUCH_ADDR = 0x38;

}  // namespace pins
