"""ui/glyphs: the HOT bump view (ui-spec §6 HOT, tokens.glyphs.bump_watches).

Geometry runs on CPython too; drawing needs MicroPython framebuf:

    node tools/mpy/run.mjs tests/runner.py test_glyphs
"""

import gc

try:
    import framebuf
    HAVE_FB = True
except ImportError:
    HAVE_FB = False

from finder import tuning as T
from tests import Skip
from ui import BG_IRIS, GREY, PROX
from ui import glyphs as gl

UNTOUCHED = 0x1234          # a colour no glyph uses: "nothing drawn here"
CX, CY = T.CENTER


def _need_fb():
    if not HAVE_FB:
        raise Skip("framebuf is MicroPython-only")


def test_bump_geometry_from_tokens():
    # outer/inner rects are the 4 px stroke around the path-centre rect
    assert gl.BUMP_OUT == ((81, 96, 38, 44, 9), (121, 96, 38, 44, 9))
    assert gl.BUMP_IN == ((85, 100, 30, 36, 5), (125, 100, 30, 36, 5))
    assert gl.BUMP_STRAP_X == (90, 130) and gl.BUMP_STRAP_Y == (84, 138)
    # yours left, the friend's right, a 2 px bg.iris gap between them
    left_end = gl.BUMP_OUT[0][0] + gl.BUMP_OUT[0][2] - 1
    assert left_end < CX - 1 and gl.BUMP_OUT[1][0] == CX + 1
    assert (gl.BUMP_Y0, gl.BUMP_Y1) == (63, 152)
    # every corner and ray end fits inside the bump iris (r 64)
    r2 = T.IRIS_R["bump"] ** 2
    for x, y, w, h, _ in gl.BUMP_OUT:
        for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
            assert (px - CX) ** 2 + (py - CY) ** 2 < r2, (px, py)
    for dx, dy in gl.BUMP_CAPS:
        assert dx * dx + dy * dy < r2


def _frame(bits):
    """The bump view drawn once into a full 240x240 frame."""
    buf = bytearray(240 * 240 * 2)
    fb = framebuf.FrameBuffer(buf, 240, 240, framebuf.RGB565)
    fb.fill(UNTOUCHED)
    gl.draw_bump(fb, 0, 240, bits)
    return buf, fb


def test_bump_colours_per_state():
    _need_fb()
    ready = (PROX[6], BG_IRIS, PROX[4])
    lit = (PROX[7], PROX[7], PROX[6])
    off = (GREY[5], BG_IRIS, GREY[3])
    for bits, me, friend, ray in ((0, ready, ready, PROX[6]),
                                  (1, lit, ready, PROX[7]),
                                  (2, ready, lit, PROX[7]),
                                  (3, lit, lit, PROX[7]),
                                  (4, ready, off, GREY[5]),
                                  (5, lit, off, PROX[7])):
        _, fb = _frame(bits)
        got = ((fb.pixel(82, 118), fb.pixel(100, 118), fb.pixel(100, 90)),
               (fb.pixel(157, 118), fb.pixel(140, 118), fb.pixel(140, 90)))
        assert got == (me, friend), (bits, got)
        assert fb.pixel(100, 145) == me[2] and fb.pixel(140, 145) == friend[2], bits
        assert fb.pixel(120, 69) == ray, bits
        assert fb.pixel(119, 118) == UNTOUCHED and fb.pixel(120, 118) == UNTOUCHED, bits
        # nothing outside the glyph rows
        assert fb.pixel(120, 62) == UNTOUCHED and fb.pixel(100, 152) == UNTOUCHED, bits


def test_strip_culling_matches_full_frame():
    _need_fb()
    full, _ = _frame(5)
    sbuf = bytearray(240 * 24 * 2)
    strip = framebuf.FrameBuffer(sbuf, 240, 24, framebuf.RGB565)
    for k in range(10):
        y0 = k * 24
        strip.fill(UNTOUCHED)
        gl.draw_bump(strip, y0, y0 + 24, 5)
        assert sbuf == full[y0 * 480:(y0 + 24) * 480], k


def test_draw_bump_allocates_nothing():
    _need_fb()
    sbuf = bytearray(240 * 24 * 2)
    strip = framebuf.FrameBuffer(sbuf, 240, 24, framebuf.RGB565)
    for y0 in (48, 72, 96, 120, 144):
        gl.draw_bump(strip, y0, y0 + 24, 1)
    gc.collect()
    a0 = gc.mem_alloc()
    for n in range(20):
        for y0 in (48, 72, 96, 120, 144):
            gl.draw_bump(strip, y0, y0 + 24, n % 6)
    grown = gc.mem_alloc() - a0
    assert grown < 256, grown
