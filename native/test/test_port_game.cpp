// finder/game.py against its Python trace (tests/test_game.py drives it).
#include <deque>
#include <string>

#include "check.h"
#include "game_probe.h"
#include "hm/game.h"
#include "port.h"

using namespace hm;
using namespace hmt;
namespace rp = hm::render_params;
namespace hp = hm::haptic_patterns;

// tests/test_game.py's FakeEst: reports the distance the test sets after any packet.
struct FakeEst final : est::RangeEstimator {
  double fixed = 40.0;
  std::optional<double> cal;
  int32_t n = 0;

  explicit FakeEst(double d) : fixed(d) { reset(); }
  const char* name() const override { return "fake"; }
  void reset() override {
    RangeEstimator::reset();
    rssi_var = 4.0;
  }
  void calibrate(double p) override { cal = p; }
  void update(ticks_t, std::optional<double> rssi, std::optional<double>, const MotionInfo*, const MotionInfo*) override {
    n += 1;
    rssi_f = rssi;
    dist_m = fixed;
  }
  // The fields a test wrote (r.est.fixed = 20.0, r.est.noise_db = ...).
  void set(const Json& v) {
    for (auto& kv : v.o) {
      const std::string& f = kv.first;
      const Json& x = kv.second;
      if (f == "fixed") fixed = x.num();
      else if (f == "cal") cal = x.opt_num();
      else if (f == "n") n = (int32_t)x.in();
      else if (f == "jit") jit = x.num();
      else if (f == "rssi_f") rssi_f = x.opt_num();
      else if (f == "rssi_var") rssi_var = x.num();
      else if (f == "noise_db") noise_db = x.opt_num();
      else if (f == "rate_db_s") rate_db_s = x.num();
      else if (f == "dist_m") dist_m = x.opt_num();
      else if (f == "dist_lo_m") dist_lo_m = x.opt_num();
      else if (f == "dist_hi_m") dist_hi_m = x.opt_num();
      else if (f == "trend") trend = (int32_t)x.in();
      else if (f == "trend_conf") trend_conf = x.num();
      else if (f == "last_t") last_t = opt_tick(x);
    }
  }
};

// One recorded Game with what it was given: the test's estimator, the
// recorded answers of its blank_fn, the arrows a test put in it and the hint
// text it set.
struct Rig {
  std::optional<FakeEst> fake;
  Calls* calls = nullptr;
  std::deque<arrow::Arrow> arrows;
  std::string hint;
  std::optional<game::Game> g;
};

static bool recorded_blank(void* rig, ticks_t t) {
  return static_cast<Rig*>(rig)->calls->take("blank_fn", J(std::vector<Json>{J(t)})).flag();
}

static arrow::Mode arrow_mode(const Json& v) {
  if (v.str() == "guided") return arrow::MODE_GUIDED;
  if (v.str() == "static") return arrow::MODE_STATIC;
  throw Mismatch("arrow mode " + v.str());
}

static Json Jtext(const rp::OptText& t) { return t ? J(t->s) : J(); }

static Json J(const rp::RenderParams& p) {
  Json runes = J(), banner = J(), rows = J(), sweep = J();
  if (p.runes) runes = Jarr(p.runes->ids, p.runes->n);
  if (p.banner) banner = J(std::vector<Json>{Jtext(p.banner->text), J(rp::name(p.banner->severity)), J(p.banner->sticky)});
  if (p.menu_rows) {
    std::vector<Json> r;
    for (int k = 0; k < p.menu_rows->n; k++) r.push_back(Jtext(p.menu_rows->rows[k]));
    rows = J(std::move(r));
  }
  if (p.sweep) {
    sweep = J(std::vector<Json>{J(p.sweep->wedge_deg), Jarr(p.sweep->bins, p.sweep->n_bins), J(p.sweep->active_bin),
                                J(p.sweep->paused)});
  }
  const rp::Status& st = p.status;
  return Jobj("RenderParams", {
      {"t_ms", J(p.t_ms)},
      {"screen", J(rp::name(p.screen))},
      {"sub", J(rp::name(p.sub))},
      {"zone", J(p.zone)},
      {"ramp", J(rp::name(p.ramp))},
      {"intensity", J(p.intensity)},
      {"speed_px_s", J(p.speed_px_s)},
      {"pulse_period_ms", J(p.pulse_period_ms)},
      {"wavelength_px", J(p.wavelength_px)},
      {"glow_r_px", J(p.glow_r_px)},
      {"ring_live", J(p.ring_live)},
      {"burst", J(p.burst)},
      {"glyph", J(rp::name(p.glyph))},
      {"arrow_deg", J(p.arrow_deg)},
      {"cone_deg", J(p.cone_deg)},
      {"arrow_style", J(rp::name(p.arrow_style))},
      {"trend", J(p.trend)},
      {"trend_strong", J(p.trend_strong)},
      {"countdown", J(p.countdown)},
      {"runes", runes},
      {"bump_icons", J(p.bump_icons)},
      {"dist_band", J(rp::band_name(p.dist_band))},
      {"dist_stale", J(p.dist_stale)},
      {"word", Jtext(p.word)},
      {"top_text", Jtext(p.top_text)},
      {"banner", banner},
      {"status", J(std::vector<Json>{J(st.own_pct), J(st.partner_pct), J(st.link_q), J(st.visible), J(st.unreliable)})},
      {"menu_rows", rows},
      {"sweep", sweep},
      {"haptic", J(hp::name(p.haptic))},
      {"heartbeat", J(hp::name(p.heartbeat))},
      {"heartbeat_every", J(p.heartbeat_every)},
      {"backlight", J(p.backlight)},
      {"fps_cap", J(p.fps_cap)},
      {"sun", J(p.sun)},
  });
}

TEST(trace_game) {
  Port<Rig> p;
  p.cls = "finder.game.Game";
  // est: the test's FakeEst, or an estimator with its own trace (or none
  // ported, which these tests only reset): the game's use of it shows in the
  // rest of its state. blank_fn: answered from the trace.
  p.ignore = {"est", "blank_fn"};
  p.make = [](const Json& a, Calls& calls) {
    auto r = std::make_unique<Rig>();
    r->calls = &calls;
    const Json& e = a[1];
    if (!e.null() && e["@"].str() == "FakeEst") {
      r->fake.emplace(e["fixed"].num());
      r->fake->set(e);
    }
    r->g.emplace(a[0].null() ? std::optional<Mac>() : std::optional<Mac>(mac(a[0])),
                 r->fake ? &*r->fake : nullptr, arrow_mode(a[2]), a[3].null() ? nullptr : recorded_blank,
                 r.get(), tick(a[4]), a[5].null() ? std::optional<int32_t>() : std::optional<int32_t>((int32_t)a[5].in()));
    return r;
  };
  p.call = [](Rig& r, const std::string& m, const Json& a) -> Json {
    game::Game& g = *r.g;
    if (m == "set_place") return g.set_place(a[0].flag()), J();
    if (m == "reset") return g.reset(tick(a[0])), J();
    if (m == "restart_estimate") return g.restart_estimate(), J();
    if (m == "set_motion") {
      return g.set_motion(tick(a[0]), (int32_t)a[1].in(), (uint32_t)a[2].in(), a[3].num(), a[4].opt_num(), a[5].flag()),
             J();
    }
    if (m == "set_battery") return g.set_battery(tick(a[0]), a[1].null() ? std::nullopt : std::optional<int32_t>((int32_t)a[1].in())), J();
    if (m == "set_usb") return g.set_usb(tick(a[0]), a[1].flag()), J();
    if (m == "on_packet") return g.on_packet(tick(a[0]), mac(a[1]), a[2].num(), beacon(a[3])), J();
    if (m == "on_accel_tap") return J(g.on_accel_tap(tick(a[0])));
    if (m == "bump_armed") return J(g.bump_armed());
    if (m == "on_touch_down") return g.on_touch_down(tick(a[0])), J();
    if (m == "on_wake") return g.on_wake(tick(a[0])), J();
    if (m == "on_gesture") {
      return g.on_gesture(tick(a[0]), (int)a[1].in(), (int32_t)a[2].in(), (int32_t)a[3].in(), opt_tick(a[4])), J();
    }
    if (m == "on_button") return g.on_button(tick(a[0]), a[1].flag()), J();
    if (m == "blanked") return J(g.blanked(tick(a[0])));
    if (m == "tick") return J(g.tick(tick(a[0])));
    if (m == "fill_beacon") {
      proto::Beacon b = beacon(a[0]);
      return Jobj("Beacon", g.fill_beacon(b, tick(a[1])), beacon_state);
    }
    if (m == "_emit") return game::Probe::emit(g, tick(a[0]), a[1].null() ? hp::NONE : hp::from_name(a[1].str().c_str())), J();
    if (m == "_new_round") return game::Probe::new_round(g, tick(a[0])), J();
    if (m == "_toast_set") {
      rp::Severity sev;
      if (!rp::from_name(a[1].str().c_str(), &sev)) throw Mismatch("severity " + a[1].str());
      return game::Probe::toast_set(g, a[0].str().c_str(), sev), J();
    }
    if (m == "_hint_set") {
      r.hint = a[1].str();
      return game::Probe::hint_set(g, tick(a[0]), r.hint.c_str()), J();
    }
    if (m == "_state_byte") return J(game::Probe::state_byte(g, tick(a[0])));
    if (m == "_peer_hz") return J(game::Probe::peer_hz(g));
    if (m == "_tap") return J(g.bump_t);
    if (m == "_screen") return J(game::Probe::screen(g));
    unported(m);
  };
  p.state = [](const Rig& r, State& s) {
    const game::Game& g = *r.g;
    s("my_mac", g.my_mac ? J(*g.my_mac) : J());
    s("indoor", J(g.indoor));
    s("arrow_mode", J(arrow::name(g.arrow_mode)));
    s("battery", J(g.battery));
    s("sun", J(g.sun));
    s("buzz", J(g.buzz));
    s("params", g.params ? J(*g.params) : J());
    s("goodbye_left", J(g.goodbye_left));
    s("power_off", J(g.power_off));
    s("screen_on", J(g.screen_on));
    s("usb", J(g.usb));
    s("mode", J(game::name(g.mode)));
    s("mode_t", J(g.mode_t));
    s("arrow", g.arrow ? Jobj("Arrow", {}) : J());   // an Arrow's own trace checks it
    s("rssi_last", J(g.rssi_last));
    s("bump_t", J(g.bump_t));
    s("taps", J(g.taps));
    s("lost_trend", J(g.lost_trend));
    s("bump_ready", J(g.bump_ready));
    s("round_t0", J(g.round_t0));
    s("found_t", J(g.found_t));
    s("scans", J(g.scans));
    s("state_byte", J(g.state_byte));
    s("saver", J(g.saver()));
    s("beacon_hz", J(g.beacon_hz()));
    s("menu_open", J(g.menu_open()));
    s("screen", J(g.screen()));
  };
  p.set = [](Rig& r, const std::string& f, const Json& v) {
    game::Game& g = *r.g;
    if (f == "est") return r.fake ? (r.fake->set(v), true) : false;
    if (f == "buzz") return g.buzz = (int)v.in(), true;
    if (f == "battery") return g.battery = v.null() ? std::nullopt : std::optional<int32_t>((int32_t)v.in()), true;
    if (f == "rssi_last") return g.rssi_last = v.opt_num(), true;
    if (f == "round_t0") return g.round_t0 = opt_tick(v), true;   // a test's long round
    if (f == "pair.split_s") return g.pair.split_s = (int32_t)v.in(), true;   // a test's short split
    if (f == "menu_open" && !v.flag()) return g.menu.close(), true;   // a test's g.menu.close()
    if (f == "arrow") {   // a fresh A.make(...) put in by the test
      if (v.null()) return g.arrow = nullptr, true;
      r.arrows.emplace_back(v["theta"].num(), v["s0"].num(), tick(v["t0"]), arrow_mode(v["mode"]), v["probe"].flag());
      return g.arrow = &r.arrows.back(), true;
    }
    return false;
  };
  CHECK_REPLAY(p);
}
