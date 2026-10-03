"""Frame and IMU profiler -- runs ON THE WATCH (stock MicroPython 1.29, the
game's app/, finder/, hal/ and ui/ deployed, e.g. ``tools/deploy.py --noapp``).

    mpremote connect <port> run tools/bench_frame.py

Prints where the time goes:
  * render: per screen fixture, ms per frame split into compose (palette,
    field and overlays) and push (SPI), plus the state-only frame (screen off)
  * imu: per BMA423 rate, the I2C read, decode and ImuFeed cost per sample
    and the share of each second they take at that rate
  * loop: 10 s of the real game loop (app/runtime.py), ``print_stats()``
"""

import gc
import time

from app.imu_feed import ImuFeed
from finder.render_params import make_params
from hal.bma423 import BMA423, decode_frames
from hal.board import Board
from ui.renderer import Renderer

N = 40                    # frames per fixture
LOOP_MS = 10000
RATES = (100, 400, 800, 1600)
_us = time.ticks_us
_d = time.ticks_diff

_FIELD = dict(ramp="green", ring_live=True, glyph="glow", status=(80, 80, 4, False, False))
FIXTURES = (
    ("FAR", dict(screen="FAR", zone=0, intensity=0.12, speed_px_s=40, pulse_period_ms=2400,
                 wavelength_px=96, glow_r_px=25, dist_band="~20")),
    ("HOT bump-ready", dict(screen="HOT", zone=3, intensity=0.85, speed_px_s=120,
                            pulse_period_ms=500, wavelength_px=60, glow_r_px=54,
                            dist_band="<3", word="BUMP!", top_text="TAP WATCHES")),
    ("PAIRING runes", dict(screen="PAIRING", sub="seen", zone=None, intensity=0.1,
                           speed_px_s=-30, pulse_period_ms=3000, wavelength_px=90,
                           glow_r_px=30, glyph="runes", runes=(0, 3, 6))),
)


class TimedDisplay:
    """Display proxy that sums the time spent pushing strips."""

    def __init__(self, d):
        self.d = d
        self.us = 0

    def push_strip(self, y0, h, buf):
        t0 = _us()
        self.d.push_strip(y0, h, buf)
        self.us += _d(_us(), t0)


def bench_render(display):
    print("render       total ms  compose  push    fps   state-only ms")
    td = TimedDisplay(display)
    for name, kw in FIXTURES:
        d = dict(_FIELD)
        d.update(kw)
        p = make_params(**d)
        r = Renderer()
        t = 100000
        for _ in range(10):                 # rings in flight, crossfades settled
            r.frame(p, td, t)
            t += 50
        td.us = 0
        gc.collect()
        t0 = _us()
        for _ in range(N):
            r.frame(p, td, t)
            t += 50
        tot = _d(_us(), t0) / N / 1000
        push = td.us / N / 1000
        t0 = _us()
        for _ in range(N):
            r.frame(p, None, t)
            t += 50
        st = _d(_us(), t0) / N / 1000
        print("%-14s %6.1f  %6.1f  %6.1f  %5.1f   %5.2f" % (name, tot, tot - push, push,
                                                          1000 / tot, st))


class _Batch:
    """FIFO stand-in that hands ImuFeed an already-read batch."""

    def __init__(self, mg):
        self.fifo_mg = mg
        self.n = 0

    def fifo_read_mg(self):
        return self.n


def bench_imu(i2c):
    print("imu  Hz   us/sample: i2c  decode  feed   ms per s")
    for hz in RATES:
        imu = BMA423(i2c, odr=hz)
        batch = _Batch(imu.fifo_mg)
        feed = ImuFeed(batch, out_hz=25)
        n_all = t_i2c = t_dec = t_feed = 0
        for _ in range(5):
            imu.fifo_flush()
            time.sleep_ms(min(1000, 150 * 1000 // hz))
            t0 = _us()
            n = imu.fifo_read()
            t1 = _us()
            n = decode_frames(imu.fifo_buf, n, imu.fifo_mg, imu.range_mg)
            t2 = _us()
            batch.n = n
            feed.poll(time.ticks_ms())
            t3 = _us()
            n_all += n
            t_i2c += _d(t1, t0)
            t_dec += _d(t2, t1)
            t_feed += _d(t3, t2)
        if not n_all:
            print("%5d   no samples" % hz)
            continue
        per = (t_i2c + t_dec + t_feed) / n_all
        print("%5d   %10.1f  %6.1f  %5.1f   %6.1f" % (hz, t_i2c / n_all, t_dec / n_all,
                                                   t_feed / n_all, per * hz / 1000))
    BMA423(i2c)                             # back to the driver defaults


def bench_loop(board):
    from app.runtime import Runtime
    rt = Runtime(board)
    rt.run(max_ms=LOOP_MS)
    print("loop: %d s of the game" % (LOOP_MS // 1000))
    rt.print_stats()


def main():
    board = Board()
    board.init(strict=False)
    if board.errors:
        print("parts missing:", board.errors)
    bench_render(board.display)
    bench_imu(board.i2c0)
    bench_loop(board)


main()
