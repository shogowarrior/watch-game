// finder/scan.py: the guided 360-degree body-turn scan (ui-spec §6 SCANNING,
// tokens thresholds.scan).
//
// Phases: READY (flat check + 3 s countdown that holds until flat), SWEEP
// (12 s of active time; the wedge turns at 30 deg/s and holds while a fault
// pauses it) and RESULT (theta + s0, or a no-fix reason).
//
// Direction comes from raw per-packet RSSI (own RSSI and the partner's
// reported rssi_last, not rssi_filt), each tagged with the wedge angle phi at
// arrival and fitted with the first circular harmonic
// y = a0_src + a1*cos(phi - theta), one a0 per source.
//
// Drive it with on_motion / on_packet as data arrives, on_peer before an
// update while the partner's beacon is fresh, cancel on a tap or short press
// and update(t_ms) once per logic frame (10 Hz), then read the render outputs
// (sub, countdown, top_text, word, toast, sweep(), mirror, pop_haptic(),
// result). Accelerometer blanking around haptic pulses comes from blank_fn.
//
// Python None: a reason, fault, countdown, time, bin or fit value is a
// std::optional, a haptic is haptic_patterns::NONE, a text nullptr. The
// samples live in fixed float arrays (the Python's array('f')), CAP long.
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/compat.h"
#include "hm/haptic_patterns.h"
#include "hm/py.h"
#include "hm/session.h"
#include "hm/tuning.h"

namespace hm {
namespace scan {

constexpr int32_t DURATION_MS = T::SCAN_DURATION_MS;
constexpr int32_t READY_MS = T::SCAN_READY_MS;
constexpr double DEG_PER_S = T::SCAN_DEG_PER_S;
constexpr double MIN_P2T_DB = T::SCAN_MIN_P2T_DB;   // no fix if 2*a1 below this
constexpr double MAX_S0_DEG = T::SCAN_MAX_S0_DEG;
constexpr double FLOOR_DEG = T::SCAN_FLOOR_DEG;
constexpr double TILT_FAULT_DEG = T::SCAN_TILT_FAULT_DEG;
constexpr int32_t TILT_FAULT_MS = T::SCAN_TILT_FAULT_MS;
constexpr int32_t ABORT_STEPS = T::SCAN_ABORT_STEPS;
constexpr int32_t ABORT_PAUSE_MS = T::SCAN_ABORT_PAUSE_MS;
constexpr int BINS = T::SCAN_BINS;
constexpr int32_t MIN_PACKETS = T::SCAN_MIN_PACKETS_PER_BIN;
constexpr double FLAT_DEG = T::SCAN_FLAT_DEG;            // face-up within 20 deg = flat ...
constexpr int32_t FLAT_HOLD_MS = T::SCAN_FLAT_HOLD_MS;   // ... held 0.5 s
constexpr int WALK_STEPS = T::SCAN_WALK_STEPS;           // >= 3 steps in 2 s = walking
constexpr int32_t WALK_WINDOW_MS = T::SCAN_WALK_WINDOW_MS;
constexpr int32_t PEER_WALK_PENALTY_MS = T::SCAN_PEER_WALK_WIDEN_MS;
constexpr int32_t PEER_WALK_FAIL_MS = T::SCAN_PEER_WALK_FAIL_MS;
constexpr double PEER_WALK_DEG = T::SCAN_PEER_WALK_WIDEN_DEG;
constexpr double TICK_EVERY_DEG = T::SCAN_TICK_DEG;
constexpr int32_t BLINK_MS = 4 * T::SCAN_BLINK_MS;   // best bin blinks twice (SCAN_BLINK_MS on/off)
constexpr int32_t MORPH_MS = T::SCAN_MORPH_MS;
// spec-silent internals
constexpr double FLAT_OFF_DEG = 30.0;   // hysteresis (as motion face_up)
constexpr int32_t MAX_DT_MS = 1000;     // a stalled loop never jumps the wedge further
constexpr int32_t MIN_FIT_N = 12;
constexpr int CAP = DURATION_MS * T::BEACON_HZ_SCAN / 500 + 160;   // 2 sources at the scan rate + margin (640)

constexpr uint8_t SRC_OWN = 0;
constexpr uint8_t SRC_PEER = 1;

enum Phase : int8_t { READY, SWEEP, RESULT };
enum Fault : int8_t { FAULT_TILT, FAULT_WALK };
enum Reason : int8_t { R_NO_FIX, R_FRIEND_MOVED, R_ABORT, R_CANCEL };
// The Python spellings ("ready", "tilt", "no_fix").
const char* name(Phase p);
const char* name(Fault f);
const char* name(Reason r);
// TOASTS.get(reason): nullptr for R_CANCEL.
const char* toast_of(Reason r);

constexpr const char* HINT_CHEST = "HOLD AT CHEST";
constexpr const char* HINT_FLAT = "HOLD FLAT";
constexpr const char* WORD_TURN = "TURN RIGHT";

// fit_harmonic's (a1_db, theta_deg 0..360, sigma_res_db, n).
struct Fit {
  double a1_db;
  double theta_deg;
  double sigma_res_db;
  int32_t n;
};
// LSQ fit of val = a0[src] + A cos(phi) + B sin(phi) over n samples, two-pass
// (demeaned per source); none if degenerate.
std::optional<Fit> fit_harmonic(const float* phi, const float* val, const uint8_t* src, int32_t n);
// deg(sigma_res / (a1 * sqrt(N/2))): theta sd of a harmonic fit.
double sigma_fit_deg(double a1, double sigma_res, int32_t n);
// evaluate's (ok, s0_deg, reason).
struct Verdict {
  bool ok;
  std::optional<double> s0_deg;
  std::optional<Reason> reason;
};
// Fit quality gate.
Verdict evaluate(double a1, double sigma_res, int32_t n, int32_t peer_walk_ms = 0);

// sweep()'s (wedge_deg, bins, active_bin, paused); bins points at the
// session's BINS values, updated in place.
struct Sweep {
  double wedge_deg;
  const std::optional<double>* bins;
  std::optional<int32_t> active_bin;
  bool paused;
};
// result's (theta_deg, s0_deg).
struct Result {
  double theta_deg;
  double s0_deg;
};

// blank_fn(ctx, t_ms): true while a haptic pulse blanks the accelerometer.
using BlankFn = bool (*)(void* ctx, ticks_t t_ms);

// One scan, from the tap to the result. Allocation-free.
class ScanSession {
 public:
  explicit ScanSession(ticks_t t_ms, BlankFn blank_fn = nullptr, void* blank_ctx = nullptr);
  void reset(ticks_t t_ms);

  // ---- inputs
  bool blanked(ticks_t t_ms) const { return blank_fn && blank_fn(blank_ctx, t_ms); }
  // IMU update: tilt from flat (deg), cumulative step count, ACT_* code. Tilt
  // and step (shake) samples inside a blanking window are ignored.
  void on_motion(ticks_t t_ms, std::optional<double> tilt_deg = std::nullopt,
                 std::optional<int32_t> steps = std::nullopt, std::optional<int32_t> activity = std::nullopt);
  // One received beacon: own raw RSSI and/or the partner's raw rssi_last.
  void on_packet(ticks_t t_ms, std::optional<double> rssi = std::nullopt,
                 std::optional<double> peer_rssi = std::nullopt);
  // Partner walking, for the next update only (Game calls it each logic tick
  // while the partner's beacon is fresh).
  void on_peer(ticks_t, bool walking) { peer_walk_ = walking; }
  // Short press / tap: stop ready/sweep with no arrow; false (no-op) once in RESULT.
  bool cancel(ticks_t t_ms);

  // ---- per frame
  // Advance phases (10 Hz); pop_haptic() then gives the frame's haptic.
  void update(ticks_t t_ms);
  haptic_patterns::Haptic pop_haptic() {
    const haptic_patterns::Haptic h = haptic_;
    haptic_ = haptic_patterns::NONE;
    return h;
  }

  // ---- render outputs
  Phase sub() const { return phase; }
  // True during ready/sweep: set the beacon scan flag and beacon at 20 Hz.
  bool active() const { return phase != RESULT; }
  // ui-spec §5.7 live mirror of own raw RSSI (0..1), none before a packet.
  std::optional<double> mirror() const { return lm_.value; }
  const char* top_text() const { return phase == READY ? (flat ? HINT_CHEST : HINT_FLAT) : nullptr; }
  const char* word() const { return phase == READY ? WORD_TURN : nullptr; }
  const char* toast() const { return reason ? toast_of(*reason) : nullptr; }
  // On a fix; theta is clockwise from the heading at sweep start (screen-up).
  std::optional<Result> result() const;
  int32_t active_bin() const { return (int32_t)((wedge_deg + 15.0) / 30.0) % BINS; }
  // In RESULT (fix) wedge_deg is theta (the morph target) and the active bin
  // is the best bin while its blink is on.
  std::optional<Sweep> sweep(opt_ticks t_ms = std::nullopt) const;
  // True when the caller should leave SCANNING (reveal or zone screen).
  bool done(ticks_t t_ms) const;

  BlankFn blank_fn;
  void* blank_ctx;
  float phi[CAP];
  float val[CAP];
  uint8_t src[CAP];
  std::optional<double> bins[BINS];
  Phase phase;
  int32_t n;
  // motion
  int32_t activity;
  int32_t steps;
  // ready
  bool flat;
  std::optional<int32_t> countdown;
  // sweep
  int32_t active_ms;
  int32_t pause_ms;
  double wedge_deg;
  bool paused;
  std::optional<Fault> fault;
  int32_t peer_walk_ms;
  opt_ticks t_sweep;
  // result
  opt_ticks t_result;
  std::optional<Reason> reason;
  std::optional<double> theta_deg;
  std::optional<double> s0_deg;
  std::optional<double> sigma_fit_deg;
  std::optional<double> a1_db;
  std::optional<int32_t> best_bin;

 private:
  void emit_(haptic_patterns::Haptic h) { haptic_ = haptic_patterns::stronger(haptic_, h); }
  void update_ready_(ticks_t t, int32_t dt);
  void start_sweep_(ticks_t t);
  void update_sweep_(ticks_t t, int32_t dt);
  void fail_(ticks_t t, Reason r);
  void finish_(ticks_t t);
  void add_(double phi_deg, double y, uint8_t s);
  double median_(int b, uint8_t s, int32_t* k_out);
  void refresh_bins_();

  float scratch_[CAP];
  int32_t seg_lo_[BINS + 1];   // seg BINS = bin 0 after 345 deg
  int32_t seg_hi_[BINS + 1];
  float med_[2 * BINS];        // per bin, per source
  int32_t bn_[2 * BINS];
  uint8_t dirty_[BINS];
  float raw_[BINS];
  uint8_t rawok_[BINS];
  float sm_[BINS];
  float sum_[2];
  int32_t cnt_[2];
  TickRing<WALK_STEPS> steps_t_;   // the walk fault: >= 3 steps in 2 s
  session::LiveMirror lm_;
  ticks_t t_last_;
  opt_ticks flat_since_;
  opt_ticks over_since_;
  bool tilt_fault_;
  std::optional<int32_t> steps_last_;
  int32_t cd_ms_;
  int32_t digit_;
  double next_tick_;
  bool peer_walk_;
  haptic_patterns::Haptic haptic_;

  friend struct Probe;   // the trace test: a Python test's call of a private method
};

}  // namespace scan
}  // namespace hm
