// hm::gestures by hand; native/test/test_port_motion.cpp replays its Python traces.
#include "check.h"
#include "hm/gestures.h"

namespace G = hm::gestures;

TEST(test_gestures_across_the_cpp_ticks_wrap) {
  // a clock that counts all 32 bits, across its wrap (the traces stay far below it)
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
