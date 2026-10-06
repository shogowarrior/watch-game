// hal/axp202.py replayed on hm::Axp202Pmu (hal_replay.h): the bring-up
// hal/board.py gives it, side-key and USB events with and without the IRQ
// line, the battery ladder, the long-press time, power-off, NACKs.
#include "check.h"
#include "hal_replay.h"
#include "hm/axp202_pmu.h"

namespace {

struct AxpPort : hmt::HalPort {
  explicit AxpPort(hmt::HalWorld& w) : w(w) {}
  hmt::HalWorld& w;
  std::unique_ptr<hm::Axp202Pmu> p;

  hmt::Json call(const std::string& op, const hmt::Json& a) override {
    using hmt::status;
    using hmt::value;
    if (op == "new") {
      p.reset(new hm::Axp202Pmu(w.i2c0, a["line"].flag() ? &w.irq : nullptr));
      return status(p->begin());
    }
    if (op == "set_long_press_ms") return status(p->set_long_press_ms((int32_t)a[0].in()));
    if (op == "clear_irqs") return status(p->clear_irqs());
    if (op == "poll") return value(p->poll());
    if (op == "battery_percent") return value(p->battery_percent());
    if (op == "battery_voltage") return value(p->battery_voltage());
    if (op == "is_charging") return value(p->is_charging());
    if (op == "vbus_present") return value(p->vbus_present());
    if (op == "shutdown") return status(p->shutdown());
    if (op == "set_ldo2_mv") return status(p->set_ldo2_mv((int)a[0].in()));
    if (op == "set_ldo2" && a[0].flag()) return status(p->set_ldo2_on());   // there is no off
    if (op == "enable_pek") return status(p->enable_pek());
    throw hmt::Mismatch("the port has no " + op);
  }
  hmt::Json state() override { return hmt::Jobj(nullptr, {}); }
};

}  // namespace

TEST(test_hal_axp202) {
  CHECK_HAL("axp202", [](hmt::HalWorld& w) { return std::unique_ptr<hmt::HalPort>(new AxpPort(w)); });
}

TEST(test_hal_percent_from_mv) {
  CHECK(hm::percent_from_mv(4200) == 100 && hm::percent_from_mv(4150) == 100);
  CHECK(hm::percent_from_mv(4100) == 95);   // halfway between 4150 (100) and 4050 (90)
  CHECK(hm::percent_from_mv(3400) == 0 && hm::percent_from_mv(3000) == 0);
}
