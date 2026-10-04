// hal/ft6336.py replayed on hm::Ft6336 (hal_replay.h): touch points, lifts,
// two fingers, clamping, every rotation and mirror, bus errors.
#include "check.h"
#include "hal_replay.h"
#include "hm/ft6336.h"

namespace {

struct TouchPort : hmt::HalPort {
  explicit TouchPort(hmt::HalWorld& w) : w(w) {}
  hmt::HalWorld& w;
  std::unique_ptr<hm::Ft6336> t;

  hmt::Json call(const std::string& op, const hmt::Json& a) override {
    if (op == "new") {
      t.reset(new hm::Ft6336(w.i2c1, (int)a["rotation"].in(), a["mirror_x"].flag(), a["mirror_y"].flag()));
      t->begin();
      return hmt::J();
    }
    if (op == "read") {
      const hm::app::TouchPoint& p = t->read();
      return hmt::J({hmt::J(p.touching), hmt::J(p.x), hmt::J(p.y), hmt::J(p.contacts)});
    }
    throw hmt::Mismatch("the port has no " + op);
  }
  hmt::Json state() override { return hmt::Jobj(nullptr, {{"errors", hmt::J(t->errors)}}); }
};

}  // namespace

TEST(test_hal_ft6336) {
  CHECK_HAL("ft6336", [](hmt::HalWorld& w) { return std::unique_ptr<hmt::HalPort>(new TouchPort(w)); });
}
