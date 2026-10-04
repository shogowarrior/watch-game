// finder/gestures.py: touch gesture recognizer (ui-spec 8), allocation-free.
//
// Feed every touch sample: update(t_ms, touching, x, y, multi); it returns an
// event code (NONE = 0) and sets ev_x/ev_y to where the gesture started and
// ev_t to when its press landed (began is true on the sample that starts a
// press). At most one event falls due per sample.
//
// Rules (constructor arguments; long_ms, slop_px and the tap window default to
// tuning.h, swipe_px 40 and debounce_ms 60 are spec-silent):
//  * release gaps shorter than debounce_ms are touch dropouts and are bridged;
//    a real release is therefore reported debounce_ms after lift
//  * LONG_PRESS fires while still held, once long_ms has passed without moving
//    more than slop_px; nothing else fires for that press
//  * SWIPE_L/R/U/D on release when the start->end move on the dominant axis is
//    >= swipe_px; screen y grows downwards
//  * TAP: a press that stayed within slop_px and lasted tap_min_ms..tap_max_ms.
//    Samples can be a ~50 ms frame apart, so the floor is checked against the
//    longest the contact can have lasted (last untouched to first lifted
//    sample), the ceiling against first touching to first lifted sample.
//    Shorter contacts (rain) and 401..799 ms holds give nothing
//  * a press that ever reports multi (two fingers) gives nothing
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/ticks.h"
#include "hm/tuning.h"

namespace hm {
namespace gestures {

constexpr int NONE = 0;
constexpr int TAP = 1;
constexpr int DOUBLE_TAP = 2;   // retired (never emitted); the slot keeps NAMES indices stable
constexpr int LONG_PRESS = 3;
constexpr int SWIPE_L = 4;
constexpr int SWIPE_R = 5;
constexpr int SWIPE_U = 6;
constexpr int SWIPE_D = 7;
constexpr const char* const NAMES[8] = {"NONE", "TAP", "DOUBLE_TAP", "LONG_PRESS",
                                        "SWIPE_L", "SWIPE_R", "SWIPE_U", "SWIPE_D"};

class GestureRecognizer {
 public:
  explicit GestureRecognizer(int32_t long_ms = T::LONG_PRESS_MS, int32_t swipe_px = 40, int32_t debounce_ms = 60,
                             int32_t slop_px = T::TAP_MOVE_PX, int32_t tap_min_ms = T::TAP_MIN_MS,
                             int32_t tap_max_ms = T::TAP_MAX_MS)
      : long_ms(long_ms), swipe_px(swipe_px), debounce_ms(debounce_ms), slop_px(slop_px),
        tap_min_ms(tap_min_ms), tap_max_ms(tap_max_ms) {
    reset();
  }

  // Forget any press and a not yet returned event.
  void reset();
  // Feed one sample; returns the event code or NONE.
  int update(ticks_t t, bool touching, int32_t x = 0, int32_t y = 0, bool multi = false);

  int32_t long_ms, swipe_px, debounce_ms, slop_px, tap_min_ms, tap_max_ms;
  bool down = false;      // debounced finger state
  bool began = false;     // the last sample started a press (touch-down)
  int32_t x = 0, y = 0;   // last touching position
  int32_t ev_x = 0, ev_y = 0;
  ticks_t ev_t = 0;       // when the gesture's press landed

 private:
  void push_(int ev, int32_t x, int32_t y);
  void press_(ticks_t t, int32_t x, int32_t y);
  void track_(ticks_t t, int32_t x, int32_t y);
  void finish_();

  bool up_ = false;       // lifted, still inside the debounce gap
  ticks_t t_up_ = 0, t0_ = 0;
  ticks_t t_pre_ = 0;     // last sample before the press
  std::optional<ticks_t> t_s_;   // last sample time (none before the first)
  int32_t x0_ = 0, y0_ = 0;
  bool moved_ = false;
  bool spent_ = false;    // long press fired or multi-touch: the press is spent
  int ev_ = NONE;
};

}  // namespace gestures
}  // namespace hm
