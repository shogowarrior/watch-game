// Glue shared by the trace tests of the game port (test_port_*.cpp).
#pragma once
#include <vector>

#include "hm/motion.h"
#include "hm/proto.h"
#include "hm/py.h"
#include "trace.h"

namespace hmt {

inline hm::Mac mac(const Json& v) {
  const std::vector<uint8_t> b = v.bytes();
  if (b.size() != 6) throw Mismatch("not a MAC: " + dump(v));
  hm::Mac m;
  memcpy(m.b, b.data(), 6);
  return m;
}
inline Json J(const hm::Mac& m) { return Jbytes(m.b, 6); }
inline hm::ticks_t tick(const Json& v) { return (hm::ticks_t)v.in(); }
inline hm::opt_ticks opt_tick(const Json& v) {
  return v.null() ? hm::opt_ticks() : hm::opt_ticks(tick(v));
}
inline std::optional<int32_t> opt_i32(const Json& v) {
  return v.null() ? std::nullopt : std::optional<int32_t>((int32_t)v.in());
}

// An object as Python's deep encoding writes it: its class name and state.
template <class T>
Json Jobj(const char* cls, const T& o, void (*state)(const T&, State&)) {
  State s;
  state(o, s);
  return Jobj(cls, std::move(s.fields));
}

// proto::Beacon as Python shows it, and a field other code wrote (false: a property).
inline void beacon_state(const hm::proto::Beacon& b, State& s) {
  s("game_id", J(b.game_id));
  s("seq", J(b.seq));
  s("rssi_last", J(b.rssi_last));
  s("rssi_filt", J(b.rssi_filt));
  s("steps", J(b.steps));
  s("activity", J(b.activity));
  s("battery", J(b.battery));
  s("state", J(b.state));
  s("flags", J(b.flags));
  s("bump_ago_ms", J(b.bump_ago_ms));
  s("sweeping", J(b.sweeping()));
  s("walking", J(b.walking()));
  s("ready", J(b.ready()));
  s("taps", J(b.taps()));
}
inline bool beacon_set(hm::proto::Beacon& b, const std::string& f, const Json& v) {
  int32_t* ints[] = {&b.game_id, &b.seq, &b.steps, &b.activity, &b.battery, &b.state, &b.flags, &b.bump_ago_ms};
  const char* names[] = {"game_id", "seq", "steps", "activity", "battery", "state", "flags", "bump_ago_ms"};
  for (int k = 0; k < 8; k++)
    if (f == names[k]) return *ints[k] = (int32_t)v.in(), true;
  if (f == "rssi_last") return b.rssi_last = v.opt_num(), true;
  if (f == "rssi_filt") return b.rssi_filt = v.opt_num(), true;
  return false;
}
// A beacon passed as an argument, from its recorded fields.
inline hm::proto::Beacon beacon(const Json& v) {
  hm::proto::Beacon b;
  for (auto& kv : v.o)
    if (kv.first[0] != '@') beacon_set(b, kv.first, kv.second);
  return b;
}

// motion::MotionTracker as Python shows it (its own trace, and an ImuFeed's part).
inline void tracker_state(const hm::motion::MotionTracker& t, State& s) {
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
}

[[noreturn]] inline void unported(const std::string& method) { throw Mismatch("the port has no " + method + "()"); }

}  // namespace hmt
