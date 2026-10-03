"""Watch UI: strip renderer for RenderParams (MicroPython framebuf).

Colour constants are the tokens.json colours as *byte-swapped* RGB565 ints
(tokens ``rgb565_swapped``), the values to put into framebuf RGB565 buffers
so the bytes go to the ST7789 big-endian. They come from finder/tuning.py,
which tools/gen_tuning.py generates from tokens.json. Not every token is
wired: the StatusStrip slots (layout.status_strip), glyphs.battery_icon,
glyphs.partner_mark and the chip radii (radius.md 6, radius.lg 8) are
literals in ui/text.py and ui/glyphs.py, so an edit to those tokens needs
the same edit there.
"""

from finder import tuning as T


def swap16(c):
    return ((c & 0xFF) << 8) | ((c >> 8) & 0xFF)


BG_BASE = T.C_BG_BASE
BG_IRIS = T.C_BG_IRIS
SURF_CHIP = T.C_SURFACE_CHIP
SURF_TOAST = T.C_SURFACE_TOAST
LINE_SUBTLE = T.C_LINE_SUBTLE
TEXT_PRI = T.C_TEXT_PRIMARY
TEXT_SEC = T.C_TEXT_SECONDARY
TEXT_TER = T.C_TEXT_TERTIARY
ACC_COLD = T.C_ACCENT_COLD
WARN = T.C_STATUS_WARN
CRIT = T.C_STATUS_CRITICAL
ACC_FOUND = T.C_ACCENT_FOUND
PROX = T.RAMP_SWAPPED["green"]
GREY = T.RAMP_SWAPPED["grey"]
