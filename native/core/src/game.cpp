// finder/game.py (see hm/game.h).
#include "hm/game.h"

#include <stdio.h>
#include <string.h>

namespace hm {
namespace game {

namespace {

namespace P = hm::pairing;
namespace A = hm::arrow;
namespace S = hm::session;
namespace X = hm::proximity;
namespace SC = hm::scan;

const char* const MODE_NAMES[] = {"PAIRING", "SEARCHING", "HUNT", "SCANNING", "FOUND", "LINK_LOST"};
const rp::Sub SUBS[] = {rp::Sub::READY, rp::Sub::SWEEP, rp::Sub::RESULT};   // by scan::Phase

constexpr double FP_INTEN = T::FIELD_PAIRING_LOOKING_1;   // the looking field (tokens field.pairing_looking)
constexpr double FP_SPEED = T::FIELD_PAIRING_LOOKING_2;
constexpr int32_t FP_PERIOD = T::FIELD_PAIRING_LOOKING_3;
constexpr double FP_GLOW = T::FIELD_PAIRING_LOOKING_4;
constexpr int32_t BREATHE_MS = T::BREATHE_PAIRING_MS;    // still PAIRING halo (seen..calibrate)

// Stamp s with its age capped at STALE_MS.
opt_ticks cap(ticks_t t_ms, opt_ticks s) {
  if (s && ticks_diff(t_ms, *s) > STALE_MS) return ticks_add(t_ms, -STALE_MS);
  return s;
}
ticks_t cap(ticks_t t_ms, ticks_t s) { return ticks_diff(t_ms, s) > STALE_MS ? ticks_add(t_ms, -STALE_MS) : s; }

// Halo radius for intensity i (tokens field.glow_r).
double glow(double i) { return T::GLOW_R_A + T::GLOW_R_B * i; }

bool in_zone_names(const char* scr) {
  for (const char* z : T::ZONE_NAMES)
    if (strcmp(scr, z) == 0) return true;
  return false;
}

// A counted spike at spike_t makes the touch that landed at t_down a knock's
// (screen to screen): from KNOCK_TOUCH_BEFORE_MS before to
// KNOCK_TOUCH_AFTER_MS after the touch-down.
bool knock(opt_ticks spike_t, ticks_t t_down) {
  if (!spike_t) return false;
  const int32_t d = ticks_diff(*spike_t, t_down);
  return -T::KNOCK_TOUCH_BEFORE_MS <= d && d <= T::KNOCK_TOUCH_AFTER_MS;
}

// LINK_LOST banners: fixed words (no running clock, ui-spec §6 LINK-LOST)
rp::Banner sticky(const char* text, rp::Severity sev) {
  rp::Banner b;
  b.text = rp::Text(text);
  b.severity = sev;
  b.sticky = true;
  return b;
}

}  // namespace

const char* name(Mode m) { return MODE_NAMES[m]; }

Game::Game(std::optional<Mac> my_mac_, est::RangeEstimator* est_, arrow::Mode arrow_mode_, BlankFn blank_fn_,
           void* blank_ctx_, ticks_t t_ms, std::optional<int32_t> battery_)
    : my_mac(my_mac_),
      est(est_ ? est_ : &own_est_),
      arrow_mode(arrow_mode_),
      blank_fn(blank_fn_),
      blank_ctx(blank_ctx_),
      pair(my_mac_),
      scan(t_ms, scan_blank, this),
      battery(battery_),
      wake_t_(t_ms) {
  set_place(false);
  reset(t_ms);
}

// ---- lifecycle -----------------------------------------------------------------

void Game::set_place(bool indoor_) {
  indoor = indoor_;
  est->set_exponent(indoor ? T::PATH_LOSS_N_INDOOR : T::PATH_LOSS_N);
}

void Game::set_theme(const char* name) {
  const int32_t k = name ? rp::theme_of(name) : rp::N_THEMES;
  theme = k < rp::N_THEMES ? k : rp::THEME_DEFAULT;
}

void Game::reset(ticks_t t_ms) {
  mode = PAIRING;
  mode_t = t_ms;
  pair.reset(t_ms);
  peer.reset();
  meter.reset();
  restart_estimate();
  arrow = nullptr;
  stash_ = nullptr;
  rdown_.reset();
  p1m_.reset();
  rssi_last.reset();
  bump_t.reset();
  taps = 0;
  tap_hot_ = false;
  used_my_.reset();
  used_peer_.reset();
  press_t_.reset();
  spike_t_.reset();
  held_g_.reset();
  touches_.clear();
  touch_block_.reset();
  hap_ = hp::NONE;
  burst_ = false;
  blank_.reset();
  toast_on_ = false;
  toast_sev_ = rp::Severity::INFO;
  toast_until_.reset();
  hint_ = nullptr;
  hint_until_ = t_ms;
  teach_far_ = false;
  last_trend_ = 0;
  last_trend_t_.reset();
  lost_trend = 0;
  lost_hint_ = false;
  left_t_.reset();
  held_ = hp::NONE;
  hz_ = meter.expected_hz;
  hz_lo_ = hz_;
  hz_t_.reset();
  unrel_t_.reset();
  br_since_.reset();
  bump_ready = false;
  br_fired_ = false;
  felt_on_ = false;
  felt_t0_ = t_ms;
  fm_.reset();
  fq_.reset();
  fm_seen_.reset();
  fq_seen_.reset();
  fq_conf_ = false;
  touch_t_.reset();
  spike_touched_.reset();
  felt_ = nullptr;
  howto.reset();
  look_t_.reset();
  howto_hinted_ = false;
  howto_shut_.reset();
  ign_s_ = nullptr;
  ign_td_ = 0;
  ign_knock_ = false;
  ign_t_.reset();
  felt_until_ = t_ms;
  still_since_.reset();
  still_done_ = false;
  peer_sweep_ = false;
  hold_n_ = 0;
  hold_t_ = t_ms;
  round_t0.reset();
  found_t.reset();
  time_on_ = false;
  lost_zone_.reset();
  lost_band_.reset();
  lost_i_ = 0.0;
  menu.reset(t_ms);
  input_t_ = t_ms;
  peer_bat_warned_ = false;
  scans = 0;
  state_byte = S::SC_PAIRING;
}

void Game::restart_estimate() {
  est->reset();
  px.reset();
}

// ---- inputs --------------------------------------------------------------------

void Game::set_motion(ticks_t t_ms, int32_t activity, uint32_t steps, double step_rate_hz,
                      std::optional<double> tilt_deg, bool face_up) {
  me.set(t_ms, activity, steps, step_rate_hz, tilt_deg, face_up);
}

void Game::on_packet(ticks_t t_ms, const Mac& mac, double rssi, const proto::Beacon& b) {
  if (power_off) return;
  if (mode == PAIRING && (pair.sub == P::LOOKING || pair.sub == P::SEEN || pair.sub == P::CONFIRMED)) {
    if (!pair.on_candidate(t_ms, mac, rssi, (b.state & S::SC_MASK) == S::SC_PAIRING)) return;
    peer.on_beacon(t_ms, b);
    rssi_last = rssi;
    meter.note(t_ms);
    pair.on_rssi(t_ms, rssi);
    pair.set_peer_confirmed(t_ms, peer.confirmed());
    return;
  }
  if (!pair.peer_mac || mac != *pair.peer_mac) return;
  peer.on_beacon(t_ms, b);
  rssi_last = rssi;
  meter.note(t_ms);
  if (mode == PAIRING && pair.sub == P::CALIBRATE) {
    pair.on_rssi(t_ms, rssi);
    return;
  }
  if (mode == PAIRING && pair.sub == P::SPLIT) pair.set_peer_ready(t_ms, peer.ready());
  const std::optional<double> peer_rssi = peer.rssi_last;
  est->update(t_ms, rssi, peer_rssi, &me.info, &peer.motion);
  if (mode == SCANNING) scan.on_packet(t_ms, rssi, peer_rssi);
  if (arrow && arrow->phase == A::PH_TURN) mirror.add(t_ms, rssi);
}

bool Game::on_accel_tap(ticks_t t_ms) {
  if (blanked(t_ms)) return false;
  spike_t_ = t_ms;
  bump_t = t_ms;
  if (touch_t_ && knock(t_ms, *touch_t_)) spike_touched_ = t_ms;
  taps = (taps + 1) & 7;
  tap_hot_ = mode == HUNT && px.zone() == X::HOT;
  return true;
}

bool Game::bump_armed() const {
  if (mode == HUNT) return px.zone() == X::HOT;
  if (mode == FOUND) return true;
  return mode == PAIRING && (pair.sub == P::SEEN || pair.sub == P::CONFIRMED);
}

void Game::on_touch_down(ticks_t t_ms) {
  note_touch(t_ms);
  if (screen_on && !power_off && !touch_block_ && ticks_diff(t_ms, wake_t_) >= T::WAKE_TOUCH_IGNORE_MS)
    touch_burst(t_ms);
}

void Game::on_wake(ticks_t t_ms) {
  if (!screen_on) wake(t_ms);
}

void Game::on_gesture(ticks_t t_ms, int code, int32_t x, int32_t y, opt_ticks t_down) {
  if (code == 0) return;
  if (!screen_on || power_off || bye_t_) return;
  const ticks_t td = t_down ? *t_down : t_ms;
  note_touch(td);
  resolve_held(t_ms, true);
  if (knock(spike_t_, td)) {   // a knock or a finger's own spike (§8)
    if (!peer_spiked(*spike_t_)) held_g_ = Held{code, x, y, td, *spike_t_};   // waits for the partner's word
    return;
  }
  gesture(t_ms, code, x, y, td);
}

void Game::note_touch(ticks_t td) {
  touch_t_ = td;
  if (knock(spike_t_, td)) spike_touched_ = spike_t_;
}

bool Game::peer_spiked(ticks_t s) const {
  if (!peer.tap_t) return false;
  const int32_t d = ticks_diff(s, *peer.tap_t);
  return -T::BUMP_WINDOW_MS <= d && d <= T::BUMP_WINDOW_MS;
}

void Game::resolve_held(ticks_t t_ms, bool now) {
  if (!held_g_) return;
  const Held h = *held_g_;
  if (peer_spiked(h.spike)) {
    held_g_.reset();
    return;
  }
  if (!now && ticks_diff(t_ms, h.spike) < T::KNOCK_WAIT_MS) return;
  held_g_.reset();
  if (screen_on && !power_off && !bye_t_) gesture(t_ms, h.code, h.x, h.y, h.t_down);
}

void Game::gesture(ticks_t t_ms, int code, int32_t x, int32_t y, ticks_t td) {
  if (ticks_diff(td, wake_t_) < T::WAKE_TOUCH_IGNORE_MS) return;
  if (touch_block_ && ticks_diff(td, *touch_block_) < 0) return;
  input_t_ = t_ms;
  if (code == gestures::LONG_PRESS) {
    long_press(t_ms);
    return;
  }
  if (menu.is_open && (code == gestures::SWIPE_U || code == gestures::SWIPE_D)) {
    menu.scroll(t_ms, code == gestures::SWIPE_U ? 1 : -1);
    return;
  }
  if (code == gestures::SWIPE_L || code == gestures::SWIPE_R) {
    if (!menu.is_open && mode == PAIRING && pair.sub == P::LOOKING && !inter_until_ && !inter_pending_)
      howto_flip(code == gestures::SWIPE_L ? 1 : -1);
    return;
  }
  if (code != gestures::TAP) return;
  if (menu.is_open) {
    menu_apply(t_ms, menu.tap(t_ms, y));
    return;
  }
  const int32_t dx = x - T::CENTER[0];
  const int32_t dy = y - T::CENTER[1];
  if (dx * dx + dy * dy > T::TAP_R_MAX_PX * T::TAP_R_MAX_PX) return;
  if (!primary(t_ms, false)) ignored(t_ms, td);
}

void Game::on_button(ticks_t t_ms, bool long_) {
  if (power_off) return;
  if (!screen_on) {
    wake(t_ms);
    return;
  }
  if (bye_t_) return;   // shutting down: BYE only
  input_t_ = t_ms;
  if (unseen_ && !long_) {
    unseen_ = false;    // lit by an event, wrist still down: a look (§8)
    return;
  }
  if (long_)
    long_press(t_ms);
  else if (menu.is_open)
    menu_apply(t_ms, menu.next(t_ms));
  else if (!primary(t_ms, true))
    ignored(t_ms, t_ms);
}

// Rain/sleeve filter: >= 3 touch-downs in 1 s block touches for 2 s.
void Game::touch_burst(ticks_t t_ms) {
  touches_.note(t_ms);
  if (touches_.full_within(t_ms, T::TOUCH_BURST_WINDOW_MS)) {
    touch_block_ = ticks_add(t_ms, T::TOUCH_BURST_IGNORE_MS);
    touches_.clear();
    ign_s_ = nullptr;   // a burst is no deliberate tap
  }
}

// The tap / short-press action of this screen; false if it did nothing (an
// ignored tap or press, §8 Ignored taps).
bool Game::primary(ticks_t t_ms, bool button) {
  if (mode == PAIRING) {
    if ((pair.sub == P::LOOKING || pair.sub == P::SEEN) && howto_shown(t_ms)) {
      if (button) howto.reset();   // a press closes the cards; it never confirms
      return true;                 // runes the player has not seen (§6 PAIRING)
    }
    if (pair.sub == P::SPLIT) {
      pair.set_ready(t_ms);
      if (pair.ready && toast_is(T_NEW_ROUND)) toast_on_ = false;   // its READY word shows at once (§6 Round start)
      return true;
    }
    return pair.confirm(t_ms);
  }
  if (mode == HUNT) {
    if (arrow && arrow->tap()) return true;
    if (px.zone() == X::HOT) {
      if (!button) return false;   // HOT: the screen is where watches knock (§8)
      if (press_t_ && 0 <= ticks_diff(t_ms, *press_t_) && ticks_diff(t_ms, *press_t_) <= T::HOT_SCAN_PRESS_MS &&
          !(peer.fresh(t_ms) && peer.pressed())) {
        press_t_.reset();    // double press: scan, not a fallback bump
        start_scan(t_ms);
      } else {
        press_t_ = t_ms;     // fallback bump press
      }
      return true;
    }
    start_scan(t_ms);
    return true;
  }
  if (mode == SCANNING) return scan.cancel(t_ms);
  if (mode == FOUND) {   // the button only: a tap or a knock never skips the result
    if (button && ticks_diff(t_ms, *found_t) >= T::FOUND_CELEBRATE_MS) {
      new_round(t_ms);
      return true;
    }
  }
  return false;
}

// ---- how-to cards and ignored taps (ui-spec §6 PAIRING, §8) --------------------

// A card is open, was on the last frame, or closed by itself moments ago: a
// press or tap then is about the card, not the runes.
bool Game::howto_shown(ticks_t t_ms) const {
  if (howto.open()) return true;
  if (params && params->sub == rp::Sub::HOWTO) return true;
  return howto_shut_ && ticks_diff(t_ms, *howto_shut_) < T::HOWTO_PRESS_GUARD_MS;
}

void Game::howto_flip(int32_t d) {
  if (howto.flip(d)) {   // just opened: the hint has done its job
    howto_known_ = true;
    if (toast_is(T_SWIPE_HOWTO)) toast_on_ = false;
    if (ign_s_ == T_SWIPE_HOWTO) ign_s_ = nullptr;
  }
}

void Game::howto_close(ticks_t t_ms) {
  if (howto.open()) {
    howto.reset();
    howto_shut_ = t_ms;
  }
}

// The toast an ignored tap or press raises on this screen, or nullptr.
const char* Game::ignored_text(ticks_t t_ms) const {
  if (inter_until_ || inter_pending_) return nullptr;   // SAVER ON owns the screen
  if (mode == PAIRING) {
    if (pair.sub == P::LOOKING && !howto.open()) return T_SWIPE_HOWTO;
  } else if (mode == HUNT) {
    const std::optional<rp::RenderParams>& p = params;   // only over the readout (no word, no banner)
    if (px.zone() == X::HOT && p && !p->word && !p->banner && p->dist_band) return T_PRESS_2X;
  } else if (mode == FOUND) {
    if (ticks_diff(t_ms, *found_t) >= T::FOUND_CELEBRATE_MS) return T_PRESS_BUTTON;
  }
  return nullptr;
}

// A deliberate tap or press that did nothing: its toast waits out the knock
// wait (resolve_ignored); at most one per IGNORED_TOAST_GAP_MS.
void Game::ignored(ticks_t t_ms, ticks_t td) {
  if (ign_t_ && ticks_diff(t_ms, *ign_t_) < T::IGNORED_TOAST_GAP_MS) return;
  if (ign_s_) return;
  const char* s = ignored_text(td);   // judged where the finger landed: a held celebrate tap stays silent
  if (s) {
    ign_s_ = s;
    ign_td_ = td;
    ign_knock_ = false;
  }
}

// Raise a waiting ignored-tap toast KNOCK_WAIT_MS after its touch landed,
// unless it was a knock: a partner spike in the knock window (or in HOT our
// own, which the felt-it check speaks for), latched as reports arrive.
void Game::resolve_ignored(ticks_t t_ms) {
  const char* s = ign_s_;
  if (!s) return;
  const ticks_t td = ign_td_;
  if (knock(peer.tap_t, td) || (mode == HUNT && knock(spike_t_, td))) ign_knock_ = true;
  if (ticks_diff(t_ms, td) < T::KNOCK_WAIT_MS) return;
  ign_s_ = nullptr;
  if (ign_knock_ || toast_on_ || menu.is_open || !screen_on || bye_t_ || ignored_text(t_ms) != s) return;
  ign_t_ = t_ms;
  toast_set(s, rp::Severity::INFO);
  if (s == T_SWIPE_HOWTO) howto_hinted_ = true;
}

// An ignored-tap or hint toast still up when its screen no longer calls for
// it is dropped (§8 Ignored taps). PRESS 2X TO SCAN also goes when a word
// takes the bottom slot (tick checks the frame); every way out of FOUND
// replaces or clears PRESS THE BUTTON already.
void Game::drop_stale_ignored() {
  if (toast_is(T_SWIPE_HOWTO)) {
    if (mode != PAIRING || pair.sub != P::LOOKING) toast_on_ = false;
  } else if (toast_is(T_PRESS_2X)) {
    if (mode != HUNT || px.zone() != X::HOT) toast_on_ = false;
  }
}

void Game::wake(ticks_t t_ms) {
  screen_on = true;
  wake_t_ = t_ms;
  input_t_ = t_ms;
  down_since_.reset();
  unseen_ = false;
}

// Event wake (ui-spec §8): the screen lights now and stays lit ms whatever the
// tilt. A dark screen wakes as on a wrist raise (boost, 300 ms touch filter)
// but keeps its wrist-down clock, so a wrist that stayed lowered goes dark
// when the hold ends, and it counts as unseen until the wrist is raised (a
// short press only wakes it). A later event extends the hold, never shortens
// it. At <= 5 % only FOUND (critical_ok) lights the screen (LOW-BATTERY:
// haptics carry the game); nothing while shutting down.
void Game::light(ticks_t t_ms, int32_t ms, bool critical_ok) {
  if (power_off || bye_t_) return;
  if (!critical_ok && critical()) return;
  if (!screen_on) {
    screen_on = true;
    unseen_ = true;
    wake_t_ = t_ms;
  }
  input_t_ = t_ms;
  const ticks_t u = ticks_add(t_ms, ms);
  if (!lit_until_ || ticks_diff(u, *lit_until_) > 0) lit_until_ = u;
}

// ---- haptics / toasts ----------------------------------------------------------

bool Game::blanked(ticks_t t_ms) {
  if (blank_.active(t_ms)) return true;
  return blank_fn && blank_fn(blank_ctx, t_ms);
}

void Game::emit(ticks_t t_ms, hp::Haptic name) {
  if (name == hp::NONE) return;
  if (menu.is_open) {   // paused under the menu: keep the strongest
    held_ = hp::stronger(held_, name);
    return;
  }
  hap_ = hp::stronger(hap_, name);
  if (buzz != BUZZ_OFF) blank_.extend(t_ms, hp::TOTAL_MS[name] + T::HAPTIC_BLANKING_MS);   // BUZZ OFF: nothing to blank
}

// Toast text; its TOAST_MS starts on the first tick it can be seen.
void Game::toast_set(const char* text, rp::Severity sev) {
  snprintf(toast_, sizeof toast_, "%s", text);
  toast_on_ = true;
  toast_sev_ = sev;
  toast_until_.reset();
}

void Game::hint_set(ticks_t t_ms, const char* text) {
  hint_ = text;
  hint_until_ = ticks_add(t_ms, T::HINT_CHIP_MS);
}

// The hint chip still showing, or nullptr.
const char* Game::hint_text(ticks_t t_ms) const {
  return hint_ && ticks_diff(hint_until_, t_ms) > 0 ? hint_ : nullptr;
}

// Start a waiting toast or SAVER ON interstitial once it can be seen: not
// under the MENU, a toast not during the sweep, the interstitial not in a scan.
void Game::show_pending(ticks_t t_ms) {
  if (menu.is_open) return;
  const bool scanning = mode == SCANNING;
  if (toast_on_ && !toast_until_ && !(scanning && scan.phase == SC::SWEEP)) toast_until_ = ticks_add(t_ms, T::TOAST_MS);
  if (inter_pending_ && !scanning) {
    inter_pending_ = false;
    inter_until_ = ticks_add(t_ms, T::BATT_INTERSTITIAL_MS);
    howto.reset();   // SAVER ON is never merged with a card
  }
}

// ---- per tick ------------------------------------------------------------------

const rp::RenderParams& Game::tick(ticks_t t_ms) {
  hap_ = hp::NONE;
  burst_ = false;
  if (!power_off) {
    resolve_held(t_ms);
    expire(t_ms);
    power(t_ms);
    battery_(t_ms);
    partner_left(t_ms);
    switch (mode) {
      case PAIRING: tick_pairing(t_ms); break;
      case SEARCHING: tick_searching(t_ms); break;
      case HUNT: tick_hunt(t_ms); break;
      case SCANNING: tick_scan(t_ms); break;
      case FOUND: tick_found(t_ms); break;
      case LINK_LOST: tick_lost(t_ms); break;
    }
    update_felt(t_ms);
    drop_stale_ignored();
    menu.tick(t_ms);
    const hp::Haptic h = held_;
    if (h != hp::NONE && !menu.is_open) {
      held_ = hp::NONE;
      emit(t_ms, h);
    }
    resolve_ignored(t_ms);
    show_pending(t_ms);
    if (mode != HUNT) peer_sweep_ = false;
    set_expected(t_ms);
  }
  state_byte = state_byte_(t_ms);
  menu.window(sun, buzz, indoor, theme);
  params = params_(t_ms);
  if (toast_is(T_PRESS_2X) && params->word) {
    toast_on_ = false;                    // BUMP!, HOLD STILL or an arrow word took the slot (§8)
    if (ign_t_ == t_ms) ign_t_.reset();   // raised this tick, never shown: no gap spent
    params = params_(t_ms);
  }
  return *params;
}

// Clear passed deadlines and cap old stamps (ticks_diff wraps).
void Game::expire(ticks_t t_ms) {
  if (toast_on_ && toast_until_ && ticks_diff(*toast_until_, t_ms) <= 0) toast_on_ = false;
  if (hint_ && ticks_diff(hint_until_, t_ms) <= 0) hint_ = nullptr;
  if (felt_ && ticks_diff(felt_until_, t_ms) <= 0) felt_ = nullptr;
  if (inter_until_ && ticks_diff(*inter_until_, t_ms) <= 0) inter_until_.reset();
  blank_.expire(t_ms);
  if (lit_until_ && ticks_diff(*lit_until_, t_ms) <= 0) lit_until_.reset();
  if (touch_block_ && ticks_diff(*touch_block_, t_ms) <= 0) touch_block_.reset();
  touches_.expire(t_ms, T::TOUCH_BURST_WINDOW_MS);
  if (spike_t_ && ticks_diff(t_ms, *spike_t_) > KNOCK_KEEP_MS) spike_t_.reset();
  if (touch_t_ && ticks_diff(t_ms, *touch_t_) > KNOCK_KEEP_MS) touch_t_.reset();
  if (ign_t_ && ticks_diff(t_ms, *ign_t_) >= T::IGNORED_TOAST_GAP_MS) ign_t_.reset();
  if (howto_shut_ && ticks_diff(t_ms, *howto_shut_) >= T::HOWTO_PRESS_GUARD_MS) howto_shut_.reset();
  look_t_ = cap(t_ms, look_t_);
  if (unrel_t_ && ticks_diff(t_ms, *unrel_t_) >= UNRELIABLE_PIN_MS) unrel_t_.reset();
  if (press_t_ && ticks_diff(t_ms, *press_t_) > T::FALLBACK_PRESS_WINDOW_MS) press_t_.reset();
  if (bump_t && ticks_diff(t_ms, *bump_t) > S::TAP_KEEP_MS) {
    bump_t.reset();
    tap_hot_ = false;
  }
  if (last_trend_t_ && ticks_diff(t_ms, *last_trend_t_) > LAST_TREND_KEEP_MS) {
    last_trend_ = 0;
    last_trend_t_.reset();
  }
  wake_t_ = cap(t_ms, wake_t_);
  input_t_ = cap(t_ms, input_t_);
  mode_t = cap(t_ms, mode_t);
  found_t = cap(t_ms, found_t);
  round_t0 = cap(t_ms, round_t0);
  down_since_ = cap(t_ms, down_since_);
  still_since_ = cap(t_ms, still_since_);
  br_since_ = cap(t_ms, br_since_);
  felt_t0_ = cap(t_ms, felt_t0_);
  hold_t_ = cap(t_ms, hold_t_);
  peer.expire(t_ms, STALE_MS);
  meter.expire(t_ms);   // rolls the delivery window: its stamp never wraps either
}

// The partner's beacon rate as it decides it (mirrors beacon_hz).
int32_t Game::peer_hz() const {
  if (peer.sweeping() || (mode == SCANNING && scan.active())) return T::BEACON_HZ_SCAN;
  if (peer.battery && *peer.battery <= T::BATT_CRITICAL_PCT) return T::BEACON_HZ_SAVER;
  if (peer.screen() == S::SC_HOT) return T::BEACON_HZ_HOT;
  return T::BEACON_HZ_NORMAL;
}

// Delivery meter rate: the lower of old/new for one window after a change.
void Game::set_expected(ticks_t t_ms) {
  const double hz = peer_hz();
  if (hz != hz_) {
    const double e = meter.expected_hz;
    hz_ = hz;
    hz_lo_ = hz < e ? hz : e;
    hz_t_ = t_ms;
  }
  if (hz_t_) {
    if (ticks_diff(t_ms, *hz_t_) < X::DELIVERY_WINDOW_MS) {
      meter.expected_hz = hz_lo_;
      return;
    }
    hz_t_.reset();
  }
  meter.expected_hz = hz;
}

void Game::update_px(ticks_t t_ms) {
  px.update_est(t_ms, *est, me.activity(), meter.ratio(t_ms));
  const int32_t tr = px.trend();
  if (tr) {
    last_trend_ = tr;
    last_trend_t_ = t_ms;
  }
  if (px.unreliable()) unrel_t_ = t_ms;
}

void Game::forget_trend() {
  last_trend_ = 0;
  last_trend_t_.reset();
  lost_trend = 0;
}

// TAP TO SCAN for 4 s on entering FAR or NEAR (no arrow shown, no other hint
// showing); the round's first FAR says FASTER IS CLOSER.
void Game::scan_hint(ticks_t t_ms) {
  if (arrow || !hint_free()) return;
  if (teach_far_ && px.zone() == X::FAR && !peer_sweep_ && !menu.is_open) {
    teach_far_ = false;
    hint_set(t_ms, T_FASTER_CLOSER);
  } else {
    hint_set(t_ms, T_TAP_TO_SCAN);
  }
}

// Past pairing, a fresh partner showing PAIRING for PARTNER_LEFT_MS has left
// the round (END ROUND, a restart): so does this watch.
void Game::partner_left(ticks_t t_ms) {
  if (mode == PAIRING || !peer.fresh(t_ms) || !peer_gone()) {
    left_t_.reset();
  } else if (!left_t_) {
    left_t_ = t_ms;
  } else if (ticks_diff(t_ms, *left_t_) >= T::PARTNER_LEFT_MS) {
    reset(t_ms);
    toast_set(T_FRIEND_LEFT, rp::Severity::WARN);
    emit(t_ms, hp::NOPE);
    light(t_ms, T::EVENT_LIT_MS);
  }
}

// PAIRING
void Game::tick_pairing(ticks_t t_ms) {
  if (pair.sub == P::LOOKING && peer.last_t) {
    peer.reset();
    meter.reset();
  }
  if ((pair.sub == P::SEEN || pair.sub == P::CONFIRMED) && bump_match(t_ms)) {
    consume_bump();
    pair.bump(t_ms);
  }
  const P::Sub was = pair.sub;
  emit(t_ms, pair.update(t_ms));
  if (pair.toast)
    toast_set(pair.toast, rp::Severity::INFO);   // CAL SKIPPED keeps the slot
  else if (was == P::CALIBRATE && pair.sub == P::SPLIT)
    toast_set(T_NEW_ROUND, rp::Severity::INFO);
  if (pair.sub == P::LOOKING) {
    looking_hint(t_ms);
  } else {   // the partner is seen: the cards close
    look_t_.reset();
    howto_hinted_ = false;
    howto_close(t_ms);
    if (pair.sub == P::CALIBRATE) howto_known_ = true;
  }
  if (pair.p1m && pair.p1m != p1m_) {
    p1m_ = pair.p1m;
    est->calibrate(*pair.p1m);
    restart_estimate();
  }
  if (pair.sub == P::SPLIT) {
    update_px(t_ms);
  } else if (pair.sub == P::DONE) {
    round_t0 = t_ms;
    teach_far_ = true;
    if (toast_is(T_NEW_ROUND)) toast_on_ = false;   // the split ended early: not into the hunt
    hint_set(t_ms, T_FIND_FRIEND);                  // before the zone hint: HOT's LOOK UP wins
    if (peer.live3(t_ms) && px.zone() && !peer_gone())
      enter_hunt(t_ms, false);
    else
      enter_searching(t_ms);
  }
}

// SWIPE: HOW TO PLAY once, HOWTO_HINT_MS into a looking spell, until the cards
// were opened or a round started since power-on; never over another toast,
// under the MENU or SAVER ON, or with the screen off (§6 PAIRING).
void Game::looking_hint(ticks_t t_ms) {
  if (!look_t_) {
    look_t_ = t_ms;
    return;
  }
  if (howto_hinted_ || howto_known_ || howto.open() || ticks_diff(t_ms, *look_t_) < T::HOWTO_HINT_MS) return;
  if (screen_on && !toast_on_ && !menu.is_open && !bye_t_ && !inter_until_ && !inter_pending_) {
    howto_hinted_ = true;
    toast_set(T_SWIPE_HOWTO, rp::Severity::INFO);
  }
}

// SEARCHING
void Game::enter_searching(ticks_t t_ms) {
  mode = SEARCHING;
  mode_t = t_ms;
  arrow = nullptr;
  forget_trend();
  est->reset();
  px.rearm();
}

void Game::tick_searching(ticks_t t_ms) {
  if (peer.live3(t_ms) && est->dist_m && !peer_gone()) {
    update_px(t_ms);
    if (px.zone()) enter_hunt(t_ms, true);
  }
}

// FAR..HOT
void Game::enter_hunt(ticks_t t_ms, bool fanfare) {
  mode = HUNT;
  mode_t = t_ms;
  const std::optional<int32_t> z = px.zone();
  input_t_ = t_ms;
  if (fanfare) {
    burst_ = true;
    emit(t_ms, hp::CLOSER);
  }
  if (z == X::FAR || z == X::NEAR)
    scan_hint(t_ms);
  else if (z == X::HOT)
    enter_hot(t_ms);
  br_since_.reset();
  bump_ready = false;
  br_fired_ = false;
}

void Game::tick_hunt(ticks_t t_ms) {
  const std::optional<int32_t> age = peer.age(t_ms);
  if (!age || *age > T::LINK_LOST_AFTER_MS) {
    enter_lost(t_ms);
    return;
  }
  update_px(t_ms);
  const std::optional<int32_t> z = px.zone();
  const int32_t ch = px.zone_changed();
  if (ch) {
    input_t_ = t_ms;
    if (ch > 0) {
      burst_ = true;
      emit(t_ms, hp::CLOSER);
      if (z == X::HOT)
        enter_hot(t_ms);
      else if (z == X::NEAR)
        scan_hint(t_ms);
    } else {
      emit(t_ms, hp::FARTHER);
      if (hint_ == T_LOOK_UP) hint_ = nullptr;
      if (z == X::FAR || z == X::NEAR) scan_hint(t_ms);
    }
  }
  if (teach_far_ && z == X::FAR && !hint_ && !peer_sweep_ && !menu.is_open)
    scan_hint(t_ms);   // the first FAR, once FIND YOUR FRIEND has gone
  update_arrow(t_ms, true);
  update_bump_ready(t_ms);
  const A::Arrow* a = arrow;
  if (bump_ready && a && a->glyph == rp::Glyph::ARROW && a->sub == rp::Sub::WALK)
    arrow = nullptr;   // the bump view takes it, silently (§6 HOT)
  update_still_hint(t_ms);
  update_peer_scan(t_ms);
  check_found(t_ms);
}

// HOT entry: LOOK UP for 4 s; on battery the screen lights (§8).
void Game::enter_hot(ticks_t t_ms) {
  hint_set(t_ms, T_LOOK_UP);
  light(t_ms, T::EVENT_LIT_MS);
}

void Game::update_arrow(ticks_t t_ms, bool link_ok) {
  A::Arrow* a = arrow;
  if (!a) return;
  const A::Phase was = a->phase;
  // under the MENU or SAVER ON (a waiting one starts this tick) the clock
  // pauses: the pacer must not run unseen, also on the tick the arrow comes back
  const bool hidden = menu.is_open || inter_until_.has_value() || inter_pending_;
  a->update(t_ms, me.activity(), me.steps(), px.trend(), peer.fresh(t_ms) && peer.walking(), link_ok, px.unreliable(),
            hidden);
  if (a->phase == A::PH_TURN && was != A::PH_TURN) mirror.reset(t_ms);
  emit(t_ms, a->haptic);
  if (a->toast) toast_set(a->toast, rp::Severity::INFO);
  if (a->done()) arrow = nullptr;
}

void Game::update_bump_ready(ticks_t t_ms) {
  if (px.zone() == X::HOT && px.band_idx == T::BUMP_READY_BAND) {
    if (!br_since_) br_since_ = t_ms;
    if (ticks_diff(t_ms, *br_since_) >= T::BUMP_READY_HOLD_MS) {
      bump_ready = true;
      if (!br_fired_ && friend_ready(t_ms)) {
        br_fired_ = true;   // "bump now": once both can count it
        emit(t_ms, hp::DOUBLE);
        light(t_ms, T::EVENT_LIT_MS);
      }
    }
  } else {
    br_since_.reset();
    bump_ready = false;
    br_fired_ = false;
  }
}

void Game::update_still_hint(ticks_t t_ms) {
  const std::optional<int32_t> z = px.zone();
  if (z != X::HOT && !arrow && me.activity() == ACT_STILL) {
    if (!still_since_) {
      still_since_ = t_ms;
    } else if (!still_done_ && hint_free() && ticks_diff(t_ms, *still_since_) >= T::HINT_STILL_MS) {
      still_done_ = true;
      hint_set(t_ms, T_TAP_TO_SCAN);
    }
  } else {
    still_since_.reset();
    still_done_ = false;
    if (z == X::WARM && hint_ == T_TAP_TO_SCAN) hint_ = nullptr;   // WARM: TAP TO SCAN only while still
  }
}

void Game::update_peer_scan(ticks_t t_ms) {
  const bool sw = peer.sweeping() && peer.fresh(t_ms);
  if (sw && !peer_sweep_) {
    hold_n_ = 1;
    hold_t_ = t_ms;
    emit(t_ms, hp::HOLD);
  } else if (sw && me.walking() && hold_n_ < T::SCAN_HOLD_REPEAT_MAX &&
             ticks_diff(t_ms, hold_t_) >= T::SCAN_HOLD_REPEAT_MS) {
    hold_n_ += 1;
    hold_t_ = t_ms;
    emit(t_ms, hp::HOLD);
  }
  peer_sweep_ = sw;
}

// bump / FOUND
bool Game::bump_match(ticks_t t_ms) const {
  const opt_ticks m = bump_t;
  const opt_ticks q = peer.tap_t;
  if (!m || !q || m == used_my_ || q == used_peer_) return false;
  int32_t d = ticks_diff(t_ms, *m);
  if (d < 0 || d > BUMP_FRESH_MS) return false;
  d = ticks_diff(*m, *q);
  return -T::BUMP_WINDOW_MS <= d && d <= T::BUMP_WINDOW_MS;
}

void Game::consume_bump() {
  used_my_ = bump_t;
  used_peer_ = peer.tap_t;
}

bool Game::pressed(ticks_t t_ms) const {
  return press_t_ && 0 <= ticks_diff(t_ms, *press_t_) && ticks_diff(t_ms, *press_t_) <= T::FALLBACK_PRESS_WINDOW_MS;
}

// The partner's watch can count a bump: heard within PEER_FRESH_MS and its
// screen in HOT or FOUND (the bump rule's own test, ui-spec §6 HOT).
bool Game::friend_ready(ticks_t t_ms) const {
  if (!peer.fresh(t_ms)) return false;
  const int32_t ps = peer.screen();
  return ps == S::SC_HOT || ps == S::SC_FOUND;
}

// Our last counted spike is within BUMP_WINDOW_MS of the partner's q.
bool Game::my_spiked(ticks_t q) const {
  if (!bump_t) return false;
  const int32_t d = ticks_diff(*bump_t, q);
  return -T::BUMP_WINDOW_MS <= d && d <= T::BUMP_WINDOW_MS;
}

bool Game::felt_ctx() const {
  if (mode == HUNT) return px.zone() == X::HOT;
  return mode == PAIRING && (pair.sub == P::SEEN || pair.sub == P::CONFIRMED);
}

// ONLY YOU FELT IT / FRIEND FELT IT: a spike the other watch did not report
// within BUMP_WINDOW_MS, judged KNOCK_WAIT_MS after it, so players learn how
// firm a bump must be (ui-spec §6 HOT). In HOT touches play no part. In
// PAIRING a spike a touch went with gets no verdict: an own spike with a
// finger landing in the knock window, or a partner spike after which its
// confirm turns on (judged FELT_CONFIRM_GRACE_MS later), was a confirming tap
// or a screen knock (§6 PAIRING).
void Game::update_felt(ticks_t t_ms) {
  if (!felt_ctx()) {
    if (felt_on_) {
      felt_on_ = false;
      fm_.reset();
      fq_.reset();
      felt_ = nullptr;
      if (toast_is(T_ONLY_YOU) || toast_is(T_FRIEND_FELT)) toast_on_ = false;
    }
    return;
  }
  const opt_ticks m = bump_t;
  const opt_ticks q = peer.tap_t;
  if (!felt_on_) {
    felt_on_ = true;
    felt_t0_ = t_ms;
    fm_seen_ = m;
    fq_seen_ = q;
  }
  const bool hot = mode == HUNT;
  if (m != fm_seen_) {
    fm_seen_ = m;
    if (m && ticks_diff(*m, felt_t0_) >= 0 && !peer_spiked(*m)) fm_ = m;
  }
  if (q != fq_seen_) {
    if (!q || ticks_diff(*q, felt_t0_) < 0 || ticks_diff(t_ms, *q) > BUMP_FRESH_MS) {
      fq_seen_ = q;   // none, or too old: never judged
    } else if (peer.tap_hot() || !hot) {
      fq_seen_ = q;
      if (!my_spiked(*q)) {
        fq_ = q;
        fq_conf_ = pair.peer_confirmed;
      }
    }   // else its ST_TAP_HOT can trail the tap count by a beacon: look again
  }
  const opt_ticks fm = fm_;
  if (fm) {
    if (peer_spiked(*fm)) {
      fm_.reset();
    } else if (ticks_diff(t_ms, *fm) >= T::KNOCK_WAIT_MS) {
      fm_.reset();   // HOT: FRIEND NOT READY already says why
      if (hot) {
        if (friend_ready(t_ms)) felt_say(t_ms, T_ONLY_YOU);
      } else if (fm != spike_touched_) {
        felt_say(t_ms, T_ONLY_YOU);
      }
    }
  }
  const opt_ticks fq = fq_;
  if (fq) {
    if (my_spiked(*fq)) {
      fq_.reset();
    } else if (hot) {
      if (ticks_diff(t_ms, *fq) >= T::KNOCK_WAIT_MS) {
        fq_.reset();
        felt_say(t_ms, T_FRIEND_FELT);
      }
    } else if (ticks_diff(t_ms, *fq) >= T::KNOCK_WAIT_MS + T::FELT_CONFIRM_GRACE_MS) {
      fq_.reset();
      if (fq_conf_ || !pair.peer_confirmed) felt_say(t_ms, T_FRIEND_FELT);
    }
  }
}

void Game::felt_say(ticks_t t_ms, const char* text) {
  if (mode == HUNT) {
    felt_ = text;
    felt_until_ = ticks_add(t_ms, T::TOAST_MS);
  } else {
    toast_set(text, rp::Severity::INFO);
  }
}

// bump_icons for the bump view: who felt the last knock (lit for
// BUMP_LIT_MS), and whether the friend's watch can count one.
int32_t Game::bump_icons(ticks_t t_ms, bool ready) const {
  int32_t v = 0;
  const opt_ticks m = bump_t;
  if (m && tap_hot_ && 0 <= ticks_diff(t_ms, *m) && ticks_diff(t_ms, *m) < T::BUMP_LIT_MS) v = rp::BI_ME;
  if (!ready) return v | rp::BI_FRIEND_OFF;
  const opt_ticks q = peer.tap_t;
  if (q && peer.tap_hot() && 0 <= ticks_diff(t_ms, *q) && ticks_diff(t_ms, *q) < T::BUMP_LIT_MS) v |= rp::BI_FRIEND;
  return v;
}

void Game::check_found(ticks_t t_ms) {
  if (px.zone() != X::HOT || !friend_ready(t_ms)) return;
  const int32_t ps = peer.screen();
  if (tap_hot_ && peer.tap_hot() && bump_match(t_ms)) {
    consume_bump();
    enter_found(t_ms);
    return;
  }
  // the §6 HOT fallback band rule; true in HOT while ZONE_BANDS[HOT] is (0, 1)
  const bool band_ok = px.band_idx && *px.band_idx <= T::FALLBACK_MAX_BAND;
  if (band_ok && pressed(t_ms) && peer.pressed()) {
    enter_found(t_ms);
    return;
  }
  if (ps == S::SC_FOUND) {
    const opt_ticks bt = bump_t;
    if ((tap_hot_ && bt && bt != used_my_ && 0 <= ticks_diff(t_ms, *bt) &&
         ticks_diff(t_ms, *bt) <= PEER_FOUND_TAP_MS) ||
        pressed(t_ms)) {
      consume_bump();
      enter_found(t_ms);
    }
  }
}

void Game::enter_found(ticks_t t_ms) {
  mode = FOUND;
  mode_t = t_ms;
  found_t = t_ms;
  arrow = nullptr;
  press_t_.reset();
  bump_ready = false;
  burst_ = true;
  emit(t_ms, hp::FOUND);
  int32_t s = round_t0 ? floordiv(ticks_diff(t_ms, *round_t0), 1000) : 0;
  S::fmt_found(s, found_word_, sizeof found_word_);
  if (s > T::FOUND_TIME_MAX_S) s = T::FOUND_TIME_MAX_S;
  snprintf(time_text_, sizeof time_text_, "TIME %d:%02d", (int)floordiv(s, 60), (int)floormod(s, 60));
  time_on_ = true;
  light(t_ms, T::FOUND_LIT_MS, true);
}

void Game::tick_found(ticks_t t_ms) {
  if (peer.fresh(t_ms) && peer.screen() == S::SC_PAIRED && ticks_diff(t_ms, *found_t) >= FOUND_FOLLOW_MS)
    new_round(t_ms);
}

void Game::new_round(ticks_t t_ms) {
  mode = PAIRING;
  mode_t = t_ms;
  pair.start_split(t_ms);
  arrow = nullptr;
  forget_trend();
  hint_ = nullptr;
  press_t_.reset();
  ign_s_ = nullptr;
  consume_bump();
  toast_set(T_NEW_ROUND, rp::Severity::INFO);
}

// SCANNING
void Game::start_scan(ticks_t t_ms) {
  if (peer.fresh(t_ms) && peer.sweeping()) {
    toast_set(T_FRIEND_SCANNING, rp::Severity::INFO);
    return;
  }
  stash_ = arrow && !arrow->done() ? arrow : nullptr;
  arrow = nullptr;
  rdown_.reset();
  hint_ = nullptr;
  scan.reset(t_ms);
  mode = SCANNING;
  mode_t = t_ms;
}

void Game::tick_scan(ticks_t t_ms) {
  const std::optional<int32_t> age = peer.age(t_ms);
  if (!age || *age > T::LINK_LOST_AFTER_MS) {
    scan.cancel(t_ms);
    unstash();
    enter_lost(t_ms);
    return;
  }
  if (stash_) {   // hidden: sigma keeps growing, the phase clock pauses
    stash_->update(t_ms, me.activity(), me.steps(), 0, false, true, px.unreliable(), true);
    if (stash_->done()) stash_ = nullptr;
  }
  double tilt = me.tilt_deg ? *me.tilt_deg : me.face_up ? 0.0 : 90.0;
  scan.on_motion(t_ms, tilt, (int32_t)me.steps(), me.activity());
  if (scan.phase == SC::READY) {
    if (me.face_up)
      rdown_.reset();
    else if (!rdown_)
      rdown_ = t_ms;
    if ((rdown_ && ticks_diff(t_ms, *rdown_) >= T::SCAN_READY_DOWN_MS) ||
        ticks_diff(t_ms, mode_t) >= SCAN_READY_MAX_MS)
      scan.cancel(t_ms);   // accidental tap: silent, the arrow comes back
  }
  if (peer.fresh(t_ms)) {
    scan.on_peer(t_ms, peer.walking());
    if (peer.sweeping() && scan.active() && my_mac && pair.peer_mac &&
        memcmp(my_mac->b, pair.peer_mac->b, sizeof my_mac->b) > 0) {
      scan.cancel(t_ms);   // lower MAC keeps its scan
      toast_set(T_FRIEND_SCANNING, rp::Severity::INFO);
    }
  }
  scan.update(t_ms);
  emit(t_ms, scan.pop_haptic());
  if (scan.phase == SC::SWEEP) stash_ = nullptr;   // the sweep started: the old arrow is spent
  if (scan.done(t_ms)) {
    const std::optional<SC::Result> r = scan.result();
    if (r) {
      std::optional<A::Arrow>& slot = arrows_[arrows_[0] && stash_ == &*arrows_[0] ? 1 : 0];
      slot = A::make(r->theta_deg, r->s0_deg, t_ms, arrow_mode);
      arrow = slot ? &*slot : nullptr;
      if (arrow) scans += 1;
    } else if (scan.reason != SC::R_CANCEL && scan.toast()) {
      toast_set(scan.toast(), rp::Severity::INFO);
    }
    mode = HUNT;
    mode_t = t_ms;
    still_since_.reset();
    unstash();
    update_arrow(t_ms, true);   // new or back: hidden if SAVER ON starts this tick
  }
}

// Put the arrow hidden by a scan back (unless it expired meanwhile).
void Game::unstash() {
  A::Arrow* st = stash_;
  stash_ = nullptr;
  if (st && !st->done()) arrow = st;
}

// LINK_LOST
void Game::enter_lost(ticks_t t_ms) {
  mode = LINK_LOST;
  mode_t = t_ms;
  lost_zone_ = px.zone();
  lost_band_ = px.band_idx;
  lost_i_ = px.intensity;
  lost_trend = last_trend_;   // 0 unless a trend in the last 30 s
  last_trend_ = 0;
  last_trend_t_.reset();
  bump_ready = false;
  hint_ = nullptr;
  lost_hint_ = false;
  emit(t_ms, hp::LOST);
  light(t_ms, T::EVENT_LIT_MS);
  est->reset();
  px.rearm();
  update_arrow(t_ms, false);
}

void Game::tick_lost(ticks_t t_ms) {
  update_arrow(t_ms, false);
  if (!lost_hint_) {
    const std::optional<int32_t> age = peer.age(t_ms);
    const int32_t el = age ? *age : ticks_diff(t_ms, mode_t) + T::LINK_LOST_AFTER_MS;
    // from the last packet; once shown it stays (a stray packet takes nothing back)
    lost_hint_ = el >= T::LOST_HINT_AFTER_MS;
  }
  if (peer.live3(t_ms) && est->dist_m && !peer_gone()) {
    update_px(t_ms);
    if (px.zone()) {
      enter_hunt(t_ms, true);
      toast_set(T_BACK, rp::Severity::INFO);
      update_arrow(t_ms, true);
    }
  }
}

rp::Banner Game::lost_banner() const {
  if (peer.goodbye) return sticky(T_FRIEND_OFF, rp::Severity::CRITICAL);
  if (peer.battery && *peer.battery <= T::BATT_BANNER_PCT) return sticky(T_FRIEND_LOW, rp::Severity::WARN);
  if (!lost_hint_) return sticky(T_SIGNAL_LOST, rp::Severity::WARN);
  return sticky(lost_trend > 0 ? T_LOST_KEEP_ON : T_LOST_GO_BACK, rp::Severity::WARN);
}

// MENU
// Long press (screen or side button): opens the MENU, or selects its row.
void Game::long_press(ticks_t t_ms) {
  if (menu.is_open) {
    menu_apply(t_ms, menu.select(t_ms, menu.sel));
    return;
  }
  if (mode == SCANNING) scan.cancel(t_ms);   // the menu freezes the field: no scan under it
  menu.open(t_ms);
}

// Apply the row action a Menu input returned (none: nothing to do).
void Game::menu_apply(ticks_t t_ms, std::optional<int> act) {
  if (!act) return;
  if (*act == menu::SUN)
    sun = !sun;
  else if (*act == menu::BUZZ)
    buzz = (buzz + 1) % (int)(sizeof menu::BUZZ_ROWS / sizeof menu::BUZZ_ROWS[0]);
  else if (*act == menu::PLACE)
    set_place(!indoor);
  else if (*act == menu::THEME)
    theme = (theme + 1) % rp::N_THEMES;
  else if (*act == menu::END)
    reset(t_ms);   // END ROUND confirmed: forget the partner
}

// power / battery
// Screen on with the wrist down: a scan (ready only while face-up) and the
// DIRECTION turn/face of a shown arrow.
bool Game::keep_on() const {
  if (mode == SCANNING) return scan.phase != SC::READY || me.face_up;
  return mode == HUNT && arrow && (arrow->phase == A::PH_TURN || arrow->phase == A::PH_FACE);
}

void Game::set_usb(ticks_t t_ms, bool on) {
  if (on && !usb && !screen_on && !power_off) wake(t_ms);
  usb = on;
}

// Tilted more than WRIST_DOWN_DEG from face-up (no tilt: not face-up).
bool Game::lowered() const {
  if (!me.tilt_deg) return !me.face_up;
  return *me.tilt_deg > T::WRIST_DOWN_DEG;
}

void Game::power(ticks_t t_ms) {
  const bool fu = me.face_up;
  if (fu && !fu_prev_ && !screen_on) wake(t_ms);   // a wrist raise on a lit screen is no new wake (§8)
  fu_prev_ = fu;
  if (!screen_on) return;
  if (!lowered()) unseen_ = false;   // the wrist is up: an event-lit screen has been seen
  if (usb || !lowered() || keep_on()) {
    down_since_.reset();
    return;
  }
  if (!down_since_) down_since_ = t_ms;
  if (lit_until_) return;   // event wake: lit whatever the tilt; the clock runs on (§8)
  const int32_t lim = critical() ? T::BATT_SCREEN_OFF_MS : T::WRIST_DOWN_MS;
  if (ticks_diff(t_ms, *down_since_) >= lim) screen_on = false;
}

void Game::battery_(ticks_t t_ms) {
  if (bye_t_) {   // shutting down: BYE and NOPE only, then power off
    const int32_t el = ticks_diff(t_ms, *bye_t_);
    if (el >= T::BYE_WORD_MS && (goodbye_left <= 0 || el >= T::BYE_WORD_MS + T::GOODBYE_GRACE_MS)) power_off = true;
    return;
  }
  char s[TEXT_N];
  if (battery) {
    const int32_t b = *battery;
    const int32_t lv = bat_level_;
    if (b <= T::BATT_SHUTDOWN_PCT) {
      menu.close();   // shutting down: BYE is shown, NOPE plays
      howto.reset();
      if (mode == SCANNING) scan.cancel(t_ms);   // and no sweep (20 Hz, F_SWEEP) runs on
      bye_t_ = t_ms;
      goodbye_left = T::GOODBYE_BEACONS;
      emit(t_ms, hp::NOPE);
      return;
    }
    if (b <= T::BATT_BANNER_PCT && lv > T::BATT_BANNER_PCT) {
      bat_level_ = T::BATT_BANNER_PCT;
      snprintf(s, sizeof s, "BATTERY %d%%", (int)T::BATT_BANNER_PCT);
      toast_set(s, rp::Severity::CRITICAL);
      emit(t_ms, hp::BATT);
    } else if (b <= T::BATT_CRITICAL_PCT && lv > T::BATT_CRITICAL_PCT) {
      bat_level_ = T::BATT_CRITICAL_PCT;
      inter_pending_ = true;
      emit(t_ms, hp::BATT);
    } else if (b <= T::BATT_WARN_PCT && lv > T::BATT_WARN_PCT) {
      bat_level_ = T::BATT_WARN_PCT;
      snprintf(s, sizeof s, "BATTERY %d%%", (int)T::BATT_WARN_PCT);
      toast_set(s, rp::Severity::WARN);
      emit(t_ms, hp::BATT);
    }
    const int32_t r = BATT_REARM_PCT;
    const int32_t rec = b > T::BATT_WARN_PCT + r       ? 100
                        : b > T::BATT_CRITICAL_PCT + r ? T::BATT_WARN_PCT
                        : b > T::BATT_BANNER_PCT + r   ? T::BATT_CRITICAL_PCT
                                                       : bat_level_;
    if (rec > bat_level_) bat_level_ = rec;
  }
  if (peer.battery) {
    const int32_t pb = *peer.battery;
    if (pb <= T::BATT_WARN_PCT && !peer_bat_warned_) {
      peer_bat_warned_ = true;
      snprintf(s, sizeof s, "FRIEND BATT %d%%", (int)T::BATT_WARN_PCT);
      toast_set(s, rp::Severity::WARN);
    } else if (pb > T::BATT_WARN_PCT + BATT_REARM_PCT) {
      peer_bat_warned_ = false;
    }
  }
}

// ---- beacon --------------------------------------------------------------------

const char* Game::screen_() const { return mode == HUNT ? T::ZONE_NAMES[*px.zone()] : name(mode); }

int32_t Game::state_byte_(ticks_t t_ms) const {
  if (bye_t_) return S::SC_BYE | S::ST_GOODBYE;
  int32_t s;
  if (mode == PAIRING && (pair.sub == P::CALIBRATE || pair.sub == P::SPLIT))
    s = S::SC_PAIRED;   // already paired: never a pairing candidate
  else
    s = S::screen_code(screen_());
  if (mode == HUNT && px.zone() == X::HOT && pressed(t_ms)) s |= S::ST_PRESS;
  if (mode == PAIRING && pair.confirmed) s |= S::ST_CONFIRMED;
  if (tap_hot_) s |= S::ST_TAP_HOT;
  return s;
}

// Beacon rate (§5.8): 20 Hz in HOT and on both watches during a scan, 5 in saver.
int32_t Game::beacon_hz() const {
  if ((mode == SCANNING && scan.active()) || peer_sweep_) return T::BEACON_HZ_SCAN;
  if (saver()) return T::BEACON_HZ_SAVER;
  if (mode == HUNT && px.zone() == X::HOT) return T::BEACON_HZ_HOT;
  return T::BEACON_HZ_NORMAL;
}

proto::Beacon& Game::fill_beacon(proto::Beacon& b, ticks_t now) {
  b.state = state_byte;
  b.set_flags(mode == SCANNING && scan.active(), taps, me.walking(), mode == PAIRING && pair.sub == P::SPLIT && pair.ready);
  b.rssi_last = proto::clamp_i8(rssi_last);
  b.rssi_filt = proto::clamp_i8(est->rssi_f);
  b.steps = (int32_t)(me.steps() & 0xFFFF);
  b.activity = me.activity();
  b.battery = battery ? *battery : proto::BATT_UNKNOWN;
  b.set_bump(now, bump_t);
  if (bye_t_ && goodbye_left > 0) goodbye_left -= 1;
  return b;
}

// ---- RenderParams --------------------------------------------------------------

double Game::backlight(ticks_t t_ms) const {
  if (!screen_on) return 0.0;
  if (sun) return T::BACKLIGHT_BOOST;
  if (saver()) return T::SAVER_BACKLIGHT;
  if (ticks_diff(t_ms, wake_t_) < T::WAKE_BOOST_MS) return T::BACKLIGHT_BOOST;
  if (!keep_on() && me.face_up && ticks_diff(t_ms, input_t_) >= T::IDLE_DIM_MS) return T::IDLE_DIM_BACKLIGHT;
  return T::BACKLIGHT_NORMAL;
}

rp::Status Game::status(ticks_t t_ms, const char* scr) {
  rp::Status st;
  int32_t own = battery ? *battery : 100;
  st.own_pct = own < 0 ? 0 : own > 100 ? 100 : own;
  st.partner_pct = peer.battery;
  if (st.partner_pct && *st.partner_pct > 100) st.partner_pct = 100;
  const std::optional<int32_t> age = peer.age(t_ms);
  if (!age || *age > T::LIVE_WINDOW_MS || !pair.peer_mac) {
    st.link_q = 0;
  } else {
    const int32_t q = (int32_t)(meter.ratio(t_ms) * T::LINK_Q_MAX + 0.5);
    st.link_q = q < 0 ? 0 : q > T::LINK_Q_MAX ? T::LINK_Q_MAX : q;
  }
  const bool pinned = st.own_pct <= T::BATT_WARN_PCT;
  const bool zone = in_zone_names(scr);
  if (strcmp(scr, "SEARCHING") == 0 || zone) {
    st.unreliable = zone && unrel_t_ && ticks_diff(t_ms, *unrel_t_) < UNRELIABLE_PIN_MS;
    st.visible = pinned || st.unreliable || ticks_diff(t_ms, wake_t_) < T::STATUS_AFTER_WAKE_MS;
  } else if (strcmp(scr, "LINK_LOST") == 0) {
    st.visible = pinned;
  }
  return st;
}

rp::RenderParams Game::params_(ticks_t t_ms) {
  rp::RenderParams p;
  const std::optional<int32_t> age = peer.age(t_ms);
  bool live = age && *age <= T::LIVE_WINDOW_MS;
  const char* scr = screen_();
  rp::Sub sub = rp::Sub::NONE;
  std::optional<int32_t> zone;
  Ramp ramp = rp::ramp_of("green");
  double inten = px.intensity;
  double speed = 0.0;
  int32_t period = BREATHE_MS;
  double glow_r = glow(inten);
  rp::Glyph glyph = rp::Glyph::GLOW;
  std::optional<double> adeg, cone;
  rp::ArrowStyle style = rp::ArrowStyle::NONE;
  int32_t trend = 0;
  bool strong = false;
  std::optional<int32_t> cd;
  std::optional<int32_t> band;
  bool stale = false;
  const char* word = nullptr;
  const char* top = nullptr;
  std::optional<rp::Banner> banner;
  std::optional<rp::Sweep> sweep;
  hp::Haptic hb = hp::NONE;
  int32_t every = 1;
  std::optional<rp::Runes> runes;
  std::optional<int32_t> bump;
  if (mode == PAIRING) {
    glyph = rp::Glyph::RUNES;
    if (pair.runes) {
      rp::Runes r;
      for (int k = 0; k < 3; k++) r.ids[k] = (*pair.runes)[k];
      runes = r;
    }
    inten = FP_INTEN;
    glow_r = FP_GLOW;
    if (pair.sub == P::LOOKING) {
      sub = rp::Sub::LOOKING;
      speed = FP_SPEED;
      period = FP_PERIOD;
      live = false;   // no partner yet: no live rings
      if (howto.card) {
        const howto::Card& c = howto::CARDS[howto.card - 1];
        sub = rp::Sub::HOWTO;
        top = c.top_text;
        word = c.word;
        glyph = c.glyph;
        runes = c.runes;
        cd = c.countdown;
        trend = c.trend;
        bump = c.bump_icons;
      } else {
        top = T_START_OTHER;
        word = "LOOKING";
      }
    } else if (pair.sub == P::SEEN) {
      sub = rp::Sub::SEEN;
      top = "SAME RUNES?";
      word = W_BUMP_YES;
    } else if (pair.sub == P::CONFIRMED) {
      sub = rp::Sub::CONFIRMED;
      top = T_WAITING_FRIEND;
      word = W_YOURE_IN;
    } else if (pair.sub == P::CALIBRATE) {
      sub = rp::Sub::CALIBRATE;
      glyph = rp::Glyph::COUNTDOWN;
      cd = pair.countdown;
      top = pair.unstable ? P::HINT_HOLD_STILL : "STAND 1 STEP APART";
      word = W_HOLD_STILL;
    } else {
      sub = pair.sub == P::SPLIT ? rp::Sub::SPLIT : rp::Sub::NONE;
      glyph = rp::Glyph::COUNTDOWN;
      cd = pair.countdown;
      if (pair.ready) {
        top = pair.peer_ready ? "BOTH READY" : "WAITING FOR FRIEND";
        word = "READY";
      } else {
        if (pair.peer_ready)
          top = "FRIEND READY";
        else if (ticks_diff(t_ms, pair.t_split) >= T::PAIR_READY_HINT_MS)
          top = "TAP WHEN READY";
        else
          top = "NO PEEKING";
        word = "SPLIT UP";
      }
      if (pair.go()) word = "GO";
      zone = px.zone();
      if (zone) {
        speed = T::ZONE_SPEED_PX_S[*zone];
        period = T::ZONE_PERIOD_MS[*zone];
        inten = px.intensity;
        glow_r = glow(inten);
      } else {
        speed = FP_SPEED;
        period = FP_PERIOD;
      }
    }
  } else if (mode == SEARCHING) {
    ramp = rp::ramp_of(T::FIELD_SEARCHING_0);
    inten = T::FIELD_SEARCHING_1;
    speed = T::FIELD_SEARCHING_2;
    period = T::FIELD_SEARCHING_3;
    glow_r = T::FIELD_SEARCHING_4;
    glyph = rp::Glyph::SEEKER;
    live = false;
    word = ticks_diff(t_ms, mode_t) >= T::SEARCHING_WALK_ABOUT_MS ? W_WALK_ABOUT : W_SEARCHING;
    top = hint_text(t_ms);   // FIND YOUR FRIEND as the round starts
  } else if (mode == HUNT) {
    const int32_t z = *px.zone();
    zone = z;
    speed = T::ZONE_SPEED_PX_S[z];
    period = T::ZONE_PERIOD_MS[z];
    band = px.band_idx;
    trend = px.trend();
    strong = px.trend_strong() && trend != 0;
    const A::Arrow* a = arrow;
    if (!a || !(a->phase == A::PH_TURN || a->phase == A::PH_FACE)) {
      hb = rp::ZONE_HEARTBEAT[z];   // turn/face: only the pacer ticks; HOT: none,
      every = T::ZONE_HB_EVERY[z];  // a pulse would blank the knock (§6)
    }
    if (a && a->phase == A::PH_TURN) {
      glow_r = T::FIELD_SCAN_SWEEP_GLOW_R;   // the live-mirror halo (§5.7)
      if (mirror.value) inten = *mirror.value;
    }
    const bool arrow_on = a && a->glyph == rp::Glyph::ARROW;
    const bool ready = friend_ready(t_ms);
    if (bump_ready && !arrow_on) {
      glyph = rp::Glyph::BUMP;   // a walk arrow was dropped for it (§6 HOT)
      bump = bump_icons(t_ms, ready);
    } else if (arrow_on) {
      glyph = rp::Glyph::ARROW;
      adeg = a->arrow_deg;
      cone = a->cone_deg;
      style = a->arrow_style;
      sub = a->sub;
      word = a->word;
      top = a->top_text;
      sweep = a->sweep;
    } else if (trend) {
      glyph = rp::Glyph::CHEVRONS;
    }
    if (sub != rp::Sub::TURN) {
      if (peer_sweep_) {
        if (!top) top = T_FRIEND_SCANNING;
        if (!word) word = W_HOLD_STILL;
      }
      if (!top && felt_) top = felt_;
      if (bump_ready) {
        if (!ready) {
          if (!top) top = T_FRIEND_NOT_READY;
        } else {
          if (!word) word = W_BUMP;
          if (!top) top = T_BUMP_WRISTS;
        }
      }
      if (!top) top = hint_text(t_ms);
    }
  } else if (mode == SCANNING) {
    sub = SUBS[scan.sub()];
    zone = px.zone();
    const int32_t z = zone ? *zone : X::FAR;
    period = T::ZONE_PERIOD_MS[z];
    const std::optional<double> mir = scan.mirror();
    if (sub == rp::Sub::READY) {
      speed = T::ZONE_SPEED_PX_S[z];
      glow_r = T::FIELD_SCAN_READY_GLOW_R;
      glyph = rp::Glyph::COUNTDOWN;
      cd = scan.countdown;
      top = scan.top_text();
      word = scan.word();
    } else {
      if (mir) inten = *mir;
      glow_r = T::FIELD_SCAN_SWEEP_GLOW_R;
      const std::optional<SC::Sweep> sw = scan.sweep(t_ms);
      if (sw) {
        rp::Sweep s;   // a copy: the session's bins change in place
        s.wedge_deg = sw->wedge_deg;
        for (int k = 0; k < SC::BINS; k++) s.bins[k] = sw->bins[k];
        s.active_bin = sw->active_bin;
        s.paused = sw->paused;
        sweep = s;
        glyph = rp::Glyph::TURN;
      }
    }
  } else if (mode == FOUND) {
    sub = ticks_diff(t_ms, *found_t) < T::FOUND_CELEBRATE_MS ? rp::Sub::CELEBRATE : rp::Sub::RESULT;
    zone = X::HOT;
    ramp = rp::ramp_of("gold");
    inten = 1.0;
    period = T::FOUND_PERIOD_MS;
    glow_r = T::GLOW_R_FOUND;
    glyph = rp::Glyph::CHECK;
    if (sub == rp::Sub::CELEBRATE) {
      if (time_on_) top = time_text_;
      word = W_FOUND;
    } else {
      top = T_PLAY_AGAIN;
      if (time_on_) word = found_word_;
    }
  } else {   // LINK_LOST
    ramp = rp::ramp_of(T::FIELD_LINK_LOST_0);
    speed = T::FIELD_LINK_LOST_2;
    period = T::FIELD_LINK_LOST_3;
    glow_r = T::FIELD_LINK_LOST_4;
    zone = lost_zone_;
    inten = lost_i_;
    glyph = rp::Glyph::SEEKER;
    live = false;
    band = lost_band_;
    stale = band.has_value();
    trend = lost_trend;   // the LAST chip's last-trend mark (§6)
    banner = lost_banner();
  }
  // modifiers: low-battery interstitial, goodbye word, toasts
  if (inter_until_ && ticks_diff(*inter_until_, t_ms) > 0 && mode != SCANNING) {
    glyph = rp::Glyph::BATTERY;
    adeg.reset();
    cone.reset();
    style = rp::ArrowStyle::NONE;
    bump.reset();
    if (sub == rp::Sub::REVEAL || sub == rp::Sub::TURN || sub == rp::Sub::WALK) sub = rp::Sub::NONE;
    sweep.reset();
    cd.reset();
    word = W_SAVER;
  }
  if (bye_t_) {
    word = W_BYE;
    hb = hp::NONE;
  }
  if (!banner && toast_on_ && toast_until_) {
    rp::Banner b;
    b.text = rp::Text(toast_);
    b.severity = toast_sev_;
    b.sticky = false;
    banner = b;
  }
  hp::Haptic haptic = hap_;
  if (buzz != BUZZ_FULL) hb = hp::NONE;
  if (buzz == BUZZ_OFF) haptic = hp::NONE;
  if (hb == hp::NONE) every = 1;
  p.status = status(t_ms, scr);
  p.screen = rp::Screen::SEARCHING;
  rp::from_name(scr, &p.screen);
  if (menu.is_open) {
    p.screen = rp::Screen::MENU;
    rp::from_name(menu.sub(), &sub);
    rp::MenuRows rows;
    for (int k = 0; k < rp::MENU_VISIBLE; k++) rows.rows[k] = rp::Text(menu.rows[k]);
    p.menu_rows = rows;
    runes.reset();
    bump.reset();
    glyph = rp::Glyph::GLOW;
    adeg.reset();
    cone.reset();
    style = rp::ArrowStyle::NONE;
    trend = 0;
    strong = false;
    cd.reset();
    band.reset();
    stale = false;
    word = top = nullptr;
    banner.reset();
    sweep.reset();
    haptic = hb = hp::NONE;
    every = 1;
  }
  if (inten < 0.0)
    inten = 0.0;
  else if (inten > 1.0)
    inten = 1.0;
  p.t_ms = t_ms;
  p.sub = sub;
  p.zone = zone;
  p.ramp = ramp;
  p.intensity = inten;
  p.speed_px_s = speed;
  p.pulse_period_ms = period;
  p.wavelength_px = rp::wavelength(speed, period);
  p.glow_r_px = glow_r;
  p.ring_live = live;
  p.burst = burst_;
  p.glyph = glyph;
  p.arrow_deg = adeg;
  p.cone_deg = cone;
  p.arrow_style = style;
  p.trend = trend;
  p.trend_strong = strong;
  p.countdown = cd;
  p.runes = runes;
  p.bump_icons = bump;
  p.dist_band = band;
  p.dist_stale = stale;
  p.word = rp::opt_text(word);
  p.top_text = rp::opt_text(top);
  p.banner = banner;
  p.sweep = sweep;
  p.haptic = haptic;
  p.heartbeat = hb;
  p.heartbeat_every = every;
  p.backlight = backlight(t_ms);
  p.fps_cap = saver() ? T::SAVER_FPS : T::FPS_TARGET;
  p.sun = sun;
  p.theme = theme;
  return p;
}

}  // namespace game
}  // namespace hm
