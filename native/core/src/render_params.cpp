#include "hm/render_params.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

namespace hm {
namespace render_params {

const char* const FIELDS[N_FIELDS] = {
    // screen
    "t_ms", "screen", "sub", "zone",
    // ripple field
    "ramp", "intensity", "speed_px_s", "pulse_period_ms", "wavelength_px", "glow_r_px", "ring_live", "burst",
    // centre glyph
    "glyph", "arrow_deg", "cone_deg", "arrow_style", "trend", "trend_strong", "countdown", "runes", "bump_icons",
    // text slots
    "dist_band", "dist_stale", "word", "top_text", "banner", "status", "menu_rows",
    // scanning
    "sweep",
    // output devices
    "haptic", "heartbeat", "heartbeat_every", "backlight", "sun", "fps_cap",
    // look
    "theme",
};

namespace {

const char* const SUB_NAMES[N_SUBS] = {
    "looking", "seen", "confirmed", "calibrate", "split", "howto", "ready", "sweep", "result", "reveal", "turn", "walk",
    "celebrate",
    "0", "0^", "0v", "0^v", "1", "1^", "1v", "1^v", "2", "2^", "2v", "2^v", "3", "3^", "3v", "3^v",
};

const char* pick(const char* const* names, int n, int i) { return i >= 0 && i < n ? names[i] : nullptr; }

int find(const char* const* names, int n, const char* s) {
  for (int i = 0; i < n; i++) {
    if (strcmp(s, names[i]) == 0) return i;
  }
  return -1;
}

// from_name for an enum; with has_none, nullptr is its NONE (-1).
template <typename E>
bool lookup(const char* const* names, int n, const char* s, E* v, bool has_none) {
  if (s == nullptr) {
    if (has_none) *v = (E)-1;
    return has_none;
  }
  const int i = find(names, n, s);
  if (i < 0) return false;
  *v = (E)i;
  return true;
}

}  // namespace

const char* name(Screen v) { return pick(T::SCREENS, N_SCREENS, (int)v); }
const char* name(Sub v) { return pick(SUB_NAMES, N_SUBS, (int)v); }
const char* name(Glyph v) { return pick(T::GLYPHS, N_GLYPHS, (int)v); }
const char* name(ArrowStyle v) { return pick(T::ARROW_STYLES, N_ARROW_STYLES, (int)v); }
const char* name(Severity v) { return pick(T::BANNER_SEVERITIES, N_SEVERITIES, (int)v); }
const char* name(Ramp v) { return pick(T::RAMP_NAMES, RAMP_N, (int)v); }

bool from_name(const char* s, Screen* v) { return lookup(T::SCREENS, N_SCREENS, s, v, false); }
bool from_name(const char* s, Sub* v) { return lookup(SUB_NAMES, N_SUBS, s, v, true); }
bool from_name(const char* s, Glyph* v) { return lookup(T::GLYPHS, N_GLYPHS, s, v, true); }
bool from_name(const char* s, ArrowStyle* v) { return lookup(T::ARROW_STYLES, N_ARROW_STYLES, s, v, true); }
bool from_name(const char* s, Severity* v) { return lookup(T::BANNER_SEVERITIES, N_SEVERITIES, s, v, false); }
bool from_name(const char* s, Ramp* v) { return lookup(T::RAMP_NAMES, RAMP_N, s, v, false); }

bool band_from_name(const char* s, std::optional<int32_t>* band) {
  if (s == nullptr) {
    band->reset();
    return true;
  }
  const int i = find(T::BAND_LABELS, N_BANDS, s);
  if (i < 0) return false;
  *band = i;
  return true;
}

void Text::set(const char* v) {
  int i = 0;
  for (; v && v[i] && i < TEXT_MAX; i++) s[i] = v[i];
  s[i] = 0;
}

bool Text::operator==(const char* v) const { return v != nullptr && strcmp(s, v) == 0; }

ArrowStyle arrow_style(std::optional<double> cone_deg) {
  if (!cone_deg) return ArrowStyle::NONE;
  for (int i = 0; i < N_ARROW_STYLES; i++) {
    if (*cone_deg <= T::ARROW_TIER_MAX_DEG[i]) return (ArrowStyle)i;
  }
  return ArrowStyle::NONE;
}

RenderParams make_params(RenderParams kw) {
  if (!kw.wavelength_px) kw.wavelength_px = wavelength(kw.speed_px_s, kw.pulse_period_ms);
  return kw;
}

// ---- validation (ui-spec §3 table + field comments) -------------------------

namespace {

struct Out {
  void (*emit)(void*, const char*);
  void* ctx;
  int n;
  void e(const char* fmt, ...) {
    char buf[160];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (emit) emit(ctx, buf);
    n++;
  }
};

// Python's repr() of an optional enum spelling: None or 'name'.
const char* repr(const char* s, char* buf, int cap) {
  if (s == nullptr) return "None";
  snprintf(buf, cap, "'%s'", s);
  return buf;
}

// Python's repr() of a one-char str.
const char* repr_ch(char ch, char* buf, int cap) {
  const unsigned char c = (unsigned char)ch;
  if (c == '\'') snprintf(buf, cap, "\"'\"");
  else if (c == '\\') snprintf(buf, cap, "'\\\\'");
  else if (c == '\n') snprintf(buf, cap, "'\\n'");
  else if (c == '\t') snprintf(buf, cap, "'\\t'");
  else if (c == '\r') snprintf(buf, cap, "'\\r'");
  else if (c < 0x20 || c >= 0x7f) snprintf(buf, cap, "'\\x%02x'", c);
  else snprintf(buf, cap, "'%c'", c);
  return buf;
}

// _in(v, lo, hi): a number within [lo, hi]; None (and NaN) never is.
bool in(std::optional<double> v, double lo, double hi) { return v && lo <= *v && *v <= hi; }

bool zone_screen(Screen s) { return s >= Screen::FAR && s <= Screen::HOT; }
int zone_index(Screen s) { return (int)s - (int)Screen::FAR; }   // ZONE_SCREENS.index(sc)

void text(Out& o, const char* name, const OptText& t, int maxlen, const char* chars) {
  if (!t) return;
  const char* s = t->s;
  const int n = (int)strlen(s);
  if (n > maxlen) o.e("%s: %d chars > %d", name, n, maxlen);
  for (int i = 0; i < n; i++) {
    if (s[i] >= 'a' && s[i] <= 'z') {   // s != s.upper()
      o.e("%s: not uppercase", name);
      break;
    }
  }
  for (int i = 0; i < n; i++) {
    if (strchr(chars, s[i]) == nullptr) {
      char r[12];
      o.e("%s: glyph %s not in font subset", name, repr_ch(s[i], r, sizeof r));
      break;
    }
  }
}

bool valid_sub(Screen screen, Sub sub) {
  switch (screen) {
    case Screen::PAIRING:
      return sub >= Sub::LOOKING && sub <= Sub::HOWTO;
    case Screen::SCANNING:
      return sub == Sub::READY || sub == Sub::SWEEP || sub == Sub::RESULT;
    case Screen::FAR:
    case Screen::NEAR:
    case Screen::WARM:
    case Screen::HOT:
      return sub == Sub::NONE || sub == Sub::REVEAL || sub == Sub::TURN || sub == Sub::WALK;
    case Screen::FOUND:
      return sub == Sub::CELEBRATE || sub == Sub::RESULT;
    case Screen::MENU:
      return is_menu_sub(sub);   // visible row index, then optional "^"/"v" scroll marks
    default:
      return sub == Sub::NONE;   // SEARCHING, LINK_LOST
  }
}

// _RAMP_FOR (RAMP_N: MENU, which is not in it).
Ramp ramp_for(Screen s) {
  switch (s) {
    case Screen::FOUND:
      return RAMP_GOLD;
    case Screen::SEARCHING:
    case Screen::LINK_LOST:
      return RAMP_GREY;
    case Screen::MENU:
      return RAMP_N;
    default:
      return RAMP_GREEN;
  }
}

}  // namespace

int validate(const RenderParams& rp, void (*emit)(void*, const char*), void* ctx) {
  Out o{emit, ctx, 0};
  char r1[24], r2[24];
  const Screen sc = rp.screen;
  const Sub sub = rp.sub;
  const char* scn = name(sc);
  const bool howto = sc == Screen::PAIRING && sub == Sub::HOWTO;

  // screen (t_ms is unsigned, so never < 0)
  if (scn == nullptr) {
    o.e("screen: unknown %d", (int)sc);
    scn = "?";
  } else if (!valid_sub(sc, sub)) {
    o.e("sub: %s not valid for %s", repr(name(sub), r1, sizeof r1), scn);
  }
  const bool zone_ok = rp.zone && 0 <= *rp.zone && *rp.zone <= 3;
  if (rp.zone && !zone_ok) o.e("zone: must be None or int 0..3");
  // z: the zone, -1 once None or out of range (a plain int: GCC misreads a local optional as uninitialized)
  const int32_t z = zone_ok ? *rp.zone : -1;
  if (zone_screen(sc)) {
    if (z != zone_index(sc)) {
      if (rp.zone) o.e("zone: %d does not match screen %s", (int)*rp.zone, scn);
      else o.e("zone: None does not match screen %s", scn);
    }
  } else if (sc == Screen::LINK_LOST && z < 0) {
    o.e("zone: LINK_LOST needs the last known zone");
  }

  // ripple field
  const Ramp want = ramp_for(sc);
  if (name(rp.ramp) == nullptr) o.e("ramp: unknown %d", (int)rp.ramp);
  else if (want != RAMP_N && rp.ramp != want) o.e("ramp: %s must be %s", scn, name(want));
  if (!in(rp.intensity, 0.0, 1.0)) o.e("intensity: must be 0..1");
  const double spd = rp.speed_px_s;
  const int32_t per = rp.pulse_period_ms;
  const std::optional<double> wl = rp.wavelength_px;
  bool ok = true;
  if (!in(spd, T::SPEED_MIN_PX_S, T::SPEED_MAX_PX_S)) {
    o.e("speed_px_s: must be %g..%g", T::SPEED_MIN_PX_S, T::SPEED_MAX_PX_S);
    ok = false;
  }
  if (!(T::PERIOD_MIN_MS <= per && per <= T::PERIOD_MAX_MS)) {
    o.e("pulse_period_ms: must be int %d..%d", (int)T::PERIOD_MIN_MS, (int)T::PERIOD_MAX_MS);
    ok = false;
  }
  if (!in(wl, 0.0, T::WAVELENGTH_MAX_PX)) {
    o.e("wavelength_px: must be 0..%g", T::WAVELENGTH_MAX_PX);
  } else if (ok && fabs(*wl - wavelength(spd, per)) > T::WAVELENGTH_TOL_PX) {
    o.e("wavelength_px: %g != |speed|*period/1000 = %g", *wl, wavelength(spd, per));
  }
  if (ok && zone_screen(sc) && z >= 0) {
    if (spd != T::ZONE_SPEED_PX_S[z] || per != T::ZONE_PERIOD_MS[z]) {
      o.e("speed_px_s/pulse_period_ms: not the %s zone tempo", scn);
    }
  }
  if (ok && (sc == Screen::SEARCHING || sc == Screen::LINK_LOST) && spd > 0) {
    o.e("speed_px_s: %s rings must be inward (<= 0)", scn);
  }
  if (sc == Screen::PAIRING && (sub == Sub::LOOKING || sub == Sub::HOWTO) && (rp.ring_live || (ok && spd > 0))) {
    o.e("ring_live/speed_px_s: no live or outward rings in PAIRING %s (no partner)", name(sub));
  }
  if (!in(rp.glow_r_px, 0.0, T::GLOW_R_MAX_PX)) o.e("glow_r_px: must be 0..%g", T::GLOW_R_MAX_PX);

  // centre glyph
  const Glyph g = rp.glyph;
  if (name(g) == nullptr) o.e("glyph: unknown %s", repr(name(g), r1, sizeof r1));
  const std::optional<double> ad = rp.arrow_deg, cd = rp.cone_deg;
  const ArrowStyle st = rp.arrow_style;
  if (!ad != !cd || !ad != (st == ArrowStyle::NONE)) o.e("arrow_deg/cone_deg/arrow_style: must be None together");
  if (ad) {
    if (!in(ad, 0.0, 360.0)) o.e("arrow_deg: must be 0..360");
    if (!in(cd, T::CONE_DRAW_MIN_DEG, T::ARROW_HIDE_SIGMA_DEG)) {
      o.e("cone_deg: must be %g..%g", T::CONE_DRAW_MIN_DEG, T::ARROW_HIDE_SIGMA_DEG);
    } else if (st != ArrowStyle::NONE && st != arrow_style(cd)) {
      o.e("arrow_style: %s does not match cone %g (%s)", repr(name(st), r1, sizeof r1), *cd,
          repr(name(arrow_style(cd)), r2, sizeof r2));
    }
    if (sc == Screen::SEARCHING || sc == Screen::SCANNING || sc == Screen::LINK_LOST || sc == Screen::FOUND ||
        sc == Screen::PAIRING) {
      o.e("arrow_deg: not allowed in %s", scn);
    }
    if (sub == Sub::READY || sub == Sub::SWEEP) o.e("arrow_deg: not allowed while sub is %s", name(sub));
    if (g != Glyph::ARROW) o.e("glyph: must be 'arrow' while an arrow is set");
  } else if (g == Glyph::ARROW) {
    o.e("glyph: 'arrow' needs arrow_deg/cone_deg/arrow_style");
  }
  if (g == Glyph::CHEVRONS && !(sc == Screen::FAR || sc == Screen::NEAR || sc == Screen::WARM) && !howto) {
    o.e("glyph: 'chevrons' only in FAR/NEAR/WARM (and the howto card 3)");
  }
  const int32_t tr = rp.trend;
  if (tr < -1 || tr > 1) {
    o.e("trend: must be -1, 0 or +1");
  } else if (tr != 0 &&
             !(sc == Screen::FAR || sc == Screen::NEAR || sc == Screen::WARM || sc == Screen::LINK_LOST) &&
             !(howto && tr == 1 && g == Glyph::CHEVRONS)) {
    o.e("trend: must be 0 in %s", scn);
  }
  if (rp.trend_strong && tr == 0) o.e("trend_strong: needs a non-zero trend");
  else if (rp.trend_strong && sc == Screen::LINK_LOST) o.e("trend_strong: must be False in LINK_LOST");
  else if (rp.trend_strong && howto) o.e("trend_strong: must be False on a howto card");
  if (howto) {
    if (!(g == Glyph::RUNES || g == Glyph::COUNTDOWN || g == Glyph::CHEVRONS || g == Glyph::BUMP)) {
      o.e("glyph: a howto card is runes, countdown, chevrons or bump");
    }
    if (!rp.top_text || !rp.word) o.e("top_text/word: a howto card has both");
  }
  const std::optional<int32_t> cn = rp.countdown;
  if (cn && !(0 <= *cn && *cn <= T::COUNTDOWN_MAX)) {
    o.e("countdown: must be None or int 0..%d", (int)T::COUNTDOWN_MAX);
  }
  if ((g == Glyph::COUNTDOWN) != cn.has_value()) o.e("countdown: set exactly when glyph is 'countdown'");
  if (rp.runes) {
    bool rok = rp.runes->n == 3;
    for (int i = 0; rok && i < 3; i++) rok = 0 <= rp.runes->ids[i] && rp.runes->ids[i] <= 7;
    if (!rok) o.e("runes: must be None or 3 rune ids 0..7");
    else if (sc != Screen::PAIRING) o.e("runes: only in PAIRING");
  } else if (g == Glyph::RUNES && (sub == Sub::SEEN || sub == Sub::CONFIRMED || sub == Sub::HOWTO)) {
    o.e("runes: glyph 'runes' needs them in %s", name(sub));
  }
  const std::optional<int32_t> bi = rp.bump_icons;
  if (bi && !(0 <= *bi && *bi <= BUMP_ICONS_MAX)) {
    o.e("bump_icons: must be None or int 0..%d", (int)BUMP_ICONS_MAX);
  } else if ((g == Glyph::BUMP) != bi.has_value()) {
    o.e("bump_icons: set exactly when glyph is 'bump'");
  } else if (bi && !howto) {
    if (sc != Screen::HOT) o.e("glyph: 'bump' only in HOT (and on the howto card 4)");
    if (sub != Sub::NONE) o.e("glyph: 'bump' needs sub None");
    if ((*bi & BI_FRIEND_OFF) && rp.word == W_BUMP) o.e("word: BUMP! while the friend cannot count a bump");
  }

  // text slots
  const std::optional<int32_t> band = rp.dist_band;
  if (band) {
    if (band_name(band) == nullptr) {
      o.e("dist_band: %d is not a band label", (int)*band);
    } else if (sc == Screen::PAIRING || sc == Screen::SEARCHING || sc == Screen::SCANNING || sc == Screen::FOUND) {
      o.e("dist_band: must be None in %s", scn);
    } else if (zone_screen(sc) && z >= 0) {
      if (!(T::ZONE_BANDS[z][0] <= *band && *band <= T::ZONE_BANDS[z][1])) {
        o.e("dist_band: %s contradicts zone %s", band_name(band), scn);
      }
    }
  }
  if (rp.dist_stale && sc != Screen::LINK_LOST) o.e("dist_stale: only in LINK_LOST");
  text(o, "word", rp.word, T::WORD_MAX_CHARS, T::WORD_CHARS);
  text(o, "top_text", rp.top_text, T::LABEL_MAX_CHARS, T::LABEL_CHARS);
  if (sc == Screen::SCANNING && sub == Sub::SWEEP && (rp.word || rp.top_text)) {
    o.e("word/top_text: both slots are suppressed during the sweep");
  }
  if (sub == Sub::TURN && rp.top_text) o.e("top_text: suppressed during the turn pacer");
  if (rp.banner) {
    const Banner& bn = *rp.banner;
    text(o, "banner", bn.text, T::LABEL_MAX_CHARS, T::LABEL_CHARS);
    if (!bn.text) o.e("banner: text missing");
    if (name(bn.severity) == nullptr) o.e("banner: severity %d", (int)bn.severity);
  }
  const Status& s = rp.status;
  if (!(0 <= s.own_pct && s.own_pct <= 100)) o.e("status: own_pct must be int 0..100");
  if (s.partner_pct && !(0 <= *s.partner_pct && *s.partner_pct <= 100)) {
    o.e("status: partner_pct must be None or int 0..100");
  }
  if (!(0 <= s.link_q && s.link_q <= T::LINK_Q_MAX)) o.e("status: link_q must be int 0..%d", (int)T::LINK_Q_MAX);
  if (s.unreliable && !s.visible) o.e("status: unreliable pins the strip (visible)");
  if ((sc == Screen::MENU) != rp.menu_rows.has_value()) o.e("menu_rows: set exactly when screen is MENU");
  if (rp.menu_rows) {
    const MenuRows& mr = *rp.menu_rows;
    if (mr.n != MENU_VISIBLE) {
      o.e("menu_rows: must be a tuple of %d rows", MENU_VISIBLE);
    } else {
      for (int k = 0; k < MENU_VISIBLE; k++) {
        if (!mr.rows[k]) o.e("menu_rows: row text missing");
        text(o, "menu_rows", mr.rows[k], T::LABEL_MAX_CHARS, T::LABEL_CHARS);
      }
    }
  }

  // scanning
  if (rp.sweep) {
    const Sweep& sw = *rp.sweep;
    if (sc != Screen::SCANNING && sub != Sub::TURN) o.e("sweep: only in SCANNING or the DIRECTION turn");
    if (!in(sw.wedge_deg, 0.0, 360.0)) o.e("sweep: wedge_deg must be 0..360");
    if (sw.n_bins != T::SCAN_BINS) {
      o.e("sweep: bins must be a tuple of %d", (int)T::SCAN_BINS);
    } else {
      for (int k = 0; k < T::SCAN_BINS; k++) {
        if (sw.bins[k] && !in(sw.bins[k], 0.0, 1.0)) {
          o.e("sweep: bin values must be None or 0..1");
          break;
        }
      }
    }
    if (sw.active_bin && !(0 <= *sw.active_bin && *sw.active_bin < T::SCAN_BINS)) {
      o.e("sweep: active_bin must be None or 0..%d", (int)T::SCAN_BINS - 1);
    }
  }

  // output devices
  const hp::Haptic hb = rp.heartbeat;
  const int32_t ev = rp.heartbeat_every;
  if (hb != hp::NONE && hb != hp::TICK && hb != hp::DOUBLE) o.e("heartbeat: must be None, TICK or DOUBLE");
  if (!(1 <= ev && ev <= 2)) {
    o.e("heartbeat_every: must be int 1..2");
  } else if (hb != hp::NONE && zone_screen(sc) && z >= 0) {
    if (hb != ZONE_HEARTBEAT[z] || ev != T::ZONE_HB_EVERY[z]) o.e("heartbeat: not the %s zone heartbeat", scn);
  }
  if (!in(rp.backlight, 0.0, 1.0)) o.e("backlight: must be 0..1");
  if (!(T::FPS_CAP_MIN <= rp.fps_cap && rp.fps_cap <= T::FPS_CAP_MAX)) {
    o.e("fps_cap: must be int %d..%d", (int)T::FPS_CAP_MIN, (int)T::FPS_CAP_MAX);
  }
  if (!(0 <= rp.theme && rp.theme < N_THEMES)) {
    char names[96];
    int k = 0;
    for (int i = 0; i < N_THEMES && k < (int)sizeof names; i++)
      k += snprintf(names + k, sizeof names - k, i ? " %s" : "%s", T::THEME_NAMES[i]);
    o.e("theme: must be one of %s", names);
  }
  return o.n;
}

// ---- dump --------------------------------------------------------------------

namespace {

// Appends like snprintf: len keeps counting past cap.
struct W {
  char* buf;
  int cap;
  int len;
  void f(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    const int room = len < cap ? cap - len : 0;
    const int n = vsnprintf(room ? buf + len : nullptr, (size_t)room, fmt, ap);
    va_end(ap);
    if (n > 0) len += n;
  }
  void s(const char* v) { f("%s", v ? v : "n"); }
  void flt(std::optional<double> v) {
    if (v) f("%.17g", *v);
    else f("n");
  }
  void opt_int(std::optional<int32_t> v) {
    if (v) f("%d", (int)*v);
    else f("n");
  }
  void text(const OptText& t) {
    if (!t) {
      f("n");
      return;
    }
    for (const char* p = t->s; *p; p++) f("%c", *p == ' ' ? '_' : *p);
  }
};

}  // namespace

int dump(const RenderParams& rp, char* buf, int cap) {
  W w{buf, cap, 0};
  if (cap > 0) buf[0] = 0;
  w.f("t_ms=%lu screen=", (unsigned long)rp.t_ms);
  w.s(name(rp.screen));
  w.f(" sub=");
  w.s(name(rp.sub));
  w.f(" zone=");
  w.opt_int(rp.zone);
  w.f(" ramp=");
  w.s(name(rp.ramp));
  w.f(" intensity=");
  w.flt(rp.intensity);
  w.f(" speed_px_s=");
  w.flt(rp.speed_px_s);
  w.f(" pulse_period_ms=%d wavelength_px=", (int)rp.pulse_period_ms);
  w.flt(rp.wavelength_px);
  w.f(" glow_r_px=");
  w.flt(rp.glow_r_px);
  w.f(" ring_live=%d burst=%d glyph=", rp.ring_live, rp.burst);
  w.s(name(rp.glyph));
  w.f(" arrow_deg=");
  w.flt(rp.arrow_deg);
  w.f(" cone_deg=");
  w.flt(rp.cone_deg);
  w.f(" arrow_style=");
  w.s(name(rp.arrow_style));
  w.f(" trend=%d trend_strong=%d countdown=", (int)rp.trend, rp.trend_strong);
  w.opt_int(rp.countdown);
  w.f(" runes=");
  if (!rp.runes) {
    w.f("n");
  } else {
    for (int i = 0; i < rp.runes->n && i < 3; i++) w.f(i ? "|%d" : "%d", (int)rp.runes->ids[i]);
  }
  w.f(" bump_icons=");
  w.opt_int(rp.bump_icons);
  w.f(" dist_band=");
  w.s(band_name(rp.dist_band));
  w.f(" dist_stale=%d word=", rp.dist_stale);
  w.text(rp.word);
  w.f(" top_text=");
  w.text(rp.top_text);
  w.f(" banner=");
  if (!rp.banner) {
    w.f("n");
  } else {
    w.text(rp.banner->text);
    w.f("|");
    w.s(name(rp.banner->severity));
    w.f("|%d", rp.banner->sticky);
  }
  const Status& s = rp.status;
  w.f(" status=%d|", (int)s.own_pct);
  w.opt_int(s.partner_pct);
  w.f("|%d|%d|%d menu_rows=", (int)s.link_q, s.visible, s.unreliable);
  if (!rp.menu_rows) {
    w.f("n");
  } else {
    for (int k = 0; k < rp.menu_rows->n && k < MENU_VISIBLE; k++) {
      if (k) w.f("|");
      w.text(rp.menu_rows->rows[k]);
    }
  }
  w.f(" sweep=");
  if (!rp.sweep) {
    w.f("n");
  } else {
    const Sweep& sw = *rp.sweep;
    w.flt(sw.wedge_deg);
    w.f("|");
    for (int k = 0; k < sw.n_bins && k < T::SCAN_BINS; k++) {
      if (k) w.f("/");
      w.flt(sw.bins[k]);
    }
    w.f("|");
    w.opt_int(sw.active_bin);
    w.f("|%d", sw.paused);
  }
  w.f(" haptic=");
  w.s(hp::name(rp.haptic));
  w.f(" heartbeat=");
  w.s(hp::name(rp.heartbeat));
  w.f(" heartbeat_every=%d backlight=", (int)rp.heartbeat_every);
  w.flt(rp.backlight);
  w.f(" sun=%d fps_cap=%d theme=", rp.sun, (int)rp.fps_cap);
  w.s(0 <= rp.theme && rp.theme < N_THEMES ? T::THEME_NAMES[rp.theme] : "?");
  return w.len;
}

}  // namespace render_params
}  // namespace hm
