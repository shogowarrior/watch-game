// finder/arrow.py: the DIRECTION overlay, the arrow lifecycle (ui-spec §6
// DIRECTION, §5.6).
//
// An Arrow is born from a scan result (theta, s0) or a walk-test probe and
// then runs by itself at the logic rate (10 Hz):
//
//     reveal (1.5 s) -> turn (guided pacer) | face (static) -> lock -> walk -> done
//
// Angles are degrees clockwise from screen-up; theta is relative to the
// heading at scan start (screen-up). After every update the object holds the
// RenderParams fields it owns (glyph, arrow_deg, cone_deg, arrow_style, sweep,
// word, top_text, sub) plus the one-shot events haptic and toast (set only on
// the update that started them; two haptics on one update keep the higher
// rank, haptic_patterns::stronger). sigma is the display half-angle before
// clamping. The spec-silent choices are listed in the Python's docstring.
//
// Python None: haptic is hp::NONE, sub/glyph/arrow_style their enum's NONE,
// word/top_text/toast nullptr (every text is a static literal), the rest
// std::optional. Python's mode and phase strings are the Mode and Phase enums.
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/estimator.h"
#include "hm/haptic_patterns.h"
#include "hm/py.h"
#include "hm/render_params.h"
#include "hm/tuning.h"

namespace hm {
namespace arrow {

namespace hp = hm::haptic_patterns;
namespace rp = hm::render_params;

constexpr int32_t REVEAL_MS = T::DIRECTION_REVEAL_MS;
constexpr double PACER_DEG_S = T::PACER_DEG_PER_S;
constexpr double TICK_EVERY_DEG = T::PACER_TICK_DEG;
constexpr int32_t LOCK_EASE_MS = T::DIRECTION_LOCK_EASE_MS;
constexpr int32_t LOCK_WORD_MS = T::DIRECTION_WALK_WORD_MS;
constexpr int32_t FACE_MS = 6000;   // static mode auto-lock (= slowest guided turn; spec-silent)
constexpr int32_t MAX_AGE_MS = T::ARROW_MAX_AGE_MS;
constexpr double BORN_MAX_SIGMA = T::ARROW_BORN_MAX_SIGMA_DEG;
constexpr double CONE_MIN = T::CONE_DRAW_MIN_DEG;
constexpr double CONE_MAX = T::ARROW_HIDE_SIGMA_DEG;   // drawn cone 12..60; a true sigma above it expires
constexpr double SKIP_TURN_DEG = T::DIRECTION_AHEAD_DEG;
constexpr double CLOCK_MAX_SIGMA = T::DIRECTION_CLOCK_MAX_SIGMA_DEG;
constexpr int32_t COLDER_HIT_MS = T::COLDER_HIT_MS;
constexpr int32_t COLDER_EVERY_MS = T::COLDER_HIT_EVERY_MS;
constexpr double COLDER_HIT_DEG = T::SIGMA_COLDER_HIT_DEG;
constexpr int32_t HINT_MS = T::HINT_CHIP_MS;
constexpr int32_t RELINK_RESTORE_MS = T::ARROW_RELINK_RESTORE_MS;
constexpr double UNRELIABLE_DEG = T::SIGMA_UNRELIABLE_DEG;
constexpr double PROBE_MIN_SIGMA = T::PROBE_SIGMA_MIN_DEG;
constexpr double K_TURN = T::SIGMA_TURN_K;
constexpr double K_STEP = T::SIGMA_STEP_K;
constexpr double K_STILL = T::SIGMA_STILL_K;

enum Mode : int8_t { MODE_GUIDED, MODE_STATIC };
enum Phase : int8_t { PH_REVEAL, PH_TURN, PH_FACE, PH_LOCK, PH_WALK, PH_DONE };
// The Python spelling ("guided", "reveal").
inline const char* name(Mode m) { return m == MODE_STATIC ? "static" : "guided"; }
inline const char* name(Phase p) {
  constexpr const char* const NAMES[6] = {"reveal", "turn", "face", "lock", "walk", "done"};
  return NAMES[p];
}

constexpr const char* W_TURN_RIGHT = "TURN RIGHT";
constexpr const char* W_TURN_LEFT = "TURN LEFT";
constexpr const char* W_FACE = "FACE IT";
constexpr const char* W_WALK = "WALK";
constexpr const char* T_WRONG_WAY = "WRONG WAY? RESCAN";
constexpr const char* T_RESCAN = "TAP TO RESCAN";
constexpr const char* TOAST_EXPIRE = "SCAN AGAIN";

constexpr hp::Haptic H_TICK = hp::TICK;
constexpr hp::Haptic H_DOUBLE = hp::DOUBLE;
constexpr hp::Haptic H_NOPE = hp::NOPE;
constexpr hp::Haptic H_FARTHER = hp::FARTHER;

// Angle to (-180, 180].
inline double wrap180(double a) {
  a = pymod(a, 360.0);
  return a > 180.0 ? a - 360.0 : a;
}

// Angle to [0, 360).
inline double wrap360(double a) {
  a = pymod(a, 360.0);
  return a >= 360.0 ? 0.0 : a;
}

// Cone half-angle model of ui-spec §5.6 (degrees), without the display-only
// unreliable widening.
double sigma(double s0, double turn_deg, int32_t steps_walked, double still_s, int32_t colder_hits);
// Clock-hour word, e.g. 120 -> "4 O'CLOCK" (0 -> 12).
const char* clock_word(double theta);
// Four-way word for a coarse bearing.
const char* quad_word(double theta);
// Bottom word of the reveal phase: precision decides the wording.
const char* reveal_word(double theta, double s);

// One DIRECTION overlay from birth (scan/probe result) to expiry.
class Arrow {
 public:
  Arrow(double theta_deg, double s0_deg, ticks_t t_ms, Mode mode = MODE_GUIDED, bool probe = false);

  bool done() const { return phase == PH_DONE; }
  // Model sigma without the display-only unreliable widening.
  double sigma_true() const;
  // Press/tap: "I'm facing it". True if consumed (turn/face phase).
  bool tap();
  // Advance to t_ms. steps is the own cumulative step counter (nullopt: none
  // read). hidden pauses the phase clocks like link loss, without a relink limit.
  void update(ticks_t t_ms, int32_t activity = ACT_UNKNOWN, std::optional<uint32_t> steps = std::nullopt,
              int32_t trend = 0, bool partner_walking = false, bool link_ok = true, bool unreliable = false,
              bool hidden = false);

  double theta;   // (-180, 180]
  double s0;
  Mode mode;
  bool probe;
  ticks_t t0;
  double turn_deg = 0.0;
  int32_t steps_walked = 0;
  double still_s = 0.0;
  int32_t colder_hits = 0;
  bool unreliable = false;
  bool link_ok = true;
  Phase phase = PH_REVEAL;
  double pacer = 0.0;
  // one-shot events of the last update
  hp::Haptic haptic = hp::NONE;
  const char* toast = nullptr;
  // set by every render
  double sigma = 0.0;   // display half-angle before clamping
  rp::Sub sub = rp::Sub::NONE;
  rp::Glyph glyph = rp::Glyph::NONE;
  std::optional<double> arrow_deg;
  std::optional<double> cone_deg;
  rp::ArrowStyle arrow_style = rp::ArrowStyle::NONE;
  std::optional<rp::Sweep> sweep;   // the turn pacer: wedge only, no bin bars (§3)
  const char* word = nullptr;
  const char* top_text = nullptr;

 private:
  bool stale_(ticks_t t_ms) const;
  void update_hidden_(ticks_t t_ms);
  void lock_(ticks_t t_ms);
  void expire_();
  void step_(ticks_t t, int32_t trend, bool partner_walking);
  void colder_(ticks_t t, int32_t trend, bool partner_walking);
  void render_(ticks_t t);

  ticks_t ph_t_;
  ticks_t last_t_;
  std::optional<uint32_t> steps_ref_;
  opt_ticks lost_t_;
  opt_ticks hold_t_;   // hidden or lost since (phase clocks paused)
  opt_ticks cold_t_;
  opt_ticks hit_t_;
  opt_ticks lock_t_;
  double lock_from_ = 0.0;
  int32_t ticks_ = 0;
  bool tap_ = false;
  double walk_deg_;
  double dir_;
  const char* word0_;
};

// New Arrow, or nullopt when sigma at birth exceeds 45 deg (no arrow is born).
std::optional<Arrow> make(double theta_deg, double s0_deg, ticks_t t_ms, Mode mode = MODE_GUIDED, bool probe = false);

}  // namespace arrow
}  // namespace hm
