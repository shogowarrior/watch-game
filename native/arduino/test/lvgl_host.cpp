// Host check for the LVGL env: frames drawn and sent through LVGL (lvgl_drawer.h)
// must land in the panel's memory exactly as the bench's own strip loop draws them.
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include <chrono>

#include "lvgl_drawer.h"
#include "hm/bench_scene.h"

void hm::logf(const char* fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  vprintf(fmt, ap);
  va_end(ap);
  putchar('\n');
}

namespace {

constexpr int W = hm::FIELD_W, GRAM_ROWS = 320;

// The ST7789's memory: CASET/RASET set the window, RAMWR starts it, RAMWRC continues.
struct GramLcd : hm::LcdBus {
  uint16_t gram[GRAM_ROWS][W] = {};
  int x0 = 0, x1 = 0, y0 = 0, y1 = 0, x = 0, y = 0, windows = 0;
  bool set_clock(uint32_t) override { return true; }
  void command(uint8_t cmd, const uint8_t* d, size_t n) override {
    if (n != 4) return;
    const int a = d[0] << 8 | d[1], b = d[2] << 8 | d[3];
    if (cmd == hm::St7789::CASET) x0 = a, x1 = b;
    if (cmd == hm::St7789::RASET) y0 = a, y1 = b, windows++;
  }
  void pixels(uint8_t cmd, const uint16_t* px, size_t count) override {
    if (cmd == hm::St7789::RAMWR) x = x0, y = y0;
    for (size_t i = 0; i < count && y <= y1; i++) {
      gram[y][x] = px[i];
      if (++x > x1) x = x0, y++;
    }
  }
  void wait() override {}
};

struct HostClock : hm::Clock {
  uint32_t ms = 0;
  uint32_t now_us() override { return ms * 1000; }
  hm::ticks_t now_ms() override { return (hm::ticks_t)ms; }
  void delay_ms(uint32_t d) override { ms += d; }
  void sleep_until_us(uint32_t) override {}
};

uint8_t ring_map[W * W];
uint16_t want[W * W];
GramLcd lcd;
HostClock clock_;
hm::RippleField field;

}  // namespace

int main() {
  hm::build_map(ring_map, W);
  hm::FieldScene scene(field);
  LvglDrawer drawer(lcd, clock_);
  if (!drawer.start()) return printf("LVGL start failed\n"), 1;
  int passed = 0, failed = 0;
  double lvgl_us = 0, blit_us = 0;
  const int frames = 60;
  for (int i = 0; i < frames; i++) {
    clock_.ms = 10000 + 33 * i;
    scene.step(hm::BENCH_FIXTURES[1], clock_.ms);   // HOT
    auto t0 = std::chrono::steady_clock::now();
    hm::blit(want, ring_map, field.pal, 0, W * W);
    auto t1 = std::chrono::steady_clock::now();
    lcd.windows = 0;
    drawer.frame(field.pal, ring_map);
    auto t2 = std::chrono::steady_clock::now();
    blit_us += std::chrono::duration<double, std::micro>(t1 - t0).count();
    lvgl_us += std::chrono::duration<double, std::micro>(t2 - t1).count();
    const bool same = memcmp(&lcd.gram[hm::St7789::ROW_OFFSET][0], want, sizeof want) == 0;
    const bool strips = lcd.windows == W / hm::Bench::SH;   // one window per 240x24 buffer
    if (same && strips) {
      passed++;
    } else {
      failed++;
      printf("FAIL frame %d: %s, %d windows\n", i, same ? "pixels match" : "pixels differ", lcd.windows);
    }
  }
  printf("host us/frame: blit %.0f, lvgl %.0f\n", blit_us / frames, lvgl_us / frames);
  printf("[lvgl-host] %d passed, 0 skipped, %d failed\n", passed, failed);
  return failed || !passed;
}
