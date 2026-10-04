// finder/pairing.py: PAIRING screen logic (ui-spec §6 PAIRING, §5.8
// calibration), pure and allocation-free.
//
//     looking -> seen -> confirmed -> calibrate -> split -> done
//
// * looking: a nearby watch that is also pairing is heard SEEN_PACKETS times
//   within SEEN_WINDOW_MS at rssi >= nominal + SEEN_MIN_DB -> seen. The
//   SEEN_SLOTS strongest candidates are tracked at once; one is taken only if
//   no other candidate still held is stronger.
// * seen: both watches show the same 3 runes (rune_ids of the two MACs). A
//   tap confirms this side (confirmed); a matched bump confirms both. seen and
//   confirmed fall back to looking when the partner is silent SEEN_LOST_MS or
//   has not shown PAIRING for SEEN_WINDOW_MS.
// * calibrate: Calibrator (3 s stable-gated mean RSSI at 1 m, clamped,
//   skipped after 10 s: toast CAL SKIPPED).
// * split: split_s countdown, TICK at 3/2/1, CLOSER and GO at 0 for
//   T::PAIR_GO_MS. Once both players are READY it jumps to
//   T::PAIR_READY_LEFT_S left.
//
// update(t) returns the haptic raised since the last update (NONE for the
// Python's None); toast is a one-shot set on the update that raised it.
// Python strings: sub is a Sub (name() gives the spelling), toast a static
// literal or nullptr, haptics the haptic_patterns enum.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include <array>
#include <optional>

#include "hm/compat.h"
#include "hm/haptic_patterns.h"
#include "hm/py.h"
#include "hm/ticks.h"
#include "hm/tuning.h"

namespace hm {
namespace pairing {

namespace hp = hm::haptic_patterns;

// Sub-states; SUB_NAMES holds the Python's strings.
enum Sub : int8_t { LOOKING, SEEN, CONFIRMED, CALIBRATE, SPLIT, DONE };
constexpr const char* const SUB_NAMES[6] = {"looking", "seen", "confirmed", "calibrate", "split", "done"};
inline const char* name(Sub s) { return SUB_NAMES[s]; }

constexpr double SEEN_MIN_DB = -15.0;                       // candidate RSSI >= p1m + this (about 3.8 m at n 2.6)
constexpr int SEEN_PACKETS = T::RELINK_PACKETS;
constexpr int32_t SEEN_WINDOW_MS = T::RELINK_WINDOW_MS;
constexpr int SEEN_SLOTS = 4;                               // pairing candidates tracked at once
constexpr int32_t SEEN_LOST_MS = T::LINK_LOST_AFTER_MS;     // candidate silent this long -> looking
constexpr int32_t UNSTABLE_SHOW_MS = 700;                   // fill paused this long before HOLD STILL
constexpr const char* HINT_HOLD_STILL = "HOLD STILL";       // calibrate chip while unstable (the renderer keys on it)
constexpr const char* TOAST_CAL_SKIPPED = "CAL SKIPPED";

// 32-bit FNV-1a hash of n bytes.
uint32_t fnv1a32(const uint8_t* data, size_t n);

// 3 rune ids, 3 bits each of the hash of the two MACs sorted: indexes 0..7
// into RUNES (finder/tuning.py, tokens glyphs.runes; tuning.h skips it).
using Runes = std::array<int32_t, 3>;
Runes rune_ids(const Mac& mac_a, const Mac& mac_b);

// 1 m RSSI calibration: stable-time-gated mean, clamped, with a skip timeout.
class Calibrator {
 public:
  static constexpr int RING = 64;   // the Python's ring default; a larger ring is clamped to it

  explicit Calibrator(double nominal = T::P1M_NOMINAL_DBM, double clamp_db = T::CAL_CLAMP_DB,
                      int32_t window_ms = T::CAL_WINDOW_MS, int32_t gate_ms = T::CAL_GATE_WINDOW_MS,
                      double sd_max = T::CAL_UNSTABLE_SD_DB, int32_t skip_ms = T::CAL_SKIP_AFTER_MS, int ring = RING);
  void reset(ticks_t t_ms);
  // One per-packet RSSI measured at ~1 m.
  void add(ticks_t t_ms, double rssi);
  // Advance the fill; true on the update that finished (or skipped).
  bool update(ticks_t t_ms);
  // Countdown digit (3/2/1 for a 3 s window) from the fill.
  int32_t digit() const;

  double nominal;
  double clamp_db;
  int32_t window_ms;
  int32_t gate_ms;
  double sd_max;
  int32_t skip_ms;
  ticks_t t0 = 0;
  int32_t fill_ms = 0;
  double sum = 0.0;
  int32_t count = 0;
  bool stable = false;
  std::optional<double> sd;
  bool done = false;
  bool skipped = false;
  std::optional<double> p1m;

 private:
  bool gate_(ticks_t t_ms);

  int n_;
  ticks_t t_[RING] = {};   // array('i') of packet times
  float v_[RING] = {};     // array('f') of their RSSI
  ticks_t last_ = 0;
  int32_t k_ = 0;          // samples written to the ring
};

// PAIRING sub-state machine for one watch.
class Pairing {
 public:
  explicit Pairing(std::optional<Mac> my_mac = std::nullopt, double nominal = T::P1M_NOMINAL_DBM);

  // Back to looking: forget the partner and calibration.
  void reset(ticks_t t_ms);
  // New round with the existing pairing and calibration.
  void start_split(ticks_t t_ms);
  // This player tapped READY in the split (before GO); final.
  void set_ready(ticks_t t_ms);
  // The partner's beacons say READY (it shows its split).
  void set_peer_ready(ticks_t t_ms, bool on);

  // ---- inputs
  // Any valid beacon while looking/seen/confirmed (peer_pairing: the sender
  // shows PAIRING). Returns true if it is the partner.
  bool on_candidate(ticks_t t_ms, const Mac& mac, double rssi, bool peer_pairing = true);
  // Partner packet RSSI (own measurement); feeds the calibration.
  void on_rssi(ticks_t t_ms, double rssi);
  // Tap / short press in seen: this side says the runes match.
  bool confirm(ticks_t t_ms);
  // Matched bump in seen/confirmed: both sides confirm at once.
  bool bump(ticks_t t_ms);
  void set_peer_confirmed(ticks_t t_ms, bool on);

  // ---- per frame
  // Advance timers; returns the haptic started this frame (or NONE).
  hp::Haptic update(ticks_t t_ms);

  // ---- outputs
  // Split countdown reached 0 (word GO).
  bool go() const { return sub == SPLIT && countdown == 0; }

  std::optional<Mac> my_mac;
  double nominal;
  Calibrator cal;
  int32_t split_s = T::PAIR_SPLIT_S;   // split countdown length, s (the web sim's demo shortens it)
  std::optional<double> p1m;
  Sub sub = LOOKING;
  ticks_t t_sub = 0;
  std::optional<Mac> peer_mac;
  std::optional<Runes> runes;
  bool confirmed = false;
  bool peer_confirmed = false;
  std::optional<int32_t> countdown;
  bool ready = false;        // split: this player tapped READY
  bool peer_ready = false;   // ... and the partner did (from its beacons)
  ticks_t t_split = 0;       // split start (t_sub moves when READY skips ahead)
  const char* toast = nullptr;
  bool unstable = false;

 private:
  void skip_(ticks_t t_ms);
  void set_(Sub s, ticks_t t_ms) {
    sub = s;
    t_sub = t_ms;
  }
  void emit_(hp::Haptic h) { haptic_ = hp::stronger(haptic_, h); }
  int slot_(const Mac& mac, double rssi);
  void start_cal_(ticks_t t_ms);

  // looking: candidate table (MAC, packet times, last RSSI and time)
  std::optional<Mac> c_mac_[SEEN_SLOTS];
  TickRing<SEEN_PACKETS> c_rx_[SEEN_SLOTS];
  double c_rssi_[SEEN_SLOTS] = {};
  ticks_t c_last_[SEEN_SLOTS] = {};
  opt_ticks last_rx_;
  opt_ticks last_pair_;   // the partner last showed PAIRING (seen/confirmed)
  opt_ticks unst_t_;
  int32_t digit_ = 0;
  hp::Haptic haptic_ = hp::NONE;
};

}  // namespace pairing
}  // namespace hm
