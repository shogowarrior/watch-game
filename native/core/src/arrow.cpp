#include "hm/arrow.h"

#include <math.h>

namespace hm {
namespace arrow {

namespace {

// RenderParams.sub for each drawn phase (lock belongs to the walk sub-screen).
rp::Sub sub_of(Phase ph) {
  switch (ph) {
    case PH_REVEAL: return rp::Sub::REVEAL;
    case PH_TURN:
    case PH_FACE: return rp::Sub::TURN;
    default: return rp::Sub::WALK;
  }
}

// "%d O'CLOCK" % (h or 12), indexed by h.
constexpr const char* const CLOCK_WORDS[12] = {
    "12 O'CLOCK", "1 O'CLOCK", "2 O'CLOCK", "3 O'CLOCK", "4 O'CLOCK",  "5 O'CLOCK",
    "6 O'CLOCK",  "7 O'CLOCK", "8 O'CLOCK", "9 O'CLOCK", "10 O'CLOCK", "11 O'CLOCK",
};

double out_cubic(double x) {
  x = 1.0 - x;
  return 1.0 - x * x * x;
}

}  // namespace

double sigma(double s0, double turn_deg, int32_t steps_walked, double still_s, int32_t colder_hits) {
  const double a = K_TURN * fabs(turn_deg);
  const double b = K_STEP * steps_walked;
  const double c = K_STILL * still_s;
  return sqrt(s0 * s0 + a * a + b * b + c * c) + COLDER_HIT_DEG * colder_hits;
}

const char* clock_word(double theta) {
  const int32_t h = (int32_t)(wrap360(theta) / 30.0 + 0.5) % 12;
  return CLOCK_WORDS[h];
}

const char* quad_word(double theta) {
  const double a = wrap180(theta);
  const double m = fabs(a);
  if (m <= 45.0) return "AHEAD";
  if (m >= 135.0) return "BEHIND";
  return a > 0 ? "RIGHT" : "LEFT";
}

const char* reveal_word(double theta, double s) {
  if (fabs(wrap180(theta)) <= SKIP_TURN_DEG) return "AHEAD";
  return s <= CLOCK_MAX_SIGMA ? clock_word(theta) : quad_word(theta);
}

std::optional<Arrow> make(double theta_deg, double s0_deg, ticks_t t_ms, Mode mode, bool probe) {
  const Arrow a(theta_deg, s0_deg, t_ms, mode, probe);
  return a.s0 <= BORN_MAX_SIGMA ? std::optional<Arrow>(a) : std::nullopt;
}

Arrow::Arrow(double theta_deg, double s0_deg, ticks_t t_ms, Mode mode, bool probe)
    : theta(wrap180(theta_deg)),
      s0(probe && PROBE_MIN_SIGMA > s0_deg ? PROBE_MIN_SIGMA : s0_deg),   // max(s0_deg, PROBE_MIN_SIGMA)
      mode(mode),
      probe(probe),
      t0(t_ms),
      ph_t_(t_ms),
      last_t_(t_ms),
      walk_deg_(probe ? theta : 0.0),
      dir_(theta >= 0 ? 1.0 : -1.0),
      word0_(reveal_word(theta, s0)) {
  render_(t_ms);
}

double Arrow::sigma_true() const { return arrow::sigma(s0, turn_deg, steps_walked, still_s, colder_hits); }

bool Arrow::tap() {
  if (link_ok && (phase == PH_TURN || phase == PH_FACE)) {
    tap_ = true;
    return true;
  }
  return false;
}

void Arrow::update(ticks_t t_ms, int32_t activity, std::optional<uint32_t> steps, int32_t trend,
                   bool partner_walking, bool link_ok, bool unreliable, bool hidden) {
  haptic = hp::NONE;
  toast = nullptr;
  if (phase == PH_DONE) return;
  int32_t dt = ticks_diff(t_ms, last_t_);
  if (dt < 0) dt = 0;
  last_t_ = t_ms;
  this->unreliable = unreliable;
  if (steps) {
    uint32_t ref = steps_ref_ ? *steps_ref_ : *steps;
    if (*steps < ref) ref = *steps;   // the counter was reset
    const uint32_t d = *steps - ref;
    steps_ref_ = steps;
    // warmer confirms the half-plane ahead: no drift while it holds
    if (d > 0 && !(link_ok && trend > 0 && !partner_walking)) steps_walked += (int32_t)d;
  }
  if (activity == ACT_STILL) still_s += dt / 1000.0;
  if (link_ok)
    lost_t_.reset();
  else if (!lost_t_)
    lost_t_ = t_ms;
  this->link_ok = link_ok;
  if (hidden || !link_ok) {
    update_hidden_(t_ms);
    return;
  }
  if (hold_t_) {
    if (phase == PH_REVEAL || phase == PH_TURN || phase == PH_FACE)
      ph_t_ = ticks_add(ph_t_, ticks_diff(t_ms, *hold_t_));
    hold_t_.reset();
  }
  step_(t_ms, trend, partner_walking);
  if (stale_(t_ms)) expire_();
  render_(t_ms);
}

bool Arrow::stale_(ticks_t t_ms) const { return sigma_true() > CONE_MAX || ticks_diff(t_ms, t0) > MAX_AGE_MS; }

void Arrow::update_hidden_(ticks_t t_ms) {
  cold_t_.reset();
  tap_ = false;
  if (!hold_t_) hold_t_ = t_ms;
  if (lost_t_ && ticks_diff(t_ms, *lost_t_) > RELINK_RESTORE_MS) {
    phase = PH_DONE;
  } else if (stale_(t_ms)) {
    if (link_ok)
      expire_();   // under the MENU: SCAN AGAIN waits for it to close
    else
      phase = PH_DONE;
  }
  render_(t_ms);
}

void Arrow::lock_(ticks_t t_ms) {
  turn_deg = fabs(theta);
  lock_from_ = wrap180(theta - pacer);
  phase = PH_LOCK;
  ph_t_ = t_ms;
  lock_t_ = t_ms;
  cold_t_.reset();
  tap_ = false;
  haptic = hp::stronger(haptic, H_DOUBLE);
}

void Arrow::expire_() {
  phase = PH_DONE;
  toast = TOAST_EXPIRE;
  haptic = hp::stronger(haptic, H_FARTHER);   // a lock on the same update: FARTHER wins
}

void Arrow::step_(ticks_t t, int32_t trend, bool partner_walking) {
  Phase ph = phase;
  if (ph == PH_REVEAL) {
    if (ticks_diff(t, ph_t_) < REVEAL_MS) return;
    const ticks_t start = ticks_add(ph_t_, REVEAL_MS);
    if (probe) {
      phase = PH_WALK;
      ph_t_ = start;
      return;
    }
    if (fabs(theta) <= SKIP_TURN_DEG) {
      lock_(t);
      return;
    }
    phase = ph = mode == MODE_GUIDED ? PH_TURN : PH_FACE;
    ph_t_ = start;
    ticks_ = 0;
    pacer = 0.0;
    tap_ = false;
  }
  if (ph == PH_TURN) {
    if (tap_) {
      lock_(t);
      return;
    }
    const double goal = fabs(theta);
    const double p = PACER_DEG_S * ticks_diff(t, ph_t_) / 1000.0;
    if (p >= goal) {
      pacer = theta;
      lock_(t);
      return;
    }
    pacer = dir_ * p;
    const int32_t n = (int32_t)(p / TICK_EVERY_DEG);
    if (n > ticks_) {
      ticks_ = n;
      haptic = hp::stronger(haptic, H_TICK);
    }
    return;
  }
  if (ph == PH_FACE) {
    if (tap_ || ticks_diff(t, ph_t_) >= FACE_MS) lock_(t);
    return;
  }
  if (ph == PH_LOCK) {
    if (ticks_diff(t, ph_t_) >= LOCK_EASE_MS) {
      phase = PH_WALK;
      ph_t_ = t;
    }
  }
  if (ph == PH_LOCK || ph == PH_WALK) colder_(t, trend, partner_walking);
}

void Arrow::colder_(ticks_t t, int32_t trend, bool partner_walking) {
  if (trend >= 0 || partner_walking) {
    cold_t_.reset();
    return;
  }
  if (!cold_t_) {
    cold_t_ = t;
    return;
  }
  if (ticks_diff(t, *cold_t_) < COLDER_HIT_MS) return;
  if (hit_t_ && ticks_diff(t, *hit_t_) < COLDER_EVERY_MS) return;
  colder_hits += 1;
  hit_t_ = t;
  cold_t_ = t;
  haptic = hp::stronger(haptic, H_NOPE);
}

void Arrow::render_(ticks_t t) {
  const double s = sigma_true() + (unreliable ? UNRELIABLE_DEG : 0.0);
  sigma = s;
  sub = rp::Sub::NONE;
  glyph = rp::Glyph::NONE;
  arrow_deg.reset();
  cone_deg.reset();
  arrow_style = rp::ArrowStyle::NONE;
  sweep.reset();
  word = nullptr;
  top_text = nullptr;
  const Phase ph = phase;
  if (ph == PH_DONE || !link_ok) return;
  sub = sub_of(ph);
  glyph = rp::Glyph::ARROW;
  const int32_t el = ticks_diff(t, ph_t_);
  const double cone = s < CONE_MIN ? CONE_MIN : s > CONE_MAX ? CONE_MAX : s;
  const rp::ArrowStyle style = rp::arrow_style(cone);   // clamped cone: the widening never hides it
  double deg;
  if (ph == PH_REVEAL || ph == PH_FACE) {
    deg = theta;
    word = ph == PH_REVEAL ? word0_ : W_FACE;
  } else if (ph == PH_TURN) {
    deg = theta - pacer;
    word = dir_ > 0 ? W_TURN_RIGHT : W_TURN_LEFT;
    sweep.emplace();   // bins all None, no active bin, not paused
    sweep->wedge_deg = wrap360(pacer);
  } else {
    if (ph == PH_LOCK) {
      const double x = (double)el / LOCK_EASE_MS;
      deg = lock_from_ * (1.0 - out_cubic(x < 1.0 ? x : 1.0));
    } else {
      deg = walk_deg_;
    }
    if (lock_t_ && ticks_diff(t, *lock_t_) < LOCK_WORD_MS) word = W_WALK;
    if (hit_t_ && ticks_diff(t, *hit_t_) < HINT_MS)
      top_text = T_WRONG_WAY;
    else if (style == rp::ArrowStyle::OUTLINE)
      top_text = T_RESCAN;
  }
  arrow_deg = wrap360(deg);
  cone_deg = cone;
  arrow_style = style;
}

}  // namespace arrow
}  // namespace hm
