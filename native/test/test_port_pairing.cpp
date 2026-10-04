// finder/pairing.py against its Python traces (Calibrator, Pairing), and its
// module functions against tests/test_game.py's checks.
#include "check.h"
#include "hm/pairing.h"
#include "port.h"

using namespace hm;
using namespace hmt;
namespace P = hm::pairing;

TEST(pairing_fnv1a32_and_runes) {
  const uint8_t a[] = {'a'};
  CHECK(P::fnv1a32(a, 0) == 0x811C9DC5u);
  CHECK(P::fnv1a32(a, 1) == 0xE40C292Cu);   // published FNV-1a test vector
  const Mac ma = {{0x24, 0x0a, 0xc4, 0x10, 0x00, 0x0a}};
  const Mac mb = {{0x24, 0x0a, 0xc4, 0x10, 0x00, 0x0b}};
  const P::Runes r = P::rune_ids(ma, mb);
  CHECK(r == P::rune_ids(mb, ma));
  CHECK(r[0] == 2 && r[1] == 0 && r[2] == 3);   // rune_ids(MAC_A, MAC_B) in Python
}

static void calibrator_state(const P::Calibrator& c, State& s) {
  s("nominal", J(c.nominal));
  s("clamp_db", J(c.clamp_db));
  s("window_ms", J(c.window_ms));
  s("gate_ms", J(c.gate_ms));
  s("sd_max", J(c.sd_max));
  s("skip_ms", J(c.skip_ms));
  s("t0", J(c.t0));
  s("fill_ms", J(c.fill_ms));
  s("sum", J(c.sum));
  s("count", J(c.count));
  s("stable", J(c.stable));
  s("sd", J(c.sd));
  s("done", J(c.done));
  s("skipped", J(c.skipped));
  s("p1m", J(c.p1m));
  s("digit", J(c.digit()));
}

TEST(trace_calibrator) {
  Port<P::Calibrator> p;
  p.cls = "finder.pairing.Calibrator";
  p.make = [](const Json& a, Calls&) {
    if (a[6].in() > P::Calibrator::RING) throw Mismatch("ring longer than Calibrator::RING");
    return std::make_unique<P::Calibrator>(a[0].num(), a[1].num(), (int32_t)a[2].in(), (int32_t)a[3].in(),
                                           a[4].num(), (int32_t)a[5].in(), (int)a[6].in());
  };
  p.call = [](P::Calibrator& c, const std::string& m, const Json& a) -> Json {
    if (m == "reset") return c.reset(tick(a[0])), J();
    if (m == "add") return c.add(tick(a[0]), a[1].num()), J();
    if (m == "update") return J(c.update(tick(a[0])));
    unported(m);
  };
  p.state = calibrator_state;
  CHECK_REPLAY(p);
}

TEST(trace_pairing) {
  Port<P::Pairing> p;
  p.cls = "finder.pairing.Pairing";
  p.make = [](const Json& a, Calls&) {
    return std::make_unique<P::Pairing>(a[0].null() ? std::optional<Mac>() : std::optional<Mac>(mac(a[0])),
                                        a[1].num());
  };
  p.call = [](P::Pairing& o, const std::string& m, const Json& a) -> Json {
    if (m == "reset") return o.reset(tick(a[0])), J();
    if (m == "start_split") return o.start_split(tick(a[0])), J();
    if (m == "set_ready") return o.set_ready(tick(a[0])), J();
    if (m == "set_peer_ready") return o.set_peer_ready(tick(a[0]), a[1].flag()), J();
    if (m == "on_candidate") return J(o.on_candidate(tick(a[0]), mac(a[1]), a[2].num(), a[3].flag()));
    if (m == "on_rssi") return o.on_rssi(tick(a[0]), a[1].num()), J();
    if (m == "confirm") return J(o.confirm(tick(a[0])));
    if (m == "bump") return J(o.bump(tick(a[0])));
    if (m == "set_peer_confirmed") return o.set_peer_confirmed(tick(a[0]), a[1].flag()), J();
    if (m == "update") {
      const char* h = haptic_patterns::name(o.update(tick(a[0])));
      return h ? J(h) : J();
    }
    unported(m);
  };
  p.state = [](const P::Pairing& o, State& s) {
    s("my_mac", o.my_mac ? J(*o.my_mac) : J());
    s("nominal", J(o.nominal));
    s("split_s", J(o.split_s));
    s("p1m", J(o.p1m));
    s("sub", J(P::name(o.sub)));
    s("t_sub", J(o.t_sub));
    s("peer_mac", o.peer_mac ? J(*o.peer_mac) : J());
    s("runes", o.runes ? Jarr(o.runes->data(), o.runes->size()) : J());
    s("confirmed", J(o.confirmed));
    s("peer_confirmed", J(o.peer_confirmed));
    s("countdown", J(o.countdown));
    s("ready", J(o.ready));
    s("peer_ready", J(o.peer_ready));
    s("t_split", J(o.t_split));
    s("toast", o.toast ? J(o.toast) : J());
    s("unstable", J(o.unstable));
    s("go", J(o.go()));
  };
  p.set = [](P::Pairing& o, const std::string& f, const Json& v) {
    if (f == "split_s") return o.split_s = (int32_t)v.in(), true;
    return false;
  };
  CHECK_REPLAY(p);
}
