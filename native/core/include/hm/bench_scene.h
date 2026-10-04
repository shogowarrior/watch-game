// The field part of ui/renderer.py's _step + _palette, for the screens the
// display bench draws (FAR..HOT with the glow glyph, PAIRING seen with runes):
// enough to drive RippleField exactly as the MicroPython renderer does for
// tools/bench_frame.py's fixtures. The full renderer port replaces it.
#pragma once
#include <stdint.h>

#include "hm/field.h"

namespace hm {

// Screen and glyph ids: index in T::SCREENS / T::GLYPHS (G_DOTS is renderer-only).
enum Screen : int8_t { S_PAIRING = 0, S_SEARCHING, S_FAR, S_NEAR, S_WARM, S_HOT, S_FOUND, S_SCANNING,
                       S_LINK_LOST, S_MENU };
enum Glyph : int8_t { G_GLOW = 0, G_SEEKER, G_CHEV, G_ARROW, G_COUNT, G_TURN, G_CHECK, G_RUNES, G_BATT,
                      G_DOTS };
enum Sub : int8_t { SUB_NONE = 0, SUB_LOOKING, SUB_SEEN };

// The RenderParams fields the field reads.
struct FieldParams {
  const char* name;
  Screen screen;
  Sub sub;
  int8_t zone;          // -1: none
  Ramp ramp;
  float intensity;
  int32_t speed_px_s;
  int32_t period_ms;
  float glow_r_px;
  bool ring_live;
  Glyph glyph;
  int32_t fps_cap;
};

// tools/bench_frame.py FIXTURES (field fields only).
extern const FieldParams BENCH_FIXTURES[3];

class FieldScene {
 public:
  explicit FieldScene(RippleField& f) : f_(f) {}
  // Advance the field to t for params p and rebuild its palette. Returns 1 on
  // a live ring spawn (a heartbeat is due), as the renderer does.
  int step(const FieldParams& p, ticks_t t);

 private:
  RippleField& f_;
  int8_t scr_ = -1;
  int8_t sub_ = -1;
};

}  // namespace hm
