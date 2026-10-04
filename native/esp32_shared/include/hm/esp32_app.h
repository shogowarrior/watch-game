// The game loop's parts (hm/platform.h) over the shared ESP32 code, so the
// Arduino and ESP-IDF shells hand the Runtime the same motor, backlight, radio
// and interrupt lines and only supply the buses and the clock.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "hm/esp32.h"
#include "hm/esp32_io.h"
#include "hm/hal.h"
#include "hm/platform.h"
#include "hm/ticks.h"

namespace hm {
namespace esp {

constexpr int32_t RX_TS_MAX_AGE = 1000;   // hal/radio.py: a driver timestamp older (or newer) is replaced by now

// The frame hal/radio.py's poll would hand on for f, drained at now; false: drop
// it (no RSSI: the IDF 4.4 radio header was not where expected, counted in hdr_bad).
inline bool app_frame(const EspNowRadio::Frame& f, ticks_t now, app::RadioFrame& out) {
  if (f.rssi == EspNowRadio::RSSI_NONE) return false;
  ticks_t t = f.t_ms & (TICKS_PERIOD - 1);
  const int32_t age = ticks_diff(now, t);
  if (age < 0 || age > RX_TS_MAX_AGE) t = now;
  out.mac = f.mac;
  out.data = f.data;
  out.len = f.len;
  out.rssi = f.rssi;
  out.t_ms = t;
  return true;
}

// ESP-NOW for the Runtime: begin() and stats() on hw.
class AppRadio : public app::Radio {
 public:
  EspNowRadio hw;

  const uint8_t* mac() const override { return hw.mac(); }
  bool send(const uint8_t* buf, size_t n) override { return hw.send(buf, n); }
  int poll(ticks_t now, void (*fn)(void* ctx, const app::RadioFrame& f), void* ctx) override {
    Drain d{now, fn, ctx, 0};
    hw.poll(&AppRadio::each, &d);
    return d.n;
  }

 private:
  struct Drain {
    ticks_t now;
    void (*fn)(void* ctx, const app::RadioFrame& f);
    void* ctx;
    int n;
  };
  static void each(void* p, const EspNowRadio::Frame& f) {
    Drain& d = *static_cast<Drain*>(p);
    app::RadioFrame out;
    if (!app_frame(f, d.now, out)) return;
    d.fn(d.ctx, out);
    d.n++;
  }
};

// The vibration motor for the Runtime: begin() on hw.
class AppMotor : public app::Motor {
 public:
  esp::Motor hw;   // esp::: the base's name hides it here

  void set(double strength) override { hw.set((float)strength); }
};

// The GPIO12 backlight, for the core's Display.
class AppBacklight : public app::Pwm {
 public:
  void set(double level) override { backlight_level(level); }
};

// An interrupt line (lines_begin() first), for the core's Pmu and Touch.
class IrqLine : public hm::Line {
 public:
  explicit IrqLine(esp::Line l) : l_(l) {}   // esp::: the base's name hides the enum here
  bool low() override { return asserted(l_); }

 private:
  esp::Line l_;
};

}  // namespace esp
}  // namespace hm
