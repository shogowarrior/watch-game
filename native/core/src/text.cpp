// Port of ui/text.py.
#include "hm/text.h"

#include <string.h>

namespace hm {
namespace ui {

namespace {

constexpr int TOP_Y = T::TOP_SLOT[1], TOP_H = T::TOP_SLOT[3];   // y 12..35 (§2)
constexpr int BOT_X = T::BOTTOM_SLOT[0], BOT_Y = T::BOTTOM_SLOT[1], BOT_W = T::BOTTOM_SLOT[2],
              BOT_H = T::BOTTOM_SLOT[3];
constexpr int LINK_W = T::LINK_BARS_0, LINK_GAP = T::LINK_BARS_1;
constexpr const int32_t* LINK_H = T::LINK_BARS_2;

uint16_t batt_col(std::optional<int32_t> pct) {
  if (!pct) return LINE_SUBTLE;
  if (*pct <= T::BATT_CRITICAL_PCT) return CRIT;
  if (*pct <= T::BATT_WARN_PCT) return WARN;
  return TEXT_SEC;
}

void battery_icon(FrameBuffer& fb, int x, int y, std::optional<int32_t> pct, uint16_t c) {
  // 22x12 body, 2 px stroke, 2x6 nub, proportional fill
  fb.fill_rect(x, y, 22, 12, c);
  fb.fill_rect(x + 2, y + 2, 18, 8, SURF_CHIP);
  fb.fill_rect(x + 22, y + 3, 2, 6, c);
  if (pct && *pct > 0) {
    const int w = (14 * (*pct < 100 ? *pct : 100) + 50) / 100;
    if (w > 0) fb.fill_rect(x + 4, y + 4, w, 4, c);
  }
}

void pill(FrameBuffer& fb, int y, const char* s, int32_t border) {
  // bottom-slot-wide surface.toast pill (radius.lg) with centred label text
  chip(fb, BOT_X, y, BOT_W, BOT_H, 8, SURF_TOAST, border);
  draw_text(fb, font::LABEL, s, 120 - width(font::LABEL, s) / 2, y + 12, TEXT_PRI);
}

}  // namespace

int width(int face, const char* s) { return 8 * font::SX[face] * (int)strlen(s); }

int draw_text(FrameBuffer& fb, int face, const char* s, int x, int y, uint16_t c) {
  const int sx = font::SX[face], sy = font::SY[face], n = (int)strlen(s);
  if (y < fb.y1() && y + 8 * sy > fb.y0()) {
    for (int i = 0; i < n; i++) {
      const uint8_t* rows = font::char_rows(s[i]);
      const int x0 = x + 8 * sx * i;
      for (int r = 0; r < 8; r++) {
        const int yr = y + r * sy;
        const unsigned v = rows[r];
        if (!v || yr >= fb.y1() || yr + sy <= fb.y0()) continue;
        for (int b = 0; b < 8;) {   // each run of ink in the row is one block
          if (!(v & (0x80u >> b))) {
            b++;
            continue;
          }
          const int b0 = b;
          while (b < 8 && (v & (0x80u >> b))) b++;
          fb.fill_rect(x0 + b0 * sx, yr, (b - b0) * sx, sy, c);
        }
      }
    }
  }
  return 8 * sx * n;
}

Num::Num(int32_t n) {
  char d[11];
  int k = 0;
  uint32_t u = n < 0 ? 0u - (uint32_t)n : (uint32_t)n;
  do {
    d[k++] = (char)('0' + u % 10);
    u /= 10;
  } while (u);
  int i = 0;
  if (n < 0) s[i++] = '-';
  while (k) s[i++] = d[--k];
  s[i] = 0;
}

// ---- chips -------------------------------------------------------------------
void chip(FrameBuffer& fb, int x, int y, int w, int h, int r, uint16_t fill, int32_t border) {
  if (border >= 0) {
    rrect(fb, x, y, w, h, r, (uint16_t)border);
    rrect(fb, x + 2, y + 2, w - 4, h - 4, r > 2 ? r - 2 : 0, fill);
  } else {
    rrect(fb, x, y, w, h, r, fill);
  }
}

void draw_top_chip(FrameBuffer& fb, const char* s, uint16_t c, int mark) {
  const int tw = width(font::LABEL, s);
  const int w = tw + 16 + (mark ? 18 : 0);
  const int x = 120 - w / 2;
  chip(fb, x, TOP_Y, w, TOP_H, 6, SURF_CHIP);
  draw_text(fb, font::LABEL, s, x + 8, TOP_Y + 4, c);
  if (mark) draw_mark(fb, x + 8 + tw + 6 + 6, TOP_Y + 12, mark, c, c, SURF_CHIP);
}

void draw_status(FrameBuffer& fb, std::optional<int32_t> own, std::optional<int32_t> partner, int link_q,
                 bool unreliable) {
  // own battery 12..63
  const uint16_t c = batt_col(own);
  chip(fb, 12, 12, 52, 20, 6, SURF_CHIP);
  battery_icon(fb, 18, 16, own, c);
  if (own && *own < 100) draw_text(fb, font::MICRO, Num(*own).s, 46, 18, *own > T::BATT_WARN_PCT ? TEXT_TER : c);
  // link bars 98..141 (4 bars, w4 gap2, bottom-aligned at y 28)
  chip(fb, 98, 12, 44, 20, 6, SURF_CHIP);
  const uint16_t lc = (link_q <= 1 || unreliable) ? WARN : TEXT_SEC;
  for (int k = 0; k < 4; k++) {
    const int h = LINK_H[k];
    fb.fill_rect(109 + (LINK_W + LINK_GAP) * k, 29 - h, LINK_W, h, k < link_q ? lc : LINE_SUBTLE);
  }
  // partner 176..227: diamond mark + battery
  chip(fb, 176, 12, 52, 20, 6, SURF_CHIP);
  const uint16_t pc = batt_col(partner);
  draw_diamond(fb, 186, 22, partner ? TEXT_SEC : LINE_SUBTLE);
  battery_icon(fb, 196, 16, partner, pc);
}

int readout_width(const char* band, bool slot) { return 12 + (slot ? 18 : 0) + 16 * (int)strlen(band) + 2 + 8 + 12; }

void draw_readout(FrameBuffer& fb, const char* band, int mark, bool slot) {
  const int w = readout_width(band, slot);
  int x = 120 - w / 2;
  chip(fb, x, BOT_Y, w, BOT_H, 20, SURF_CHIP);
  x += 12;
  if (slot) {
    if (mark) draw_mark(fb, x + 6, BOT_Y + 20, mark, PROX[6], ACC_COLD, SURF_CHIP);
    x += 18;
  }
  x += draw_text(fb, font::WORD, band, x, BOT_Y + 4, TEXT_SEC);
  draw_text(fb, font::LABEL, "M", x + 2, BOT_Y + 18, TEXT_SEC);
}

void draw_word(FrameBuffer& fb, const char* s, uint16_t c) {
  const int w = width(font::WORD, s) + 24;
  const int x = 120 - w / 2;
  chip(fb, x, BOT_Y, w, BOT_H, 20, SURF_CHIP);
  draw_text(fb, font::WORD, s, x + 12, BOT_Y + 4, c);
}

uint16_t sev_col(render_params::Severity sev) {
  switch (sev) {
    case render_params::Severity::INFO:
      return PROX[5];
    case render_params::Severity::CRITICAL:
      return CRIT;
    default:
      return WARN;
  }
}

void draw_toast(FrameBuffer& fb, const char* s, uint16_t border, int dy) { pill(fb, BOT_Y + dy, s, border); }

void draw_menu_row(FrameBuffer& fb, int y, const char* s, bool selected) {
  pill(fb, y, s, selected ? PROX[5] : -1);
}

void draw_countdown(FrameBuffer& fb, const char* s) {
  const int w = width(font::DISPLAY, s);
  draw_text(fb, font::DISPLAY, s, 120 - w / 2, 96, TEXT_PRI);
}

}  // namespace ui
}  // namespace hm
