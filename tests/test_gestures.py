from finder.compat import ticks_add
from finder.gestures import (GestureRecognizer, NONE, TAP, DOUBLE_TAP, LONG_PRESS,
                             SWIPE_L, SWIPE_R, SWIPE_U, SWIPE_D, NAMES)

DT = 10


def _feed(g, out, t0, t1, touching, x=120, y=120, x1=None, y1=None, base=0):
    """Samples every DT ms in [t0, t1); position ramps to (x1, y1)."""
    x1 = x if x1 is None else x1
    y1 = y if y1 is None else y1
    n = (t1 - t0) // DT
    for i in range(n):
        t = t0 + i * DT
        f = i / (n - 1) if n > 1 else 1.0
        xi = int(round(x + (x1 - x) * f))
        yi = int(round(y + (y1 - y) * f))
        ev = g.update(ticks_add(base, t), touching, xi, yi)
        if ev:
            out.append((t, ev, g.ev_x, g.ev_y))
    return t1


def _one(g, t, touching, out, x=120, y=120):
    ev = g.update(t, touching, x, y)
    if ev:
        out.append((t, ev, g.ev_x, g.ev_y))


def _evs(out):
    return [e[1] for e in out]


def test_single_tap_immediate_without_double_flag():
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 100, True, 30, 40)
    _feed(g, out, 100, 1000, False)
    assert out == [(160, TAP, 30, 40)], out   # lift seen at 100 + 60 debounce
    assert not g.down


def test_dropout_under_debounce_is_bridged():
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 100, True)
    _one(g, 100, False, out)
    _one(g, 159, True, out)        # 59 ms gap: same press
    _feed(g, out, 160, 300, True)
    _feed(g, out, 300, 800, False)
    assert _evs(out) == [TAP], out


def test_dropout_at_debounce_is_a_release():
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 100, True)
    _feed(g, out, 100, 160, False)
    _feed(g, out, 160, 260, True)   # gap of exactly 60 ms -> new press
    _feed(g, out, 260, 800, False)
    assert [(e[0], e[1]) for e in out] == [(160, TAP), (320, TAP)], out


def test_dropouts_do_not_break_long_press():
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 400, True)
    _feed(g, out, 400, 450, False)
    _feed(g, out, 450, 1000, True)
    _feed(g, out, 1000, 1500, False)
    assert [(e[0], e[1]) for e in out] == [(600, LONG_PRESS)], out


def test_long_press_fires_while_held_at_threshold():
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 2000, True, 50, 60)
    assert out == [(600, LONG_PRESS, 50, 60)], out
    _feed(g, out, 2000, 2500, False)
    assert len(out) == 1               # no TAP or swipe after the long press


def test_just_under_long_press_is_tap():
    g = GestureRecognizer()
    out = []
    _one(g, 0, True, out)
    _one(g, 599, True, out)
    _one(g, 610, False, out)
    _feed(g, out, 620, 1000, False)
    assert _evs(out) == [TAP], out
    g = GestureRecognizer()
    out = []
    _one(g, 0, True, out)
    _one(g, 600, True, out)
    assert _evs(out) == [LONG_PRESS], out


def test_movement_cancels_long_press():
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 100, True, 100, 100, 125, 100)   # > 20 px slop, < 40
    _feed(g, out, 100, 1000, True, 125, 100)
    _feed(g, out, 1000, 1200, False)
    assert out == [], out


def test_small_jitter_still_taps():
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 150, True, 100, 100, 118, 85)
    _feed(g, out, 150, 400, False)
    assert _evs(out) == [TAP], out


def test_swipes_all_directions():
    cases = ((SWIPE_R, 60, 120, 180, 120), (SWIPE_L, 180, 120, 60, 120),
             (SWIPE_D, 120, 60, 120, 180), (SWIPE_U, 120, 180, 120, 60))
    for ev, x0, y0, x1, y1 in cases:
        g = GestureRecognizer(double_tap=True)
        out = []
        _feed(g, out, 0, 200, True, x0, y0, x1, y1)
        _feed(g, out, 200, 1000, False)
        assert out == [(260, ev, x0, y0)], (NAMES[ev], out)


def test_swipe_threshold_edges():
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 100, True, 100, 100, 140, 100)   # exactly 40 px
    _feed(g, out, 100, 400, False)
    assert _evs(out) == [SWIPE_R], out
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 100, True, 100, 100, 100, 61)    # 39 px up
    _feed(g, out, 100, 400, False)
    assert out == [], out


def test_swipe_dominant_axis_and_slow_drag():
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 100, True, 100, 100, 150, 130)
    _feed(g, out, 100, 400, False)
    assert _evs(out) == [SWIPE_R], out
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 100, True, 200, 100, 170, 80)    # leaves the slop early ...
    _feed(g, out, 100, 1500, True, 170, 80, 150, 60)  # ... then drags slowly
    _feed(g, out, 1500, 1800, False)
    assert _evs(out) == [SWIPE_L], out


def test_double_tap_suppresses_single():
    g = GestureRecognizer(double_tap=True)
    out = []
    _feed(g, out, 0, 80, True, 100, 100)
    _feed(g, out, 80, 230, False)
    _feed(g, out, 230, 300, True, 110, 95)
    _feed(g, out, 300, 1200, False)
    assert out == [(360, DOUBLE_TAP, 110, 95)], out


def test_tap_delayed_by_window_when_flag_set():
    g = GestureRecognizer(double_tap=True)
    out = []
    _feed(g, out, 0, 80, True, 10, 20)
    _feed(g, out, 80, 1000, False)
    assert out == [(430, TAP, 10, 20)], out   # lift at 80 + 350


def test_double_tap_window_edges():
    # second touch-down 349 ms after the lift: double tap
    g = GestureRecognizer(double_tap=True)
    out = []
    _feed(g, out, 0, 80, True)
    _feed(g, out, 80, 420, False)
    _one(g, 429, True, out)
    _feed(g, out, 430, 500, True)
    _feed(g, out, 500, 1200, False)
    assert _evs(out) == [DOUBLE_TAP], out
    # 350 ms: window expired -> two single taps
    g = GestureRecognizer(double_tap=True)
    out = []
    _feed(g, out, 0, 80, True)
    _feed(g, out, 80, 430, False)
    _feed(g, out, 430, 500, True)
    _feed(g, out, 500, 1500, False)
    assert [(e[0], e[1]) for e in out] == [(430, TAP), (850, TAP)], out


def test_far_second_tap_is_two_taps():
    g = GestureRecognizer(double_tap=True)
    out = []
    _feed(g, out, 0, 80, True, 20, 20)
    _feed(g, out, 80, 200, False)
    _feed(g, out, 200, 280, True, 200, 200)
    _feed(g, out, 280, 1200, False)
    assert [(e[1], e[2]) for e in out] == [(TAP, 20), (TAP, 200)], out
    assert out[0][0] == 340 and out[1][0] == 630, out


def test_tap_then_swipe_in_window():
    g = GestureRecognizer(double_tap=True)
    out = []
    _feed(g, out, 0, 80, True, 100, 100)
    _feed(g, out, 80, 200, False)
    _feed(g, out, 200, 300, True, 100, 100, 180, 100)
    _feed(g, out, 300, 800, False)
    assert _evs(out) == [TAP, SWIPE_R], out
    assert out[0][0] < 300          # TAP released as soon as the move shows


def test_tap_then_long_press_in_window_is_queued():
    g = GestureRecognizer(double_tap=True)
    out = []
    _feed(g, out, 0, 80, True, 30, 30)
    _feed(g, out, 80, 200, False)
    _feed(g, out, 200, 1000, True, 40, 40)
    assert [(e[0], e[1], e[2]) for e in out] == [(800, TAP, 30), (810, LONG_PRESS, 40)], out


def test_triple_tap_is_double_then_tap():
    g = GestureRecognizer(double_tap=True)
    out = []
    t = 0
    for _ in range(3):
        t = _feed(g, out, t, t + 70, True)
        t = _feed(g, out, t, t + 130, False)
    _feed(g, out, t, t + 1000, False)
    assert _evs(out) == [DOUBLE_TAP, TAP], out


def test_ticks_wraparound():
    base = ticks_add(0, -300)      # press straddles the tick wrap
    g = GestureRecognizer(double_tap=True)
    out = []
    _feed(g, out, 0, 1000, True, base=base)
    _feed(g, out, 1000, 1500, False, base=base)
    assert [(e[0], e[1]) for e in out] == [(600, LONG_PRESS)], out
    g = GestureRecognizer(double_tap=True)
    out = []
    _feed(g, out, 0, 80, True, base=base)
    _feed(g, out, 80, 200, False, base=base)
    _feed(g, out, 200, 260, True, base=base)
    _feed(g, out, 260, 1000, False, base=base)
    assert _evs(out) == [DOUBLE_TAP], out


def test_queue_next_pending_and_reset():
    g = GestureRecognizer(double_tap=True)
    g.update(0, True, 30, 30)
    g.update(70, False)
    g.update(200, True, 40, 40)
    assert g.update(900, True, 40, 40) == TAP
    assert g.pending() == 1 and g.next() == LONG_PRESS and g.ev_x == 40
    assert g.pending() == 0 and g.next() == NONE
    g.update(950, True, 40, 40)
    g.reset()
    assert not g.down and g.pending() == 0
    assert g.update(1000, False) == NONE
    assert len(NAMES) == 8 and NAMES[SWIPE_D] == "SWIPE_D"


def test_idle_emits_nothing():
    g = GestureRecognizer(double_tap=True)
    out = []
    _feed(g, out, 0, 5000, False)
    assert out == []
