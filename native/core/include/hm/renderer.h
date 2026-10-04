// ui/renderer.py: one RenderParams -> a 240x240 RGB565 frame (byte-swapped,
// as framebuf stores it), drawn strip by strip.
//
// The renderer reads only RenderParams (ui-spec §3). It owns the time-based
// state the contract leaves to it: ring spawns and positions, crossfades,
// iris open/close, arrow smoothing, the wedge glide between 10 Hz params,
// toast motion and phase timers. Per frame:
//
//   hm::haptic_patterns::Haptic beat = r.frame(p, now);   // now: the render clock
//   for (int y = 0; y < 240; y += 24) {                   // any strip height
//     r.draw_strip(p, y, 24, buf[k]);                      // the field, then the overlays
//     lcd.push_strip(buf[k], 24);                           // while the next one is drawn
//     k ^= 1;
//   }
//
// frame() advances the state (the Python's _step) and, when the frame is
// drawn, plans the overlays and builds the field palette (_snap, _plan,
// _palette). draw_strip() writes rows y0..y0+h-1 of that frame and changes
// no state, so a frame drawn in strips of any height has exactly the pixels
// of the frame drawn whole (draw(), the Python's own way). p must be the
// params frame() was given. beat is the heartbeat to start now (a live ring
// spawn; hm::haptic_patterns::NONE otherwise); the caller plays p.haptic
// itself, as in the Python.
//
// With the screen off, frame(p, now, false) keeps time; the first drawn frame
// after that is the current state with no intro (§8): no arrow scale-in, glide
// or expire shrink, and a toast already up sits at rest.
//
// Nothing here allocates: the arrow, wedge and text are drawn from member
// buffers and the font rows. Every derived timestamp uses ticks_add, and
// one-shot animations are gated by flags, so nothing breaks at the 2^30 ms
// ticks wrap.
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/field.h"
#include "hm/glyphs.h"
#include "hm/haptic_patterns.h"
#include "hm/render_params.h"
#include "hm/ticks.h"

namespace hm {
namespace ui {

class Renderer {
 public:
  static constexpr int W = 240;

  Renderer();
  Renderer(const Renderer&) = delete;
  Renderer& operator=(const Renderer&) = delete;

  void reset();
  // Advance to now; with draw (the Python's display), plan the frame for draw_strip.
  haptic_patterns::Haptic frame(const render_params::RenderParams& p, ticks_t now, bool draw = true);
  // Rows y0..y0+h-1 of the last drawn frame into px (W*h pixels).
  void draw_strip(const render_params::RenderParams& p, int y0, int h, uint16_t* px);
  void draw(const render_params::RenderParams& p, uint16_t* frame) { draw_strip(p, 0, W, frame); }

  RippleField field;

 private:
  using RP = render_params::RenderParams;
  using Sub = render_params::Sub;

  haptic_patterns::Haptic step(const RP& p, ticks_t t);
  void cal_fill(const RP& p, ticks_t t, ticks_t prev_t, bool restart);
  void snap(const RP& p, ticks_t t);
  void set_ad(double ad);
  void wedge_reset(const render_params::Sweep& sw);
  int wedge_deg(const render_params::Sweep& sw, ticks_t t);
  void plan(const RP& p, ticks_t t);
  void plan_morph(const RP& p, ticks_t t);
  uint16_t word_col(const RP& p) const;
  void palette(const RP& p, ticks_t t);
  void draw_bins(const RP& p, FrameBuffer& fb, int which);
  void draw_runes(const RP& p, FrameBuffer& fb);

  uint8_t map_[W * W];   // ring-index map (ui/field.py RingMap)
  int32_t dt_;           // the field's smoothed frame interval, ms (RippleField keeps its own private)
  ticks_t t_;            // the last drawn frame's clock
  int scr_;
  Sub sub_;
  ticks_t sub_t0_;
  bool saver_, dark_;
  std::optional<ticks_t> burst_t_;
  render_params::OptText toast_s_;
  render_params::Severity toast_sev_;
  bool toast_st_, toast_out_;
  ticks_t toast_t0_;
  int32_t cal_ms_;
  int sw_a_, sw_b_;
  ticks_t sw_t_;
  int32_t adq_;
  bool a_on_, a_grow_, a_shrink_;
  int a_scr_;
  int32_t a_q_, a_tgt_;
  ticks_t a_t0_, a_off_t0_;
  int a_style_, a_cone_;
  int morph_k_;
  bool morphed_;
  int32_t morph_a_;
  // per-frame plan
  int morph_, morph_e_;
  int g_;
  bool arrow_, sweep_, pacer_;
  Arrow arrow_buf_;
  Wedge wedge_;
  int top_, bot_;
  bool bot_slot_;
  render_params::Text top_s_, bot_s_;
  uint16_t top_c_, bot_c_;
  int bot_dy_, bot_mark_;
  int bot_band_;
};

}  // namespace ui
}  // namespace hm
