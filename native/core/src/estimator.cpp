#include "hm/estimator.h"

namespace hm {
namespace est {

void RangeEstimator::reset() {
  rssi_f.reset();
  rssi_var = 100.0;
  noise_db.reset();
  jz_.reset();
  jt_ = 0;
  rate_db_s = 0.0;
  dist_m.reset();
  dist_lo_m.reset();
  dist_hi_m.reset();
  trend = 0;
  trend_conf = 0.0;
  last_t.reset();
}

void RangeEstimator::note_noise(ticks_t t_ms, double rssi) {
  if (jz_ && ticks_diff(t_ms, jt_) < JIT_GAP_MS) {
    const double d = rssi - *jz_;
    jit += JIT_A * ((d > 0 ? d : -d) - jit);
  }
  jz_ = rssi;
  jt_ = t_ms;
  noise_db = SD_PER_STEP * jit;
}

void RangeEstimator::set_distance_from_rssi() {
  if (!rssi_f) return;
  const double sd = rssi_var > 0 ? sqrt(rssi_var) : 0.0;
  dist_m = pl.rssi_to_dist(*rssi_f);
  dist_lo_m = pl.rssi_to_dist(*rssi_f + sd);
  dist_hi_m = pl.rssi_to_dist(*rssi_f - sd);
}

}  // namespace est
}  // namespace hm
