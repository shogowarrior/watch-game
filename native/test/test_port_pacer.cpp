// app/pacer.py against its Python trace (tests/test_pacer.py, and the game
// loop's frames in tests/test_app_runtime.py).
#include "check.h"
#include "hm/pacer.h"
#include "port.h"

using namespace hm;
using namespace hmt;

// A test's calls of private methods.
namespace hm {
namespace pacer {
struct Probe {
  static void note(FramePacer& p, int32_t busy_ms) { p.note_(busy_ms); }
};
}  // namespace pacer
}  // namespace hm

TEST(trace_frame_pacer) {
  Port<pacer::FramePacer> p;
  p.cls = "app.pacer.FramePacer";
  p.make = [](const Json& a, Calls&) {
    return std::make_unique<pacer::FramePacer>(tick(a[0]), a[1].null() ? 0 : (int32_t)a[1].in());
  };
  p.call = [](pacer::FramePacer& pc, const std::string& m, const Json& a) -> Json {
    if (m == "reset") return pc.reset(tick(a[0]), a[1].null() ? 0 : (int32_t)a[1].in()), J();
    if (m == "restart") return pc.restart(tick(a[0])), J();
    if (m == "begin") return J(pc.begin(tick(a[0]), (int32_t)a[1].in(), opt_i32(a[2])));
    if (m == "shown") return pc.shown(tick(a[0])), J();
    if (m == "window") return pc.window(), J();
    if (m == "jitter_ms") return J(pc.jitter_ms());
    if (m == "_note") return pacer::Probe::note(pc, (int32_t)a[0].in()), J();
    unported(m);
  };
  p.state = [](const pacer::FramePacer& pc, State& s) {
    s("i", J(pc.i));
    s("fps", J(pc.fps));
    s("period", J(pc.period));
    s("t_next", J(pc.t_next));
    s("cost", J(pc.cost));
    s("changes", J(pc.changes));
    s("frames", J(pc.frames));
    s("missed", J(pc.missed));
    s("late_sum", J(pc.late_sum));
    s("late_max", J(pc.late_max));
    s("iv_n", J(pc.iv_n));
    s("iv_sum", J(pc.iv_sum));
    s("iv_sq", J(pc.iv_sq));
    s("iv_max", J(pc.iv_max));
  };
  CHECK_REPLAY(p);
}
