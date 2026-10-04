// The watch's parts as the game loop (hm::app::Runtime, a port of
// app/runtime.py) uses them: the methods of hal/*.py that app/runtime.py
// calls, with a status return where the Python raises OSError (the core is
// built without exceptions).
//
// The core implements Pmu, Display, Imu and Touch over the buses (the AXP202,
// ST7789, BMA423 and FT6336 register sequences of hal/*.py); a shell gives
// those buses (hm::I2c, hm::LcdBus), the backlight and motor PWM, the radio
// (hm::esp::EspNowRadio on both ESP32 builds) and the clock, then loops:
//
//     hm::app::Runtime rt(parts, clock);
//     rt.begin();
//     for (;;) { feed_watchdog(); rt.idle(rt.step()); }
//
// native/test fakes every part (as tests/test_app_runtime.py does). A part
// that is absent is a null pointer in Parts, as Runtime(parts=...) leaves it
// None. Ticks are ms with MicroPython's 2^30 period (hm/ticks.h).
//
// Two deliberate differences from the Python: the BMA423 feature engine is
// not used (the Runtime takes the Python's bare-FIFO path: steps and activity
// from hm::motion, no wrist-raise wake), and the shell's watchdog is always
// the hardware one (the Python starts with a stoppable timer watchdog on USB).
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "hm/ticks.h"

namespace hm {
namespace app {

// Pmu::poll() events (hal/axp202.py EV_*).
constexpr int EV_SHORT = 0x01;      // PEK short press (on release)
constexpr int EV_LONG = 0x02;       // PEK held past set_long_press_ms
constexpr int EV_VBUS_IN = 0x08;
constexpr int EV_VBUS_OUT = 0x10;
constexpr int EV_PRESS = 0x20;      // PEK pushed down
constexpr int EV_RELEASE = 0x40;    // PEK let go

// The AXP202 power chip (hal/axp202.py AXP202). -1 or false: a bus error.
// The core's takes the IRQ line (hm::Line, GPIO35) as hal's irq_pin: poll()
// reads no register while it is high.
struct Pmu {
  virtual ~Pmu() = default;
  virtual bool set_long_press_ms(int32_t ms) = 0;
  virtual bool clear_irqs() = 0;
  virtual int poll() = 0;              // EV_* seen since the last poll (and cleared)
  virtual int battery_percent() = 0;   // 0..100
  virtual int battery_voltage() = 0;   // mV
  virtual int is_charging() = 0;       // 0 or 1
  virtual int vbus_present() = 0;      // 0 or 1
  virtual bool shutdown() = 0;         // everything off but the RTC (game.power_off only)
};

// A wait that keeps the loop's motor timing (Runtime::idle), for the panel's
// sleep-out delay.
using WaitFn = void (*)(void* ctx, int32_t ms);

// The ST7789 panel and its backlight (hal/st7789.py ST7789).
struct Display {
  virtual ~Display() = default;
  // Rows y0..y0+h-1, 240 pixels each, byte-swapped RGB565 (what framebuf
  // holds). It may return while px is still being sent (DMA): px may be
  // written again once a later push_strip() or flush() has returned.
  virtual void push_strip(int y0, int h, const uint16_t* px) = 0;
  virtual void flush() = 0;                                     // every pixel is on the panel
  virtual void sleep() = 0;                                     // backlight 0, DISPOFF + SLPIN
  // SLPOUT + DISPON, then the backlight at level (the Runtime passes 0: lit
  // over the first fresh frame; hal's level=None is not needed).
  virtual void wake(double level, WaitFn wait, void* ctx) = 0;
  virtual void brightness(double level) = 0;                    // backlight 0..1
  bool asleep = false;
};

// The BMA423 FIFO (hal/bma423.py BMA423; the feature engine is not used: steps
// and activity come from hm::motion::MotionTracker).
struct Imu {
  static constexpr int FIFO_FRAMES = 170;   // 1024-byte FIFO, 6 bytes a frame
  virtual ~Imu() = default;
  virtual int fifo_read_mg() = 0;           // frames now in fifo_mg (x, y, z each, milli-g); -1: a bus error
  virtual bool set_odr(int32_t hz) = 0;     // and empty the FIFO; odr is the rate the chip is at
  int16_t fifo_mg[3 * FIFO_FRAMES] = {};
  int32_t odr = 100;
  int z_sign = 1;                           // hal/pins.py BMA423_Z_SIGN
};

// One touch-panel reading (hal/ft6336.py read()): x and y keep their last
// value while not touching; contacts 2 is two fingers or a palm.
struct TouchPoint {
  bool touching = false;
  int32_t x = 0, y = 0;
  int contacts = 0;
};

// The FT6336 touch panel (hal/ft6336.py FT6336).
struct Touch {
  virtual ~Touch() = default;
  virtual const TouchPoint& read() = 0;   // a bus error reads as not touching, counted in errors
  uint32_t errors = 0;
};

// The vibration motor (hal/haptics.py Motor).
struct Motor {
  virtual ~Motor() = default;
  virtual void set(double strength) = 0;   // 0..1
};

// A PWM output a shell drives, for the core's Display: the backlight on
// GPIO12, which must stay low at reset (a strapping pin, AGENTS.md rule 14).
struct Pwm {
  virtual ~Pwm() = default;
  virtual void set(double level) = 0;      // duty 0..1
};

// One received frame, valid during the poll callback. As hal/radio.py does,
// the Radio drops frames without an RSSI and gives a frame whose driver
// timestamp is in the future or over 1000 ms old (RX_TS_MAX_AGE) now instead.
struct RadioFrame {
  const uint8_t* mac;   // 6 bytes
  const uint8_t* data;
  int len;
  int rssi;             // dBm
  ticks_t t_ms;         // when it arrived
};

// ESP-NOW broadcast (hal/radio.py): sending and draining. The beacon schedule
// (finder/link.py TxScheduler) is the Runtime's.
struct Radio {
  virtual ~Radio() = default;
  virtual const uint8_t* mac() const = 0;   // 6 bytes
  virtual bool send(const uint8_t* buf, size_t n) = 0;
  // Hands waiting frames to fn, oldest first, at most 32; returns how many.
  virtual int poll(ticks_t now, void (*fn)(void* ctx, const RadioFrame& f), void* ctx) = 0;
};

struct Parts {
  Pmu* pmu = nullptr;
  Display* display = nullptr;
  Imu* imu = nullptr;
  Touch* touch = nullptr;
  Motor* motor = nullptr;
  Radio* radio = nullptr;
};

}  // namespace app
}  // namespace hm
