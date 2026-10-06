// finder/scan.py against its Python traces (test_scan; test_episode too when
// run.py --tests records it).
#include <string.h>

#include "check.h"
#include "hm/scan.h"
#include "port.h"

using namespace hm;
using namespace hmt;

// The recorded blank_fn: answers each call as the Python's did.
static bool recorded_blank_fn(void* calls, ticks_t t) {
  const Json r = static_cast<Calls*>(calls)->take("blank_fn", J(std::vector<Json>{J(t)}));
  return !r.null() && r.flag();
}

// phi, val and src as state shows them (CRC-32s). Recomputing the CRCs on
// every trace line is most of the replay's time, so the last ones are kept
// until the arrays' bytes change.
static void sample_state(const scan::ScanSession& o, State& s) {
  static struct {
    float phi[scan::CAP], val[scan::CAP];
    uint8_t src[scan::CAP];
    Json jphi, jval, jsrc;
    bool ok = false;
  } last;
  if (!last.ok || memcmp(last.phi, o.phi, sizeof o.phi) || memcmp(last.val, o.val, sizeof o.val) ||
      memcmp(last.src, o.src, sizeof o.src)) {
    memcpy(last.phi, o.phi, sizeof o.phi);
    memcpy(last.val, o.val, sizeof o.val);
    memcpy(last.src, o.src, sizeof o.src);
    last.jphi = Jarray('f', o.phi, scan::CAP, sizeof(float));
    last.jval = Jarray('f', o.val, scan::CAP, sizeof(float));
    last.jsrc = Jarray('B', o.src, scan::CAP, 1);
    last.ok = true;
  }
  s("phi", last.jphi);
  s("val", last.jval);
  s("src", last.jsrc);
}

// A test's calls of private methods.
namespace hm {
namespace scan {
struct Probe {
  static void start_sweep(ScanSession& o, ticks_t t) { o.start_sweep_(t); }
};
}  // namespace scan
}  // namespace hm

TEST(trace_scan_session) {
  Port<scan::ScanSession> p;
  p.cls = "finder.scan.ScanSession";
  p.make = [](const Json& a, Calls& calls) {
    if (a[1].null()) return std::make_unique<scan::ScanSession>(tick(a[0]));
    return std::make_unique<scan::ScanSession>(tick(a[0]), recorded_blank_fn, &calls);
  };
  p.call = [](scan::ScanSession& o, const std::string& m, const Json& a) -> Json {
    if (m == "reset") return o.reset(tick(a[0])), J();
    if (m == "blanked") return J(o.blanked(tick(a[0])));
    if (m == "on_motion") return o.on_motion(tick(a[0]), a[1].opt_num(), opt_i32(a[2]), opt_i32(a[3])), J();
    if (m == "on_packet") return o.on_packet(tick(a[0]), a[1].opt_num(), a[2].opt_num()), J();
    if (m == "on_peer") return o.on_peer(tick(a[0]), !a[1].null() && a[1].flag()), J();
    if (m == "cancel") return J(o.cancel(tick(a[0])));
    if (m == "update") return o.update(tick(a[0])), J();
    if (m == "pop_haptic") return J(haptic_patterns::name(o.pop_haptic()));
    if (m == "sweep") {
      const std::optional<scan::Sweep> sw = o.sweep(opt_tick(a[0]));
      if (!sw) return J();
      return J(std::vector<Json>{J(sw->wedge_deg), Jarr(sw->bins, scan::BINS), J(sw->active_bin), J(sw->paused)});
    }
    if (m == "done") return J(o.done(tick(a[0])));
    if (m == "_start_sweep") return scan::Probe::start_sweep(o, tick(a[0])), J();
    unported(m);
  };
  p.state = [](const scan::ScanSession& o, State& s) {
    if (!o.blank_fn) s("blank_fn", J());   // a Python function is not state; None is
    sample_state(o, s);
    s("bins", Jarr(o.bins, scan::BINS));
    s("phase", J(scan::name(o.phase)));
    s("n", J(o.n));
    s("activity", J(o.activity));
    s("steps", J(o.steps));
    s("flat", J(o.flat));
    s("countdown", J(o.countdown));
    s("active_ms", J(o.active_ms));
    s("pause_ms", J(o.pause_ms));
    s("wedge_deg", J(o.wedge_deg));
    s("paused", J(o.paused));
    s("fault", o.fault ? J(scan::name(*o.fault)) : J());
    s("peer_walk_ms", J(o.peer_walk_ms));
    s("t_sweep", J(o.t_sweep));
    s("t_result", J(o.t_result));
    s("reason", o.reason ? J(scan::name(*o.reason)) : J());
    s("theta_deg", J(o.theta_deg));
    s("s0_deg", J(o.s0_deg));
    s("sigma_fit_deg", J(o.sigma_fit_deg));
    s("a1_db", J(o.a1_db));
    s("best_bin", J(o.best_bin));
    s("sub", J(scan::name(o.sub())));
    s("active", J(o.active()));
    s("mirror", J(o.mirror()));
    s("top_text", J(o.top_text()));
    s("word", J(o.word()));
    s("toast", J(o.toast()));
    const std::optional<scan::Result> r = o.result();
    s("result", r ? J(std::vector<Json>{J(r->theta_deg), J(r->s0_deg)}) : J());
    s("active_bin", J(o.active_bin()));
  };
  CHECK_REPLAY(p);
}
