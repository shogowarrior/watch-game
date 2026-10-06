#include "hm/menu.h"

#include <stdio.h>
#include <string.h>

namespace hm {
namespace menu {

Menu::Menu() {
  for (int i = 0; i < N_ROWS; i++) labels_[i] = ROWS[i];
  for (int k = 0; k < VISIBLE; k++) rows[k] = ROWS[k];
  reset(0);
}

void Menu::reset(ticks_t t_ms) {
  is_open = false;
  sel = 0;
  top = 0;
  t_ = t_ms;
  confirm_t_.reset();
}

void Menu::open(ticks_t t_ms) {
  reset(t_ms);
  is_open = true;
}

void Menu::close() {
  is_open = false;
  confirm_t_.reset();
}

std::optional<int> Menu::next(ticks_t t_ms) {
  if (confirm_t_ && sel == END) return select(t_ms, END);
  confirm_t_.reset();
  sel = (int)floormod(sel + 1, N_ROWS);
  t_ = t_ms;
  if (sel < top)
    top = sel;
  else if (sel >= top + VISIBLE)
    top = sel - VISIBLE + 1;
  return std::nullopt;
}

void Menu::scroll(ticks_t t_ms, int d) {
  t_ = t_ms;
  confirm_t_.reset();
  int t = top + d;
  top = t = t < 0 ? 0 : t > TOP_MAX ? TOP_MAX : t;
  if (sel < t)
    sel = t;
  else if (sel >= t + VISIBLE)
    sel = t + VISIBLE - 1;
}

std::optional<int> Menu::tap(ticks_t t_ms, int32_t y) {
  for (int i = 0; i < VISIBLE; i++) {
    const int32_t y0 = T::MENU_ROWS_Y[i];
    if (y0 <= y && y < y0 + T::MENU_ROW_H) {
      sel = top + i;
      return select(t_ms, sel);
    }
  }
  return std::nullopt;
}

std::optional<int> Menu::select(ticks_t t_ms, int row) {
  t_ = t_ms;
  if (row != END) {
    confirm_t_.reset();
  } else if (!confirm_t_) {
    confirm_t_ = t_ms;   // asks first: SURE? PRESS
    return std::nullopt;
  }
  if (row == RESUME || row == END) close();
  return row;
}

void Menu::tick(ticks_t t_ms) {
  if (ticks_diff(t_ms, t_) > T::MENU_AUTOCLOSE_MS) t_ = ticks_add(t_ms, -T::MENU_AUTOCLOSE_MS);   // never a wrapped age
  if (!is_open) return;
  if (ticks_diff(t_ms, t_) >= T::MENU_AUTOCLOSE_MS)
    close();
  else if (confirm_t_ && ticks_diff(t_ms, *confirm_t_) >= T::MENU_CONFIRM_MS)
    confirm_t_.reset();
}

bool Menu::window(bool sun, int buzz, bool indoor, int32_t theme) {
  const char** r = labels_;
  r[SUN] = sun ? SUN_ON : ROWS[SUN];
  r[BUZZ] = BUZZ_ROWS[buzz];
  r[PLACE] = indoor ? PLACE_IN : ROWS[PLACE];
  r[THEME] = THEME_ROWS[theme];
  r[END] = confirm_t_ ? CONFIRM : ROWS[END];
  bool changed = false;
  for (int k = 0; k < VISIBLE; k++) {
    if (strcmp(rows[k], r[top + k]) != 0) changed = true;
    rows[k] = r[top + k];
  }
  return changed;
}

const char* Menu::sub() {
  snprintf(sub_, SUB_LEN, "%d%s%s", sel - top, top > 0 ? "^" : "", top < TOP_MAX ? "v" : "");
  return sub_;
}

}  // namespace menu
}  // namespace hm
