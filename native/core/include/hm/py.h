// What the game port needs from Python where C++ behaves differently. The port
// computes in double, as CPython does, so it matches the Python traces
// native/test replays bit for bit.
#pragma once
#include <math.h>
#include <stdint.h>
#include <string.h>

#include <optional>

#include "hm/compat.h"
#include "hm/ticks.h"

namespace hm {

using opt_ticks = std::optional<ticks_t>;   // a time stamp or None

// round(x) for a float, halves to even (int(round(x)) in Python).
inline int64_t pyround(double x) { return (int64_t)nearbyint(x); }

// x % m for floats: the result takes the sign of m.
inline double pymod(double x, double m) {
  const double r = fmod(x, m);
  return r != 0 && (r < 0) != (m < 0) ? r + m : r;
}

struct Mac {
  uint8_t b[6];
  bool operator==(const Mac& o) const { return memcmp(b, o.b, sizeof b) == 0; }
  bool operator!=(const Mac& o) const { return !(*this == o); }
};

}  // namespace hm
