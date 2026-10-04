// finder/estimators/base.py: activity codes and the motion hint every
// estimator and the game share.
#pragma once
#include <stdint.h>

namespace hm {

enum Activity : int32_t { ACT_UNKNOWN = 0, ACT_STILL = 1, ACT_WALK = 2, ACT_RUN = 3 };

// Motion hint for one watch (from the BMA423 step counter and activity).
struct MotionInfo {
  int32_t activity = ACT_UNKNOWN;
  float step_rate_hz = 0.0f;
  uint32_t steps = 0;
  float speed_mps(float stride_m = 0.7f) const { return step_rate_hz * stride_m; }   // upper-bound-ish
};

}  // namespace hm
