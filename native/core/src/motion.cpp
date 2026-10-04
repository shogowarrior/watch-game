#include "hm/motion.h"

#include <math.h>

namespace hm {
namespace motion {

double tilt_from_gravity(double gx, double gy, double gz, double z_sign) {
  const double n = sqrt(gx * gx + gy * gy + gz * gz);
  if (n <= 0.0) return 180.0;
  double c = gz * (z_sign >= 0 ? 1.0 : -1.0) / n;
  c = c < -1.0 ? -1.0 : c > 1.0 ? 1.0 : c;
  return acos(c) * R2D;
}

MotionTracker::MotionTracker(double stride_m_, int32_t rate_hz, double run_stride_k_, int z_sign_)
    : stride_m(stride_m_), run_stride_k(run_stride_k_), z_sign(z_sign_ >= 0 ? 1.0 : -1.0) {
  dt0_ = 1.0 / rate_hz;
  const int n = (int)rate_hz;
  n_ = n > 8 ? n : 8;
  if (n_ > BUF_MAX) n_ = BUF_MAX;   // the fixed array's cap (Python: any length)
  for (int i = 0; i < BUF_MAX; i++) buf_[i] = 0.0;
  reset();
}

void MotionTracker::reset() {
  steps = 0;
  sw_steps = 0;
  step_rate_hz = 0.0;
  activity = ACT_UNKNOWN;
  is_still = false;
  mag_sd_g = 0.0;
  gx = 0.0;
  gy = 0.0;
  gz = 1.0;
  face_up = false;
  dist_m = 0.0;
  chip_live = false;
  t_.reset();
  i_ = 0;
  k_ = 0;
  s_ = 0.0;
  ss_ = 0.0;
  lpf_ = 0.0;
  lps_ = 0.0;
  armed_ = true;
  pk_ = 0.0;
  pk_t_ = 0;
  amp_ = 0.15;
  last_step_t_.reset();
  streak_ = 0;
  chip_steps_.reset();
  chip_act_.reset();
  chip_t_.reset();
  sw_out_ = 0;
  cad_n_ = 0;
  cad_t0_.reset();
  iv_hz_ = 0.0;
  have_raw_ = false;
}

double MotionTracker::roll_deg() const { return atan2(gy, gz * z_sign) * R2D; }

double MotionTracker::pitch_deg() const { return atan2(-gx, sqrt(gy * gy + gz * gz)) * R2D; }

// ---- raw accelerometer path ----------------------------------------------------

bool MotionTracker::add_sample(ticks_t t_ms, double x, double y, double z) {
  double dt;
  if (!t_) {
    dt = dt0_;
    gx = x;
    gy = y;
    gz = z;
    const double m0 = sqrt(x * x + y * y + z * z);
    lpf_ = lps_ = m0;
  } else {
    dt = ticks_diff(t_ms, *t_) * 0.001;
    if (dt <= 0.0 || dt > 0.5) dt = dt0_;
  }
  t_ = t_ms;
  have_raw_ = true;
  const double m = sqrt(x * x + y * y + z * z);

  // 1 s window sd of |a| (running sums of |a|-1 g for float32, re-summed per wrap)
  const int n = n_;
  int i = i_;
  double old = buf_[i];
  const double d = m - 1.0;
  buf_[i] = d;
  i += 1;
  if (i == n) i = 0;
  i_ = i;
  if (k_ < n) {
    k_ += 1;
    old = 0.0;
  }
  s_ += d - old;
  ss_ += d * d - old * old;
  if (i == 0) {
    double s = 0.0;
    double ss = 0.0;
    for (int j = 0; j < n; j++) {
      s += buf_[j];
      ss += buf_[j] * buf_[j];
    }
    s_ = s;
    ss_ = ss;
  }
  const int k = k_;
  const double mu = s_ / k;
  const double var = ss_ / k - mu * mu;
  const double sd = var > 0.0 ? sqrt(var) : 0.0;
  mag_sd_g = sd;
  if (is_still) {
    if (sd > STILL_OFF_G) is_still = false;
  } else if (k == n && sd < STILL_ON_G) {
    is_still = true;
  }

  // gravity + tilt + face-up
  const double a = dt / (TAU_G_S + dt);
  const double gx_ = gx + a * (x - gx);
  const double gy_ = gy + a * (y - gy);
  const double gz_ = gz + a * (z - gz);
  gx = gx_;
  gy = gy_;
  gz = gz_;
  const double gn = sqrt(gx_ * gx_ + gy_ * gy_ + gz_ * gz_);
  if (gn > 0.0) {
    const double c = gz_ * z_sign / gn;
    face_up = c > (face_up ? FACE_OFF_COS : FACE_ON_COS);
  }

  // software step detector: band-pass |a|, peak with hysteresis
  lpf_ += dt / (TAU_BP_FAST_S + dt) * (m - lpf_);
  lps_ += dt / (TAU_BP_SLOW_S + dt) * (m - lps_);
  const double bp = lpf_ - lps_;
  amp_ -= amp_ * dt * 0.25;   // forget old step strength (tau 4 s)
  double thr = 0.4 * amp_;
  if (thr < STEP_MIN_G) thr = STEP_MIN_G;
  bool stepped = false;
  if (armed_) {
    if (bp > pk_) {
      pk_ = bp;
      pk_t_ = t_ms;
    } else if (pk_ > thr && bp < 0.5 * pk_) {
      amp_ += 0.25 * (pk_ - amp_);
      armed_ = false;
      stepped = sw_step(pk_t_);
    }
  } else if (bp < -0.3 * thr) {
    armed_ = true;
    pk_ = 0.0;
  }

  update_rate(t_ms);
  update_activity(t_ms);
  return stepped;
}

bool MotionTracker::sw_step(ticks_t t) {
  const int32_t d = last_step_t_ ? ticks_diff(t, *last_step_t_) : STEP_MAX_MS + 1;
  if (d < STEP_MIN_MS) return false;
  last_step_t_ = t;
  if (d > STEP_MAX_MS) {
    streak_ = 1;
    return true;
  }
  streak_ += 1;
  const int32_t k = streak_ == STEP_CONFIRM ? STEP_CONFIRM : streak_ > STEP_CONFIRM ? 1 : 0;
  if (k) {
    sw_steps += (uint32_t)k;
    if (!chip_live) {
      add_steps(k);
      sw_out_ += k;
    }
  }
  if (!chip_live) {
    const double f = 1000.0 / d;
    iv_hz_ = streak_ == 2 ? f : iv_hz_ + 0.3 * (f - iv_hz_);
  }
  return true;
}

// ---- on-chip path ----------------------------------------------------------------

void MotionTracker::set_chip(ticks_t t_ms, std::optional<int32_t> steps_, std::optional<int32_t> act_code) {
  if (steps_) {
    const std::optional<int32_t> last = chip_steps_;
    chip_steps_ = steps_;
    if (last && *steps_ > *last) {
      int32_t d = *steps_ - *last;
      cad_n_ += d;
      d -= sw_out_;   // counted by the software path while the chip was out
      if (d > 0) add_steps(d);
    }
    sw_out_ = 0;
    chip_t_ = t_ms;
  }
  if (act_code) {
    chip_act_ = CHIP_ACT[*act_code & 3];
    chip_t_ = t_ms;
  }
  chip_live = true;
  update_rate(t_ms);
  update_activity(t_ms);
}

// ---- shared ------------------------------------------------------------------------

void MotionTracker::add_steps(int32_t k) {
  steps += (uint32_t)k;
  const double s = stride_m * (activity == ACT_RUN ? run_stride_k : 1.0);
  dist_m += k * s;
}

void MotionTracker::update_rate(ticks_t t) {
  if (!chip_live) {
    if (!last_step_t_ || ticks_diff(t, *last_step_t_) > STEP_MAX_MS) streak_ = 0;
    step_rate_hz = streak_ >= STEP_CONFIRM ? iv_hz_ : 0.0;
    return;
  }
  if (!cad_t0_) {
    cad_t0_ = t;
    return;
  }
  const int32_t el = ticks_diff(t, *cad_t0_);
  if (el >= 2000) {
    const double inst = cad_n_ * 1000.0 / el;
    step_rate_hz += 0.5 * (inst - step_rate_hz);
    if (step_rate_hz < 0.05) step_rate_hz = 0.0;
    cad_n_ = 0;
    cad_t0_ = t;
  }
}

void MotionTracker::update_activity(ticks_t t) {
  chip_live = chip_t_ && ticks_diff(t, *chip_t_) < CHIP_LIVE_MS;
  if (!have_raw_ && chip_act_) is_still = *chip_act_ == ACT_STILL;
  int32_t act;
  if (have_raw_ && is_still) {
    act = ACT_STILL;
  } else if (chip_live && chip_act_) {
    act = *chip_act_;
  } else if (step_rate_hz > RUN_HZ) {
    act = ACT_RUN;
  } else if (step_rate_hz > WALK_HZ) {
    act = ACT_WALK;
  } else {
    act = ACT_UNKNOWN;
  }
  activity = act;
}

}  // namespace motion
}  // namespace hm
