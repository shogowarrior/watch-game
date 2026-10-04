#include "hm/bma423_imu.h"

namespace hm {

namespace {

// ACC_CONF's odr field for a rate in Hz, -1 for another rate.
int odr_code(int32_t hz) {
  static const int32_t HZ[] = {25, 50, 100, 200, 400, 800, 1600};
  for (int i = 0; i < 7; i++)
    if (HZ[i] == hz) return 6 + i;
  return -1;
}

}  // namespace

bool Bma423Imu::init() {
  uint8_t id;
  if (!i2c_.read(Bma423::ADDR, Bma423::REG_CHIP_ID, &id, 1) || id != Bma423::CHIP_ID) return false;
  const ticks_t t0 = clock_.now_ms();
  write(Bma423::REG_CMD, CMD_SOFTRESET);   // it may NACK while it reboots
  for (;;) {                               // the interface answers again after a few ms
    const int32_t dt = ticks_diff(clock_.now_ms(), t0);
    if (dt >= RESET_MS) {
      if (i2c_.read(Bma423::ADDR, Bma423::REG_CHIP_ID, &id, 1) && id == Bma423::CHIP_ID) break;
      if (dt > RESET_TIMEOUT_MS) return false;
    }
    clock_.delay_ms(1);
  }
  if (!write(Bma423::REG_PWR_CONF, 0x00)) return false;   // adv_power_save off (on after reset)
  clock_.sleep_until_us(clock_.now_us() + APS_WAIT_US);
  return write(Bma423::REG_ACC_CONF, (uint8_t)(ACC_PERF | ACC_BWP_NORMAL | odr_code(odr))) &&
         write(Bma423::REG_ACC_RANGE, RANGE_4G) &&
         write(Bma423::REG_FIFO_CONFIG_0, 0x00) &&   // stream mode, no sensortime frame
         write(Bma423::REG_FIFO_CONFIG_1, FIFO_ACC_EN) && write(Bma423::REG_PWR_CTRL, PWR_ACC_EN);
}

int Bma423Imu::fifo_read_mg() {
  const int n = chip_.fifo_read(raw_, FIFO_FRAMES);
  return n > 0 ? Bma423::decode(raw_, n, fifo_mg, RANGE_MG) : n;
}

// The FIFO is emptied: its samples were taken at the old rate. odr follows
// the chip: set once ACC_CONF took the new rate, even if the flush fails.
bool Bma423Imu::set_odr(int32_t hz) {
  const int code = odr_code(hz);
  if (code < 0 || !write(Bma423::REG_ACC_CONF, (uint8_t)(ACC_PERF | ACC_BWP_NORMAL | code))) return false;
  odr = hz;
  return write(Bma423::REG_CMD, CMD_FIFO_FLUSH);
}

}  // namespace hm
