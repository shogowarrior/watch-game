#include "hm/pacer.h"

#include <math.h>

namespace hm {
namespace pacer {

void FramePacer::reset(ticks_t now, int32_t cap) {
  i = cap_index_(cap ? cap : T::FPS_LOCKS[0]);
  fps = T::FPS_LOCKS[i];
  period = 1000 / fps;
  t_next = now;
  k_ = n_ = 0;
  cost = 0;
  up_t_.reset();
  skip_ = false;
  anchor_ = false;
  last_.reset();
  changes = 0;
  window();
}

void FramePacer::restart(ticks_t now) {
  t_next = now;
  anchor_ = true;
  skip_ = true;
  last_.reset();
}

int FramePacer::cap_index_(int32_t cap) {
  int j = 0;
  while (j < N_LOCKS - 1 && T::FPS_LOCKS[j] > cap) j++;
  return j;
}

ticks_t FramePacer::begin(ticks_t now, int32_t cap, std::optional<int32_t> busy_ms) {
  if (busy_ms) {
    if (skip_) {
      skip_ = false;
    } else {
      note_(*busy_ms);
      choose_(now, cap);
    }
  } else {
    const int ic = cap_index_(cap);
    if (ic > i) set_(ic);   // the cap still applies with the screen off
  }
  if (anchor_) {
    anchor_ = false;
    t_next = now;
  }
  const int32_t per_old = period;
  int32_t late = ticks_diff(now, t_next);
  if (late < 0) late = 0;   // called early (tests): draw the slot anyway
  const int32_t k = late / per_old;
  const ticks_t slot = ticks_add(t_next, k * per_old);
  late -= k * per_old;
  missed += k;
  t_next = ticks_add(slot, period);
  if (busy_ms) {
    late_sum += late;
    if (late > late_max) late_max = late;
  }
  return slot;
}

void FramePacer::shown(ticks_t t) {
  frames++;
  const opt_ticks last = last_;
  last_ = t;
  if (!last) return;
  const int32_t d = ticks_diff(t, *last);
  iv_n++;
  iv_sum += d;
  iv_sq += (int64_t)d * d;
  if (d > iv_max) iv_max = d;
  if (iv_sq > SUM_LIMIT) {   // no log reading the window: stay small ints
    iv_n >>= 1;
    iv_sum >>= 1;
    iv_sq >>= 1;
  }
}

void FramePacer::note_(int32_t busy_ms) {
  const int32_t old = cost_[k_];
  cost_[k_] = busy_ms;
  k_ = (k_ + 1) % N_COST;
  if (n_ < N_COST) {
    n_++;
  } else if (busy_ms < cost && old < cost) {
    return;   // neither touches the top two: cost unchanged
  }
  int32_t a = 0, b = 0;   // largest, second largest
  for (int j = 0; j < n_; j++) {
    const int32_t v = cost_[j];
    if (v > a) {
      b = a;
      a = v;
    } else if (v > b) {
      b = v;
    }
  }
  cost = n_ > 1 ? b : a;
}

void FramePacer::choose_(ticks_t now, int32_t cap) {
  const int ic = cap_index_(cap);
  if (ic > i || cost > period) {   // over the cap, or missing slots: drop now
    int j = ic > i ? ic : i;
    while (j < N_LOCKS - 1 && cost > 1000 / T::FPS_LOCKS[j]) j++;
    set_(j);
    up_t_.reset();
    return;
  }
  if (i > ic && cost * (100 + T::FPS_MARGIN_PCT) <= (1000 / T::FPS_LOCKS[i - 1]) * 100) {
    if (!up_t_) {
      up_t_ = now;
    } else if (ticks_diff(now, *up_t_) >= T::FPS_RAISE_MS) {
      set_(i - 1);
      up_t_.reset();
    }
  } else {
    up_t_.reset();
  }
}

void FramePacer::set_(int j) {
  if (j != i) {
    i = j;
    fps = T::FPS_LOCKS[j];
    period = 1000 / fps;
    changes++;
  }
}

void FramePacer::window() {
  frames = missed = late_sum = late_max = 0;
  iv_n = iv_sum = 0;
  iv_sq = 0;
  iv_max = 0;
}

double FramePacer::jitter_ms() const {
  const int32_t n = iv_n;
  if (n < 2) return 0.0;
  const double m = (double)iv_sum / n;   // Python's int / int: both exact in a double
  const double v = (double)iv_sq / n - m * m;
  return v > 0 ? pow(v, 0.5) : 0.0;      // CPython's float ** 0.5 is C's pow
}

}  // namespace pacer
}  // namespace hm
