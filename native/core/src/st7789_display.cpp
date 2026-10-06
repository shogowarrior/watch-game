#include "hm/st7789_display.h"

namespace hm {

bool St7789Display::init(uint16_t* black, int rows) {
  if (bl_power_) {
    if (!bl_power_->set_ldo2_on()) return false;
    bl_on_ = true;
    clock_.delay_ms(10);   // the panel's power-on reset
  }
  lcd_.init(black, rows);
  next_ = -1;
  asleep = false;
  return true;
}

void St7789Display::command(uint8_t c) {
  next_ = -1;
  bus_.command(c, nullptr, 0);
}

void St7789Display::push_strip(int y0, int h, const uint16_t* px) {
  if (y0 != next_) lcd_.begin_window(0, y0, St7789::W, St7789::H - y0);
  lcd_.push_pixels(px, (size_t)St7789::W * h);
  const int y = y0 + h;
  next_ = y >= St7789::H ? -1 : y;
}

void St7789Display::sleep() {
  backlight_.set(0.0);
  command(DISPOFF);
  command(SLPIN);   // GRAM is kept
  slpin_t_ = clock_.now_ms();
  slept_ = true;
  clock_.delay_ms(5);
  asleep = true;
}

void St7789Display::pause(app::WaitFn wait, void* ctx, int32_t ms) {
  if (wait)
    wait(ctx, ms);
  else
    clock_.delay_ms((uint32_t)ms);
}

void St7789Display::wake(double level, app::WaitFn wait, void* ctx) {
  if (slept_) {
    const int32_t e = ticks_diff(clock_.now_ms(), slpin_t_);
    if (e >= 0 && e < SLP_MS) pause(wait, ctx, SLP_MS - e);
  }
  command(St7789::SLPOUT);
  pause(wait, ctx, SLP_MS);
  command(St7789::DISPON);
  asleep = false;
  brightness(level);
}

void St7789Display::brightness(double level) {
  level = level < 0 ? 0.0 : level > 1 ? 1.0 : level;
  const int32_t duty = (int32_t)(level * 65535 + 0.5);
  if (duty && !bl_on_) {   // LDO2 before the first light, if no init switched it on
    if (bl_power_ && !bl_power_->set_ldo2_on()) return;
    bl_on_ = true;
  }
  backlight_.set(duty / 65535.0);
}

}  // namespace hm
