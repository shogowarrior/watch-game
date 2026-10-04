// ui/text.py: text in the font faces, chips, the StatusStrip, the
// DistanceReadout pill, word pills, toasts, menu rows and countdown digits
// (ui-spec §2). Screen coordinates, drawn into a FrameBuffer. The Python
// caches a bitmap per string and blits it in the ink colour; draw_text draws
// the same ink pixels straight from the font rows, so nothing is cached and
// nothing allocates.
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/font.h"
#include "hm/framebuf.h"
#include "hm/glyphs.h"
#include "hm/render_params.h"

namespace hm {
namespace ui {

// Width of s in face: 8 * SX * len(s).
int width(int face, const char* s);
// TextCache.draw: s with its top-left at (x, y) in colour c; returns its width.
int draw_text(FrameBuffer& fb, int face, const char* s, int x, int y, uint16_t c);
// TextCache.num: str(n) for small ints (battery %, timers), on the stack.
struct Num {
  char s[12];
  explicit Num(int32_t n);
};

// Opaque rounded chip; border >= 0 adds a 2 px border in that colour.
void chip(FrameBuffer& fb, int x, int y, int w, int h, int r, uint16_t fill, int32_t border = -1);
// Top-slot hint chip (type.label, radius.md) centred on x 120; mark != 0
// appends a 12 px trend mark in c (the LINK-LOST LAST chip).
void draw_top_chip(FrameBuffer& fb, const char* s, uint16_t c, int mark = 0);
// StatusStrip (y 12..31): own battery | link bars | partner battery. The link
// bars turn status.warn at link_q <= 1 or while unreliable (§5.5).
void draw_status(FrameBuffer& fb, std::optional<int32_t> own, std::optional<int32_t> partner, int link_q,
                 bool unreliable);
// Readout pill width; slot reserves the 12 + 6 px trend-mark slot (§2).
int readout_width(const char* band, bool slot);
// DistanceReadout pill: [mark] numeral + "M" (h 40, radius h/2). slot keeps
// the mark's slot while the trend is 0, so the numerals hold still.
void draw_readout(FrameBuffer& fb, const char* band, int mark, bool slot);
// Word pill: type.word, h 40, text y 190..221, width 16n + 24.
void draw_word(FrameBuffer& fb, const char* s, uint16_t c);
// Toast / banner border colour per severity (SEV_COL).
uint16_t sev_col(render_params::Severity sev);
// Toast / banner: full bottom slot, 2 px border, dy px below it.
void draw_toast(FrameBuffer& fb, const char* s, uint16_t border, int dy);
// MENU row at y (h 40); the selected row has a prox.5 border.
void draw_menu_row(FrameBuffer& fb, int y, const char* s, bool selected);
// type.display digits centred at (120, 120): 1 digit x 108..132, y 96..144.
void draw_countdown(FrameBuffer& fb, const char* s);

}  // namespace ui
}  // namespace hm
