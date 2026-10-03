import math
from finder.compat import ticks_add, ticks_diff
from finder.estimators.base import ACT_STILL, ACT_WALK
from finder import scan
from finder.scan import (ScanSession, fit_harmonic, evaluate, sigma_fit_deg,
                         READY, SWEEP, RESULT)
from sim.rng import Rng


def _cos_model(amp):
    def f(d):
        return -70.0 + amp * math.cos(d * math.pi / 180.0)
    return f


def _shadow_model(depth=12.0, width=50.0):
    """Body shadow: flat-ish front, a Gaussian notch behind the player."""
    def f(d):
        x = (d - 180.0) % 360.0
        if x > 180.0:
            x -= 360.0
        return -62.0 - depth * math.exp(-x * x / (2.0 * width * width))
    return f


def _cerr(a, b):
    d = (a - b) % 360.0
    return 360.0 - d if d > 180.0 else d


def _flat(ss, t):
    return 5.0, 0, ACT_STILL


def _drive(theta=90.0, noise=3.0, seed=1, model=None, motion=_flat, t0=0,
           peer_walk=None, drop=None, max_ms=40000, ss=None, peer=True, trace=None):
    """Simulate a paced turn: packets at 20 Hz from both sources, logic at 10 Hz.

    The player follows the pacer exactly, so the true heading is the wedge.
    Returns (session, [(t_rel, haptic)]).
    """
    rng = Rng(seed)
    model = model or _cos_model(5.0)
    if ss is None:
        ss = ScanSession(t0)
    haps = []
    t_upd = 0
    t = 0
    while t <= max_ms and ss.phase != RESULT:
        tt = ticks_add(t0, t)
        tilt, steps, act = motion(ss, t)
        ss.on_motion(tt, tilt, steps, act)
        if peer_walk is not None:
            ss.on_peer(tt, peer_walk(ss, t))
        if ss.phase == SWEEP:
            a = ss.active_ms + (0 if ss.paused else t - t_upd)
            h = a * 0.03
            if h < 360.0 and not (drop and drop[0] <= h < drop[1]):
                d = h - theta
                own = model(d) + rng.gauss(0.0, noise)
                pr = model(d) + 4.0 + rng.gauss(0.0, noise) if peer else None
                ss.on_packet(tt, own, pr)
        if t % 100 == 0:
            ss.update(tt)
            t_upd = t
            h = ss.pop_haptic()
            if h is not None:
                haps.append((t, h))
            if trace is not None:
                trace.append((t, ss.phase, ss.wedge_deg, ss.paused, ss.top_text, ss.countdown,
                              ss.fault))
        t += 50
    return ss, haps


def _sweep_t(ss, t):
    """ms since sweep start in the driver's relative time (t0 = 0)."""
    return None if ss.t_sweep is None else t - ss.t_sweep


# ---- pure maths --------------------------------------------------------------

def test_fit_harmonic_exact_two_sources():
    from array import array
    n = 72
    phi = array("f", [0.0] * n)
    val = array("f", [0.0] * n)
    src = bytearray(n)
    for i in range(n):
        p = (i // 2) * 10.0
        phi[i] = p
        src[i] = i & 1
        val[i] = (-70.0 if src[i] == 0 else -61.0) + 6.0 * math.cos((p - 123.0) * math.pi / 180.0)
    a1, th, sres, m = fit_harmonic(phi, val, src, n)
    assert abs(a1 - 6.0) < 0.01, a1
    assert _cerr(th, 123.0) < 0.1, th
    assert sres < 0.01 and m == n


def test_fit_degenerate_coverage():
    from array import array
    n = 40
    phi = array("f", [10.0] * n)
    val = array("f", [-70.0 + (i % 3) for i in range(n)])
    assert fit_harmonic(phi, val, bytearray(n), n) is None
    assert fit_harmonic(phi, val, bytearray(n), 5) is None


def test_evaluate_thresholds():
    ok, s0, r = evaluate(3.0, 2.0, 400)
    assert ok and r is None
    sf = sigma_fit_deg(3.0, 2.0, 400)
    assert abs(s0 - math.sqrt(sf * sf + 400.0)) < 1e-6
    assert s0 >= 20.0
    ok, s0, r = evaluate(1.9, 0.1, 400)       # 2*a1 = 3.8 dB < 4
    assert not ok and r == scan.R_NO_FIX
    ok, s0, r = evaluate(2.1, 0.1, 400)
    assert ok
    ok, s0, r = evaluate(3.0, 40.0, 100)      # sigma_fit ~108 deg
    assert not ok and r == scan.R_NO_FIX and s0 > 45.0
    ok1, s1, _ = evaluate(5.0, 3.0, 400, peer_walk_ms=2500)
    ok0, s00, _ = evaluate(5.0, 3.0, 400, peer_walk_ms=1500)
    assert ok1 and ok0 and abs(s1 - s00 - 15.0) < 1e-6
    ok, s0, r = evaluate(5.0, 3.0, 400, peer_walk_ms=4100)
    assert not ok and r == scan.R_FRIEND_MOVED


# ---- full sessions -----------------------------------------------------------

def test_known_theta_noise_levels():
    seed = 1
    for noise in (0.0, 2.0, 4.0, 8.0):
        for theta in (0.0, 75.0, 180.0, 290.0, 355.0):
            seed += 1
            ss, _ = _drive(theta=theta, noise=noise, seed=seed)
            r = ss.result
            assert r is not None, (noise, theta, ss.reason, ss.a1_db, ss.s0_deg)
            th, s0 = r
            err = _cerr(th, theta)
            assert err < 3.0 * ss.sigma_fit_deg + 3.0, (noise, theta, th, ss.sigma_fit_deg)
            assert 20.0 <= s0 <= 45.0
            assert ss.n > 400                      # both sources, 12 s at 20 Hz
            if noise == 0.0:
                assert err < 1.0 and abs(ss.a1_db - 5.0) < 0.1


def test_body_shadow_pattern():
    for i, theta in enumerate((30.0, 140.0, 250.0)):
        ss, _ = _drive(theta=theta, noise=3.0, seed=40 + i, model=_shadow_model())
        r = ss.result
        assert r is not None, ss.reason
        assert _cerr(r[0], theta) < 12.0, (theta, r)
        assert r[1] < 30.0


def test_single_source_still_fits():
    ss, _ = _drive(theta=200.0, noise=2.0, seed=9, peer=False)
    assert ss.result is not None and _cerr(ss.result[0], 200.0) < 10.0


def test_haptic_sequence_and_bins():
    tr = []
    ss, haps = _drive(theta=90.0, noise=0.0, seed=3, trace=tr)
    names = [h for _, h in haps]
    assert names == ["TICK"] * 3 + ["TICK"] * 3 + ["DOUBLE"] + ["TICK"] * 3 + ["CLOSER"], names
    # countdown ticks one second apart, first once flat is held 0.5 s
    assert [t for t, _ in haps[:3]] == [500, 1500, 2500], haps
    # sweep ticks at 45 deg = 1.5 s of active time
    sw = [t for t, _ in haps[3:10]]
    for a, b in zip(sw, sw[1:]):
        assert b - a == 1500
    # result: bins in 0..1, peak at theta, trough opposite
    sw = ss.sweep(ticks_add(ss.t_result, 50))
    wedge, bins, ab, paused = sw
    assert wedge == ss.theta_deg and not paused and ab == ss.best_bin == 3
    assert len(bins) == 12 and all(b is not None and 0.0 <= b <= 1.0 for b in bins)
    assert bins[3] == 1.0 and bins[9] == 0.0
    assert ss.sweep(ticks_add(ss.t_result, 250))[2] is None   # blink off
    assert ss.sweep(ticks_add(ss.t_result, 450))[2] == 3
    assert not ss.done(ticks_add(ss.t_result, 1100))
    assert ss.done(ticks_add(ss.t_result, 1200))
    assert ss.top_text is None and ss.word is None and ss.toast is None
    # wedge rises monotonically at 30 deg/s during the sweep, 12 s long
    sweep_rows = [row for row in tr if row[1] == SWEEP]
    ws = [row[2] for row in sweep_rows]
    assert all(b >= a for a, b in zip(ws, ws[1:]))
    assert abs(ws[10] - ws[0] - 30.0) < 1e-6
    assert all(row[4] is None for row in sweep_rows)
    assert 0.0 <= ss.mirror <= 1.0


def test_mirror_is_the_live_mirror():
    from finder.session import LiveMirror
    ss = ScanSession(0)
    lm = LiveMirror()
    assert ss.mirror is None
    ss._start_sweep(0)
    for i, r in enumerate((-60, -58, -70, -65, -55, -66)):
        t = 100 + 50 * i
        ss.on_packet(t, r, None)
        lm.add(t, r)
        assert ss.mirror == lm.value, (i, ss.mirror, lm.value)


def test_noisy_bins_stay_in_unit_range():
    for seed in range(60, 66):
        ss, _ = _drive(theta=40.0 * seed, noise=6.0, seed=seed)
        for b in ss.bins:
            assert b is None or 0.0 <= b <= 1.0, (seed, ss.bins)


def test_bins_without_data_are_none():
    ss, _ = _drive(theta=90.0, noise=1.0, seed=5, drop=(190.0, 260.0))
    assert ss.result is not None
    bins = ss.bins
    assert bins[7] is None and bins[8] is None, bins
    assert all(bins[k] is not None for k in (0, 1, 2, 3, 4, 5, 6, 9, 10, 11))


def test_active_bin_tracks_wedge():
    ss = ScanSession(0)
    seen = []
    def motion(s, t):
        if s.phase == SWEEP:
            tup = s.sweep()
            seen.append((tup[0], tup[2], tup[3], len(tup[1])))
        return 5.0, 0, ACT_STILL
    _drive(ss=ss, motion=motion)
    for w, ab, paused, nb in seen:
        assert nb == 12 and not paused
        assert ab == int((w + 15.0) / 30.0) % 12


def test_ready_holds_until_flat():
    def motion(ss, t):
        if t < 2000 or 4000 <= t < 6000:   # tilted at start and mid-countdown
            return 60.0, 0, ACT_STILL
        return 5.0, 0, ACT_STILL
    tr = []
    ss, haps = _drive(motion=motion, trace=tr)
    assert ss.result is not None
    rows = dict((r[0], r) for r in tr)
    assert rows[1500][1] == READY and rows[1500][5] == 3 and rows[1500][4] == "HOLD FLAT"
    assert rows[3000][4] == "HOLD AT CHEST" and rows[3000][5] == 3
    assert rows[5000][4] == "HOLD FLAT" and rows[5000][5] == 2   # held, not reset
    ticks = [t for t, h in haps[:3]]
    assert ticks[0] == 2500 and ticks[1] == 3500, haps
    assert ticks[2] >= 6500
    s0 = [r[0] for r in tr if r[1] == SWEEP][0]
    assert s0 >= 8000


def test_tilt_pause_and_resume():
    def motion(ss, t):
        st = _sweep_t(ss, t)
        if st is not None and 4000 <= st < 5500:
            return 50.0, 0, ACT_STILL
        return 5.0, 0, ACT_STILL
    tr = []
    ss, haps = _drive(theta=120.0, noise=2.0, seed=11, motion=motion, trace=tr)
    assert ss.result is not None and _cerr(ss.result[0], 120.0) < 10.0
    paused = [r for r in tr if r[1] == SWEEP and r[3]]
    assert paused, "never paused"
    assert all(r[4] is None and r[6] == "tilt" for r in paused)   # §6: no chip during the sweep
    ws = [r[2] for r in paused]
    assert max(ws) - min(ws) < 1e-6          # wedge holds its angle
    assert 800 <= len(paused) * 100 <= 1100  # 1.5 s tilt - 0.5 s latency; resumes when flat
    assert [h for _, h in haps].count("NOPE") == 1
    sw = [r for r in tr if r[1] == SWEEP]
    assert (sw[-1][0] - sw[0][0]) >= 12000 + 800
    assert 800 <= ss.pause_ms <= 1100, ss.pause_ms


def test_short_tilt_does_not_pause():
    def motion(ss, t):
        st = _sweep_t(ss, t)
        if st is not None and 4000 <= st < 4400:
            return 50.0, 0, ACT_STILL
        return 5.0, 0, ACT_STILL
    tr = []
    ss, haps = _drive(motion=motion, trace=tr)
    assert not any(r[3] for r in tr) and ss.result is not None
    assert "NOPE" not in [h for _, h in haps]


def test_walking_pauses_steps_and_activity():
    def motion(ss, t):
        st = _sweep_t(ss, t)
        steps = 0
        if st is not None:
            steps = min(3, max(0, (st - 3000) // 400 + 1)) if st >= 3000 else 0
        return 5.0, steps, ACT_STILL
    tr = []
    ss, haps = _drive(motion=motion, trace=tr)
    paused = [r for r in tr if r[1] == SWEEP and r[3]]
    assert paused and all(r[4] is None and r[6] == "walk" for r in paused)
    assert ss.result is not None and ss.steps == 3
    assert [h for _, h in haps].count("NOPE") == 1

    def motion2(ss, t):
        st = _sweep_t(ss, t)
        act = ACT_WALK if st is not None and 2000 <= st < 3000 else ACT_STILL
        return 5.0, 0, act
    tr = []
    ss, _ = _drive(motion=motion2, trace=tr)
    paused = [r for r in tr if r[1] == SWEEP and r[3]]
    assert 800 <= len(paused) * 100 <= 1200 and ss.result is not None


def test_fault_on_the_completing_frame_keeps_the_fix():
    def motion(ss, t):
        st = _sweep_t(ss, t)
        return 5.0, 0, ACT_WALK if st is not None and st >= 11950 else ACT_STILL
    ss, haps = _drive(motion=motion)
    assert ss.result is not None and not ss.paused
    assert haps[-1][1] == "CLOSER", haps


def test_abort_on_steps():
    def motion(ss, t):
        st = _sweep_t(ss, t)
        steps = 0 if st is None or st < 2000 else (st - 2000) // 300
        return 5.0, steps, ACT_STILL
    ss, haps = _drive(motion=motion)
    assert ss.phase == RESULT and ss.reason == scan.R_ABORT and ss.result is None
    assert ss.toast == "SCAN STOPPED" and ss.steps > 8
    assert haps[-1][1] == "NOPE"
    assert ss.done(ss.t_result)


def test_abort_on_total_pause():
    def motion(ss, t):
        st = _sweep_t(ss, t)
        if st is not None and st >= 3000:
            return 60.0, 0, ACT_STILL
        return 5.0, 0, ACT_STILL
    ss, _ = _drive(motion=motion)
    assert ss.reason == scan.R_ABORT and ss.pause_ms > 6000
    assert ss.pause_ms <= 6100


def test_no_fix_flat_and_weak_signal():
    ss, haps = _drive(model=_cos_model(0.0), noise=3.0, seed=21)
    assert ss.reason == scan.R_NO_FIX and ss.result is None
    assert ss.toast == "NO FIX, TRY AGAIN" and haps[-1][1] == "NOPE"
    ss, _ = _drive(model=_cos_model(1.5), noise=0.3, seed=22)   # 3 dB peak-to-trough
    assert ss.reason == scan.R_NO_FIX and ss.a1_db < 2.0
    ss, _ = _drive(model=_cos_model(2.5), noise=0.3, seed=23)   # 5 dB: fix
    assert ss.result is not None
    ss, _ = _drive(model=_cos_model(2.5), noise=40.0, seed=24)  # s0 > 45
    assert ss.reason == scan.R_NO_FIX


def test_partner_walking():
    base, _ = _drive(theta=60.0, noise=2.0, seed=31)

    def pw(lo, hi):
        def f(ss, t):
            st = _sweep_t(ss, t)
            return st is not None and lo <= st < hi
        return f
    ss, _ = _drive(theta=60.0, noise=2.0, seed=31, peer_walk=pw(1000, 4000))
    assert ss.result is not None
    assert abs(ss.s0_deg - base.s0_deg - 15.0) < 0.5, (ss.s0_deg, base.s0_deg)
    ss, _ = _drive(theta=60.0, noise=2.0, seed=31, peer_walk=pw(1000, 2500))
    assert abs(ss.s0_deg - base.s0_deg) < 0.5
    ss, _ = _drive(theta=60.0, noise=2.0, seed=31, peer_walk=pw(1000, 6000))
    assert ss.reason == scan.R_FRIEND_MOVED and ss.toast == "FRIEND MOVED"


def test_partner_walk_counts_only_reported_frames():
    # Game calls on_peer only while the partner's beacon is fresh: once the
    # reports stop (beacons stale) the scan stops counting on the next frame
    ss = ScanSession(0)
    ss._start_sweep(0)
    for t in range(0, 5100, 100):
        if 1000 <= t < 2000:
            ss.on_peer(t, True)            # fresh "walking" reports for 1 s ...
        ss.update(t)                       # ... then none
    assert ss.phase == SWEEP and ss.peer_walk_ms == 1000, ss.peer_walk_ms


def test_cancel_any_phase():
    ss = ScanSession(0)
    ss.on_motion(0, 5.0, 0, ACT_STILL)
    ss.update(100)
    assert ss.cancel(200)                   # tap or short press
    assert ss.phase == RESULT and ss.reason == scan.R_CANCEL
    assert ss.result is None and ss.toast is None and ss.done(200)
    assert not ss.active

    def motion(s, t):
        if s.phase == SWEEP and s.active_ms >= 3000:
            s.cancel(t)
        return 5.0, 0, ACT_STILL
    ss, haps = _drive(motion=motion)
    assert ss.reason == scan.R_CANCEL and ss.result is None
    assert "CLOSER" not in [h for _, h in haps]


def test_tap_during_result_keeps_fix():
    # A tap in the 1.2 s blink/morph after a fix must not throw the fix away.
    ss, haps = _drive()
    assert ss.phase == RESULT and ss.result is not None
    fix = ss.result
    t = ss.t_result + 300
    assert not ss.cancel(t)                 # tap or short press
    assert ss.result == fix and ss.reason is None
    assert not ss.done(t + 10)
    assert ss.done(ss.t_result + scan.BLINK_MS + scan.MORPH_MS)
    assert ss.result == fix
    # A cancelled scan stays cancelled (idempotent).
    ss = ScanSession(0)
    assert ss.cancel(100) and not ss.cancel(200)
    assert ss.reason == scan.R_CANCEL and ss.t_result == 100


def test_blanking_callback():
    win = [None]

    def blank_fn(t):
        return win[0] is not None and 0 <= ticks_diff(t, win[0]) < 1200

    def motion(ss, t):
        st = _sweep_t(ss, t)
        if st is not None and 3000 <= st < 4200:
            if win[0] is None:
                win[0] = t
            return 60.0, 5, ACT_STILL      # tilt + step burst from the motor
        return 5.0, 5 if st is not None and st >= 3000 else 0, ACT_STILL
    ss = ScanSession(0, blank_fn=blank_fn)
    tr = []
    _drive(ss=ss, motion=motion, trace=tr)
    assert not any(r[3] for r in tr) and ss.result is not None and ss.steps == 0
    assert not ScanSession(0).blanked(0)


def test_ticks_wrap():
    t0 = ticks_add(0, -6000)
    ss, haps = _drive(theta=210.0, noise=2.0, seed=7, t0=t0)
    assert ss.result is not None and _cerr(ss.result[0], 210.0) < 10.0
    assert len(haps) == 11
