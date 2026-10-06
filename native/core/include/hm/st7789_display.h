// The ST7789 panel and its backlight as the game loop drives them
// (hal/st7789.py ST7789): hm::St7789's init and windows, plus the strips the
// renderer pushes, sleep and wake, and the backlight on GPIO12. On the V1 the
// AXP202's LDO2 powers the panel and the backlight: it is switched on before
// the init and never off (AGENTS.md rule 5), so the backlight goes dark by
// duty 0. native/test/test_hal_st7789.cpp replays the Python's traffic on it.
#pragma once
#include <stdint.h>

#include "hm/axp202_pmu.h"
#include "hm/hal.h"
#include "hm/platform.h"
#include "hm/st7789.h"

namespace hm {

class St7789Display final : public app::Display {
 public:
  static constexpr uint8_t SLPIN = 0x10, DISPOFF = 0x28;
  static constexpr int32_t SLP_MS = 120;              // the ST7789's minimum from SLPIN to SLPOUT, and after SLPOUT
  static constexpr double DEFAULT_BRIGHTNESS = 0.6;   // hal/board.py, set at bring-up

  // backlight: the GPIO12 PWM. bl_power: the PMU whose LDO2 feeds the panel and
  // the backlight (hal's bl_power=pmu.set_ldo2), or nullptr when the PMU did
  // not come up (the panel then shows only if LDO2 is already on).
  St7789Display(LcdBus& bus, Clock& clock, app::Pwm& backlight, Axp202Pmu* bl_power)
      : bus_(bus), clock_(clock), lcd_(bus, clock), backlight_(backlight), bl_power_(bl_power) {}
  // LDO2 on and 10 ms for the panel's power-on reset, then St7789::init (black:
  // a strip of `rows` rows, rows dividing 240, to clear GRAM with). Blocks
  // about 340 ms. False if LDO2 could not be switched on (the Python raises).
  bool init(uint16_t* black, int rows);

  // A strip that starts where the last one ended continues its window (RAMWRC);
  // any other opens a window from y0 to the bottom row.
  void push_strip(int y0, int h, const uint16_t* px) override;
  void flush() override { bus_.wait(); }
  void sleep() override;
  // Waits out the rest of SLP_MS since the last SLPIN, sends SLPOUT, waits
  // SLP_MS, sends DISPON. wait (nullptr: the clock's delay) does both waits.
  void wake(double level, app::WaitFn wait, void* ctx) override;
  // The PWM gets hal's duty_u16 / 65535: level rounded to a whole 65535th.
  void brightness(double level) override;

 private:
  void command(uint8_t c);   // one command; it ends any open window
  void pause(app::WaitFn wait, void* ctx, int32_t ms);
  LcdBus& bus_;
  Clock& clock_;
  St7789 lcd_;
  app::Pwm& backlight_;
  Axp202Pmu* bl_power_;
  bool bl_on_ = false;     // LDO2 switched on by this driver
  bool slept_ = false;     // slpin_t_ holds the last SLPIN
  ticks_t slpin_t_ = 0;
  int next_ = -1;          // the row an open window continues at
};

}  // namespace hm
