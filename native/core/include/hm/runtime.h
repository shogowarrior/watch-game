// app/runtime.py: the watch main loop, hardware (hm::app::Parts) <->
// hm::game::Game <-> hm::ui::Renderer.
//
//     static hm::runtime::Runtime rt(parts, clock);   // ~110 KB: never on a stack
//     rt.begin();
//     for (;;) { feed_watchdog(); rt.idle(rt.step()); }
//
// One step() runs the Python's stages in its order (read its docstring for
// the why of each): radio (drain -> LinkMonitor -> game.on_packet), imu
// (ImuFeed: tracker + bump spikes -> game.on_accel_tap; fast only while
// game.bump_armed()), touch (GestureRecognizer; landings and gestures in time
// order), button (PEK short/long), logic every TICK_MS (battery every 10 s,
// with the low-reading confirmation; game.tick; screen power; power-off),
// render on the FramePacer lock (each frame drawn at its slot), tx (beacons at
// game.beacon_hz on the jittered TxScheduler) and haptic (HapticPlayer ->
// Motor). step() returns the ms to the next deadline; idle() sleeps it,
// ticking the motor every ms while a pattern plays.
//
// A frame is drawn in STRIP_ROWS-row strips into two buffers in turn: the
// renderer draws one while the Display sends the other, and after each strip
// the motor is serviced and touch sampled (once TOUCH_GAP_MS have passed), as
// the Python's _HapticDisplay does after each band. The renderer's beat starts
// when the frame is out (the Python's path for a renderer that announces no
// spawn; ui/renderer.py announces none yet).
//
// Not ported: the gc, telemetry and debug-sink stages, the watchdog (the
// shell's), the BMA423 feature engine (hm/platform.h), the renderer-less
// metronome (the renderer always exists) and stats(); the parts come up in the
// shell, so a part that failed is just absent. The fps line is an "HM fps"
// line (hm::logf, native/tools/bench_report.py). No allocation.
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/game.h"
#include "hm/gestures.h"
#include "hm/hal.h"
#include "hm/haptic_patterns.h"
#include "hm/imu_feed.h"
#include "hm/link.h"
#include "hm/pacer.h"
#include "hm/platform.h"
#include "hm/proto.h"
#include "hm/renderer.h"
#include "hm/ticks.h"
#include "hm/tuning.h"

namespace hm {
namespace runtime {

constexpr int GAME_ID = 1;
constexpr int32_t TICK_MS = T::LOGIC_MS;   // game logic rate (10 Hz)
constexpr int32_t INPUT_MS = 20;           // touch/button/imu poll when nothing else is due
constexpr int32_t TOUCH_GAP_MS = 15;       // touch samples between strips at least this far apart
constexpr int32_t HAPTIC_SLICE_MS = 1;     // idle motor tick while a pattern plays
constexpr int32_t BATTERY_MS = 10000;
constexpr int BATT_LOW_READS = 3;          // falling readings <= BATT_WARN_PCT in a row before the game sees one
constexpr int32_t BATT_RECHECK_MS = 1000;  // ... taken this far apart
constexpr int IMU_OUT_HZ = 25;             // MotionTracker rate
constexpr int32_t MAX_SLEEP_MS = 50;
constexpr int PMU_OFF_TRIES = 3;           // shared I2C0: retry a glitched AXP202 power-off write
constexpr int STRIP_ROWS = 24;             // rows per strip (the IDF bus's DMA chunk)
constexpr uint32_t ACC_LIMIT_US = 0x1FFFFFFF;   // stage sums halve past this

// stages (the Python's STAGES without gc and tele)
enum Stage { S_RADIO, S_IMU, S_TOUCH, S_BUTTON, S_LOGIC, S_RENDER, S_TX, S_HAPTIC, S_N };

class Runtime {
 public:
  // fps_log_ms: an "HM fps" line that often (0: none; main.py uses 10000).
  Runtime(const app::Parts& parts, Clock& clock, int32_t fps_log_ms = 0);
  Runtime(const Runtime&) = delete;
  Runtime& operator=(const Runtime&) = delete;

  void begin();               // the game and the loop state, at the clock's now (once)
  int32_t step();             // one loop pass; ms until the next deadline
  void idle(int32_t ms);      // sleep ms, ticking the motor every ms while a pattern plays
  void stop() { running = false; }
  bool hap_active() const { return motor_lvl_ > 0 || player.active(); }

  app::Parts parts;
  Clock& clock;
  int32_t fps_log_ms;
  std::optional<game::Game> game;
  std::optional<imu_feed::ImuFeed> feed;   // with an imu
  link::LinkMonitor link;
  link::TxScheduler sched;                 // the Python radio's beacon schedule
  haptic_patterns::HapticPlayer player;
  gestures::GestureRecognizer gestures;
  proto::Beacon tx, rxb;
  ui::Renderer renderer;
  pacer::FramePacer pacer;
  const render_params::RenderParams* params = nullptr;   // the last tick's (in game)
  bool screen_is_on = true;
  std::optional<double> bl_level;
  double fps = 0.0;
  bool started = false, running = false, powered_off = false;
  uint32_t io_errors[S_N] = {};            // bus errors that reached the loop, per stage
  uint32_t st_us[S_N] = {}, st_max[S_N] = {}, st_n[S_N] = {};
  uint32_t loops = 0, frames = 0, ticks = 0;

 private:
  friend struct Probe;   // the tests: a Python test's call of a private method

  static void rx_cb(void* self, const app::RadioFrame& f) { static_cast<Runtime*>(self)->on_rx(f); }
  static void tap_cb(void* self, ticks_t t) { static_cast<Runtime*>(self)->on_tap(t); }
  static bool blank_cb(void* self, ticks_t t) { return static_cast<Runtime*>(self)->feed->blanked(t); }
  static void wait_cb(void* self, int32_t ms) { static_cast<Runtime*>(self)->idle(ms); }

  uint32_t acc(int i, uint32_t a);
  int32_t wait(ticks_t now) const;
  void quiet();
  void stage_radio(ticks_t now);
  void on_rx(const app::RadioFrame& f);
  void stage_imu(ticks_t now);
  void on_tap(ticks_t t);
  void sample_touch(ticks_t t);
  void stage_touch(ticks_t now);
  void stage_button(ticks_t now);
  void stage_logic(ticks_t now);
  void battery(ticks_t now);
  void screen(ticks_t now);
  void backlight(double level);
  void power_off(ticks_t now);
  void stage_render(ticks_t now, ticks_t t0);
  void service();
  void set_rate(int32_t hz);
  void stage_tx(ticks_t now);
  void stage_haptic(ticks_t now);
  void fps_log(ticks_t now);

  uint8_t txbuf_[proto::SIZE] = {};
  uint16_t strips_[2][ui::Renderer::W * STRIP_ROWS];
  ticks_t t_tick_ = 0, t_input_ = 0, t_batt_ = 0;
  int low_n_ = 0;
  ticks_t touch_t_ = 0;                    // last touch sample
  opt_ticks td_t_;                         // touch-down sampled, not yet dispatched
  int g_code_ = 0;                         // gesture sampled, not yet dispatched
  ticks_t g_t_ = 0, g_t0_ = 0;
  int32_t g_x_ = 0, g_y_ = 0;
  int buzz_ = -1;
  bool fresh_ = false;                     // params' haptic not yet played
  ticks_t fps_t_ = 0;
  uint32_t fps_n_ = 0;
  int32_t busy_ = 0;                       // loop busy ms since the last drawn frame
  ticks_t log_t_ = 0;
  uint32_t log_us_ = 0;                    // the last fps line's print time
  double motor_lvl_ = 0.0;
};

}  // namespace runtime
}  // namespace hm
