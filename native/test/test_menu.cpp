// hm::menu against finder/menu.py (native/test/golden/menu.txt).
#include <optional>
#include <string>

#include "check.h"
#include "golden.h"
#include "hm/menu.h"

using hmt::num;
namespace M = hm::menu;

namespace {

// The golden file prints labels with '_' for their spaces.
std::string token(const char* label) {
  std::string s(label);
  for (char& c : s)
    if (c == ' ') c = '_';
  return s;
}

// is_open sel top confirm_armed sub rows[0..3], from t[k] on.
bool state_is(M::Menu& m, const hmt::Tokens& t, size_t k) {
  if (!(num(t[k]) == m.is_open && num(t[k + 1]) == m.sel && num(t[k + 2]) == m.top &&
        num(t[k + 3]) == m.confirm_armed() && t[k + 4] == m.sub()))
    return false;
  for (int i = 0; i < M::VISIBLE; i++)
    if (t[k + 5 + i] != token(m.rows[i])) return false;
  return t.size() == k + 5 + M::VISIBLE;
}

bool action_is(const std::string& want, std::optional<int> got) {
  return hmt::none(want) ? !got.has_value() : got.has_value() && *got == num(want);
}

}  // namespace

TEST(test_menu_matches_python) {
  int n = 0, actions = 0, changes = 0;
  M::Menu m;
  for (const hmt::Tokens& t : hmt::golden("menu")) {
    const std::string& op = t[0];
    const hm::ticks_t at = t.size() > 1 ? (hm::ticks_t)num(t[1]) : 0;
    if (op == "menu_new") {
      m = M::Menu();
      CHECK(state_is(m, t, 2));
    } else if (op == "open") {
      m.open(at);
      CHECK(state_is(m, t, 3));
    } else if (op == "close") {
      m.close();
      CHECK(state_is(m, t, 2));
    } else if (op == "next") {
      const std::optional<int> a = m.next(at);
      CHECK(action_is(t[3], a) && state_is(m, t, 4));
      actions += a.has_value();
    } else if (op == "scroll") {
      m.scroll(at, (int)num(t[2]));
      CHECK(state_is(m, t, 4));
    } else if (op == "tap") {
      const std::optional<int> a = m.tap(at, num(t[2]));
      CHECK(action_is(t[4], a) && state_is(m, t, 5));
      actions += a.has_value();
    } else if (op == "select") {
      const std::optional<int> a = m.select(at, (int)num(t[2]));
      CHECK(action_is(t[4], a) && state_is(m, t, 5));
      actions += a.has_value();
    } else if (op == "tick") {
      m.tick(at);
      CHECK(state_is(m, t, 3));
    } else if (op == "setsel") {
      m.sel = (int)num(t[1]);
      CHECK(state_is(m, t, 3));
    } else if (op == "window") {
      const bool changed = m.window(num(t[1]), (int)num(t[2]), num(t[3]));
      CHECK(num(t[5]) == changed && state_is(m, t, 6));
      changes += changed;
    } else {
      CHECK(false);   // an op this test does not know
    }
    n++;
  }
  CHECK(n == 132 && actions == 16 && changes == 29);
}

TEST(test_menu_timers_across_the_cpp_ticks_wrap) {
  // C++ ticks wrap at 2^32 (the Python's at 2^30, which the golden file never reaches)
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
