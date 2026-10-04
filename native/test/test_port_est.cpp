// finder/estimators/base.py and kalman2.py against their Python traces.
#include "check.h"
#include "hm/estimator.h"
#include "hm/kalman2.h"
#include "port.h"

using namespace hm;
using namespace hmt;

static MotionInfo motion_info(const Json& v) {
  MotionInfo m;
  m.activity = (int32_t)v["activity"].in();
  m.step_rate_hz = v["step_rate_hz"].num();
  m.steps = (uint32_t)v["steps"].in();
  return m;
}

TEST(trace_path_loss) {
  Port<est::PathLoss> p;
  p.cls = "finder.estimators.base.PathLoss";
  p.make = [](const Json& a, Calls&) { return std::make_unique<est::PathLoss>(a[0].num(), a[1].num()); };
  p.call = [](est::PathLoss& pl, const std::string& m, const Json& a) -> Json {
    if (m == "rssi_to_dist") return J(pl.rssi_to_dist(a[0].num()));
    if (m == "dist_to_rssi") return J(pl.dist_to_rssi(a[0].num()));
    unported(m);
  };
  p.state = [](const est::PathLoss& pl, State& s) {
    s("p0", J(pl.p0));
    s("n", J(pl.n));
  };
  p.set = [](est::PathLoss& pl, const std::string& f, const Json& v) {
    if (f == "p0") return pl.p0 = v.num(), true;
    if (f == "n") return pl.n = v.num(), true;
    return false;
  };
  CHECK_REPLAY(p);
}

TEST(trace_motion_info) {
  Port<MotionInfo> p;
  p.cls = "finder.estimators.base.MotionInfo";
  p.make = [](const Json& a, Calls&) {
    auto m = std::make_unique<MotionInfo>();
    m->activity = (int32_t)a[0].in();
    m->step_rate_hz = a[1].num();
    m->steps = (uint32_t)a[2].in();
    return m;
  };
  p.call = [](MotionInfo& m, const std::string& name, const Json& a) -> Json {
    if (name == "speed_mps") return J(m.speed_mps(a[0].num()));
    unported(name);
  };
  p.state = [](const MotionInfo& m, State& s) {
    s("activity", J(m.activity));
    s("step_rate_hz", J(m.step_rate_hz));
    s("steps", J(m.steps));
  };
  CHECK_REPLAY(p);
}

TEST(trace_kalman2) {
  Port<est::Kalman2> p;
  p.cls = "finder.estimators.kalman2.Estimator";
  p.make = [](const Json& a, Calls&) {
    if (a[0].null()) return std::make_unique<est::Kalman2>();
    return std::make_unique<est::Kalman2>(est::PathLoss(a[0]["p0"].num(), a[0]["n"].num()));
  };
  p.call = [](est::Kalman2& k, const std::string& m, const Json& a) -> Json {
    if (m == "update") {
      MotionInfo mine, peer;
      if (!a[3].null()) mine = motion_info(a[3]);
      if (!a[4].null()) peer = motion_info(a[4]);
      k.update(tick(a[0]), a[1].opt_num(), a[2].opt_num(), a[3].null() ? nullptr : &mine,
               a[4].null() ? nullptr : &peer);
      return J();
    }
    if (m == "calibrate") return k.calibrate(a[0].num()), J();
    if (m == "set_exponent") return k.set_exponent(a[0].num()), J();
    if (m == "reset") return k.reset(), J();
    unported(m);
  };
  p.state = [](const est::Kalman2& k, State& s) {
    s("rssi_f", J(k.rssi_f));
    s("rssi_var", J(k.rssi_var));
    s("noise_db", J(k.noise_db));
    s("jit", J(k.jit));
    s("rate_db_s", J(k.rate_db_s));
    s("dist_m", J(k.dist_m));
    s("dist_lo_m", J(k.dist_lo_m));
    s("dist_hi_m", J(k.dist_hi_m));
    s("trend", J(k.trend));
    s("trend_conf", J(k.trend_conf));
    s("last_t", J(k.last_t));
    s("cal", J(k.cal));
    s("bias", J(k.bias));
    s("p00", J(k.p00));
    s("p01", J(k.p01));
    s("p11", J(k.p11));
    s("sig", J(k.sig));
    s("peer_bias", J(k.peer_bias));
    s("peer_n", J(k.peer_n));
    s("speed", J(k.speed));
    s("vmax", J(k.vmax));
  };
  CHECK_REPLAY(p);
}
