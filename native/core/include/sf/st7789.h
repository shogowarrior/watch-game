// ST7789 240x240 panel protocol for the T-Watch 2020 V1, the same commands
// as hal/st7789.py so every runtime shows the same picture: no RST pin
// (software reset), RGB565, MADCTL MY|MX with GRAM rows 80..319, inversion on.
#pragma once
#include <stdint.h>

#include "sf/hal.h"

namespace sf {

class St7789 {
 public:
  static constexpr int W = 240, H = 240;
  static constexpr uint8_t SWRESET = 0x01, SLPOUT = 0x11, NORON = 0x13, INVON = 0x21, DISPON = 0x29,
                           CASET = 0x2A, RASET = 0x2B, RAMWR = 0x2C, MADCTL = 0x36, RAMWRC = 0x3C,
                           COLMOD = 0x3A;
  static constexpr uint8_t MADCTL_ROT2 = 0xC0;   // MY|MX: this watch's upright orientation ...
  static constexpr int ROW_OFFSET = 80;          // ... shows GRAM rows 80..319 of 320

  St7789(LcdBus& bus, Clock& clock) : bus_(bus), clock_(clock) {}
  // SWRESET + init; the panel supply (AXP202 LDO2) must already be on. Blocks ~300 ms.
  // ``black`` is a strip of ``rows`` rows used to clear GRAM before DISPON.
  void init(uint16_t* black, int rows);
  // A window of w x h pixels at (x, y); push_pixels then fills it row by row,
  // in as many calls as the caller likes (see LcdBus::pixels for buffer reuse).
  void begin_window(int x, int y, int w, int h);
  void push_pixels(const uint16_t* px, size_t n);
  void begin_frame() { begin_window(0, 0, W, H); }   // strips follow top to bottom
  void push_strip(const uint16_t* px, int rows) { push_pixels(px, (size_t)W * rows); }
  void end_frame() { bus_.wait(); }

 private:
  void window(int x0, int y0, int x1, int y1);
  LcdBus& bus_;
  Clock& clock_;
  bool first_ = true;     // next strip opens the RAMWR
};

}  // namespace sf
