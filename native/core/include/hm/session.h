// finder/session.py: game helpers: own motion snapshot, partner view, live
// mirror, beacon state byte.
//
// The beacon state byte (proto.h byte 12; bump proto::VERSION if this layout
// changes):
//
//   bits 0-3  screen code = index in T::SCREENS (MENU is never sent: the
//             screen under the menu is); SC_PAIRED in PAIRING calibrate and
//             split, SC_BYE while shutting down
//   bit 4     fallback bump press armed (short press in HOT, 3 s)
//   bit 5     goodbye (3 % battery shutdown)
//   bit 6     runes confirmed (PAIRING confirmed, calibrate, split)
//   bit 7     the last accepted accelerometer tap was made in HOT
//
// Bump timing: a beacon carries bump_ago_ms (ms since the sender's last
// accepted tap, at transmit time); the receiver puts it on its own clock as
// t_rx - bump_ago_ms - AIR_MS, so the offset between the two clocks cancels.
//
// Python None: a time, battery, RSSI or tilt is a std::optional.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include <optional>

#include "hm/compat.h"
#include "hm/estimator.h"
#include "hm/proto.h"
#include "hm/py.h"
#include "hm/tuning.h"

namespace hm {
namespace motion {
class MotionTracker;
}
namespace session {

// screen codes: T::SCREENS order (SCREENS is T::SCREENS)
constexpr int32_t SC_PAIRING = 0;
constexpr int32_t SC_SEARCHING = 1;
constexpr int32_t SC_FAR = 2;
constexpr int32_t SC_NEAR = 3;
constexpr int32_t SC_WARM = 4;
constexpr int32_t SC_HOT = 5;
constexpr int32_t SC_FOUND = 6;
constexpr int32_t SC_SCANNING = 7;
constexpr int32_t SC_LINK_LOST = 8;
constexpr int32_t SC_PAIRED = 9;    // PAIRING calibrate/split (beacon only, like SC_BYE): never a candidate
constexpr int32_t SC_BYE = 15;
constexpr int32_t SC_MASK = 0x0F;   // screen code bits of the state byte
constexpr int32_t ST_PRESS = 0x10;
constexpr int32_t ST_GOODBYE = 0x20;
constexpr int32_t ST_CONFIRMED = 0x40;
constexpr int32_t ST_TAP_HOT = 0x80;

constexpr int32_t AIR_MS = 2;
constexpr int32_t PEER_FRESH_MS = 1500;   // partner flags older than this are ignored
constexpr int32_t STEP_RATE_MS = 2000;    // partner cadence window
constexpr int32_t TAP_KEEP_MS = 70000;    // partner tap forgotten after this (> proto BUMP_MAX)

// Beacon screen code for a RenderParams screen name: its index in T::SCREENS
// (-1 for an unknown name, where the Python raises).
int32_t screen_code(const char* name);

// "m:ss" up to 9:59, then "10M+" (LINK_LOST timer), written into buf; returns buf.
constexpr size_t MSS_LEN = 8;   // a buffer this long always fits
const char* fmt_mss(int32_t ms, char* buf, size_t n);

// Own motion inputs for one logic tick (from motion::MotionTracker or the sim).
class MotionSnap {
 public:
  MotionInfo info;
  std::optional<double> tilt_deg;
  bool face_up = true;

  int32_t activity() const { return info.activity; }
  uint32_t steps() const { return info.steps; }
  bool walking() const { return info.activity == ACT_WALK || info.activity == ACT_RUN; }

  void set(ticks_t t_ms, int32_t activity = ACT_UNKNOWN, uint32_t steps = 0, double step_rate_hz = 0.0,
           std::optional<double> tilt_deg = std::nullopt, bool face_up = true);
  // Copy a MotionTracker (no tilt before its first sample).
  void from_tracker(ticks_t t_ms, const motion::MotionTracker& mt);
};

// Latest partner beacon fields, packet timing and derived motion.
class PeerView {
 public:
  PeerView() { reset(); }
  void reset();
  // Record one partner beacon received at t_rx.
  void on_beacon(ticks_t t_rx, const proto::Beacon& b);
  // Cap old stamps so ticks_diff never wraps: last_t ages at most stale_ms
  // (then live3 needs new packets), tap_t is dropped after TAP_KEEP_MS.
  void expire(ticks_t t_ms, int32_t stale_ms);
  // RELINK_PACKETS packets within RELINK_WINDOW_MS (relink / SEARCHING exit).
  bool live3(ticks_t t_ms) const { return rx_.full_within(t_ms, T::RELINK_WINDOW_MS); }
  std::optional<int32_t> age(ticks_t t_ms) const {
    return last_t ? std::optional<int32_t>(ticks_diff(t_ms, *last_t)) : std::nullopt;
  }
  bool fresh(ticks_t t_ms, int32_t ms = PEER_FRESH_MS) const { return last_t && ticks_diff(t_ms, *last_t) <= ms; }

  int32_t screen() const { return state & SC_MASK; }
  bool sweeping() const { return flags & proto::F_SWEEP; }
  bool walking() const { return (flags & proto::F_WALK) || motion.activity == ACT_WALK || motion.activity == ACT_RUN; }
  bool pressed() const { return state & ST_PRESS; }
  // Tapped READY in its split countdown (it shows PAIRED).
  bool ready() const { return (flags & proto::F_READY) && (state & SC_MASK) == SC_PAIRED; }
  bool confirmed() const { return state & ST_CONFIRMED; }
  bool tap_hot() const { return state & ST_TAP_HOT; }

  MotionInfo motion;
  opt_ticks last_t;
  int32_t state;
  int32_t flags;
  std::optional<int32_t> battery;
  std::optional<double> rssi_last;   // partner's raw RSSI of us
  opt_ticks tap_t;                   // partner's last accepted tap, on our clock
  bool goodbye;

 private:
  TickRing<T::RELINK_PACKETS> rx_;
  std::optional<int32_t> taps_;
  std::optional<int32_t> st0_;   // step count at the start of the cadence window
  int32_t sl_;                   // last step count
  opt_ticks st_t_;               // start of the cadence window
};

// Live signal mirror (ui-spec §5.7): 150 ms EMA of raw RSSI, min/max since start.
class LiveMirror {
 public:
  LiveMirror() { reset(0); }
  void reset(ticks_t t_ms);
  void add(ticks_t t_ms, double rssi);

  std::optional<double> ema;
  double lo, hi;
  std::optional<double> value;   // 0..1, None before a packet

 private:
  ticks_t t_;
};

}  // namespace session
}  // namespace hm
