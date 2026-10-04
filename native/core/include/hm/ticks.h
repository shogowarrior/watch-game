// Wrap-safe millisecond ticks and Python-style integer division.
#pragma once
#include <stdint.h>

namespace hm {

using ticks_t = uint32_t;

// MicroPython's tick period: ticks_add wraps at 2^30 ms and ticks_diff is the
// signed difference modulo 2^30, as on the watch, so the game port computes
// what the Python does across a wrap. A clock may count all 32 bits (2^30
// divides 2^32); intervals must stay under 2^29 ms (6 days).
constexpr ticks_t TICKS_PERIOD = 1u << 30;

inline int32_t ticks_diff(ticks_t a, ticks_t b) {
  const int32_t d = (int32_t)((a - b) & (TICKS_PERIOD - 1));
  return d >= (int32_t)(TICKS_PERIOD / 2) ? d - (int32_t)TICKS_PERIOD : d;
}
inline ticks_t ticks_add(ticks_t t, int32_t d) { return (t + (uint32_t)d) & (TICKS_PERIOD - 1); }

// Python's a // b and a % b (floor), for b > 0; C's / and % truncate toward 0.
inline int64_t floordiv(int64_t a, int64_t b) { return a >= 0 ? a / b : -((-a + b - 1) / b); }
inline int64_t floormod(int64_t a, int64_t b) { return a - floordiv(a, b) * b; }

}  // namespace hm
