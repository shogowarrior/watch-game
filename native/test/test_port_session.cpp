// finder/session.py against its Python traces (MotionSnap and PeerView come
// from test_game, LiveMirror also from test_scan and the scan sessions).
#include <string.h>

#include "check.h"
#include "hm/motion.h"
#include "hm/session.h"
#include "port.h"

using namespace hm;
using namespace hmt;

static Json motion_info(const MotionInfo& m) {
  return Jobj("MotionInfo", {{"activity", J(m.activity)}, {"step_rate_hz", J(m.step_rate_hz)}, {"steps", J(m.steps)}});
}

// A proto.Beacon argument, from its fields.
static proto::Beacon beacon(const Json& v) {
  proto::Beacon b;
  b.game_id = (int32_t)v["game_id"].in();
  b.seq = (int32_t)v["seq"].in();
  b.rssi_last = v["rssi_last"].opt_num();
  b.rssi_filt = v["rssi_filt"].opt_num();
  b.steps = (int32_t)v["steps"].in();
  b.activity = (int32_t)v["activity"].in();
  b.battery = (int32_t)v["battery"].in();
  b.state = (int32_t)v["state"].in();
  b.flags = (int32_t)v["flags"].in();
  b.bump_ago_ms = (int32_t)v["bump_ago_ms"].in();
  return b;
}

TEST(session_helpers) {
  char buf[session::MSS_LEN];
  CHECK(strcmp(session::fmt_mss(12000, buf, sizeof buf), "0:12") == 0);
  CHECK(strcmp(session::fmt_mss(87000, buf, sizeof buf), "1:27") == 0);
  CHECK(strcmp(session::fmt_mss(599999, buf, sizeof buf), "9:59") == 0);
  CHECK(strcmp(session::fmt_mss(600000, buf, sizeof buf), "10M+") == 0);
  CHECK(strcmp(session::fmt_mss(-5, buf, sizeof buf), "0:00") == 0);
  CHECK(session::screen_code("PAIRING") == session::SC_PAIRING && session::screen_code("HOT") == session::SC_HOT);
  CHECK(session::screen_code("LINK_LOST") == session::SC_LINK_LOST && session::screen_code("BYE") == -1);
}

// No Python test calls from_tracker (Game.set_tracker), so no trace has it.
TEST(session_from_tracker) {
  motion::MotionTracker mt;
  session::MotionSnap ms;
  ms.from_tracker(0, mt);
  CHECK(!ms.tilt_deg && ms.face_up == mt.face_up && ms.activity() == mt.activity);
  mt.add_sample(20, 0.0, 0.5, 0.8);
  mt.set_chip(20, 7, 1);
  ms.from_tracker(20, mt);
  CHECK(ms.tilt_deg && *ms.tilt_deg == mt.tilt_deg() && ms.steps() == mt.steps);
  CHECK(ms.info.step_rate_hz == mt.step_rate_hz && ms.activity() == mt.activity);
}

TEST(trace_motion_snap) {
  Port<session::MotionSnap> p;
  p.cls = "finder.session.MotionSnap";
  p.make = [](const Json&, Calls&) { return std::make_unique<session::MotionSnap>(); };
  p.call = [](session::MotionSnap& m, const std::string& name, const Json& a) -> Json {
    if (name == "set")
      return m.set(tick(a[0]), (int32_t)a[1].in(), (uint32_t)a[2].in(), a[3].num(), a[4].opt_num(), a[5].flag()), J();
    unported(name);
  };
  p.state = [](const session::MotionSnap& m, State& s) {
    s("info", motion_info(m.info));
    s("tilt_deg", J(m.tilt_deg));
    s("face_up", J(m.face_up));
    s("activity", J(m.activity()));
    s("steps", J(m.steps()));
    s("walking", J(m.walking()));
  };
  CHECK_REPLAY(p);
}

TEST(trace_peer_view) {
  Port<session::PeerView> p;
  p.cls = "finder.session.PeerView";
  p.make = [](const Json&, Calls&) { return std::make_unique<session::PeerView>(); };
  p.call = [](session::PeerView& v, const std::string& m, const Json& a) -> Json {
    if (m == "reset") return v.reset(), J();
    if (m == "on_beacon") return v.on_beacon(tick(a[0]), beacon(a[1])), J();
    if (m == "expire") return v.expire(tick(a[0]), (int32_t)a[1].in()), J();
    if (m == "live3") return J(v.live3(tick(a[0])));
    if (m == "age") return J(v.age(tick(a[0])));
    if (m == "fresh") return J(v.fresh(tick(a[0]), (int32_t)a[1].in()));
    unported(m);
  };
  p.state = [](const session::PeerView& v, State& s) {
    s("motion", motion_info(v.motion));
    s("last_t", J(v.last_t));
    s("state", J(v.state));
    s("flags", J(v.flags));
    s("battery", J(v.battery));
    s("rssi_last", J(v.rssi_last));
    s("tap_t", J(v.tap_t));
    s("goodbye", J(v.goodbye));
    s("screen", J(v.screen()));
    s("sweeping", J(v.sweeping()));
    s("walking", J(v.walking()));
    s("pressed", J(v.pressed()));
    s("ready", J(v.ready()));
    s("confirmed", J(v.confirmed()));
    s("tap_hot", J(v.tap_hot()));
  };
  CHECK_REPLAY(p);
}

TEST(trace_live_mirror) {
  Port<session::LiveMirror> p;
  p.cls = "finder.session.LiveMirror";
  p.make = [](const Json&, Calls&) { return std::make_unique<session::LiveMirror>(); };
  p.call = [](session::LiveMirror& l, const std::string& m, const Json& a) -> Json {
    if (m == "reset") return l.reset(tick(a[0])), J();
    if (m == "add") return l.add(tick(a[0]), a[1].num()), J();
    unported(m);
  };
  p.state = [](const session::LiveMirror& l, State& s) {
    s("ema", J(l.ema));
    s("lo", J(l.lo));
    s("hi", J(l.hi));
    s("value", J(l.value));
  };
  CHECK_REPLAY(p);
}
