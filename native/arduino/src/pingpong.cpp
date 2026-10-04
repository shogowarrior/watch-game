#include "pingpong.h"

#include <math.h>
#include <stdio.h>

#include "hm/hal.h"

namespace pingpong {

namespace {

// "-57.3": tenths as a signed decimal, without printf's float support.
const char* tenths(char* s, int64_t t) {
  const int64_t a = t < 0 ? -t : t;
  snprintf(s, 16, "%s%d.%d", t < 0 ? "-" : "", (int)(a / 10), (int)(a % 10));
  return s;
}

}  // namespace

void RssiSummary::add(int v) {
  lo = n ? (v < lo ? v : lo) : v;
  hi = n ? (v > hi ? v : hi) : v;
  n++;
  sum += v;
  sq += (int64_t)v * v;
}

void RssiSummary::log(const char* where) const {
  if (!n) return hm::logf("HM pingpong_rssi where=%s n=0", where);
  const double mean = (double)sum / n, var = (double)sq / n - mean * mean;
  char m[16], s[16];
  hm::logf("HM pingpong_rssi where=%s n=%d min=%d mean=%s max=%d sd=%s", where, n, lo,
           tenths(m, llround(mean * 10)), hi, tenths(s, llround(sqrt(var > 0 ? var : 0) * 10)));
}

Pinger::Pinger(int n, int period_ms, int tail_ms)
    : n_(n < MAX_N ? n : MAX_N), period_(period_ms), tail_(tail_ms) {
  ping_.game_id = GAME_ID;   // the rest as radio_pingpong.Pinger: seq and the last pong's RSSI change
  ping_.state = KIND_PING;
  ping_.rssi_last = hm::proto::RSSI_NONE;
}

bool Pinger::due(hm::ticks_t now, uint8_t* out) {
  if (sent >= n_ || (sent && hm::ticks_diff(now, next_) < 0)) return false;
  // Next target from the last one (a fixed grid), from now after a stall: finder.link.TxScheduler.
  next_ = hm::ticks_add(sent && hm::ticks_diff(now, next_) < period_ ? next_ : now, period_);
  ping_.seq = (uint16_t)sent;
  ping_.pack(out);
  t_sent_[sent++] = now;
  last_send_ = now;
  return true;
}

void Pinger::on_rx(const uint8_t* d, size_t len, int8_t r, hm::ticks_t t) {
  const int s = hm::proto::valid(d, len, GAME_ID) && d[12] == KIND_PONG ? hm::proto::seq_of(d) : -1;
  if (s < 0 || s >= sent || got_[s]) {
    stray++;
    return;
  }
  got_[s] = 1;
  pongs++;
  rtt.add((uint32_t)hm::ticks_diff(t, t_sent_[s]));
  if (any_rx_) gaps.add((uint32_t)hm::ticks_diff(t, last_rx_));
  any_rx_ = true;
  last_rx_ = t;
  ping_.rssi_last = r;
  rssi.add(r);
  pong_.unpack(d);
  if (pong_.rssi_last != hm::proto::RSSI_NONE) peer_rssi.add((int)pong_.rssi_last);
}

bool Pinger::done(hm::ticks_t now) const {
  return sent >= n_ && (pongs >= n_ || hm::ticks_diff(now, last_send_) >= tail_);
}

void Pinger::report(int channel) const {
  char pct[16];
  hm::logf("HM pingpong role=ping ch=%d sent=%d pongs=%d delivery_pct=%s stray=%d", channel, sent, pongs,
           tenths(pct, sent ? (int64_t)pongs * 1000 / sent : 0), stray);
  hm::logf("HM pingpong_rtt p50_ms=%u p95_ms=%u max_ms=%u", (unsigned)rtt.pct(50), (unsigned)rtt.pct(95),
           (unsigned)rtt.pct(100));
  hm::logf("HM pingpong_gap p50_ms=%u p95_ms=%u max_ms=%u", (unsigned)gaps.pct(50), (unsigned)gaps.pct(95),
           (unsigned)gaps.pct(100));
  rssi.log("rx");
  peer_rssi.log("peer");
}

}  // namespace pingpong
