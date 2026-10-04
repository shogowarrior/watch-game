// app/imu_feed.py: the BMA423 FIFO -> hm::motion::MotionTracker (out_hz, g)
// plus the bump spike detector.
//
//     hm::imu_feed::ImuFeed feed(imu, on_tap, ctx, 25);
//     feed.motor(t, level);       // every motor level change (haptic blanking)
//     feed.set_fast(armed);       // FAST_HZ while a bump can count, else SLOW_HZ
//     int n = feed.poll(now);     // every loop: drains the FIFO (170 frames)
//     game.set_tracker(now, feed.tracker);
//
// The FIFO runs in milli-g at SLOW_HZ, and at FAST_HZ (T::BUMP_ODR_HZ) while
// set_fast(true): at 800 Hz a 1-3 ms knock keeps most of its 3-8 g peak.
// Samples are time-stamped backwards from now (continuing the previous
// batch's clock, to a 1/256 ms, while it stays within the resync bound: 40 ms
// at 100 Hz, 10 at 800 Hz), block-averaged to out_hz and handed to the
// tracker in g.
//
// Bump spike (ui-spec §6, §7), fast rate only: a run of |a - g_lp| above
// RUN_G, spike_min_ms..spike_max_ms wide (taken at the run level), that peaks
// at SPIKE_G or more, not within refractory_ms of the last accepted one, and
// not overlapping a motor pulse or blank_ms after it (the feed keeps its own
// short pulse history: samples reach it up to a batch late). The gravity
// low-pass (160 ms at any rate, kept across rate switches) holds still during
// a run, and follows again once the run is longer than spike_max_ms. The
// thresholds are fields, as the Python's attributes a notebook tunes.
//
// The per-sample work is one integer kernel (feed_kernel) over a state array
// that reports block sums and run starts and ends as records; only those
// reach the tracker and the spike checks. Python's OSError from the bus is a
// status here: poll returns -1, set_fast false. Allocation-free.
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/haptic_patterns.h"
#include "hm/motion.h"
#include "hm/platform.h"
#include "hm/py.h"
#include "hm/ticks.h"
#include "hm/tuning.h"

namespace hm {
namespace imu_feed {

constexpr int32_t SLOW_HZ = 100;                 // tracker only (the BMA423's default odr)
constexpr int32_t FAST_HZ = T::BUMP_ODR_HZ;      // while a bump can count
constexpr double SPIKE_G = T::BUMP_SPIKE_G;      // ui-spec §6 HOT (tokens thresholds.bump_spike)
constexpr double RUN_G = T::BUMP_RUN_G;
constexpr int32_t SPIKE_MIN_MS = T::BUMP_SPIKE_MS[0];
constexpr int32_t SPIKE_MAX_MS = T::BUMP_SPIKE_MS[1];
constexpr int32_t REFRACTORY_MS = T::BUMP_REFRACTORY_MS;   // ringing after a knock is not a second bump
constexpr int32_t RESYNC_MIN_MS = 10;            // batch clock this far from now (or 4 samples): re-anchor
constexpr int G_SHIFT = 4;                       // gravity low-pass at 100 Hz: tau ~ 16 samples (160 ms)
constexpr int NP = 4;                            // motor pulses remembered for blanking

// kernel state (int32 each)
enum : int {
  S_GX = 0,      // gravity x, y, z in mg << shift
  S_SHIFT = 3,
  S_THR2 = 4,
  S_FAST = 5,
  S_RUN = 6,     // samples in the open run (0: none)
  S_PK = 7,      // its peak |a - g|^2
  S_SX = 8,      // block sums x, y, z and count
  S_SN = 11,
  S_DEC = 12,
  S_RMAX = 13,   // run length past which gravity follows again
  S_SEED = 14,   // 1 once gravity is seeded
  S_N = 15,
};
// kernel records (5 int32 each)
enum : int32_t {
  R_BLOCK = 1,   // (1, i, sx, sy, sz): block of dec samples ends at i
  R_START = 2,   // (2, i, 0, 0, 0): run starts at sample i
  R_END = 3,     // (3, i, run, peak, 0): run ended before sample i
};

// Gravity, spike runs and block sums over n samples (x, y, z mg each) into
// the state st; writes at most 2 records a sample to ev and returns how many.
// |a - g|^2 is exact; the state and records keep it in int32, as the Python's
// array('i') does (the chip's ±4 g range keeps it under 2^31).
int feed_kernel(const int16_t* mg, int n, int32_t* st, int32_t* ev);

// A spike accepted as a bump tap at t (the run's start).
using TapFn = void (*)(void* ctx, ticks_t t);

class ImuFeed {
 public:
  // imu: the BMA423 FIFO (fifo_read_mg, fifo_mg; set_odr and odr for the fast
  // rate). out_hz: the tracker's rate, at most motion::BUF_MAX (100). z_sign
  // none: the imu's own. The feed starts at the imu's odr; the first set_fast
  // puts the chip where it belongs.
  explicit ImuFeed(app::Imu& imu, TapFn on_tap = nullptr, void* tap_ctx = nullptr, int32_t out_hz = 50,
                   std::optional<int> z_sign = std::nullopt);
  ImuFeed(const ImuFeed&) = delete;
  ImuFeed& operator=(const ImuFeed&) = delete;

  // FAST_HZ (spikes detected) or SLOW_HZ; samples still in the FIFO are
  // dropped (set_odr empties it). False on a bus error: the feed then follows
  // whatever rate the chip reports (odr), and the next call retries.
  bool set_fast(bool on);
  // Motor output changed at t (level 0 = off).
  void motor(ticks_t t, double level);
  // True if t falls in a pulse or blank_ms after one ends (the game's blank_fn).
  bool blanked(ticks_t t) const;
  // Drain the FIFO; returns samples read, or -1 on a bus error (nothing
  // changed after the read). Sets tap (or none).
  int poll(ticks_t now);

  app::Imu& imu;
  int32_t out_hz;
  motion::MotionTracker tracker;
  TapFn on_tap;
  void* tap_ctx;
  int32_t thr2;               // run level, mg squared
  int32_t peak2;              // spike peak, mg squared
  int32_t spike_min_ms, spike_max_ms;
  int32_t refractory_ms;
  int32_t blank_ms;
  int32_t n_samples = 0;
  int32_t n_taps = 0;
  int32_t n_rejected = 0;     // runs too wide/narrow, blanked or refractory
  int32_t n_low = 0;          // runs that never reached the spike peak
  int32_t n_blanked = 0;
  opt_ticks last_tap;
  int32_t peak_mg = 0;        // largest |a - g| seen (debug / tuning)
  int32_t hz = 0;
  bool fast;
  int32_t dec = 1;            // FIFO samples a tracker sample
  int g_shift = G_SHIFT;
  opt_ticks tap;              // this poll's accepted tap

 private:
  void rate_(int32_t hz);
  ticks_t at_(int32_t i) const;
  void stamp_(ticks_t now, int n);
  void end_run_(ticks_t t_end, int32_t run, int32_t pk);

  ticks_t on_[NP] = {};       // pulse start times (ring)
  ticks_t off_[NP] = {};      // pulse end times (the newest: its start while on)
  int pi_ = 0;
  int pn_ = 0;
  bool lvl_on_ = false;
  int32_t st_[S_N] = {};
  int32_t ev_[5 * (2 * app::Imu::FIFO_FRAMES + 2)] = {};   // <= 2 records a sample
  ticks_t run_t_ = 0;
  double k_ = 0;              // block sum (mg) -> mean (g)
  int32_t dq_ = 0;            // sample period, ms in Q8
  int32_t resync_ = 0;
  opt_ticks tb_;              // this batch: first sample at tb_ + f0_/256 ms
  int32_t f0_ = 0;
  int n_ = 0;                 // ... and its size
};

}  // namespace imu_feed
}  // namespace hm
