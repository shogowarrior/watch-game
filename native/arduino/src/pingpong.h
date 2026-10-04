// The ping side of tools/radio_pingpong.py: 16-byte beacons (finder/proto.py) on
// a fixed grid, matched against the pongs a MicroPython watch echoes with
// pp.run('pong'), and reported as HM lines next to the RSSI the peer measured.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "hm/proto.h"
#include "hm/stats.h"
#include "hm/ticks.h"

namespace pingpong {

constexpr uint8_t GAME_ID = 0xEE;    // tools/radio_pingpong.py
constexpr uint8_t KIND_PING = 1, KIND_PONG = 2;

// n, min, mean, max and population sd of RSSI values, as radio_pingpong.summary.
struct RssiSummary {
  int n = 0, lo = 0, hi = 0;
  int64_t sum = 0, sq = 0;
  void add(int v);
  void log(const char* where) const;
};

class Pinger {
 public:
  explicit Pinger(int n = 1000, int period_ms = 50, int tail_ms = 1000);
  // True when a ping is due at now, packed into out (hm::proto::SIZE bytes): send it then.
  bool due(hm::ticks_t now, uint8_t* out);
  void on_rx(const uint8_t* d, size_t len, int8_t rssi, hm::ticks_t t);
  bool done(hm::ticks_t now) const;
  void report(int channel) const;

  int sent = 0, pongs = 0, stray = 0;
  hm::Stats rtt, gaps;           // ms
  RssiSummary rssi, peer_rssi;   // what this watch measured on pongs; what the peer measured on pings

 private:
  static constexpr int MAX_N = hm::Stats::N;
  const int n_, period_, tail_;
  hm::ticks_t next_ = 0, last_send_ = 0, last_rx_ = 0;
  bool any_rx_ = false;
  hm::proto::Beacon ping_, pong_;
  hm::ticks_t t_sent_[MAX_N];
  uint8_t got_[MAX_N] = {};
};

}  // namespace pingpong
