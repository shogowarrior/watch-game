// The bench with LVGL drawing and sending each timed frame (lvgl_drawer.h)
// over LovyanGFX's SPI bus, so LVGL's own cost shows against the LovyanGFX env.
#include <lvgl.h>   // the version macros

#include "lgfx_bus.h"
#include "lvgl_drawer.h"
#include "hm/esp32.h"

namespace {

hm::esp::EspClock clock_;

// LovyanGFX's bus, then LVGL on top of it.
class LvglLcd : public LgfxBus {
 public:
  bool begin(uint32_t hz) override {
    return LgfxBus::begin(hz) && drawer.start();
  }
  LvglDrawer drawer{*this, clock_};
};

}  // namespace

BenchEnv& bench_env() {
  static LvglLcd lcd;
  static BenchEnv env{"arduino-lvgl", "lvgl_" HM_STR(LVGL_VERSION_MAJOR) "." HM_STR(LVGL_VERSION_MINOR) "." HM_STR(
                                          LVGL_VERSION_PATCH) "_" HM_LGFX_LIBRARY,
                      lcd, &lcd.drawer};
  return env;
}
