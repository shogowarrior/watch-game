#include "hm/pairing.h"

#include <math.h>

namespace hm {
namespace pairing {

namespace {
constexpr uint32_t FNV_OFF = 0x811C9DC5;
constexpr uint32_t FNV_PRIME = 0x01000193;
}  // namespace

uint32_t fnv1a32(const uint8_t* data, size_t n) {
  uint32_t h = FNV_OFF;
  for (size_t i = 0; i < n; i++) h = (h ^ data[i]) * FNV_PRIME;   // uint32_t wraps as the Python's & 0xFFFFFFFF
  return h;
}

Runes rune_ids(const Mac& mac_a, const Mac& mac_b) {
  const Mac* a = &mac_a;
  const Mac* b = &mac_b;
  if (memcmp(b->b, a->b, sizeof a->b) < 0) {   // bytes compare as unsigned, like memcmp
    a = &mac_b;
    b = &mac_a;
  }
  uint8_t ab[2 * sizeof a->b];
  memcpy(ab, a->b, sizeof a->b);
  memcpy(ab + sizeof a->b, b->b, sizeof b->b);
  const uint32_t h = fnv1a32(ab, sizeof ab);
  return Runes{(int32_t)(h & 7), (int32_t)((h >> 3) & 7), (int32_t)((h >> 6) & 7)};
}

// ---- Calibrator --------------------------------------------------------------

Calibrator::Calibrator(double nominal, double clamp_db, int32_t window_ms, int32_t gate_ms, double sd_max,
                       int32_t skip_ms, int ring)
    : nominal(nominal), clamp_db(clamp_db), window_ms(window_ms), gate_ms(gate_ms), sd_max(sd_max),
      skip_ms(skip_ms), n_(clamp(ring, 1, RING)) {
  reset(0);
}

void Calibrator::reset(ticks_t t_ms) {
  t0 = t_ms;
  last_ = t_ms;
  k_ = 0;
  fill_ms = 0;
  sum = 0.0;
  count = 0;
  stable = false;
  sd.reset();
  done = false;
  skipped = false;
  p1m.reset();
}

void Calibrator::add(ticks_t t_ms, double rssi) {
  if (done) return;
  const int i = k_ % n_;   // k_ >= 0: C's % is the Python's
  t_[i] = t_ms;
  v_[i] = (float)rssi;
  k_++;
  if (stable) {
    sum += rssi;
    count++;
  }
}

bool Calibrator::gate_(ticks_t t_ms) {
  const int n = k_ < n_ ? k_ : n_;
  double s = 0.0;
  double ss = 0.0;
  int32_t c = 0;
  for (int j = 0; j < n; j++) {
    const int i = (k_ - 1 - j) % n_;   // >= 0 since j < n <= k_
    if (ticks_diff(t_ms, t_[i]) > gate_ms) break;
    const double v = (double)v_[i];
    s += v;
    ss += v * v;
    c++;
  }
  if (c < 3) {
    sd.reset();
    return false;
  }
  const double mu = s / c;
  const double var = ss / c - mu * mu;
  sd = var > 0.0 ? sqrt(var) : 0.0;
  return *sd <= sd_max;
}

bool Calibrator::update(ticks_t t_ms) {
  if (done) return false;
  const int32_t dt = ticks_diff(t_ms, last_);
  last_ = t_ms;
  const bool was = stable;
  stable = gate_(t_ms);
  if (was && stable && dt > 0) fill_ms += dt;
  if (fill_ms >= window_ms && count > 0) {
    const double m = sum / count;
    const double lo = nominal - clamp_db;
    const double hi = nominal + clamp_db;
    p1m = m < lo ? lo : m > hi ? hi : m;
    done = true;
    return true;
  }
  if (ticks_diff(t_ms, t0) >= skip_ms) {
    p1m = nominal;
    skipped = true;
    done = true;
    return true;
  }
  return false;
}

int32_t Calibrator::digit() const {
  const int32_t n = (int32_t)floordiv(window_ms + 999, 1000);
  const int32_t d = n - (int32_t)floordiv(fill_ms, 1000);
  return d < 1 ? 1 : d > n ? n : d;
}

// ---- Pairing -----------------------------------------------------------------

Pairing::Pairing(std::optional<Mac> my_mac, double nominal) : my_mac(my_mac), nominal(nominal), cal(nominal) {
  reset(0);
}

void Pairing::reset(ticks_t t_ms) {
  sub = LOOKING;
  t_sub = t_ms;
  peer_mac.reset();
  runes.reset();
  p1m.reset();
  confirmed = false;
  peer_confirmed = false;
  for (int k = 0; k < SEEN_SLOTS; k++) c_mac_[k].reset();
  last_rx_.reset();
  last_pair_.reset();
  countdown.reset();
  ready = false;
  peer_ready = false;
  t_split = t_ms;
  toast = nullptr;
  unstable = false;
  unst_t_.reset();
  digit_ = 0;
  haptic_ = hp::NONE;
}

void Pairing::start_split(ticks_t t_ms) {
  set_(SPLIT, t_ms);
  t_split = t_ms;
  countdown = split_s;
  digit_ = split_s;
  ready = false;
  peer_ready = false;
}

void Pairing::set_ready(ticks_t t_ms) {
  if (sub != SPLIT || ready || countdown == 0) return;
  ready = true;
  emit_(hp::TICK);
  skip_(t_ms);
}

void Pairing::set_peer_ready(ticks_t t_ms, bool on) {
  if (sub != SPLIT || !on || peer_ready) return;
  peer_ready = true;
  emit_(hp::DOUBLE);
  skip_(t_ms);
}

void Pairing::skip_(ticks_t t_ms) {
  if (!(ready && peer_ready)) return;
  const int32_t left = split_s * 1000 - ticks_diff(t_ms, t_sub);
  const int32_t keep = T::PAIR_READY_LEFT_S * 1000;
  if (left > keep) t_sub = ticks_add(t_ms, keep - split_s * 1000);
}

bool Pairing::on_candidate(ticks_t t_ms, const Mac& mac, double rssi, bool peer_pairing) {
  if (sub == LOOKING) {
    if (!peer_pairing || rssi < nominal + SEEN_MIN_DB) return false;
    const int k = slot_(mac, rssi);
    if (k < 0) return false;
    TickRing<SEEN_PACKETS>& rx = c_rx_[k];
    rx.note(t_ms);
    c_rssi_[k] = rssi;
    c_last_[k] = t_ms;
    if (!rx.full_within(t_ms, SEEN_WINDOW_MS)) return false;
    for (int j = 0; j < SEEN_SLOTS; j++)
      if (j != k && c_mac_[j] && c_rssi_[j] > rssi) return false;   // a stronger candidate is still around
    peer_mac = c_mac_[k];
    runes = rune_ids(my_mac ? *my_mac : *peer_mac, *peer_mac);
    set_(SEEN, t_ms);
    last_rx_ = t_ms;
    last_pair_ = t_ms;
    emit_(hp::DOUBLE);
    return true;
  }
  if (peer_mac && mac == *peer_mac) {
    last_rx_ = t_ms;
    if (peer_pairing) last_pair_ = t_ms;
    return true;
  }
  return false;
}

// Candidate slot of mac. A new MAC takes an empty slot, else the weakest one
// if it is stronger than that; otherwise -1 (it could never be taken).
int Pairing::slot_(const Mac& mac, double rssi) {
  int k = 0;
  for (int j = 0; j < SEEN_SLOTS; j++) {
    const std::optional<Mac>& m = c_mac_[j];
    if (m && *m == mac) return j;
    if (c_mac_[k] && (!m || c_rssi_[j] < c_rssi_[k])) k = j;
  }
  if (c_mac_[k] && c_rssi_[k] >= rssi) return -1;
  c_mac_[k] = mac;
  c_rx_[k].clear();
  return k;
}

void Pairing::on_rssi(ticks_t t_ms, double rssi) {
  last_rx_ = t_ms;
  if (sub == CALIBRATE) cal.add(t_ms, rssi);
}

bool Pairing::confirm(ticks_t t_ms) {
  if (sub != SEEN) return false;
  confirmed = true;
  if (peer_confirmed)
    start_cal_(t_ms);
  else
    set_(CONFIRMED, t_ms);
  return true;
}

bool Pairing::bump(ticks_t t_ms) {
  if (sub != SEEN && sub != CONFIRMED) return false;
  confirmed = true;
  peer_confirmed = true;
  start_cal_(t_ms);
  return true;
}

void Pairing::set_peer_confirmed(ticks_t t_ms, bool on) {
  peer_confirmed = on;
  if (on && sub == CONFIRMED) start_cal_(t_ms);
}

void Pairing::start_cal_(ticks_t t_ms) {
  set_(CALIBRATE, t_ms);
  unstable = false;
  unst_t_.reset();
  cal.reset(t_ms);
  digit_ = 0;
  countdown = cal.digit();
}

hp::Haptic Pairing::update(ticks_t t_ms) {
  toast = nullptr;
  if (sub == LOOKING) {
    for (int k = 0; k < SEEN_SLOTS; k++)   // forget candidates silent for the window
      if (c_mac_[k] && ticks_diff(t_ms, c_last_[k]) > SEEN_WINDOW_MS) c_mac_[k].reset();
  } else if (sub == SEEN || sub == CONFIRMED) {
    if ((last_rx_ && ticks_diff(t_ms, *last_rx_) > SEEN_LOST_MS) ||
        (last_pair_ && ticks_diff(t_ms, *last_pair_) > SEEN_WINDOW_MS))
      reset(t_ms);
  } else if (sub == CALIBRATE) {
    const bool fin = cal.update(t_ms);
    if (cal.stable)
      unst_t_.reset();
    else if (!unst_t_)
      unst_t_ = t_ms;
    unstable = unst_t_ && ticks_diff(t_ms, *unst_t_) >= UNSTABLE_SHOW_MS;
    const int32_t d = cal.digit();
    countdown = d;
    if (fin) {
      p1m = cal.p1m;
      if (cal.skipped) toast = TOAST_CAL_SKIPPED;
      emit_(hp::CLOSER);
      start_split(t_ms);
    } else if (cal.stable && d != digit_) {
      digit_ = d;
      emit_(hp::TICK);
    }
  } else if (sub == SPLIT) {
    const int32_t el = ticks_diff(t_ms, t_sub);
    const int32_t left = split_s * 1000 - el;
    if (left <= -T::PAIR_GO_MS) {
      countdown = 0;
      set_(DONE, t_ms);
    } else {
      const int32_t d = left <= 0 ? 0 : (int32_t)floordiv(left + 999, 1000);
      countdown = d;
      if (d != digit_) {
        digit_ = d;
        if (d == 0)
          emit_(hp::CLOSER);
        else if (d <= 3)
          emit_(hp::TICK);
      }
    }
  }
  const hp::Haptic h = haptic_;
  haptic_ = hp::NONE;
  return h;
}

}  // namespace pairing
}  // namespace hm
