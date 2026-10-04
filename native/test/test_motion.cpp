// hm::motion::MotionTracker against finder/motion.py (native/test/golden/motion.txt):
// every output after each batch of samples and each chip read, compared exactly.
#include <math.h>
#include <stdio.h>

#include <optional>
#include <string>

#include "check.h"
#include "golden.h"
#include "hm/motion.h"

using hmt::flt;
using hmt::near;
using hmt::none;
using hmt::num;

namespace {

// steps sw_steps step_rate_hz activity is_still mag_sd_g gx gy gz face_up dist_m chip_live
// tilt_deg roll_deg pitch_deg has_gravity, from t[i]
bool state_is(const hmt::Tokens& t, size_t i, const hm::motion::MotionTracker& m) {
  return num(t[i]) == (long)m.steps && num(t[i + 1]) == (long)m.sw_steps && near(t[i + 2], m.step_rate_hz) &&
         num(t[i + 3]) == m.activity && num(t[i + 4]) == m.is_still && near(t[i + 5], m.mag_sd_g) &&
         near(t[i + 6], m.gx) && near(t[i + 7], m.gy) && near(t[i + 8], m.gz) && num(t[i + 9]) == m.face_up &&
         near(t[i + 10], m.dist_m) && num(t[i + 11]) == m.chip_live && near(t[i + 12], m.tilt_deg()) &&
         near(t[i + 13], m.roll_deg()) && near(t[i + 14], m.pitch_deg()) && num(t[i + 15]) == m.has_gravity();
}

std::optional<int32_t> opt(const std::string& s) {
  return none(s) ? std::nullopt : std::optional<int32_t>((int32_t)num(s));
}

}  // namespace

TEST(test_motion_tracker_matches_python) {
  hm::motion::MotionTracker m;
  double lsb = 0.0;
  int batches = 0, samples = 0, chips = 0, cmds = 0, tilts = 0;
  long stepped = 0;
  bool acts[4] = {false, false, false, false}, ran = false;
  for (const hmt::Tokens& t : hmt::golden("motion")) {
    if (t[0] == "lsb") {
      lsb = flt(t[1]);
    } else if (t[0] == "tilt") {
      CHECK(near(t[6], hm::motion::tilt_from_gravity(flt(t[1]), flt(t[2]), flt(t[3]), (double)num(t[4]))));
      tilts++;
    } else if (t[0] == "new" || t[0] == "reset") {
      if (t[0] == "new") {
        m = hm::motion::MotionTracker(flt(t[1]), (int32_t)num(t[2]), flt(t[3]), (int)num(t[4]));
      } else {
        m.reset();
      }
      CHECK(state_is(t, t[0] == "new" ? 6 : 2, m));
      cmds++;
    } else if (t[0] == "c") {
      m.set_chip((hm::ticks_t)num(t[1]), opt(t[2]), opt(t[3]));
      CHECK(state_is(t, 5, m));
      chips++;
    } else if (t[0] == "b") {
      const long n = num(t[1]);
      long got = 0;
      for (long k = 0; k < n; k++) {
        const size_t j = 2 + 4 * k;
        got += m.add_sample((hm::ticks_t)num(t[j]), num(t[j + 1]) * lsb, num(t[j + 2]) * lsb, num(t[j + 3]) * lsb);
      }
      const size_t o = 2 + 4 * n + 1;
      const bool ok = num(t[o]) == got && state_is(t, o + 1, m);
      if (!ok) printf("  motion: batch ending t=%s differs (steps %u sd %.17g)\n", t[o - 5].c_str(), m.steps, m.mag_sd_g);
      CHECK(ok);
      batches++;
      samples += (int)n;
      stepped += got;
      acts[m.activity] = true;
      ran = ran || (m.activity == hm::ACT_RUN && !m.chip_live);
    }
  }
  CHECK(tilts == 8 && cmds == 12 && chips == 57 && batches == 169 && samples == 4045);
  CHECK(stepped > 100 && acts[0] && acts[1] && acts[2] && acts[3] && ran);
}

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
