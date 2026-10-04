// Replays the traces native/tools/trace_hal.py records from hal/*.py on the
// core's drivers (hm/axp202_pmu.h, st7789_display.h, bma423_imu.h, ft6336.h):
// each scripted call is made on the C++ driver over fakes that answer from
// the recording, and what the driver does (I2C transactions with their bytes,
// panel commands and pixels, backlight duty, IRQ line reads, sleeps and waits,
// in order), its result and its state must come out as the Python's. run.py
// records the traces into HM_TRACES (hal.<driver>.jsonl).
//
// The fakes compare requests, not wire framing: a panel command is its code
// and parameters whatever the CS framing, a RAMWRC right after pixels
// continues them as the Python's open window does, and a read of a register
// a port declares streaming (the BMA423's FIFO_DATA) may come in several
// pieces.
#pragma once
#include <stdint.h>

#include <functional>
#include <memory>
#include <set>
#include <string>

#include "hm/hal.h"
#include "hm/platform.h"
#include "trace.h"

namespace hmt {

// What a port's call returns besides a value: an error status (it matches any
// exception the Python raised), or nothing (a call that reports nothing in
// C++: any Python result matches, its io and state are still checked).
Json raised();
Json voided();

inline Json status(bool ok) { return ok ? J() : raised(); }   // hal's None, or its raise
inline Json value(int v) { return v < 0 ? raised() : J(v); }   // -1: a bus error

// The pixel bytes of a strip the recorder pushes (trace_hal.py pattern()).
void pattern(int seed, uint8_t* out, size_t n);

// One scenario's watch: every part answers from the io of the call being
// replayed, and throws Mismatch at the first request the Python did not make.
class HalWorld {
 public:
  explicit HalWorld(uint64_t t0_ms) : t_us_(t0_ms * 1000) {}

  struct Bus : hm::I2c {
    Bus(HalWorld& w, int id) : w(w), id(id) {}
    bool write(uint8_t addr, uint8_t reg, const uint8_t* d, size_t n) override;
    bool read(uint8_t addr, uint8_t reg, uint8_t* d, size_t n) override;
    HalWorld& w;
    int id;
  };
  struct Lcd : hm::LcdBus {
    explicit Lcd(HalWorld& w) : w(w) {}
    bool set_clock(uint32_t) override;
    void command(uint8_t cmd, const uint8_t* d, size_t n) override;
    void pixels(uint8_t cmd, const uint16_t* px, size_t count) override;
    void wait() override {}
    HalWorld& w;
    bool in_ramwr = false;   // the panel takes pixels: a RAMWRC continues them
  };
  struct Backlight : hm::app::Pwm {
    explicit Backlight(HalWorld& w) : w(w) {}
    void set(double level) override;
    HalWorld& w;
  };
  struct Irq : hm::Line {
    explicit Irq(HalWorld& w) : w(w) {}
    bool low() override;
    HalWorld& w;
  };
  struct Clock : hm::Clock {
    explicit Clock(HalWorld& w) : w(w) {}
    uint32_t now_us() override { return (uint32_t)w.t_us_; }
    hm::ticks_t now_ms() override { return (hm::ticks_t)(w.t_us_ / 1000); }
    void delay_ms(uint32_t ms) override;
    void sleep_until_us(uint32_t t) override;
    HalWorld& w;
  };

  Bus i2c0{*this, 0}, i2c1{*this, 1};
  Lcd lcd{*this};
  Backlight backlight{*this};
  Irq irq{*this};
  Clock clock{*this};
  static void wait(void* world, int32_t ms);   // an app::WaitFn, the loop's idle: {"wait": ms}

  // A port's allowances: (bus << 16 | addr << 8 | reg) of registers whose
  // reads stream, and the Python events the C++ never makes (skip(ev) true).
  std::set<int> streams;
  std::function<bool(const Json&)> skip;

  void start(const Json& io);   // the call's recorded io
  void finish();                // throws if the Python did more
  void advance(uint64_t ms) { t_us_ += ms * 1000; }

 private:
  const Json& expect(const std::string& what);   // the next event; throws if there is none
  [[noreturn]] void differ(const std::string& what, const Json& ev);
  void pixel_bytes(const uint8_t* p, size_t n);
  const Json* io_ = nullptr;
  size_t next_ = 0;
  size_t part_ = 0;        // bytes of event next_ already served (a streaming read, pixels)
  uint32_t crc_ = 0;       // of the pixel bytes so far
  uint64_t t_us_;
};

// The BMA423's allowances (test_hal_bma423.cpp, test_hal_board.cpp): its
// FIFO_DATA streams (hm::Bma423::fifo_read reads it 120 bytes at a time,
// Arduino Wire's buffer; the Python in one burst); the C++ has no I2C scan (it
// is given 0x19, which the Python's scan finds first: any other address shows
// in the traffic after it); and it does not read INTERNAL_STATUS (0x2A, the
// feature engine's, not ported) at the end of its init.
void bma423_allowances(HalWorld& w);

// One driver's port: makes the driver (op "new", or the board's "init") and
// each recorded call on it.
struct HalPort {
  virtual ~HalPort() = default;
  virtual Json call(const std::string& op, const Json& a) = 0;   // throws Mismatch on an unknown op
  virtual Json state() = 0;                                        // the fields the recorder writes in "s"
};

// Replays HM_TRACES/hal.<driver>.jsonl, a fresh world and port per scenario;
// prints the first difference and returns false on it.
bool replay_hal(const char* driver, const std::function<std::unique_ptr<HalPort>(HalWorld&)>& make);

}  // namespace hmt

// The trace test of one driver: fails on a difference, or skips if python_changed().
#define CHECK_HAL(driver, make)                                                     \
  do {                                                                              \
    if (!hmt::replay_hal(driver, make)) {                                           \
      if (hmt::python_changed()) SKIP("the Python changed since the port matched it"); \
      CHECK(!"the driver differs from its trace");                                  \
    }                                                                               \
  } while (0)
