#include "sf/stats.h"

#include <math.h>
#include <string.h>

#include <algorithm>

namespace sf {

uint32_t Stats::pct(int p) const {
  const int n = count();
  if (!n) return 0;
  static uint32_t tmp[N];   // the bench runs on one task
  memcpy(tmp, v_, n * sizeof(uint32_t));
  const int k = (int)(((int64_t)p * (n - 1) + 50) / 100);
  std::nth_element(tmp, tmp + k, tmp + n);
  return tmp[k];
}

uint32_t Stats::sd() const {
  const int n = count();
  if (n < 2) return 0;
  double m = 0, s = 0;
  for (int i = 0; i < n; i++) m += v_[i];
  m /= n;
  for (int i = 0; i < n; i++) s += (v_[i] - m) * (v_[i] - m);
  return (uint32_t)(sqrt(s / n) + 0.5);
}

}  // namespace sf
