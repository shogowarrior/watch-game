// finder/motion.py: lightweight wrist motion tracker for the BMA423
// (accelerometer only).
//
// Feed raw samples (add_sample, g, 25-50 Hz) and/or the on-chip step counter
// and activity register (set_chip). Exposes steps, cadence, activity, a still
// flag, gravity/tilt/face-up and stride odometry. No position from double
// integration, on purpose (docs/estimation/imu-drift.md): steps x stride only.
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/estimator.h"
#include "hm/ticks.h"
#include "hm/tuning.h"

namespace hm {
namespace motion {

// BMA423 ACTIVITY_TYPE (reg 0x27) codes 0..3 -> ACT_*
constexpr int32_t CHIP_ACT[4] = {ACT_STILL, ACT_WALK, ACT_RUN, ACT_UNKNOWN};

constexpr double STILL_ON_G = 0.012;    // sd of |a| over the window to enter "still"
constexpr double STILL_OFF_G = 0.025;   // ... and to leave it
// face-up within T::SCAN_FLAT_DEG (20, ui-spec §6 SCANNING ready), left above
// FACE_OFF_DEG (spec-silent hysteresis, imu-drift.md); the motion trace checks the cosines
constexpr double FACE_OFF_DEG = 30.0;
constexpr double FACE_ON_COS = 0.9396926207859084;    // math.cos(FACE_ON_DEG * math.pi / 180.0)
constexpr double FACE_OFF_COS = 0.8660254037844387;   // math.cos(FACE_OFF_DEG * math.pi / 180.0)
constexpr double TAU_G_S = 0.5;         // gravity low-pass
constexpr double TAU_BP_FAST_S = 0.053; // step band-pass: ~3 Hz low-pass ...
constexpr double TAU_BP_SLOW_S = 0.25;  // ... minus ~0.6 Hz low-pass
constexpr double STEP_MIN_G = 0.05;
constexpr int32_t STEP_MIN_MS = 250;    // max 4 steps/s
constexpr int32_t STEP_MAX_MS = 2000;   // longer gap ends a walking streak
constexpr int32_t STEP_CONFIRM = 4;     // steps in a regular streak before any are credited
constexpr int32_t CHIP_LIVE_MS = 5000;
constexpr double RUN_HZ = 2.5;
constexpr double WALK_HZ = 0.5;
constexpr double R2D = 180.0 / 3.141592653589793;   // Python's _R2D (math.pi)

// The |a| window holds 1 s of samples (Python sizes its list from rate_hz);
// a fixed array caps it at 100 Hz, the BMA423 FIFO rate.
constexpr int BUF_MAX = 100;

// Angle (deg) between the display normal and up; 0 = face-up flat.
double tilt_from_gravity(double gx, double gy, double gz, double z_sign = 1);

// Per-watch motion state; all per-sample work is O(1) and allocation-free.
class MotionTracker {
 public:
  double stride_m;
  double run_stride_k;
  double z_sign;         // +1.0 or -1.0
  uint32_t steps;
  uint32_t sw_steps;     // software detector total (credited steps)
  double step_rate_hz;
  int32_t activity;
  bool is_still;
  double mag_sd_g;
  double gx, gy, gz;
  bool face_up;
  double dist_m;
  bool chip_live;

  explicit MotionTracker(double stride_m = 0.7, int32_t rate_hz = 50, double run_stride_k = 1.3, int z_sign = 1);
  void reset();

  // Display tilt from face-up flat (deg), from the gravity estimate.
  double tilt_deg() const { return tilt_from_gravity(gx, gy, gz, z_sign); }
  // True once a sample has set the gravity estimate (before it, tilt_deg reads
  // the initial face-up guess).
  bool has_gravity() const { return t_.has_value(); }
  double roll_deg() const;
  double pitch_deg() const;

  // One accelerometer sample in g. Returns true if a step was detected.
  bool add_sample(ticks_t t_ms, double x, double y, double z);
  // Feed BMA423 STEP_COUNTER (cumulative) and/or ACTIVITY_TYPE (0..3).
  void set_chip(ticks_t t_ms, std::optional<int32_t> steps = std::nullopt,
                std::optional<int32_t> act_code = std::nullopt);

 private:
  double dt0_;
  int n_;                      // window length (Python: len(self._buf))
  double buf_[BUF_MAX];
  std::optional<ticks_t> t_;   // last sample time
  int i_, k_;
  double s_, ss_;              // running sums of |a| - 1 g, re-summed per wrap
  double lpf_, lps_;
  bool armed_;
  double pk_;
  ticks_t pk_t_;
  double amp_;
  std::optional<ticks_t> last_step_t_;
  int32_t streak_;
  std::optional<int32_t> chip_steps_;
  std::optional<int32_t> chip_act_;   // ACT_*
  std::optional<ticks_t> chip_t_;
  int32_t sw_out_;             // steps the software path credited since the last chip step read
  int32_t cad_n_;
  std::optional<ticks_t> cad_t0_;
  double iv_hz_;
  bool have_raw_;

  bool sw_step(ticks_t t);
  void add_steps(int32_t k);
  void update_rate(ticks_t t);
  void update_activity(ticks_t t);
};

}  // namespace motion
}  // namespace hm
