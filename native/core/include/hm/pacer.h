// app/pacer.py: the frame lock, frames on an even time grid at a rate the
// watch can hold.
//
//     hm::pacer::FramePacer pc(now);
//     if (ticks_diff(now, pc.t_next) >= 0) {              // a frame is due
//       ticks_t slot = pc.begin(now, params.fps_cap, busy_ms);
//       renderer.frame(params, display, slot);           // animate to the slot, not to now
//       pc.shown(clock());                               // the frame is on the panel
//     }
//
// begin(now) returns the frame's slot, the latest grid time at or before now,
// so every drawn frame moves the animation on by whole periods; slots the loop
// misses entirely are skipped (missed). The rate is one of T::FPS_LOCKS, at
// most the cap: it drops at once when cost (the second largest busy_ms of the
// last T::FPS_COST_N) passes the period, and rises one step after
// T::FPS_RAISE_MS in which cost plus T::FPS_MARGIN_PCT fitted the faster
// period. restart(now) (screen woken) draws the next frame at once and ignores
// the busy time across the wake; begin without busy_ms (a state-only frame)
// keeps the lock. The log counters (frames, missed, late_*, iv_*) are read
// and cleared by window(). Integer work, nothing allocated.
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/py.h"
#include "hm/ticks.h"
#include "hm/tuning.h"

namespace hm {
namespace pacer {

constexpr int N_LOCKS = sizeof(T::FPS_LOCKS) / sizeof(T::FPS_LOCKS[0]);
constexpr int N_COST = T::FPS_COST_N;
constexpr int64_t SUM_LIMIT = 0x1FFFFFFF;   // interval sums halve past this (MicroPython small ints)

class FramePacer {
 public:
  // cap 0 (Python None): the fastest lock.
  explicit FramePacer(ticks_t now = 0, int32_t cap = 0) { reset(now, cap); }

  void reset(ticks_t now, int32_t cap = 0);
  // Screen back on: the next frame is due at once and drawn at the time it
  // starts; the busy time across the wake is ignored.
  void restart(ticks_t now);
  // Frame due at now (t_next reached): returns its slot time. busy_ms: the
  // loop's busy time since the previous drawn frame (none: a state-only frame).
  ticks_t begin(ticks_t now, int32_t cap, std::optional<int32_t> busy_ms = std::nullopt);
  // The frame is on the panel at t: interval stats.
  void shown(ticks_t t);
  // Clear the log counters (frames, missed, late_*, iv_*).
  void window();
  // Standard deviation of the shown-frame interval this window, ms.
  double jitter_ms() const;

  int i;               // index of the lock in T::FPS_LOCKS
  int32_t fps;
  int32_t period;      // ms
  ticks_t t_next;      // the next frame's slot
  int32_t cost;        // second largest busy of the ring, ms
  int32_t changes;     // lock changes since reset
  int32_t frames, missed, late_sum, late_max;
  int32_t iv_n, iv_sum;
  int64_t iv_sq;       // a stray interval's square can pass 2^31
  int32_t iv_max;

 private:
  friend struct Probe;
  static int cap_index_(int32_t cap);   // the Python caches it; six compares here
  void note_(int32_t busy_ms);
  void choose_(ticks_t now, int32_t cap);
  void set_(int i);

  int32_t cost_[N_COST] = {};   // busy_ms ring
  int k_ = 0;                   // ... next slot
  int n_ = 0;                   // ... filled entries
  opt_ticks up_t_;              // since then a faster lock has fitted
  bool skip_ = false;           // next busy_ms spans a wake: ignore it
  bool anchor_ = false;         // next frame restarts the grid at its start
  opt_ticks last_;              // when the previous frame was shown
};

}  // namespace pacer
}  // namespace hm
