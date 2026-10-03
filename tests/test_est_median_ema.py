import math

from finder.compat import ticks_add
from finder.estimators.base import PathLoss
from finder.estimators import median_ema as M
from finder.estimators.median_ema import Estimator
from sim.rng import Rng

from tests.est_helpers import STILL, walk


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


def test_constructor_path_loss():
    e = Estimator(PathLoss(-50.0, 2.0))
    e.update(0, -70, None, STILL, STILL)
    assert e.pl.n == 2.0 and e._cal == -50.0


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
    t = feed(e, 0, 100, lambda t: -80.0 + 0.001 * t, walk, None, 3.0, rng)  # +1 dB/s
    assert e.moving and e.trend == 1 and e.rate_db_s > 0.5
    t = feed(e, t, 150, lambda u: -70.0 - 0.001 * (u - t), walk, None, 3.0, rng)
    assert e.trend == -1 and e.rate_db_s < -0.5


def test_peer_walking_counts():
    e = Estimator()
    feed(e, 0, 100, lambda t: -60.0 - 0.001 * t, None, walk)
    assert e.moving and e.trend == -1


def test_no_trend_when_both_still():
    e = Estimator()
    rng = Rng(2)
    feed(e, 0, 150, lambda t: -80.0 + 0.001 * t, None, None, 3.0, rng)
    assert not e.moving and e.trend == 0 and e.rate_db_s > 0.5
    feed(e, 20000, 100, lambda t: -60.0, None, lambda t: walk(t, 0.4))  # shuffling
    assert not e.moving and e.trend == 0


def test_trend_drops_when_walking_stops():
    e = Estimator()
    t = feed(e, 0, 100, lambda t: -80.0 + 0.001 * t, walk)
    assert e.trend == 1
    last = walk(t)
    e.update(t + 1500, None, None, last, STILL)
    assert not e.moving and e.trend == 0


def test_sparse_packets_keep_points():
    e = Estimator()
    feed(e, 0, 8, lambda t: -85.0 + 0.0005 * t, walk, None, dt=1500)
    assert e._n >= M.MIN_PTS and e.trend == 1


def test_sim_imu_drift_does_not_matter():
    """Same trace with ideal vs drifty step counters: only gating changes."""
    from tools import bakeoff
    out = []
    for imu in ("ideal", "drifty"):
        m = bakeoff.evaluate(Estimator, bakeoff.record("walk_away_back", "typical", 0, imu, 40.0))
        assert m["dist_log_rmse"] < 0.8 and m["trend_acc"] > 0.5
        out.append(m["trend_acc"])
    assert abs(out[0] - out[1]) < 0.15
