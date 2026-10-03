from finder.compat import TickRing, ticks_add, ticks_diff, ticks_us, sleep_ms, clamp


def test_ticks_wrap():
    a = ticks_add(0, -5)
    assert ticks_diff(0, a) == 5
    assert ticks_diff(a, 0) == -5


def test_ticks_us_and_sleep_ms():
    a = ticks_us()
    sleep_ms(3)
    assert 2000 <= ticks_diff(ticks_us(), a) < 1000000   # same wrap-aware diff as ms ticks


def test_clamp():
    assert clamp(5, 0, 3) == 3 and clamp(-1, 0, 3) == 0 and clamp(2, 0, 3) == 2


def test_tick_ring_full_within_window_and_wrap():
    r = TickRing(3)
    assert not r.full_within(0, 1000)
    for t in (0, 400, 800):
        r.note(t)
    assert r.full_within(800, 1000) and r.full_within(1000, 1000)
    assert not r.full_within(1001, 1000)          # the oldest left the window
    r.note(1500)                                  # oldest is now 400
    assert not r.full_within(1500, 1000) and r.full_within(1400, 1000)
    r.clear()
    assert r.n == 0 and not r.full_within(1500, 10 ** 6)
    t0 = ticks_add(0, -300)                       # across the ticks wrap
    for k in range(3):
        r.note(ticks_add(t0, 200 * k))
    assert r.full_within(ticks_add(t0, 900), 1000)
    r.expire(ticks_add(t0, 1400), 1000)           # newest still in the window: kept
    assert r.n == 3
    r.expire(ticks_add(t0, 1401), 1000)
    assert r.n == 0
