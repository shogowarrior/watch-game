// hm::ui::Renderer against the MicroPython renderer: every tools/render_snapshots.py
// fixture (native/test/golden/frames.txt) rendered as render_fixture does, its last
// frame's CRC-32 checked against tests/snapshot_crc.json.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <fstream>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include "check.h"
#include "golden.h"
#include "hm/renderer.h"
#include "hm/text.h"

namespace rp_ = hm::render_params;
using hmt::none;
using hmt::num;

namespace {

constexpr int W = hm::ui::Renderer::W;

std::vector<std::string> split(const std::string& s, char sep) {
  std::vector<std::string> out;
  size_t a = 0;
  for (;;) {
    const size_t b = s.find(sep, a);
    out.push_back(s.substr(a, b == std::string::npos ? std::string::npos : b - a));
    if (b == std::string::npos) return out;
    a = b + 1;
  }
}

double dbl(const std::string& s) { return strtod(s.c_str(), nullptr); }   // a Python repr, exactly
std::optional<double> opt_d(const std::string& s) { return none(s) ? std::nullopt : std::optional<double>(dbl(s)); }
std::optional<int32_t> opt_i(const std::string& s) {
  return none(s) ? std::nullopt : std::optional<int32_t>((int32_t)num(s));
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

// One `name=value` token (native/tools/golden/render_params.py's format) into p.
bool set_field(rp_::RenderParams& p, const std::string& tok) {
  const size_t eq = tok.find('=');
  if (eq == std::string::npos) return false;
  const std::string k = tok.substr(0, eq), v = tok.substr(eq + 1);
  const std::vector<std::string> parts = split(v, '|');
  if (k == "t_ms") p.t_ms = (hm::ticks_t)num(v);
  else if (k == "screen") return enum_of(v, &p.screen);
  else if (k == "sub") return enum_of(v, &p.sub);
  else if (k == "zone") p.zone = opt_i(v);
  else if (k == "ramp") return enum_of(v, &p.ramp);
  else if (k == "intensity") p.intensity = dbl(v);
  else if (k == "speed_px_s") p.speed_px_s = dbl(v);
  else if (k == "pulse_period_ms") p.pulse_period_ms = (int32_t)num(v);
  else if (k == "wavelength_px") p.wavelength_px = opt_d(v);
  else if (k == "glow_r_px") p.glow_r_px = dbl(v);
  else if (k == "ring_live") p.ring_live = num(v);
  else if (k == "burst") p.burst = num(v);
  else if (k == "glyph") return enum_of(v, &p.glyph);
  else if (k == "arrow_deg") p.arrow_deg = opt_d(v);
  else if (k == "cone_deg") p.cone_deg = opt_d(v);
  else if (k == "arrow_style") return enum_of(v, &p.arrow_style);
  else if (k == "trend") p.trend = (int32_t)num(v);
  else if (k == "trend_strong") p.trend_strong = num(v);
  else if (k == "countdown") p.countdown = opt_i(v);
  else if (k == "runes") {
    p.runes.reset();
    if (!none(v)) {
      if (parts.size() > 3) return false;
      p.runes = rp_::Runes{(int32_t)parts.size(), {}};
      for (size_t i = 0; i < parts.size(); i++) p.runes->ids[i] = (int32_t)num(parts[i]);
    }
  } else if (k == "dist_band") return rp_::band_from_name(none(v) ? nullptr : v.c_str(), &p.dist_band);
  else if (k == "dist_stale") p.dist_stale = num(v);
  else if (k == "word") p.word = text(v);
  else if (k == "top_text") p.top_text = text(v);
  else if (k == "banner") {
    p.banner.reset();
    if (!none(v)) {
      if (parts.size() != 3) return false;
      rp_::Banner b;
      b.text = text(parts[0]);
      b.sticky = num(parts[2]);
      if (!rp_::from_name(parts[1].c_str(), &b.severity)) return false;
      p.banner = b;
    }
  } else if (k == "status") {
    if (parts.size() != 5) return false;
    p.status = rp_::Status{(int32_t)num(parts[0]), opt_i(parts[1]), (int32_t)num(parts[2]), num(parts[3]) != 0,
                           num(parts[4]) != 0};
  } else if (k == "menu_rows") {
    p.menu_rows.reset();
    if (!none(v)) {
      if (parts.size() > (size_t)rp_::MENU_VISIBLE) return false;
      p.menu_rows = rp_::MenuRows{(int32_t)parts.size(), {}};
      for (size_t i = 0; i < parts.size(); i++) p.menu_rows->rows[i] = text(parts[i]);
    }
  } else if (k == "sweep") {
    p.sweep.reset();
    if (!none(v)) {
      if (parts.size() != 4) return false;
      rp_::Sweep sw;
      sw.wedge_deg = dbl(parts[0]);
      const std::vector<std::string> bins = split(parts[1], '/');
      if (bins.size() > (size_t)hm::T::SCAN_BINS) return false;
      sw.n_bins = (int32_t)bins.size();
      for (size_t i = 0; i < bins.size(); i++) sw.bins[i] = opt_d(bins[i]);
      sw.active_bin = opt_i(parts[2]);
      sw.paused = num(parts[3]);
      p.sweep = sw;
    }
  } else if (k == "haptic" || k == "heartbeat") {
    const hm::haptic_patterns::Haptic h = none(v) ? hm::haptic_patterns::NONE : hm::haptic_patterns::from_name(v.c_str());
    if (!none(v) && h == hm::haptic_patterns::NONE) return false;
    (k == "haptic" ? p.haptic : p.heartbeat) = h;
  } else if (k == "heartbeat_every") p.heartbeat_every = (int32_t)num(v);
  else if (k == "backlight") p.backlight = dbl(v);
  else if (k == "sun") p.sun = num(v);
  else if (k == "fps_cap") p.fps_cap = (int32_t)num(v);
  else return false;
  return true;
}

struct Phase {
  int off;
  rp_::RenderParams p;
};
struct Fixture {
  std::string name;
  int run_ms;
  std::vector<Phase> phases;
};

int T0 = 0, FPS_MS = 0;

// native/test/golden/frames.txt; empty on a parse error.
std::vector<Fixture> fixtures() {
  std::vector<Fixture> out;
  for (const hmt::Tokens& t : hmt::golden("frames")) {
    if (t[0] == "t0" && t.size() == 4) {
      T0 = (int)num(t[1]);
      FPS_MS = (int)num(t[3]);
    } else if (t[0] == "fixture" && t.size() == 4) {
      out.push_back(Fixture{t[1], (int)num(t[2]), {}});
    } else if (t[0] == "phase" && !out.empty() && t.size() == 2 + (size_t)rp_::N_FIELDS) {
      Phase ph{(int)num(t[1]), rp_::RenderParams()};
      for (size_t i = 2; i < t.size(); i++) {
        if (!set_field(ph.p, t[i])) {
          printf("  frames.txt: bad token %s\n", t[i].c_str());
          return {};
        }
      }
      out.back().phases.push_back(ph);
    } else {
      printf("  frames.txt: bad line starting %s\n", t[0].c_str());
      return {};
    }
  }
  return out;
}

// tests/snapshot_crc.json: {"name": crc, ...}, one entry per line.
std::map<std::string, uint32_t> snapshot_crcs() {
  std::map<std::string, uint32_t> out;
  std::ifstream f("tests/snapshot_crc.json");
  std::string line;
  while (std::getline(f, line)) {
    const size_t a = line.find('"'), b = line.find('"', a + 1), c = line.find(':', b);
    if (a == std::string::npos || b == std::string::npos || c == std::string::npos) continue;
    out[line.substr(a + 1, b - a - 1)] = (uint32_t)strtoul(line.c_str() + c + 1, nullptr, 10);
  }
  return out;
}

// zlib CRC-32 of the frame's bytes as framebuf stores them (each pixel little-endian).
uint32_t crc32(const uint16_t* px, size_t n) {
  uint32_t c = 0xFFFFFFFFu;
  for (size_t i = 0; i < 2 * n; i++) {
    c ^= (i & 1) ? px[i / 2] >> 8 : px[i / 2] & 0xFF;
    for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1)));
  }
  return ~c;
}

uint16_t frame_buf[W * W], strip_buf[W * W];

// render_snapshots.render_fixture: a frame every FPS_MS from T0 through run_ms,
// each with the phase in effect and t_ms = T0 + t. Leaves the last frame in frame_buf.
void render_fixture(hm::ui::Renderer& r, const Fixture& fx, rp_::RenderParams& p) {
  r.reset();
  size_t k = 0;
  for (int t = 0; t <= fx.run_ms; t += FPS_MS) {
    while (k + 1 < fx.phases.size() && fx.phases[k + 1].off <= t) k++;
    p = fx.phases[k].p;
    p.t_ms = (hm::ticks_t)(T0 + t);
    r.frame(p, (hm::ticks_t)(T0 + t));
  }
  r.draw(p, frame_buf);
}

// Draw the last frame again in strips of h rows; true if it equals frame_buf.
bool strips_match(hm::ui::Renderer& r, const rp_::RenderParams& p, int h) {
  memset(strip_buf, 0x5A, sizeof strip_buf);   // a row a strip skips shows
  for (int y = 0; y < W; y += h) r.draw_strip(p, y, y + h <= W ? h : W - y, strip_buf + y * W);
  return memcmp(strip_buf, frame_buf, sizeof frame_buf) == 0;
}

}  // namespace

TEST(test_renderer_fixtures_cover_the_snapshots) {
  const std::vector<Fixture> fx = fixtures();
  const std::map<std::string, uint32_t> want = snapshot_crcs();
  CHECK(!fx.empty() && T0 > 0 && FPS_MS > 0);
  CHECK(fx.size() == want.size());
  for (const Fixture& f : fx) CHECK(want.count(f.name) && !f.phases.empty() && f.phases[0].off == 0);
}

TEST(test_renderer_matches_every_snapshot_crc) {
  const std::vector<Fixture> fx = fixtures();
  const std::map<std::string, uint32_t> want = snapshot_crcs();
  CHECK(!fx.empty());
  std::unique_ptr<hm::ui::Renderer> r(new hm::ui::Renderer());   // one renderer, reset per fixture
  rp_::RenderParams p;
  int matched = 0;
  for (const Fixture& f : fx) {
    render_fixture(*r, f, p);
    const uint32_t got = crc32(frame_buf, W * W);
    const auto it = want.find(f.name);
    if (it != want.end() && it->second == got) matched++;
    else printf("  %s: crc %08x, the MicroPython renderer's %08x\n", f.name.c_str(), (unsigned)got,
                it == want.end() ? 0u : (unsigned)it->second);
  }
  printf("  renderer: %d of %d snapshot CRCs match\n", matched, (int)fx.size());
  CHECK(matched == (int)fx.size());
}

namespace {

const Fixture* find(const std::vector<Fixture>& fx, const char* name) {
  for (const Fixture& f : fx) {
    if (f.name == name) return &f;
  }
  return nullptr;
}

}  // namespace

TEST(test_renderer_strips_draw_the_whole_frame) {
  // The watch pushes each strip while drawing the next: any strip height gives
  // the frame drawn whole (the overlay culling boxes cover every glyph). Every
  // fixture, plus the widest beam, the turn pacer and the scan wedge all round.
  std::vector<Fixture> fx = fixtures();
  CHECK(!fx.empty());
  const Fixture* arrow = find(fx, "direction_walk_outline");
  const Fixture* turn = find(fx, "direction_turn");
  const Fixture* sweep = find(fx, "scan_sweep");
  CHECK(arrow && turn && sweep);
  std::vector<Fixture> more;
  for (int d = 0; d < 360; d += 15) {
    Fixture a = *arrow, w = *turn, s = *sweep;
    a.phases[0].p.arrow_deg = d;
    a.phases[0].p.cone_deg = 60;
    a.run_ms = 400;
    w.phases[0].p.sweep->wedge_deg = d;
    s.phases[0].p.sweep->wedge_deg = d;
    s.phases[0].p.sweep->active_bin = d / 30;
    more.push_back(a);
    more.push_back(w);
    more.push_back(s);
  }
  fx.insert(fx.end(), more.begin(), more.end());
  std::unique_ptr<hm::ui::Renderer> r(new hm::ui::Renderer());
  rp_::RenderParams p;
  for (const Fixture& f : fx) {
    render_fixture(*r, f, p);
    for (int h : {24, 60, 7, 1}) {
      if (!strips_match(*r, p, h)) printf("  %s (%g): strips of %d differ\n", f.name.c_str(),
                                          p.sweep ? p.sweep->wedge_deg : p.arrow_deg.value_or(-1), h);
      CHECK(strips_match(*r, p, h));
    }
  }
}

TEST(test_renderer_heartbeat_on_live_spawns_only) {
  // tests/test_renderer.py test_heartbeat_on_live_spawns_only: a TICK per live
  // ring spawn (every 500 ms), none for ghost rings, every 2nd with heartbeat_every 2.
  const std::vector<Fixture> fx = fixtures();
  CHECK(!fx.empty());
  const Fixture* warm = find(fx, "warm_colder");
  CHECK(warm);
  std::unique_ptr<hm::ui::Renderer> r(new hm::ui::Renderer());
  rp_::RenderParams p = warm->phases[0].p;   // WARM: hunt(2, ...)
  p.glyph = rp_::Glyph::GLOW;
  p.pulse_period_ms = 500;
  p.heartbeat = hm::haptic_patterns::TICK;
  const auto beats = [&](bool live, int every, bool drawn) {
    std::vector<int> at;
    r->reset();
    p.ring_live = live;
    p.heartbeat_every = every;
    for (int t = 0; t <= 2000; t += 50) {
      p.t_ms = (hm::ticks_t)(T0 + t);
      if (r->frame(p, p.t_ms, drawn) == hm::haptic_patterns::TICK) at.push_back(t);
    }
    return at;
  };
  CHECK(beats(true, 1, true) == std::vector<int>({0, 500, 1000, 1500, 2000}));
  CHECK(beats(false, 1, true).empty());
  CHECK(beats(true, 2, true).size() == 2);
  CHECK(beats(true, 1, false).size() == 5);   // screen off: the state still advances
}

TEST(test_renderer_wake_shows_the_state_without_intro) {
  // §8: frames drawn after screen-off frames show the current state at once.
  // A fixture run dark until its last frame, then drawn, has its arrow at rest
  // (no scale-in) and its toast up: the same frame as one drawn throughout
  // once both have settled.
  const std::vector<Fixture> fx = fixtures();
  CHECK(!fx.empty());
  std::unique_ptr<hm::ui::Renderer> r(new hm::ui::Renderer());
  rp_::RenderParams p;
  static uint16_t lit[W * W];
  for (const char* name : {"hot_arrow_solid_a", "scan_result_no_fix", "link_lost"}) {
    const Fixture* f = find(fx, name);
    CHECK(f);
    render_fixture(*r, *f, p);   // drawn every frame
    memcpy(lit, frame_buf, sizeof lit);
    r->reset();
    size_t k = 0;
    for (int t = 0; t <= f->run_ms; t += FPS_MS) {
      while (k + 1 < f->phases.size() && f->phases[k + 1].off <= t) k++;
      p = f->phases[k].p;
      p.t_ms = (hm::ticks_t)(T0 + t);
      r->frame(p, p.t_ms, t == f->run_ms);
    }
    r->draw(p, frame_buf);
    if (memcmp(lit, frame_buf, sizeof lit)) printf("  %s: the woken frame differs\n", name);
    CHECK(memcmp(lit, frame_buf, sizeof lit) == 0);
  }
}

TEST(test_text_num_is_python_str) {
  CHECK(strcmp(hm::ui::Num(0).s, "0") == 0 && strcmp(hm::ui::Num(24).s, "24") == 0);
  CHECK(strcmp(hm::ui::Num(-7).s, "-7") == 0 && strcmp(hm::ui::Num(2147483647).s, "2147483647") == 0);
  CHECK(strcmp(hm::ui::Num(-2147483647 - 1).s, "-2147483648") == 0);
}
