// Partner link bookkeeping (MAC lock, seq dedup, loss window) and the TX
// schedule (finder/link.py). Integer-only and allocation-free per packet.
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/py.h"

namespace hm {
namespace link {

// on_packet results
enum : int {
  R_BAD = 0,         // not our magic/version/game
  R_PARTNER = 1,     // new frame from the locked partner
  R_OTHER = 2,       // valid beacon from a non-partner while locked
  R_CANDIDATE = 3,   // valid beacon while unlocked
  R_DUP = 4,         // partner frame with repeated seq
};

// Tiny PRNG (period 65535).
class Xorshift16 {
 public:
  explicit Xorshift16(int seed = 0xACE1) : x((seed & 0xFFFF) ? (seed & 0xFFFF) : 0xACE1) {}
  int next();
  int x;
};

// Beacon schedule: every period ms +/- jitter ms. Targets advance from the
// previous target so the mean rate holds on a coarse loop; after a stall it
// restarts from now.
class TxScheduler {
 public:
  explicit TxScheduler(int period_ms = 50, int jitter_ms = 5, int seed = 0xACE1)
      : period(period_ms), jitter(jitter_ms), rng(seed) {}
  int interval();
  bool due(ticks_t now) const { return !next_t || ticks_diff(now, *next_t) >= 0; }
  void mark_sent(ticks_t now);

  int period;
  int jitter;
  Xorshift16 rng;
  opt_ticks next_t;
};

// Tracks one partner: seq dedup, restarts, loss over the last loss_window gaps.
class LinkMonitor {
 public:
  static constexpr int MAX_WINDOW = 64;   // loss_window is clamped to this
  explicit LinkMonitor(int game_id = 0, int loss_window = 40, int max_gap = 200);

  void reset_link();   // forget seq/loss/last-seen (keeps the lock and counters)
  void lock(const Mac& mac);
  void unlock();
  int on_packet(const Mac& mac, const uint8_t* buf, int n, ticks_t t_rx);   // an R_* code
  int loss_pct() const;   // percent of partner beacons missed over the window
  std::optional<int32_t> age_ms(ticks_t now) const {
    return last_seen ? std::optional<int32_t>(ticks_diff(now, *last_seen)) : std::nullopt;
  }

  int game_id;
  int max_gap;   // seq jump beyond this = partner restarted
  int w;
  int n_bad = 0, n_other = 0, n_dup = 0, n_restart = 0;
  std::optional<Mac> partner;
  int n_rx = 0;
  int last_seq = -1;
  opt_ticks last_seen;

 private:
  void push_gap(int g);
  int32_t gaps_[MAX_WINDOW] = {};
  int gi_ = 0, gn_ = 0;
  int32_t gsum_ = 0;
};

}  // namespace link
}  // namespace hm
