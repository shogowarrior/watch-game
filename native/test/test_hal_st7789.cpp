// hal/st7789.py replayed on hm::St7789Display (hal_replay.h): the init with
// and without the LDO2 hook, strips that continue a window or open one, sleep
// and wake (the SLPIN to SLPOUT wait, across a clock wrap too), the backlight
// duty's rounding.
#include <vector>

#include "check.h"
#include "hal_replay.h"
#include "hm/st7789_display.h"

namespace {

struct LcdPort : hmt::HalPort {
  explicit LcdPort(hmt::HalWorld& w) : w(w) {}
  hmt::HalWorld& w;
  hm::Axp202Pmu pmu{w.i2c0, nullptr};   // bl_power's chip (brought up before the trace's calls)
  std::unique_ptr<hm::St7789Display> d;
  std::vector<uint16_t> black = std::vector<uint16_t>(240 * 24), px;

  hmt::Json call(const std::string& op, const hmt::Json& a) override {
    if (op == "new") {
      d.reset(new hm::St7789Display(w.lcd, w.clock, w.backlight, a["bl_power"].flag() ? &pmu : nullptr));
      return a["init"].flag() ? hmt::status(d->init(black.data(), 24)) : hmt::J();
    }
    if (op == "init") return hmt::status(d->init(black.data(), 24));
    if (op == "push_strip") {
      const int y0 = (int)a[0].in(), h = (int)a[1].in();
      px.assign((size_t)240 * h, 0);
      hmt::pattern((int)a[2].in(), (uint8_t*)px.data(), px.size() * 2);
      d->push_strip(y0, h, px.data());
    } else if (op == "brightness") {
      d->brightness(a[0].num());
    } else if (op == "sleep") {
      d->sleep();
    } else if (op == "wake") {
      d->wake(a[0].num(), a[1].flag() ? hmt::HalWorld::wait : nullptr, &w);
    } else {
      throw hmt::Mismatch("the port has no " + op);
    }
    return hmt::voided();
  }
  hmt::Json state() override { return hmt::Jobj(nullptr, {{"asleep", hmt::J(d->asleep)}}); }
};

}  // namespace

TEST(test_hal_st7789) {
  CHECK_HAL("st7789", [](hmt::HalWorld& w) { return std::unique_ptr<hmt::HalPort>(new LcdPort(w)); });
}
