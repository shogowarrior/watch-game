from finder import estimators
from finder.estimators.base import MotionInfo, PathLoss, RangeEstimator, ACT_STILL

STILL = MotionInfo(ACT_STILL, 0.0, 0)


def test_default_is_known():
    assert estimators.DEFAULT in estimators.NAMES


def test_make_default():
    e = estimators.make()
    assert isinstance(e, RangeEstimator) and e.name == estimators.DEFAULT
    e.calibrate(-45)
    for i in range(20):
        e.update(1000 + 100 * i, -65, -66, STILL, STILL)
    assert e.dist_m is not None and 0.5 < e.dist_m < 100.0
    assert e.dist_lo_m <= e.dist_m <= e.dist_hi_m
    assert e.trend == 0


def test_make_every_name():
    for n in estimators.NAMES:
        e = estimators.make(n)
        assert isinstance(e, RangeEstimator) and e.name == n
        e.update(0, -60, None, STILL, STILL)
        assert e.dist_m is not None and e.dist_m == e.dist_m


def test_make_passes_kwargs_and_fresh_instances():
    pl = PathLoss(-50.0, 2.0)
    e = estimators.make(estimators.DEFAULT, path_loss=pl)
    assert e.pl is pl
    assert estimators.make() is not estimators.make()
    assert estimators.make("particle", n=8).n == 8


def test_make_rejects_bad_names():
    for bad in ("base", "_x", "base.math"):
        try:
            estimators.make(bad)
        except ValueError:
            continue
        assert False, bad
    try:
        estimators.make("no_such_estimator")
    except ImportError:
        return
    assert False, "unknown estimator imported"
