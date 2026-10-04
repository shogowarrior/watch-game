// finder/menu.py: MENU overlay state (ui-spec MENU), a 5-row list, 4 rows
// visible, that scrolls.
//
// The Game owns one Menu. It opens and closes it, passes input on (next /
// scroll / tap / select) and applies the row action those return (SUN, BUZZ,
// PLACE, END once confirmed; RESUME only closes). END ROUND asks first: the
// first select arms "SURE? PRESS" for MENU_CONFIRM_MS; any other row, a swipe
// or moving off the row cancels it. The menu closes itself MENU_AUTOCLOSE_MS
// after the last input. window() rebuilds the labels and the visible rows at
// tick time only, so the renderer's rows and sub always describe one window.
// Labels are static literals.
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/ticks.h"
#include "hm/tuning.h"

namespace hm {
namespace menu {

constexpr int N_ROWS = 5;
constexpr const char* const ROWS[N_ROWS] = {"RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: OUT", "END ROUND"};
constexpr int RESUME = 0, SUN = 1, BUZZ = 2, PLACE = 3, END = 4;   // row index = the action select returns
constexpr int VISIBLE = (int)(sizeof(T::MENU_ROWS_Y) / sizeof(T::MENU_ROWS_Y[0]));   // rows on screen
constexpr int TOP_MAX = N_ROWS - VISIBLE;
constexpr const char* CONFIRM = "SURE? PRESS";
constexpr const char* const BUZZ_ROWS[3] = {"BUZZ: FULL", "BUZZ: EVENTS", "BUZZ: OFF"};   // index = game BUZZ_*
constexpr const char* SUN_ON = "SUN: ON";
constexpr const char* PLACE_IN = "PLACE: IN";
constexpr int SUB_LEN = 16;

// Open flag, selected row sel, first visible row top, the END ROUND confirm
// and the auto-close timer.
class Menu {
 public:
  Menu();
  void reset(ticks_t t_ms);
  void open(ticks_t t_ms);
  void close();

  // ---- input
  // Short press: confirms an armed END ROUND on its row, else selects the next
  // row (moving off END ROUND cancels SURE? PRESS). Returns an action or none.
  std::optional<int> next(ticks_t t_ms);
  // Swipe: show d rows further down (negative: up); the selection follows into
  // view. Cancels a pending confirm (SURE? PRESS may scroll away).
  void scroll(ticks_t t_ms, int d);
  // Tap at screen y: selects the visible row there; returns its action.
  std::optional<int> tap(ticks_t t_ms, int32_t y);
  // Long press (or tap) on row; returns the action or none. RESUME and a
  // confirmed END ROUND close the menu.
  std::optional<int> select(ticks_t t_ms, int row);

  // ---- per tick
  // Auto-close and the confirm timeout; caps the input stamp's age.
  void tick(ticks_t t_ms);
  // Row labels for these settings (buzz: game BUZZ_*, 0..2), and the visible rows;
  // returns true when a visible label changed (the Python builds a new rows tuple then).
  bool window(bool sun, int buzz, bool indoor);
  // RenderParams.sub for MENU: the visible index of sel, then '^'/'v' when
  // rows are hidden above/below. Formats into a member buffer.
  const char* sub();
  // END ROUND is armed (SURE? PRESS): the Python's _confirm_t is not None (for tests).
  bool confirm_armed() const { return confirm_t_.has_value(); }

  bool is_open = false;
  int sel = 0, top = 0;
  const char* rows[VISIBLE];   // visible window (rebuilt by window)

 private:
  const char* labels_[N_ROWS];
  ticks_t t_ = 0;                       // last input (auto-close)
  std::optional<ticks_t> confirm_t_;    // END ROUND armed at
  char sub_[SUB_LEN] = {};
};

}  // namespace menu
}  // namespace hm
