// hm::esp's game-loop parts (esp32_app.h) on the host: AppRadio drains frames
// as hal/radio.py's poll does. esp32_io.cpp needs ESP-IDF, so this file
// stands in for the two EspNowRadio calls AppRadio makes.
#include <string.h>

#include <vector>

#include "check.h"
#include "hm/esp32_app.h"

using hm::esp::EspNowRadio;

namespace {

std::vector<EspNowRadio::Frame> waiting;   // what the stand-in radio drains next
int sent = 0;

EspNowRadio::Frame frame(uint8_t tag, int8_t rssi, hm::ticks_t t) {
  EspNowRadio::Frame f = {};
  memset(f.mac, tag, 6);
  f.len = 16;
  memset(f.data, tag, f.len);
  f.rssi = rssi;
  f.t_ms = t;
  return f;
}

struct Got {
  std::vector<hm::app::RadioFrame> frames;
  std::vector<uint8_t> first_bytes;
};

void collect(void* ctx, const hm::app::RadioFrame& f) {
  Got& g = *static_cast<Got*>(ctx);
  g.frames.push_back(f);
  g.first_bytes.push_back(f.data[0]);
}

}  // namespace

namespace hm {
namespace esp {

bool EspNowRadio::send(const uint8_t*, size_t) { return ++sent, true; }

int EspNowRadio::poll(void (*fn)(void* ctx, const Frame& f), void* ctx) {
  int c = 0;
  for (; c < (int)waiting.size() && c < MAX_DRAIN; c++) fn(ctx, waiting[c]);
  waiting.erase(waiting.begin(), waiting.begin() + c);
  return c;
}

}  // namespace esp
}  // namespace hm

TEST(test_app_radio_drops_frames_without_rssi) {
  hm::esp::AppRadio r;
  waiting = {frame(1, -60, 1000), frame(2, EspNowRadio::RSSI_NONE, 1000), frame(3, -128 + 1, 1000)};
  Got g;
  CHECK(r.poll(1000, collect, &g) == 2 && waiting.empty());
  CHECK(g.frames.size() == 2 && g.first_bytes[0] == 1 && g.first_bytes[1] == 3);
  CHECK(g.frames[0].rssi == -60 && g.frames[1].rssi == -127 && g.frames[0].len == 16 && g.frames[0].mac[5] == 1);
  CHECK(r.send(nullptr, 0) && sent == 1);
}

TEST(test_app_radio_replaces_stale_and_future_timestamps) {
  // hal/radio.py: t & TICKS_MASK; ticks_diff(now, t) < 0 or > RX_TS_MAX_AGE -> now
  hm::esp::AppRadio r;
  const hm::ticks_t now = 50000;
  waiting = {frame(1, -70, now), frame(2, -70, now - 1000), frame(3, -70, now - 1001), frame(4, -70, now + 1),
             frame(5, -70, (1u << 30) + now - 20)};
  Got g;
  CHECK(r.poll(now, collect, &g) == 5);
  CHECK(g.frames[0].t_ms == now && g.frames[1].t_ms == now - 1000);   // fresh and exactly 1000 ms old: kept
  CHECK(g.frames[2].t_ms == now && g.frames[3].t_ms == now);          // older, or from the future: now
  CHECK(g.frames[4].t_ms == now - 20);                                // a 32-bit clock, masked to 2^30
}

TEST(test_app_radio_across_the_tick_wrap) {
  hm::esp::AppRadio r;
  const hm::ticks_t now = 30;   // just after the 2^30 wrap
  waiting = {frame(1, -70, (1u << 30) - 40), frame(2, -70, (1u << 30) - 2000)};
  Got g;
  CHECK(r.poll(now, collect, &g) == 2);
  CHECK(g.frames[0].t_ms == (1u << 30) - 40 && g.frames[1].t_ms == now);
}
