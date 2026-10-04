from tests import Skip, fakes
from finder import haptic_patterns as hp
from finder import tuning as T
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
    for on_ms, off_ms in pattern:
        iv.append((t, t + on_ms))
        t += on_ms + off_ms
    return iv


def _flat(p):
    a = []
    for on_ms, off_ms in p:
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
        for on_ms, off_ms in hp.PATTERNS[name]:
            assert on_ms >= T.HAPTIC_MIN_PULSE_MS, name
        for on_ms, off_ms in hp.PATTERNS[name][:-1]:
            assert off_ms >= T.HAPTIC_MIN_GAP_MS, name
        assert hp.PATTERNS[name][-1][1] == 0, name
    assert hp.MIN_PULSE_MS == T.HAPTIC_MIN_PULSE_MS == 60
    assert hp.HB_RESUME_MS == 1000 and hp.EVENT_GUARD_MS == 1000
    assert hp.MAX_DUTY_PCT == 12 and hp.BLANKING_MS == T.HAPTIC_BLANKING_MS
    # rank order follows the token priority list (highest first)
    r = [T.HAPTIC_RANK[n] for n in T.HAPTIC_NAMES]
    assert r == sorted(r, reverse=True)
    assert hp.FOUND is hp.PATTERNS["FOUND"] and hp.TICK is hp.PATTERNS["TICK"]


def test_tokens_json_alignment():
    import json
    import os
    f = "docs/design/tokens.json"          # the runner works from the repo root
    try:
        os.stat(f)
    except OSError:
        raise Skip("no docs/design/tokens.json")
    with open(f) as fh:
        h = json.load(fh)["haptics"]
    assert set(h["patterns"]) == set(hp.PATTERNS)
    for name, arr in h["patterns"].items():
        assert _flat(hp.PATTERNS[name]) == arr, name
    assert list(hp.NAMES) == h["priority"]
    assert hp.MIN_PULSE_MS == h["min_pulse_ms"] and hp.MIN_GAP_MS == h["min_gap_ms"]
    assert set(h["modes"]) == {"FULL", "EVENTS", "OFF"}   # MODE_FULL / MODE_EVENTS / MODE_OFF


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
    pl.play_named("FARTHER")
    assert pl.tick(500) == 1.0
    assert pl.tick(799) == 1.0
    assert pl.tick(800) == 0.0


def test_late_tick_never_skips_a_pulse():
    pl = HapticPlayer()
    pl.play_named("TICK", 0)
    assert pl.tick(70) == 1.0          # would have ended at 60: starts late instead
    assert pl.tick(129) == 1.0
    assert pl.tick(130) == 0.0
    # frame-rate ticking (33 ms) still delivers all four FOUND pulses
    pl = HapticPlayer()
    pl.play_named("FOUND", 1000)
    tr = _trace(pl, 1005, 2400, 33)
    assert len(_edges(tr, 1005, 33)) == 4
    # and all five LOST pulses (60/60 stutter)
    pl = HapticPlayer()
    pl.play_named("LOST", 0)
    assert len(_edges(_trace(pl, 0, 1200, 33), 0, 33)) == 5
    # a late off tick: the gap is timed from the real off edge (§7 >= 60 ms)
    pl = HapticPlayer()
    pl.play_named("FOUND", 0)
    assert pl.tick(0) == 1.0 and pl.tick(79) == 1.0
    assert pl.tick(95) == 0.0          # first pulse ends 15 ms late
    assert _edges(_trace(pl, 96, 290), 96) == [(155, 235)]


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
    # countdown TICKs at ~1 Hz: a TICK is never guarded against a TICK
    pl = HapticPlayer()
    assert pl.play_named("TICK", 0)
    _trace(pl, 0, 990)
    assert pl.play_named("TICK", 990)
    assert pl.tick(990) == 1.0
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
    pl = HapticPlayer()
    assert pl.play_named("HOLD", 0)
    _trace(pl, 0, 1200)
    assert pl.play_named("TICK", 1200)
    _trace(pl, 1200, 1600)
    assert pl.tick(1610) == 0.0                # HOLD ends 10 ms late: the gap still counts
    assert _edges(_trace(pl, 1611, 1800), 1611) == [(1610 + gap, 1670 + gap)]


def test_queued_event_latest_wins():
    pl = HapticPlayer()
    assert pl.play_named("HOLD", 0)            # 0..1600
    _trace(pl, 0, 1100)
    assert pl.play_named("CLOSER", 1100)       # waits for HOLD
    assert pl.play_named("FARTHER", 1200)      # same rank: replaces CLOSER
    assert not pl.play_named("TICK", 1250)     # lower than the waiting one: dropped
    ed = _edges(_trace(pl, 1100, 2500), 1100)
    gap = hp.MIN_GAP_MS
    assert ed[1:] == [(1600 + gap, 1900 + gap)], ed   # only FARTHER (300 ms) plays


def test_heartbeat_resumes_one_second_after_event():
    pl = HapticPlayer()
    assert pl.heartbeat("TICK", 0)
    tr = _trace(pl, 0, 30)
    assert pl.play_named("CLOSER", 30)         # the heartbeat pulse plays out, then
    tr += _trace(pl, 30, 700)                   # CLOSER starts 60 ms after it (§7)
    assert _edges(tr, 0) == [(0, 60), (120, 180), (240, 300), (360, 560)]
    end = 560                                   # CLOSER: 120 + 440
    assert not pl.heartbeat("TICK", end + 999)
    assert not pl.heartbeat("TICK", end + 500)
    assert pl.heartbeat("TICK", end + 1000)
    assert pl.tick(end + 1000) == 1.0
    # a frame with an event and a heartbeat: the event replaces the heartbeat
    pl = HapticPlayer()
    pl.play_named("FARTHER", 0)
    pl.heartbeat("TICK", 0)
    assert _edges(_trace(pl, 0, 1000), 0) == [(0, 300)]
    pl.heartbeat("TICK", 1000)               # heartbeat 700 ms after: dropped
    pl.heartbeat("TICK", 1300)               # 1 s after the event: plays
    assert _edges(_trace(pl, 1000, 1500), 1000) == [(1300, 1360)]
    pl.heartbeat("DOUBLE", 2000)
    assert _edges(_trace(pl, 2000, 2400), 2000) == [(2000, 2060), (2140, 2200)]
    pl = HapticPlayer()
    pl.play_named("NOPE", 0)
    pl.heartbeat("TICK", 0)
    assert _edges(_trace(pl, 0, 1000), 0) == [(0, 300), (450, 750)]


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


def test_zone_tempos_keep_duty():
    from finder import tuning as T
    for p in T.ZONE_PERIOD_MS:                     # every zone keeps TICK <= 12 % duty
        assert p >= hp.min_period(hp.TICK)


def test_per_frame_allocation_free_on_micropython():
    import gc
    if not hasattr(gc, "mem_alloc"):
        raise Skip("needs MicroPython gc.mem_alloc")
    from finder import tuning as T
    per = T.ZONE_PERIOD_MS
    pl = HapticPlayer()
    pl.set_metronome(per[1], 0)
    ev = ["TICK"]
    gc.collect()
    gc.disable()
    try:
        a0 = gc.mem_alloc()
        for i in range(200):
            pl.set_metronome(per[1 + (i & 1)], i)
            pl.tick(i)
        pl.heartbeat(ev[0], 300)
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


def test_metronome_stop():
    pl = HapticPlayer()
    pl.set_metronome(1000, 0)
    assert pl.tick(0) == 1.0
    pl.set_metronome(0)
    assert pl.tick(1) == 1.0           # a started beat finishes its pulse
    assert _edges(_trace(pl, 60, 3000), 60) == []
    assert pl.period_ms == 0


def test_event_preempts_metronome_and_grid_continues():
    pl = HapticPlayer()
    pl.set_metronome(500, 0)
    tr = _trace(pl, 0, 950)
    pl.play_named("FOUND", 950)             # 950..2010 (last pulse 1510..2010)
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
    tr = _trace(pl, 0, 10)
    assert pl.play_named("FARTHER", 10)   # the beat pulse plays out, FARTHER 60 ms after it
    assert pl.active and pl.busy
    tr += _trace(pl, 10, 2100)         # the 1000 beat is < 1 s after the event: dropped
    assert _edges(tr, 0) == [(0, 60), (120, 420), (2000, 2060)]


def test_event_keeps_min_gap_after_any_pulse():
    # §7: every pulse and every gap >= 60 ms, also when an event follows a
    # heartbeat pulse or pre-empts an event
    def check(tr):
        ed = _edges(tr, 0)
        for a, b in ed:
            assert b - a >= hp.MIN_PULSE_MS, ed
        for i in range(1, len(ed)):
            assert ed[i][0] - ed[i - 1][1] >= hp.MIN_GAP_MS, ed
        return ed
    for d in range(10, 120, 10):
        pl = HapticPlayer()
        assert pl.heartbeat("TICK", 0)
        tr = _trace(pl, 0, d)
        assert pl.play_named("DOUBLE", d)     # bump-ready right after a heartbeat
        tr += _trace(pl, d, 1000)
        ed = check(tr)
        assert len(ed) == 3 and ed[0] == (0, 60), (d, ed)
    for d, first in ((310, (360, 440)), (100, (360, 440))):
        pl = HapticPlayer()
        assert pl.play_named("NOPE", 0)       # 0..300, off 300..450, 450..750
        tr = _trace(pl, 0, d)
        assert pl.play_named("FOUND", d)      # pre-empts NOPE (in its gap / mid-pulse)
        tr += _trace(pl, d, 2000)
        ed = check(tr)
        assert ed[0] == (0, 300) and ed[1] == first and len(ed) == 5, (d, ed)
    pl = HapticPlayer()                       # the heartbeat pulse ends on a late tick
    assert pl.heartbeat("TICK", 0) and pl.tick(0) == 1.0
    assert pl.play_named("CLOSER", 30)
    assert pl.tick(30) == 1.0 and pl.tick(59) == 1.0 and pl.tick(75) == 0.0
    assert _edges(_trace(pl, 76, 250), 76) == [(135, 195)]


def test_modes():
    pl = HapticPlayer()
    pl.set_metronome(1000, 0)
    assert pl.tick(0) == 1.0
    assert pl.tick(1) == 1.0
    pl.set_mode(hp.MODE_EVENTS)        # heartbeats muted, events still play
    assert _edges(_trace(pl, 2, 1100), 2) == []
    assert not pl.heartbeat("TICK", 1100)
    pl.play_named("FARTHER", 1100)
    assert pl.tick(1100) == 1.0
    pl.set_mode(hp.MODE_OFF)           # off: cancels and rejects
    assert pl.tick(1101) == 0.0 and not pl.busy
    assert not pl.play_named("FOUND", 1102)
    assert _edges(_trace(pl, 1102, 3000), 1102) == []
    pl.set_mode(hp.MODE_FULL)
    ed = _edges(_trace(pl, 3000, 4100), 3000)
    assert ed == [(3000, 3060), (4000, 4060)]   # grid kept while off


def test_blank_window_merges_and_expires():
    w = hp.BlankWindow()
    assert not w.active(0)
    w.extend(100, hp.TOTAL_MS["TICK"] + hp.BLANKING_MS)       # 60 + 0 + 150
    assert not w.active(99) and w.active(100) and w.active(309) and not w.active(310)
    w.extend(400, 100)
    w.extend(450, 20)                  # inside: keeps the later end
    assert w.active(499) and not w.active(500)
    w.extend(600, 100)
    w.extend(650, 200)                 # overlapping: extends the end
    assert w.active(849) and not w.active(850)
    w.reset()
    t0 = ticks_add(0, -50)             # across the ticks wrap
    w.extend(t0, 100)
    assert w.active(ticks_add(t0, 99)) and not w.active(ticks_add(t0, 100))
    w.extend(1000, 100)
    w.expire(1099)
    assert w.active(1050)                  # a late query inside the window still blanks
    w.expire(1100)
    assert not w.active(1050) and w.until is None
    w.extend(1000, 100)
    w.reset()
    assert not w.active(1000)


def test_stronger_and_total_ms():
    assert hp.stronger(None, "TICK") == "TICK"
    assert hp.stronger("TICK", "FOUND") == "FOUND"
    assert hp.stronger("FOUND", "TICK") == "FOUND"
    assert hp.stronger("TICK", "TICK") == "TICK"
    for n in hp.NAMES:
        assert hp.TOTAL_MS[n] == sum(T.HAPTIC_PATTERNS[n])


def test_ticks_wraparound():
    t0 = ticks_add(0, -40)             # just before the ticks wrap point
    pl = HapticPlayer()
    pl.play_named("FARTHER", t0)
    assert pl.tick(t0) == 1.0
    assert pl.tick(ticks_add(t0, 299)) == 1.0
    assert pl.tick(ticks_add(t0, 300)) == 0.0
    pl.set_metronome(500, ticks_add(t0, 1300))
    ons = [ticks_add(t0, k) for k in range(300, 2400) if pl.tick(ticks_add(t0, k))]
    assert ticks_add(t0, 1300) in ons and ticks_add(t0, 1360) not in ons
    assert ticks_add(t0, 1800) in ons


def test_motor_pwm_apply_on_change():
    m = fakes.install()
    from hal.haptics import Motor
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
    from hal.haptics import Motor
    mo = Motor()
    pl = HapticPlayer()
    pl.set_metronome(500, 0)
    for t in range(0, 1100):
        mo.set(pl.tick(t))
    # 3 beats -> on/off pairs, written only on edges
    assert mo._pwm.history == [65535, 0] * 3


def test_heartbeat_handed_ahead_waits_for_its_time():
    """A heartbeat given a later start waits (``beat_due``), is not ``active``
    (the loop need not tick the motor every ms), starts on time, and can be
    cancelled until it starts; an event that starts first replaces it."""
    pl = HapticPlayer()
    assert pl.tick(0) == 0
    assert pl.heartbeat("TICK", 250)
    assert pl.beat_due == 250 and not pl.active and not pl.beat_playing
    assert pl.tick(249) == 0 and pl.beat_due == 250
    assert pl.tick(250) == 1.0 and pl.active and pl.beat_playing and pl.beat_due is None
    assert not pl.cancel_heartbeat()          # on: plays out
    assert pl.tick(250 + hp.MIN_PULSE_MS - 1) == 1.0 and pl.tick(250 + hp.MIN_PULSE_MS) == 0
    assert pl.heartbeat("TICK", 900) and pl.cancel_heartbeat()
    assert pl.beat_due is None and all(pl.tick(t) == 0 for t in range(400, 1200, 5))
    assert pl.heartbeat("TICK", 1500)         # first output tick late: a full pulse from it
    assert pl.tick(1520) == 1.0 and pl.tick(1520 + hp.MIN_PULSE_MS - 1) == 1.0
    pl = HapticPlayer()
    pl.tick(0)
    assert pl.heartbeat("TICK", 250) and pl.play_named("CLOSER", 100)
    assert pl.beat_due is None and pl.busy
