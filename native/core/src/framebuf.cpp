// Port of MicroPython's extmod/modframebuf.c (v1.29.0): fill_rect, line, poly, ellipse.
#include "hm/framebuf.h"

namespace hm {
namespace ui {

void FrameBuffer::fill_rect(int x, int y, int w, int h, uint16_t c) {
  if (h < 1 || w < 1 || x + w <= 0 || y + h <= y0_ || y >= y1_ || x >= W) return;
  const int xend = x + w < W ? x + w : W;
  const int yend = y + h < y1_ ? y + h : y1_;
  if (x < 0) x = 0;
  if (y < y0_) y = y0_;
  for (; y < yend; y++) {
    uint16_t* p = px_ + (y - y0_) * W;
    for (int i = x; i < xend; i++) p[i] = c;
  }
}

void FrameBuffer::line(int x1, int y1, int x2, int y2, uint16_t c) {
  // Bresenham as framebuf: the last point is set on its own after the loop.
  int dx = x2 - x1, sx = 1;
  if (dx <= 0) {
    dx = -dx;
    sx = -1;
  }
  int dy = y2 - y1, sy = 1;
  if (dy <= 0) {
    dy = -dy;
    sy = -1;
  }
  const bool steep = dy > dx;
  if (steep) {
    int t = x1;
    x1 = y1;
    y1 = t;
    t = dx;
    dx = dy;
    dy = t;
    t = sx;
    sx = sy;
    sy = t;
  }
  int e = 2 * dy - dx;
  for (int i = 0; i < dx; ++i) {
    if (steep) pixel(y1, x1, c);
    else pixel(x1, y1, c);
    while (e >= 0) {
      y1 += sy;
      e -= 2 * dx;
    }
    x1 += sx;
    e += 2 * dy;
  }
  pixel(x2, y2, c);
}

void FrameBuffer::poly(int x, int y, const int16_t* xy, int n, uint16_t c, bool fill) {
  if (n <= 0) return;
  if (!fill) {
    int px1 = xy[0], py1 = xy[1];
    for (int i = n - 1; i >= 0; i--) {
      const int px2 = xy[2 * i], py2 = xy[2 * i + 1];
      line(x + px1, y + py1, x + px2, y + py2, c);
      px1 = px2;
      py1 = py2;
    }
    return;
  }
  if (n > POLY_MAX) return;
  // framebuf's integer scanline fill (http://alienryderflex.com/polygon_fill/):
  // per row, the sorted x where edges cross it, filled pairwise. Each row
  // only touches itself, so rows outside this buffer are skipped.
  int y_min = xy[1], y_max = xy[1];
  for (int i = 1; i < n; i++) {
    const int py = xy[2 * i + 1];
    if (py < y_min) y_min = py;
    if (py > y_max) y_max = py;
  }
  if (y_min < y0_ - y) y_min = y0_ - y;
  if (y_max > y1_ - 1 - y) y_max = y1_ - 1 - y;
  int nodes[POLY_MAX];
  for (int row = y_min; row <= y_max; row++) {
    int n_nodes = 0;
    int px1 = xy[0], py1 = xy[1];
    for (int i = n - 1; i >= 0; i--) {
      const int px2 = xy[2 * i], py2 = xy[2 * i + 1];
      // The bottom pixel of an edge is left out (it would repeat the next
      // edge's node); local minima and horizontal edges are drawn instead.
      if (py1 != py2 && ((py1 > row && py2 <= row) || (py1 <= row && py2 > row))) {
        nodes[n_nodes++] = (32 * px1 + 32 * (px2 - px1) * (row - py1) / (py2 - py1) + 16) / 32;
      } else if (row == (py1 > py2 ? py1 : py2)) {
        if (py1 < py2) pixel(x + px2, y + py2, c);
        else if (py2 < py1) pixel(x + px1, y + py1, c);
        else line(x + px1, y + py1, x + px2, y + py2, c);
      }
      px1 = px2;
      py1 = py2;
    }
    if (!n_nodes) continue;
    int i = 0;
    while (i < n_nodes - 1) {   // framebuf's bubble sort
      if (nodes[i] > nodes[i + 1]) {
        const int s = nodes[i];
        nodes[i] = nodes[i + 1];
        nodes[i + 1] = s;
        if (i) i--;
      } else {
        i++;
      }
    }
    for (i = 0; i + 1 < n_nodes; i += 2) fill_rect(x + nodes[i], y + row, nodes[i + 1] - nodes[i] + 1, 1, c);
  }
}

namespace {

void ellipse_points(FrameBuffer& fb, int cx, int cy, int x, int y, uint16_t c, bool fill, int mask) {
  if (fill) {
    if (mask & ELLIPSE_Q1) fb.fill_rect(cx, cy - y, x + 1, 1, c);
    if (mask & ELLIPSE_Q2) fb.fill_rect(cx - x, cy - y, x + 1, 1, c);
    if (mask & ELLIPSE_Q3) fb.fill_rect(cx - x, cy + y, x + 1, 1, c);
    if (mask & ELLIPSE_Q4) fb.fill_rect(cx, cy + y, x + 1, 1, c);
  } else {
    if (mask & ELLIPSE_Q1) fb.pixel(cx + x, cy - y, c);
    if (mask & ELLIPSE_Q2) fb.pixel(cx - x, cy - y, c);
    if (mask & ELLIPSE_Q3) fb.pixel(cx - x, cy + y, c);
    if (mask & ELLIPSE_Q4) fb.pixel(cx + x, cy + y, c);
  }
}

}  // namespace

void FrameBuffer::ellipse(int cx, int cy, int xr, int yr, uint16_t c, bool fill, int mask) {
  // framebuf's two-region midpoint ellipse. Radii 0, 0 would never leave the
  // first loop here; framebuf draws the centre point (checked on the wasm port).
  if (xr == 0 && yr == 0) return ellipse_points(*this, cx, cy, 0, 0, c, fill, mask);
  const int two_asquare = 2 * xr * xr, two_bsquare = 2 * yr * yr;
  int x = xr, y = 0;
  int xchange = yr * yr * (1 - 2 * xr), ychange = xr * xr;
  int err = 0, stoppingx = two_bsquare * xr, stoppingy = 0;
  while (stoppingx >= stoppingy) {   // 1st set of points, y' > -1
    ellipse_points(*this, cx, cy, x, y, c, fill, mask);
    y += 1;
    stoppingy += two_asquare;
    err += ychange;
    ychange += two_asquare;
    if (2 * err + xchange > 0) {
      x -= 1;
      stoppingx -= two_bsquare;
      err += xchange;
      xchange += two_bsquare;
    }
  }
  x = 0;
  y = yr;
  xchange = yr * yr;
  ychange = xr * xr * (1 - 2 * yr);
  err = 0;
  stoppingx = 0;
  stoppingy = two_asquare * yr;
  while (stoppingx <= stoppingy) {   // 2nd set of points, y' < -1
    ellipse_points(*this, cx, cy, x, y, c, fill, mask);
    x += 1;
    stoppingx += two_bsquare;
    err += xchange;
    xchange += two_bsquare;
    if (2 * err + ychange > 0) {
      y -= 1;
      stoppingy -= two_asquare;
      err += ychange;
      ychange += two_asquare;
    }
  }
}

}  // namespace ui
}  // namespace hm
