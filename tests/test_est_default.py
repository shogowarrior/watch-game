"""The default estimator, ``make()``, and the contract every estimator in NAMES
keeps. Estimator-specific tests live in tests/test_est_<name>.py."""

from finder import estimators
from finder.compat import ticks_add
from finder.estimators.base import (MotionInfo, PathLoss, RangeEstimator, ACT_WALK,
                                    SD_PER_STEP, JIT_INIT)
from finder.tuning import UNRELIABLE_SD_DB
from sim.rng import Rng

from tests.est_helpers import STILL, walk, feed


def test_default_is_known():
    assert estimators.DEFAULT in estimators.NAMES


def test_make_default():
    e = estimators.make()
    assert isinstance(e, RangeEstimator) and e.name == estimators.DEFAULT
    assert type(e) is estimators.cls()
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
        e.update(0, None)
        assert e.dist_m is None and e.trend == 0 and e.noise_db is None, n
        e.update(100, -60)
        for k in ("dist_m", "dist_lo_m", "dist_hi_m", "rssi_f", "rssi_var", "noise_db",
                  "rate_db_s", "trend_conf"):
            v = getattr(e, k)
            assert v is not None and v == v, (n, k)
        assert e.dist_lo_m <= e.dist_m <= e.dist_hi_m, n


def test_distance_inside_its_bounds_on_a_sim_trace():
    from tools import bakeoff
    tr = bakeoff.record("walk_away_back", "harsh", 0, duration=40.0)
    for n in estimators.NAMES:
        e = estimators.make(n)
        e.calibrate(tr.cal)
        peer = None
        for t_ms, _t, pks, mm, _d, _vr in tr.ticks:
            if not pks:
                e.update(t_ms, None, None, mm, peer)
                continue
            for p_t, rssi, prssi, pm in pks:
                if pm is not None:
                    peer = pm
                e.update(p_t, rssi, prssi, mm, peer)
                assert e.dist_lo_m <= e.dist_m <= e.dist_hi_m, (n, p_t, e.dist_lo_m, e.dist_m)


def test_set_exponent_moves_distance():
    """MENU PLACE (Game.set_place -> set_exponent): indoor n 3.0 reads nearer than 2.6."""
    for n in estimators.NAMES:
        e = estimators.make(n)
        d = []
        for x in (2.6, 3.0):
            e.set_exponent(x)
            e.calibrate(-45.0)
            e.reset()
            for i in range(100):
                e.update(1000 + 100 * i, -75, None, STILL, STILL)
            d.append(e.dist_m)
        assert d[1] < 0.9 * d[0], (n, d)


def test_noise_db_is_the_same_quantity_everywhere():
    """ui-spec 5.5: noise_db is the learnt packet-to-packet RSSI noise sd in every
    estimator, so 3 dB of fading reads reliable and 10 dB unreliable whichever runs."""
    for sd in (3.0, 10.0):
        es = [estimators.make(n) for n in estimators.NAMES]
        rng = Rng(7)
        worst = 0.0
        for i in range(600):
            t = 100 * i
            r = -80.0 + 0.02 * i + rng.gauss(0.0, sd)
            for e in es:
                e.update(t, r, None, walk(t), STILL)
            if i >= 100:
                v = [e.noise_db for e in es]
                worst = max(worst, max(v) - min(v))
        nz = es[0].noise_db
        assert worst < 1.0, (sd, worst)
        assert abs(nz - sd) < 0.25 * sd and (nz > UNRELIABLE_SD_DB) == (sd > UNRELIABLE_SD_DB), (sd, nz)


def test_noise_db_ignores_pairs_over_1s():
    """ui-spec 5.5: only own packets under 1 s apart form a noise pair."""
    for n in estimators.NAMES:
        e = estimators.make(n)
        for i in range(100):
            e.update(1500 * i, -70.0 + (10.0 if i & 1 else -10.0))
        assert abs(e.noise_db - SD_PER_STEP * JIT_INIT) < 1e-9, (n, e.noise_db)


# ---- shared contract (every name in NAMES) ---------------------------------

def test_contract_ticks_wrap():
    for n in estimators.NAMES:
        e = estimators.make(n)
        t0 = ticks_add(0, -3000)
        for i in range(80):
            t = ticks_add(t0, 100 * i)
            e.update(t, -80.0 + 0.1 * i, None, MotionInfo(ACT_WALK, 1.8, i // 5), STILL)
        assert e.trend == 1 and e.rssi_f > -75.0, (n, e.trend, e.rssi_f)


def test_contract_stride_error_does_not_flip_trend():
    for n in estimators.NAMES:
        for hz in (1.2, 1.8, 2.6):
            e = estimators.make(n)
            for i in range(150):
                t = 100 * i
                e.update(t, -85.0 + 0.1 * i, None, walk(t, hz), STILL)
            assert e.trend == 1, (n, hz)


def test_contract_still_ignores_slow_drift():
    for n in estimators.NAMES:
        e = estimators.make(n)
        feed(e, 0, 200, lambda i: -70.0 + 0.02 * i, walking=False)
        assert e.trend == 0, n


def test_contract_stop_clears_trend():
    for n in estimators.NAMES:
        e = estimators.make(n)
        feed(e, 0, 100, lambda i: -85.0 + 0.1 * i)
        assert e.trend == 1, n
        feed(e, 10000, 30, lambda i: -75.0, walking=False)
        assert e.trend == 0, n


def test_contract_trend_follows_walk():
    for n in estimators.NAMES:
        e = estimators.make(n)
        e.calibrate(-45.0)
        feed(e, 0, 150, lambda i: -85.0 + 0.1 * i)
        assert e.trend == 1 and e.rate_db_s > 0.0 and e.trend_conf > 0.0, n
        feed(e, 15000, 200, lambda i: -70.0 - 0.1 * i)
        assert e.trend == -1, n


def test_path_loss_roundtrip():
    pl = PathLoss(-45, 2.0)
    for d in (1.0, 3.0, 10.0, 40.0):
        r = pl.dist_to_rssi(d)
        assert abs(pl.rssi_to_dist(r) - d) < 1e-6 * d + 1e-9
    assert abs(pl.dist_to_rssi(10.0) - (-65.0)) < 1e-9


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
