// finder/proximity.py against its Python traces.
#include "check.h"
#include "hm/proximity.h"
#include "port.h"

using namespace hm;
using namespace hm::proximity;
using namespace hmt;

static std::optional<int32_t> opt_i32(const Json& v) {
  return v.null() ? std::nullopt : std::optional<int32_t>((int32_t)v.in());
}

// A range estimator showing only what Proximity.update_est reads.
struct EstStub final : est::RangeEstimator {
  void update(ticks_t, std::optional<double>, std::optional<double>, const MotionInfo*, const MotionInfo*) override {}
};

// The estimator argument of update_est, from its recorded fields.
static void est_from(const Json& a, EstStub& e) {
  e.dist_m = a["dist_m"].opt_num();
  e.rssi_f = a["rssi_f"].opt_num();
  e.noise_db = a["noise_db"].opt_num();
  e.trend = (int32_t)a["trend"].in();
  e.trend_conf = a["trend_conf"].num();
}

TEST(trace_zone_tracker) {
  Port<ZoneTracker> p;
  p.cls = "finder.proximity.ZoneTracker";
  p.make = [](const Json&, Calls&) { return std::make_unique<ZoneTracker>(); };
  p.call = [](ZoneTracker& z, const std::string& m, const Json& a) -> Json {
    if (m == "reset") return z.reset(), J();
    if (m == "rearm") return z.rearm(), J();
    if (m == "update") return J(z.update(tick(a[0]), a[1].opt_num()));
    unported(m);
  };
  p.state = [](const ZoneTracker& z, State& s) {
    s("zone", J(z.zone));
    s("changed", J(z.changed));
  };
  CHECK_REPLAY(p);
}

TEST(trace_delivery_meter) {
  Port<DeliveryMeter> p;
  p.cls = "finder.proximity.DeliveryMeter";
  p.make = [](const Json& a, Calls&) { return std::make_unique<DeliveryMeter>(a[0].num(), (int32_t)a[1].in()); };
  p.call = [](DeliveryMeter& d, const std::string& m, const Json& a) -> Json {
    if (m == "reset") return d.reset(), J();
    if (m == "expire") return d.expire(tick(a[0])), J();
    if (m == "note") return d.note(tick(a[0])), J();
    if (m == "ratio") return J(d.ratio(tick(a[0])));
    unported(m);
  };
  p.state = [](const DeliveryMeter& d, State& s) { s("expected_hz", J(d.expected_hz)); };
  p.set = [](DeliveryMeter& d, const std::string& f, const Json& v) {
    if (f == "expected_hz") return d.expected_hz = v.num(), true;
    return false;
  };
  CHECK_REPLAY(p);
}

TEST(trace_trend_gate) {
  Port<TrendGate> p;
  p.cls = "finder.proximity.TrendGate";
  p.make = [](const Json&, Calls&) { return std::make_unique<TrendGate>(); };
  p.call = [](TrendGate& g, const std::string& m, const Json& a) -> Json {
    if (m == "reset") return g.reset(), J();
    if (m == "force_off") return g.force_off(tick(a[0])), J();
    if (m == "update")
      return J(g.update(tick(a[0]), (int32_t)a[1].in(), a[2].num(), a[3].opt_num(), a[4].opt_num(),
                        (int32_t)a[5].in(), opt_i32(a[6]), a[7].opt_num()));
    unported(m);
  };
  p.state = [](const TrendGate& g, State& s) {
    s("trend", J(g.trend));
    s("trend_strong", J(g.trend_strong));
    s("unreliable", J(g.unreliable));
    s("delta_db", J(g.delta_db));
  };
  CHECK_REPLAY(p);
}

TEST(trace_proximity) {
  Port<Proximity> p;
  p.cls = "finder.proximity.Proximity";
  p.make = [](const Json&, Calls&) { return std::make_unique<Proximity>(); };
  p.call = [](Proximity& x, const std::string& m, const Json& a) -> Json {
    if (m == "reset") return x.reset(), J();
    if (m == "rearm") return x.rearm(), J();
    if (m == "update")
      return x.update(tick(a[0]), a[1].opt_num(), a[2].opt_num(), a[3].opt_num(), (int32_t)a[4].in(), a[5].num(),
                      (int32_t)a[6].in(), a[7].opt_num()),
             J();
    if (m == "update_est") {
      EstStub e;
      est_from(a[1], e);
      return x.update_est(tick(a[0]), e, (int32_t)a[2].in(), a[3].opt_num()), J();
    }
    unported(m);
  };
  p.state = [](const Proximity& x, State& s) {   // zones and gate show as refs: their own traces check them
    s("intensity", J(x.intensity));
    s("band_idx", J(x.band_idx));
    s("zone", J(x.zone()));
    s("zone_changed", J(x.zone_changed()));
    s("band", x.band() ? J(x.band()) : J());
    s("trend", J(x.trend()));
    s("trend_strong", J(x.trend_strong()));
    s("unreliable", J(x.unreliable()));
  };
  CHECK_REPLAY(p);
}
