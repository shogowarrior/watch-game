// The AXP202 power chip as the game loop drives it (hal/axp202.py AXP202,
// with the bring-up hal/board.py gives it): the side key (PEK), the battery,
// USB and power-off. Every write to REG 0x12 keeps DCDC3, the ESP32's own
// supply, set, and nothing here switches LDO2 (panel and backlight) off
// (AGENTS.md rule 5). native/test/test_hal_axp202.cpp replays the Python's
// bus traffic on it.
#pragma once
#include <stdint.h>

#include "hm/axp202.h"
#include "hm/hal.h"
#include "hm/platform.h"

namespace hm {

// Rough state of charge, 0..100, from a resting cell voltage (hal's curve for
// while the fuel gauge is not valid yet).
int percent_from_mv(int mv);

class Axp202Pmu final : public app::Pmu {
 public:
  static constexpr uint8_t CHIP_ID = 0x41;
  static constexpr uint8_t REG_STATUS = 0x00,      // bit5 VBUS present
      REG_CHG_STATUS = 0x01,                       // bit6 charging
      REG_IC_TYPE = 0x03, REG_OFF_CTL = 0x32,      // bit7: shut all outputs down
      REG_POK_SET = 0x36,                          // [5:4] PEK long-press time
      REG_INTEN1 = 0x40, REG_INTSTS1 = 0x48,       // ..0x44, ..0x4C (write 1 to clear)
      REG_BAT_V_H8 = 0x78,                         // + 0x79 L4, 1.1 mV/LSB
      REG_ADC_EN1 = 0x82, REG_BAT_PCT = 0xB9;      // [6:0] percent, bit7 = not valid yet
  static constexpr uint8_t ADC_ON = 0x80 | 0x40 | 0x08 | 0x02;   // battery V and I, VBUS V, APS V
  static constexpr uint8_t IRQ1_VBUS_REMOVED = 0x04, IRQ1_VBUS_CONNECT = 0x08, IRQ3_PEK_LONG = 0x01,
                           IRQ3_PEK_SHORT = 0x02, IRQ5_PEK_FALL = 0x20, IRQ5_PEK_RISE = 0x40;
  static constexpr int LDO2_MV = 3300;   // panel and backlight supply (hal/board.py, as TTGO.h)

  // irq: the IRQ line (GPIO35, active low), or nullptr: poll() always reads.
  Axp202Pmu(I2c& i2c, Line* irq) : i2c_(i2c), irq_(irq) {}
  // AXP202(i2c) and hal/board.py _make_pmu: the chip id, the ADCs on, LDO2 at
  // 3.3 V and on, the PEK IRQs on, IRQs latched before this boot cleared.
  // False on a bus error or another chip.
  bool begin();
  bool set_ldo2_mv(int mv);   // 1800..3300 mV in 100 mV steps
  bool set_ldo2_on();         // hal's set_ldo2(True), the display's bl_power; there is no off
  bool enable_pek();          // short and long press IRQs

  bool set_long_press_ms(int32_t ms) override;   // 1000, 1500, 2000 or 2500 ms
  bool clear_irqs() override;
  int poll() override;
  int battery_percent() override;   // the fuel gauge, or percent_from_mv while it is not valid
  int battery_voltage() override;
  int is_charging() override;
  int vbus_present() override;
  bool shutdown() override;

 private:
  int read(uint8_t reg);                   // -1 on a bus error
  bool write(uint8_t reg, uint8_t v);      // REG 0x12 always with DCDC3
  bool update(uint8_t reg, uint8_t set, uint8_t clear = 0);   // writes only a change
  I2c& i2c_;
  Line* irq_;
};

}  // namespace hm
