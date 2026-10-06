// The tests' reach into hm::game::Game's private members (its friend Probe):
// the Python tests call g._emit, g._start_scan, set g._held and read g._toast
// and the like.
// One definition for every test file.
#pragma once
#include "hm/game.h"

namespace hm {
namespace game {

struct Probe {
  static void emit(Game& g, ticks_t t, hp::Haptic h) { g.emit(t, h); }
  static void hold(Game& g, hp::Haptic h) { g.held_ = h; }   // g._held = name: plays on the next tick
  static void new_round(Game& g, ticks_t t) { g.new_round(t); }
  static void start_scan(Game& g, ticks_t t) { g.start_scan(t); }
  static void toast_set(Game& g, const char* text, rp::Severity sev) { g.toast_set(text, sev); }
  static void hint_set(Game& g, ticks_t t, const char* text) { g.hint_set(t, text); }
  static int32_t state_byte(const Game& g, ticks_t t) { return g.state_byte_(t); }
  static int32_t peer_hz(const Game& g) { return g.peer_hz(); }
  static const char* screen(const Game& g) { return g.screen_(); }
  static bool toast_on(const Game& g) { return g.toast_on_; }
  static opt_ticks bye_t(const Game& g) { return g.bye_t_; }
  static ticks_t wake_t(const Game& g) { return g.wake_t_; }
  static ticks_t input_t(const Game& g) { return g.input_t_; }
  static opt_ticks touch_block(const Game& g) { return g.touch_block_; }
  static int touches(const Game& g) { return g.touches_.size(); }   // landings in the burst ring
};

}  // namespace game
}  // namespace hm
