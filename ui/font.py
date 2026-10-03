"""License-free bitmap faces derived from MicroPython framebuf's built-in 8x8 font.

The 8x8 font already has 2 px vertical and 1 px horizontal strokes, so an
anisotropic scale keeps strokes square:

  face     cell   scale (x, y)  tokens
  MICRO    8x8    1 x 1         type.micro   (battery %, debug)
  LABEL    8x16   1 x 2         type.label   (chips, toasts, menu)
  WORD     16x32  2 x 4         type.word / type.numeral
  DISPLAY  24x48  3 x 6         type.display (countdown digits)

``render(face, s)`` returns a MONO_HLSB bytearray (1 = ink) plus its size;
ui/text.py caches one FrameBuffer per distinct (face, string). MicroPython
only (the built-in font comes from framebuf).
"""

import framebuf

from finder.compat import const

MICRO = const(0)
LABEL = const(1)
WORD = const(2)
DISPLAY = const(3)
SX = (1, 1, 2, 3)
SY = (1, 2, 4, 6)

_rows = {}
_exp = {}
_scratch = None


# Hand-drawn replacements for built-in glyphs that break up when scaled: the
# built-in "1" has its flag one row below the stem top, so at 2x4 / 3x6 it
# reads as a detached block ("t" / "i"). Same stroke rules as the font.
OVERRIDE = {
    "1": bytes((0x18, 0x38, 0x78, 0x18, 0x18, 0x18, 0x7E, 0x00)),
}


def char_rows(ch):
    """8 row bytes (MSB = left pixel) of ``ch`` in the built-in font."""
    global _scratch
    r = _rows.get(ch)
    if r is None:
        r = OVERRIDE.get(ch)
        if r is not None:
            _rows[ch] = r
            return r
        if _scratch is None:
            b = bytearray(8)
            _scratch = (b, framebuf.FrameBuffer(b, 8, 8, framebuf.MONO_HLSB))
        b, fb = _scratch
        fb.fill(0)
        fb.text(ch, 0, 0, 1)
        r = bytes(b)
        _rows[ch] = r
    return r


def _expand_table(sx):
    t = _exp.get(sx)
    if t is None:
        t = []
        for v in range(256):
            o = 0
            for i in range(8):
                o <<= sx
                if v & (0x80 >> i):
                    o |= (1 << sx) - 1
            t.append(o)
        _exp[sx] = t
    return t


def render(face, s):
    """MONO_HLSB bitmap of ``s`` in ``face`` -> (buf, w, h)."""
    sx = SX[face]
    sy = SY[face]
    n = len(s)
    w = 8 * sx * n
    h = 8 * sy
    stride = sx * n
    buf = bytearray(stride * h)
    tab = _expand_table(sx) if sx > 1 else None
    for ci in range(n):
        rows = char_rows(s[ci])
        col = ci * sx
        for r in range(8):
            v = rows[r]
            if tab is not None:
                v = tab[v]
            base = r * sy * stride + col
            for b in range(sx):
                byte = (v >> (8 * (sx - 1 - b))) & 0xFF
                if byte:
                    for k in range(sy):
                        buf[base + k * stride + b] = byte
    return buf, w, h
