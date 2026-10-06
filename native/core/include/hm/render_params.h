// finder/render_params.py: RenderParams, the one thing the renderer reads each
// frame (ui-spec §3). A plain struct with the Python's field names in FIELDS
// order; its member initializers are DEFAULTS (a valid SEARCHING frame once
// make_params has filled wavelength_px). Closed sets of strings are enums with
// name() for the Python spelling (dist_band: an index into T::BAND_LABELS);
// text lives in fixed Text buffers; tuples are small structs.
//
// Python None: sub, glyph, arrow_style, haptic and heartbeat are the enum's
// NONE; every other field the Python lets be None (zone, wavelength_px,
// arrow_deg, cone_deg, countdown, runes, bump_icons, dist_band, word, top_text,
// banner and its text, status.partner_pct, menu_rows and each row, sweep, each
// sweep bin and active_bin) is a std::optional.
// Python allows any type in any field; validate() here checks every rule the
// typed fields can break (not the "must be bool/int/str" ones).
#pragma once
#include <math.h>
#include <stdint.h>

#include <optional>

#include "hm/field.h"
#include "hm/haptic_patterns.h"
#include "hm/ticks.h"
#include "hm/tuning.h"

namespace hm {
namespace render_params {

namespace hp = hm::haptic_patterns;

constexpr int N_FIELDS = 36;
extern const char* const FIELDS[N_FIELDS];

// T::SCREENS order.
enum class Screen : int8_t { PAIRING, SEARCHING, FAR, NEAR, WARM, HOT, FOUND, SCANNING, LINK_LOST, MENU };
constexpr int N_SCREENS = 10;
// Every sub: SUBS_PAIRING, SUBS_SCANNING, SUBS_DIRECTION, SUBS_FOUND ("result"
// once), then the 16 MENU subs (visible row 0..3, then "^" and/or "v" marks).
enum class Sub : int8_t {
  NONE = -1, LOOKING, SEEN, CONFIRMED, CALIBRATE, SPLIT, HOWTO, READY, SWEEP, RESULT, REVEAL, TURN, WALK, CELEBRATE,
  MENU_0, MENU_LAST = MENU_0 + 15,
};
constexpr int N_SUBS = 29;
// T::GLYPHS order.
enum class Glyph : int8_t { NONE = -1, GLOW, SEEKER, CHEVRONS, ARROW, COUNTDOWN, TURN, CHECK, RUNES, BATTERY, BUMP };
constexpr int N_GLYPHS = 10;
// T::ARROW_STYLES order.
enum class ArrowStyle : int8_t { NONE = -1, SOLID_A, SOLID_B, OUTLINE };
constexpr int N_ARROW_STYLES = 3;
// T::BANNER_SEVERITIES order.
enum class Severity : int8_t { INFO, WARN, CRITICAL };
constexpr int N_SEVERITIES = 3;
// The ramp is hm::Ramp (field.h, T::RAMP_NAMES order).

// The Python spelling of each value; nullptr for NONE.
const char* name(Screen v);
const char* name(Sub v);
const char* name(Glyph v);
const char* name(ArrowStyle v);
const char* name(Severity v);
const char* name(Ramp v);
constexpr int N_BANDS = 6;
inline const char* band_name(std::optional<int32_t> band) {
  return band && *band >= 0 && *band < N_BANDS ? T::BAND_LABELS[*band] : nullptr;
}
// The inverse (for logs and tests): false when s names no value; nullptr is NONE where there is one.
bool from_name(const char* s, Screen* v);
bool from_name(const char* s, Sub* v);
bool from_name(const char* s, Glyph* v);
bool from_name(const char* s, ArrowStyle* v);
bool from_name(const char* s, Severity* v);
bool from_name(const char* s, Ramp* v);
bool band_from_name(const char* s, std::optional<int32_t>* band);

// MENU sub: the visible index of the selected row, then "^" when rows are
// hidden above and "v" when rows are hidden below (finder/menu.py Menu.sub).
inline Sub menu_sub(int row, bool up, bool down) {
  return (Sub)((int)Sub::MENU_0 + 4 * row + (up ? 1 : 0) + (down ? 2 : 0));
}
inline bool is_menu_sub(Sub s) { return s >= Sub::MENU_0 && s <= Sub::MENU_LAST; }
inline int menu_row(Sub s) { return ((int)s - (int)Sub::MENU_0) / 4; }
inline bool menu_up(Sub s) { return ((int)s - (int)Sub::MENU_0) & 1; }
inline bool menu_down(Sub s) { return ((int)s - (int)Sub::MENU_0) & 2; }

constexpr bool streq(const char* a, const char* b) {
  while (*a && *a == *b) {
    a++;
    b++;
  }
  return *a == *b;
}
constexpr Ramp ramp_of(const char* s) {
  for (int i = 0; i < RAMP_N; i++) {
    if (streq(s, T::RAMP_NAMES[i])) return (Ramp)i;
  }
  return RAMP_N;
}

// The field theme (ui-spec §4A): an index into T::THEME_NAMES, the MENU's order.
constexpr int N_THEMES = (int)(sizeof(T::THEME_NAMES) / sizeof(T::THEME_NAMES[0]));
// The index of the theme called s; N_THEMES for none.
constexpr int32_t theme_of(const char* s) {
  for (int i = 0; i < N_THEMES; i++) {
    if (streq(s, T::THEME_NAMES[i])) return i;
  }
  return N_THEMES;
}
constexpr int32_t THEME_DEFAULT = theme_of(T::THEME_DEFAULT);
static_assert(THEME_DEFAULT < N_THEMES, "T::THEME_DEFAULT names a theme");

// T.ZONE_HEARTBEAT (tuning.h has ZONE_HEARTBEAT_0..2 as strings and notes _3 as None).
constexpr hp::Haptic ZONE_HEARTBEAT[4] = {hp::TICK, hp::TICK, hp::DOUBLE, hp::NONE};
constexpr int MENU_VISIBLE = sizeof(T::MENU_ROWS_Y) / sizeof(T::MENU_ROWS_Y[0]);   // rows on screen
// bump_icons bits (HOT bump view, ui-spec §6 HOT)
constexpr int32_t BI_ME = 1;                                // your watch counted a spike in the last 1 s
constexpr int32_t BI_FRIEND = 2;                            // the friend's reported spike, in the last 1 s
constexpr int32_t BI_FRIEND_OFF = 4;                        // the friend's watch cannot count a bump (grey)
constexpr int32_t BUMP_ICONS_MAX = BI_ME | BI_FRIEND_OFF;   // 6 and 7 never occur (bits 1 and 2 exclude)
constexpr const char* W_BUMP = "BUMP!";                     // hm::game's W_BUMP (tests check)

// A str of up to TEXT_MAX chars: more than any slot allows, so validate can
// still flag an over-long one (longer input is cut). None is std::nullopt.
constexpr int TEXT_MAX = 31;
struct Text {
  char s[TEXT_MAX + 1] = {};
  Text() = default;
  explicit Text(const char* v) { set(v); }
  void set(const char* v);   // nullptr: ""
  bool operator==(const char* v) const;
};
using OptText = std::optional<Text>;
// A text slot from a C string: nullptr is None.
inline OptText opt_text(const char* v) { return v ? OptText(Text(v)) : std::nullopt; }

// (own_pct, partner_pct|None, link_q, visible, unreliable)
struct Status {
  int32_t own_pct = 100;
  std::optional<int32_t> partner_pct;
  int32_t link_q = 0;
  bool visible = false;
  bool unreliable = false;
};

// (text, severity, sticky)
struct Banner {
  OptText text;
  Severity severity = Severity::INFO;
  bool sticky = false;
};

// (wedge_deg, bins, active_bin, paused); bins is SCAN_BINS long when valid.
struct Sweep {
  double wedge_deg = 0.0;
  int32_t n_bins = T::SCAN_BINS;
  std::optional<double> bins[T::SCAN_BINS];
  std::optional<int32_t> active_bin;
  bool paused = false;
};

// 3 rune ids when valid (n is the tuple length).
struct Runes {
  int32_t n = 3;
  int32_t ids[3] = {};
};

// The visible MENU rows (n is the tuple length, MENU_VISIBLE when valid).
struct MenuRows {
  int32_t n = MENU_VISIBLE;
  OptText rows[MENU_VISIBLE];
};

struct RenderParams {
  // screen
  ticks_t t_ms = 0;
  Screen screen = Screen::SEARCHING;
  Sub sub = Sub::NONE;
  std::optional<int32_t> zone;
  // ripple field
  Ramp ramp = ramp_of(T::FIELD_SEARCHING_0);
  double intensity = T::FIELD_SEARCHING_1;
  double speed_px_s = T::FIELD_SEARCHING_2;
  int32_t pulse_period_ms = T::FIELD_SEARCHING_3;
  std::optional<double> wavelength_px;   // None until make_params
  double glow_r_px = T::FIELD_SEARCHING_4;
  bool ring_live = false;
  bool burst = false;
  // centre glyph
  Glyph glyph = Glyph::SEEKER;
  std::optional<double> arrow_deg;
  std::optional<double> cone_deg;
  ArrowStyle arrow_style = ArrowStyle::NONE;
  int32_t trend = 0;
  bool trend_strong = false;
  std::optional<int32_t> countdown;
  std::optional<Runes> runes;
  std::optional<int32_t> bump_icons;   // BI_* bits
  // text slots
  std::optional<int32_t> dist_band;   // index into T::BAND_LABELS
  bool dist_stale = false;
  OptText word;
  OptText top_text;
  std::optional<Banner> banner;
  Status status;
  std::optional<MenuRows> menu_rows;
  // scanning
  std::optional<Sweep> sweep;
  // output devices
  hp::Haptic haptic = hp::NONE;
  hp::Haptic heartbeat = hp::NONE;
  int32_t heartbeat_every = 1;
  double backlight = T::BACKLIGHT_NORMAL;
  bool sun = false;
  int32_t fps_cap = T::FPS_TARGET;
  // look
  int32_t theme = THEME_DEFAULT;   // theme_of(name), N_THEMES for none (validate rejects it); drawn as Ripple
};
// replace(rp, **kw) is a copy and plain assignment in C++.

// Ring spacing in px: |speed| * period / 1000.
inline double wavelength(double speed_px_s, int32_t pulse_period_ms) {
  return fabs(speed_px_s) * pulse_period_ms / 1000.0;
}

// Style tier for a cone half-angle; NONE when hidden (> 60 deg) or None.
ArrowStyle arrow_style(std::optional<double> cone_deg);

// make_params(**kw): start from RenderParams{} (DEFAULTS), set the fields, then
// call this; a None wavelength_px becomes wavelength(speed_px_s, pulse_period_ms).
RenderParams make_params(RenderParams kw = RenderParams());

// Check the §3 validity rules: calls emit(ctx, message) for each violation, in
// the Python's order and wording, and returns how many there were.
int validate(const RenderParams& rp, void (*emit)(void* ctx, const char* msg) = nullptr, void* ctx = nullptr);

// `field=value` for every field in FIELDS order, space-separated, into buf (a
// snprintf: returns the length it needed). None is "n", bools 0/1, floats
// %.17g (round-trips), a space in text "_", tuple items joined by "|" (sweep
// bins by "/").
constexpr int DUMP_MAX = 1536;
int dump(const RenderParams& rp, char* buf, int cap);

}  // namespace render_params
}  // namespace hm
