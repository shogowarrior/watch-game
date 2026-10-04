// hm::gestures against finder/gestures.py (native/test/golden/gestures.txt).
#include "check.h"
#include "golden.h"
#include "hm/gestures.h"

using hmt::num;
namespace G = hm::gestures;

TEST(test_gestures_match_python) {
  int news = 0, resets = 0, samples = 0;
  int events[8] = {};
  G::GestureRecognizer g;
  for (const hmt::Tokens& t : hmt::golden("gestures")) {
    if (t[0] == "g_new") {
      g = G::GestureRecognizer(num(t[1]), num(t[2]), num(t[3]), num(t[4]), num(t[5]), num(t[6]));
      news++;
    } else if (t[0] == "g_reset") {
      g.reset();
      resets++;
    } else if (t[0] == "s") {
      // s <t> <touching> <x> <y> <multi> -> <event> <ev_x> <ev_y> <ev_t> <down> <began> <x> <y>
      const int e = g.update((hm::ticks_t)num(t[1]), num(t[2]), num(t[3]), num(t[4]), num(t[5]));
      CHECK(num(t[7]) == e);
      CHECK(num(t[8]) == g.ev_x && num(t[9]) == g.ev_y && num(t[10]) == (long)g.ev_t);
      CHECK(num(t[11]) == g.down && num(t[12]) == g.began && num(t[13]) == g.x && num(t[14]) == g.y);
      events[e]++;
      samples++;
    }
  }
  CHECK(news == 46 && resets == 2 && samples == 1185);
  // every event but the retired DOUBLE_TAP shows up
  CHECK(events[G::TAP] == 24 && events[G::LONG_PRESS] == 5 && events[G::SWIPE_L] == 3 &&
        events[G::SWIPE_R] == 3 && events[G::SWIPE_U] == 1 && events[G::SWIPE_D] == 2 &&
        events[G::DOUBLE_TAP] == 0);
}

TEST(test_gestures_across_the_cpp_ticks_wrap) {
  // C++ ticks wrap at 2^32 (the Python's at 2^30, which the golden file never reaches)
  const hm::ticks_t base = 0xFFFFFF00u;
  G::GestureRecognizer g;
  int got = G::NONE;
  hm::ticks_t at = 0;
  for (hm::ticks_t dt = 0; dt < 1000 && got == G::NONE; dt += 10) {
    got = g.update(base + dt, true, 50, 60);
    at = dt;
  }
  CHECK(got == G::LONG_PRESS && at == 800 && g.ev_t == base);
  g.reset();
  for (hm::ticks_t dt = 250; dt < 350; dt += 10) g.update(base + dt, true);
  got = G::NONE;
  for (hm::ticks_t dt = 350; dt < 600 && got == G::NONE; dt += 10) {
    got = g.update(base + dt, false);
    at = dt;
  }
  CHECK(got == G::TAP && at == 410 && g.ev_t == base + 250);
  CHECK(G::NAMES[G::SWIPE_D][6] == 'D' && sizeof(G::NAMES) / sizeof(G::NAMES[0]) == 8);
}
