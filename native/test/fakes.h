// Host fakes for the sf::hal interfaces: they record traffic and keep a fake
// clock that advances as real hardware would (SPI wire time, delays).
#pragma once
#include <stdint.h>

#include <map>
#include <string>
#include <vector>

#include "sf/hal.h"
#include "sf/imu_sampler.h"

namespace sft {

extern std::vector<std::string> log_lines;    // every sf::logf line

struct FakeClock : sf::Clock {
  uint64_t t = 1000000;
  uint32_t now_us() override { return (uint32_t)(t += 3); }
  sf::ticks_t now_ms() override { return (sf::ticks_t)(t / 1000); }
  void delay_ms(uint32_t ms) override { t += (uint64_t)ms * 1000; }
  void sleep_until_us(uint32_t when) override {
    const int32_t d = (int32_t)(when - (uint32_t)t);
    if (d > 0) t += d;
  }
};

struct LcdOp {
  uint8_t cmd;
  std::vector<uint8_t> data;   // command parameters
  size_t pixels;               // pixel count (pixels() calls)
};

struct FakeLcd : sf::LcdBus {
  explicit FakeLcd(FakeClock& c) : clock(c) {}
  FakeClock& clock;
  uint32_t hz = 0;
  uint32_t max_hz = 80000000;
  std::vector<LcdOp> ops;
  bool set_clock(uint32_t h) override {
    if (h > max_hz) return false;
    hz = h;
    return true;
  }
  void command(uint8_t cmd, const uint8_t* d, size_t n) override {
    ops.push_back({cmd, std::vector<uint8_t>(d, d + n), 0});
  }
  void pixels(uint8_t cmd, const uint16_t*, size_t count) override {
    ops.push_back({cmd, {}, count});
    clock.t += (uint64_t)count * 16 * 1000000 / hz;   // the wire time
  }
  void wait() override {}
};

// AXP202 and BMA423 registers; the BMA423 FIFO always holds `fifo` bytes of 1 g on z.
struct FakeI2c : sf::I2c {
  std::map<int, uint8_t> regs;                  // (addr << 8 | reg) -> value
  std::vector<std::pair<int, uint8_t>> writes;  // (addr << 8 | reg, value)
  int fifo = 96;
  FakeI2c() {
    regs[0x19 << 8 | 0x00] = 0x13;              // BMA423 chip id
    regs[0x35 << 8 | 0x12] = 0x5B;              // AXP202 power outputs (DCDC3 set)
    regs[0x35 << 8 | 0x28] = 0xA5;
  }
  bool write(uint8_t addr, uint8_t reg, const uint8_t* d, size_t n) override {
    for (size_t i = 0; i < n; i++) {
      writes.push_back({addr << 8 | reg, d[i]});
      regs[addr << 8 | reg] = d[i];
    }
    return addr == 0x19 || addr == 0x35;
  }
  bool read(uint8_t addr, uint8_t reg, uint8_t* d, size_t n) override {
    if (addr == 0x19 && reg == 0x24) {
      d[0] = (uint8_t)fifo;
      d[1] = (uint8_t)(fifo >> 8);
      return true;
    }
    for (size_t i = 0; i < n; i++) {
      if (addr == 0x19 && reg == 0x26) d[i] = (i % 6 == 5) ? 0x20 : 0;   // z = 0x2000 = 1 g at 8 g
      else d[i] = regs[addr << 8 | reg];
    }
    return addr == 0x19 || addr == 0x35;
  }
};

struct FakeImuTask : sf::ImuTask {
  sf::ImuSampler* s = nullptr;
  int starts = 0;
  void start(sf::ImuSampler& sampler, int) override {
    s = &sampler;
    starts++;
    sampler.poll();                             // one poll stands in for the task
  }
  void stop() override { s = nullptr; }
};

}  // namespace sft
