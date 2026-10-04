// finder/estimators/kalman2.py: two-state Kalman filter [rssi, rate] with
// IMU-bounded process noise, the game's default range estimator.
//
// The rate is a mean-reverting (Singer) state whose spread follows what the
// step counters allow, |d rssi/dt| <= 10 n / ln10 * (v_me + v_peer) / d, with
// a hard clamp at CAP_K times that bound. Both watches still -> the rate is
// pinned to 0 and the RSSI is averaged hard. Peer-reported RSSI is a second
// measurement once its offset has been averaged over PEER_MIN_N reports.
// Innovations are Huber-clipped, tighter on the fade side. Measurement noise
// is the shared noise_db clamped to [SIG_MIN, SIG_MAX], which also sets the
// body/fade bias. Game.set_place() switches the exponent with set_exponent().
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/estimator.h"

namespace hm {
namespace est {
namespace kalman2 {

constexpr double N_EFF = T::PATH_LOSS_N;   // outdoor; Game.set_place() switches
constexpr double BIAS_A = -6.0;      // p0 = cal - (BIAS_A + BIAS_B * fading sigma): -4.5 dB on the
constexpr double BIAS_B = 1.0;       // cleanest channel (sigma 1.5) to +2 dB on the roughest (sigma 8)
constexpr double STRIDE_M = 0.8;     // generous stride for the speed bound
constexpr double V_UNKNOWN = 1.5;    // m/s assumed when a watch sends no motion
constexpr double STILL_HZ = 0.7;     // cadence below this with activity "still" -> not walking
constexpr int32_t STEP_HOLD_MS = 1300;   // no new step for this long -> still (> 1 s chip poll + tick)
constexpr double D_MIN = 1.5;        // m, floor for the rate bound
constexpr double TAU_V = 60.0;       // s, rate mean reversion while moving
constexpr double TAU_V_STILL = 0.3;  // s, rate decay while both still
constexpr double START_K = 0.5;      // rate spread (units of the bound) when a walk starts
constexpr double CAP_K = 1.5;        // hard clamp on |rate| in units of the bound
constexpr double Q_R_STILL = 0.1;    // dB^2/s, slow environment drift
constexpr double Q_R_MOVE = 0.15;    // dB^2/s per m/s walked (shadowing decorrelation)
constexpr double SIG_MIN = 1.5;      // dB, clamp on the fading sigma (noise_db)
constexpr double SIG_MAX = 8.0;
constexpr double K_UP = 2.5;         // Huber clip (sigmas) for innovations above the prediction
constexpr double K_DOWN = 1.0;       // ... and below it (fades, blocking)
constexpr double PEER_ALPHA = 0.02;  // bias learning for peer-reported RSSI
constexpr int32_t PEER_MIN_N = 20;   // peer reports averaged into peer_bias before the peer RSSI is used
constexpr double Z_ON = 0.05;        // |rate|/sd to start a trend while moving
constexpr double Z_FLIP = 0.3;       // opposite-sign |rate|/sd to flip it
constexpr int32_t MAX_GAP_MS = 3000; // longer silence -> trend unsure, rate reset
constexpr double SH_VAR = 9.0;       // dB^2 of shadowing added to the distance spread

// Speed hint from one watch: cadence * stride, zeroed as soon as steps stop
// (whatever the activity code: an unknown one is usually a fidgeting wrist).
class Mot {
 public:
  std::optional<uint32_t> steps;
  std::optional<ticks_t> t;
  double v = V_UNKNOWN;

  void feed(ticks_t t_ms, const MotionInfo* m);   // m null: no motion sent (None)
};

}  // namespace kalman2

class Kalman2 : public RangeEstimator {
 public:
  double cal;                  // the 1 m calibration (p0 before the bias)
  std::optional<double> bias;  // nullopt: recomputed on the next publish
  double p00, p01, p11;        // covariance of [rssi, rate]
  double sig;                  // fading sigma: noise_db clamped, set on every packet
  double peer_bias;
  int32_t peer_n;
  double speed;                // m/s, my speed hint + the partner's
  double vmax;                 // the rate bound, dB/s

  explicit Kalman2(const PathLoss& path_loss = PathLoss(T::P1M_NOMINAL_DBM, kalman2::N_EFF));
  const char* name() const override { return "kalman2"; }
  void reset() override;
  void calibrate(double rssi_at_1m) override;
  void update(ticks_t t_ms, std::optional<double> rssi, std::optional<double> peer_rssi = std::nullopt,
              const MotionInfo* my_motion = nullptr, const MotionInfo* peer_motion = nullptr) override;

 private:
  kalman2::Mot me_;
  kalman2::Mot peer_;

  void predict(double dt);
  void correct(double z, double rv);
  void update_trend();   // Python's _trend (a method cannot share the field's name)
  void publish();
};

}  // namespace est
}  // namespace hm
