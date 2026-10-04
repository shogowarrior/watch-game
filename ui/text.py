"""Text and chips: cached glyph-string bitmaps, pills, StatusStrip, toasts.

Strings are rendered once per distinct (face, text) into MONO_HLSB
FrameBuffers and blitted with a 2-entry RGB565 palette plus a transparent
key. MicroPython compares the blit key after the palette lookup, so the key
is the palette's background entry.

All ``draw_*`` helpers take a FrameBuffer and the screen row ``y0`` of its
top (0 for the renderer's full frame) and use absolute screen coordinates. Nothing here allocates on a cache hit.
"""

import framebuf

from finder import tuning as T
from finder.compat import const
from ui import (ACC_COLD, CRIT, LINE_SUBTLE, PROX, SURF_CHIP, SURF_TOAST, TEXT_PRI,
                TEXT_SEC, TEXT_TER, WARN)
from ui import font
from ui.font import DISPLAY, LABEL, MICRO, WORD
from ui.glyphs import draw_diamond, draw_mark, rrect

CACHE_MAX = const(24)     # strings per face before the face cache is flushed


class TextCache:
    """(face, string) -> (MONO_HLSB FrameBuffer, w, h); colour -> palette."""

    def __init__(self):
        self._c = ({}, {}, {}, {})
        self._pal = {}
        self._num = {}
        self.misses = 0

    def get(self, face, s):
        d = self._c[face]
        e = d.get(s)
        if e is None:
            if len(d) >= CACHE_MAX:
                d.clear()
            buf, w, h = font.render(face, s)
            e = (framebuf.FrameBuffer(buf, w, h, framebuf.MONO_HLSB), w, h)
            d[s] = e
            self.misses += 1
        return e

    def pal(self, c):
        e = self._pal.get(c)
        if e is None:
            k = 1 if c == 0 else 0            # background entry != ink colour
            fb = framebuf.FrameBuffer(bytearray(4), 2, 1, framebuf.RGB565)
            fb.pixel(0, 0, k)
            fb.pixel(1, 0, c)
            e = (fb, k)
            self._pal[c] = e
        return e

    def num(self, n):
        """Cached decimal string for small ints (battery %, timers)."""
        s = self._num.get(n)
        if s is None:
            s = str(n)
            self._num[n] = s
        return s

    def draw(self, fb, y0, face, s, x, y, c):
        """Blit ``s`` with its top-left at (x, y); returns its width."""
        e = self.get(face, s)
        p = self.pal(c)
        fb.blit(e[0], x, y - y0, p[1], p[0])
        return e[1]


def width(face, s):
    return 8 * font.SX[face] * len(s)


# ---- chips -------------------------------------------------------------------
def chip(fb, y0, x, y, w, h, r, fill, border=-1):
    """Opaque rounded chip; ``border`` >= 0 adds a 2 px border in that colour."""
    y -= y0
    if border >= 0:
        rrect(fb, x, y, w, h, r, border)
        rrect(fb, x + 2, y + 2, w - 4, h - 4, r - 2 if r > 2 else 0, fill)
    else:
        rrect(fb, x, y, w, h, r, fill)


TOP_Y = T.TOP_SLOT[1]
TOP_H = T.TOP_SLOT[3]       # y 12..35 (§2)
BOT_X, BOT_Y, BOT_W, BOT_H = T.BOTTOM_SLOT


def draw_top_chip(tc, fb, y0, s, c, mark=0):
    """Top-slot hint chip (type.label, radius.md) centred on x 120.

    ``mark`` != 0 appends a 12 px trend mark in ``c`` (LINK-LOST LAST chip).
    """
    tw = width(LABEL, s)
    w = tw + 16 + (18 if mark else 0)
    x = 120 - w // 2
    chip(fb, y0, x, TOP_Y, w, TOP_H, 6, SURF_CHIP)
    tc.draw(fb, y0, LABEL, s, x + 8, TOP_Y + 4, c)
    if mark:
        draw_mark(fb, y0, x + 8 + tw + 6 + 6, TOP_Y + 12, mark, c, c, SURF_CHIP)


def _batt_col(pct):
    if pct is None:
        return LINE_SUBTLE
    if pct <= T.BATT_CRITICAL_PCT:
        return CRIT
    if pct <= T.BATT_WARN_PCT:
        return WARN
    return TEXT_SEC


def _battery_icon(fb, y0, x, y, pct, c):
    """22x12 body, 2 px stroke, 2x6 nub, proportional fill."""
    y -= y0
    fb.fill_rect(x, y, 22, 12, c)
    fb.fill_rect(x + 2, y + 2, 18, 8, SURF_CHIP)
    fb.fill_rect(x + 22, y + 3, 2, 6, c)
    if pct is not None and pct > 0:
        w = (14 * (pct if pct < 100 else 100) + 50) // 100
        if w > 0:
            fb.fill_rect(x + 4, y + 4, w, 4, c)


LINK_W, LINK_GAP, LINK_H = T.LINK_BARS


def draw_status(tc, fb, y0, own, partner, link_q, unreliable):
    """StatusStrip (y 12..31): own battery | link bars | partner battery.

    Link bars turn status.warn at link_q <= 1 or while ``unreliable`` (§5.5).
    """
    # own battery 12..63
    c = _batt_col(own)
    chip(fb, y0, 12, 12, 52, 20, 6, SURF_CHIP)
    _battery_icon(fb, y0, 18, 16, own, c)
    if own is not None and own < 100:
        tc.draw(fb, y0, MICRO, tc.num(own), 46, 18, TEXT_TER if own > T.BATT_WARN_PCT else c)
    # link bars 98..141 (4 bars, w4 gap2, bottom-aligned at y 28)
    chip(fb, y0, 98, 12, 44, 20, 6, SURF_CHIP)
    lc = WARN if (link_q <= 1 or unreliable) else TEXT_SEC
    for k in range(4):
        h = LINK_H[k]
        fb.fill_rect(109 + (LINK_W + LINK_GAP) * k, 29 - h - y0, LINK_W, h,
                     lc if k < link_q else LINE_SUBTLE)
    # partner 176..227: diamond mark + battery
    chip(fb, y0, 176, 12, 52, 20, 6, SURF_CHIP)
    pc = _batt_col(partner)
    draw_diamond(fb, y0, 186, 22, TEXT_SEC if partner is not None else LINE_SUBTLE)
    _battery_icon(fb, y0, 196, 16, partner, pc)


def readout_width(band, slot):
    """Pill width; ``slot`` reserves the 12 + 6 px trend-mark slot (§2)."""
    return 12 + (18 if slot else 0) + 16 * len(band) + 2 + 8 + 12


def draw_readout(tc, fb, y0, band, mark, slot):
    """DistanceReadout pill: [mark] numeral + 'M' (h 40, radius h/2).

    ``slot`` keeps the mark's slot even while the trend is 0, so the pill
    and numerals hold still as the mark comes and goes.
    """
    w = readout_width(band, slot)
    x = 120 - w // 2
    chip(fb, y0, x, BOT_Y, w, BOT_H, 20, SURF_CHIP)
    x += 12
    if slot:
        if mark:
            draw_mark(fb, y0, x + 6, BOT_Y + 20, mark, PROX[6], ACC_COLD, SURF_CHIP)
        x += 18
    x += tc.draw(fb, y0, WORD, band, x, BOT_Y + 4, TEXT_SEC)
    tc.draw(fb, y0, LABEL, "M", x + 2, BOT_Y + 18, TEXT_SEC)


def draw_word(tc, fb, y0, s, c):
    """Word pill: type.word, h 40, text y 190..221, width 16n + 24."""
    w = width(WORD, s) + 24
    x = 120 - w // 2
    chip(fb, y0, x, BOT_Y, w, BOT_H, 20, SURF_CHIP)
    tc.draw(fb, y0, WORD, s, x + 12, BOT_Y + 4, c)


SEV_COL = {"info": PROX[5], "warn": WARN, "critical": CRIT}


def _pill(tc, fb, y0, y, s, border):
    """Bottom-slot-wide surface.toast pill (radius.lg) with centred label text."""
    chip(fb, y0, BOT_X, y, BOT_W, BOT_H, 8, SURF_TOAST, border)
    tc.draw(fb, y0, LABEL, s, 120 - width(LABEL, s) // 2, y + 12, TEXT_PRI)


def draw_toast(tc, fb, y0, s, border, dy):
    """Toast / banner: full bottom slot, 2 px border, ``dy`` px below it."""
    _pill(tc, fb, y0, BOT_Y + dy, s, border)


def draw_menu_row(tc, fb, y0, y, s, selected):
    """MENU row at ``y`` (h 40); the selected row has a prox.5 border."""
    _pill(tc, fb, y0, y, s, PROX[5] if selected else -1)


def draw_countdown(tc, fb, y0, s):
    """type.display digits centred at (120, 120): 1 digit x 108..132, y 96..144."""
    w = width(DISPLAY, s)
    tc.draw(fb, y0, DISPLAY, s, 120 - w // 2, 96, TEXT_PRI)
