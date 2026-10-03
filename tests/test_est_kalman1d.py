from finder.compat import ticks_add
from finder.estimators.base import MotionInfo, RangeEstimator, ACT_STILL, ACT_WALK
from finder.estimators import kalman1d
from finder.estimators.kalman1d import Estimator

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
    assert isinstance(e, RangeEstimator) and e.name == "kalman1d"
    e.update(0, None)
    assert e.dist_m is None and e.trend == 0
    e.update(100, -70)
    for k in ("rssi_f", "rssi_var", "dist_m", "dist_lo_m", "dist_hi_m", "trend_conf", "rate_db_s"):
        v = getattr(e, k)
        assert v is not None and v == v
    assert e.dist_lo_m <= e.dist_m <= e.dist_hi_m


def test_peer_only_starts_filter():
    e = Estimator()
    e.update(0, None, -66, STILL, STILL)
    assert e.rssi_f == -66.0 and e.dist_m is not None


def test_calibrate_includes_body_loss():
    e = Estimator()
    e.calibrate(-45.0)
    assert e.pl.p0 == -45.0 - kalman1d.BODY_DB
    _feed(e, 0, 200, lambda i: e.pl.dist_to_rssi(8.0), walking=False)
    assert abs(e.dist_m - 8.0) < 0.2 * 8.0


def test_still_converges_and_no_trend():
    e = Estimator()
    _feed(e, 0, 300, lambda i: -72 + (3 if i % 3 == 0 else -2 if i % 3 == 1 else -1), walking=False)
    assert abs(e.rssi_f + 72.0) < 1.5
    assert e.trend == 0 and e.trend_conf == 0.0
    assert e.rssi_var < kalman1d.R_OWN


def test_still_ignores_slow_drift():
    e = Estimator()
    _feed(e, 0, 200, lambda i: -70.0 + 0.02 * i, walking=False)
    assert e.trend == 0


def test_trend_follows_walk():
    e = Estimator()
    _feed(e, 0, 150, lambda i: -85.0 + 0.1 * i)
    assert e.trend == 1 and e.rate_db_s > 0.3 and e.trend_conf > 0.0
    _feed(e, 15000, 200, lambda i: -70.0 - 0.1 * i)
    assert e.trend == -1 and e.rate_db_s < -0.3


def test_stop_clears_then_restores_trend():
    e = Estimator()
    _feed(e, 0, 100, lambda i: -85.0 + 0.1 * i)
    assert e.trend == 1
    _feed(e, 10000, 30, lambda i: -75.0, walking=False)
    assert e.trend == 0
    e.update(13000, -75.0, None, _walk(13000), STILL)
    e.update(13600, -75.0, None, _walk(13600), STILL)
    assert e.trend == 1  # resumes the last guess until new evidence arrives


def test_step_timing_beats_activity_flag():
    e = Estimator()
    _feed(e, 0, 30, lambda i: -70.0)
    assert e.moving
    n = _walk(3000).steps
    for i in range(20):  # activity still says WALK but steps stopped
        t = 3000 + 100 * i
        e.update(t, -70.0, None, MotionInfo(ACT_WALK, 1.8, n), STILL)
    assert not e.moving


def test_fade_clipped_more_than_spike():
    lo = Estimator()
    hi = Estimator()
    _feed(lo, 0, 100, lambda i: -70.0, walking=False)
    _feed(hi, 0, 100, lambda i: -70.0, walking=False)
    _feed(lo, 10000, 5, lambda i: -100.0, walking=False)
    _feed(hi, 10000, 5, lambda i: -40.0, walking=False)
    assert abs(lo.rssi_f + 70.0) < 3.0
    assert (-70.0 - lo.rssi_f) < (hi.rssi_f + 70.0)


def test_peer_rssi_fused():
    e = Estimator()
    _feed(e, 0, 300, lambda i: -70.0, walking=False, peer=lambda i: -64.0)
    r = kalman1d.R_OWN / (kalman1d.R_OWN + kalman1d.R_PEER)
    assert abs(e.rssi_f - (-70.0 + 6.0 * r)) < 1.0


def test_motion_scales_process_noise():
    a = Estimator()
    b = Estimator()
    _feed(a, 0, 100, lambda i: -70.0, walking=False)
    _feed(b, 0, 100, lambda i: -70.0)
    assert a.rssi_var < b.rssi_var


def test_distance_backlash_holds_small_jitter():
    e = Estimator()
    _feed(e, 0, 200, lambda i: -70.0, walking=False)
    d = e.dist_m
    _feed(e, 20000, 5, lambda i: -69.0, walking=False)
    assert e.dist_m == d


def test_stride_error_does_not_flip_trend():
    for hz in (1.2, 1.8, 2.6):
        e = Estimator()
        for i in range(150):
            t = 100 * i
            e.update(t, -85.0 + 0.1 * i, None, _walk(t, hz), STILL)
        assert e.trend == 1


def test_long_gap_flags_low_confidence():
    e = Estimator()
    _feed(e, 0, 100, lambda i: -85.0 + 0.1 * i)
    assert e.trend == 1
    for i in range(60):
        t = 10000 + 100 * i
        e.update(t, None, None, _walk(t), STILL)
    assert e.trend == 1 and e.trend_conf == 0.0


def test_ticks_wrap():
    e = Estimator()
    t0 = ticks_add(0, -3000)
    for i in range(80):
        t = ticks_add(t0, 100 * i)
        e.update(t, -80.0 + 0.1 * i, None, MotionInfo(ACT_WALK, 1.8, i // 5), STILL)
    assert e.trend == 1 and e.rssi_f > -75.0


def test_regression_recentres():
    e = Estimator()
    _feed(e, 0, 900, lambda i: -85.0 + 0.02 * i)
    assert e.trend == 1 and abs(e.rate_db_s - 0.2) < 0.1


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
