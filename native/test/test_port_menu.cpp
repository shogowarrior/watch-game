// finder/menu.py and finder/haptic_patterns.py against their Python traces.
#include "check.h"
#include "hm/haptic_patterns.h"
#include "hm/menu.h"
#include "port.h"

using namespace hm;
using namespace hmt;

TEST(trace_menu) {
  Port<menu::Menu> p;
  p.cls = "finder.menu.Menu";
  p.make = [](const Json&, Calls&) { return std::make_unique<menu::Menu>(); };
  p.call = [](menu::Menu& u, const std::string& m, const Json& a) -> Json {
    if (m == "open") return u.open(tick(a[0])), J();
    if (m == "close") return u.close(), J();
    if (m == "reset") return u.reset(tick(a[0])), J();
    if (m == "next") return J(u.next(tick(a[0])));
    if (m == "scroll") return u.scroll(tick(a[0]), (int)a[1].in()), J();
    if (m == "tap") return J(u.tap(tick(a[0]), (int32_t)a[1].in()));
    if (m == "select") return J(u.select(tick(a[0]), (int)a[1].in()));
    if (m == "tick") return u.tick(tick(a[0])), J();
    if (m == "window") {   // Python returns None
      const int32_t th = render_params::theme_of(a[3].str().c_str());
      if (th == render_params::N_THEMES) throw Mismatch("theme " + a[3].str());
      return u.window(a[0].flag(), (int)a[1].in(), a[2].flag(), th), J();
    }
    unported(m);
  };
  p.state = [](const menu::Menu& u, State& s) {
    s("is_open", J(u.is_open));
    s("sel", J(u.sel));
    s("top", J(u.top));
    std::vector<Json> rows;
    for (const char* r : u.rows) rows.push_back(J(r));
    s("rows", J(std::move(rows)));
    s("sub", J(const_cast<menu::Menu&>(u).sub()));   // formats into the menu's own buffer
  };
  p.set = [](menu::Menu& u, const std::string& f, const Json& v) {
    if (f == "sel") return u.sel = (int)v.in(), true;
    if (f == "top") return u.top = (int)v.in(), true;
    if (f == "is_open") return u.is_open = v.flag(), true;
    return false;
  };
  CHECK_REPLAY(p);
}

TEST(trace_blank_window) {
  using haptic_patterns::BlankWindow;
  Port<BlankWindow> p;
  p.cls = "finder.haptic_patterns.BlankWindow";
  p.make = [](const Json&, Calls&) { return std::make_unique<BlankWindow>(); };
  p.call = [](BlankWindow& b, const std::string& m, const Json& a) -> Json {
    if (m == "extend") return b.extend(tick(a[0]), (int32_t)a[1].in()), J();
    if (m == "active") return J(b.active(tick(a[0])));
    if (m == "expire") return b.expire(tick(a[0])), J();
    if (m == "reset") return b.reset(), J();
    unported(m);
  };
  p.state = [](const BlankWindow& b, State& s) {
    s("t0", J(b.t0));
    s("until", J(b.until));
  };
  CHECK_REPLAY(p);
}

static haptic_patterns::Haptic haptic(const Json& v) {
  return v.null() ? haptic_patterns::NONE : haptic_patterns::from_name(v.str().c_str());
}

TEST(trace_haptic_player) {
  using haptic_patterns::HapticPlayer;
  Port<HapticPlayer> p;
  p.cls = "finder.haptic_patterns.HapticPlayer";
  p.make = [](const Json& a, Calls&) { return std::make_unique<HapticPlayer>((int)a[0].in()); };
  p.call = [](HapticPlayer& h, const std::string& m, const Json& a) -> Json {
    if (m == "tick") return J(h.tick(tick(a[0])));
    if (m == "play_named") return J(h.play_named(haptic(a[0]), opt_tick(a[1])));
    if (m == "heartbeat") return J(h.heartbeat(haptic(a[0]), opt_tick(a[1])));
    if (m == "cancel_heartbeat") return J(h.cancel_heartbeat());
    if (m == "hb_allowed") return J(h.hb_allowed(opt_tick(a[0])));
    if (m == "set_metronome") return h.set_metronome((int32_t)a[0].in(), opt_tick(a[1]), haptic(a[2])), J();
    if (m == "set_mode") return h.set_mode((int)a[0].in()), J();
    unported(m);
  };
  p.state = [](const HapticPlayer& h, State& s) {
    s("mode", J(h.mode));
    s("busy", J(h.busy()));
    s("active", J(h.active()));
    s("beat_due", J(h.beat_due()));
    s("beat_playing", J(h.beat_playing()));
    s("period_ms", J(h.period_ms()));
  };
  CHECK_REPLAY(p);
}
