// AXP202 power chip: the one rail the display needs (hal/axp202.py, AGENTS.md rule 5).
// REG 0x12 bit 1 is DCDC3, the ESP32's own supply: every write keeps it set.
#pragma once
#include <stdint.h>

#include "hm/hal.h"

namespace hm {
namespace axp202 {

constexpr uint8_t ADDR = 0x35;
constexpr uint8_t REG_POWER = 0x12;      // output enables
constexpr uint8_t REG_LDO24_MV = 0x28;   // [7:4] LDO2 = 1800 + 100*n mV
constexpr uint8_t BIT_DCDC3 = 0x02;      // ESP32 supply: never cleared
constexpr uint8_t BIT_LDO2 = 0x04;       // panel and backlight on the V1

// LDO2 at 3.3 V and on (the panel's supply on the V1). False if the chip did not answer.
bool panel_power_on(I2c& i2c);

}  // namespace axp202
}  // namespace hm
