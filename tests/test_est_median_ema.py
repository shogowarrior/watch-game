import math

from finder.compat import ticks_add
from finder.estimators.base import MotionInfo, PathLoss, RangeEstimator, ACT_STILL, ACT_WALK
from finder.estimators import median_ema as M
from finder.estimators.median_ema import Estimator
from sim.rng import Rng

STILL = MotionInfo(ACT_STILL, 0.0, 100)


def walker(t_ms, hz=1.8):
    """MotionInfo of someone stepping at ``hz`` since t=0."""
    return MotionInfo(ACT_WALK, hz, 100 + int(t_ms * 0.001 * hz))


def feed(e, t0, n, rssi_at, me=None, peer=None, noise=0.0, rng=None, dt=100):
    for i in range(n):
        t = t0 + dt * i
        r = rssi_at(t)
        if noise:
            r += rng.gauss(0.0, noise)
        m = me(t) if me else STILL
        p = peer(t) if peer else STILL
        e.update(t, int(math.floor(r + 0.5)), None, m, p)
    return t0 + dt * n


def test_is_range_estimator():
    e = Estimator()
    assert isinstance(e, RangeEstimator) and e.name == "median_ema"
    e.update(0, None, None, STILL, STILL)
    assert e.dist_m is None and e.trend == 0


def expected_dist(e, rssi):
    p0 = -45.0 - M.BODY_DB - M.BODY_B * (e.jit - M.JIT_REF)
    return PathLoss(p0, M.N_PL).rssi_to_dist(rssi)


def test_constant_rssi_distance():
    e = Estimator()
    e.calibrate(-45.0)
    r = PathLoss(-45.0 - M.BODY_DB, M.N_PL).dist_to_rssi(12.0)
    for i in range(100):
        e.update(1000 + 100 * i, r, r, STILL, STILL)
    assert e.jit < 1.0  # no jitter -> little fade loss added back
    assert abs(math.log10(e.dist_m / expected_dist(e, r))) <= M.DEAD_LOG + 1e-9
    assert e.trend == 0 and e.dist_lo_m < e.dist_m < e.dist_hi_m


def test_rough_channel_reads_closer():
    """Same median RSSI, more packet-to-packet jitter -> more loss assumed -> nearer."""
    out = []
    for amp in (1, 6):
        e = Estimator()
        e.calibrate(-45.0)
        for i in range(300):
            r = -70 + (amp if i & 1 else -amp)
            e.update(100 * i, r, -70, STILL, STILL)
        assert abs(e.jit - 2 * amp) < 0.5
        out.append(e.dist_m)
    assert out[1] < 0.75 * out[0]


def test_distance_backlash():
    e = Estimator()
    for i in range(600):
        e.update(100 * i, -70 + (i % 3) - 1, None, STILL, STILL)
    d = e.dist_m
    for i in range(600, 700):
        e.update(100 * i, -70 + (i % 3) - 1, None, STILL, STILL)
        assert e.dist_m == d
    e2 = Estimator(PathLoss(-50.0, 2.0))
    e2.update(0, -70, None, STILL, STILL)
    assert e2.pl.n == 2.0 and e2._cal == -50.0


def test_median_matches_sorted():
    e = Estimator()
    rng = Rng(3)
    hist = []
    for _ in range(300):
        y = float(rng.randint(-95, -40))
        hist.append(y)
        w = sorted(hist[-M.MED_N:])
        n = len(w)
        ref = w[n // 2] if n & 1 else 0.5 * (w[n // 2 - 1] + w[n // 2])
        assert e._median(y) == ref


def test_median_kills_fades():
    e = Estimator()
    for i in range(200):
        r = -95 if i % 4 == 0 else -70  # one deep fade in four
        e.update(100 * i, r, -70, STILL, STILL)
    assert abs(e.rssi_f + 70.0) < 0.5


def test_ls_sums_match_direct():
    e = Estimator()
    rng = Rng(5)
    t = ticks_add(0, -9000)  # crosses the ticks wrap
    for i in range(400):
        t = ticks_add(t, 60 + rng.randint(0, 120))
        e._push(t, -70.0 + 0.3 * i * 0.1 + rng.gauss(0.0, 3.0))
        e._evict(t)
    b, z = e._slope()
    xs = []
    ys = []
    i = e._h
    from finder.compat import ticks_diff
    for _ in range(e._n):
        xs.append(ticks_diff(e._wt[i], t) * 0.001)
        ys.append(e._wy[i])
        i = (i + 1) % M.WIN_CAP
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) * (x - mx) for x in xs)
    assert abs(b - sxy / sxx) < 1e-6
    assert -xs[0] <= M.WIN_MS * 0.001 + 0.2


def test_trend_follows_rssi_when_walking():
    e = Estimator()
    rng = Rng(1)
    t = feed(e, 0, 100, lambda t: -80.0 + 0.001 * t, walker, None, 3.0, rng)  # +1 dB/s
    assert e.moving and e.trend == 1 and e.rate_db_s > 0.5
    t = feed(e, t, 150, lambda u: -70.0 - 0.001 * (u - t), walker, None, 3.0, rng)
    assert e.trend == -1 and e.rate_db_s < -0.5


def test_peer_walking_counts():
    e = Estimator()
    feed(e, 0, 100, lambda t: -60.0 - 0.001 * t, None, walker)
    assert e.moving and e.trend == -1


def test_no_trend_when_both_still():
    e = Estimator()
    rng = Rng(2)
    feed(e, 0, 150, lambda t: -80.0 + 0.001 * t, None, None, 3.0, rng)
    assert not e.moving and e.trend == 0 and e.rate_db_s > 0.5
    feed(e, 20000, 100, lambda t: -60.0, None, lambda t: walker(t, 0.4))  # shuffling
    assert not e.moving and e.trend == 0


def test_trend_drops_when_walking_stops():
    e = Estimator()
    t = feed(e, 0, 100, lambda t: -80.0 + 0.001 * t, walker)
    assert e.trend == 1
    last = walker(t)
    e.update(t + 1500, None, None, last, STILL)
    assert not e.moving and e.trend == 0


def test_sparse_packets_keep_points():
    e = Estimator()
    feed(e, 0, 8, lambda t: -85.0 + 0.0005 * t, walker, None, dt=1500)
    assert e._n >= M.MIN_PTS and e.trend == 1


def test_ticks_wrap():
    e = Estimator()
    t0 = ticks_add(0, -3000)
    for i in range(60):
        t = ticks_add(t0, 100 * i)
        e.update(t, -70.0 + 0.1 * i, None, walker(100 * i), STILL)
    assert e.trend == 1


def test_sim_imu_drift_does_not_matter():
    """Same trace with ideal vs drifty step counters: only gating changes."""
    import sys
    sys.path.insert(0, "tools")
    from bakeoff import record, evaluate
    out = []
    for imu in ("ideal", "drifty"):
        m = evaluate(Estimator, record("walk_away_back", "typical", 0, imu, 40.0))
        assert m["dist_log_rmse"] < 0.8 and m["trend_acc"] > 0.5
        out.append(m["trend_acc"])
    assert abs(out[0] - out[1]) < 0.15
