#include "sf/st7789.h"

#include <string.h>

namespace sf {

void St7789::init(uint16_t* black, int rows) {
  bus_.command(SWRESET, nullptr, 0);
  clock_.delay_ms(150);
  bus_.command(SLPOUT, nullptr, 0);
  clock_.delay_ms(120);                    // ST7789 minimum after SLPOUT
  const uint8_t colmod = 0x55;             // 16-bit RGB565
  bus_.command(COLMOD, &colmod, 1);
  clock_.delay_ms(10);
  bus_.command(MADCTL, &MADCTL_ROT2, 1);
  bus_.command(INVON, nullptr, 0);         // this IPS panel needs inversion on
  clock_.delay_ms(10);
  bus_.command(NORON, nullptr, 0);
  clock_.delay_ms(10);
  // GRAM is random after power-on and SWRESET keeps it: black before DISPON
  memset(black, 0, (size_t)W * rows * 2);
  begin_frame();
  for (int y = 0; y < H; y += rows) push_strip(black, rows);
  end_frame();
  bus_.command(DISPON, nullptr, 0);
  clock_.delay_ms(10);
}

void St7789::window(int x0, int y0, int x1, int y1) {
  const uint8_t c[4] = {(uint8_t)(x0 >> 8), (uint8_t)x0, (uint8_t)(x1 >> 8), (uint8_t)x1};
  bus_.command(CASET, c, 4);
  y0 += ROW_OFFSET;
  y1 += ROW_OFFSET;
  const uint8_t r[4] = {(uint8_t)(y0 >> 8), (uint8_t)y0, (uint8_t)(y1 >> 8), (uint8_t)y1};
  bus_.command(RASET, r, 4);
}

void St7789::begin_frame() {
  window(0, 0, W - 1, H - 1);
  first_ = true;
}

void St7789::push_strip(const uint16_t* px, int rows) {
  bus_.pixels(first_ ? RAMWR : RAMWRC, px, (size_t)W * rows);
  first_ = false;
}

}  // namespace sf
