// hal/board.py's Board.init replayed on the core's drivers (hal_replay.h):
// the bring-up a shell does, in hal/board.py's ORDER, on one shared I2C0 (the
// AXP202 and the BMA423) and the touch's I2C1, then a call on each part.
#include <vector>

#include "check.h"
#include "hal_replay.h"
#include "hm/axp202_pmu.h"
#include "hm/bma423_imu.h"
#include "hm/ft6336.h"
#include "hm/st7789_display.h"

namespace {

struct BoardPort : hmt::HalPort {
  explicit BoardPort(hmt::HalWorld& w) : w(w) { hmt::bma423_allowances(w); }
  hmt::HalWorld& w;
  std::unique_ptr<hm::Axp202Pmu> pmu;
  std::unique_ptr<hm::St7789Display> display;
  std::unique_ptr<hm::Bma423Imu> imu;
  std::unique_ptr<hm::Ft6336> touch;
  std::vector<uint16_t> strip = std::vector<uint16_t>(240 * 24);   // the renderer's strip, black at init

  // What a shell does: pmu, display, backlight, imu, touch (the motor and the
  // radio are the shell's own).
  bool bring_up() {
    pmu.reset(new hm::Axp202Pmu(w.i2c0, &w.irq));
    if (!pmu->begin()) return false;
    display.reset(new hm::St7789Display(w.lcd, w.clock, w.backlight, pmu.get()));
    if (!display->init(strip.data(), 24)) return false;
    display->brightness(hm::St7789Display::DEFAULT_BRIGHTNESS);
    imu.reset(new hm::Bma423Imu(w.i2c0, w.clock, hm::BMA423_Z_SIGN));
    if (!imu->init()) return false;
    touch.reset(new hm::Ft6336(w.i2c1));
    touch->begin();
    return true;
  }

  hmt::Json call(const std::string& op, const hmt::Json& a) override {
    if (op == "init") return hmt::status(bring_up());
    if (op == "pmu.poll") return hmt::value(pmu->poll());
    if (op == "imu.fifo_read_mg") return hmt::value(imu->fifo_read_mg());
    if (op == "touch.read") {
      const hm::app::TouchPoint& p = touch->read();
      return hmt::J({hmt::J(p.touching), hmt::J(p.x), hmt::J(p.y), hmt::J(p.contacts)});
    }
    if (op == "display.push_strip") {
      hmt::pattern((int)a[2].in(), (uint8_t*)strip.data(), strip.size() * 2);
      display->push_strip((int)a[0].in(), (int)a[1].in(), strip.data());
    } else if (op == "display.sleep") {
      display->sleep();
    } else {
      throw hmt::Mismatch("the port has no " + op);
    }
    return hmt::voided();
  }
  hmt::Json state() override {
    return hmt::Jobj(nullptr, {{"asleep", hmt::J(display->asleep)}, {"odr", hmt::J(imu->odr)},
                               {"z_sign", hmt::J(imu->z_sign)}, {"touch_errors", hmt::J(touch->errors)}});
  }
};

}  // namespace

TEST(test_hal_board) {
  CHECK_HAL("board", [](hmt::HalWorld& w) { return std::unique_ptr<hmt::HalPort>(new BoardPort(w)); });
}
