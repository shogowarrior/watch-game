// finder/proto.py: the 16-byte ESP-NOW beacon, little-endian.
//
//   off  field         off  field
//     0  magic "SK"     10  activity (ACT_*)
//     2  version        11  battery % (BATT_UNKNOWN)
//     3  game_id        12  game state (session.h)
//     4  seq u16        13  flags: sweep, taps (3 bits), walk, ready
//     6  rssi_last i8   14  bump_ago_ms u16 (BUMP_NONE)
//     7  rssi_filt i8
//     8  steps u16
#pragma once
#include <math.h>
#include <stddef.h>
#include <stdint.h>

#include "hm/ticks.h"

namespace hm {
namespace proto {

constexpr int SIZE = 16;
constexpr uint8_t VERSION = 3;
constexpr int RSSI_NONE = -128;
constexpr uint16_t BUMP_NONE = 0xFFFF, BUMP_MAX = 0xFFFE;
constexpr uint8_t BATT_UNKNOWN = 255;
constexpr uint8_t F_SWEEP = 0x01, F_TAPS = 0x0E, F_TAPS_SHIFT = 1, F_WALK = 0x10, F_READY = 0x20;

// An RSSI-ish value as i8, rounded half to even like Python's round(); NAN is None.
int8_t clamp_i8(float v);
uint8_t clamp_u8(int32_t v);
uint16_t clamp_u16(int32_t v);
// ms since my last bump, or BUMP_NONE when there is none (has_bump false) or it is too old.
uint16_t bump_ago(ticks_t now, bool has_bump, ticks_t bump_t);
inline uint16_t steps_delta(uint16_t now, uint16_t old) { return (uint16_t)(now - old); }
// A beacon of this version (and of this game, unless game_id < 0).
bool valid(const uint8_t* buf, size_t n, int game_id = -1);
inline uint16_t seq_of(const uint8_t* buf) { return (uint16_t)(buf[4] | buf[5] << 8); }

struct Beacon {
  uint8_t game_id = 0;
  uint16_t seq = 0;
  float rssi_last = NAN, rssi_filt = NAN;   // NAN: none (sent as RSSI_NONE)
  uint32_t steps = 0;
  int32_t activity = 0, battery = BATT_UNKNOWN, state = 0;
  uint8_t flags = 0;
  int32_t bump_ago_ms = BUMP_NONE;

  uint16_t next_seq() { return ++seq; }
  void set_flags(bool sweeping, int taps, bool walking, bool ready);
  void set_bump(ticks_t now, bool has_bump, ticks_t bump_t) { bump_ago_ms = bump_ago(now, has_bump, bump_t); }
  bool sweeping() const { return flags & F_SWEEP; }
  bool walking() const { return flags & F_WALK; }
  bool ready() const { return flags & F_READY; }
  int taps() const { return (flags & F_TAPS) >> F_TAPS_SHIFT; }
  void pack(uint8_t* buf) const;
  // Decodes the fields without validating (check valid() first). rssi_* come
  // back as the i8 on the wire, RSSI_NONE included, as finder/proto.py does.
  void unpack(const uint8_t* buf);
};

}  // namespace proto
}  // namespace hm
