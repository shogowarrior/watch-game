// finder/compat.py for the C++ port: the clamp helper and TickRing.
#pragma once
#include <stdint.h>

#include "hm/ticks.h"

namespace hm {

template <typename V>
inline V clamp(V x, V lo, V hi) {
  return x < lo ? lo : x > hi ? hi : x;
}

// The last K event stamps (ticks ms). full_within is true when K events fell
// within window_ms of now (3 packets in 2 s, 3 touches in 1 s).
template <int K>
class TickRing {
 public:
  void clear() { i_ = n_ = 0; }
  void note(ticks_t t) {
    t_[i_] = t;
    i_ = i_ + 1 == K ? 0 : i_ + 1;
    if (n_ < K) n_++;
  }
  bool full_within(ticks_t now, int32_t window_ms) const {
    return n_ == K && ticks_diff(now, t_[i_]) <= window_ms;
  }
  // Forget every stamp once the newest is older than window_ms, so none ages
  // into a wrapped ticks_diff.
  void expire(ticks_t now, int32_t window_ms) {
    if (n_ && ticks_diff(now, t_[(i_ ? i_ : K) - 1]) > window_ms) clear();
  }
  int size() const { return n_; }

 private:
  ticks_t t_[K] = {};
  int i_ = 0, n_ = 0;   // next slot (the oldest stamp once full), stamps held
};

}  // namespace hm
