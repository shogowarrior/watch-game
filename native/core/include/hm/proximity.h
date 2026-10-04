// finder/proximity.py: distance -> proximity, zone, readout band and gated
// trend (ui-spec §5.1-5.5). Pure logic, no allocation per update. Feed it the
// estimator's outputs every logic tick (~10 Hz); read zone(), intensity,
// band_idx/band(), trend(), trend_strong() and unreliable() for RenderParams.
//
// Python None: a distance, RSSI, noise or delivery ratio is a
// std::optional<double>, a zone or band index a std::optional<int32_t>, the
// band label nullptr.
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/estimator.h"
#include "hm/py.h"
#include "hm/tuning.h"

namespace hm {
namespace proximity {

// tokens.json thresholds.* (via T) under this module's names (the arrays as
// references: static, since a namespace-scope reference has external linkage)
static constexpr auto& ZONE_ENTER_M = T::ZONE_ENTER_M;     // boundary k: zone k -> k+1
static constexpr auto& ZONE_EXIT_M = T::ZONE_EXIT_M;       // boundary k: zone k+1 -> k
static constexpr auto& ZONE_DWELL_MS = T::ZONE_DWELL_MS;   // both directions
static constexpr auto& BAND_EDGES_M = T::BAND_EDGES_M;
static constexpr auto& BAND_LABELS = T::BAND_LABELS;
constexpr double BAND_HYST = T::BAND_HYST;
static constexpr auto& ZONE_BANDS = T::ZONE_BANDS;         // zone -> (lo, hi)
constexpr double PROX_FAR_M = T::PROX_D_FAR_M;   // p = 0 at this distance
constexpr double PROX_MIN_M = T::PROX_D_MIN_M;   // d floor inside the log
constexpr int32_t INTENSITY_TAU_MS = T::INTENSITY_TAU_MS;
constexpr double TREND_CONF_MIN = T::TREND_CONF_MIN;
constexpr int32_t TREND_WINDOW_MS = T::TREND_WINDOW_MS;
constexpr int32_t TREND_EVAL_MS = T::TREND_EVAL_MS;
constexpr int32_t TREND_HOLD_EVALS = T::TREND_HOLD_EVALS;
constexpr double TREND_MIN_DELTA_DB = T::TREND_START_DB;   // §5.5 starting +1 threshold
constexpr double TREND_STRONG_CONF = T::TREND_STRONG_CONF;
constexpr double TREND_STRONG_DB = T::TREND_STRONG_DB;
constexpr int32_t TREND_FLIP_MIN_MS = T::TREND_FLIP_MIN_MS;
constexpr double UNRELIABLE_SD_DB = T::UNRELIABLE_SD_DB;
constexpr double UNRELIABLE_DELIVERY = T::UNRELIABLE_DELIVERY;
constexpr int32_t DELIVERY_WINDOW_MS = T::UNRELIABLE_WINDOW_MS;

constexpr int32_t FAR = T::ZONE_FAR, NEAR = T::ZONE_NEAR, WARM = T::ZONE_WARM, HOT = T::ZONE_HOT;

constexpr double LN_RATIO = T::PROX_LN_RATIO;   // ln(PROX_RATIO): p = 1 at PROX_FAR_M / PROX_RATIO (2 m)

// ---- §5.1 proximity and intensity

// Log-spaced proximity: 60 m -> 0.0, 2 m -> 1.0, clamped.
double prox(double d_m);
// First-order smoothing of p toward the displayed intensity.
double intensity(double i_prev, double p, int32_t dt_ms, int32_t tau_ms = INTENSITY_TAU_MS);

// ---- §5.2 zones

// Zone for a first fix (no hysteresis): enter edges decide.
int32_t zone_for(double d_m);

// Zone with enter/exit hysteresis and symmetric per-boundary dwell. One step
// per decision; the dwell timer restarts after every commit. The first fix (or
// the first after rearm) jumps straight to its zone.
class ZoneTracker {
 public:
  ZoneTracker() { reset(); }
  void reset();
  void rearm() { arm_ = true; }   // next fix jumps without dwell (relink after LINK_LOST); zone is kept
  std::optional<int32_t> update(ticks_t now, std::optional<double> d_m);

  std::optional<int32_t> zone;
  int32_t changed;   // +1 closer / -1 farther / 0, for this update only

 private:
  bool arm_;
  int32_t dir_;
  ticks_t since_;
};

// ---- §5.4 readout band

// Band index 0..5 with no hysteresis.
int32_t band_raw(double d_m);
// Band index with 15 % hysteresis, zone clamp and trend-monotonic rule. prev
// nullopt means no previous band. The zone clamp is applied last, so the band
// never contradicts the zone; the caller must drop a trend that the final
// clamp contradicts (Proximity does).
int32_t band(std::optional<int32_t> prev, double d_m, std::optional<int32_t> zone, int32_t trend = 0);

// ---- delivery ratio

// Packet delivery ratio over the last window_ms in 1 s buckets.
class DeliveryMeter {
 public:
  static constexpr int32_t MAX_NB = DELIVERY_WINDOW_MS / 1000;   // window_ms is clamped to MAX_NB s
  explicit DeliveryMeter(double expected_hz = 10.0, int32_t window_ms = DELIVERY_WINDOW_MS);
  void reset();
  // Roll the window without a packet, so a long silence never wraps the stamp.
  void expire(ticks_t now) {
    if (t_) roll(now);
  }
  void note(ticks_t now);       // one received partner packet
  double ratio(ticks_t now);    // received / expected over the window (1.0 until 1 s of history)

  double expected_hz;

 private:
  void roll(ticks_t now);
  int32_t nb_;
  uint16_t b_[MAX_NB];
  int32_t head_;
  opt_ticks t_;     // start of the head bucket
  int32_t span_;    // completed buckets (capped at nb - 1)
};

// ---- §5.5 trend gate

// Decides the displayed warmer/colder verdict from the estimator's trend.
// Hard gates (checked every update, close at once): own activity walk/run,
// zone known and not HOT, not unreliable. Every TREND_EVAL_MS the gate samples
// rssi_f into an 8 s ring; a candidate sign needs trend_conf >= TREND_CONF_MIN
// and an agreeing 8 s delta of at least TREND_MIN_DELTA_DB. A candidate is
// shown after TREND_HOLD_EVALS consecutive evaluations; it is hidden at the
// first evaluation that does not support it. A reversal of sign is blocked for
// TREND_FLIP_MIN_MS after the last change of the shown value.
class TrendGate {
 public:
  static constexpr int32_t N = TREND_WINDOW_MS / TREND_EVAL_MS + 1;   // ring size
  TrendGate() { reset(); }
  void reset();
  void force_off(ticks_t now);   // hide the trend now (e.g. the zone clamp moved the band against it)
  int32_t update(ticks_t now, int32_t est_trend, double trend_conf, std::optional<double> rssi_f,
                 std::optional<double> noise_db, int32_t activity, std::optional<int32_t> zone,
                 std::optional<double> delivery = std::nullopt);

  int32_t trend;
  bool trend_strong;
  bool unreliable;
  double delta_db;   // rssi_f change over the window (+ = closer)

 private:
  void set(ticks_t now, int32_t v);
  bool sample(ticks_t now, std::optional<double> rssi_f);
  float r_[N] = {};
  int32_t cnt_;              // valid samples in the ring
  int32_t head_;
  opt_ticks next_;           // time of the next evaluation
  int32_t cand_, cand_n_;
  int32_t last_sign_;        // last non-zero sign shown
  opt_ticks changed_t_;      // time the shown value last changed
};

// ---- combined

// Zone, intensity, band and gated trend from one distance estimate.
class Proximity {
 public:
  Proximity() { reset(); }
  void reset();   // forget everything (new round / SEARCHING)
  void rearm();   // after LINK_LOST: next fix jumps zone/band/intensity; trend restarts

  std::optional<int32_t> zone() const { return zones.zone; }
  int32_t zone_changed() const { return zones.changed; }
  const char* band() const { return band_idx ? BAND_LABELS[*band_idx] : nullptr; }
  int32_t trend() const { return gate.trend; }
  bool trend_strong() const { return gate.trend_strong; }
  bool unreliable() const { return gate.unreliable; }

  void update(ticks_t now, std::optional<double> d_m, std::optional<double> rssi_f = std::nullopt,
              std::optional<double> noise_db = std::nullopt, int32_t est_trend = 0, double trend_conf = 0.0,
              int32_t activity = ACT_UNKNOWN, std::optional<double> delivery = std::nullopt);
  // Reads a range estimator after its update. The unreliable gate takes the
  // learnt packet-to-packet noise noise_db (ui-spec §5.5); it is nullopt until
  // the first packet, which the gate reads as not unreliable.
  void update_est(ticks_t now, const est::RangeEstimator& est, int32_t activity = ACT_UNKNOWN,
                  std::optional<double> delivery = std::nullopt) {
    update(now, est.dist_m, est.rssi_f, est.noise_db, est.trend, est.trend_conf, activity, delivery);
  }

  ZoneTracker zones;
  TrendGate gate;
  double intensity;
  std::optional<int32_t> band_idx;

 private:
  ticks_t t_;   // last update; read only once band_idx is set, so always written by then
};

}  // namespace proximity
}  // namespace hm
