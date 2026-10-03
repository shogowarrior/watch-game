from finder.estimators.base import PathLoss
from finder.estimators import particle
from finder.estimators.particle import Estimator

from tests.est_helpers import STILL, walk, feed


def _sorted(e):
    for j in range(1, e.n):
        if e.D[j] < e.D[j - 1]:
            return False
    return True


def test_particle_count_configurable():
    for n in (8, 24, 40):
        e = Estimator(n=n)
        assert len(e.D) == n and len(e.W) == n
        feed(e, 0, 120, lambda i: -75.0 + 0.05 * i + (4 if i % 5 == 0 else -1), peer=lambda i: -74)
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
        feed(e, 0, 80, lambda i: -80.0 + 0.1 * i + (3 if i % 4 == 0 else -1))
    assert a.dist_m == b.dist_m and a.trend == b.trend and a.p_closer == b.p_closer


def test_still_distance_matches_model():
    pl = PathLoss(-45.0, particle.N_PL)
    for z in (-60.0, -72.0, -84.0):
        e = Estimator(PathLoss(-45.0, particle.N_PL))
        feed(e, 0, 200, lambda i: z + (3 if i % 3 == 0 else -2 if i % 3 == 1 else -1), walking=False)
        want = pl.rssi_to_dist(z - particle.P0_ADJ)
        assert 0.7 * want < e.dist_m < 1.4 * want
        assert e.dist_lo_m < want < e.dist_hi_m * 1.2
        assert e.trend == 0


def test_set_exponent_without_reset():
    """MENU PLACE mid-round: the new exponent applies, adapted (default) or fixed n."""
    for fixed in (False, True):
        d = []
        for n in (2.6, 3.0):
            e = Estimator(PathLoss(-45.0, particle.N_PL) if fixed else None)
            e.set_exponent(n)
            feed(e, 0, 100, lambda i: -75.0, walking=False)
            d.append(e.dist_m)
        assert d[1] < 0.9 * d[0], (fixed, d)


def test_weaker_signal_is_farther():
    near = Estimator()
    far = Estimator()
    feed(near, 0, 100, lambda i: -62.0, walking=False)
    feed(far, 0, 100, lambda i: -82.0, walking=False)
    assert far.dist_m > 3.0 * near.dist_m


def test_walk_trend_is_confident():
    e = Estimator()
    e.calibrate(-45.0)
    feed(e, 0, 150, lambda i: -85.0 + 0.1 * i)
    assert e.trend == 1 and e.trend_conf > 0.5      # trend_conf is P(closer)


def test_stop_zeroes_speed():
    e = Estimator()
    feed(e, 0, 100, lambda i: -85.0 + 0.1 * i)
    feed(e, 10000, 30, lambda i: -75.0, walking=False)
    assert e.speed == 0.0


def test_fade_outliers_rejected():
    e = Estimator()
    feed(e, 0, 100, lambda i: -70.0, walking=False)
    d0 = e.dist_m
    feed(e, 10000, 10, lambda i: -100.0 if i % 2 == 0 else -70.0, walking=False)
    assert 0.7 * d0 < e.dist_m < 1.5 * d0


def test_peer_rssi_fused():
    e = Estimator()
    feed(e, 0, 200, lambda i: -70.0 + (6 if i % 2 else -6), walking=False, peer=lambda i: -70)
    f = Estimator()
    feed(f, 0, 200, lambda i: -70.0 + (6 if i % 2 else -6), walking=False)
    assert e.dist_hi_m / e.dist_lo_m <= f.dist_hi_m / f.dist_lo_m


def test_gaps_and_repeated_timestamps():
    e = Estimator()
    e.update(1000, -70, None, STILL, STILL)
    e.update(1000, -71, None, STILL, STILL)
    for i in range(20):
        e.update(1000 + 50 * i, None, None, STILL, STILL)
    e.update(60000, -90, -88, walk(60000), None)
    e.update(60100, -89, None, None, None)
    for k in ("dist_m", "dist_lo_m", "dist_hi_m"):
        v = getattr(e, k)
        assert v == v and 0.0 < v < 1e4
    assert -200.0 < e.rssi_f < 0.0


def test_sim_approach_beats_chance():
    from tools import bakeoff
    m = bakeoff.evaluate(Estimator, bakeoff.record("approach", "typical", 0, duration=20.0))
    assert m["trend_acc"] > 0.7 and m["dist_log_rmse"] < 0.8 and m["zone_flips"] < 5.0
