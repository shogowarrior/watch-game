// hm::motion by hand; native/test/test_port_motion.cpp replays its Python traces.
#include <math.h>

#include "check.h"
#include "hm/motion.h"

TEST(test_motion_constants_are_pythons) {
  CHECK(hm::motion::FACE_ON_COS == cos(20.0 * M_PI / 180.0));
  CHECK(hm::motion::FACE_OFF_COS == cos(30.0 * M_PI / 180.0));
  CHECK(hm::motion::R2D == 180.0 / M_PI);
}

TEST(test_motion_window_is_one_second_and_at_least_8) {
  // the window length follows rate_hz as in Python (max(int(rate_hz), 8)), capped by the fixed array
  hm::motion::MotionTracker slow(0.7, 5), fast(0.7, 400);
  hm::ticks_t t = 0;
  for (int i = 0; i < 7; i++, t += 20) slow.add_sample(t, 0.0, 0.0, 1.0);
  CHECK(!slow.is_still);   // 7 of 8 samples
  slow.add_sample(t, 0.0, 0.0, 1.0);
  CHECK(slow.is_still);
  for (int i = 0; i < hm::motion::BUF_MAX - 1; i++, t += 2) fast.add_sample(t, 0.0, 0.0, 1.0);
  CHECK(!fast.is_still);
  fast.add_sample(t, 0.0, 0.0, 1.0);
  CHECK(fast.is_still);
}
