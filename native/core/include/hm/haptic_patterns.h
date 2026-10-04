// finder/haptic_patterns.py: the 9 haptic patterns and a tick-driven player
// (ui-spec §7). A token array [on, off, on, ...] is a pattern of steps
// (on_ms, off_ms); everything plays by name at full strength.
//
// Two tracks feed one motor:
//  * events (play_named), ranked by RANK: dropped if one of the same or higher
//    rank started < EVENT_GUARD_MS ago (a TICK only by a higher rank: countdown
//    TICKs come at 1 Hz). A higher rank pre-empts; a lower one waits in a
//    one-slot queue (latest of the same or higher rank wins) and starts
//    MIN_GAP_MS after the higher one ends.
//  * heartbeats (heartbeat, or the metronome grid): FULL mode only; an event
//    cuts one, and they resume HB_RESUME_MS after the event ends.
// An accepted event lets a pulse that is on finish and starts MIN_GAP_MS after
// the motor last went off (§7: every gap >= 60 ms). tick(t) returns the motor
// strength (0 or 1) every loop step; nothing blocks.
//
// Python None: a name is Haptic NONE, a tick time std::nullopt. A pattern is
// held as its name (an index into PATTERNS), not a pointer.
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/ticks.h"
#include "hm/tuning.h"

namespace hm {
namespace haptic_patterns {

using OptTicks = std::optional<ticks_t>;

constexpr int32_t MIN_PULSE_MS = T::HAPTIC_MIN_PULSE_MS;   // ERM spin-up floor (60)
constexpr int32_t MIN_GAP_MS = T::HAPTIC_MIN_GAP_MS;
constexpr int32_t MAX_DUTY_PCT = (int32_t)(T::HAPTIC_MAX_DUTY * 100 + 0.5);   // 12
constexpr int32_t EVENT_GUARD_MS = T::HAPTIC_EVENT_GUARD_MS;   // 1000
constexpr int32_t HB_RESUME_MS = T::HAPTIC_HB_RESUME_MS;       // 1000
constexpr int32_t BLANKING_MS = T::HAPTIC_BLANKING_MS;         // 150: accel ignores pulse .. end + this

// The 9 names in T::HAPTIC_NAMES order (priority, highest first); NONE is None.
enum Haptic : int8_t { NONE = -1, FOUND, LOST, NOPE, HOLD, BATT, CLOSER, FARTHER, DOUBLE, TICK };
constexpr int N_NAMES = 9;

// The Python spelling ("TICK"), nullptr for NONE.
inline const char* name(Haptic h) { return h >= 0 && h < N_NAMES ? T::HAPTIC_NAMES[h] : nullptr; }
// PATTERNS.get(s): NONE for an unknown name (or nullptr).
Haptic from_name(const char* s);

// steps(arr) without copying: step i is (on(i), off(i)); the last off is 0.
struct Pattern {
  const int32_t* arr;
  int32_t len;
  constexpr int32_t n_steps() const { return (len + 1) / 2; }
  constexpr int32_t on(int32_t i) const { return arr[2 * i]; }
  constexpr int32_t off(int32_t i) const { return 2 * i + 1 < len ? arr[2 * i + 1] : 0; }
};

template <int N>
constexpr Pattern pattern_of(const int32_t (&a)[N]) {
  return Pattern{a, N};
}

constexpr Pattern PATTERNS[N_NAMES] = {
    pattern_of(T::HAPTIC_PATTERNS_FOUND),   pattern_of(T::HAPTIC_PATTERNS_LOST),
    pattern_of(T::HAPTIC_PATTERNS_NOPE),    pattern_of(T::HAPTIC_PATTERNS_HOLD),
    pattern_of(T::HAPTIC_PATTERNS_BATT),    pattern_of(T::HAPTIC_PATTERNS_CLOSER),
    pattern_of(T::HAPTIC_PATTERNS_FARTHER), pattern_of(T::HAPTIC_PATTERNS_DOUBLE),
    pattern_of(T::HAPTIC_PATTERNS_TICK),
};
constexpr int32_t RANK[N_NAMES] = {
    T::HAPTIC_RANK_FOUND,   T::HAPTIC_RANK_LOST,   T::HAPTIC_RANK_NOPE,
    T::HAPTIC_RANK_HOLD,    T::HAPTIC_RANK_BATT,   T::HAPTIC_RANK_CLOSER,
    T::HAPTIC_RANK_FARTHER, T::HAPTIC_RANK_DOUBLE, T::HAPTIC_RANK_TICK,
};

constexpr int32_t sum_ms(const Pattern& p) {
  int32_t s = 0;
  for (int32_t i = 0; i < p.len; i++) s += p.arr[i];
  return s;
}
// on + off, per name
constexpr int32_t TOTAL_MS[N_NAMES] = {
    sum_ms(PATTERNS[FOUND]),   sum_ms(PATTERNS[LOST]),   sum_ms(PATTERNS[NOPE]),
    sum_ms(PATTERNS[HOLD]),    sum_ms(PATTERNS[BATT]),   sum_ms(PATTERNS[CLOSER]),
    sum_ms(PATTERNS[FARTHER]), sum_ms(PATTERNS[DOUBLE]), sum_ms(PATTERNS[TICK]),
};

constexpr int32_t max_rank() {
  int32_t m = 0;
  for (int i = 0; i < N_NAMES; i++) m = RANK[i] > m ? RANK[i] : m;
  return m;
}
constexpr int NRANK = max_rank() + 1;

// buzz modes, in MENU order (finder.game BUZZ_* and its BUZZ row cycle use these)
constexpr int MODE_FULL = 0;     // default
constexpr int MODE_EVENTS = 1;   // events only, no heartbeats
constexpr int MODE_OFF = 2;

// The haptic to keep when `name` is raised in a frame that already has `cur`
// (NONE = none yet): the higher rank, the later one on a tie.
inline Haptic stronger(Haptic cur, Haptic name) { return cur == NONE || RANK[name] >= RANK[cur] ? name : cur; }

// Total motor-on time of a pattern.
constexpr int32_t on_ms(const Pattern& p) {
  int32_t s = 0;
  for (int32_t i = 0; i < p.n_steps(); i++) s += p.on(i);
  return s;
}

// Shortest repeat period keeping a pattern at <= MAX_DUTY_PCT duty: the
// Python's ceiling -(-on * 100 // pct), here for on >= 0.
constexpr int32_t min_period(const Pattern& p) { return (on_ms(p) * 100 + MAX_DUTY_PCT - 1) / MAX_DUTY_PCT; }

// Accelerometer blanking window around haptic pulses: extend(t, ms) when a
// pattern starts at t (ms = its length + BLANKING_MS), active(t) while inside,
// expire(now) once per tick so a passed window is never compared again.
// Overlapping windows merge; no allocation.
class BlankWindow {
 public:
  BlankWindow() { reset(); }
  void reset() {
    t0 = 0;
    until.reset();
  }
  void extend(ticks_t t, int32_t ms);
  bool active(ticks_t t) const { return until && ticks_diff(t, t0) >= 0 && ticks_diff(*until, t) > 0; }
  void expire(ticks_t now) {
    if (until && ticks_diff(*until, now) <= 0) until.reset();
  }

  ticks_t t0;
  OptTicks until;
};

// _Track: plays one pattern; exact edges when ticked every ms, never skips a pulse.
struct Track {
  Haptic pat = NONE;   // the pattern playing (NONE: None)
  int32_t i = 0;
  bool on = false;
  bool shown = false;
  bool stop = false;   // end after the pulse that is on
  OptTicks ph;         // current phase start (None: at next tick)
  OptTicks end;        // when the last pattern ended

  void start(Haptic p, OptTicks t);
  // End of the pulse being output now (None if no pulse is on).
  OptTicks pulse_end() const;
  double advance(ticks_t t);
};

// Tick-driven event + heartbeat player (see the top of this file). Timing is
// exact when ticked every ms: a step's pulse is on for [start, start + on_ms)
// and off for the following off_ms. A pulse first output by a late tick starts
// at that tick and still plays its full on_ms; a pulse ended by a late tick
// still gets its full off time. Blocked metronome beats are dropped, but the
// beat grid is kept.
class HapticPlayer {
 public:
  explicit HapticPlayer(int mode = MODE_FULL);

  // MODE_*: OFF silences and cancels; EVENTS mutes heartbeats only.
  void set_mode(int m);
  // True while an event (or its queued successor) plays.
  bool busy() const { return ev().pat != NONE || pend_ != NONE; }
  // True while any pattern (event, queued event or heartbeat) plays.
  bool active() const { return busy() || hb().pat != NONE; }

  // Start event `name` at t_ms (None: the next tick); returns accepted. NONE
  // (an unknown name) returns false.
  bool play_named(Haptic name, OptTicks t_ms = std::nullopt);
  // True if a heartbeat may start at t (FULL, no event, resumed).
  bool hb_allowed(OptTicks t) const;
  // Start heartbeat `name` if allowed; returns started.
  bool heartbeat(Haptic name, OptTicks t_ms = std::nullopt);

  // Beat pattern `name` (NONE: keep the last, TICK at first) every period_ms;
  // <= 0 stops it. The period is raised to min_period so duty stays <=
  // MAX_DUTY_PCT. Starting from stopped beats at t_ms (or the next tick); a
  // tempo change keeps phase: the next beat is the last beat + the new period
  // (fired at once if that is already past).
  void set_metronome(int32_t period_ms, OptTicks t_ms = std::nullopt, Haptic name = NONE);
  int32_t period_ms() const { return period_; }

  // Advance to t (ticks ms) and return the motor strength 0..1.
  double tick(ticks_t t);

  int mode = MODE_FULL;

 private:
  // The Python swaps _ev and _hb on pre-emption; here an index picks which track is which.
  Track& ev() { return tr_[ev_i_]; }
  Track& hb() { return tr_[1 - ev_i_]; }
  const Track& ev() const { return tr_[ev_i_]; }
  const Track& hb() const { return tr_[1 - ev_i_]; }
  void start_event_(Haptic pattern, int32_t p, OptTicks t_ms, OptTicks t);
  void beat_(ticks_t t);

  Track tr_[2];
  int ev_i_ = 0;
  int32_t rank_ = -1;       // rank of the playing event
  Haptic pend_ = NONE;      // one-slot queue: waits for a higher event
  int32_t pend_rank_ = -1;
  OptTicks ev_t_[NRANK];    // last start per rank (drop rule)
  OptTicks ev_end_;         // last event end (heartbeat resume)
  OptTicks now_;            // last tick time
  bool on_ = false;         // motor output at the last tick
  OptTicks off_t_;          // when the output last fell to 0 (< MIN_GAP_MS ago)
  // metronome
  int32_t period_ = 0;
  OptTicks next_;
  Haptic beat_pat_ = TICK;
};

}  // namespace haptic_patterns
}  // namespace hm
