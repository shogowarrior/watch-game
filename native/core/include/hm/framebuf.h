// The parts of MicroPython's framebuf (extmod/modframebuf.c, v1.29.0) the
// renderer draws with, RGB565 only and pixel for pixel: fill_rect, pixel,
// line, poly (filled or outline) and ellipse (filled or outline, quadrant
// mask). Colours are stored as given: the byte-swapped RGB565 ints that
// framebuf stores little-endian, so the bytes go to the ST7789 MSB first.
//
// A FrameBuffer is rows y0..y0+h-1 of the 240-wide screen and takes screen
// coordinates, clipping everything outside its rows. Each primitive writes
// one colour and every row it touches depends only on that row, so a frame
// drawn strip by strip has exactly the pixels of the frame drawn whole; poly
// skips the rows outside the strip instead of scanning them all as framebuf
// does.
#pragma once
#include <stdint.h>

namespace hm {
namespace ui {

// ellipse() quadrant mask: Q2 Q1 / Q3 Q4.
constexpr int ELLIPSE_Q1 = 1, ELLIPSE_Q2 = 2, ELLIPSE_Q3 = 4, ELLIPSE_Q4 = 8, ELLIPSE_ALL = 15;
constexpr int POLY_MAX = 40;   // vertices poly() takes (framebuf has no limit; the renderer's most is 34)

class FrameBuffer {
 public:
  static constexpr int W = 240;
  // px holds W*h pixels: screen rows y0..y0+h-1.
  FrameBuffer(uint16_t* px, int y0, int h) : px_(px), y0_(y0), y1_(y0 + h) {}

  int y0() const { return y0_; }
  int y1() const { return y1_; }   // one past the last row
  void pixel(int x, int y, uint16_t c) {
    if (0 <= x && x < W && y0_ <= y && y < y1_) px_[(y - y0_) * W + x] = c;
  }
  void fill_rect(int x, int y, int w, int h, uint16_t c);
  void line(int x1, int y1, int x2, int y2, uint16_t c);
  // n vertices xy[2k], xy[2k+1], offset by (x, y).
  void poly(int x, int y, const int16_t* xy, int n, uint16_t c, bool fill);
  void ellipse(int cx, int cy, int xr, int yr, uint16_t c, bool fill, int mask = ELLIPSE_ALL);

 private:
  uint16_t* px_;
  int y0_, y1_;
};

}  // namespace ui
}  // namespace hm
