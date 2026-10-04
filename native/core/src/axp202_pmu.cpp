#include "hm/axp202_pmu.h"

namespace hm {

namespace {

// (mV, %) of a Li-ion cell at rest, falling.
constexpr int OCV[][2] = {{4150, 100}, {4050, 90}, {3950, 75}, {3850, 55}, {3780, 40},
                          {3720, 25},  {3650, 12}, {3550, 5},  {3400, 0}};

}  // namespace

int percent_from_mv(int mv) {
  if (mv >= OCV[0][0]) return 100;
  for (size_t i = 1; i < sizeof OCV / sizeof OCV[0]; i++)
    if (mv >= OCV[i][0])
      return OCV[i][1] + (OCV[i - 1][1] - OCV[i][1]) * (mv - OCV[i][0]) / (OCV[i - 1][0] - OCV[i][0]);
  return 0;
}

int Axp202Pmu::read(uint8_t reg) {
  uint8_t v;
  return i2c_.read(axp202::ADDR, reg, &v, 1) ? v : -1;
}

bool Axp202Pmu::write(uint8_t reg, uint8_t v) {
  if (reg == axp202::REG_POWER) v |= axp202::BIT_DCDC3;
  return i2c_.write8(axp202::ADDR, reg, v);
}

bool Axp202Pmu::update(uint8_t reg, uint8_t set, uint8_t clear) {
  const int v = read(reg);
  if (v < 0) return false;
  const uint8_t n = (uint8_t)((v & ~clear) | set);
  return n == v || write(reg, n);
}

bool Axp202Pmu::begin() {
  return read(REG_IC_TYPE) == CHIP_ID && update(REG_ADC_EN1, ADC_ON) && set_ldo2_mv(LDO2_MV) && set_ldo2_on() &&
         enable_pek() && clear_irqs();
}

bool Axp202Pmu::set_ldo2_mv(int mv) {
  mv = mv < 1800 ? 1800 : mv > 3300 ? 3300 : mv;
  return update(axp202::REG_LDO24_MV, (uint8_t)((mv - 1800) / 100 << 4), 0xF0);   // LDO4 bits kept
}

bool Axp202Pmu::set_ldo2_on() { return update(axp202::REG_POWER, axp202::BIT_LDO2); }

bool Axp202Pmu::enable_pek() { return update(REG_INTEN1 + 2, IRQ3_PEK_SHORT | IRQ3_PEK_LONG); }

bool Axp202Pmu::set_long_press_ms(int32_t ms) {
  const int n = ms < 1250 ? 0 : ms < 1750 ? 1 : ms < 2250 ? 2 : 3;
  return update(REG_POK_SET, (uint8_t)(n << 4), 0x30);   // the power-on and power-off hold bits kept
}

bool Axp202Pmu::clear_irqs() {
  for (int i = 0; i < 5; i++)
    if (!write((uint8_t)(REG_INTSTS1 + i), 0xFF)) return false;
  return true;
}

// All five status registers are read before any is cleared, and one is only
// reported once its clear went through: a bus error leaves its bits latched
// for the next poll, so no event is lost. Unrelated bits are cleared too, so
// the shared line releases.
int Axp202Pmu::poll() {
  if (irq_ && !irq_->low()) return 0;
  uint8_t st[5];
  for (int i = 0; i < 5; i++) {
    const int v = read((uint8_t)(REG_INTSTS1 + i));
    if (v < 0) return -1;
    st[i] = (uint8_t)v;
  }
  for (int i = 0; i < 5; i++)
    if (st[i] && !write((uint8_t)(REG_INTSTS1 + i), st[i])) st[i] = 0;   // write 1s to clear what was seen
  int ev = 0;
  if (st[2] & IRQ3_PEK_SHORT) ev |= app::EV_SHORT;
  if (st[2] & IRQ3_PEK_LONG) ev |= app::EV_LONG;
  if (st[4] & IRQ5_PEK_FALL) ev |= app::EV_PRESS;
  if (st[4] & IRQ5_PEK_RISE) ev |= app::EV_RELEASE;
  if (st[0] & IRQ1_VBUS_CONNECT) ev |= app::EV_VBUS_IN;
  if (st[0] & IRQ1_VBUS_REMOVED) ev |= app::EV_VBUS_OUT;
  return ev;
}

int Axp202Pmu::battery_percent() {
  const int v = read(REG_BAT_PCT);
  if (v < 0) return -1;
  if (!(v & 0x80)) return (v & 0x7F) > 100 ? 100 : v & 0x7F;
  const int mv = battery_voltage();
  return mv < 0 ? -1 : percent_from_mv(mv);
}

int Axp202Pmu::battery_voltage() {
  const int h = read(REG_BAT_V_H8);
  const int lo = h < 0 ? -1 : read(REG_BAT_V_H8 + 1);
  return lo < 0 ? -1 : (h << 4 | (lo & 0x0F)) * 11 / 10;
}

int Axp202Pmu::is_charging() {
  const int v = read(REG_CHG_STATUS);
  return v < 0 ? -1 : (v & 0x40) != 0;
}

int Axp202Pmu::vbus_present() {
  const int v = read(REG_STATUS);
  return v < 0 ? -1 : (v & 0x20) != 0;
}

bool Axp202Pmu::shutdown() { return update(REG_OFF_CTL, 0x80); }

}  // namespace hm
