// hm::render_params against finder/render_params.py (native/test/golden/render_params.txt).
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <map>
#include <string>
#include <vector>

#include "check.h"
#include "golden.h"
#include "hm/game.h"
#include "hm/render_params.h"

namespace rp_ = hm::render_params;
using hmt::none;
using hmt::num;
using rp_::RenderParams;

namespace {

double dbl(const std::string& s) { return strtod(s.c_str(), nullptr); }   // a Python repr, exactly
std::optional<double> opt_d(const std::string& s) { return none(s) ? std::nullopt : std::optional<double>(dbl(s)); }
std::optional<int32_t> opt_i(const std::string& s) {
  return none(s) ? std::nullopt : std::optional<int32_t>((int32_t)num(s));
}

std::vector<std::string> split(const std::string& s, char sep) {
  std::vector<std::string> out;
  if (s.empty()) return out;
  size_t a = 0;
  for (;;) {
    const size_t b = s.find(sep, a);
    out.push_back(s.substr(a, b == std::string::npos ? std::string::npos : b - a));
    if (b == std::string::npos) return out;
    a = b + 1;
  }
}

rp_::OptText text(const std::string& s) {
  if (none(s)) return std::nullopt;
  std::string t = s;
  for (char& c : t) c = c == '_' ? ' ' : c;
  return rp_::Text(t.c_str());
}

template <typename E>
bool enum_of(const std::string& s, E* v) {
  return rp_::from_name(none(s) ? nullptr : s.c_str(), v);
}

// One `name=value` token (as the golden file and dump() write it) into rp.
bool set_field(RenderParams& rp, const std::string& tok) {
  const size_t eq = tok.find('=');
  if (eq == std::string::npos) return false;
  const std::string k = tok.substr(0, eq), v = tok.substr(eq + 1);
  if (k == "t_ms") rp.t_ms = (hm::ticks_t)num(v);
  else if (k == "screen") return enum_of(v, &rp.screen);
  else if (k == "sub") return enum_of(v, &rp.sub);
  else if (k == "zone") rp.zone = opt_i(v);
  else if (k == "ramp") return enum_of(v, &rp.ramp);
  else if (k == "intensity") rp.intensity = dbl(v);
  else if (k == "speed_px_s") rp.speed_px_s = dbl(v);
  else if (k == "pulse_period_ms") rp.pulse_period_ms = (int32_t)num(v);
  else if (k == "wavelength_px") rp.wavelength_px = opt_d(v);
  else if (k == "glow_r_px") rp.glow_r_px = dbl(v);
  else if (k == "ring_live") rp.ring_live = num(v);
  else if (k == "burst") rp.burst = num(v);
  else if (k == "glyph") return enum_of(v, &rp.glyph);
  else if (k == "arrow_deg") rp.arrow_deg = opt_d(v);
  else if (k == "cone_deg") rp.cone_deg = opt_d(v);
  else if (k == "arrow_style") return enum_of(v, &rp.arrow_style);
  else if (k == "trend") rp.trend = (int32_t)num(v);
  else if (k == "trend_strong") rp.trend_strong = num(v);
  else if (k == "countdown") rp.countdown = opt_i(v);
  else if (k == "runes") {
    rp.runes.reset();
    if (!none(v)) {
      const std::vector<std::string> p = split(v, '|');
      if (p.size() > 3) return false;
      rp.runes = rp_::Runes{(int32_t)p.size(), {}};
      for (size_t i = 0; i < p.size(); i++) rp.runes->ids[i] = (int32_t)num(p[i]);
    }
  } else if (k == "bump_icons") rp.bump_icons = opt_i(v);
  else if (k == "dist_band") return rp_::band_from_name(none(v) ? nullptr : v.c_str(), &rp.dist_band);
  else if (k == "dist_stale") rp.dist_stale = num(v);
  else if (k == "word") rp.word = text(v);
  else if (k == "top_text") rp.top_text = text(v);
  else if (k == "banner") {
    rp.banner.reset();
    if (!none(v)) {
      const std::vector<std::string> p = split(v, '|');
      if (p.size() != 3) return false;
      rp_::Banner b;
      b.text = text(p[0]);
      b.sticky = num(p[2]);
      if (!rp_::from_name(p[1].c_str(), &b.severity)) return false;
      rp.banner = b;
    }
  } else if (k == "status") {
    const std::vector<std::string> p = split(v, '|');
    if (p.size() != 5) return false;
    rp.status = rp_::Status{(int32_t)num(p[0]), opt_i(p[1]), (int32_t)num(p[2]), num(p[3]) != 0, num(p[4]) != 0};
  } else if (k == "menu_rows") {
    rp.menu_rows.reset();
    if (!none(v)) {
      const std::vector<std::string> p = split(v, '|');
      if (p.size() > (size_t)rp_::MENU_VISIBLE) return false;
      rp.menu_rows = rp_::MenuRows{(int32_t)p.size(), {}};
      for (size_t i = 0; i < p.size(); i++) rp.menu_rows->rows[i] = text(p[i]);
    }
  } else if (k == "sweep") {
    rp.sweep.reset();
    if (!none(v)) {
      const std::vector<std::string> p = split(v, '|');
      if (p.size() != 4) return false;
      rp_::Sweep sw;
      sw.wedge_deg = dbl(p[0]);
      const std::vector<std::string> bins = split(p[1], '/');
      if (bins.size() > (size_t)hm::T::SCAN_BINS) return false;
      sw.n_bins = (int32_t)bins.size();
      for (size_t i = 0; i < bins.size(); i++) sw.bins[i] = opt_d(bins[i]);
      sw.active_bin = opt_i(p[2]);
      sw.paused = num(p[3]);
      rp.sweep = sw;
    }
  } else if (k == "haptic" || k == "heartbeat") {
    const hm::haptic_patterns::Haptic h = none(v) ? hm::haptic_patterns::NONE : hm::haptic_patterns::from_name(v.c_str());
    if (!none(v) && h == hm::haptic_patterns::NONE) return false;
    (k == "haptic" ? rp.haptic : rp.heartbeat) = h;
  } else if (k == "heartbeat_every") rp.heartbeat_every = (int32_t)num(v);
  else if (k == "backlight") rp.backlight = dbl(v);
  else if (k == "sun") rp.sun = num(v);
  else if (k == "fps_cap") rp.fps_cap = (int32_t)num(v);
  else return false;
  return true;
}

// A numeric piece matches by value (hmt::near), anything else by text.
bool same_piece(const std::string& want, const std::string& got) {
  if (want == got) return true;
  char* end = nullptr;
  strtod(got.c_str(), &end);
  const bool got_num = !got.empty() && *end == 0;
  strtod(want.c_str(), &end);
  return got_num && !want.empty() && *end == 0 && hmt::near(want, dbl(got));
}

bool same_value(const std::string& want, const std::string& got) {
  std::string w = want, g = got;
  for (char& c : w) c = c == '/' ? '|' : c;
  for (char& c : g) c = c == '/' ? '|' : c;
  const std::vector<std::string> a = split(w, '|'), b = split(g, '|');
  if (a.size() != b.size()) return false;
  for (size_t i = 0; i < a.size(); i++) {
    if (!same_piece(a[i], b[i])) return false;
  }
  return true;
}

std::map<std::string, std::string> fields_of(const std::string& line) {
  std::map<std::string, std::string> m;
  size_t a = 0;
  while (a < line.size()) {
    size_t b = line.find(' ', a);
    if (b == std::string::npos) b = line.size();
    const std::string tok = line.substr(a, b - a);
    const size_t eq = tok.find('=');
    if (eq != std::string::npos) m[tok.substr(0, eq)] = tok.substr(eq + 1);
    a = b + 1;
  }
  return m;
}

// dump(rp) has every field, in FIELDS order, each matching the Python's token.
bool dump_matches(const RenderParams& rp, const hmt::Tokens& t, size_t from) {
  char buf[rp_::DUMP_MAX];
  const int n = rp_::dump(rp, buf, sizeof buf);
  if (n <= 0 || n >= (int)sizeof buf) return false;
  std::map<std::string, std::string> got = fields_of(buf);
  if (got.size() != (size_t)rp_::N_FIELDS || t.size() - from != (size_t)rp_::N_FIELDS) return false;
  size_t i = from;
  for (int f = 0; f < rp_::N_FIELDS; f++, i++) {
    const std::string want_k = rp_::FIELDS[f];
    if (t[i].compare(0, want_k.size() + 1, want_k + "=") != 0) return false;
    const std::string want = t[i].substr(want_k.size() + 1);
    if (!same_value(want, got[want_k])) {
      printf("  %s: want %s, got %s\n", want_k.c_str(), want.c_str(), got[want_k].c_str());
      return false;
    }
  }
  return strncmp(buf, "t_ms=", 5) == 0;
}

std::string joined(const hmt::Tokens& t, size_t from) {
  std::string s;
  for (size_t i = from; i < t.size(); i++) s += (i > from ? " " : "") + t[i];
  return s;
}

void collect(void* ctx, const char* msg) { static_cast<std::vector<std::string>*>(ctx)->push_back(msg); }

size_t arrow_at(const hmt::Tokens& t) {
  for (size_t i = 0; i < t.size(); i++) {
    if (t[i] == "->") return i;
  }
  return t.size();
}

}  // namespace

TEST(test_render_params_names_like_python) {
  int n = 0;
  for (const hmt::Tokens& t : hmt::golden("render_params")) {
    if (t[0] == "fields") {
      CHECK(t.size() == 1 + rp_::N_FIELDS);
      for (int i = 0; i < rp_::N_FIELDS; i++) CHECK(t[1 + i] == rp_::FIELDS[i]);
      n++;
    } else if (t[0] == "menu_visible") {
      CHECK(num(t[1]) == rp_::MENU_VISIBLE);
      n++;
    } else if (t[0] == "names") {
      const std::string& e = t[1];
      const int k = (int)t.size() - 2;
      for (int i = 0; i < k; i++) {
        const std::string& want = t[2 + i];
        const char* got = e == "screen"        ? rp_::name((rp_::Screen)i)
                          : e == "sub"         ? rp_::name((rp_::Sub)i)
                          : e == "glyph"       ? rp_::name((rp_::Glyph)i)
                          : e == "ramp"        ? rp_::name((hm::Ramp)i)
                          : e == "arrow_style" ? rp_::name((rp_::ArrowStyle)i)
                          : e == "severity"    ? rp_::name((rp_::Severity)i)
                          : e == "band"        ? rp_::band_name(i)
                                               : hm::haptic_patterns::name(rp_::ZONE_HEARTBEAT[i]);
        CHECK(got ? want == got : none(want));
      }
      const int count = e == "screen" ? rp_::N_SCREENS : e == "sub" ? rp_::N_SUBS : e == "glyph" ? rp_::N_GLYPHS
                        : e == "ramp" ? hm::RAMP_N : e == "arrow_style" ? rp_::N_ARROW_STYLES
                        : e == "severity" ? rp_::N_SEVERITIES : e == "band" ? rp_::N_BANDS : 4;
      CHECK(k == count);
      n++;
    }
  }
  CHECK(rp_::name(rp_::Sub::NONE) == nullptr && rp_::name(rp_::Glyph::NONE) == nullptr);
  CHECK(strcmp(rp_::name(rp_::menu_sub(2, true, true)), "2^v") == 0);
  CHECK(rp_::menu_row(rp_::menu_sub(3, false, true)) == 3 && rp_::menu_down(rp_::menu_sub(3, false, true)) &&
        !rp_::menu_up(rp_::menu_sub(3, false, true)) && rp_::is_menu_sub(rp_::menu_sub(0, false, false)));
  CHECK(n == 10);
}

TEST(test_render_params_make_wavelength_arrow_style_like_python) {
  int makes = 0, waves = 0, styles = 0;
  for (const hmt::Tokens& t : hmt::golden("render_params")) {
    if (t[0] == "make") {
      const size_t a = arrow_at(t);
      RenderParams kw;   // DEFAULTS
      for (size_t i = 1; i < a; i++) CHECK(set_field(kw, t[i]));
      CHECK(dump_matches(rp_::make_params(kw), t, a + 1));
      makes++;
    } else if (t[0] == "wavelength") {
      CHECK(hmt::near(t[4], rp_::wavelength(dbl(t[1]), (int32_t)num(t[2]))));
      waves++;
    } else if (t[0] == "arrow_style") {
      const char* got = rp_::name(rp_::arrow_style(opt_d(t[1])));
      CHECK(got ? t[3] == got : none(t[3]));
      styles++;
    }
  }
  CHECK(makes == 11 && waves == 16 && styles == 19);
}

TEST(test_render_params_validate_like_python) {
  int cases = 0, errors = 0;
  std::vector<std::string> got;
  size_t next = 0;
  bool in_case = false;
  for (const hmt::Tokens& t : hmt::golden("render_params")) {
    if (t[0] == "validate") {
      if (in_case) CHECK(next == got.size());   // no missing error lines for the last case
      const size_t a = arrow_at(t);
      RenderParams rp;   // DEFAULTS, then the fields that differ (wavelength_px always given)
      for (size_t i = 1; i < a; i++) CHECK(set_field(rp, t[i]));
      got.clear();
      const int n = rp_::validate(rp, collect, &got);
      CHECK(n == (int)got.size() && n == num(t[a + 1]));
      CHECK(rp_::validate(rp) == n);
      next = 0;
      in_case = true;
      cases++;
    } else if (t[0] == "error") {
      CHECK(in_case && next < got.size());
      const std::string want = joined(t, 1);
      if (want != got[next]) printf("  want: %s\n  got:  %s\n", want.c_str(), got[next].c_str());
      CHECK(want == got[next]);
      next++;
      errors++;
    }
  }
  CHECK(next == got.size());
  CHECK(cases == 203 && errors == 218);
}

// test_bump_icons_rules: the word validate checks is the game's.
static_assert(rp_::streq(hm::game::W_BUMP, rp_::W_BUMP) && rp_::BUMP_ICONS_MAX == 5, "W_BUMP or BUMP_ICONS_MAX");

TEST(test_render_params_defaults_are_a_valid_searching_frame) {
  const RenderParams rp = rp_::make_params();
  CHECK(rp.screen == rp_::Screen::SEARCHING && rp.ramp == hm::RAMP_GREY && rp.glyph == rp_::Glyph::SEEKER);
  CHECK(rp.wavelength_px && *rp.wavelength_px == rp_::wavelength(rp.speed_px_s, rp.pulse_period_ms));
  CHECK(rp_::validate(rp) == 0);
  CHECK(!RenderParams().wavelength_px && RenderParams().status.own_pct == 100);
  char small[16];
  CHECK(rp_::dump(rp, small, sizeof small) > (int)sizeof small && strlen(small) == sizeof small - 1);
  rp_::Text long_text("THIS IS A TEXT LONGER THAN THIRTY-ONE CHARS");
  CHECK(strlen(long_text.s) == (size_t)rp_::TEXT_MAX && long_text == "THIS IS A TEXT LONGER THAN THIR");
  CHECK(!rp_::opt_text(nullptr) && rp_::opt_text("GO") && *rp_::opt_text("GO") == "GO");
}
