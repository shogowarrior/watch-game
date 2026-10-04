#include "hm/kalman2.h"

namespace hm {
namespace est {

using namespace kalman2;

void kalman2::Mot::feed(ticks_t t_ms, const MotionInfo* m) {
  if (m == nullptr) {
    v = V_UNKNOWN;
    return;
  }
  if (!steps) {
    steps = m->steps;   // first sight: a count, not a step
  } else if (m->steps != *steps) {
    steps = m->steps;
    t = t_ms;
  }
  const double hz = m->step_rate_hz;
  if (!t || ticks_diff(t_ms, *t) >= STEP_HOLD_MS) {
    v = 0.0;
  } else if (m->activity == ACT_STILL && hz < STILL_HZ) {
    v = 0.0;
  } else {
    const double w = hz * STRIDE_M;
    v = w > 0.5 ? w : 0.5;
  }
}

Kalman2::Kalman2(const PathLoss& path_loss) : RangeEstimator(path_loss), cal(path_loss.p0) { Kalman2::reset(); }

void Kalman2::reset() {
  RangeEstimator::reset();
  bias.reset();
  p00 = 100.0;
  p01 = 0.0;
  p11 = 1.0;
  sig = SD_PER_STEP * JIT_INIT;   // noise_db's prior; set from it on every packet
  peer_bias = 0.0;
  peer_n = 0;
  speed = V_UNKNOWN;
  vmax = 1.0;
  me_ = Mot();
  peer_ = Mot();
}

void Kalman2::calibrate(double rssi_at_1m) {
  cal = rssi_at_1m;
  bias.reset();
}

void Kalman2::update(ticks_t t_ms, std::optional<double> rssi, std::optional<double> peer_rssi,
                     const MotionInfo* my_motion, const MotionInfo* peer_motion) {
  me_.feed(t_ms, my_motion);
  if (rssi) peer_.feed(t_ms, peer_motion);
  speed = me_.v + peer_.v;
  if (!rssi) {
    if (speed <= 0.0 || (last_t && ticks_diff(t_ms, *last_t) > MAX_GAP_MS)) {
      trend = 0;
      trend_conf = 0.0;
    }
    return;
  }
  note_noise(t_ms, *rssi);
  const double s = *noise_db;   // fading sigma = the shared ui-spec 5.5 noise, clamped
  sig = s < SIG_MIN ? SIG_MIN : s > SIG_MAX ? SIG_MAX : s;
  if (!rssi_f) {
    rssi_f = *rssi;
    p00 = sig * sig;
    last_t = t_ms;
    publish();
    return;
  }
  const double dt = ticks_diff(t_ms, *last_t) * 0.001;
  if (dt > 0.0) {
    last_t = t_ms;
    if (dt > MAX_GAP_MS * 0.001) {
      rate_db_s = 0.0;
      p01 = 0.0;
      p11 = vmax * vmax + 0.01;
    }
    predict(dt);
  }
  const double rv = sig * sig;
  correct(*rssi, rv);
  if (peer_rssi) {
    double a;
    if (peer_n < 50) {
      peer_n += 1;
      a = 1.0 / peer_n;
    } else {
      a = PEER_ALPHA;
    }
    peer_bias += a * ((*peer_rssi - *rssi_f) - peer_bias);
    if (peer_n >= PEER_MIN_N) correct(*peer_rssi - peer_bias, rv);
  }
  update_trend();
  publish();
}

void Kalman2::predict(double dt) {
  const double s = speed;
  double d = dist_m ? *dist_m : 5.0;
  if (d < D_MIN) d = D_MIN;
  const double vm = KDB * pl.n * s / d;
  const double tau = s > 0.0 ? TAU_V : TAU_V_STILL;
  const double a = tau / (tau + dt);
  const double v = rate_db_s;
  *rssi_f += v * dt;
  rate_db_s = v * a;
  double p11_ = p11;
  if (s > 0.0 && vmax <= 0.0) p11_ += START_K * START_K * vm * vm;
  vmax = vm;
  const double p01_ = p01;
  p00 += dt * (2.0 * p01_ + dt * p11_) + (Q_R_STILL + Q_R_MOVE * s) * dt;
  p01 = a * (p01_ + dt * p11_);
  p11 = a * a * p11_ + vm * vm * (1.0 - a * a) + 1e-4;
}

void Kalman2::correct(double z, double rv) {
  const double p00_ = p00;
  const double p01_ = p01;
  const double S = p00_ + rv;
  double e = z - *rssi_f;
  const double lim = K_UP * K_UP * S;
  if (e * e > lim || (e < 0.0 && e * e > K_DOWN * K_DOWN * S)) e = (e > 0.0 ? K_UP : -K_DOWN) * sqrt(S);
  const double k0 = p00_ / S;
  const double k1 = p01_ / S;
  *rssi_f += k0 * e;
  const double v = rate_db_s + k1 * e;
  const double cap = CAP_K * vmax;
  rate_db_s = v > cap ? cap : v < -cap ? -cap : v;
  p00 = p00_ - k0 * p00_;
  p01 = p01_ - k0 * p01_;
  const double p11_ = p11 - k1 * p01_;
  p11 = p11_ > 1e-6 ? p11_ : 1e-6;
}

void Kalman2::update_trend() {
  const double z = rate_db_s / sqrt(p11);
  int32_t tr = trend;
  if (speed <= 0.0) {
    tr = 0;
  } else if (tr == 0 || tr * z < -Z_FLIP) {
    tr = z > Z_ON ? 1 : z < -Z_ON ? -1 : tr;
  }
  trend = tr;
  const double c = fabs(z);
  trend_conf = tr == 0 ? 0.0 : c > 1.0 ? 1.0 : c;
}

void Kalman2::publish() {
  const double b = BIAS_A + BIAS_B * sig;
  if (!bias || fabs(b - *bias) > 0.5) {
    bias = b;
    pl.p0 = cal - b;
  }
  rssi_var = p00 + SH_VAR;
  set_distance_from_rssi();   // display hysteresis is proximity's (ui-spec 5.2/5.4)
}

}  // namespace est
}  // namespace hm
