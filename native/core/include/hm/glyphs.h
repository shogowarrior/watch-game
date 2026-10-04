// ui/glyphs.py: centre glyphs (ui-spec §2, §6; tokens.glyphs), rounded rects,
// scan bins, the sweep wedge and the small trend marks, plus the token colours
// of ui/__init__.py. Every draw_* takes a FrameBuffer and screen coordinates
// (the Python's y - y0 is the FrameBuffer's business), so framebuf clips to
// it. Thick strokes are filled polygons or ellipse pairs; the polygons come
// from ui_tables.h, and the per-frame rotations write into the caller's Arrow
// and Wedge (the Python's module scratch arrays) with the Q14 sin/cos tables.
#pragma once
#include <stdint.h>

#include "hm/field_tables.h"
#include "hm/framebuf.h"
#include "hm/ticks.h"
#include "hm/tuning.h"
#include "hm/ui_tables.h"

namespace hm {
namespace ui {

// ---- ui/__init__.py: token colours, byte-swapped RGB565 --------------------
constexpr uint16_t BG_BASE = T::C_BG_BASE;
constexpr uint16_t BG_IRIS = T::C_BG_IRIS;
constexpr uint16_t SURF_CHIP = T::C_SURFACE_CHIP;
constexpr uint16_t SURF_TOAST = T::C_SURFACE_TOAST;
constexpr uint16_t LINE_SUBTLE = T::C_LINE_SUBTLE;
constexpr uint16_t TEXT_PRI = T::C_TEXT_PRIMARY;
constexpr uint16_t TEXT_SEC = T::C_TEXT_SECONDARY;
constexpr uint16_t TEXT_TER = T::C_TEXT_TERTIARY;
constexpr uint16_t ACC_COLD = T::C_ACCENT_COLD;
constexpr uint16_t WARN = T::C_STATUS_WARN;
constexpr uint16_t CRIT = T::C_STATUS_CRITICAL;
constexpr uint16_t ACC_FOUND = T::C_ACCENT_FOUND;
constexpr const int32_t* PROX = T::RAMP_SWAPPED_GREEN;   // prox.0 .. prox.7
constexpr const int32_t* GREY = T::RAMP_SWAPPED_GREY;

constexpr int CX = T::CENTER[0], CY = T::CENTER[1];

// Rotate n/2 Q4 points by integer deg (clockwise) and scale (Q8) -> px.
void rot_into(int16_t* dst, const int16_t* src, int n, int deg, int32_t scale);

// ---- arrow (dart + beam + keyline) ------------------------------------------
constexpr int STYLE_OUT = 3;   // STYLES: solid_a 1, solid_b 2, outline 3

// prep_arrow's output: the dart, its 2 px keyline and outline inset in px,
// and the beam sector (centre + n+1 arc points, n = 3..12).
struct Arrow {
  int16_t dart[8], key[8], in[8];
  int16_t beam[2 * 14];
  int beam_n;   // beam vertices; 0: no beam (the Python's None)
};
void prep_arrow(Arrow& a, int deg, int cone, int32_t scale);
void draw_arrow(FrameBuffer& fb, int style, const Arrow& a);

// ---- chevrons -----------------------------------------------------------------
int32_t chevron_nudge(ticks_t t);   // 0..6 px, 800 ms in_out_sine each way
void draw_chevrons(FrameBuffer& fb, int trend, bool strong, int nudge);

// ---- seeker, check, turn, battery, dots, runes ----------------------------------
void draw_seeker(FrameBuffer& fb);
void draw_check(FrameBuffer& fb);
void draw_turn(FrameBuffer& fb);
void rrect(FrameBuffer& fb, int x, int y, int w, int h, int r, uint16_t c);   // 2-3 fill_rects + 4 quadrants
void draw_battery(FrameBuffer& fb, int32_t pct);   // the 10 % interstitial glyph
void draw_dots(FrameBuffer& fb);                    // PAIRING looking
void draw_rune(FrameBuffer& fb, int rid, int cx, uint16_t c);
constexpr uint16_t RUNE_COL = TEXT_PRI;
constexpr uint16_t RUNE_WAIT = LINE_SUBTLE;
constexpr uint16_t RUNE_OK = T::RAMP_SWAPPED_GREEN[7];

// ---- HOT bump view (tokens.glyphs.bump_watches) ----------------------------------
// Two watch icons, yours left and the friend's right, with 3 rays above; bits
// is RenderParams.bump_icons. Each part is skipped on strips it misses.
void draw_bump(FrameBuffer& fb, int32_t bits);

// ---- sweep wedge, bins ------------------------------------------------------------
struct Wedge {
  int16_t w[16], key[16];   // 30 deg sector r 70..110 and its 2 px bg.base keyline
};
void prep_wedge(Wedge& w, int deg);
void draw_wedge(FrameBuffer& fb, const Wedge& w, uint16_t c);
bool wedge_hits_bottom(int deg);   // the wedge reaches the bottom slot (y >= 186)
void draw_bin(FrameBuffer& fb, int k, int32_t norm_q8, uint16_t c, uint16_t track = BG_IRIS, bool full = true);
void draw_bin_hollow(FrameBuffer& fb, int k, uint16_t c);

// ---- small marks (readout / LAST chip) ------------------------------------------
void draw_mark(FrameBuffer& fb, int x, int y, int trend, uint16_t up_col, uint16_t dn_col, uint16_t bg);
void draw_diamond(FrameBuffer& fb, int x, int y, uint16_t c);

}  // namespace ui
}  // namespace hm
