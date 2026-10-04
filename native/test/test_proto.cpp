// hm::proto against finder/proto.py (native/test/golden/proto.txt).
#include <math.h>
#include <stdio.h>

#include "check.h"
#include "golden.h"
#include "hm/compat.h"
#include "hm/proto.h"

using hmt::flt;
using hmt::none;
using hmt::num;

TEST(test_proto_packs_and_reads_back_like_python) {
  int packs = 0, bumps = 0, deltas = 0;
  for (const hmt::Tokens& t : hmt::golden("proto")) {
    if (t[0] == "pack") {
      hm::proto::Beacon b;
      b.game_id = (uint8_t)num(t[1]);
      b.seq = (uint16_t)num(t[2]);
      b.rssi_last = none(t[3]) ? NAN : flt(t[3]);
      b.rssi_filt = none(t[4]) ? NAN : flt(t[4]);
      b.steps = (uint32_t)num(t[5]);
      b.activity = num(t[6]);
      b.battery = num(t[7]);
      b.state = num(t[8]);
      b.set_flags(num(t[9]), num(t[10]), num(t[11]), num(t[12]));
      b.bump_ago_ms = num(t[13]);
      uint8_t buf[hm::proto::SIZE];
      b.pack(buf);
      char hex[2 * hm::proto::SIZE + 1];
      for (int i = 0; i < hm::proto::SIZE; i++) snprintf(hex + 2 * i, 3, "%02x", buf[i]);
      CHECK(t[15] == hex);
      hm::proto::Beacon r;
      r.unpack(buf);
      const long want[] = {r.game_id, r.seq, (long)r.rssi_last, (long)r.rssi_filt, (long)r.steps, r.activity,
                           r.battery, r.state, r.flags, r.bump_ago_ms, r.sweeping(), r.taps(), r.walking(),
                           r.ready(), hm::proto::valid(buf, sizeof buf, r.game_id)};
      for (int i = 0; i < 15; i++) CHECK(num(t[17 + i]) == want[i]);
      CHECK(!hm::proto::valid(buf, sizeof buf, (r.game_id + 1) & 0xFF) && !hm::proto::valid(buf, 15));
      packs++;
    } else if (t[0] == "bump_ago") {
      const bool has = !none(t[2]);
      CHECK(hm::proto::bump_ago((hm::ticks_t)num(t[1]), has, has ? (hm::ticks_t)num(t[2]) : 0) == num(t[4]));
      bumps++;
    } else if (t[0] == "steps_delta") {
      CHECK(hm::proto::steps_delta((uint16_t)num(t[1]), (uint16_t)num(t[2])) == num(t[4]));
      deltas++;
    }
  }
  CHECK(packs == 5 && bumps == 5 && deltas == 3);
}

TEST(test_tick_ring_counts_events_within_a_window) {
  hm::TickRing<3> r;
  r.note(100);
  r.note(900);
  CHECK(!r.full_within(1000, 2000));        // two of three
  r.note(1500);
  CHECK(r.full_within(1600, 2000) && !r.full_within(2200, 2000));
  r.note(2600);                              // the oldest is now 900
  CHECK(r.full_within(2800, 2000));
  r.expire(2700, 500);
  CHECK(r.size() == 3);
  r.expire(3200, 500);
  CHECK(r.size() == 0);
}
