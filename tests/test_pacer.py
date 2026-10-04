"""app/pacer.py: frame grid, slot times, lock choice from measured cost, log counters."""

from tests import Skip
from finder import tuning as T
from app.pacer import FramePacer, LOCKS


def _run(pc, t, ms, busy, cap=20, shown_after=0):
    """Frames whenever due from ``t`` for ``ms``, each costing ``busy`` ms
    (an int or a function of the frame count). Returns (slots, t)."""
    slots = []
    end = t + ms
    k = 0
    while t < end:
        if t >= pc.t_next:
            b = busy(k) if callable(busy) else busy
            slots.append(pc.begin(t, cap, b))
            pc.shown(t + shown_after)
            k += 1
            t += b if b > 0 else 1
            continue
        t = pc.t_next if pc.t_next < end else end
    return slots, t


def test_slots_on_the_grid_and_missed_slots_skipped():
    pc = FramePacer(0)
    assert pc.fps == 20 and pc.period == 50
    assert pc.begin(0, 20) == 0 and pc.t_next == 50
    assert pc.begin(57, 20) == 50 and pc.t_next == 100     # late start: drawn at its slot
    assert pc.missed == 0
    assert pc.begin(230, 20) == 200 and pc.t_next == 250   # 100 and 150 missed
    assert pc.missed == 2


def test_cheap_frames_hold_the_cap():
    pc = FramePacer(0)
    slots, _ = _run(pc, 0, 5000, 30)
    assert pc.fps == 20 and pc.missed == 0
    assert all(slots[i + 1] - slots[i] == 50 for i in range(len(slots) - 1))


def test_lock_drops_at_once_to_a_rate_that_fits():
    pc = FramePacer(0)
    slots, t = _run(pc, 0, 400, 80)            # 80 ms frames: 20 fps misses slots
    assert pc.fps == 10 and pc.changes == 1
    pc.window()
    slots, t = _run(pc, t, 3000, 80)
    assert pc.fps == 10 and pc.missed == 0
    d = [slots[i + 1] - slots[i] for i in range(len(slots) - 1)]
    assert d and all(x == 100 for x in d), d    # even steps: the animation moves 100 ms a frame
    pc = FramePacer(0)
    _run(pc, 0, 1000, 120)
    assert pc.fps == 8                          # 120 ms fits 125, not 100


def test_one_slow_frame_never_moves_the_lock():
    pc = FramePacer(0)
    _run(pc, 0, 3000, lambda k: 200 if k == 20 else 30)
    assert pc.fps == 20 and pc.changes == 0
    _run(pc, 3000, 3000, lambda k: 200 if k in (3, 9) else 30)
    assert pc.fps == 5 and pc.changes == 1      # two in 16: a real cost


def test_lock_rises_one_step_after_headroom():
    pc = FramePacer(0)
    _run(pc, 0, 1000, 130)
    assert pc.fps == 7                          # 130 > 125
    slots, t = _run(pc, 1000, T.FPS_RAISE_MS + 3000, 90)
    assert pc.fps == 8 and pc.changes == 2      # one step, once the 130s left the ring
    slots, t = _run(pc, t, 2 * T.FPS_RAISE_MS + 1000, 90)
    assert pc.fps == 10                         # 90 * 1.1 = 99 fits 100
    _run(pc, t, 3 * T.FPS_RAISE_MS, 90)
    assert pc.fps == 10                         # 99 does not fit 50


def test_margin_holds_a_lock_with_too_little_headroom():
    pc = FramePacer(0)
    _run(pc, 0, 1000, 120)
    assert pc.fps == 8
    _run(pc, 1000, 4 * T.FPS_RAISE_MS, 95)      # 95 * 1.1 > 100: stay at 8
    assert pc.fps == 8


def test_cap_bounds_the_lock():
    pc = FramePacer(0)
    _run(pc, 0, 500, 10)
    assert pc.fps == 20
    _run(pc, 500, 500, 10, cap=10)              # saver: at once
    assert pc.fps == 10
    _run(pc, 1000, T.FPS_RAISE_MS - 200, 10, cap=20)
    assert pc.fps == 10                         # rises only after the raise time
    _run(pc, 1000 + T.FPS_RAISE_MS, 1000, 10, cap=20)
    assert pc.fps == 20
    pc = FramePacer(0, cap=15)                  # a cap between locks: the next one down
    assert pc.fps == 10


def test_state_only_frames_keep_the_lock():
    pc = FramePacer(0)
    _run(pc, 0, 1000, 80)
    assert pc.fps == 10
    t = 1000
    for k in range(100):                        # screen off: no cost, the grid keeps time
        t = pc.t_next
        pc.begin(t, 20)
    assert pc.fps == 10 and pc.t_next == t + 100
    pc.begin(pc.t_next, 10)
    assert pc.fps == 10
    pc.begin(pc.t_next, 8)                      # a lower cap still applies
    assert pc.fps == 8


def test_restart_draws_at_the_wake_and_ignores_its_busy_time():
    pc = FramePacer(0)
    _run(pc, 0, 1000, 30)
    pc.restart(5000)
    assert pc.t_next == 5000
    s = pc.begin(5130, 20, 400)                 # SLPOUT wait inside this window
    assert s == 5130 and pc.t_next == 5180      # a fresh frame, not a stale slot
    assert pc.fps == 20 and pc.changes == 0
    assert pc.begin(5180, 20, 30) == 5180


def test_interval_stats_and_window():
    pc = FramePacer(0)
    _run(pc, 0, 1000, 30)
    assert pc.frames == 20 and pc.iv_n == 19 and pc.iv_max == 50
    assert pc.jitter_ms() == 0.0
    pc.window()
    assert pc.frames == 0 and pc.missed == 0 and pc.iv_n == 0
    pc = FramePacer(0)
    for t in (0, 100, 150, 250, 300):
        pc.shown(1000 + t)
    assert pc.iv_n == 4 and pc.iv_max == 100
    assert abs(pc.jitter_ms() - 25.0) < 0.01    # 100, 50, 100, 50


def test_late_counters():
    pc = FramePacer(0)
    pc.begin(0, 20, 30)
    pc.begin(62, 20, 30)
    pc.begin(104, 20, 30)
    assert pc.late_sum == 16 and pc.late_max == 12


def test_locks_token_rules():
    assert LOCKS[0] == T.FPS_TARGET == T.FPS_CAP_MAX and LOCKS[-1] == T.FPS_CAP_MIN
    assert T.SAVER_FPS in LOCKS
    for a in (10, 20):                          # spawns land on frames in every zone
        assert a in LOCKS
        for per in T.ZONE_PERIOD_MS:
            assert per % (1000 // a) == 0, (a, per)


def test_long_window_sums_stay_small_ints():
    pc = FramePacer(0)
    t = 0
    for k in range(60000):
        t += 160 if k & 1 else 40
        pc.shown(t)
    assert pc.iv_sq <= 0x1FFFFFFF + 160 * 160
    assert 55.0 < pc.jitter_ms() < 65.0


def test_per_frame_allocation_free_on_micropython():
    import gc
    if not hasattr(gc, "mem_alloc"):
        raise Skip("needs MicroPython gc.mem_alloc")
    pc = FramePacer(0)
    t = 0
    for k in range(40):                         # fill the cost ring, settle the lock
        t = pc.t_next
        pc.begin(t, 20, 80)
        pc.shown(t + 80)
    gc.collect()
    gc.disable()
    try:
        a0 = gc.mem_alloc()
        for k in range(300):
            t = pc.t_next + (k % 7)
            pc.begin(t, 20, 70 + (k % 20))
            pc.shown(t + 75)
        used = gc.mem_alloc() - a0
    finally:
        gc.enable()
    assert used <= 64, used


def test_cost_is_the_second_largest_of_the_last_frames():
    pc = FramePacer(0)
    seq = []
    x = 12345
    for k in range(400):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        v = 40 + (x >> 16) % 60 if k % 50 < 40 else 150 + (x >> 16) % 10
        seq.append(v)
        pc._note(v)
        last = sorted(seq[-T.FPS_COST_N:])
        assert pc.cost == (last[-2] if len(last) > 1 else last[-1]), (k, last, pc.cost)
