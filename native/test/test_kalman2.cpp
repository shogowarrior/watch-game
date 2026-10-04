// hm::est::Kalman2 against finder/estimators/kalman2.py (native/test/golden/kalman2.txt):
// every public field after every update, compared exactly.
#include <stdio.h>
#include <string.h>

#include <optional>
#include <string>

#include "check.h"
#include "golden.h"
#include "hm/kalman2.h"

using hmt::flt;
using hmt::near;
using hmt::none;
using hmt::num;

namespace {

std::optional<double> opt(const std::string& s) { return none(s) ? std::nullopt : std::optional<double>(flt(s)); }
bool same(const std::string& want, std::optional<double> got) { return none(want) ? !got : got && near(want, *got); }

// "<act> <hz> <steps>" at t[i], or "n n n": false (None)
bool motion(const hmt::Tokens& t, size_t i, hm::MotionInfo* m) {
  if (none(t[i])) return false;
  m->activity = (int32_t)num(t[i]);
  m->step_rate_hz = flt(t[i + 1]);
  m->steps = (uint32_t)num(t[i + 2]);
  return true;
}

}  // namespace

TEST(test_kalman2_matches_python_update_by_update) {
  hm::est::Kalman2 e;
  int ups = 0, idle = 0, peer = 0, cmds = 0, moving = 0, trends[3] = {0, 0, 0};
  for (const hmt::Tokens& t : hmt::golden("kalman2")) {
    if (t[0] == "new") {
      e = hm::est::Kalman2();
    } else if (t[0] == "exp") {
      e.set_exponent(flt(t[1]));
    } else if (t[0] == "cal") {
      e.calibrate(flt(t[1]));
    } else if (t[0] == "reset") {
      e.reset();
    }
    if (t[0] != "up") {
      cmds++;
      continue;
    }
    hm::MotionInfo me, pm;
    const bool has_me = motion(t, 4, &me), has_pm = motion(t, 7, &pm);
    e.update((hm::ticks_t)num(t[1]), opt(t[2]), opt(t[3]), has_me ? &me : nullptr, has_pm ? &pm : nullptr);
    const bool ok = same(t[11], e.rssi_f) && near(t[12], e.rssi_var) && near(t[13], e.rate_db_s) &&
                    same(t[14], e.dist_m) && same(t[15], e.dist_lo_m) && same(t[16], e.dist_hi_m) &&
                    num(t[17]) == e.trend && near(t[18], e.trend_conf) && same(t[19], e.noise_db) &&
                    near(t[20], e.speed) && near(t[21], e.sig) && near(t[22], e.vmax) && near(t[23], e.peer_bias) &&
                    num(t[24]) == e.peer_n && same(t[25], e.bias) && near(t[26], e.pl.p0);
    if (!ok) printf("  kalman2: update at t=%s differs (rssi_f %.17g dist %.17g)\n", t[1].c_str(),
                    e.rssi_f ? *e.rssi_f : NAN, e.dist_m ? *e.dist_m : NAN);
    CHECK(ok);
    ups++;
    idle += none(t[2]);
    peer += !none(t[3]);
    moving += e.speed > 0.0;
    trends[e.trend + 1]++;
  }
  // the vectors cover idle ticks, partner reports, moving and still, and every trend
  CHECK(ups == 424 && cmds == 15);
  CHECK(idle > 50 && peer > 100 && moving > 100 && ups - moving > 50);
  CHECK(trends[0] > 20 && trends[1] > 20 && trends[2] > 20);
}

TEST(test_kalman2_motion_hint_speed) {
  // tests/test_est_kalman2.py test_motion_hint_speed: still with slow creep, then a 0.5 m/s floor
  hm::est::kalman2::Mot m;
  hm::MotionInfo i{hm::ACT_STILL, 0.5, 0};
  for (uint32_t k = 0; k < 10; k++) {
    i.steps = k;
    m.feed(1000 * k, &i);
  }
  CHECK(m.v == 0.0);
  hm::est::kalman2::Mot u;
  hm::MotionInfo j{hm::ACT_UNKNOWN, 0.3, 0};
  for (uint32_t k = 0; k < 10; k++) {
    j.steps = k;
    u.feed(1000 * k, &j);
  }
  CHECK(u.v == 0.5);
  const hm::MotionInfo w{hm::ACT_WALK, 1.8, 11};
  u.feed(10000, &w);
  CHECK(u.v == 1.8 * hm::est::kalman2::STRIDE_M);
  u.feed(10100, nullptr);
  CHECK(u.v == hm::est::kalman2::V_UNKNOWN);
}

TEST(test_kalman2_is_a_range_estimator) {
  hm::est::Kalman2 k;
  hm::est::RangeEstimator& e = k;
  CHECK(strcmp(e.name(), "kalman2") == 0 && e.pl.n == hm::T::PATH_LOSS_N && k.cal == hm::T::P1M_NOMINAL_DBM);
  e.update(0, std::nullopt);
  CHECK(!e.dist_m && e.trend == 0 && !e.noise_db);
  e.update(100, -60.0);
  CHECK(e.dist_m && e.dist_lo_m && e.dist_hi_m && e.rssi_f && e.noise_db);
  CHECK(*e.dist_lo_m <= *e.dist_m && *e.dist_m <= *e.dist_hi_m);
}
