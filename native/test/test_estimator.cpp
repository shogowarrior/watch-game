// hm::est (estimator.h) against finder/estimators/base.py (native/test/golden/estimator.txt).
#include <math.h>
#include <string.h>

#include <optional>
#include <string>

#include "check.h"
#include "golden.h"
#include "hm/estimator.h"

using hmt::flt;
using hmt::near;
using hmt::none;
using hmt::num;

namespace {

// The generator's Probe: the base's helpers, driven directly.
class Probe : public hm::est::RangeEstimator {
 public:
  void update(hm::ticks_t, std::optional<double>, std::optional<double>, const hm::MotionInfo*,
              const hm::MotionInfo*) override {}
  void step(hm::ticks_t t_ms, std::optional<double> rssi, double var) {
    if (rssi) {
      note_noise(t_ms, *rssi);
      rssi_f = *rssi;
      rssi_var = var;
    }
    set_distance_from_rssi();
  }
};

std::optional<double> opt(const std::string& s) { return none(s) ? std::nullopt : std::optional<double>(flt(s)); }
bool same(const std::string& want, std::optional<double> got) { return none(want) ? !got : got && near(want, *got); }

// noise_db jit rssi_f rssi_var dist_m dist_lo_m dist_hi_m trend trend_conf rate_db_s last_t p0 n, from t[i]
bool state_is(const hmt::Tokens& t, size_t i, const Probe& e) {
  return same(t[i], e.noise_db) && near(t[i + 1], e.jit) && same(t[i + 2], e.rssi_f) && near(t[i + 3], e.rssi_var) &&
         same(t[i + 4], e.dist_m) && same(t[i + 5], e.dist_lo_m) && same(t[i + 6], e.dist_hi_m) &&
         num(t[i + 7]) == e.trend && near(t[i + 8], e.trend_conf) && near(t[i + 9], e.rate_db_s) &&
         (none(t[i + 10]) ? !e.last_t : e.last_t && (long)*e.last_t == num(t[i + 10])) &&
         near(t[i + 11], e.pl.p0) && near(t[i + 12], e.pl.n);
}

}  // namespace

TEST(test_estimator_base_matches_python) {
  int pls = 0, steps = 0, cmds = 0;
  Probe e;
  for (const hmt::Tokens& t : hmt::golden("estimator")) {
    if (t[0] == "pl0") {
      const hm::est::PathLoss d;
      CHECK(near(t[2], d.p0) && near(t[3], d.n));
    } else if (t[0] == "pl" || t[0] == "pd") {
      const hm::est::PathLoss pl(flt(t[1]), flt(t[2]));
      CHECK(near(t[5], t[0] == "pl" ? pl.rssi_to_dist(flt(t[3])) : pl.dist_to_rssi(flt(t[3]))));
      pls++;
    } else if (t[0] == "step") {
      e.step((hm::ticks_t)num(t[1]), opt(t[2]), flt(t[3]));
      if (!state_is(t, 5, e)) printf("  estimator: step at t=%s differs\n", t[1].c_str());
      CHECK(state_is(t, 5, e));
      steps++;
    } else {
      if (t[0] == "exp") e.set_exponent(flt(t[1]));
      if (t[0] == "cal") e.calibrate(flt(t[1]));
      if (t[0] == "reset") e.reset();
      const size_t at = t[0] == "new" || t[0] == "reset" ? 2 : 3;
      CHECK(state_is(t, at, e));
      cmds++;
    }
  }
  CHECK(pls == 36 && steps == 55 && cmds == 5);
}

TEST(test_estimator_constants_are_pythons) {
  CHECK(hm::est::KDB == 10.0 / log(10.0));
  CHECK(hm::est::SD_PER_STEP == sqrt(M_PI) / 2.0);
  Probe e;
  CHECK(strcmp(e.name(), "base") == 0 && e.jit == hm::est::JIT_INIT && !e.noise_db && !e.dist_m);
}
