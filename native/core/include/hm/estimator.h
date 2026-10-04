// finder/estimators/base.py: activity codes and the motion hint every
// estimator and the game share.
#pragma once
#include <math.h>
#include <stdint.h>

#include <optional>

#include "hm/ticks.h"
#include "hm/tuning.h"

namespace hm {

enum Activity : int32_t { ACT_UNKNOWN = 0, ACT_STILL = 1, ACT_WALK = 2, ACT_RUN = 3 };

// Motion hint for one watch (from the BMA423 step counter and activity).
struct MotionInfo {
  int32_t activity = ACT_UNKNOWN;
  double step_rate_hz = 0.0;
  uint32_t steps = 0;
  double speed_mps(double stride_m = 0.7) const { return step_rate_hz * stride_m; }   // upper-bound-ish
};

}  // namespace hm

// The range-estimator interface (the rest of finder/estimators/base.py).
// Times are ticks ms; rssi in dBm, nullopt on an idle tick (Python None);
// trend +1 closer (warmer), -1 farther (colder), 0 unsure; distances in metres.
namespace hm {
namespace est {

constexpr double KDB = 4.3429448190325175;           // 10 / ln 10, dB per neper: rssi = p0 - n*KDB*ln(d)
constexpr double SD_PER_STEP = 0.8862269254527579;   // sqrt(pi) / 2: Gaussian sd from mean |x_k - x_(k-1)|

constexpr double JIT_INIT = 4.0;                       // mean |rssi step| assumed before any data, dB
constexpr double JIT_A = T::NOISE_EMA_ALPHA;           // its adaptation per packet pair (ui-spec 5.5)
constexpr int32_t JIT_GAP_MS = T::NOISE_PAIR_GAP_MS;   // packets further apart don't form a pair

// Log-distance path-loss model: rssi = p0 - 10*n*log10(d / 1 m).
class PathLoss {
 public:
  double p0;
  double n;

  explicit PathLoss(double p0_dbm = T::P1M_NOMINAL_DBM, double n_ = T::PATH_LOSS_N) : p0(p0_dbm), n(n_) {}
  double rssi_to_dist(double rssi) const { return pow(10.0, (p0 - rssi) / (10.0 * n)); }
  double dist_to_rssi(double d) const {
    if (d < 0.05) d = 0.05;
    return p0 - 10.0 * n * log10(d);
  }
};

// Base class: subclasses override update() and set the public fields.
class RangeEstimator {
 public:
  PathLoss pl;
  // mean |rssi_k - rssi_k-1| of own packets, dB (noise_db's source). Kept across
  // reset(): relink/SEARCHING forget the range, not the channel roughness.
  double jit = JIT_INIT;
  std::optional<double> rssi_f;      // filtered RSSI, dBm
  double rssi_var;                   // variance of rssi_f, dB^2 (some add shadowing for dist_lo/hi)
  std::optional<double> noise_db;    // learnt packet-to-packet RSSI noise sd, dB (channel roughness)
  double rate_db_s;                  // d(rssi_f)/dt, + means getting closer
  std::optional<double> dist_m;      // point estimate
  std::optional<double> dist_lo_m;   // ~1-sigma lower bound
  std::optional<double> dist_hi_m;   // ~1-sigma upper bound
  int32_t trend;                     // +1 warmer, -1 colder, 0 unsure
  double trend_conf;                 // 0..1
  std::optional<ticks_t> last_t;     // ms of the last accepted measurement

  // Python's __init__ calls the subclass reset(); a C++ base constructor
  // cannot, so each subclass constructor calls its own.
  explicit RangeEstimator(const PathLoss& path_loss = PathLoss()) : pl(path_loss) { RangeEstimator::reset(); }
  virtual const char* name() const { return "base"; }
  virtual void reset();
  // 1 m calibration (PAIRING calibrate, ui-spec 5.8): RSSI with the watches ~1 m apart.
  virtual void calibrate(double rssi_at_1m) { pl.p0 = rssi_at_1m; }
  // Path-loss exponent for RSSI -> metres (Game.set_place: outdoor or indoor).
  void set_exponent(double n) { pl.n = n; }
  // A null motion pointer is Python's None (no hint from that watch).
  virtual void update(ticks_t t_ms, std::optional<double> rssi, std::optional<double> peer_rssi = std::nullopt,
                      const MotionInfo* my_motion = nullptr, const MotionInfo* peer_motion = nullptr) = 0;

 protected:
  ~RangeEstimator() = default;   // never deleted through the base (no heap)
  // Once per own packet: learn noise_db (ui-spec 5.5), the same way in every
  // estimator so the unreliable gate does not depend on which one runs.
  void note_noise(ticks_t t_ms, double rssi);
  void set_distance_from_rssi();

 private:
  std::optional<double> jz_;   // last own RSSI and its time, for the next pair
  ticks_t jt_;
};

}  // namespace est
}  // namespace hm
