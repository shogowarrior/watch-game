#include "sf/imu_sampler.h"

#include <math.h>

namespace sf {

void ImuSampler::poll() {
  const uint32_t t0 = clock_.now_us();
  ImuStats& s = stats;
  s.polls++;
  int level = 0;
  int n = imu_.fifo_read(raw_, Bma423::FIFO_BYTES / Bma423::FRAME_BYTES, &level);
  if (n < 0) {
    s.errors++;
  } else {
    if ((uint32_t)level > s.fifo_max) s.fifo_max = level;
    if (level >= Bma423::FIFO_BYTES - Bma423::FRAME_BYTES) s.full++;
    n = Bma423::decode(raw_, n, mg_, imu_.range_mg());
    s.samples += n;
    for (int i = 0; i < n; i++) {
      const float x = mg_[3 * i], y = mg_[3 * i + 1], z = mg_[3 * i + 2];
      const uint32_t a = (uint32_t)sqrtf(x * x + y * y + z * z);
      if (a > s.peak_mg) s.peak_mg = a;
    }
  }
  s.read_us += clock_.now_us() - t0;
}

}  // namespace sf
