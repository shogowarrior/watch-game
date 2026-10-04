#include "hm/proximity.h"

#include <math.h>

namespace hm {
namespace proximity {

// ---- §5.1 proximity and intensity

double prox(double d_m) {
  if (d_m < PROX_MIN_M) d_m = PROX_MIN_M;
  const double p = log(PROX_FAR_M / d_m) / LN_RATIO;
  return p < 0.0 ? 0.0 : p > 1.0 ? 1.0 : p;
}

double intensity(double i_prev, double p, int32_t dt_ms, int32_t tau_ms) {
  if (dt_ms <= 0) return i_prev;
  return i_prev + (p - i_prev) * (1.0 - exp((double)-dt_ms / (double)tau_ms));
}

// ---- §5.2 zones

int32_t zone_for(double d_m) {
  int32_t z = 0;
  while (z < 3 && d_m <= ZONE_ENTER_M[z]) z++;
  return z;
}

// +1 toward the closer zone, -1 toward the farther one, 0 stay.
static int32_t want(int32_t z, double d_m) {
  if (z < 3 && d_m <= ZONE_ENTER_M[z]) return 1;
  if (z > 0 && d_m >= ZONE_EXIT_M[z - 1]) return -1;
  return 0;
}

void ZoneTracker::reset() {
  zone.reset();
  changed = 0;
  arm_ = true;
  dir_ = 0;
  since_ = 0;
}

std::optional<int32_t> ZoneTracker::update(ticks_t now, std::optional<double> d_m) {
  changed = 0;
  if (!d_m) return zone;
  if (arm_ || !zone) {
    const int32_t nz = zone_for(*d_m);
    if (zone && nz != *zone) changed = nz > *zone ? 1 : -1;
    zone = nz;
    arm_ = false;
    dir_ = 0;
    since_ = now;
    return nz;
  }
  int32_t z = *zone;
  const int32_t w = want(z, *d_m);
  if (w != dir_) {
    dir_ = w;
    since_ = now;
  }
  if (w) {
    const int32_t dwell = ZONE_DWELL_MS[w > 0 ? z : z - 1];
    if (ticks_diff(now, since_) >= dwell) {
      zone = z = z + w;
      changed = w;
      dir_ = want(z, *d_m);   // the next boundary's dwell starts at this commit
      since_ = now;
    }
  }
  return zone;
}

// ---- §5.4 readout band

int32_t band_raw(double d_m) {
  int32_t b = 0;
  while (b < 5 && d_m >= BAND_EDGES_M[b]) b++;
  return b;
}

int32_t band(std::optional<int32_t> prev, double d_m, std::optional<int32_t> zone, int32_t trend) {
  int32_t b;
  if (!prev) {
    b = band_raw(d_m);
  } else {
    b = *prev;
    while (b < 5 && d_m >= BAND_EDGES_M[b] * BAND_HYST) b++;
    while (b > 0 && d_m < BAND_EDGES_M[b - 1] / BAND_HYST) b--;
    if (trend > 0 && b > *prev)
      b = *prev;
    else if (trend < 0 && b < *prev)
      b = *prev;
  }
  if (zone) {
    const int32_t lo = ZONE_BANDS[*zone][0], hi = ZONE_BANDS[*zone][1];
    b = b < lo ? lo : b > hi ? hi : b;
  }
  return b;
}

// ---- delivery ratio

DeliveryMeter::DeliveryMeter(double expected_hz, int32_t window_ms)
    : expected_hz(expected_hz), nb_(clamp(window_ms / 1000, (int32_t)1, MAX_NB)) {
  reset();
}

void DeliveryMeter::reset() {
  for (int32_t i = 0; i < nb_; i++) b_[i] = 0;
  head_ = 0;
  t_.reset();
  span_ = 0;
}

void DeliveryMeter::roll(ticks_t now) {
  if (!t_) {
    t_ = now;
    return;
  }
  const int32_t dt = ticks_diff(now, *t_);
  if (dt < 1000) return;
  int32_t k = dt / 1000;
  if (k > nb_) k = nb_;
  for (int32_t i = 0; i < k; i++) {
    head_ = (head_ + 1) % nb_;
    b_[head_] = 0;
    if (span_ < nb_ - 1) span_++;
  }
  t_ = ticks_add(*t_, (dt / 1000) * 1000);
}

void DeliveryMeter::note(ticks_t now) {
  roll(now);
  if (b_[head_] < 65535) b_[head_]++;
}

double DeliveryMeter::ratio(ticks_t now) {
  roll(now);
  if (!t_) return 1.0;
  const int32_t span_ms = span_ * 1000 + ticks_diff(now, *t_);
  if (span_ms < 1000 || expected_hz <= 0) return 1.0;
  int32_t n = 0;
  for (int32_t i = 0; i < nb_; i++) n += b_[i];
  const double r = (double)n / (expected_hz * (double)span_ms / 1000.0);
  return r > 1.0 ? 1.0 : r;
}

// ---- §5.5 trend gate

void TrendGate::reset() {
  trend = 0;
  trend_strong = false;
  unreliable = false;
  delta_db = 0.0;
  cnt_ = 0;
  head_ = -1;
  next_.reset();
  cand_ = 0;
  cand_n_ = 0;
  last_sign_ = 0;
  changed_t_.reset();
}

void TrendGate::set(ticks_t now, int32_t v) {
  if (v != trend) {
    trend = v;
    changed_t_ = now;
    if (v) last_sign_ = v;
  }
  if (!v) trend_strong = false;
}

void TrendGate::force_off(ticks_t now) {
  cand_n_ = 0;
  set(now, 0);
}

bool TrendGate::sample(ticks_t now, std::optional<double> rssi_f) {
  if (!next_) next_ = now;
  const int32_t late = ticks_diff(now, *next_);
  if (late < 0) return false;
  if (late >= 2 * TREND_EVAL_MS || !rssi_f) cnt_ = 0;   // gap: the 1 s spacing is broken, refill
  next_ = late >= TREND_EVAL_MS ? ticks_add(now, TREND_EVAL_MS) : ticks_add(*next_, TREND_EVAL_MS);
  if (!rssi_f) return true;
  head_ = (head_ + 1) % N;
  r_[head_] = (float)*rssi_f;
  if (cnt_ < N) cnt_++;
  delta_db = cnt_ == N ? *rssi_f - (double)r_[(head_ + 1) % N] : 0.0;
  return true;
}

int32_t TrendGate::update(ticks_t now, int32_t est_trend, double trend_conf, std::optional<double> rssi_f,
                          std::optional<double> noise_db, int32_t activity, std::optional<int32_t> zone,
                          std::optional<double> delivery) {
  const bool sd_bad = noise_db && *noise_db > UNRELIABLE_SD_DB;
  const bool del_bad = delivery && *delivery < UNRELIABLE_DELIVERY;
  unreliable = sd_bad || del_bad;
  const bool evald = sample(now, rssi_f);
  const bool is_open = (activity == ACT_WALK || activity == ACT_RUN) && zone && *zone != HOT && !unreliable;
  if (!is_open) {
    cand_n_ = 0;
    set(now, 0);
    return trend;
  }
  if (!evald) return trend;
  int32_t c = 0;
  if (cnt_ == N && trend_conf >= TREND_CONF_MIN) {
    const double d = delta_db;
    if (est_trend > 0 && d >= TREND_MIN_DELTA_DB)
      c = 1;
    else if (est_trend < 0 && d <= -TREND_MIN_DELTA_DB)
      c = -1;
  }
  if (c == 0 || c != cand_) {
    cand_ = c;
    cand_n_ = c ? 1 : 0;
  } else {
    cand_n_++;
  }
  if (c != trend) {
    if (trend) set(now, 0);   // evidence no longer supports the shown sign
    if (c && cand_n_ >= TREND_HOLD_EVALS) {
      const bool flip = c == -last_sign_ && changed_t_ && ticks_diff(now, *changed_t_) < TREND_FLIP_MIN_MS;
      if (!flip) set(now, c);
    }
  }
  if (trend) {
    const double d = delta_db;
    trend_strong = trend_conf >= TREND_STRONG_CONF && (trend > 0 ? d : -d) >= TREND_STRONG_DB;
  }
  return trend;
}

// ---- combined

void Proximity::reset() {
  zones.reset();
  gate.reset();
  intensity = 0.0;
  band_idx.reset();
  t_ = 0;
}

void Proximity::rearm() {
  zones.rearm();
  gate.reset();
  band_idx.reset();
}

void Proximity::update(ticks_t now, std::optional<double> d_m, std::optional<double> rssi_f,
                       std::optional<double> noise_db, int32_t est_trend, double trend_conf, int32_t activity,
                       std::optional<double> delivery) {
  if (!d_m) {
    const std::optional<int32_t> z = zones.update(now, std::nullopt);
    gate.update(now, 0, 0.0, std::nullopt, noise_db, activity, z, delivery);
    t_ = now;
    return;
  }
  const double p = prox(*d_m);
  if (!band_idx)   // first fix after reset/rearm: jump
    intensity = p;
  else
    intensity = proximity::intensity(intensity, p, ticks_diff(now, t_));
  t_ = now;
  const std::optional<int32_t> z = zones.update(now, d_m);
  gate.update(now, est_trend, trend_conf, rssi_f, noise_db, activity, z, delivery);
  const std::optional<int32_t> prev = band_idx;
  const int32_t tr = gate.trend;
  const int32_t b = proximity::band(prev, *d_m, z, tr);
  if (prev && ((tr > 0 && b > *prev) || (tr < 0 && b < *prev))) gate.force_off(now);
  band_idx = b;
}

}  // namespace proximity
}  // namespace hm
