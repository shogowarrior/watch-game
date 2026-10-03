from tests import fakes
from finder import haptic_patterns as hp
from finder.compat import ticks_add
from finder.haptic_patterns import HapticPlayer


def _trace(pl, t0, t1, step=1):
    """Strength at every ``step`` ms in [t0, t1)."""
    out = []
    t = t0
    while t < t1:
        out.append(pl.tick(t))
        t += step
    return out


def _edges(trace, t0=0, step=1):
    """(t_on, t_off) intervals where the trace is non-zero."""
    iv = []
    on = None
    for i, s in enumerate(trace):
        t = t0 + i * step
        if s and on is None:
            on = t
        elif not s and on is not None:
            iv.append((on, t))
            on = None
    if on is not None:
        iv.append((on, t0 + len(trace) * step))
    return iv


def _expected(pattern, t0=0):
    iv = []
    t = t0
    for on_ms, off_ms, _ in pattern:
        iv.append((t, t + on_ms))
        t += on_ms + off_ms
    return iv


def _flat(p):
    a = []
    for on_ms, off_ms, _ in p:
        a.append(on_ms)
        if off_ms:
            a.append(off_ms)
    return a


def test_patterns_match_tokens():
    from finder import tuning as T
    assert set(hp.PATTERNS) == set(T.HAPTIC_PATTERNS) == set(T.HAPTIC_NAMES)
    assert len(hp.PATTERNS) == 9
    for name in T.HAPTIC_PATTERNS:
        assert _flat(hp.PATTERNS[name]) == list(T.HAPTIC_PATTERNS[name]), name
        for on_ms, off_ms, st in hp.PATTERNS[name]:
            assert on_ms >= T.HAPTIC_MIN_PULSE_MS and 0 < st <= 1, name
        for on_ms, off_ms, st in hp.PATTERNS[name][:-1]:
            assert off_ms >= T.HAPTIC_MIN_GAP_MS, name
        assert hp.PATTERNS[name][-1][1] == 0, name
        assert hp.priority_of(hp.PATTERNS[name]) == T.HAPTIC_RANK[name], name
    assert hp.MIN_PULSE_MS == T.HAPTIC_MIN_PULSE_MS == 60
    assert hp.HB_RESUME_MS == 1000 and hp.EVENT_GUARD_MS == 1000
    assert hp.MAX_DUTY_PCT == 12 and hp.BLANKING_MS == T.HAPTIC_BLANKING_MS
    # rank order follows the token priority list (highest first)
    r = [T.HAPTIC_RANK[n] for n in T.HAPTIC_NAMES]
    assert r == sorted(r, reverse=True)
    assert hp.FOUND is hp.PATTERNS["FOUND"] and hp.TICK is hp.PATTERNS["TICK"]


def test_tokens_json_alignment():
    import sys
    if sys.implementation.name != "cpython":
        return
    import json
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    f = os.path.join(root, "docs", "design", "tokens.json")
    if not os.path.exists(f):
        print("SKIP test_tokens_json_alignment: no tokens.json")
        return
    with open(f) as fh:
        h = json.load(fh)["haptics"]
    assert set(h["patterns"]) == set(hp.PATTERNS)
    for name, arr in h["patterns"].items():
        assert _flat(hp.PATTERNS[name]) == arr, name
    assert list(hp.NAMES) == h["priority"]
    assert hp.MIN_PULSE_MS == h["min_pulse_ms"] and hp.MIN_GAP_MS == h["min_gap_ms"]
    assert list(hp.MODE_NAMES) == sorted(h["modes"], key=hp.MODE_NAMES.index)


def test_every_token_name_plays_with_exact_edges():
    from finder import tuning as T
    from finder import arrow
    for name in (arrow.H_TICK, arrow.H_DOUBLE) + tuple(T.HEARTBEATS):
        assert name in hp.PATTERNS, name
    for name in T.HAPTIC_PATTERNS:
        pl = HapticPlayer()
        assert pl.play_named(name, 1000), name
        tr = _trace(pl, 1000, 4000)
        assert _edges(tr, 1000) == _expected(hp.PATTERNS[name], 1000), name
        assert not pl.busy, name
    pl = HapticPlayer()
    assert not pl.play_named("found", 0) and not pl.play_named("zone_closer", 0)
    assert pl.tick(0) == 0.0


def test_found_timing_and_strength():
    pl = HapticPlayer()
    pl.play_named("FOUND", 0)
    tr = _trace(pl, 0, 1059)
    assert tr[0] == 1.0 and tr[79] == 1.0
    assert tr[80] == 0.0 and tr[139] == 0.0    # first gap is exactly 80..140
    assert tr[140] == 1.0 and tr[219] == 1.0 and tr[220] == 0.0
    assert tr[280] == 1.0 and tr[359] == 1.0
    assert max(tr[360:560]) == 0.0             # 200 ms gap
    assert min(tr[560:1059]) == 1.0            # 500 ms final buzz
    assert pl.busy
    assert pl.tick(1059) == 1.0
    assert pl.tick(1060) == 0.0
    assert not pl.busy


def test_pending_start_uses_first_tick():
    pl = HapticPlayer()
    pl.play(hp.FARTHER)
    assert pl.tick(500) == 1.0
    assert pl.tick(799) == 1.0
    assert pl.tick(800) == 0.0


def test_late_tick_never_skips_a_pulse():
    pl = HapticPlayer()
    pl.play(hp.TICK, 0)
    assert pl.tick(70) == 1.0          # would have ended at 60: starts late instead
    assert pl.tick(129) == 1.0
    assert pl.tick(130) == 0.0
    # frame-rate ticking (33 ms) still delivers all four FOUND pulses
    pl = HapticPlayer()
    pl.play(hp.FOUND, 1000)
    tr = _trace(pl, 1005, 2400, 33)
    assert len(_edges(tr, 1005, 33)) == 4
    # and all five LOST pulses (60/60 stutter)
    pl = HapticPlayer()
    pl.play(hp.LOST, 0)
    assert len(_edges(_trace(pl, 0, 1200, 33), 0, 33)) == 5


def test_drop_rule_same_or_higher_within_guard():
    pl = HapticPlayer()
    assert pl.play_named("CLOSER", 0)
    assert not pl.play_named("FARTHER", 500)   # same rank started 500 ms ago
    assert not pl.play_named("TICK", 600)      # lower, higher started < 1 s ago
    assert pl.play_named("FOUND", 700)         # higher pre-empts
    assert pl.tick(700) == 1.0
    _trace(pl, 701, 3000)
    assert pl.play_named("FARTHER", 3000)      # guard long over
    pl = HapticPlayer()
    assert pl.play_named("CLOSER", 0)
    _trace(pl, 0, 1000)
    assert pl.play_named("FARTHER", 1000)      # exactly 1 s later: allowed
    # the WARM DOUBLE heartbeat never blocks a DOUBLE event (BLIP HUNT bug)
    pl = HapticPlayer()
    assert pl.heartbeat("DOUBLE", 0)
    assert pl.play_named("DOUBLE", 100)


def test_lower_event_waits_for_higher_to_finish():
    pl = HapticPlayer()
    assert pl.play_named("HOLD", 0)            # 0..1600
    _trace(pl, 0, 1200)
    assert pl.play_named("TICK", 1200)         # HOLD started >= 1 s ago: queued
    assert pl.busy
    tr = _trace(pl, 1200, 2000)
    ed = _edges(tr, 1200)
    assert ed[0] == (1200, 1600)               # HOLD's last pulse is not truncated
    gap = hp.MIN_GAP_MS
    assert ed[1] == (1600 + gap, 1660 + gap)   # then the queued TICK
    assert not pl.busy


def test_heartbeat_resumes_one_second_after_event():
    pl = HapticPlayer()
    assert pl.heartbeat("TICK", 0)
    assert pl.tick(0) == 1.0
    assert pl.play_named("CLOSER", 30)         # event cuts the running heartbeat
    tr = _trace(pl, 30, 400)
    assert _edges(tr, 30) == [(30, 90), (150, 210), (270, 400)]
    _trace(pl, 400, 471)
    end = 470                                   # CLOSER: 30 + 440
    assert not pl.heartbeat("TICK", end + 999)
    assert not pl.heartbeat("TICK", end + 500)
    assert pl.heartbeat("TICK", end + 1000)
    assert pl.tick(end + 1000) == 1.0
    # renderer frame: [event, heartbeat] -> the event replaces the heartbeat
    pl = HapticPlayer()
    pl.play_frame(["FARTHER", "TICK"], 0, has_event=True)
    assert _edges(_trace(pl, 0, 1000), 0) == [(0, 300)]
    pl.play_frame(["TICK"], 1000)               # heartbeat 700 ms after: dropped
    pl.play_frame(["TICK"], 1300)               # 1 s after the event: plays
    assert _edges(_trace(pl, 1000, 1500), 1000) == [(1300, 1360)]
    pl.play_frame(["DOUBLE"], 2000)
    assert _edges(_trace(pl, 2000, 2400), 2000) == [(2000, 2060), (2140, 2200)]


def test_metronome_grid_and_duty_cap():
    pl = HapticPlayer()
    pl.set_metronome(1000, 0)
    ed = _edges(_trace(pl, 0, 3500), 0)
    assert ed == [(0, 60), (1000, 1060), (2000, 2060), (3000, 3060)]
    pl = HapticPlayer()
    pl.set_metronome(500, 0)
    assert _edges(_trace(pl, 0, 1100), 0) == [(0, 60), (500, 560), (1000, 1060)]
    pl = HapticPlayer()
    pl.set_metronome(250, 0)            # 60/250 = 24 % > 12 %: raised to 500
    assert pl.period_ms == 500
    pl = HapticPlayer()
    pl.set_metronome(500, 0, "DOUBLE")  # 120 ms on: at least 1000
    assert pl.period_ms == 1000
    assert _edges(_trace(pl, 0, 1300), 0) == [(0, 60), (140, 200), (1000, 1060), (1140, 1200)]
    for name in hp.PATTERNS:
        p = hp.PATTERNS[name]
        assert hp.on_ms(p) * 100 <= hp.MAX_DUTY_PCT * hp.min_period(p), name


def test_tempo_table_matches_geometric_curve():
    tab = [hp.tempo_for_bucket(i) for i in range(hp.TEMPO_STEPS + 1)]
    assert tab[0] == hp.TEMPO_COLD_MS == 2400 and tab[-1] == hp.TEMPO_HOT_MS == 500
    for a, b in zip(tab, tab[1:]):
        assert a > b                               # strictly faster when hotter
    assert hp.tempo_for_bucket(-3) == tab[0] and hp.tempo_for_bucket(99) == tab[-1]
    ratio = hp.TEMPO_HOT_MS / hp.TEMPO_COLD_MS
    for i in range(101):
        c = i / 100
        exact = hp.TEMPO_COLD_MS * ratio ** c
        got = hp.tempo_ms(c)
        assert abs(got - exact) <= exact * 0.04 + 1, (c, got, exact)
        assert got == tab[hp.tempo_bucket(c)]
    assert hp.tempo_bucket(0.5) == hp.TEMPO_STEPS // 2
    assert hp.tempo_ms(-1) == 2400 and hp.tempo_ms(2) == 500
    assert hp.tempo_ms(0.3) > hp.tempo_ms(0.6)
    for p in tab:                                  # every tempo keeps TICK <= 12 % duty
        assert p >= hp.min_period(hp.TICK)


def test_per_frame_allocation_free_on_micropython():
    import gc
    if not hasattr(gc, "mem_alloc"):
        return  # CPython: nothing meaningful to measure
    pl = HapticPlayer()
    b = hp.tempo_bucket(0.5)
    pl.set_metronome(hp.tempo_for_bucket(b), 0)
    ev = ["TICK"]
    gc.collect()
    gc.disable()
    try:
        a0 = gc.mem_alloc()
        for i in range(200):
            pl.set_metronome(hp.tempo_for_bucket(b + (i & 1)), i)
            pl.tick(i)
            pl.blanked(i)
        pl.play_frame(ev, 300)
        for i in range(300, 400):
            pl.tick(i)
        used = gc.mem_alloc() - a0
    finally:
        gc.enable()
    assert used <= 128, used


def test_metronome_tempo_change_keeps_phase():
    pl = HapticPlayer()
    pl.set_metronome(1000, 0)
    ed = _edges(_trace(pl, 0, 2300), 0)
    assert ed[-1] == (2000, 2060)
    pl.set_metronome(500, 2300)        # next beat = last (2000) + 500
    pl.set_metronome(500, 2301)        # repeated calls are no-ops
    ed = _edges(_trace(pl, 2300, 3100), 2300)
    assert ed == [(2500, 2560), (3000, 3060)]
    pl.set_metronome(1000, 3100)       # slower: last (3000) + 1000
    ed = _edges(_trace(pl, 3100, 5100), 3100)
    assert ed == [(4000, 4060), (5000, 5060)]


def test_metronome_speedup_fires_immediately_when_overdue():
    pl = HapticPlayer()
    pl.set_metronome(2400, 0)
    _trace(pl, 0, 2000)
    pl.set_metronome(500, 2000)        # last + 500 = 500 is long past
    ed = _edges(_trace(pl, 2000, 3100), 2000)
    assert ed[0] == (2000, 2060)       # immediate beat, then 500 ms grid
    assert ed[1] == (2500, 2560) and ed[2] == (3000, 3060)


def test_metronome_stop_and_sync():
    pl = HapticPlayer()
    pl.set_metronome(1000, 0)
    assert pl.tick(0) == 1.0
    pl.stop_metronome()
    assert pl.tick(1) == 1.0           # a started beat finishes its pulse
    assert _edges(_trace(pl, 60, 3000), 60) == []
    pl.set_metronome(1000, 3000)
    pl.tick(3000)
    pl.sync(3300)                      # align to a ripple spawn
    ed = _edges(_trace(pl, 3001, 4400), 3001)
    assert ed == [(3001, 3060), (3300, 3360), (4300, 4360)]


def test_event_preempts_metronome_and_grid_continues():
    pl = HapticPlayer()
    pl.set_metronome(500, 0)
    tr = _trace(pl, 0, 950)
    pl.play(hp.FOUND, 950)             # 950..2010 (last pulse 1510..2010)
    tr += _trace(pl, 950, 3600)
    ed = _edges(tr, 0)
    beats = [e for e in ed if e[1] - e[0] == 60 and e[0] % 500 == 0]
    assert beats[:2] == [(0, 60), (500, 560)]
    # beats up to 2010 + 1000 are dropped; the grid resumes at 3500
    assert beats[2:] == [(3500, 3560)]
    assert (950, 1030) in ed           # FOUND first pulse intact


def test_event_cuts_running_beat():
    pl = HapticPlayer()
    pl.set_metronome(1000, 0)
    assert pl.tick(0) == 1.0
    pl.play(hp.FARTHER, 10)
    assert pl.tick(10) == 1.0          # event level replaces beat
    assert pl.tick(309) == 1.0
    assert pl.tick(310) == 0.0         # beat does not resume after the event
    assert pl.tick(1000) == 0.0        # 1000 is < 1 s after the event: dropped
    assert _edges(_trace(pl, 1001, 2100), 1001) == [(2000, 2060)]


def test_priority_override_and_stop():
    pl = HapticPlayer()
    assert pl.play(hp.FOUND, 0)
    assert pl.play(hp.TICK, 1500, priority=9) is True   # explicit rank pre-empts
    assert pl.tick(1500) == 1.0 and pl.tick(1560) == 0.0
    pl.stop()
    assert not pl.busy
    custom = ((60, 60, 0.5), (60, 0, 0.5))
    assert hp.priority_of(custom) == 0
    assert pl.play(custom, 5000)
    assert pl.tick(5000) == 0.5


def test_modes_and_intensity():
    pl = HapticPlayer(intensity=0.5)
    pl.set_metronome(1000, 0)
    assert pl.tick(0) == 0.5
    pl.set_intensity(2)
    assert pl.tick(1) == 1.0
    pl.set_mode("EVENTS")              # heartbeats muted, events still play
    assert pl.mode == hp.MODE_EVENTS
    assert _edges(_trace(pl, 2, 1100), 2) == []
    assert not pl.heartbeat("TICK", 1100)
    pl.play(hp.FARTHER, 1100)
    assert pl.tick(1100) == 1.0
    pl.set_enabled(False)              # off: cancels and rejects
    assert not pl.enabled
    assert pl.tick(1101) == 0.0
    assert not pl.play(hp.FOUND, 1102)
    assert _edges(_trace(pl, 1102, 3000), 1102) == []
    pl.set_mode("FULL")
    ed = _edges(_trace(pl, 3000, 4100), 3000)
    assert ed == [(3000, 3060), (4000, 4060)]   # grid kept while off
    pl.set_intensity(0)
    assert pl.tick(5000) == 0.0


def test_blanking_window():
    pl = HapticPlayer()
    assert not pl.blanked(0)
    pl.play(hp.TICK, 0)
    pl.tick(0)
    assert pl.blanked(0)
    pl.tick(59)
    pl.tick(60)                        # pulse ended
    assert pl.blanked(60) and pl.blanked(60 + hp.BLANKING_MS - 1)
    assert not pl.blanked(60 + hp.BLANKING_MS)


def test_ticks_wraparound():
    t0 = ticks_add(0, -40)             # just before the ticks wrap point
    pl = HapticPlayer()
    pl.play(hp.FARTHER, t0)
    assert pl.tick(t0) == 1.0
    assert pl.tick(ticks_add(t0, 299)) == 1.0
    assert pl.tick(ticks_add(t0, 300)) == 0.0
    pl.set_metronome(500, ticks_add(t0, 1300))
    ons = [ticks_add(t0, k) for k in range(300, 2400) if pl.tick(ticks_add(t0, k))]
    assert ticks_add(t0, 1300) in ons and ticks_add(t0, 1360) not in ons
    assert ticks_add(t0, 1800) in ons


def _motor_cls():
    try:
        from hal.haptics import Motor
    except ImportError:
        # tools/mpy/run.mjs does not copy hal/ yet (DIRS lacks "hal")
        import sys
        if sys.implementation.name != "micropython":
            raise
        print("SKIP test_haptics hal tests: hal/ not mounted in the WASM runner")
        return None
    return Motor


def test_motor_pwm_apply_on_change():
    m = fakes.install()
    Motor = _motor_cls()
    if Motor is None:
        return
    mo = Motor()
    pwm = mo._pwm
    assert pwm.pin.id == 4 and 200 <= pwm.freq() <= 1000
    assert pwm.duty_u16() == 0
    mo.set(1.0)
    assert pwm.duty_u16() == 65535
    mo.set(1.0)
    mo.set(1.0)
    assert pwm.history == [65535]      # written once
    mo.set(0.5)
    lo = int(0.35 * 65535)
    assert pwm.duty_u16() == lo + int((65535 - lo) * 0.5)
    mo.set(0.01)
    assert pwm.duty_u16() >= lo        # non-zero strength always spins up
    mo.off()
    assert pwm.duty_u16() == 0 and mo.strength == 0
    n = len(pwm.history)
    mo.off()
    assert len(pwm.history) == n
    mo.set(2)
    assert pwm.duty_u16() == 65535
    mo.deinit()
    assert m.pins[4].value() == 0


def test_player_drives_motor():
    fakes.install()
    Motor = _motor_cls()
    if Motor is None:
        return
    mo = Motor()
    pl = HapticPlayer()
    pl.set_metronome(500, 0)
    for t in range(0, 1100):
        mo.set(pl.tick(t))
    # 3 beats -> on/off pairs, written only on edges
    assert mo._pwm.history == [65535, 0] * 3
