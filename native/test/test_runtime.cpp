// tests/test_app_runtime.py on hm::runtime::Runtime (app/runtime.py's port):
// the same fake parts and scenarios on a fake ms clock, with the real
// Renderer drawing every frame (as on MicroPython). Each test names its
// Python twin. What the port leaves out (telemetry and the debug sink, gc,
// the feature engine, the metronome, the announced beat, stats()) has no
// twin, nor have the tests that monkeypatch game.bump_armed (the HOT bump
// test has a spike and a touch in one frame). The Python's 4 bands per frame
// are STRIPS strips here; where a Python test hooks a game method to see
// its calls, the C++ one reads the game's state or replays the touch samples
// the loop took.
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include <deque>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "check.h"
#include "fakes.h"
#include "game_probe.h"
#include "hm/runtime.h"

using namespace hm;
namespace R = hm::runtime;
namespace hp = hm::haptic_patterns;
using GP = hm::game::Probe;

// A Python test's calls of the loop's private methods.
namespace hm {
namespace runtime {
struct Probe {
  static void sample_touch(Runtime& r, ticks_t t) { r.sample_touch(t); }
  static void stage_touch(Runtime& r, ticks_t t) { r.stage_touch(t); }
  static void power_off(Runtime& r, ticks_t t) { r.power_off(t); }
  static int low_n(const Runtime& r) { return r.low_n_; }
  static void battery_due(Runtime& r, ticks_t t) { r.t_batt_ = t; }
};
}  // namespace runtime
}  // namespace hm
using Rp = hm::runtime::Probe;

namespace {

constexpr int STRIPS = 240 / R::STRIP_ROWS;   // pushes per frame
const Mac MAC_A = {{0x24, 0x0a, 0xc4, 0x10, 0x00, 0x0a}};
const Mac MAC_B = {{0x24, 0x0a, 0xc4, 0x10, 0x00, 0x0b}};
constexpr uint32_t RUN_MS = 60000;

// The watch's clock in ms: it moves only when the loop sleeps or a fake part takes time.
struct MsClock : Clock {
  explicit MsClock(uint32_t t = 0) : now(t) {}
  uint32_t now;
  uint32_t now_us() override { return now * 1000u; }
  ticks_t now_ms() override { return now; }
  void delay_ms(uint32_t ms) override { now += ms; }
  void sleep_until_us(uint32_t t) override {
    if ((int32_t)(t - now * 1000u) > 0) now = (t + 999) / 1000;
  }
};

struct Spike {
  uint32_t t;   // ms
  int64_t w;    // samples wide
  int mg;       // more on z
};

// FakeIMU: a BMA423-like FIFO in milli-g at odr (sample k from 1 taken k/odr s
// after t0): face-up gravity plus the spikes; every fail_every-th read a bus error.
struct FakeImu : app::Imu {
  FakeImu(MsClock& c, std::vector<Spike> s = {}) : clock(c), spikes(std::move(s)), t0(c.now) {}
  MsClock& clock;
  std::vector<Spike> spikes;
  std::vector<int32_t> odrs;   // set_odr calls
  uint32_t t0;
  int64_t k = 0;               // samples read
  int fail_every = 0, reads = 0;

  bool set_odr(int32_t hz) override {
    odr = hz;
    odrs.push_back(hz);
    t0 = clock.now;   // FIFO emptied
    k = 0;
    return true;
  }
  int fifo_read_mg() override {
    if (fail_every && ++reads % fail_every == 0) return -1;
    const int64_t hz = odr;
    const int64_t k1 = (int64_t)(clock.now - t0) * hz / 1000;
    int64_t n = k1 - k;
    if (n > FIFO_FRAMES) {   // stream mode: the newest 170 stay
      k = k1 - FIFO_FRAMES;
      n = FIFO_FRAMES;
    }
    for (int64_t i = 0; i < n; i++) {
      const int64_t t_us = (int64_t)t0 * 1000 + (k + i + 1) * 1000000 / hz;
      int z = 1000 + (i & 1 ? 3 : -3);
      for (const Spike& s : spikes)
        if ((int64_t)s.t * 1000 <= t_us && t_us < (int64_t)s.t * 1000 + s.w * 1000000 / hz) z += s.mg;
      fifo_mg[3 * i] = 5;
      fifo_mg[3 * i + 1] = -4;
      fifo_mg[3 * i + 2] = (int16_t)(z < 4000 ? z : 3999);
    }
    k = k1;
    return (int)n;
  }
};

struct Press {
  uint32_t t0, t1;   // down, up
  int32_t x, y;
};

// FakeTouch; logs each read (when, touching) for a test to replay.
struct FakeTouch : app::Touch {
  FakeTouch(MsClock& c, std::vector<Press> p = {}) : clock(c), presses(std::move(p)) {}
  MsClock& clock;
  std::vector<Press> presses;
  int contacts = 1;   // fingers while touching (2: multi-touch)
  app::TouchPoint r;
  std::vector<std::pair<uint32_t, bool>> reads;

  const app::TouchPoint& read() override {
    r.touching = false;
    r.contacts = 0;
    for (const Press& p : presses) {
      if (p.t0 <= clock.now && clock.now < p.t1) {
        r.touching = true;
        r.x = p.x;
        r.y = p.y;
        r.contacts = contacts;
      }
    }
    reads.push_back({clock.now, r.touching});
    return r;
  }
};

struct Ev {
  uint32_t t;
  int mask;   // app::EV_*
};

// FakePMU: side-key events at their times, a battery level, VBUS; bus errors on request.
struct FakePmu : app::Pmu {
  FakePmu(MsClock& c, std::vector<Ev> e = {}, int p = 90) : clock(c), events(e.begin(), e.end()), pct(p) {}
  MsClock& clock;
  std::deque<Ev> events;
  int pct;
  int vbus = 0;
  bool off = false;
  int fail_off = 0;         // shutdown() fails this many times
  int fail_poll_every = 0;  // every n-th poll() a bus error
  bool fail_battery = false;
  int cleared = 0, polls = 0, seen = 0;   // seen: polls that returned events

  bool set_long_press_ms(int32_t) override { return true; }
  bool clear_irqs() override {
    cleared += 1;
    while (!events.empty() && events.front().t <= clock.now) events.pop_front();
    return true;
  }
  int poll() override {
    if (fail_poll_every && ++polls % fail_poll_every == 0) return -1;
    int ev = 0;
    while (!events.empty() && events.front().t <= clock.now) {
      ev |= events.front().mask;
      events.pop_front();
    }
    if (ev) seen += 1;
    return ev;
  }
  int battery_percent() override { return fail_battery ? -1 : pct; }
  int battery_voltage() override { return 3900; }
  int is_charging() override { return 0; }
  int vbus_present() override { return vbus; }
  bool shutdown() override {
    if (fail_off) {
      fail_off -= 1;
      return false;
    }
    off = true;
    return true;
  }
};

// The panel. Each 24 rows pushed cost costs[k % n] ms of the clock in turn
// (the Python's SlowDisplay, JitterDisplay and CostDisplay; none: free);
// push_ms is the longest push. With slpout a wake waits 120 ms through the
// loop's wait (WakeDisplay). log holds wakes, pushes and backlight levels;
// drawn, each frame's (slot, clock) at its first strip.
struct FakeDisplay : app::Display {
  enum Kind { WAKE, PUSH, LEVEL };
  struct Entry {
    Kind kind;
    double level;
  };
  explicit FakeDisplay(MsClock* c = nullptr, std::vector<int> k = {}) : clock(c), costs(std::move(k)) {}
  MsClock* clock;
  std::vector<int> costs;
  bool slpout = false;
  int pushes = 0, push_ms = 0;
  std::optional<double> level;
  std::vector<Entry> log;
  const pacer::FramePacer* pacer = nullptr;   // to read each frame's slot
  std::vector<std::pair<ticks_t, ticks_t>> drawn;

  void push_strip(int y0, int h, const uint16_t*) override {
    if (y0 == 0 && pacer) drawn.push_back({ticks_add(pacer->t_next, -pacer->period), clock->now});
    if (!costs.empty()) {
      const int ms = costs[pushes % costs.size()] * h / 24;
      if (ms > push_ms) push_ms = ms;
      clock->now += ms;
    }
    pushes += 1;
    log.push_back({PUSH, 0});
  }
  void flush() override {}
  void sleep() override { asleep = true; }
  void wake(double lvl, app::WaitFn wait, void* ctx) override {
    log.push_back({WAKE, 0});
    if (slpout) wait(ctx, 120);
    asleep = false;
    brightness(lvl);
  }
  void brightness(double lvl) override {
    log.push_back({LEVEL, lvl});
    level = lvl;
  }
};

// RecMotor: motor on/off edges on the watch's clock, and every level set.
struct RecMotor : app::Motor {
  explicit RecMotor(MsClock& c) : clock(c) {}
  MsClock& clock;
  double lvl = 0;
  std::vector<std::pair<uint32_t, bool>> log;
  std::vector<double> history;

  void set(double s) override {
    if ((s > 0) != (lvl > 0)) log.push_back({clock.now, s > 0});
    lvl = s;
    history.push_back(s);
  }
};

bool any_on(const std::vector<double>& h, size_t from = 0, size_t to = SIZE_MAX) {
  for (size_t k = from; k < h.size() && k < to; k++)
    if (h[k] > 0) return true;
  return false;
}

struct Pulse {
  int32_t on;
  std::optional<int32_t> before, after;   // gaps (None at the ends)
};

// _pulses: [(on ms, gap before, gap after)].
std::vector<Pulse> pulses(const std::vector<std::pair<uint32_t, bool>>& log) {
  std::vector<Pulse> out;
  for (size_t i = 0; i + 1 < log.size(); i++) {
    if (!log[i].second || log[i + 1].second) continue;
    Pulse p{(int32_t)(log[i + 1].first - log[i].first), {}, {}};
    if (i > 0) p.before = (int32_t)(log[i].first - log[i - 1].first);
    if (i + 2 < log.size()) p.after = (int32_t)(log[i + 2].first - log[i + 1].first);
    out.push_back(p);
  }
  return out;
}

// hal/radio.py SimRadio: frames reach the linked radios' inboxes, delivered
// on their next poll with the sender's clock (stale or future stamps: now).
struct SimRadio : app::Radio {
  struct Msg {
    Mac mac;
    std::vector<uint8_t> data;
    int rssi;
    ticks_t t;
  };
  SimRadio(MsClock& c, const Mac& m) : clock(c), mac_(m) {}
  MsClock& clock;
  Mac mac_;
  std::vector<std::pair<SimRadio*, int>> links;
  std::deque<Msg> inbox;
  int n_tx = 0;
  int fail_every = 0, sends = 0;

  const uint8_t* mac() const override { return mac_.b; }
  bool send(const uint8_t* buf, size_t n) override {
    if (fail_every && ++sends % fail_every == 0) return false;
    for (auto& l : links) l.first->inbox.push_back({mac_, std::vector<uint8_t>(buf, buf + n), l.second, clock.now});
    n_tx += 1;
    return true;
  }
  int poll(ticks_t now, void (*fn)(void*, const app::RadioFrame&), void* ctx) override {
    int c = 0;
    while (c < 32 && !inbox.empty()) {
      const Msg m = inbox.front();
      inbox.pop_front();
      c += 1;
      ticks_t t = m.t & (TICKS_PERIOD - 1);
      const int32_t a = ticks_diff(now, t);
      if (a < 0 || a > 1000) t = now;
      fn(ctx, app::RadioFrame{m.mac.b, m.data.data(), (int)m.data.size(), m.rssi, t});
    }
    return c;
  }
};

void connect(SimRadio& a, SimRadio& b, int rssi) {
  a.links.push_back({&b, rssi});
  b.links.push_back({&a, rssi});
}

// _watch: every part faked, the loop not begun.
struct Watch {
  Watch(MsClock& c, SimRadio* radio, std::vector<Spike> spikes = {}, std::vector<Press> touches = {},
        std::vector<Ev> buttons = {})
      : pmu(c, std::move(buttons)), display(&c), imu(c, std::move(spikes)), touch(c, std::move(touches)), motor(c) {
    app::Parts parts;
    parts.pmu = &pmu;
    parts.display = &display;
    parts.imu = &imu;
    parts.touch = &touch;
    parts.motor = &motor;
    parts.radio = radio;
    rt = std::make_unique<R::Runtime>(parts, c);
  }
  FakePmu pmu;
  FakeDisplay display;
  FakeImu imu;
  FakeTouch touch;
  RecMotor motor;
  std::unique_ptr<R::Runtime> rt;
};

// _run: steps ms of the clock, sleeping what step() returns (at least 1 ms);
// returns the haptics the logic ticks raised.
std::vector<hp::Haptic> run_for(R::Runtime& rt, MsClock& c, uint32_t ms) {
  std::vector<hp::Haptic> haps;
  uint32_t n = rt.ticks;
  const uint32_t end = c.now + ms;
  while (c.now < end && !rt.powered_off) {
    const int32_t w = rt.step();
    c.now += w > 1 ? w : 1;
    if (rt.ticks != n && rt.params->haptic != hp::NONE) haps.push_back(rt.params->haptic);
    n = rt.ticks;
  }
  return haps;
}

// Runtime.run(max_ms): step, then idle what it returns.
void run(R::Runtime& rt, MsClock& c, uint32_t max_ms) {
  rt.begin();
  const uint32_t t0 = c.now;
  while (rt.running) {
    const int32_t w = rt.step();
    if (c.now - t0 >= max_ms) break;
    if (w > 0) rt.idle(w);
  }
}

// Two watches on their own clocks, the one behind stepping next.
void run_apart(Watch& a, MsClock& ca, Watch& b, MsClock& cb, uint32_t end, void (*each)(void*) = nullptr,
               void* ctx = nullptr) {
  uint32_t due[2] = {0, 0};
  Watch* w[2] = {&a, &b};
  MsClock* c[2] = {&ca, &cb};
  while (ca.now < end || cb.now < end) {
    if (each) each(ctx);
    const int k = due[0] <= due[1] ? 0 : 1;
    if (c[k]->now < due[k]) c[k]->now = due[k];
    const int32_t s = w[k]->rt->step();
    w[k]->rt->idle(s > 0 ? s : 1);   // own clock: the other watch is independent
    due[k] = c[k]->now;
  }
}

bool has(const std::vector<hp::Haptic>& v, hp::Haptic h) {
  for (hp::Haptic x : v)
    if (x == h) return true;
  return false;
}

}  // namespace

// test_two_watches_one_minute
TEST(runtime_two_watches_one_minute) {
  MsClock clock(0);
  SimRadio ra(clock, MAC_A), rb(clock, MAC_B);
  connect(ra, rb, -50);
  Watch a(clock, &ra, {{1000, 2, 3000}, {1500, 12, 3000}, {20000, 2, 3000}}, {}, {{2000, app::EV_SHORT}});
  const int32_t y_buzz = T::MENU_ROWS_Y[2] + T::MENU_ROW_H / 2;     // row 2: BUZZ
  const int32_t y_resume = T::MENU_ROWS_Y[0] + T::MENU_ROW_H / 2;   // row 0: RESUME
  Watch b(clock, &rb, {},
          {{45000, 46000, 120, 120},          // long press: MENU
           {47000, 47080, 120, y_buzz},       // BUZZ: EVENTS
           {48000, 48080, 120, y_buzz},       // BUZZ: OFF
           {49000, 49080, 120, y_resume}},    // RESUME
          {{2500, app::EV_SHORT}});
  Watch* ws[2] = {&a, &b};
  for (Watch* w : ws) w->rt->begin();
  std::optional<uint32_t> off_at;
  size_t off_idx = 0;
  bool confirmed_seen = false;
  while (clock.now < RUN_MS) {
    int32_t w = 1000;
    for (Watch* x : ws) {
      const int32_t d = x->rt->step();
      if (d < w) w = d;
    }
    if (b.rt->game->pair.sub == pairing::CALIBRATE && a.rt->game->pair.sub == pairing::CALIBRATE) confirmed_seen = true;
    if (!off_at && b.rt->game->buzz == game::BUZZ_OFF) {
      off_at = clock.now;
      off_idx = b.motor.history.size();
    }
    clock.now += w > 0 ? w : 1;
  }

  // frames at the capped rate (20 fps, screens stay on: face-up)
  for (Watch* w : ws) {
    const R::Runtime& rt = *w->rt;
    CHECK(rt.game->screen_on && !w->display.asleep);
    CHECK(0.95 * 20 * RUN_MS / 1000 <= rt.frames && rt.frames <= 20 * RUN_MS / 1000 + 2);
    CHECK(w->display.pushes == STRIPS * (int)rt.frames);
    CHECK(rt.ticks >= 0.98 * RUN_MS / 100);
    CHECK(w->display.level && *w->display.level > 0);
    for (uint32_t e : rt.io_errors) CHECK(e == 0);
    CHECK(rt.fps > 18.0);
  }

  // beacons both ways, partner locked in the LinkMonitor
  CHECK(ra.n_tx > 600 && rb.n_tx > 600);
  CHECK(a.rt->link.n_rx > 300 && b.rt->link.n_rx > 300);
  CHECK(a.rt->link.partner == MAC_B && b.rt->link.partner == MAC_A);
  CHECK(a.rt->tx.seq == ra.n_tx);

  // pairing: both confirmed -> calibrate -> split -> hunt
  CHECK(confirmed_seen);
  for (Watch* w : ws) CHECK(w->rt->game->pair.peer_mac && w->rt->game->mode != game::PAIRING);
  CHECK(a.rt->game->pair.runes == b.rt->game->pair.runes);

  // bump spikes, sampled fast only in PAIRING seen / confirmed: 2.5 ms
  // accepted, 15 ms (a shake) rejected; the split's 100 Hz sees none
  const imu_feed::ImuFeed& f = *a.rt->feed;
  CHECK(a.imu.odrs.size() >= 2 && a.imu.odrs[0] == T::BUMP_ODR_HZ && a.imu.odrs[1] == 100);
  CHECK(f.fast == a.rt->game->bump_armed());
  CHECK(f.n_taps == 1);
  CHECK(f.last_tap && abs(ticks_diff(*f.last_tap, 1000)) <= 2);
  CHECK(f.n_rejected == 1);
  CHECK(f.n_samples >= 5900);

  // haptics: the motor was driven; buzz OFF (via the menu) silenced watch B
  CHECK(any_on(a.motor.history));
  CHECK(any_on(b.motor.history, 0, off_idx));
  CHECK(off_at && 48000 <= *off_at && *off_at < 49000);
  CHECK(!any_on(b.motor.history, off_idx));
  CHECK(b.motor.lvl == 0);
  CHECK(a.rt->game->buzz == game::BUZZ_FULL);
  // A (FULL) still buzzes, B (OFF) does not; HOT has no heartbeat, so an event
  // (a held one plays on the next tick)
  CHECK(a.rt->game->px.zone() == proximity::HOT && b.rt->game->px.zone() == proximity::HOT);
  const size_t a_idx = a.motor.history.size();
  for (Watch* w : ws) GP::hold(*w->rt->game, hp::NOPE);
  const uint32_t end = clock.now + 1500;
  while (clock.now < end) {
    int32_t w = 1000;
    for (Watch* x : ws) {
      const int32_t d = x->rt->step();
      if (d < w) w = d;
    }
    clock.now += w > 0 ? w : 1;
  }
  CHECK(any_on(a.motor.history, a_idx));
  CHECK(!any_on(b.motor.history, off_idx));
  CHECK(!b.rt->game->menu_open());
  for (Watch* w : ws) CHECK(!w->pmu.off && !w->rt->powered_off);
}

// test_missing_parts_and_screen_off
TEST(runtime_missing_parts_and_screen_off) {
  MsClock clock(0);
  FakeDisplay d;
  FakeImu imu(clock);
  app::Parts parts;
  parts.display = &d;
  parts.imu = &imu;
  auto rt = std::make_unique<R::Runtime>(parts, clock);
  run(*rt, clock, 1000);
  CHECK(rt->frames >= 18 && d.pushes == STRIPS * (int)rt->frames);
  // face down (z = -1 g) for > WRIST_DOWN_MS: display sleeps, frames stop
  imu.spikes.push_back({clock.now, 100000, -2000});
  const int n0 = d.pushes;
  run(*rt, clock, T::WRIST_DOWN_MS + 2000);
  CHECK(d.asleep && !rt->screen_is_on && rt->bl_level == 0.0);
  CHECK(d.pushes < n0 + STRIPS * 20 * (T::WRIST_DOWN_MS / 1000 + 1));
  const int n1 = d.pushes;
  run(*rt, clock, 1000);
  CHECK(d.pushes == n1);
  // a new loop on the same parts (a restarted shell): it wakes the asleep panel
  imu.spikes.clear();
  rt = std::make_unique<R::Runtime>(parts, clock);
  run(*rt, clock, 500);
  CHECK(!d.asleep && rt->frames > 0 && d.level && *d.level > 0);
}

// test_haptic_pulses_keep_floor_with_slow_frames: frames take 40 ms, renderer
// events start after the frame, the motor is serviced between strips and
// while idle, so pulses keep MIN_PULSE_MS.
TEST(runtime_haptic_pulses_keep_floor_with_slow_frames) {
  MsClock ca(0), cb(0);
  SimRadio ra(ca, MAC_A), rb(cb, MAC_B);
  connect(ra, rb, -50);
  Watch a(ca, &ra, {}, {}, {{2000, app::EV_SHORT}});
  Watch b(cb, &rb, {}, {}, {{2500, app::EV_SHORT}});
  Watch* ws[2] = {&a, &b};
  for (Watch* w : ws) {
    w->display.costs = {4};
    w->rt->begin();
  }
  const uint32_t end = 20000;
  run_apart(a, ca, b, cb, end);
  for (Watch* w : ws) {
    const int strip = w->display.push_ms;
    CHECK(strip <= 10);
    CHECK(w->rt->frames >= 0.95 * 20 * end / 1000);
    const std::vector<Pulse> p = pulses(w->motor.log);
    CHECK(p.size() >= 6);
    int ticks = 0;
    for (const Pulse& x : p) {
      if ((x.before && *x.before < 200) || (x.after && *x.after < 200)) continue;
      ticks += 1;   // a single TICK: exact start, end within a strip
      CHECK(hp::MIN_PULSE_MS <= x.on && x.on <= hp::MIN_PULSE_MS + strip);
    }
    CHECK(ticks >= 3);
    for (const Pulse& x : p) CHECK(x.on >= hp::MIN_PULSE_MS);   // a later pulse may start a strip late, never shorter
    CHECK(w->rt->feed->dec == 4);                                // tracker fed at 25 Hz
  }
}

// test_scan_countdown_ticks_all_reach_the_motor: the scan ``ready`` countdown
// TICKs (1 s apart, ui-spec §6) each become a full motor pulse, though frames
// take ~40 ms with jitter.
TEST(runtime_scan_countdown_ticks_all_reach_the_motor) {
  MsClock ca(0), cb(0);
  SimRadio ra(ca, MAC_A), rb(cb, MAC_B);
  connect(ra, rb, -50);
  Watch a(ca, &ra, {}, {}, {{1000, app::EV_SHORT}});   // both confirm: calibrate
  Watch b(cb, &rb, {}, {}, {{1000, app::EV_SHORT}});
  Watch* ws[2] = {&a, &b};
  for (Watch* w : ws) {
    w->display.costs = {4, 5, 3};   // 24 rows: 3, 4 or 5 ms in turn
    w->rt->begin();
  }
  struct Scan {
    Watch* a;
    MsClock* c;
    std::optional<uint32_t> t;
  } s{&a, &ca, {}};
  run_apart(a, ca, b, cb, 7000, [](void* p) {
    Scan& s = *static_cast<Scan*>(p);
    if (!s.t && s.c->now >= 2000) {
      s.t = s.c->now;
      GP::start_scan(*s.a->rt->game, *s.t);
      s.a->motor.log.clear();
    }
  }, &s);
  CHECK(a.rt->game->mode == game::SCANNING);
  std::vector<uint32_t> on;
  for (auto& e : a.motor.log)
    if (e.second) on.push_back(e.first);
  const std::vector<Pulse> p = pulses(a.motor.log);
  CHECK(on.size() >= 3 && p.size() >= 3);
  CHECK(950 <= on[1] - on[0] && on[1] - on[0] <= 1050 && 950 <= on[2] - on[1] && on[2] - on[1] <= 1050);
  CHECK(on[0] - *s.t <= 500 + 100 + 60);   // flat held 0.5 s, logic + frame
  for (int k = 0; k < 3; k++) CHECK(p[k].on >= hp::MIN_PULSE_MS);
}

// test_low_battery_shutdown_needs_confirmation_and_no_usb
TEST(runtime_low_battery_shutdown_needs_confirmation_and_no_usb) {
  MsClock clock(1000);
  SimRadio ra(clock, MAC_A);
  Watch w(clock, &ra);
  w.pmu.pct = 2;
  w.pmu.vbus = 1;
  R::Runtime& rt = *w.rt;
  rt.begin();
  run_for(rt, clock, 25000);   // on USB: 2 % never powers off
  CHECK(!rt.powered_off && !GP::bye_t(*rt.game));
  CHECK(rt.game->battery == T::BATT_SHUTDOWN_PCT + 1);
  CHECK(!w.pmu.off);
  w.pmu.vbus = 0;              // unplugged
  uint32_t t_low = 0;
  const uint32_t give_up = clock.now + R::BATTERY_MS + 1000;   // the Python waits for ever
  while (!Rp::low_n(rt) && clock.now < give_up) {   // first off-USB reading (unconfirmed)
    t_low = clock.now;
    const int32_t s = rt.step();
    clock.now += s > 1 ? s : 1;
  }
  CHECK(Rp::low_n(rt));
  CHECK(rt.game->battery == T::BATT_SHUTDOWN_PCT + 1 && !GP::bye_t(*rt.game));
  run_for(rt, clock, 30000);
  CHECK(rt.powered_off && !rt.running && w.pmu.off);
  CHECK(GP::bye_t(*rt.game) && ticks_diff(*GP::bye_t(*rt.game), t_low) == (R::BATT_LOW_READS - 1) * R::BATT_RECHECK_MS);
  CHECK(w.display.asleep && w.motor.lvl == 0);
}

// test_one_low_battery_reading_is_ignored: an unconfirmed shutdown-level
// reading never reaches the game: it keeps its last value (100 at boot), so
// no BATTERY 5% toast, BATT buzz or saver.
TEST(runtime_one_low_battery_reading_is_ignored) {
  MsClock clock(1000);
  SimRadio ra(clock, MAC_A);
  Watch w(clock, &ra);
  R::Runtime& rt = *w.rt;
  w.pmu.pct = 0;   // a sagging voltage-fallback reading at boot
  rt.begin();
  rt.step();
  const game::Game& g = *rt.game;
  CHECK(g.battery == 100 && !g.saver() && !GP::toast_on(g));
  w.pmu.pct = 60;
  run_for(rt, clock, 11000);
  CHECK(g.battery == 60);
  w.pmu.pct = 0;   // one more, at a later 10 s reading
  Rp::battery_due(rt, clock.now);
  const uint32_t give_up = clock.now + 1000;   // the Python waits for ever
  while (!Rp::low_n(rt) && clock.now < give_up) {
    const int32_t s = rt.step();
    clock.now += s > 1 ? s : 1;
  }
  CHECK(Rp::low_n(rt));
  w.pmu.pct = 60;
  const std::vector<hp::Haptic> haps = run_for(rt, clock, 3000);
  CHECK(g.battery == 60 && !g.saver() && !GP::toast_on(g));
  CHECK(!has(haps, hp::BATT));
  CHECK(!rt.powered_off && !w.pmu.off && !GP::bye_t(g));
}

// test_one_sagging_ladder_reading_is_ignored: one falling reading on the
// LOW-BATTERY ladder (5 % from a cell at 8 %) neither alarms nor latches the
// 5 % step, so the real 5 % still buzzes.
TEST(runtime_one_sagging_ladder_reading_is_ignored) {
  MsClock clock(1000);
  SimRadio ra(clock, MAC_A);
  Watch w(clock, &ra);
  R::Runtime& rt = *w.rt;
  w.pmu.pct = 8;
  rt.begin();
  run_for(rt, clock, 11000);
  const game::Game& g = *rt.game;
  CHECK(g.battery == 8);
  w.pmu.pct = 5;   // one sag at a later 10 s reading
  Rp::battery_due(rt, clock.now);
  std::vector<hp::Haptic> haps = run_for(rt, clock, 500);
  CHECK(Rp::low_n(rt) == 1 && g.battery == 8);
  w.pmu.pct = 8;
  for (hp::Haptic h : run_for(rt, clock, 12000)) haps.push_back(h);
  CHECK(g.battery == 8 && !GP::toast_on(g));
  CHECK(!has(haps, hp::BATT));
  w.pmu.pct = 5;   // the real 5 %
  haps = run_for(rt, clock, 15000);
  int n = 0;
  for (hp::Haptic h : haps) n += h == hp::BATT;
  CHECK(n == 1 && g.battery == 5);
}

// test_power_off_survives_panel_and_bus_errors: a glitched power-off write
// on the shared I2C0 is retried (the C++ panel's sleep cannot fail).
TEST(runtime_power_off_retries_the_pmu) {
  MsClock clock(1000);
  SimRadio ra(clock, MAC_A);
  Watch w(clock, &ra);
  w.pmu.fail_off = 2;
  w.rt->begin();
  Rp::power_off(*w.rt, clock.now);
  CHECK(w.pmu.off && w.rt->powered_off && !w.rt->running && w.display.asleep);
  CHECK(w.rt->io_errors[R::S_LOGIC] == 2);
}

// test_no_radio_still_powers_off_at_shutdown_level: ui-spec LOW-BATTERY 3 %:
// BYE ends in the AXP202 power-off with no goodbye beacon to send (R-12).
TEST(runtime_no_radio_still_powers_off_at_shutdown_level) {
  MsClock clock(1000);
  FakePmu pmu(clock, {}, 2);
  FakeDisplay d;
  FakeImu imu(clock);
  RecMotor m(clock);
  app::Parts parts;
  parts.pmu = &pmu;
  parts.display = &d;
  parts.imu = &imu;
  parts.motor = &m;
  auto rt = std::make_unique<R::Runtime>(parts, clock);
  rt->begin();
  run_for(*rt, clock, 10000);
  CHECK(rt->powered_off && pmu.off);
}

// test_press_latched_before_begin_is_dropped: a side-key press latched before
// the loop starts never reaches the game, so it never opens MENU.
TEST(runtime_press_latched_before_begin_is_dropped) {
  MsClock clock(1000);
  SimRadio ra(clock, MAC_A);
  Watch w(clock, &ra, {}, {}, {{500, app::EV_LONG}});
  w.rt->begin();
  run_for(*w.rt, clock, 1000);
  CHECK(w.pmu.cleared == 1 && w.pmu.seen == 0 && !w.rt->game->menu_open());
}

// test_wake_lights_a_fresh_frame_and_keeps_motor_timing: ui-spec §8: the
// panel wakes dark and is lit only once a fresh frame is out; the motor is
// serviced through the 120 ms SLPOUT wait.
TEST(runtime_wake_lights_a_fresh_frame_and_keeps_motor_timing) {
  MsClock clock(0);
  FakeDisplay d(&clock);
  d.slpout = true;
  FakeImu imu(clock);
  RecMotor m(clock);
  app::Parts parts;
  parts.display = &d;
  parts.imu = &imu;
  parts.motor = &m;
  auto rt = std::make_unique<R::Runtime>(parts, clock);
  d.pacer = &rt->pacer;
  run(*rt, clock, 1000);
  imu.spikes.push_back({clock.now, 100000, -2000});   // face down: the screen goes off
  run(*rt, clock, T::WRIST_DOWN_MS + 2000);
  CHECK(d.asleep && !rt->screen_is_on);
  d.log.clear();
  d.drawn.clear();
  m.log.clear();
  rt->game->on_wake(clock.now);
  rt->player.play_named(hp::LOST, clock.now);   // 5 x 60 ms on, 60 ms off
  run(*rt, clock, 1000);
  CHECK(!d.drawn.empty() && d.drawn[0].first == d.drawn[0].second);   // no stale frame after the SLPOUT wait
  size_t i = 0;
  while (i < d.log.size() && d.log[i].kind != FakeDisplay::WAKE) i++;
  size_t lit = i + 1;
  while (lit < d.log.size() && !(d.log[lit].kind == FakeDisplay::LEVEL && d.log[lit].level > 0)) lit++;
  CHECK(lit < d.log.size());
  int pushed = 0;
  for (size_t k = i + 1; k < lit; k++) pushed += d.log[k].kind == FakeDisplay::PUSH;
  CHECK(pushed >= STRIPS);
  const hp::Pattern& lost = hp::PATTERNS[hp::LOST];
  const int32_t on_ms = lost.on(0), off_ms = lost.off(0);
  const std::vector<Pulse> p = pulses(m.log);
  CHECK((int32_t)p.size() == lost.n_steps());
  for (const Pulse& x : p) {   // every edge within the 1 ms idle slice
    CHECK(on_ms <= x.on && x.on <= on_ms + 1);
    CHECK(!x.after || (off_ms <= *x.after && *x.after <= off_ms + 1));
  }
}

// test_short_taps_count_while_frames_render: ui-spec §8: a 60-400 ms touch is
// a TAP. A frame takes 40 ms, so touch is also sampled between strips,
// TOUCH_GAP_MS apart: the samples the loop took find all forty 70 ms taps at
// every phase, each landing within a gap and a strip's push.
TEST(runtime_short_taps_sampled_while_frames_render) {
  MsClock clock(0);
  SimRadio ra(clock, MAC_A);
  std::vector<Press> taps;
  for (uint32_t i = 0; i < 40; i++) taps.push_back({5000 + 507 * i, 5070 + 507 * i, 120, 120});
  Watch w(clock, &ra, {}, taps);
  w.display.costs = {4};
  w.rt->begin();
  run_for(*w.rt, clock, 26000);
  const int32_t late = R::TOUCH_GAP_MS + w.display.push_ms;   // a gap, then a strip's push
  CHECK(late <= R::TOUCH_GAP_MS + 10);
  gestures::GestureRecognizer gr;
  std::vector<uint32_t> downs;
  int n = 0;
  for (auto& s : w.touch.reads) {
    n += gr.update(s.first, s.second, 120, 120) == gestures::TAP;
    if (gr.began) downs.push_back(s.first);
  }
  CHECK(n == 40 && downs.size() == 40);
  for (size_t k = 0; k < downs.size(); k++) CHECK(downs[k] >= taps[k].t0 && downs[k] - taps[k].t0 <= (uint32_t)late);
  const auto& r = w.touch.reads;
  int32_t gmax = 0;
  for (size_t k = 1; k < r.size(); k++) {
    CHECK(r[k].first >= r[k - 1].first);
    if ((int32_t)(r[k].first - r[k - 1].first) > gmax) gmax = (int32_t)(r[k].first - r[k - 1].first);
  }
  CHECK(gmax <= late);
  CHECK(r.size() <= 26000 / 12);   // not after every strip
}

// _bump_in_hot: two watches paired and both in HOT; both accelerometers
// spike at t0 and, with finger, A's screen is touched by the knock (70 ms
// after the spike). Each time in knocks is one more knock on both, touching
// A's screen too. Runs to the last knock + 1.2 s; false if a check failed.
static bool bump_in_hot(uint32_t t0, bool finger, std::vector<uint32_t> knocks, game::Mode* ma, game::Mode* mb) {
  MsClock clock(0);
  SimRadio ra(clock, MAC_A), rb(clock, MAC_B);
  connect(ra, rb, -50);
  std::vector<Spike> spikes = {{t0, 2, 3000}};
  std::vector<Press> touches;
  if (finger) touches.push_back({t0 + 70, t0 + 160, 120, 120});
  for (uint32_t t : knocks) {
    spikes.push_back({t, 2, 3000});
    touches.push_back({t + 20, t + 110, 120, 120});
  }
  Watch a(clock, &ra, spikes, touches, {{1000, app::EV_SHORT}});
  Watch b(clock, &rb, spikes, {}, {{1100, app::EV_SHORT}});
  Watch* ws[2] = {&a, &b};
  for (Watch* w : ws) {
    w->rt->begin();
    w->rt->game->pair.split_s = 1;            // short split countdown
    w->rt->game->buzz = game::BUZZ_OFF;       // no motor pulse can blank a spike
  }
  bool hot = false;
  const uint32_t end = (knocks.empty() ? t0 : knocks.back()) + 1200;
  while (clock.now < end) {
    if (!hot && clock.now >= t0 - 50) {
      hot = true;
      for (Watch* w : ws)
        if (w->rt->game->mode != game::HUNT || w->rt->game->px.zone() != proximity::HOT) return false;
    }
    int32_t w = 1000;
    for (Watch* x : ws) {
      const int32_t d = x->rt->step();
      if (d < w) w = d;
    }
    clock.now += w > 0 ? w : 1;
  }
  const int32_t n = 1 + (int32_t)knocks.size();
  if (a.rt->feed->n_taps != n || b.rt->feed->n_taps != n) return false;
  *ma = a.rt->game->mode;
  *mb = b.rt->game->mode;
  return true;
}

// test_a_knock_that_touches_the_screen_is_found_on_both: ui-spec §6, rule
// 10: screen-to-screen knocks touch the panel; the touch never withdraws the
// spike, so both watches enter FOUND, finger or not. Knocks that go on after
// FOUND (on TAP=AGAIN) start no new round (§8).
TEST(runtime_a_knock_that_touches_the_screen_is_found_on_both) {
  game::Mode ma, mb;
  CHECK(bump_in_hot(7960, true, {}, &ma, &mb));
  CHECK(ma == game::FOUND && mb == game::FOUND);
  CHECK(bump_in_hot(7960, false, {}, &ma, &mb));
  CHECK(ma == game::FOUND && mb == game::FOUND);
  const uint32_t k = 7960 + T::FOUND_CELEBRATE_MS;
  CHECK(bump_in_hot(7960, true, {k + 300, k + 900}, &ma, &mb));
  CHECK(ma == game::FOUND && mb == game::FOUND);
}

// test_touch_down_seen_when_a_press_follows_a_gap: a sample gap over the 60 ms
// debounce can end one press and start the next on the same sample: that
// finger still lands (§6). The Python sees the game's on_touch_down calls;
// here both landings are in the game's burst ring.
TEST(runtime_touch_down_seen_when_a_press_follows_a_gap) {
  MsClock clock(0);
  SimRadio ra(clock, MAC_A);
  Watch w(clock, &ra, {}, {{5000, 5500, 120, 120}, {5555, 5705, 120, 120}});
  R::Runtime& rt = *w.rt;
  rt.begin();
  run_for(rt, clock, 5500);
  CHECK(GP::touches(*rt.game) == 1);
  rt.step();          // lifted
  clock.now += 60;    // no sample for 60 ms; touching again
  run_for(rt, clock, 500);
  CHECK(GP::touches(*rt.game) == 2);
}

// test_tap_never_beats_its_own_touch_down: §8 burst filter: a press that
// lands and ends between two stage samples (mid-frame samples, then a pause)
// reaches the game landing first, so as the 3rd landing in 1 s it blocks its own TAP.
TEST(runtime_tap_never_beats_its_own_touch_down) {
  MsClock clock(0);
  SimRadio ra(clock, MAC_A);
  Watch w(clock, &ra, {}, {{5000, 5100, 120, 120}, {5300, 5400, 120, 120}, {5605, 5680, 120, 120}});
  R::Runtime& rt = *w.rt;
  run_for(rt, clock, 5600);
  const game::Game& g = *rt.game;
  rt.step();
  const uint32_t t = clock.now;
  for (uint32_t dt : {10u, 90u}) {   // mid-frame samples: landed, lifted
    clock.now = t + dt;
    Rp::sample_touch(rt, clock.now);
  }
  clock.now = t + 160;               // next stage: the TAP is due as well
  const ticks_t i0 = GP::input_t(g);
  Rp::stage_touch(rt, clock.now);
  CHECK(GP::touch_block(g) && GP::input_t(g) == i0);
}

// test_touch_that_lands_in_the_wake_window_is_ignored: ui-spec §8: touches are
// ignored for 300 ms after a wake, judged by when the finger landed.
TEST(runtime_touch_that_lands_in_the_wake_window_is_ignored) {
  MsClock clock(0);
  SimRadio ra(clock, MAC_A);
  const uint32_t down = T::WRIST_DOWN_MS + 1000;
  Watch w(clock, &ra, {{1000, down / 10, -2000}});
  R::Runtime& rt = *w.rt;
  run_for(rt, clock, down + 500);   // face down 1 s on: the screen goes off
  const game::Game& g = *rt.game;
  CHECK(!g.screen_on);
  const uint32_t give_up = clock.now + 5000;   // the Python waits for ever
  while (!g.screen_on && clock.now < give_up) {   // face up: wake
    const int32_t s = rt.step();
    clock.now += s > 1 ? s : 1;
  }
  CHECK(g.screen_on);
  const ticks_t t = GP::wake_t(g);
  w.touch.presses.push_back({t + 150, t + 250, 120, 120});
  run_for(rt, clock, 1000);
  CHECK(GP::input_t(g) == t);
}

// test_io_errors_never_stop_the_loop: bus errors that reach the loop from a
// part are counted in io_errors and the loop goes on (C++ touch and motor
// calls cannot fail: their drivers count their own errors).
TEST(runtime_io_errors_never_stop_the_loop) {
  MsClock clock(0);
  SimRadio ra(clock, MAC_A);
  Watch w(clock, &ra, {}, {}, {{2000, app::EV_SHORT}});
  w.pmu.fail_poll_every = 2;
  w.pmu.fail_battery = true;
  w.imu.fail_every = 3;
  ra.fail_every = 4;
  R::Runtime& rt = *w.rt;
  rt.begin();
  rt.player.play_named(hp::LOST, 1000);
  run_for(rt, clock, 5000);
  const uint32_t* e = rt.io_errors;
  CHECK(e[R::S_BUTTON] && e[R::S_LOGIC] && e[R::S_IMU] && e[R::S_TX]);
  CHECK(rt.running && rt.ticks >= 45 && rt.frames >= 90);
}

// test_frame_lock_holds_what_slow_frames_fit_and_steps_evenly: ui-spec §4
// rule 6: 70-90 ms frames cannot hold 20 fps, so the lock drops to 10 at
// once; every frame then animates exactly 100 ms on from the one before and
// starts on its slot.
TEST(runtime_frame_lock_holds_what_slow_frames_fit_and_steps_evenly) {
  MsClock clock(0);
  FakeDisplay d(&clock, {7, 8, 9});
  FakeImu imu(clock);
  RecMotor m(clock);
  app::Parts parts;
  parts.display = &d;
  parts.imu = &imu;
  parts.motor = &m;
  auto rt = std::make_unique<R::Runtime>(parts, clock);
  d.pacer = &rt->pacer;
  run(*rt, clock, 6000);
  CHECK(rt->pacer.fps == 10 && rt->pacer.changes == 1);
  std::vector<std::pair<ticks_t, ticks_t>> late;
  for (auto& x : d.drawn)
    if (x.first >= 1000) late.push_back(x);
  CHECK(late.size() >= 45);
  for (size_t k = 0; k + 1 < late.size(); k++) {
    CHECK(ticks_diff(late[k + 1].first, late[k].first) == 100);
    CHECK(late[k].second == late[k].first);   // drawn on its slot
  }
  CHECK(9.5 <= rt->fps && rt->fps <= 10.5);
}

// The "HM fps" lines logged since mark.
static std::vector<std::string> fps_lines(size_t mark) {
  std::vector<std::string> out;
  for (size_t k = mark; k < hmt::log_lines.size(); k++)
    if (hmt::log_lines[k].rfind("HM fps ", 0) == 0) out.push_back(hmt::log_lines[k]);
  return out;
}

// key=value of one log line.
static std::string field(const std::string& line, const char* key) {
  const std::string k = std::string(" ") + key + "=";
  const size_t i = line.find(k);
  if (i == std::string::npos) return "";
  const size_t a = i + k.size();
  return line.substr(a, line.find(' ', a) - a);
}

// test_fps_line_reports_lock_misses_and_jitter
TEST(runtime_fps_line_reports_lock_misses_and_jitter) {
  MsClock clock(0);
  FakeDisplay d(&clock, {7, 8, 9});
  FakeImu imu(clock);
  app::Parts parts;
  parts.display = &d;
  parts.imu = &imu;
  auto rt = std::make_unique<R::Runtime>(parts, clock, 2000);
  const size_t mark = hmt::log_lines.size();
  run(*rt, clock, 6500);
  const std::vector<std::string> lines = fps_lines(mark);
  CHECK(lines.size() == 3);
  const std::string& l = lines.back();
  const double fps = atof(field(l, "fps").c_str());
  CHECK(9.0 <= fps && fps <= 10.5 && field(l, "lock") == "10" && field(l, "miss") == "0");
  CHECK(field(l, "late_avg") == "0" && field(l, "late_max") == "0");   // the fake loop wakes on the slot
  CHECK(atoi(field(l, "max").c_str()) <= 106 && atof(field(l, "jit").c_str()) < 5.0);
  const int cost = atoi(field(l, "cost").c_str());
  CHECK(70 <= cost && cost <= 90);
  const std::string lock0 = field(lines[0], "lock");
  CHECK(lock0 == "20" || lock0 == "10");   // first window: the drop shows
}

// test_fps_line_off_by_default
TEST(runtime_fps_line_off_by_default) {
  MsClock clock(0);
  FakeDisplay d(&clock, {7, 8, 9});
  FakeImu imu(clock);
  app::Parts parts;
  parts.display = &d;
  parts.imu = &imu;
  auto rt = std::make_unique<R::Runtime>(parts, clock);
  const size_t mark = hmt::log_lines.size();
  run(*rt, clock, 12000);
  CHECK(fps_lines(mark).empty());
}

// test_found_lights_face_down_panels_for_10s: ui-spec §8 Event wake: both
// wrists down, both panels asleep; the bump's FOUND wakes both panels and
// lights them for FOUND_LIT_MS, then they sleep.
TEST(runtime_found_lights_face_down_panels_for_10s) {
  MsClock clock(0);
  SimRadio ra(clock, MAC_A), rb(clock, MAC_B);
  connect(ra, rb, -50);
  const uint32_t t0 = 16000;
  const std::vector<Spike> spikes = {{2000, 100000, -2000}, {t0, 2, 3000}};   // face down from 2 s; the knock
  Watch a(clock, &ra, spikes, {}, {{1000, app::EV_SHORT}});
  Watch b(clock, &rb, spikes, {}, {{1100, app::EV_SHORT}});
  Watch* ws[2] = {&a, &b};
  for (Watch* w : ws) {
    w->rt->begin();
    w->rt->game->pair.split_s = 1;
    w->rt->game->buzz = game::BUZZ_OFF;
  }
  const auto run_to = [&](uint32_t t_end) {
    while (clock.now < t_end) {
      int32_t w = 1000;
      for (Watch* x : ws) {
        const int32_t d = x->rt->step();
        if (d < w) w = d;
      }
      clock.now += w > 0 ? w : 1;
    }
  };
  const auto lit = [](const Watch* w) { return w->rt->screen_is_on && w->rt->bl_level && *w->rt->bl_level > 0.0; };
  run_to(t0 - 100);
  for (Watch* w : ws) CHECK(w->display.asleep && !w->rt->screen_is_on);
  run_to(t0 + 600);
  for (Watch* w : ws) CHECK(w->rt->game->mode == game::FOUND && lit(w) && !w->display.asleep);
  ticks_t ft = 0;
  for (Watch* w : ws) {
    CHECK(w->rt->game->found_t);
    if (w->rt->game->found_t && *w->rt->game->found_t > ft) ft = *w->rt->game->found_t;
  }
  run_to(ft + T::FOUND_LIT_MS - 200);
  for (Watch* w : ws) CHECK(lit(w));
  run_to(ft + T::FOUND_LIT_MS + 300);
  for (Watch* w : ws) CHECK(w->display.asleep && w->rt->game->mode == game::FOUND);
}
