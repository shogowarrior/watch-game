// Frame-time statistics: a fixed sample buffer, no allocation.
#pragma once
#include <stdint.h>

namespace hm {

class Stats {
 public:
  static constexpr int N = 1024;   // samples kept (the first N)
  void reset() { n_ = 0; total_ = 0; }
  void add(uint32_t v) {
    if (n_ < N) v_[n_] = v;
    n_++;
    total_ += v;
  }
  int count() const { return n_ < N ? n_ : N; }
  uint32_t mean() const { return n_ ? (uint32_t)(total_ / n_) : 0; }
  // p-th percentile (0..100) and population standard deviation of the kept samples
  uint32_t pct(int p) const;
  uint32_t sd() const;

 private:
  uint32_t v_[N];
  int n_ = 0;
  uint64_t total_ = 0;
};

}  // namespace hm
