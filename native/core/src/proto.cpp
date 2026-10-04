#include "hm/proto.h"

namespace hm {
namespace proto {

int8_t clamp_i8(std::optional<double> v) {
  if (!v) return RSSI_NONE;
  return (int8_t)clamp<int64_t>(pyround(*v), -128, 127);
}

uint8_t clamp_u8(int32_t v) { return (uint8_t)(v < 0 ? 0 : v > 255 ? 255 : v); }

uint16_t clamp_u16(int32_t v) { return (uint16_t)(v < 0 ? 0 : v > 0xFFFF ? 0xFFFF : v); }

uint16_t bump_ago(ticks_t now, opt_ticks bump_t) {
  if (!bump_t) return BUMP_NONE;
  const int32_t d = ticks_diff(now, *bump_t);
  if (d < 0) return 0;
  return d > BUMP_MAX ? BUMP_NONE : (uint16_t)d;
}

bool valid(const uint8_t* buf, size_t n, int game_id) {
  return n >= (size_t)SIZE && buf[0] == 'S' && buf[1] == 'K' && buf[2] == VERSION &&
         (game_id < 0 || buf[3] == game_id);
}

void Beacon::set_flags(bool sweeping, int taps, bool walking, bool ready) {
  flags = (sweeping ? F_SWEEP : 0) | ((taps & 7) << F_TAPS_SHIFT) | (walking ? F_WALK : 0) | (ready ? F_READY : 0);
}

void Beacon::pack(uint8_t* b) const {
  b[0] = 'S';
  b[1] = 'K';
  b[2] = VERSION;
  b[3] = (uint8_t)game_id;
  b[4] = (uint8_t)seq;
  b[5] = (uint8_t)(seq >> 8);
  b[6] = (uint8_t)clamp_i8(rssi_last);
  b[7] = (uint8_t)clamp_i8(rssi_filt);
  b[8] = (uint8_t)steps;
  b[9] = (uint8_t)(steps >> 8);
  b[10] = clamp_u8(activity);
  b[11] = clamp_u8(battery);
  b[12] = clamp_u8(state);
  b[13] = (uint8_t)flags;
  const uint16_t bump = clamp_u16(bump_ago_ms);
  b[14] = (uint8_t)bump;
  b[15] = (uint8_t)(bump >> 8);
}

void Beacon::unpack(const uint8_t* b) {
  game_id = b[3];
  seq = b[4] | b[5] << 8;
  rssi_last = (int8_t)b[6];
  rssi_filt = (int8_t)b[7];
  steps = b[8] | b[9] << 8;
  activity = b[10];
  battery = b[11];
  state = b[12];
  flags = b[13];
  bump_ago_ms = b[14] | b[15] << 8;
}

}  // namespace proto
}  // namespace hm
