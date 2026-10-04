#include "hm/link.h"

#include "hm/proto.h"

namespace hm {
namespace link {

int Xorshift16::next() {
  int v = x;
  v ^= (v << 7) & 0xFFFF;
  v ^= v >> 9;
  v ^= (v << 8) & 0xFFFF;
  return x = v;
}

int TxScheduler::interval() { return period - jitter + rng.next() % (2 * jitter + 1); }

void TxScheduler::mark_sent(ticks_t now) {
  ticks_t base = next_t && ticks_diff(now, *next_t) < period ? *next_t : now;
  next_t = ticks_add(base, interval());
}

LinkMonitor::LinkMonitor(int game_id, int loss_window, int max_gap)
    : game_id(game_id), max_gap(max_gap), w(clamp(loss_window, 1, MAX_WINDOW)) {}

void LinkMonitor::reset_link() {
  n_rx = 0;
  last_seq = -1;
  last_seen.reset();
  gi_ = gn_ = 0;
  gsum_ = 0;
}

void LinkMonitor::lock(const Mac& mac) {
  partner = mac;
  reset_link();
}

void LinkMonitor::unlock() {
  partner.reset();
  reset_link();
}

int LinkMonitor::on_packet(const Mac& mac, const uint8_t* buf, int n, ticks_t t_rx) {
  if (!proto::valid(buf, n, game_id)) {
    n_bad++;
    return R_BAD;
  }
  if (!partner) return R_CANDIDATE;
  if (mac != *partner) {
    n_other++;
    return R_OTHER;
  }
  const int seq = proto::seq_of(buf);
  if (n_rx) {
    const int gap = (seq - last_seq) & 0xFFFF;
    if (gap == 0) {
      n_dup++;
      last_seen = t_rx;
      return R_DUP;
    }
    if (gap > max_gap) {   // backwards or implausible jump: restarted
      n_restart++;
      gi_ = gn_ = 0;
      gsum_ = 0;
    } else {
      push_gap(gap);
    }
  }
  last_seq = seq;
  n_rx++;
  last_seen = t_rx;
  return R_PARTNER;
}

void LinkMonitor::push_gap(int g) {
  if (gn_ == w)
    gsum_ -= gaps_[gi_];
  else
    gn_++;
  gaps_[gi_] = g;
  gsum_ += g;
  gi_ = gi_ + 1 == w ? 0 : gi_ + 1;
}

int LinkMonitor::loss_pct() const {
  return gsum_ == 0 ? 0 : (int)floordiv(100 * (gsum_ - gn_) + gsum_ / 2, gsum_);
}

}  // namespace link
}  // namespace hm
