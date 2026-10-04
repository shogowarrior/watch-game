// hm::menu by hand; native/test/test_port_menu.cpp replays its Python traces.
#include "check.h"
#include "hm/menu.h"

namespace M = hm::menu;

TEST(test_menu_timers_across_the_cpp_ticks_wrap) {
  // a clock that counts all 32 bits, across its wrap (the traces stay far below it)
  M::Menu m;
  const hm::ticks_t t = 0xFFFFFF9Cu;   // 100 ms before the wrap
  m.open(t);
  m.select(t, M::END);
  m.tick(t + hm::T::MENU_CONFIRM_MS - 1);
  CHECK(m.confirm_armed() && m.is_open);
  m.tick(t + hm::T::MENU_CONFIRM_MS);
  CHECK(!m.confirm_armed() && m.is_open);
  m.tick(t + hm::T::MENU_AUTOCLOSE_MS - 1);
  CHECK(m.is_open);
  m.tick(t + hm::T::MENU_AUTOCLOSE_MS);
  CHECK(!m.is_open);
}
