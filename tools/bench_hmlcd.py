"""MicroPython with a C display module -- runs ON THE WATCH, on the custom build
(stock 1.29 plus the hmlcd module from native/micropython, native/README.md).

    python3 tools/deploy.py --noapp       # hal/, finder/, ui/
    mpremote run tools/bench_hmlcd.py

The fixtures of tools/bench_frame.py, drawn by the real Renderer, sent two ways:
  stock   Renderer.frame as the game does today: draw, then push over
          machine.SPI at 26.67 MHz while the drawing waits.
  hmlcd   draw into one of two frame buffers and hand it to hmlcd.push, which
          sends it from a task on core 0 (through internal DMA buffers, half
          duplex) while this core draws the next frame; at 26.67, 40, 80 MHz.
One line per fixture and mode:
  HM mpy mode= hz= fixture= fps= frame_us= draw_us= push_us= wait_us=
draw_us: this core's work per frame; push_us: core 0's push per frame;
wait_us: how long this core waited for the previous push. Then a test card
holds 5 s at each hmlcd clock to check the picture by eye, and the stock
driver takes the screen back. On stock firmware it stops at once.
"""

import framebuf
import gc
import time

import machine

from finder.render_params import make_params
from hal.board import Board
from hal.st7789 import rgb565
from ui.renderer import BH, NB, W, Renderer

try:
    import hmlcd
except ImportError:
    hmlcd = None

N = 60                    # frames timed per fixture and mode
CLOCKS = (26666666, 40000000, 80000000)
HOLD_MS = 5000
_us = time.ticks_us
_d = time.ticks_diff

_FIELD = dict(ramp="green", ring_live=True, glyph="glow", status=(80, 80, 4, False, False))
FIXTURES = (              # as tools/bench_frame.py
    ("FAR", dict(screen="FAR", zone=0, intensity=0.12, speed_px_s=40, pulse_period_ms=2400,
                 wavelength_px=96, glow_r_px=25, dist_band="~20")),
    ("HOT", dict(screen="HOT", zone=3, intensity=0.85, speed_px_s=120, pulse_period_ms=500,
                 wavelength_px=60, glow_r_px=54, dist_band="<3", word="BUMP!", top_text="TAP WATCHES")),
    ("PAIRING", dict(screen="PAIRING", sub="seen", zone=None, intensity=0.1, speed_px_s=-30,
                     pulse_period_ms=3000, wavelength_px=90, glow_r_px=30, glyph="runes", runes=(0, 3, 6))),
)


class _Timed:
    """The real display, with the time spent in push_strip counted."""

    def __init__(self, d):
        self.d = d
        self.us = 0

    def push_strip(self, y0, h, buf):
        a = _us()
        self.d.push_strip(y0, h, buf)
        self.us += _d(_us(), a)


class _Hand:
    """Takes the renderer's four bands and hands the whole frame to hmlcd."""

    def __init__(self, r):
        self.r = r

    def push_strip(self, y0, h, buf):
        if y0 + h == W:
            hmlcd.push(self.r.buf, 0, W)


def _frame_set(buf=None):
    buf = buf or bytearray(W * W * 2)
    mv = memoryview(buf)
    n = W * BH * 2
    bands = tuple(mv[k * n:(k + 1) * n] for k in range(NB))
    return (buf, framebuf.FrameBuffer(buf, W, W, framebuf.RGB565), bands,
            tuple(framebuf.FrameBuffer(b, W, BH, framebuf.RGB565) for b in bands))


def _use(r, s):
    r.buf, r.fb, r.bands, r.band_fbs = s


def _line(mode, hz, name, us, draw, push, wait):
    print("HM mpy mode=%s hz=%d fixture=%s fps=%.1f frame_us=%d draw_us=%d push_us=%d wait_us=%d" % (
        mode, hz, name, 1e6 * N / us, us // N, draw // N, push // N, wait // N))


def stock(disp):
    timed = _Timed(disp)
    for name, kw in FIXTURES:
        d = dict(_FIELD)
        d.update(kw)
        p = make_params(**d)
        r = Renderer()
        t = 100000
        for _ in range(10):               # rings in flight, crossfades settled
            r.frame(p, disp, t)
            t += 50
        gc.collect()
        timed.us = 0
        t0 = _us()
        for _ in range(N):
            r.frame(p, timed, t)
            t += 50
        us = _d(_us(), t0)
        _line("stock", 26666666, name, us, us - timed.us, timed.us, timed.us)


def fast(hz):
    hand = None
    for name, kw in FIXTURES:
        d = dict(_FIELD)
        d.update(kw)
        p = make_params(**d)
        r = Renderer()
        sets = (_frame_set(r.buf), _frame_set())
        hand = _Hand(r)
        t = 100000
        for i in range(10):
            _use(r, sets[i & 1])
            r.frame(p, hand, t)
            t += 50
        hmlcd.wait()
        gc.collect()
        s0 = hmlcd.stats()
        t0 = _us()
        for i in range(N):
            _use(r, sets[i & 1])          # draw into the buffer that is not on the wire
            r.frame(p, hand, t)
            t += 50
        hmlcd.wait()
        us = _d(_us(), t0)
        s1 = hmlcd.stats()
        wait = s1[4] - s0[4]
        _line("hmlcd", hz, name, us, us - wait, s1[3] - s0[3], wait)
    return hand.r.buf


def card(buf):
    """Colour bars, 1-pixel black and white lines, a red-to-blue gradient, a white edge."""
    fb = framebuf.FrameBuffer(buf, W, W, framebuf.RGB565)
    white, black = rgb565(255, 255, 255), rgb565(0, 0, 0)
    for i, (r, g, b) in enumerate(((255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0),
                                   (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0))):
        fb.fill_rect(i * 30, 0, 30, 80, rgb565(r, g, b))
    for y in range(80, 160):
        fb.hline(0, y, W, white if y & 1 else black)
    for x in range(W):
        fb.vline(x, 160, 80, rgb565(255 - x * 255 // 239, 0, x * 255 // 239))
    fb.rect(0, 0, W, W, white)


def main():
    if hmlcd is None:
        print("HM error what=no_hmlcd (stock firmware: build native/micropython first)")
        return
    board = Board()
    board.init(strict=False)
    if board.errors:
        print("parts missing:", board.errors)
    disp = board.display
    print("HM hello variant=micropython-hmlcd cpu_mhz=%d" % (machine.freq() // 1000000))
    stock(disp)
    disp.spi.deinit()                     # hmlcd takes the bus
    shown = None
    try:
        for hz in CLOCKS:
            try:
                hmlcd.init(hz)
            except OSError as e:
                print("HM error what=spi_clock hz=%d (%s)" % (hz, e))
                continue
            shown = fast(hz)
            card(shown)
            hmlcd.push(shown, 0, W)
            print("  look now: test card at %.2f MHz for %d s" % (hz / 1e6, HOLD_MS // 1000))
            time.sleep_ms(HOLD_MS)
    finally:
        hmlcd.deinit()
        disp.spi.init()                   # back to machine.SPI with its old settings
        disp._cs.init(machine.Pin.OUT, value=1)
    print("HM done")


if __name__ == "__main__":
    main()
