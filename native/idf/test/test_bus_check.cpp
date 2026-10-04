// The I2C0 bus check on fakes: a clean run passes, and every fault it is there
// to catch (a changed register, a garbled RTC read, a sensor that stops
// answering at the faster clock, a missing chip) fails it.
#include <string.h>

#include <map>

#include "check.h"
#include "fakes.h"
#include "hm/bus_check.h"

namespace {

// The AXP202 and BMA423 of hmt::FakeI2c plus a PCF8563 RTC, with a clock per chip.
struct FakeBus : hm::I2cBus {
  hmt::FakeI2c chips;
  std::map<int, uint8_t> rtc = {{0x00, 0x00}, {0x01, 0x00}, {0x02, 0x37}, {0x0D, 0x80}};
  bool rtc_present = true;
  uint32_t bma_hz = 400000;
  uint32_t bma_max_hz = 1000000;   // the sensor stops answering above this
  int axp_reads = 0;
  int flip_after = -1;             // AXP202 output enables change after this many AXP202 reads

  bool set_clock(uint8_t addr, uint32_t hz) override {
    if (addr != 0x19) return false;
    bma_hz = hz;
    return true;
  }
  bool write(uint8_t addr, uint8_t reg, const uint8_t* d, size_t n) override {
    return chips.write(addr, reg, d, n);
  }
  bool read(uint8_t addr, uint8_t reg, uint8_t* d, size_t n) override {
    if (addr == 0x51) {
      for (size_t i = 0; i < n; i++) d[i] = rtc[reg + (int)i];
      return rtc_present;
    }
    if (addr == 0x19 && bma_hz > bma_max_hz) return false;
    if (addr == 0x35 && ++axp_reads == flip_after) chips.regs[0x35 << 8 | 0x12] ^= 0x02;
    return chips.read(addr, reg, d, n);
  }
};

struct Rig {
  FakeBus bus;
  hmt::FakeClock clock;
  hm::Bma423 bma{bus, clock};
  hm::ImuSampler sampler{bma, clock};
  hmt::FakeImuTask task;
  hm::BusCheck check{bus, clock, bma, sampler, task};
};

const std::string& last_line() { return hmt::log_lines.back(); }

bool has(const std::string& line, const char* field) { return line.find(field) != std::string::npos; }

}  // namespace

TEST(test_bus_check_clean_run_passes) {
  Rig r;
  CHECK(r.check.baseline());
  CHECK(r.check.run(1000000, 100));
  CHECK(has(last_line(), "HM i2c bma_hz=1000000 armed=1 "));
  CHECK(has(last_line(), " probe_errors=0 changed=0 bad_time=0 after_errors=0 after_changed=0 after_bad_time=0"));
  CHECK(r.bus.bma_hz == 400000);   // back at the safe clock
  CHECK(r.task.starts == 1 && r.task.s == nullptr);
}

TEST(test_bus_check_flags_a_changed_register) {
  Rig r;
  CHECK(r.check.baseline());
  r.bus.flip_after = r.bus.axp_reads + 5;   // DCDC3's bit flips during the run
  CHECK(!r.check.run(1000000, 100));
  CHECK(!has(last_line(), " changed=0 "));
  CHECK(has(last_line(), " after_changed=1 "));
}

TEST(test_bus_check_flags_garbled_rtc_seconds) {
  Rig r;
  CHECK(r.check.baseline());
  r.bus.rtc[0x02] = 0x5A;   // not BCD
  CHECK(!r.check.run(1000000, 100));
  CHECK(!has(last_line(), " bad_time=0 "));
  CHECK(!has(last_line(), " after_bad_time=0"));
}

TEST(test_bus_check_flags_a_sensor_too_slow_for_the_clock) {
  Rig r;
  CHECK(r.check.baseline());
  r.bus.bma_max_hz = 700000;
  CHECK(r.check.run(700000, 100));
  CHECK(!r.check.run(1000000, 100));
  CHECK(has(last_line(), "HM i2c bma_hz=1000000 armed=0 "));
  CHECK(r.bus.bma_hz == 400000);
}

TEST(test_bus_check_needs_every_chip) {
  Rig r;
  r.bus.rtc_present = false;
  CHECK(!r.check.baseline());
  CHECK(has(last_line(), "HM error what=i2c_probe addr=0x51 "));
}
