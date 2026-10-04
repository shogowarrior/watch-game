// Port of ui/glyphs.py's drawing; its import-time geometry is in ui_tables.h.
#include "hm/glyphs.h"

namespace hm {
namespace ui {

namespace {

constexpr int CHEV_STRONG_DY = T::CHEVRON_STACK_PX / 2;   // warmer: copies at y 112 / 128 (+-8)
constexpr int CHEV_DN_STRONG_DY = 13;   // colder: +-13 (107 / 133), so the bands stay apart
constexpr int SR = T::SEEKER_RING[0], SS = T::SEEKER_RING[1];   // ring r 14, stroke 4
constexpr int SK1 = T::SEEKER_TICKS_1, SK2 = T::SEEKER_TICKS_2, SK_W = T::SEEKER_TICKS_3;
constexpr int SK_LEN = SK2 - SK1;
constexpr int SW_R0 = T::SWEEP_R_INNER, SW_R1 = T::SWEEP_R_OUTER;
// keyline arc angles: 2 px beside a radial side is +1.0 deg at r 112, +1.7 at r 68
constexpr int KEY_OUT[4] = {-16, -5, 5, 16};
constexpr int KEY_IN[4] = {-17, -6, 6, 17};

inline int deg360(int deg) { return (int)floormod(deg, 360); }

constexpr uint16_t DART_COL[4] = {0, (uint16_t)PROX[7], (uint16_t)PROX[6], (uint16_t)PROX[5]};
constexpr uint16_t BEAM_COL[4] = {0, (uint16_t)PROX[4], (uint16_t)PROX[3], (uint16_t)PROX[2]};

void polar(int16_t* dst, int k, int32_t r, int deg) {
  deg = deg360(deg);
  dst[k] = (int16_t)((r * F::SIN[deg] + 8192) >> 14);
  dst[k + 1] = (int16_t)-((r * F::COS[deg] + 8192) >> 14);
}

void bin_box(int16_t* b, int a, int r0, int r1, int h) {
  polar(b, 0, r1, a - h);
  polar(b, 2, r1, a + h);
  polar(b, 4, r0, a + h);
  polar(b, 6, r0, a - h);
}

}  // namespace

void rot_into(int16_t* dst, const int16_t* src, int n, int deg, int32_t scale) {
  const int32_t c = F::COS[deg360(deg)], s = F::SIN[deg360(deg)];
  for (int k = 0; k < n; k += 2) {
    const int32_t x = src[k], y = src[k + 1];
    dst[k] = (int16_t)(((((x * c - y * s) >> 14) * scale) + 2048) >> 12);
    dst[k + 1] = (int16_t)(((((x * s + y * c) >> 14) * scale) + 2048) >> 12);
  }
}

// ---- arrow --------------------------------------------------------------------
void prep_arrow(Arrow& a, int deg, int cone, int32_t scale) {
  rot_into(a.dart, U::DART_Q4, 8, deg, scale);
  rot_into(a.key, U::DART_KEY_Q4, 8, deg, scale);   // 2 px mitred keyline
  rot_into(a.in, U::DART_IN_Q4, 8, deg, scale);     // outline tier inset
  a.beam_n = 0;
  if (cone <= 0) return;
  cone = cone < 12 ? 12 : (cone > 60 ? 60 : cone);
  const int span = 2 * cone;
  int n = (span + 9) / 10;
  if (n < 3) n = 3;
  a.beam[0] = a.beam[1] = 0;
  const int a0 = deg - cone;
  const int32_t r = (T::BEAM_R * scale) >> 8;
  for (int k = 0; k <= n; k++) polar(a.beam, 2 + 2 * k, r, a0 + (span * k) / n);
  a.beam_n = n + 2;
}

void draw_arrow(FrameBuffer& fb, int style, const Arrow& a) {
  if (a.beam_n) fb.poly(CX, CY, a.beam, a.beam_n, BEAM_COL[style], true);
  fb.poly(CX, CY, a.key, 4, BG_BASE, true);
  fb.poly(CX, CY, a.dart, 4, DART_COL[style], true);
  if (style == STYLE_OUT) fb.poly(CX, CY, a.in, 4, BG_IRIS, true);
}

// ---- chevrons -----------------------------------------------------------------
int32_t chevron_nudge(ticks_t t) {
  const int deg = (int)(t % (2 * T::CHEVRON_NUDGE_MS)) * 180 / T::CHEVRON_NUDGE_MS;
  return (T::CHEVRON_NUDGE_PX * (16384 - F::COS[deg]) + 16384) >> 15;
}

void draw_chevrons(FrameBuffer& fb, int trend, bool strong, int nudge) {
  // Strong = two copies: warmer centred at y 112 / 128, colder at 107 / 133
  // (the hollow band is 4 px wider than the filled shape, so 16 px apart the
  // two bands would fuse into one filled blob). Each colder copy is finished
  // (band, then interior) before the next is drawn.
  if (trend > 0) {
    const int dy = strong ? CHEV_STRONG_DY : 0;
    const int a = CY - dy - nudge;
    fb.poly(CX, a, U::CHEV_UP, 6, PROX[7], true);
    if (strong) fb.poly(CX, a + 2 * dy, U::CHEV_UP, 6, PROX[7], true);
  } else if (trend < 0) {
    const int dy = strong ? CHEV_DN_STRONG_DY : 0;
    int a = CY - dy + nudge;
    fb.poly(CX, a, U::CHEV_DN_OUT, 8, ACC_COLD, true);
    fb.poly(CX, a, U::CHEV_DN_IN, 6, BG_IRIS, true);
    if (strong) {
      a += 2 * dy;
      fb.poly(CX, a, U::CHEV_DN_OUT, 8, ACC_COLD, true);
      fb.poly(CX, a, U::CHEV_DN_IN, 6, BG_IRIS, true);
    }
  }
}

// ---- seeker, check, turn, battery, dots, runes ----------------------------------
void draw_seeker(FrameBuffer& fb) {
  // ring r 14 plus 4 ticks r 20..28, stroke.m, grey.7 (§6 SEARCHING)
  const uint16_t c = GREY[7];
  fb.ellipse(CX, CY, SR + SS / 2, SR + SS / 2, c, true);
  fb.ellipse(CX, CY, SR - SS / 2, SR - SS / 2, BG_IRIS, true);
  fb.fill_rect(CX - SK_W / 2, CY - SK2, SK_W, SK_LEN, c);
  fb.fill_rect(CX - SK_W / 2, CY + SK1, SK_W, SK_LEN, c);
  fb.fill_rect(CX - SK2, CY - SK_W / 2, SK_LEN, SK_W, c);
  fb.fill_rect(CX + SK1, CY - SK_W / 2, SK_LEN, SK_W, c);
}

void draw_check(FrameBuffer& fb) {
  fb.ellipse(CX, CY, T::CHECK_DISC_R, T::CHECK_DISC_R, ACC_FOUND, true);
  fb.poly(CX, CY, U::CHECK_A, 6, BG_BASE, true);
}

void draw_turn(FrameBuffer& fb) {
  // SCANNING sweep / result: the turn-right arc (the scan always turns right)
  fb.poly(CX, CY, U::TURN_R, (int)(sizeof U::TURN_R / sizeof U::TURN_R[0]) / 2, PROX[6], true);
  fb.poly(CX, CY, U::TURN_R_HEAD, 3, PROX[6], true);
}

void rrect(FrameBuffer& fb, int x, int y, int w, int h, int r, uint16_t c) {
  if (r <= 0) {
    fb.fill_rect(x, y, w, h, c);
    return;
  }
  fb.fill_rect(x + r, y, w - 2 * r, h, c);
  if (h > 2 * r) {
    fb.fill_rect(x, y + r, r, h - 2 * r, c);
    fb.fill_rect(x + w - r, y + r, r, h - 2 * r, c);
  }
  const int x1 = x + w - 1 - r, y1 = y + h - 1 - r;
  fb.ellipse(x + r, y + r, r, r, c, true, ELLIPSE_Q2);
  fb.ellipse(x1, y + r, r, r, c, true, ELLIPSE_Q1);
  fb.ellipse(x + r, y1, r, r, c, true, ELLIPSE_Q3);
  fb.ellipse(x1, y1, r, r, c, true, ELLIPSE_Q4);
}

void draw_battery(FrameBuffer& fb, int32_t pct) {
  // 64x32 rounded body, 3 px warn outline, nub
  const int y = 104;
  rrect(fb, 88, y, 64, 32, 6, WARN);
  rrect(fb, 91, y + 3, 58, 26, 3, BG_IRIS);
  fb.fill_rect(152, 114, 6, 12, WARN);
  if (pct > 0) {
    const int w = (54 * (pct < 100 ? pct : 100)) / 100;
    if (w > 0) fb.fill_rect(93, y + 5, w, 22, WARN);
  }
}

void draw_dots(FrameBuffer& fb) {
  // 3 line.subtle dots r 5 at the rune centres
  for (int x : T::RUNE_CENTERS_X) fb.ellipse(x, CY, 5, 5, LINE_SUBTLE, true);
}

void draw_rune(FrameBuffer& fb, int rid, int cx, uint16_t c) {
  const int k = rid & 7;
  for (int i = U::RUNE_FIRST[k]; i < U::RUNE_FIRST[k + 1]; i++) {
    const int16_t* op = U::RUNE_OPS[i];
    const int x = cx + op[1], y = CY + op[2], r = op[3];
    switch (op[0]) {
      case U::R_POLY:
        fb.poly(cx, CY, U::RUNE_QUADS[op[1]], 4, c, true);
        break;
      case U::R_DISC:
        fb.ellipse(x, y, r, r, c, true);
        break;
      case U::R_RING:
        fb.ellipse(x, y, r + 3, r + 3, c, true);
        fb.ellipse(x, y, r - 3, r - 3, BG_IRIS, true);
        break;
      default:   // R_CUT
        fb.ellipse(x, y, r, r, BG_IRIS, true);
    }
  }
}

// ---- sweep wedge, bins ------------------------------------------------------------
void prep_wedge(Wedge& wg, int deg) {
  // 30 deg sector r 70..110 centred on deg (clockwise from up), plus its 2 px
  // bg.base keyline (r 68..112, sides pushed out 2 px)
  for (int k = 0; k < 4; k++) {
    polar(wg.w, 2 * k, SW_R1, deg - 15 + 10 * k);
    polar(wg.w, 14 - 2 * k, SW_R0, deg - 15 + 10 * k);
    polar(wg.key, 2 * k, SW_R1 + 2, deg + KEY_OUT[k]);
    polar(wg.key, 14 - 2 * k, SW_R0 - 2, deg + KEY_IN[k]);
  }
}

void draw_wedge(FrameBuffer& fb, const Wedge& wg, uint16_t c) {
  // keyline first, so the wedge never merges with a halo or crest of the same
  // level behind it (the DIRECTION-TURN pacer sits on the live halo)
  fb.poly(CX, CY, wg.key, 8, BG_BASE, true);
  fb.poly(CX, CY, wg.w, 8, c, true);
}

bool wedge_hits_bottom(int deg) {
  // centre within 70 deg of 6 o'clock
  deg = deg360(deg);
  return 110 < deg && deg < 250;
}

void draw_bin(FrameBuffer& fb, int k, int32_t norm_q8, uint16_t c, uint16_t track, bool full) {
  // Radial bar k (6 deg wide at k*30 deg), r 70 -> 70 + 40*norm, in its dark
  // track (full false: only a 2 px keyline round the bar, for the active bin
  // drawn over the wedge, so the wedge stays lit past the bar)
  const int a = k * 30;
  int16_t b[8];
  int r1 = SW_R0 + (((SW_R1 - SW_R0) * norm_q8) >> 8);
  if (r1 < SW_R0 + 3) r1 = SW_R0 + 3;
  bin_box(b, a, SW_R0 - 2, full ? SW_R1 + 2 : r1 + 2, 4);
  fb.poly(CX, CY, b, 4, track, true);
  bin_box(b, a, SW_R0, r1, 3);
  fb.poly(CX, CY, b, 4, c, true);
}

void draw_bin_hollow(FrameBuffer& fb, int k, uint16_t c) {
  // no-data bin: 2 px outline of the full 70..110 box, in its track
  const int a = k * 30;
  int16_t b[8];
  bin_box(b, a, SW_R0 - 2, SW_R1 + 2, 4);
  fb.poly(CX, CY, b, 4, BG_IRIS, true);
  bin_box(b, a, SW_R0, SW_R1, 3);
  fb.poly(CX, CY, b, 4, c, false);
  bin_box(b, a, SW_R0 + 1, SW_R1 - 1, 2);
  fb.poly(CX, CY, b, 4, c, false);
}

// ---- small marks ------------------------------------------------------------------
void draw_mark(FrameBuffer& fb, int x, int y, int trend, uint16_t up_col, uint16_t dn_col, uint16_t bg) {
  // 12 px trend mark centred at (x, y): filled up / 2 px hollow down
  if (trend > 0) {
    fb.poly(x, y, U::MARK_UP, 3, up_col, true);
  } else if (trend < 0) {
    fb.poly(x, y, U::MARK_DN_OUT, 3, dn_col, true);
    fb.poly(x, y, U::MARK_DN_IN, 3, bg, true);
  }
}

void draw_diamond(FrameBuffer& fb, int x, int y, uint16_t c) { fb.poly(x, y, U::DIAMOND, 4, c, true); }

}  // namespace ui
}  // namespace hm
