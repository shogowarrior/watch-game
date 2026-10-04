// finder/motion.py and finder/gestures.py against their Python traces.
#include "check.h"
#include "hm/gestures.h"
#include "hm/motion.h"
#include "port.h"

using namespace hm;
using namespace hmt;

TEST(trace_motion_tracker) {
  Port<motion::MotionTracker> p;
  p.cls = "finder.motion.MotionTracker";
  p.make = [](const Json& a, Calls&) {
    return std::make_unique<motion::MotionTracker>(a[0].num(), (int32_t)a[1].in(), a[2].num(), (int)a[3].num());
  };
  p.call = [](motion::MotionTracker& t, const std::string& m, const Json& a) -> Json {
    if (m == "add_sample") return J(t.add_sample(tick(a[0]), a[1].num(), a[2].num(), a[3].num()));
    if (m == "set_chip") return t.set_chip(tick(a[0]), opt_i32(a[1]), opt_i32(a[2])), J();
    if (m == "reset") return t.reset(), J();
    unported(m);
  };
  p.state = [](const motion::MotionTracker& t, State& s) {
    s("stride_m", J(t.stride_m));
    s("run_stride_k", J(t.run_stride_k));
    s("z_sign", J(t.z_sign));
    s("steps", J(t.steps));
    s("sw_steps", J(t.sw_steps));
    s("step_rate_hz", J(t.step_rate_hz));
    s("activity", J(t.activity));
    s("is_still", J(t.is_still));
    s("mag_sd_g", J(t.mag_sd_g));
    s("gx", J(t.gx));
    s("gy", J(t.gy));
    s("gz", J(t.gz));
    s("face_up", J(t.face_up));
    s("dist_m", J(t.dist_m));
    s("chip_live", J(t.chip_live));
    s("tilt_deg", J(t.tilt_deg()));
    s("has_gravity", J(t.has_gravity()));
    s("roll_deg", J(t.roll_deg()));
    s("pitch_deg", J(t.pitch_deg()));
  };
  CHECK_REPLAY(p);
}

TEST(trace_gesture_recognizer) {
  Port<gestures::GestureRecognizer> p;
  p.cls = "finder.gestures.GestureRecognizer";
  p.make = [](const Json& a, Calls&) {
    return std::make_unique<gestures::GestureRecognizer>((int32_t)a[0].in(), (int32_t)a[1].in(), (int32_t)a[2].in(),
                                                         (int32_t)a[3].in(), (int32_t)a[4].in(), (int32_t)a[5].in());
  };
  p.call = [](gestures::GestureRecognizer& g, const std::string& m, const Json& a) -> Json {
    if (m == "update")
      return J(g.update(tick(a[0]), a[1].flag(), (int32_t)a[2].in(), (int32_t)a[3].in(), a[4].flag()));
    if (m == "reset") return g.reset(), J();
    unported(m);
  };
  p.state = [](const gestures::GestureRecognizer& g, State& s) {
    s("long_ms", J(g.long_ms));
    s("swipe_px", J(g.swipe_px));
    s("debounce_ms", J(g.debounce_ms));
    s("slop_px", J(g.slop_px));
    s("tap_min_ms", J(g.tap_min_ms));
    s("tap_max_ms", J(g.tap_max_ms));
    s("down", J(g.down));
    s("began", J(g.began));
    s("x", J(g.x));
    s("y", J(g.y));
    s("ev_x", J(g.ev_x));
    s("ev_y", J(g.ev_y));
    s("ev_t", J(g.ev_t));
  };
  CHECK_REPLAY(p);
}
