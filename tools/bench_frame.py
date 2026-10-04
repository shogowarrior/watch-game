"""Frame and IMU profiler -- runs ON THE WATCH (stock MicroPython 1.29, the
game's app/, finder/, hal/ and ui/ deployed, e.g. ``tools/deploy.py --noapp``).

    mpremote connect <port> run tools/bench_frame.py

Prints where the time goes:
  * kernels: which hot loops run as viper on this build (palette, field
    blit, IMU decode, IMU feed) and the CPU clock
  * render: per screen fixture, ms per frame split into step (state), plan,
    palette, field (ring-map blit), overlays and push (SPI), then the
    overlay ms of each of the 10 strips
  * push: one frame sent as 10 windowed strips, as one window of 10, 5 and
    1 writes
  * overlap: whether a second thread can send strips while this one draws:
    ms per frame of 10 strips, each after 3 ms of viper work, sent inline,
    from a send thread, and from a send thread with the drawing split into
    0.5 ms slices that hand the GIL over (``lock.acquire(0)``)
  * imu: per BMA423 rate, the I2C read, decode and ImuFeed cost per sample
    and the share of each second they take at that rate
  * loop: 10 s of the real game loop (app/runtime.py), ``print_stats()``,
    then 10 s more with bump sensing forced on (the IMU at 800 Hz). The
    screen is held on (the game would turn it off face-down).
"""

import gc
import time

import machine

from app import imu_feed
from app.imu_feed import ImuFeed
from finder.render_params import make_params
from hal import bma423
from hal.bma423 import BMA423, decode_frames
from hal.board import Board
from ui import field
from ui.renderer import NS, SH, W, Renderer

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


def bench_kernels(r):
    print("cpu %d MHz  kernels: palette %s, field blit %s, imu decode %s, imu feed %s" % (
        machine.freq() // 1000000, field.KERNEL, r.map.kind, bma423.DECODE_KERNEL,
        imu_feed.KERNEL))


def _frame_parts(r, p, display, t, acc, strips):
    """Renderer.frame(p, display, t), timed part by part into ``acc``
    (step, plan, palette, field, overlays, push; us) and the overlays of
    each strip into ``strips``."""
    a = _us()
    r._step(p, t)
    b = _us()
    acc[0] += _d(b, a)
    if r._dark:
        r._dark = False
        r._snap(p, t)
    r._plan(p, t)
    a = _us()
    acc[1] += _d(a, b)
    r._palette(p, t)
    b = _us()
    acc[2] += _d(b, a)
    pal = r.field.pal
    arr = r.field.pal_arr
    m = r.map
    buf = r.buf
    fb = r.fb
    for s in range(NS):
        y0 = s * SH
        a = _us()
        m.blit(y0, pal, arr, buf, fb)
        b = _us()
        acc[3] += _d(b, a)
        r._strip(p, t, y0, fb)
        a = _us()
        d = _d(a, b)
        acc[4] += d
        strips[s] += d
        display.push_strip(y0, SH, buf)
        acc[5] += _d(_us(), a)


def bench_render(display):
    print("render         total ms  step  plan  pal  field  overlays  push    fps")
    r = None
    for name, kw in FIXTURES:
        d = dict(_FIELD)
        d.update(kw)
        p = make_params(**d)
        r = Renderer()
        t = 100000
        for _ in range(10):                 # rings in flight, crossfades settled
            r.frame(p, display, t)
            t += 50
        gc.collect()
        acc = [0] * 6
        strips = [0] * NS
        t0 = _us()
        for _ in range(N):
            _frame_parts(r, p, display, t, acc, strips)
            t += 50
        tot = _d(_us(), t0) / N / 1000
        ms = [x / N / 1000 for x in acc]
        print("%-14s %6.1f  %5.1f %5.1f %5.1f %5.1f    %5.1f  %5.1f  %5.1f" % (
            name, tot, ms[0], ms[1], ms[2], ms[3], ms[4], ms[5], 1000 / tot))
        print("  overlays per strip ms: " + " ".join("%.1f" % (x / N / 1000) for x in strips))
    return r


def bench_push(display, r):
    """One frame (the last fixture's strip, ten times) four ways."""
    frame = bytearray(W * W * 2)
    for s in range(NS):
        frame[s * W * SH * 2:(s + 1) * W * SH * 2] = r.buf
    mv = memoryview(frame)
    n = W * SH * 2
    spi = display.spi
    out = []
    t0 = _us()
    for _ in range(10):                     # a window per strip
        for s in range(NS):
            display._next = -1
            display.push_strip(s * SH, SH, mv[s * n:(s + 1) * n])
    out.append(_d(_us(), t0) / 10000)
    for k in (NS, NS // 2, 1):              # one window, k writes
        step = len(frame) // k
        t0 = _us()
        for _ in range(10):
            display._begin(0, 0, W - 1, W - 1)
            for i in range(k):
                spi.write(mv[i * step:(i + 1) * step])
            display._cs(1)
        out.append(_d(_us(), t0) / 10000)
    print("push ms: 10 windows %.1f, 1 window x 10 writes %.1f, x 5 writes %.1f, "
          "x 1 write %.1f" % tuple(out))


def _spin_fn():
    """A viper busy loop (holds the GIL, as the field blit does)."""
    import micropython

    @micropython.viper
    def spin(n: int) -> int:
        s = 0
        i = 0
        while i < n:
            s += i ^ (s >> 3)
            i += 1
        return s
    return spin


def bench_overlap(display, r):
    """Can a send thread overlap the drawing? 10 strips, 3 ms of work each."""
    try:
        import _thread
    except ImportError:
        print("overlap: no _thread")
        return
    spin = _spin_fn()
    n = 1000
    while True:                             # n spins = 0.5 ms
        t0 = _us()
        spin(n)
        dt = _d(_us(), t0)
        if dt >= 2000:
            break
        n *= 2
    n = n * 500 // dt
    buf = r.buf
    st = {"go": _thread.allocate_lock(), "done": _thread.allocate_lock(),
          "y": 0, "stop": False}
    go = st["go"]
    done = st["done"]
    go.acquire()
    done.acquire()

    def worker():
        while True:
            go.acquire()
            if st["stop"]:
                done.release()
                return
            display.push_strip(st["y"], SH, buf)
            done.release()

    yl = _thread.allocate_lock()
    yl.acquire()                            # acquire(0) on it only hands the GIL over

    def frame(mode):
        busy = False
        for s in range(NS):
            if mode == 2:
                for _ in range(6):
                    spin(n)
                    yl.acquire(0)
            else:
                spin(6 * n)
            if mode == 0:
                display.push_strip(s * SH, SH, buf)
                continue
            if busy:
                done.acquire()
            st["y"] = s * SH
            go.release()
            busy = True
        done.acquire()

    out = []
    for mode in range(3):
        if mode == 1:
            _thread.start_new_thread(worker, ())
        frame(mode)
        t0 = _us()
        for _ in range(5):
            frame(mode)
        out.append(_d(_us(), t0) / 5000)
    st["stop"] = True
    go.release()
    done.acquire()
    print("overlap ms/frame (10 x 3 ms work + strips): inline %.1f, thread %.1f, "
          "thread + GIL hand-offs %.1f" % tuple(out))


class _Batch:
    """FIFO stand-in that hands ImuFeed an already-read batch."""

    def __init__(self, mg, hz):
        self.fifo_mg = mg
        self.odr = hz                       # the feed starts at this rate
        self.n = 0

    def fifo_read_mg(self):
        return self.n

    def set_odr(self, hz):
        self.odr = hz


def bench_imu(i2c):
    print("imu  Hz   us/sample: i2c  decode  feed   ms per s")
    for hz in RATES:
        imu = BMA423(i2c, odr=hz)
        batch = _Batch(imu.fifo_mg, hz)
        feed = ImuFeed(batch, out_hz=25)    # spikes looked for above 100 Hz
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
    rt.begin()
    rt.game._keep_on = lambda: True         # screen stays on whatever the wrist does
    rt.run(max_ms=LOOP_MS)
    print("loop: %d s of the game (%s)" % (LOOP_MS // 1000, rt.game.mode))
    rt.print_stats()
    rt.game.bump_armed = lambda: True       # the IMU at 800 Hz, as in HOT
    rt.run(max_ms=LOOP_MS)
    print("loop: %d s more with bump sensing on (IMU %d Hz)" % (LOOP_MS // 1000, imu_feed.FAST_HZ))
    rt.print_stats()


def main():
    board = Board()
    board.init(strict=False)
    if board.errors:
        print("parts missing:", board.errors)
    r = bench_render(board.display)
    bench_kernels(r)
    bench_push(board.display, r)
    bench_overlap(board.display, r)
    bench_imu(board.i2c0)
    bench_loop(board)


main()
