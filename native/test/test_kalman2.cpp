// hm::est::Kalman2 by hand; native/test/test_port_est.cpp replays its Python traces.
#include <string.h>

#include "check.h"
#include "hm/kalman2.h"

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
