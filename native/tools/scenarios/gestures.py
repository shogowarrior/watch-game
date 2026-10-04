"""finder/gestures.py: touch sequences through the recognizer, one line per
sample: taps (and their duration window), brisk taps at frame-rate sampling,
long presses, swipes, drags, jitter, dropouts under and at the debounce,
multi-touch, a knock's touch, a reset mid-press, idle time and other
thresholds. Times stay well inside the Python's 2^30 tick range."""

from finder import gestures as G


class _Run:
    """One recognizer; ``s`` feeds a sample and returns its line."""

    def __init__(self, args=None):
        self.g = G.GestureRecognizer(*args) if args else G.GestureRecognizer()
        self.args = args

    def head(self, what):
        g = self.g
        return "g_new %d %d %d %d %d %d %s" % (g.long_ms, g.swipe_px, g.debounce_ms, g.slop_px, g.tap_min_ms,
                                             g.tap_max_ms, what)

    def s(self, t, touching, x=120, y=120, multi=False):
        g = self.g
        e = g.update(t, touching, x, y, multi)
        return "s %d %d %d %d %d -> %d %d %d %d %d %d %d %d" % (
            t, touching, x, y, multi, e, g.ev_x, g.ev_y, g.ev_t, g.down, g.began, g.x, g.y)

    def ramp(self, t0, t1, touching, x=120, y=120, x1=None, y1=None, dt=10, multi=False):
        """Samples every dt ms in [t0, t1); the position ramps to (x1, y1) (tests/test_gestures.py)."""
        x1 = x if x1 is None else x1
        y1 = y if y1 is None else y1
        n = (t1 - t0) // dt
        for i in range(n):
            f = i / (n - 1) if n > 1 else 1.0
            yield self.s(t0 + i * dt, touching, int(round(x + (x1 - x) * f)), int(round(y + (y1 - y) * f)), multi)


def _cases():
    r = _Run()
    yield r.head("single_tap")
    yield from r.ramp(1000, 1100, True, 30, 40)
    yield from r.ramp(1100, 1300, False)

    for ms in (20, 59, 60, 400, 401, 790):
        r = _Run()
        yield r.head("tap_window_%d" % ms)
        yield r.s(1000, True)
        yield r.s(1000 + ms - 1, True)
        yield r.s(1000 + ms, False)
        yield from r.ramp(1000 + ms + 10, 1000 + ms + 120, False)

    # the watch samples touch once per ~45-50 ms frame: a 60-90 ms tap seen once still taps
    for period in (45, 50):
        for ms in (60, 90):
            for ph in (0, 20, 40):
                r = _Run()
                yield r.head("brisk_%d_%d_%d" % (period, ms, ph))
                t = 2000 + ph
                while t < 2900:
                    yield r.s(t, 2500 <= t < 2500 + ms, 100, 150)
                    t += period

    # a knock's touch: screen to screen, a brief contact. Sampled every 10 ms it is
    # under the 60 ms floor (nothing), bouncing it stays one press; caught by one
    # frame it can still be a TAP whose ev_t (the touch-down) the game matches
    # against the accelerometer spike (finder/game.py _knock)
    r = _Run()
    yield r.head("knock_10ms")
    yield from r.ramp(3000, 3040, False)
    yield r.s(3040, True, 118, 125)
    yield from r.ramp(3050, 3200, False)
    r = _Run()
    yield r.head("knock_bounce")
    yield from r.ramp(3000, 3020, False)
    yield r.s(3020, True, 90, 100)
    yield r.s(3030, False)
    yield r.s(3040, False)
    yield r.s(3050, True, 92, 101)
    yield from r.ramp(3060, 3200, False)
    r = _Run()
    yield r.head("knock_frame")
    for t in (3000, 3050, 3100, 3150, 3200, 3250, 3300):
        yield r.s(t, t == 3100, 140, 60)
    r = _Run()
    yield r.head("knock_two_points")
    yield r.s(3000, False)
    yield r.s(3050, True, 120, 120, True)
    yield r.s(3100, False)
    yield from r.ramp(3150, 3400, False, dt=50)

    r = _Run()
    yield r.head("began_and_dropout")
    for t, touching in ((0, False), (10, True), (20, True), (30, False), (50, True), (60, False), (150, False),
                        (160, True), (170, False), (240, False)):
        yield r.s(t, touching)

    r = _Run()
    yield r.head("dropout_bridged")
    yield from r.ramp(0, 100, True)
    yield r.s(100, False)
    yield r.s(159, True)
    yield from r.ramp(160, 300, True)
    yield from r.ramp(300, 400, False)

    r = _Run()
    yield r.head("dropout_at_debounce")
    yield from r.ramp(0, 100, True)
    yield from r.ramp(100, 160, False)
    yield from r.ramp(160, 260, True)
    yield from r.ramp(260, 360, False)

    r = _Run()
    yield r.head("dropouts_in_long_press")
    yield from r.ramp(0, 400, True, dt=20)
    yield from r.ramp(400, 450, False)
    yield from r.ramp(450, 1200, True, dt=25)
    yield from r.ramp(1200, 1300, False)

    r = _Run()
    yield r.head("long_press")
    yield from r.ramp(500, 1400, True, 50, 60, dt=20)
    yield from r.ramp(1400, 1500, False)

    r = _Run()
    yield r.head("just_under_long_press")
    yield r.s(0, True)
    yield r.s(799, True)
    yield r.s(810, False)
    yield from r.ramp(820, 900, False)
    r = _Run()
    yield r.head("long_press_at_threshold")
    yield r.s(0, True)
    yield r.s(800, True)
    yield r.s(820, False)
    yield r.s(900, False)

    r = _Run()
    yield r.head("moved_no_long_press")
    yield from r.ramp(0, 100, True, 100, 100, 125, 100)
    yield from r.ramp(100, 1000, True, 125, 100, dt=50)
    yield from r.ramp(1000, 1100, False)

    r = _Run()
    yield r.head("jitter_in_slop")
    yield from r.ramp(0, 150, True, 100, 100, 112, 88)
    yield from r.ramp(150, 250, False)
    r = _Run()
    yield r.head("short_drag")
    yield from r.ramp(0, 150, True, 100, 100, 113, 100)
    yield from r.ramp(150, 250, False)

    r = _Run()
    yield r.head("multi_touch")
    yield r.s(0, True)
    yield r.s(50, True, multi=True)
    yield r.s(100, True)
    yield from r.ramp(110, 200, False)
    yield from r.ramp(500, 1500, True, multi=True, dt=50)
    yield from r.ramp(1500, 1600, False)
    yield from r.ramp(2000, 2100, True)
    yield from r.ramp(2100, 2200, False)

    for name, x0, y0, x1, y1 in (("right", 60, 120, 180, 120), ("left", 180, 120, 60, 120),
                                 ("down", 120, 60, 120, 180), ("up", 120, 180, 120, 60),
                                 ("edge_40", 100, 100, 140, 100), ("short_39", 100, 100, 100, 61),
                                 ("diagonal_r", 100, 100, 150, 130), ("diagonal_tie", 100, 100, 50, 150)):
        r = _Run()
        yield r.head("swipe_" + name)
        yield from r.ramp(0, 200, True, x0, y0, x1, y1)
        yield from r.ramp(200, 300, False)

    r = _Run()
    yield r.head("slow_drag_swipe")
    yield from r.ramp(0, 100, True, 200, 100, 170, 80)
    yield from r.ramp(100, 1500, True, 170, 80, 150, 60, dt=50)
    yield from r.ramp(1500, 1600, False)

    r = _Run()
    yield r.head("reset_mid_press")
    yield from r.ramp(0, 60, True, 10, 20)
    r.g.reset()
    yield "g_reset"
    yield r.s(70, False)
    yield from r.ramp(80, 200, True, 30, 30)
    yield from r.ramp(200, 300, False)
    yield r.s(1000, True, 30, 30)
    yield r.s(1800, True, 40, 40)
    r.g.reset()
    yield "g_reset"
    yield r.s(1900, False)

    r = _Run()
    yield r.head("idle")
    yield from r.ramp(0, 1000, False, dt=100)

    # other thresholds: long 500, swipe 30, debounce 40, slop 8, taps 50..300
    r = _Run((500, 30, 40, 8, 50, 300))
    yield r.head("custom")
    yield from r.ramp(0, 60, True, 100, 100)
    yield from r.ramp(60, 120, False)
    yield from r.ramp(200, 800, True, 100, 100, dt=50)
    yield from r.ramp(800, 900, False)
    yield from r.ramp(1000, 1100, True, 100, 100, 100, 131)
    yield from r.ramp(1100, 1200, False)
    yield from r.ramp(1300, 1400, True, 100, 100, 109, 100)
    yield from r.ramp(1400, 1500, False)
    yield from r.ramp(1600, 1950, True, 100, 100, dt=50)
    yield from r.ramp(1950, 2050, False)


def lines():
    yield ("# g_new <long_ms> <swipe_px> <debounce_ms> <slop_px> <tap_min_ms> <tap_max_ms> <case>: a fresh "
           "recognizer; g_reset: reset(); s <t> <touching> <x> <y> <multi> -> <event> <ev_x> <ev_y> <ev_t> "
           "<down> <began> <x> <y>")
    yield from _cases()
