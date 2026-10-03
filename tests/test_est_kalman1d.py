from finder.estimators.base import MotionInfo, ACT_WALK
from finder.estimators import kalman1d
from finder.estimators.kalman1d import Estimator

from tests.est_helpers import STILL, walk, feed


def test_peer_only_starts_filter():
    e = Estimator()
    e.update(0, None, -66, STILL, STILL)
    assert e.rssi_f == -66.0 and e.dist_m is not None


def test_calibrate_includes_body_loss():
    e = Estimator()
    e.calibrate(-45.0)
    assert e.pl.p0 == -45.0 - kalman1d.BODY_DB
    feed(e, 0, 200, lambda i: e.pl.dist_to_rssi(8.0), walking=False)
    assert abs(e.dist_m - 8.0) < 0.2 * 8.0


def test_still_converges_and_no_trend():
    e = Estimator()
    feed(e, 0, 300, lambda i: -72 + (3 if i % 3 == 0 else -2 if i % 3 == 1 else -1), walking=False)
    assert abs(e.rssi_f + 72.0) < 1.5
    assert e.trend == 0 and e.trend_conf == 0.0
    assert e.rssi_var < kalman1d.R_OWN


def test_rate_follows_walk():
    e = Estimator()
    feed(e, 0, 150, lambda i: -85.0 + 0.1 * i)
    assert e.rate_db_s > 0.3
    feed(e, 15000, 200, lambda i: -70.0 - 0.1 * i)
    assert e.rate_db_s < -0.3


def test_stop_clears_then_restores_trend():
    e = Estimator()
    feed(e, 0, 100, lambda i: -85.0 + 0.1 * i)
    assert e.trend == 1
    feed(e, 10000, 30, lambda i: -75.0, walking=False)
    assert e.trend == 0
    e.update(13000, -75.0, None, walk(13000), STILL)
    e.update(13600, -75.0, None, walk(13600), STILL)
    assert e.trend == 1  # resumes the last guess until new evidence arrives


def test_step_timing_beats_activity_flag():
    e = Estimator()
    feed(e, 0, 30, lambda i: -70.0)
    assert e.moving
    n = walk(3000).steps
    for i in range(20):  # activity still says WALK but steps stopped
        t = 3000 + 100 * i
        e.update(t, -70.0, None, MotionInfo(ACT_WALK, 1.8, n), STILL)
    assert not e.moving


def test_fade_clipped_more_than_spike():
    lo = Estimator()
    hi = Estimator()
    feed(lo, 0, 100, lambda i: -70.0, walking=False)
    feed(hi, 0, 100, lambda i: -70.0, walking=False)
    feed(lo, 10000, 5, lambda i: -100.0, walking=False)
    feed(hi, 10000, 5, lambda i: -40.0, walking=False)
    assert abs(lo.rssi_f + 70.0) < 3.0
    assert (-70.0 - lo.rssi_f) < (hi.rssi_f + 70.0)


def test_peer_rssi_fused():
    e = Estimator()
    feed(e, 0, 300, lambda i: -70.0, walking=False, peer=lambda i: -64.0)
    r = kalman1d.R_OWN / (kalman1d.R_OWN + kalman1d.R_PEER)
    assert abs(e.rssi_f - (-70.0 + 6.0 * r)) < 1.0


def test_motion_scales_process_noise():
    a = Estimator()
    b = Estimator()
    feed(a, 0, 100, lambda i: -70.0, walking=False)
    feed(b, 0, 100, lambda i: -70.0)
    assert a.rssi_var < b.rssi_var


def test_distance_backlash_holds_small_jitter():
    e = Estimator()
    feed(e, 0, 200, lambda i: -70.0, walking=False)
    d = e.dist_m
    feed(e, 20000, 5, lambda i: -69.0, walking=False)
    assert e.dist_m == d


def test_long_gap_flags_low_confidence():
    e = Estimator()
    feed(e, 0, 100, lambda i: -85.0 + 0.1 * i)
    assert e.trend == 1
    for i in range(60):
        t = 10000 + 100 * i
        e.update(t, None, None, walk(t), STILL)
    assert e.trend == 1 and e.trend_conf == 0.0


def test_regression_recentres():
    e = Estimator()
    feed(e, 0, 900, lambda i: -85.0 + 0.02 * i)
    assert e.trend == 1 and abs(e.rate_db_s - 0.2) < 0.1


def test_sim_approach_beats_chance():
    from tools import bakeoff
    m = bakeoff.evaluate(Estimator, bakeoff.record("approach", "typical", 0, duration=20.0))
    assert m["trend_acc"] > 0.6 and m["dist_log_rmse"] < 0.8
