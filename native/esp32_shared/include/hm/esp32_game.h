// The game on both ESP32 builds: hal/board.py's bring-up over a shell's buses,
// then app/runtime.py's loop (hm::runtime::Runtime) as main.py runs it.
//
//     hm::esp::GameBuses b;                 // the buses that started
//     if (i2c0_ok) b.i2c0 = &i2c0;
//     ...
//     hm::esp::start_game(b);               // after the shell's "HM hello"
//
// The shell starts its buses, and the task watchdog if its framework does not
// (ESP-IDF does from sdkconfig); start_game does the rest on a core-1 task
// (Wi-Fi and the system tasks keep core 0): the parts in hal/board.py's ORDER
// (pmu, display, backlight, imu, touch, haptics, radio), each left out if it
// does not come up, as Board.init(strict=False) does, and an "HM parts" line
// naming which did; then the loop, which feeds the task watchdog once per pass
// and prints an "HM fps" line every 10 s (main.py's fps_log_ms). Ten seconds
// in, an "HM mem" line gives the loop task's unused stack and the free heap.
#pragma once
#include "hm/hal.h"

namespace hm {
namespace esp {

struct GameBuses {
  I2c* i2c0 = nullptr;     // pins 21/22: the AXP202 and the BMA423 (nullptr: the bus did not start)
  I2c* i2c1 = nullptr;     // pins 23/32: the FT6336
  LcdBus* lcd = nullptr;   // the ST7789 on SPI, no MISO
  bool radio = true;       // false: no ESP-NOW (QEMU has no Wi-Fi model)
};

// Starts the game task; returns at once. Call once.
void start_game(const GameBuses& b);

// The panel bus of the QEMU builds: Espressif's QEMU has no SPI DMA, so a real
// push never finishes; this one sends the pixels nowhere.
struct NullLcdBus final : LcdBus {
  bool set_clock(uint32_t) override { return true; }
  void command(uint8_t, const uint8_t*, size_t) override {}
  void pixels(uint8_t, const uint16_t*, size_t) override {}
  void wait() override {}
};

}  // namespace esp
}  // namespace hm
