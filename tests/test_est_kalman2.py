from finder.compat import ticks_add
from finder.estimators.base import MotionInfo, RangeEstimator, ACT_STILL, ACT_WALK
from finder.estimators.kalman2 import Estimator

STILL = MotionInfo(ACT_STILL, 0.0, 0)


def _walk(t_ms, hz=1.8):
    """Walking hint whose step count advances with time."""
    return MotionInfo(ACT_WALK, hz, int(t_ms * hz / 1000.0))


def _feed(e, t0, n, rssi_fn, walking=True, peer=None):
    for i in range(n):
        t = t0 + 100 * i
        m = _walk(t) if walking else STILL
        e.update(t, rssi_fn(i), peer(i) if peer else None, m, STILL)


def test_interface():
    e = Estimator()
    assert isinstance(e, RangeEstimator) and e.name == "kalman2"
    e.update(0, None)
    assert e.dist_m is None and e.trend == 0
    e.update(100, -70)
    for k in ("rssi_f", "dist_m", "dist_lo_m", "dist_hi_m", "trend_conf", "rate_db_s"):
        v = getattr(e, k)
        assert v == v and v is not None
    assert e.dist_lo_m <= e.dist_m <= e.dist_hi_m


def test_still_converges_and_no_trend():
    e = Estimator()
    e.calibrate(-45.0)
    _feed(e, 0, 300, lambda i: -72 + (3 if i % 3 == 0 else -2 if i % 3 == 1 else -1), walking=False)
    assert abs(e.rssi_f - (-72.0)) < 1.5
    assert abs(e.dist_m - e.pl.rssi_to_dist(e.rssi_f)) < 0.3 * e.dist_m
    assert e.trend == 0 and abs(e.rate_db_s) < 0.05


def test_still_ignores_slow_drift():
    e = Estimator()
    _feed(e, 0, 200, lambda i: -70.0 + 0.02 * i, walking=False)
    assert e.trend == 0


def test_trend_follows_walk():
    e = Estimator()
    e.calibrate(-45.0)
    _feed(e, 0, 150, lambda i: -85.0 + 0.1 * i)
    assert e.trend == 1 and e.rate_db_s > 0.3 and e.trend_conf > 0.0
    _feed(e, 15000, 200, lambda i: -70.0 - 0.1 * i)
    assert e.trend == -1


def test_stop_clears_trend():
    e = Estimator()
    _feed(e, 0, 100, lambda i: -85.0 + 0.1 * i)
    assert e.trend == 1
    _feed(e, 10000, 30, lambda i: -75.0, walking=False)
    assert e.trend == 0


def test_stop_clears_trend_without_packets():
    e = Estimator()
    _feed(e, 0, 100, lambda i: -85.0 + 0.1 * i)
    assert e.trend == 1
    n = e._me.steps
    for i in range(30):
        e.update(10000 + 50 * i, None, None, MotionInfo(ACT_STILL, 0.0, n), None)
    assert e.trend == 0 and e.trend_conf == 0.0


def test_fade_outliers_rejected():
    e = Estimator()
    _feed(e, 0, 100, lambda i: -70.0, walking=False)
    _feed(e, 10000, 10, lambda i: -100.0 if i % 2 == 0 else -70.0, walking=False)
    assert abs(e.rssi_f + 70.0) < 2.0


def test_peer_offset_learnt():
    e = Estimator()
    _feed(e, 0, 300, lambda i: -70.0, walking=False, peer=lambda i: -64.0)
    assert abs(e.rssi_f + 70.0) < 1.0 and abs(e.peer_bias - 6.0) < 1.0


def test_stride_error_does_not_flip_trend():
    for hz in (1.2, 1.8, 2.6):
        e = Estimator()
        for i in range(150):
            t = 100 * i
            e.update(t, -85.0 + 0.1 * i, None, _walk(t, hz), STILL)
        assert e.trend == 1


def test_ticks_wrap():
    e = Estimator()
    t0 = ticks_add(0, -3000)
    for i in range(80):
        t = ticks_add(t0, 100 * i)
        e.update(t, -80.0 + 0.1 * i, None, MotionInfo(ACT_WALK, 1.8, i // 5), STILL)
    assert e.trend == 1 and e.rssi_f > -75.0


def test_sim_approach_beats_chance():
    import sys
    import finder
    f = getattr(finder, "__file__", "finder/__init__.py")
    tools = f[: f.rfind("finder/")] + "tools" if "finder/" in f else "tools"
    if tools not in sys.path:
        sys.path.append(tools)
    from bakeoff import record, evaluate
    m = evaluate(Estimator, record("approach", "typical", 0, duration=20.0))
    assert m["trend_acc"] > 0.6 and m["dist_log_rmse"] < 0.8
