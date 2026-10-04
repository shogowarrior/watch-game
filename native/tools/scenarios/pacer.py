"""app/pacer.py: FramePacer through the game loop's pattern past what
tests/test_pacer.py reaches: busy times that wander, burst and settle (so the
lock drops several steps at once and climbs back one at a time), every cap
including one between locks and none, state-only frames with a cap that
falls and rises, screen wakes, frames shown late and early, the clock across
the 2^30 tick wrap, log windows read on the way, and resets. One line per
frame: slot, lock and the log counters."""

from app.pacer import FramePacer
from finder.compat import ticks_add, ticks_diff

WRAP = 1 << 30


class Lcg:
    def __init__(self, seed):
        self.x = seed

    def next(self, m):
        self.x = (self.x * 1103515245 + 12345) & 0x7FFFFFFF
        return (self.x >> 8) % m


class Run:
    def __init__(self, t, cap=None):
        self.t = t
        self.pc = FramePacer(t, cap) if cap is not None else FramePacer(t)

    def line(self, what):
        pc = self.pc
        return "%s t=%d next=%d fps=%d cost=%d ch=%d fr=%d miss=%d late=%d/%d iv=%d/%d/%d/%d" % (
            what, self.t, pc.t_next, pc.fps, pc.cost, pc.changes, pc.frames, pc.missed, pc.late_sum,
            pc.late_max, pc.iv_n, pc.iv_sum, pc.iv_sq, pc.iv_max)

    def frame(self, cap, busy, lag=0, show=0):
        """The loop reaches the frame ``lag`` ms after it is due (or after the
        last one ended, if later: slots missed), the frame takes ``busy`` ms
        and is on the panel ``show`` ms after that (None: a state-only frame,
        nothing shown)."""
        pc = self.pc
        due = pc.t_next
        self.t = ticks_add(self.t if ticks_diff(self.t, due) > 0 else due, lag)
        slot = pc.begin(self.t, cap, busy)
        if busy is not None:
            self.t = ticks_add(self.t, busy)
            pc.shown(ticks_add(self.t, show))
        return self.line("frame %d %d" % (slot, cap))

    def log(self):
        pc = self.pc
        out = "log jit=%r" % pc.jitter_ms()
        pc.window()
        return out


def lines():
    yield "# frame <slot> <cap> | log jitter; t next fps cost changes frames missed late_sum/max iv_n/sum/sq/max"
    rng = Lcg(11)
    r = Run(WRAP - 4000)
    for k in range(600):                     # cost wanders 20..140 ms, now and then a burst
        b = 20 + rng.next(60) + (k // 100) * 15 + (300 if rng.next(40) == 0 else 0)
        yield r.frame(20, b, rng.next(9), rng.next(4))
        if k % 97 == 96:
            yield r.log()
    for k in range(400):                     # settles: the lock climbs back a step at a time
        yield r.frame(20, 25 + rng.next(10), rng.next(3))
    for cap in (15, 8, 7, 6, 5, 3, 0, 20, 10, 100):
        for k in range(30):
            yield r.frame(cap, 30 + rng.next(30), rng.next(5), 1)
    yield r.log()
    for cap in (20, 8, 5, 20, 10):           # screen off: the cap still applies, nothing else moves
        for k in range(20):
            yield r.frame(cap, None, rng.next(200))
    r.pc.restart(ticks_add(r.t, 700))        # screen woken: a fresh grid, the wake's busy time ignored
    yield r.line("restart")
    yield r.frame(20, 450, 130)
    for k in range(80):
        yield r.frame(20, 15 + rng.next(30), rng.next(2))
    yield r.log()
    yield r.log()
    r.t = ticks_add(r.t, 9)                  # called early (as a test may): the slot anyway
    yield r.line("early %d" % r.pc.begin(r.t, 20, 10))
    r.pc.reset(r.t, 7)
    yield r.line("reset 7")
    for k in range(60):
        yield r.frame(20, 40 + rng.next(80), rng.next(400), rng.next(3))
    r.pc.reset(r.t)
    yield r.line("reset")
    r = Run(WRAP - 50, 9)
    for k in range(40):
        yield r.frame(9, 60 + rng.next(70))
    yield "end %d %r" % (ticks_diff(r.t, 0), r.pc.jitter_ms())
