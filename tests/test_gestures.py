from finder.compat import ticks_add
from finder import tuning as T
from finder.gestures import (GestureRecognizer, NONE, TAP, LONG_PRESS,
                             SWIPE_L, SWIPE_R, SWIPE_U, SWIPE_D, NAMES)

DT = 10


def _feed(g, out, t0, t1, touching, x=120, y=120, x1=None, y1=None, base=0, multi=False):
    """Samples every DT ms in [t0, t1); position ramps to (x1, y1)."""
    x1 = x if x1 is None else x1
    y1 = y if y1 is None else y1
    n = (t1 - t0) // DT
    for i in range(n):
        t = t0 + i * DT
        f = i / (n - 1) if n > 1 else 1.0
        xi = int(round(x + (x1 - x) * f))
        yi = int(round(y + (y1 - y) * f))
        ev = g.update(ticks_add(base, t), touching, xi, yi, multi)
        if ev:
            out.append((t, ev, g.ev_x, g.ev_y))
    return t1


def _one(g, t, touching, out, x=120, y=120, multi=False):
    ev = g.update(t, touching, x, y, multi)
    if ev:
        out.append((t, ev, g.ev_x, g.ev_y))


def _evs(out):
    return [e[1] for e in out]


def test_defaults_follow_spec():
    g = GestureRecognizer()
    assert g.long_ms == T.LONG_PRESS_MS == 800 and g.slop_px == T.TAP_MOVE_PX == 12
    assert (g.tap_min_ms, g.tap_max_ms) == (T.TAP_MIN_MS, T.TAP_MAX_MS) == (60, 400)


def test_single_tap():
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 100, True, 30, 40)
    _feed(g, out, 100, 1000, False)
    assert out == [(160, TAP, 30, 40)], out   # lift seen at 100 + 60 debounce
    assert not g.down


def test_tap_duration_window_edges():
    # duration = first touching sample .. first lifted sample (ui-spec §8: 60-400 ms)
    for ms, want in ((20, []), (59, []), (60, [TAP]), (400, [TAP]), (401, []), (790, [])):
        g = GestureRecognizer()
        out = []
        _one(g, 0, True, out)
        _one(g, ms - 1, True, out)
        _one(g, ms, False, out)
        _feed(g, out, ms + 10, ms + 500, False)
        assert _evs(out) == want, (ms, out)


def test_brisk_taps_at_frame_rate_sampling():
    # the watch samples touch once per ~45-50 ms frame: a 60-90 ms tap seen by a
    # single sample still taps (the floor uses the longest possible contact)
    for period in (45, 50):
        for ms in (60, 70, 80, 90):
            for ph in range(0, period, 5):
                g = GestureRecognizer()
                got = []
                t = ph
                while t < 1500:
                    e = g.update(t, 500 <= t < 500 + ms)
                    if e:
                        got.append(e)
                    t += period
                assert got == [TAP], (period, ms, ph, got)


def test_began_marks_the_touch_down_sample():
    g = GestureRecognizer()
    seen = []
    for t, touching in ((0, False), (10, True), (20, True), (30, False), (50, True),
                        (60, False), (150, False), (160, True)):
        g.update(t, touching)
        if g.began:
            seen.append(t)
    assert seen == [10, 160], seen      # a dropout under the debounce is the same press


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
    _feed(g, out, 450, 1200, True)
    _feed(g, out, 1200, 1700, False)
    assert [(e[0], e[1]) for e in out] == [(800, LONG_PRESS)], out


def test_long_press_fires_while_held_at_threshold():
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 2000, True, 50, 60)
    assert out == [(800, LONG_PRESS, 50, 60)], out
    _feed(g, out, 2000, 2500, False)
    assert len(out) == 1               # no TAP or swipe after the long press


def test_just_under_long_press_gives_nothing():
    g = GestureRecognizer()
    out = []
    _one(g, 0, True, out)
    _one(g, 799, True, out)
    _one(g, 810, False, out)
    _feed(g, out, 820, 1200, False)
    assert out == [], out              # past the tap window, short of a long press
    g = GestureRecognizer()
    out = []
    _one(g, 0, True, out)
    _one(g, 800, True, out)
    assert _evs(out) == [LONG_PRESS], out


def test_movement_cancels_long_press():
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 100, True, 100, 100, 125, 100)   # > 12 px slop, < 40
    _feed(g, out, 100, 1000, True, 125, 100)
    _feed(g, out, 1000, 1200, False)
    assert out == [], out


def test_small_jitter_still_taps():
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 150, True, 100, 100, 112, 88)    # 12 px: inside the slop
    _feed(g, out, 150, 400, False)
    assert _evs(out) == [TAP], out
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 150, True, 100, 100, 113, 100)   # 13 px: a short drag
    _feed(g, out, 150, 400, False)
    assert out == [], out


def test_multi_touch_gives_nothing():
    g = GestureRecognizer()
    out = []
    _one(g, 0, True, out)
    _one(g, 50, True, out, multi=True)    # a second finger joins
    _one(g, 100, True, out)
    _feed(g, out, 110, 500, False)
    assert out == [], out
    _feed(g, out, 500, 1500, True, multi=True)
    _feed(g, out, 1500, 2000, False)
    assert out == [], out                 # no LONG_PRESS either
    _feed(g, out, 2000, 2100, True)       # the next one-finger press is fine
    _feed(g, out, 2100, 2500, False)
    assert _evs(out) == [TAP], out


def test_swipes_all_directions():
    cases = ((SWIPE_R, 60, 120, 180, 120), (SWIPE_L, 180, 120, 60, 120),
             (SWIPE_D, 120, 60, 120, 180), (SWIPE_U, 120, 180, 120, 60))
    for ev, x0, y0, x1, y1 in cases:
        g = GestureRecognizer()
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


def test_ticks_wraparound():
    base = ticks_add(0, -300)      # press straddles the tick wrap
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 1000, True, base=base)
    _feed(g, out, 1000, 1500, False, base=base)
    assert [(e[0], e[1]) for e in out] == [(800, LONG_PRESS)], out
    g = GestureRecognizer()
    out = []
    _feed(g, out, 250, 350, True, base=base)
    _feed(g, out, 350, 1000, False, base=base)
    assert [(e[0], e[1]) for e in out] == [(410, TAP)], out


def test_reset_and_names():
    g = GestureRecognizer()
    g.update(0, True, 30, 30)
    assert g.update(800, True, 40, 40) == LONG_PRESS and g.ev_x == 30
    g.reset()
    assert not g.down
    assert g.update(1000, False) == NONE
    assert len(NAMES) == 8 and NAMES[SWIPE_D] == "SWIPE_D"


def test_idle_emits_nothing():
    g = GestureRecognizer()
    out = []
    _feed(g, out, 0, 5000, False)
    assert out == []
