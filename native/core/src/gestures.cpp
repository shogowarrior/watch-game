#include "hm/gestures.h"

namespace hm {
namespace gestures {

void GestureRecognizer::reset() {
  down = false;
  began = false;
  x = y = 0;
  ev_x = ev_y = 0;
  ev_t = 0;
  up_ = false;
  t_up_ = t0_ = t_pre_ = 0;
  t_s_.reset();
  x0_ = y0_ = 0;
  moved_ = false;
  spent_ = false;
  ev_ = NONE;
}

int GestureRecognizer::update(ticks_t t, bool touching, int32_t x_, int32_t y_, bool multi) {
  began = false;
  if (down) {
    if (up_ && ticks_diff(t, t_up_) >= debounce_ms) {
      finish_();
    } else if (touching) {
      up_ = false;
      track_(t, x_, y_);
    } else if (!up_) {
      up_ = true;
      t_up_ = t;
    }
  }
  if (!down && touching) press_(t, x_, y_);
  if (multi && down) spent_ = true;   // ui-spec 8: ignore multi-touch
  t_s_ = t;
  const int e = ev_;
  ev_ = NONE;
  return e;
}

void GestureRecognizer::push_(int ev, int32_t x_, int32_t y_) {
  ev_ = ev;
  ev_x = x_;
  ev_y = y_;
  ev_t = t0_;
}

void GestureRecognizer::press_(ticks_t t, int32_t x_, int32_t y_) {
  down = true;
  began = true;
  up_ = false;
  t0_ = t;
  t_pre_ = t_s_ ? *t_s_ : t;
  x0_ = x_;
  y0_ = y_;
  moved_ = false;
  spent_ = false;
  track_(t, x_, y_);
}

void GestureRecognizer::track_(ticks_t t, int32_t x_, int32_t y_) {
  x = x_;
  y = y_;
  if (spent_) return;
  if (!moved_) {
    const int32_t dx = x_ - x0_, dy = y_ - y0_, s = slop_px;
    if (dx > s || dx < -s || dy > s || dy < -s) {
      moved_ = true;
    } else if (ticks_diff(t, t0_) >= long_ms) {
      spent_ = true;
      push_(LONG_PRESS, x0_, y0_);
    }
  }
}

void GestureRecognizer::finish_() {
  down = false;
  up_ = false;
  if (spent_) return;
  const int32_t dx = x - x0_, dy = y - y0_;
  const int32_t ax = dx >= 0 ? dx : -dx, ay = dy >= 0 ? dy : -dy;
  if (ax >= ay && ax >= swipe_px) {
    push_(dx > 0 ? SWIPE_R : SWIPE_L, x0_, y0_);
  } else if (ay > ax && ay >= swipe_px) {
    push_(dy > 0 ? SWIPE_D : SWIPE_U, x0_, y0_);
  } else if (!moved_) {   // a short drag gives nothing
    // sampled contact: it may have lasted from t_pre_ to t_up_
    if (ticks_diff(t_up_, t_pre_) >= tap_min_ms && ticks_diff(t_up_, t0_) <= tap_max_ms)
      push_(TAP, x0_, y0_);
  }
}

}  // namespace gestures
}  // namespace hm
