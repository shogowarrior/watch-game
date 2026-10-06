// finder/game.py: one watch's game, the state machine of ui-spec §6-8.
//
//     PAIRING (looking/seen/confirmed/calibrate/split) -> SEARCHING | FAR..HOT
//     FAR..HOT (+ DIRECTION arrow, + trend chevrons) <-> SCANNING
//     HOT --bump--> FOUND --tap/press--> PAIRING split (new round)
//     FAR..HOT/SCANNING --5 s silence--> LINK_LOST --3 packets/2 s--> zone
//     past pairing --partner in PAIRING for 2 s--> PAIRING looking
//     MENU is an overlay over any screen; LOW-BATTERY is a modifier.
//
// Pure logic, fed by the main loop (or a test) as the Python is; read the
// Python module docstring for the rules and the spec-silent choices. FOUND
// never comes from RSSI: only a matched bump or the two-press fallback.
//
// Python None: times and numbers are std::optional, the arrow a null pointer,
// a haptic name hp::NONE. Strings the Python builds ("TIME 1:05", "BATTERY
// 20%") are formatted into member buffers; every other text is a static
// literal, compared by pointer. No allocation after construction.
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/arrow.h"
#include "hm/compat.h"
#include "hm/estimator.h"
#include "hm/gestures.h"
#include "hm/haptic_patterns.h"
#include "hm/kalman2.h"
#include "hm/menu.h"
#include "hm/motion.h"
#include "hm/pairing.h"
#include "hm/proto.h"
#include "hm/proximity.h"
#include "hm/py.h"
#include "hm/render_params.h"
#include "hm/scan.h"
#include "hm/session.h"
#include "hm/tuning.h"

namespace hm {
namespace game {

namespace hp = hm::haptic_patterns;
namespace rp = hm::render_params;

// game modes (MENU is an overlay flag); name() gives the Python's M_* string
enum Mode : int8_t { PAIRING, SEARCHING, HUNT, SCANNING, FOUND, LINK_LOST };
const char* name(Mode m);

constexpr int BUZZ_FULL = hp::MODE_FULL, BUZZ_OFF = hp::MODE_OFF;

constexpr int32_t BUMP_FRESH_MS = 2000;        // own tap older than this never matches
constexpr int32_t KNOCK_KEEP_MS = 2000;        // a spike older than this makes no touch a knock's
constexpr int32_t PEER_FOUND_TAP_MS = 2000;    // partner already FOUND: follow if we tapped this recently
constexpr int32_t UNRELIABLE_PIN_MS = 3000;    // StatusStrip stays pinned this long after 'unreliable' clears
constexpr int32_t FOUND_FOLLOW_MS = 500;       // FOUND at least this long before following a new round
constexpr int32_t BATT_REARM_PCT = 3;          // a threshold re-arms once charged this far above it
constexpr int32_t LAST_TREND_KEEP_MS = 30000;  // a trend older than this at link loss counts as none
constexpr int32_t STALE_MS = 21600000;         // "since" stamps never age past 6 h
constexpr int32_t SCAN_READY_MAX_MS = 15000;   // scan ``ready`` never flat within this: silent cancel

constexpr const char* W_SEARCHING = "SEARCHING";
constexpr const char* W_WALK_ABOUT = "WALK ABOUT";
constexpr const char* W_HOLD_STILL = "HOLD STILL";
constexpr const char* W_BUMP = "BUMP!";
constexpr const char* W_FOUND = "FOUND";
constexpr const char* W_AGAIN = "TAP=AGAIN";
constexpr const char* W_SAVER = "SAVER ON";
constexpr const char* W_BYE = "BYE";
constexpr const char* T_TAP_TO_SCAN = "TAP TO SCAN";
constexpr const char* T_LOOK_AROUND = "LOOK AROUND";
constexpr const char* T_TAP_WATCHES = "TAP WATCHES";
constexpr const char* T_FRIEND_SCANNING = "FRIEND SCANNING";
constexpr const char* T_BACK = "BACK IN RANGE";
constexpr const char* T_FRIEND_OFF = "FRIEND IS OFF";
constexpr const char* T_FRIEND_LOW = "FRIEND LOW BATTERY";
constexpr const char* T_FRIEND_LEFT = "FRIEND LEFT";

// Accelerometer blanking from outside the game (app/imu_feed.py's blanked).
using BlankFn = bool (*)(void* ctx, ticks_t t_ms);

class Game {
 public:
  // est: the range estimator (nullptr: the game's own default, kalman2).
  explicit Game(std::optional<Mac> my_mac = std::nullopt, est::RangeEstimator* est = nullptr,
                arrow::Mode arrow_mode = arrow::MODE_GUIDED, BlankFn blank_fn = nullptr, void* blank_ctx = nullptr,
                ticks_t t_ms = 0, std::optional<int32_t> battery = 100);
  Game(const Game&) = delete;
  Game& operator=(const Game&) = delete;

  // ---- lifecycle
  void set_place(bool indoor);   // outdoor (default) or indoor/crowded: the path-loss exponent
  void reset(ticks_t t_ms);      // everything back to PAIRING looking (forget the partner)
  void restart_estimate();       // forget the range estimate and proximity

  // ---- inputs
  void set_motion(ticks_t t_ms, int32_t activity, uint32_t steps = 0, double step_rate_hz = 0.0,
                  std::optional<double> tilt_deg = std::nullopt, bool face_up = true);
  void set_tracker(ticks_t t_ms, const motion::MotionTracker& mt) { me.from_tracker(t_ms, mt); }
  void set_battery(ticks_t, std::optional<int32_t> pct) { battery = pct; }
  void set_usb(ticks_t t_ms, bool on);
  void on_packet(ticks_t t_ms, const Mac& mac, double rssi, const proto::Beacon& b);
  bool on_accel_tap(ticks_t t_ms);   // true if accepted as a bump tap
  bool bump_armed() const;           // a bump spike can count now: sample fast
  void on_touch_down(ticks_t t_ms);
  void on_wake(ticks_t t_ms);
  void on_gesture(ticks_t t_ms, int code, int32_t x = 120, int32_t y = 120, opt_ticks t_down = std::nullopt);
  void on_button(ticks_t t_ms, bool long_ = false);
  bool blanked(ticks_t t_ms);

  // ---- per tick (10 Hz): advance the logic; returns this frame's RenderParams (also in params)
  const rp::RenderParams& tick(ticks_t t_ms);

  // ---- beacon
  proto::Beacon& fill_beacon(proto::Beacon& b, ticks_t now);   // this watch's fields, at transmit time
  int32_t beacon_hz() const;
  bool saver() const { return battery && *battery <= T::BATT_CRITICAL_PCT; }
  bool menu_open() const { return menu.is_open; }
  const char* screen() const { return menu.is_open ? "MENU" : screen_(); }

  std::optional<Mac> my_mac;
  est::RangeEstimator* est;
  bool indoor = false;
  arrow::Mode arrow_mode;
  BlankFn blank_fn;
  void* blank_ctx;
  pairing::Pairing pair;
  proximity::Proximity px;
  proximity::DeliveryMeter meter;
  scan::ScanSession scan;
  session::PeerView peer;
  session::MotionSnap me;
  session::LiveMirror mirror;
  menu::Menu menu;
  std::optional<int32_t> battery;
  bool sun = false;
  int buzz = BUZZ_FULL;
  std::optional<rp::RenderParams> params;
  int32_t goodbye_left = 0;
  bool power_off = false;
  bool screen_on = true;
  bool usb = false;   // on USB power: screen kept on (set_usb)
  Mode mode = PAIRING;
  ticks_t mode_t = 0;
  arrow::Arrow* arrow = nullptr;   // the shown arrow (in arrows_ unless a caller put one in)
  std::optional<double> rssi_last;
  opt_ticks bump_t;
  int32_t taps = 0;
  int32_t lost_trend = 0;   // last trend at link loss (LINK_LOST hint / mark)
  bool bump_ready = false;
  opt_ticks round_t0;
  opt_ticks found_t;
  int32_t scans = 0;        // completed scans with a fix (arrows earned)
  int32_t state_byte = session::SC_PAIRING;

 private:
  struct Held {             // a gesture waiting on the partner's word (knock or finger)
    int code;
    int32_t x, y;
    ticks_t t_down, spike;
  };
  static constexpr int TEXT_N = rp::TEXT_MAX + 1;

  static bool scan_blank(void* self, ticks_t t_ms) { return static_cast<Game*>(self)->blanked(t_ms); }
  bool knock(ticks_t t_down) const;
  bool peer_spiked(ticks_t s) const;
  void resolve_held(ticks_t t_ms, bool now = false);
  void gesture(ticks_t t_ms, int code, int32_t x, int32_t y, ticks_t td);
  void touch_burst(ticks_t t_ms);
  void primary(ticks_t t_ms, bool button);
  void wake(ticks_t t_ms);
  void emit(ticks_t t_ms, hp::Haptic name);
  void toast_set(const char* text, rp::Severity sev = rp::Severity::INFO);
  void hint_set(ticks_t t_ms, const char* text);
  void show_pending(ticks_t t_ms);
  void expire(ticks_t t_ms);
  int32_t peer_hz() const;
  void set_expected(ticks_t t_ms);
  void update_px(ticks_t t_ms);
  void forget_trend();
  void scan_hint(ticks_t t_ms);
  bool peer_gone() const { return peer.screen() == session::SC_PAIRING; }
  void partner_left(ticks_t t_ms);
  void tick_pairing(ticks_t t_ms);
  void enter_searching(ticks_t t_ms);
  void tick_searching(ticks_t t_ms);
  void enter_hunt(ticks_t t_ms, bool fanfare);
  void tick_hunt(ticks_t t_ms);
  void update_arrow(ticks_t t_ms, bool link_ok, bool hidden = false);
  void update_bump_ready(ticks_t t_ms);
  void update_still_hint(ticks_t t_ms);
  void update_peer_scan(ticks_t t_ms);
  bool bump_match(ticks_t t_ms) const;
  void consume_bump();
  bool pressed(ticks_t t_ms) const;
  void check_found(ticks_t t_ms);
  void enter_found(ticks_t t_ms);
  void tick_found(ticks_t t_ms);
  void new_round(ticks_t t_ms);
  void start_scan(ticks_t t_ms);
  void tick_scan(ticks_t t_ms);
  bool unstash();
  void enter_lost(ticks_t t_ms);
  void tick_lost(ticks_t t_ms);
  rp::Banner lost_banner(ticks_t t_ms) const;
  void long_press(ticks_t t_ms);
  void menu_apply(ticks_t t_ms, std::optional<int> act);
  bool keep_on() const;
  bool lowered() const;
  void power(ticks_t t_ms);
  bool critical() const { return battery && *battery <= T::BATT_BANNER_PCT; }
  void battery_(ticks_t t_ms);
  const char* screen_() const;
  int32_t state_byte_(ticks_t t_ms) const;
  double backlight(ticks_t t_ms) const;
  rp::Status status(ticks_t t_ms, const char* scr);
  rp::RenderParams params_(ticks_t t_ms);

  est::Kalman2 own_est_;
  std::optional<arrow::Arrow> arrows_[2];   // the shown arrow and one stashed under a scan
  hp::BlankWindow blank_;
  TickRing<T::TOUCH_BURST_COUNT> touches_;
  int32_t bat_level_ = 100;         // battery ladder: survives END ROUND
  opt_ticks inter_until_;
  bool inter_pending_ = false;      // SAVER ON raised, not yet seen
  opt_ticks bye_t_;
  ticks_t wake_t_;
  opt_ticks down_since_;
  bool fu_prev_ = true;
  arrow::Arrow* stash_ = nullptr;   // arrow hidden under a scan (restored if it ends in ready)
  opt_ticks rdown_;                 // scan ready: face-down since
  std::optional<double> p1m_;
  bool tap_hot_ = false;
  opt_ticks used_my_, used_peer_;
  opt_ticks press_t_;
  opt_ticks spike_t_;               // last counted accelerometer spike (knock touches)
  std::optional<Held> held_g_;
  opt_ticks touch_block_;
  hp::Haptic hap_ = hp::NONE;
  bool burst_ = false;
  bool toast_on_ = false;
  char toast_[TEXT_N] = {};
  rp::Severity toast_sev_ = rp::Severity::INFO;
  opt_ticks toast_until_;           // None while the toast waits to be seen
  const char* hint_ = nullptr;
  ticks_t hint_until_ = 0;
  int32_t last_trend_ = 0;
  opt_ticks last_trend_t_;
  opt_ticks left_t_;                // partner back in PAIRING since
  hp::Haptic held_ = hp::NONE;      // event haptic held while the MENU is open
  double hz_ = 0, hz_lo_ = 0;
  opt_ticks hz_t_;
  opt_ticks unrel_t_;
  opt_ticks br_since_;
  bool br_fired_ = false;
  opt_ticks still_since_;
  bool still_done_ = false;
  bool peer_sweep_ = false;
  int32_t hold_n_ = 0;
  ticks_t hold_t_ = 0;
  bool time_on_ = false;
  char time_text_[TEXT_N] = {};
  std::optional<int32_t> lost_zone_;
  std::optional<int32_t> lost_band_;
  double lost_i_ = 0.0;
  ticks_t input_t_ = 0;
  bool peer_bat_warned_ = false;

  friend struct Probe;   // the trace test: a Python test's call of a private method
};

}  // namespace game
}  // namespace hm
