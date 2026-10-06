// hal/bma423.py replayed on hm::Bma423Imu (hal_replay.h): the init as
// hal/board.py makes it (a slow or silent reset, another chip, NACKs), FIFO
// fills from empty to full with the 0x8000 stop, rate changes and their flush.
#include "check.h"
#include "hal_replay.h"
#include "hm/bma423_imu.h"

namespace {

struct ImuPort : hmt::HalPort {
  explicit ImuPort(hmt::HalWorld& w) : w(w) { hmt::bma423_allowances(w); }
  hmt::HalWorld& w;
  std::unique_ptr<hm::Bma423Imu> m;

  hmt::Json call(const std::string& op, const hmt::Json& a) override {
    if (op == "new") {
      m.reset(new hm::Bma423Imu(w.i2c0, w.clock, hm::BMA423_Z_SIGN));
      return hmt::status(m->init());
    }
    if (op == "fifo_read_mg") return hmt::value(m->fifo_read_mg());
    if (op == "set_odr") return hmt::status(m->set_odr((int32_t)a[0].in()));
    throw hmt::Mismatch("the port has no " + op);
  }
  hmt::Json state() override {
    return hmt::Jobj(nullptr, {{"odr", hmt::J(m->odr)}, {"z_sign", hmt::J(m->z_sign)},
                               {"fifo_mg", hmt::Jarr(m->fifo_mg, 3 * hm::app::Imu::FIFO_FRAMES)}});
  }
};

}  // namespace

TEST(test_hal_bma423) {
  CHECK_HAL("bma423", [](hmt::HalWorld& w) { return std::unique_ptr<hmt::HalPort>(new ImuPort(w)); });
}
