#include "hm/runtime.h"

#include <string.h>

namespace hm {
namespace runtime {

namespace hp = haptic_patterns;
namespace rp = render_params;

Runtime::Runtime(const app::Parts& parts, Clock& clock, int32_t fps_log_ms)
    : parts(parts), clock(clock), fps_log_ms(fps_log_ms), link(GAME_ID) {
  tx.game_id = rxb.game_id = GAME_ID;
}

void Runtime::begin() {
  if (started) return;
  app::Pmu* pmu = parts.pmu;
  // the long press (§8, AXP202 PEK); presses latched before the loop never open MENU
  if (pmu && !(pmu->set_long_press_ms(T::BUTTON_LONG_MS) && pmu->clear_irqs())) io_errors[S_BUTTON] += 1;
  const ticks_t now = now_ms();
  std::optional<Mac> mac;
  if (parts.radio) {
    mac.emplace();
    memcpy(mac->b, parts.radio->mac(), sizeof mac->b);
    // hal/radio.py EspNowRadio: a clock seed with the MAC mixed in, so two
    // watches never beat in lock-step
    sched.rng = link::Xorshift16(link::Xorshift16(now & 0xFFFF).x ^ (mac->b[4] << 8) ^ mac->b[5]);
  }
  if (parts.imu) feed.emplace(*parts.imu, &Runtime::tap_cb, this, IMU_OUT_HZ);
  game.emplace(mac, nullptr, arrow::MODE_GUIDED, feed ? &Runtime::blank_cb : nullptr, this, now);
  touch_t_ = now;
  fps_t_ = now;
  t_tick_ = now;
  pacer.reset(now);
  log_t_ = now;
  t_input_ = now;
  t_batt_ = now;
  // a panel left asleep (a shell's earlier run): screen() wakes it
  screen_is_on = !(parts.display && parts.display->asleep);
  started = true;
  running = true;
}

int32_t Runtime::step() {
  if (!started) begin();
  ticks_t now = now_ms();
  const ticks_t t0 = now;
  loops += 1;
  if (powered_off) return MAX_SLEEP_MS;
  uint32_t a = clock.now_us();
  if (parts.radio) {
    stage_radio(now);
    a = acc(S_RADIO, a);
  }
  if (feed) {
    stage_imu(now);
    a = acc(S_IMU, a);
  }
  if (parts.touch) {
    stage_touch(now);
    a = acc(S_TOUCH, a);
  }
  if (parts.pmu) {
    stage_button(now);
    a = acc(S_BUTTON, a);
  }
  if (ticks_diff(now, t_tick_) >= 0) {
    stage_logic(now);
    a = acc(S_LOGIC, a);
    if (powered_off) return MAX_SLEEP_MS;
    now = now_ms();   // a panel wake blocks ~120 ms (SLPOUT)
  }
  if (ticks_diff(now, pacer.t_next) >= 0) {
    stage_render(now, t0);
    a = acc(S_RENDER, a);
    now = now_ms();
  }
  if (parts.radio) {
    stage_tx(now);
    a = acc(S_TX, a);
  }
  stage_haptic(now);
  acc(S_HAPTIC, a);
  t_input_ = ticks_add(now, INPUT_MS);
  if (fps_log_ms && ticks_diff(now, log_t_) >= fps_log_ms) fps_log(now);
  const ticks_t t = now_ms();
  busy_ += ticks_diff(t, t0);
  return wait(t);
}

int32_t Runtime::wait(ticks_t now) const {
  ticks_t nx = t_input_;
  if (ticks_diff(t_tick_, nx) < 0) nx = t_tick_;
  if (ticks_diff(pacer.t_next, nx) < 0) nx = pacer.t_next;
  const opt_ticks d = player.beat_due();
  if (d && ticks_diff(*d, nx) < 0) nx = *d;
  if (parts.radio && sched.next_t && ticks_diff(*sched.next_t, nx) < 0) nx = *sched.next_t;
  const int32_t w = ticks_diff(nx, now);
  return w > MAX_SLEEP_MS ? MAX_SLEEP_MS : w > 0 ? w : 0;
}

void Runtime::idle(int32_t ms) {
  const ticks_t end = ticks_add(now_ms(), ms);
  if (!hap_active()) {
    const opt_ticks d = player.beat_due();
    if (!d || ticks_diff(*d, end) > 0) {
      clock.delay_ms(ms);
      return;
    }
    const int32_t r = ticks_diff(*d, now_ms());
    if (r > 0) clock.delay_ms(r);
  }
  for (;;) {
    stage_haptic(now_ms());
    const int32_t r = ticks_diff(end, now_ms());
    if (r <= 0) return;
    clock.delay_ms(r > HAPTIC_SLICE_MS ? HAPTIC_SLICE_MS : r);
  }
}

void Runtime::quiet() {
  if (parts.motor) parts.motor->set(0);
  if (feed) feed->set_fast(false);   // a bus error leaves it as the chip reports
}

// ---- stages ------------------------------------------------------------------

void Runtime::stage_radio(ticks_t now) {
  const std::optional<Mac>& pm = game->pair.peer_mac;
  if (pm != link.partner) {
    if (pm)
      link.lock(*pm);
    else
      link.unlock();
  }
  parts.radio->poll(now, &Runtime::rx_cb, this);
}

void Runtime::on_rx(const app::RadioFrame& f) {
  Mac mac;
  memcpy(mac.b, f.mac, sizeof mac.b);
  const int r = link.on_packet(mac, f.data, f.len, f.t_ms);
  if (r == link::R_BAD || r == link::R_DUP) return;
  rxb.unpack(f.data);
  game->on_packet(f.t_ms, mac, f.rssi, rxb);
}

void Runtime::stage_imu(ticks_t now) {
  if (feed->poll(now) < 0) io_errors[S_IMU] += 1;
}

void Runtime::on_tap(ticks_t t) { game->on_accel_tap(t); }

// The touch panel -> GestureRecognizer at t; a landing finger and a gesture
// wait for stage_touch (at most one of each per frame).
void Runtime::sample_touch(ticks_t t) {
  touch_t_ = t;
  const app::TouchPoint& r = parts.touch->read();
  const int code = gestures.update(t, r.touching, r.x, r.y, r.contacts > 1);
  if (gestures.began && !td_t_) td_t_ = t;
  if (code && !g_code_) {
    g_code_ = code;
    g_t_ = t;
    g_x_ = gestures.ev_x;
    g_y_ = gestures.ev_y;
    g_t0_ = gestures.ev_t;
  }
}

void Runtime::stage_touch(ticks_t now) {
  sample_touch(now);
  const int code = g_code_;
  opt_ticks td = td_t_;   // in time order; on one sample the gesture first (it ended the press before that finger)
  td_t_.reset();
  if (td && (!code || ticks_diff(g_t_, *td) > 0)) {
    game->on_touch_down(*td);   // a finger landed (rain filter)
    td.reset();
  }
  if (code) {
    g_code_ = 0;
    game->on_gesture(g_t_, code, g_x_, g_y_, g_t0_);
  }
  if (td) game->on_touch_down(*td);   // landed as (or after) the gesture's press ended
}

void Runtime::stage_button(ticks_t now) {
  const int ev = parts.pmu->poll();
  if (ev < 0) {
    io_errors[S_BUTTON] += 1;
    return;
  }
  if (ev & app::EV_LONG) game->on_button(now, true);
  if (ev & app::EV_SHORT) game->on_button(now, false);
}

void Runtime::stage_logic(ticks_t now) {
  game::Game& g = *game;
  const ticks_t nx = ticks_add(t_tick_, TICK_MS);
  t_tick_ = ticks_diff(nx, now) > 0 ? nx : ticks_add(now, TICK_MS);
  if (parts.pmu && ticks_diff(now, t_batt_) >= 0) {
    t_batt_ = ticks_add(now, BATTERY_MS);
    battery(now);
  }
  if (feed) g.set_tracker(now, feed->tracker);
  if (fresh_ && params->haptic != hp::NONE) player.play_named(params->haptic, now);   // no frame drew it
  params = &g.tick(now);
  fresh_ = true;
  if (feed && !feed->set_fast(g.bump_armed())) io_errors[S_IMU] += 1;
  ticks += 1;
  if (g.buzz != buzz_) {
    buzz_ = g.buzz;
    player.set_mode(buzz_);   // game::BUZZ_* are HapticPlayer modes
  }
  screen(now);
  if (g.power_off) power_off(now);
}

// A reading at or under BATT_WARN_PCT lower than the game's value reaches it
// only after BATT_LOW_READS such readings in a row, and a shutdown-level one
// never on USB: one sagging reading never alarms, saves or powers off.
void Runtime::battery(ticks_t now) {
  app::Pmu* pmu = parts.pmu;
  int32_t pct = pmu->battery_percent();
  const int usb = pct < 0 ? -1 : pmu->vbus_present();
  if (usb < 0) {
    io_errors[S_LOGIC] += 1;
    return;
  }
  if (usb && pct <= T::BATT_SHUTDOWN_PCT) pct = T::BATT_SHUTDOWN_PCT + 1;   // on USB: never an automatic power-off
  game->set_usb(now, usb != 0);   // on USB: the screen stays on (§8)
  const std::optional<int32_t> b = game->battery;
  if (pct <= T::BATT_WARN_PCT && (!b || pct < *b)) {
    low_n_ += 1;   // a drop on the LOW-BATTERY ladder: confirm it
    if (low_n_ < BATT_LOW_READS) {
      t_batt_ = ticks_add(now, BATT_RECHECK_MS);
      return;
    }
  }
  low_n_ = 0;
  game->set_battery(now, pct);
}

void Runtime::screen(ticks_t now) {
  const bool on = game->screen_on;
  if (on == screen_is_on) return;
  screen_is_on = on;
  app::Display* d = parts.display;
  if (d) {
    if (on)
      d->wake(0, &Runtime::wait_cb, this);   // dark until a fresh frame is out; motor ticks meanwhile
    else
      d->sleep();
  }
  bl_level = 0.0;
  busy_ = 0;
  if (on) pacer.restart(now);   // first frame at once (§8: no intro)
}

void Runtime::backlight(double level) {
  if (bl_level && level == *bl_level) return;
  bl_level = level;
  if (parts.display) parts.display->brightness(level);
}

// The game said so: motor off, panel asleep, then the AXP202 cuts the power.
void Runtime::power_off(ticks_t) {
  powered_off = true;
  running = false;
  quiet();
  if (parts.display) parts.display->sleep();
  app::Pmu* pmu = parts.pmu;
  if (!pmu) return;
  for (int k = 0; k < PMU_OFF_TRIES; k++) {
    if (pmu->shutdown()) return;
    io_errors[S_LOGIC] += 1;
  }
}

// A frame on the lock's grid, drawn at its slot time; t0 is when this loop
// pass started (its busy time up to here belongs to this frame).
void Runtime::stage_render(ticks_t now, ticks_t t0) {
  if (!params) return;
  const rp::RenderParams& p = *params;
  app::Display* d = screen_is_on ? parts.display : nullptr;
  std::optional<int32_t> busy;
  if (d) {   // state-only frames leave the lock alone
    const int32_t pre = ticks_diff(now, t0);
    busy = busy_ + pre;
    busy_ = -pre;   // the rest of this pass: the next frame's
  } else {
    busy_ = 0;
  }
  const ticks_t slot = pacer.begin(now, p.fps_cap ? p.fps_cap : T::FPS_TARGET, busy);
  const hp::Haptic event = fresh_ ? p.haptic : hp::NONE;   // params.haptic plays once
  fresh_ = false;
  const hp::Haptic beat = renderer.frame(p, slot, d != nullptr);
  if (d) {
    int k = 0;
    for (int y = 0; y < ui::Renderer::W; y += STRIP_ROWS) {
      renderer.draw_strip(p, y, STRIP_ROWS, strips_[k]);
      d->push_strip(y, STRIP_ROWS, strips_[k]);
      service();
      k ^= 1;
    }
    d->flush();
  }
  now = now_ms();   // events start when the frame is out
  if (d) {
    pacer.shown(now);
    backlight(p.backlight);   // lit only over a fresh frame
    frames += 1;
    fps_n_ += 1;
  }
  const int32_t el = ticks_diff(now, fps_t_);
  if (el >= 1000) {
    fps = fps_n_ * 1000.0 / el;
    fps_n_ = 0;
    fps_t_ = now;
  }
  if (event != hp::NONE) player.play_named(event, now);
  if (beat != hp::NONE) player.heartbeat(beat, now);
}

// After each strip: pulse edges stay within a strip of schedule mid-frame,
// and touch is sampled once TOUCH_GAP_MS have passed since the last sample.
void Runtime::service() {
  const ticks_t t = now_ms();
  stage_haptic(t);
  if (parts.touch && ticks_diff(t, touch_t_) >= TOUCH_GAP_MS) sample_touch(t);
}

// hal/radio.py set_rate: the beacon period, jitter ~10 % of it (>= 1 ms).
void Runtime::set_rate(int32_t hz) {
  const int32_t per = 1000 / hz;
  if (per != sched.period) {
    sched.period = per;
    sched.jitter = per / 10 ? per / 10 : 1;
  }
}

void Runtime::stage_tx(ticks_t now) {
  set_rate(game->beacon_hz());
  if (!sched.due(now)) return;
  tx.next_seq();
  game->fill_beacon(tx, now);
  tx.pack(txbuf_);
  sched.mark_sent(now);
  if (!parts.radio->send(txbuf_, sizeof txbuf_)) io_errors[S_TX] += 1;
}

void Runtime::stage_haptic(ticks_t now) {
  const double lvl = player.tick(now);
  if (lvl == motor_lvl_) return;
  motor_lvl_ = lvl;
  if (feed) feed->motor(now, lvl);
  if (parts.motor) parts.motor->set(lvl);
}

// "HM fps fps= lock= miss= late_avg= late_max= jit= max= cost= log_us=": the
// Python's fps line (frames shown per second, the lock, slots missed, how late
// frames started after their slot, the sd and max of the shown-frame
// interval, the lock's frame cost, the previous line's print time).
void Runtime::fps_log(ticks_t now) {
  const int32_t el = ticks_diff(now, log_t_);
  const int32_t n = pacer.frames;
  const uint32_t a = clock.now_us();
  const int32_t f10 = el > 0 ? (int32_t)(((int64_t)n * 10000 + el / 2) / el) : 0;
  const int32_t j10 = (int32_t)(pacer.jitter_ms() * 10 + 0.5);
  logf("HM fps fps=%d.%d lock=%d miss=%d late_avg=%d late_max=%d jit=%d.%d max=%d cost=%d log_us=%u", (int)(f10 / 10),
       (int)(f10 % 10), (int)pacer.fps, (int)pacer.missed, (int)(n ? pacer.late_sum / n : 0), (int)pacer.late_max,
       (int)(j10 / 10), (int)(j10 % 10), (int)pacer.iv_max, (int)pacer.cost, (unsigned)log_us_);
  log_us_ = clock.now_us() - a;
  log_t_ = now;
  pacer.window();
}

uint32_t Runtime::acc(int i, uint32_t a) {
  const uint32_t b = clock.now_us();
  const uint32_t d = b - a;
  st_us[i] += d;
  st_n[i] += 1;
  if (d > st_max[i]) st_max[i] = d;
  if (st_us[i] > ACC_LIMIT_US) {   // long window: decay, keep the average
    st_us[i] >>= 1;
    st_n[i] >>= 1;
  }
  return b;
}

}  // namespace runtime
}  // namespace hm
