#include "hm/session.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hm/motion.h"

namespace hm {
namespace session {

int32_t screen_code(const char* name) {
  const int32_t n = (int32_t)(sizeof(T::SCREENS) / sizeof(T::SCREENS[0]));
  for (int32_t i = 0; i < n; i++)
    if (strcmp(T::SCREENS[i], name) == 0) return i;
  return -1;
}

const char* fmt_found(int32_t s, char* buf, size_t n) {
  if (s < 0) s = 0;
  if (s <= T::FOUND_WORD_MSS_MAX_S)
    snprintf(buf, n, "FOUND %d:%02d", (int)(s / 60), (int)(s % 60));
  else if (s <= T::FOUND_TIME_MAX_S)
    snprintf(buf, n, "FOUND%d:%02d", (int)(s / 60), (int)(s % 60));
  else
    snprintf(buf, n, "FOUND 1H+");
  return buf;
}

void MotionSnap::set(ticks_t, int32_t activity, uint32_t steps, double step_rate_hz, std::optional<double> tilt_deg,
                     bool face_up) {
  info.activity = activity;
  info.steps = steps;
  info.step_rate_hz = step_rate_hz;
  this->tilt_deg = tilt_deg;
  this->face_up = face_up;
}

void MotionSnap::from_tracker(ticks_t t_ms, const motion::MotionTracker& mt) {
  std::optional<double> tilt;
  if (mt.has_gravity()) tilt = mt.tilt_deg();
  set(t_ms, mt.activity, mt.steps, mt.step_rate_hz, tilt, mt.face_up);
}

void PeerView::reset() {
  last_t.reset();
  rx_.clear();
  state = SC_PAIRING;
  flags = 0;
  battery.reset();
  rssi_last.reset();
  tap_t.reset();
  taps_.reset();
  st0_.reset();
  sl_ = 0;
  st_t_.reset();
  goodbye = false;
  motion.activity = ACT_UNKNOWN;
  motion.steps = 0;
  motion.step_rate_hz = 0.0;
}

void PeerView::on_beacon(ticks_t t_rx, const proto::Beacon& b) {
  rx_.note(t_rx);
  last_t = t_rx;
  state = b.state;
  flags = b.flags;
  battery = b.battery == proto::BATT_UNKNOWN ? std::nullopt : std::optional<int32_t>(b.battery);
  rssi_last = b.rssi_last && *b.rssi_last == proto::RSSI_NONE ? std::nullopt : b.rssi_last;
  if (b.state & ST_GOODBYE) goodbye = true;
  const int32_t ba = b.bump_ago_ms;
  if (ba != proto::BUMP_NONE) {
    const ticks_t tt = ticks_add(t_rx, -(ba + AIR_MS));
    const int32_t taps = b.taps();
    if (!tap_t || taps != taps_ || abs(ticks_diff(tt, *tap_t)) > 100) tap_t = tt;
    taps_ = taps;
  }
  // motion: activity + cadence from the u16 step counter
  motion.activity = b.activity;
  if (!st0_) {
    st0_ = sl_ = b.steps;
    st_t_ = t_rx;
    motion.steps = 0;
  } else {
    const int32_t d = proto::steps_delta((uint16_t)b.steps, (uint16_t)sl_);
    if (d >= 0x8000) {   // the counter went back: the partner restarted
      st0_ = sl_ = b.steps;
      st_t_ = t_rx;
      motion.step_rate_hz = 0.0;
    } else {
      motion.steps += d;
      sl_ = b.steps;
      const int32_t el = ticks_diff(t_rx, *st_t_);
      if (el >= STEP_RATE_MS) {
        motion.step_rate_hz = proto::steps_delta((uint16_t)b.steps, (uint16_t)*st0_) * 1000.0 / el;
        st0_ = b.steps;
        st_t_ = t_rx;
      }
    }
  }
}

void PeerView::expire(ticks_t t_ms, int32_t stale_ms) {
  if (last_t && ticks_diff(t_ms, *last_t) > stale_ms) {
    last_t = ticks_add(t_ms, -stale_ms);
    rx_.clear();
  }
  if (st_t_ && ticks_diff(t_ms, *st_t_) > stale_ms) st_t_ = ticks_add(t_ms, -stale_ms);
  if (tap_t && ticks_diff(t_ms, *tap_t) > TAP_KEEP_MS) tap_t.reset();
}

void LiveMirror::reset(ticks_t t_ms) {
  ema.reset();
  t_ = t_ms;
  lo = 0.0;
  hi = 0.0;
  value.reset();
}

void LiveMirror::add(ticks_t t_ms, double rssi) {
  double e;
  if (!ema) {
    e = rssi;
    lo = hi = e;
  } else {
    e = *ema;
    const int32_t dt = ticks_diff(t_ms, t_);
    const double a = 1.0 - exp((double)-(dt > 0 ? dt : 0) / T::MIRROR_EMA_MS);
    e += a * (rssi - e);
    if (e < lo) lo = e;
    if (e > hi) hi = e;
  }
  t_ = t_ms;
  ema = e;
  double span = hi - lo;
  if (span < T::MIRROR_MIN_SPAN_DB) span = T::MIRROR_MIN_SPAN_DB;
  const double v = (e - lo) / span;
  value = v < 0.0 ? 0.0 : v > 1.0 ? 1.0 : v;
}

}  // namespace session
}  // namespace hm
