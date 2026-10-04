// The bench and the drivers it uses, on fakes: the commands match hal/*.py.
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "check.h"
#include "fakes.h"
#include "sf/axp202.h"
#include "sf/bench.h"
#include "sf/bma423.h"
#include "sf/st7789.h"

namespace sft {
std::vector<std::string> log_lines;
}

void sf::logf(const char* fmt, ...) {
  char buf[256];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  sft::log_lines.push_back(buf);
}

namespace {

int count_prefix(const char* prefix) {
  int n = 0;
  for (const std::string& l : sft::log_lines) n += l.compare(0, strlen(prefix), prefix) == 0;
  return n;
}

void no_backlight(bool) {}

}  // namespace

TEST(test_st7789_init_matches_hal) {
  // hal/st7789.py init(): SWRESET, SLPOUT, COLMOD 0x55, MADCTL 0xC0, INVON,
  // NORON, black GRAM in a full window (rows 80..319), DISPON.
  sft::FakeClock clock;
  sft::FakeLcd lcd(clock);
  lcd.set_clock(26666667);
  sf::St7789 panel(lcd, clock);
  static uint16_t strip[240 * 24];
  strip[5] = 0x1234;
  panel.init(strip, 24);
  const auto& o = lcd.ops;
  CHECK(o.size() == 6 + 2 + 10 + 1);
  const uint8_t cmds[] = {0x01, 0x11, 0x3A, 0x36, 0x21, 0x13, 0x2A, 0x2B};
  for (int i = 0; i < 8; i++) CHECK(o[i].cmd == cmds[i]);
  CHECK(o[2].data == std::vector<uint8_t>({0x55}));
  CHECK(o[3].data == std::vector<uint8_t>({0xC0}));
  CHECK(o[6].data == std::vector<uint8_t>({0, 0, 0, 239}));
  CHECK(o[7].data == std::vector<uint8_t>({0, 80, 1, 63}));
  CHECK(o[8].cmd == 0x2C && o[8].pixels == 240 * 24);
  for (int i = 9; i < 18; i++) CHECK(o[i].cmd == 0x3C && o[i].pixels == 240 * 24);
  CHECK(o[18].cmd == 0x29);
  CHECK(strip[5] == 0);
}

TEST(test_st7789_window_takes_pixels_in_chunks) {
  sft::FakeClock clock;
  sft::FakeLcd lcd(clock);
  lcd.set_clock(40000000);
  sf::St7789 panel(lcd, clock);
  static uint16_t px[900];
  panel.begin_window(30, 60, 30, 30);
  panel.push_pixels(px, 500);
  panel.push_pixels(px + 500, 400);
  const auto& o = lcd.ops;
  CHECK(o.size() == 4);
  CHECK(o[0].cmd == 0x2A && o[0].data == std::vector<uint8_t>({0, 30, 0, 59}));
  CHECK(o[1].cmd == 0x2B && o[1].data == std::vector<uint8_t>({0, 140, 0, 169}));   // GRAM rows 80..
  CHECK(o[2].cmd == 0x2C && o[2].pixels == 500);
  CHECK(o[3].cmd == 0x3C && o[3].pixels == 400);
}

TEST(test_axp202_turns_ldo2_on_and_keeps_dcdc3) {
  sft::FakeI2c i2c;
  i2c.regs[0x35 << 8 | 0x12] = 0x00;            // even if DCDC3 read back clear
  CHECK(sf::axp202::panel_power_on(i2c));
  CHECK(i2c.regs[0x35 << 8 | 0x12] == (0x04 | 0x02));
  CHECK(i2c.regs[0x35 << 8 | 0x28] == 0xF5);    // LDO2 3.3 V, LDO4 bits kept
  for (auto& w : i2c.writes)
    if (w.first == (0x35 << 8 | 0x12)) CHECK(w.second & 0x02);
}

TEST(test_bma423_init_matches_hal) {
  sft::FakeClock clock;
  sft::FakeI2c i2c;
  sf::Bma423 imu(i2c, clock);
  CHECK(!imu.init(300, 8));
  CHECK(imu.init(800, 8));
  // soft reset, then hal/bma423.py _configure() with odr 800 (code 11), +-8 g (code 2)
  const std::vector<std::pair<int, uint8_t>> want = {
      {0x19 << 8 | 0x7E, 0xB6}, {0x19 << 8 | 0x7C, 0x00}, {0x19 << 8 | 0x40, 0xAB}, {0x19 << 8 | 0x41, 0x02},
      {0x19 << 8 | 0x48, 0x00}, {0x19 << 8 | 0x49, 0x40}, {0x19 << 8 | 0x7D, 0x04}};
  CHECK(i2c.writes == want);
  CHECK(imu.range_mg() == 8000);
}

TEST(test_bma423_decode_matches_python) {
  // hal/bma423.py _DCHECK frames; expected values from decode_frames on CPython
  const uint8_t raw[] = {16, 0, 240, 255, 0, 32, 240, 127, 0, 128, 15, 0, 90, 195, 60, 165,
                         255, 1, 0, 128, 0, 128, 0, 128, 16, 0, 16, 0, 16, 0};
  const int16_t want4[] = {2, -2, 1000, 3998, -4000, 0, -1896, -2838, 61};
  const int16_t want16[] = {8, -8, 4000, 15992, -16000, 0, -7586, -11352, 242};
  int16_t out[15];
  CHECK(sf::Bma423::decode(raw, 5, out, 4000) == 3);
  CHECK(memcmp(out, want4, sizeof want4) == 0);
  CHECK(sf::Bma423::decode(raw, 5, out, 16000) == 3);
  CHECK(memcmp(out, want16, sizeof want16) == 0);
}

TEST(test_bench_runs_every_step) {
  sft::log_lines.clear();
  sft::FakeClock clock;
  sft::FakeLcd lcd(clock);
  lcd.max_hz = 40000000;                        // 80 MHz refused: logged, skipped
  sft::FakeI2c i2c;
  sft::FakeImuTask imu;
  sf::BenchHost host{"host", "g++", clock, lcd, i2c, imu, no_backlight};
  static sf::Bench bench(host);
  CHECK(bench.setup());
  bench.run();
  CHECK(sft::log_lines.back() == "SF done");
  CHECK(count_prefix("SF compose ") == 3);
  CHECK(count_prefix("SF push ") == 2);
  CHECK(count_prefix("SF window ") == 2 * 5);
  CHECK(count_prefix("SF run hz=40000000 target=") == 1 + 5);
  CHECK(count_prefix("SF run hz=26666667 target=") == 1 + 3);
  CHECK(count_prefix("SF error what=spi_clock hz=80000000") == 1 + 1 + 1 + 1);
  CHECK(count_prefix("SF imu ") == 2 && imu.starts == 1);
  for (const std::string& l : sft::log_lines) {
    if (l.compare(0, 9, "SF window") == 0)                       // each tiling covers the screen
      CHECK(l.find(" n=10 ") != std::string::npos || l.find(" n=4 ") != std::string::npos ||
            l.find(" n=16 ") != std::string::npos || l.find(" n=64 ") != std::string::npos ||
            l.find(" n=900 ") != std::string::npos);
    if (l.compare(0, 31, "SF run hz=40000000 target=30 fp") == 0) {   // fits: ~30 fps, no misses
      CHECK(l.find("fps=29.") != std::string::npos || l.find("fps=30.") != std::string::npos);
      CHECK(l.find("miss=0") != std::string::npos);
    }
  }
}
