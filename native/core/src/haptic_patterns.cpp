#include "hm/haptic_patterns.h"

#include <string.h>

namespace hm {
namespace haptic_patterns {

Haptic from_name(const char* s) {
  if (s == nullptr) return NONE;
  for (int i = 0; i < N_NAMES; i++) {
    if (strcmp(s, T::HAPTIC_NAMES[i]) == 0) return (Haptic)i;
  }
  return NONE;
}

void BlankWindow::extend(ticks_t t, int32_t ms) {
  const ticks_t end = ticks_add(t, ms);
  if (!until || ticks_diff(t, *until) >= 0) {
    t0 = t;
    until = end;
  } else if (ticks_diff(end, *until) > 0) {
    until = end;
  }
}

// ---- Track -----------------------------------------------------------------

void Track::start(Haptic p, OptTicks t) {
  pat = p;
  i = 0;
  on = true;
  shown = false;
  stop = false;
  ph = t;
}

OptTicks Track::pulse_end() const {
  if (pat == NONE || !on || !shown) return std::nullopt;
  return ticks_add(*ph, PATTERNS[pat].on(i));
}

double Track::advance(ticks_t t) {
  const Pattern& p = PATTERNS[pat];
  if (!ph) ph = t;
  while (true) {
    const int32_t on_ms_ = p.on(i), off_ms = p.off(i);
    if (on) {
      if (ticks_diff(t, *ph) < 0) return 0.0;   // scheduled in the future
      if (!shown) {                              // first output tick; late -> full pulse from t
        ph = t;
        shown = true;
        return 1.0;
      }
      if (ticks_diff(t, ticks_add(*ph, on_ms_)) < 0) return 1.0;
      on = false;
      ph = t;                                    // off phase from the real off edge (§7 gap)
      if (stop) {
        stop = false;
        pat = NONE;
        end = ph;
        return 0.0;
      }
    } else {
      const ticks_t e = ticks_add(*ph, off_ms);
      if (ticks_diff(t, e) < 0) return 0.0;
      i += 1;
      if (i >= p.n_steps()) {
        pat = NONE;
        end = e;
        return 0.0;
      }
      on = true;
      shown = false;
      ph = e;
    }
  }
}

// ---- HapticPlayer ----------------------------------------------------------

HapticPlayer::HapticPlayer(int m) { set_mode(m); }

void HapticPlayer::set_mode(int m) {
  mode = m;
  if (m != MODE_FULL) hb().pat = NONE;
  if (m == MODE_OFF) {
    ev().pat = NONE;
    pend_ = NONE;
    rank_ = -1;
  }
}

bool HapticPlayer::play_named(Haptic name, OptTicks t_ms) {
  if (name == NONE || mode == MODE_OFF) return false;
  const int32_t p = RANK[name];
  const OptTicks t = t_ms ? t_ms : now_;
  if (t) {
    const int32_t g = EVENT_GUARD_MS;
    for (int32_t r = p > 0 ? p : 1; r < NRANK; r++) {
      if (ev_t_[r]) {
        const int32_t d = ticks_diff(*t, *ev_t_[r]);
        if (-g < d && d < g) return false;   // same/higher started < guard ago
      }
    }
  }
  if (ev().pat != NONE && rank_ > p) {
    if (pend_ == NONE || p >= pend_rank_) {
      pend_ = name;                          // after the higher one ends
      pend_rank_ = p;
      return true;
    }
    return false;
  }
  start_event_(name, p, t_ms, t);
  return true;
}

void HapticPlayer::start_event_(Haptic pattern, int32_t p, OptTicks t_ms, OptTicks t) {
  OptTicks e = ev().pulse_end();
  if (e) {                                   // pre-empted mid-pulse: ends on the other track
    ev_i_ = 1 - ev_i_;
    hb().stop = true;
  } else {
    e = hb().pulse_end();
    if (e) {
      hb().stop = true;                      // the heartbeat pulse plays out, then stops
    } else {
      hb().pat = NONE;                       // an event replaces the heartbeat
      e = off_t_;
    }
  }
  OptTicks s = t_ms;
  if (e) {                                   // §7: >= MIN_GAP_MS after the motor went off
    const ticks_t g = ticks_add(*e, MIN_GAP_MS);
    if (!s || ticks_diff(g, *s) > 0) s = g;
  }
  ev().start(pattern, s);
  rank_ = p;
  ev_t_[p] = t;
}

bool HapticPlayer::hb_allowed(OptTicks t) const {
  if (mode != MODE_FULL || ev().pat != NONE || pend_ != NONE) return false;
  return !ev_end_ || !t || ticks_diff(*t, *ev_end_) >= HB_RESUME_MS;
}

bool HapticPlayer::heartbeat(Haptic name, OptTicks t_ms) {
  if (name == NONE || !hb_allowed(t_ms ? t_ms : now_)) return false;
  hb().start(name, t_ms);
  return true;
}

bool HapticPlayer::cancel_heartbeat() {
  Track& h = hb();
  if (h.waiting(now_)) {
    h.pat = NONE;
    return true;
  }
  return false;
}

void HapticPlayer::set_metronome(int32_t period_ms, OptTicks t_ms, Haptic name) {
  if (period_ms <= 0) {
    period_ = 0;
    return;
  }
  if (name != NONE) beat_pat_ = name;
  const int32_t lo = min_period(PATTERNS[beat_pat_]);
  if (period_ms < lo) period_ms = lo;
  const int32_t old = period_;
  if (old == period_ms) return;
  if (old && next_) {
    next_ = ticks_add(*next_, period_ms - old);
  } else {
    next_ = t_ms;
  }
  period_ = period_ms;
}

double HapticPlayer::tick(ticks_t t) {
  now_ = t;
  double lvl = 0.0;
  Track& ev = this->ev();                    // the Python's local: nothing below swaps the tracks
  if (ev.pat != NONE) {
    lvl = ev.advance(t);
    if (ev.pat == NONE) {
      ev_end_ = ev.end;
      rank_ = -1;
      if (pend_ != NONE) {
        const Haptic pat = pend_;
        const int32_t p = pend_rank_;
        pend_ = NONE;
        const ticks_t t0 = ticks_add(*ev.end, MIN_GAP_MS);   // keep pulses distinct
        start_event_(pat, p, t0, t0);
        lvl = ev.advance(t);
      }
    }
  } else if (ev_end_ && ticks_diff(t, *ev_end_) >= HB_RESUME_MS) {
    ev_end_.reset();                         // resume window over (wrap-safe)
  }
  if (period_) beat_(t);
  if (hb().pat != NONE) {
    const double h = hb().advance(t);
    if (lvl == 0.0) lvl = h;
  }
  if (mode == MODE_OFF) lvl = 0.0;
  if (lvl != 0.0) {
    on_ = true;
  } else if (on_) {
    on_ = false;
    off_t_ = t;
    if (ev.pat != NONE && ev.on && !ev.shown) {   // §7: gap from the real off edge
      const ticks_t g = ticks_add(t, MIN_GAP_MS);
      if (ticks_diff(g, *ev.ph) > 0) ev.ph = g;     // (advance above has set ev.ph)
    }
  } else if (off_t_ && ticks_diff(t, *off_t_) >= MIN_GAP_MS) {
    off_t_.reset();                          // gap over (wrap-safe)
  }
  return lvl;
}

// Advance the beat grid; start a heartbeat on each allowed beat.
void HapticPlayer::beat_(ticks_t t) {
  if (!next_) next_ = t;
  const int32_t d = ticks_diff(t, *next_);
  if (d >= 0) {
    const ticks_t sched = ticks_add(*next_, d - d % period_);
    next_ = ticks_add(sched, period_);
    if (hb_allowed(t)) hb().start(beat_pat_, sched);   // else: drop beat, keep grid
  }
}

}  // namespace haptic_patterns
}  // namespace hm
