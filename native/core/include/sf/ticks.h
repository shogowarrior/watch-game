// Wrap-safe millisecond ticks and Python-style integer division.
#pragma once
#include <stdint.h>

namespace sf {

using ticks_t = uint32_t;

inline int32_t ticks_diff(ticks_t a, ticks_t b) { return (int32_t)(a - b); }
inline ticks_t ticks_add(ticks_t t, int32_t d) { return t + (uint32_t)d; }

// Python's a // b and a % b (floor), for b > 0; C's / and % truncate toward 0.
inline int64_t floordiv(int64_t a, int64_t b) { return a >= 0 ? a / b : -((-a + b - 1) / b); }
inline int64_t floormod(int64_t a, int64_t b) { return a - floordiv(a, b) * b; }

}  // namespace sf
