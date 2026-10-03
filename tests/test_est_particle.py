from finder.compat import ticks_add
from finder.estimators.base import MotionInfo, PathLoss, RangeEstimator, ACT_STILL, ACT_WALK
from finder.estimators import particle
from finder.estimators.particle import Estimator

STILL = MotionInfo(ACT_STILL, 0.0, 0)


def _walk(t_ms, hz=1.8):
    """Walking hint whose step count advances with time."""
    return MotionInfo(ACT_WALK, hz, int(t_ms * hz / 1000.0))


def _feed(e, t0, n, rssi_fn, walking=True, peer=None):
    for i in range(n):
        t = t0 + 100 * i
        m = _walk(t) if walking else STILL
        e.update(t, rssi_fn(i), peer(i) if peer else None, m, STILL)


def _sorted(e):
    for j in range(1, e.n):
        if e.D[j] < e.D[j - 1]:
            return False
    return True


def test_interface():
    e = Estimator()
    assert isinstance(e, RangeEstimator) and e.name == "particle"
    e.update(0, None, None, STILL, STILL)
    assert e.dist_m is None and e.trend == 0
    e.update(100, -70)
    for k in ("rssi_f", "rssi_var", "dist_m", "dist_lo_m", "dist_hi_m", "trend_conf", "rate_db_s"):
        v = getattr(e, k)
        assert v is not None and v == v
    assert e.dist_lo_m <= e.dist_m <= e.dist_hi_m


def test_particle_count_configurable():
    for n in (8, 24, 40):
        e = Estimator(n=n)
        assert len(e.D) == n and len(e.W) == n
        _feed(e, 0, 120, lambda i: -75.0 + 0.05 * i + (4 if i % 5 == 0 else -1), peer=lambda i: -74)
        s = 0.0
        for w in e.W:
            assert w >= 0.0
            s += w
        assert abs(s - e.tot) < 1e-6 * e.tot and _sorted(e)
        assert 1.0 <= e.ess <= n + 1e-6
        assert e.dist_lo_m <= e.dist_m <= e.dist_hi_m


def test_deterministic():
    a = Estimator()
    b = Estimator()
    for e in (a, b):
        _feed(e, 0, 80, lambda i: -80.0 + 0.1 * i + (3 if i % 4 == 0 else -1))
    assert a.dist_m == b.dist_m and a.trend == b.trend and a.p_closer == b.p_closer


def test_still_distance_matches_model():
    pl = PathLoss(-45.0, particle.N_PL)
    for z in (-60.0, -72.0, -84.0):
        e = Estimator(PathLoss(-45.0, particle.N_PL))
        _feed(e, 0, 200, lambda i: z + (3 if i % 3 == 0 else -2 if i % 3 == 1 else -1), walking=False)
        want = pl.rssi_to_dist(z - particle.P0_ADJ)
        assert 0.7 * want < e.dist_m < 1.4 * want
        assert e.dist_lo_m < want < e.dist_hi_m * 1.2
        assert e.trend == 0


def test_weaker_signal_is_farther():
    near = Estimator()
    far = Estimator()
    _feed(near, 0, 100, lambda i: -62.0, walking=False)
    _feed(far, 0, 100, lambda i: -82.0, walking=False)
    assert far.dist_m > 3.0 * near.dist_m


def test_trend_follows_walk():
    e = Estimator()
    e.calibrate(-45.0)
    _feed(e, 0, 150, lambda i: -85.0 + 0.1 * i)
    assert e.trend == 1 and e.rate_db_s > 0.0 and e.trend_conf > 0.5
    _feed(e, 15000, 200, lambda i: -70.0 - 0.1 * i)
    assert e.trend == -1 and e.rate_db_s < 0.0


def test_stop_clears_trend():
    e = Estimator()
    _feed(e, 0, 100, lambda i: -85.0 + 0.1 * i)
    assert e.trend == 1
    _feed(e, 10000, 30, lambda i: -75.0, walking=False)
    assert e.trend == 0 and e.speed == 0.0


def test_fade_outliers_rejected():
    e = Estimator()
    _feed(e, 0, 100, lambda i: -70.0, walking=False)
    d0 = e.dist_m
    _feed(e, 10000, 10, lambda i: -100.0 if i % 2 == 0 else -70.0, walking=False)
    assert 0.7 * d0 < e.dist_m < 1.5 * d0


def test_peer_rssi_fused():
    e = Estimator()
    _feed(e, 0, 200, lambda i: -70.0 + (6 if i % 2 else -6), walking=False, peer=lambda i: -70)
    f = Estimator()
    _feed(f, 0, 200, lambda i: -70.0 + (6 if i % 2 else -6), walking=False)
    assert e.dist_hi_m / e.dist_lo_m <= f.dist_hi_m / f.dist_lo_m


def test_gaps_and_repeated_timestamps():
    e = Estimator()
    e.update(1000, -70, None, STILL, STILL)
    e.update(1000, -71, None, STILL, STILL)
    for i in range(20):
        e.update(1000 + 50 * i, None, None, STILL, STILL)
    e.update(60000, -90, -88, _walk(60000), None)
    e.update(60100, -89, None, None, None)
    for k in ("dist_m", "dist_lo_m", "dist_hi_m"):
        v = getattr(e, k)
        assert v == v and 0.0 < v < 1e4
    assert -200.0 < e.rssi_f < 0.0


def test_ticks_wrap():
    e = Estimator()
    t0 = ticks_add(0, -3000)
    for i in range(80):
        t = ticks_add(t0, 100 * i)
        e.update(t, -80.0 + 0.1 * i, None, MotionInfo(ACT_WALK, 1.8, i // 5), STILL)
    assert e.trend == 1


def test_sim_approach_beats_chance():
    import sys
    import finder
    f = getattr(finder, "__file__", "finder/__init__.py")
    tools = f[: f.rfind("finder/")] + "tools" if "finder/" in f else "tools"
    if tools not in sys.path:
        sys.path.append(tools)
    from bakeoff import record, evaluate
    m = evaluate(Estimator, record("approach", "typical", 0, duration=20.0))
    assert m["trend_acc"] > 0.7 and m["dist_log_rmse"] < 0.8 and m["zone_flips"] < 5.0
