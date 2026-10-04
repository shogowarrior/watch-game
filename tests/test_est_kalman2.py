from finder.estimators.base import MotionInfo, ACT_STILL, ACT_WALK, ACT_UNKNOWN
from finder.estimators import kalman2
from finder.estimators.kalman2 import Estimator
from finder.tuning import TREND_CONF_MIN

from tests.est_helpers import STILL, walk, feed


def test_still_converges_and_no_trend():
    e = Estimator()
    e.calibrate(-45.0)
    feed(e, 0, 300, lambda i: -72 + (3 if i % 3 == 0 else -2 if i % 3 == 1 else -1), walking=False)
    assert abs(e.rssi_f - (-72.0)) < 1.5
    assert abs(e.dist_m - e.pl.rssi_to_dist(e.rssi_f)) < 1e-6 * e.dist_m   # no hidden deadband
    assert e.trend == 0 and abs(e.rate_db_s) < 0.05


def test_rate_pinned_to_zero_while_both_still():
    """Both watches still: the speed bound is 0, so the rate is held at 0 even
    while the RSSI keeps drifting (it is averaged, not extrapolated)."""
    e = Estimator()
    e.calibrate(-45.0)
    feed(e, 0, 100, lambda i: -85.0 + 0.1 * i)               # walking closer, +1 dB/s
    assert e.rate_db_s > 0.3
    feed(e, 10000, 100, lambda i: -75.0 + 0.1 * i, walking=False)
    assert e.speed == 0.0 and e.vmax == 0.0 and e.rate_db_s == 0.0
    assert e.p11 < 1e-3, e.p11                                # rate spread decays (TAU_V_STILL)


def test_rate_never_exceeds_speed_bound():
    """|rate| <= CAP_K x the IMU bound (10 n / ln10 x speed / max(d, D_MIN))."""
    e = Estimator()
    e.calibrate(-45.0)
    for i in range(80):
        t = 100 * i
        e.update(t, -55.0 - 0.3 * i, None, walk(t, 1.2), STILL)   # -3 dB/s, faster than walking allows
        if i:
            assert abs(e.rate_db_s) <= kalman2.CAP_K * e.vmax + 1e-9, (i, e.rate_db_s, e.vmax)
    assert e.rate_db_s < 0.0 and -e.rate_db_s >= 0.99 * kalman2.CAP_K * e.vmax   # the clamp binds


def test_motion_hint_speed():
    m = kalman2._Mot()
    for i in range(10):                  # "still" with slow step creep: not walking
        m.feed(1000 * i, MotionInfo(ACT_STILL, 0.5, i))
    assert m.v == 0.0
    m = kalman2._Mot()
    for i in range(10):                  # unknown activity, slow steps: 0.5 m/s floor
        m.feed(1000 * i, MotionInfo(ACT_UNKNOWN, 0.3, i))
    assert m.v == 0.5
    m.feed(10000, MotionInfo(ACT_WALK, 1.8, 11))
    assert abs(m.v - 1.8 * kalman2.STRIDE_M) < 1e-9


def test_walk_start_kicks_rate_spread():
    # Checked against START_K itself rather than by patching it: the C++ port
    # replays this test's trace, and a patched module constant can't follow.
    e = Estimator()
    feed(e, 0, 50, lambda i: -70.0, walking=False)
    assert e.p11 < 0.01, e.p11                         # both still: the rate is pinned
    e.update(5000, -70.0, None, walk(5000), STILL)   # first walking packet
    kick = (kalman2.START_K * e.vmax) ** 2
    assert kick > 0.1 and e.p11 > 0.5 * kick, (e.p11, kick)


def test_stop_clears_trend_without_packets():
    e = Estimator()
    feed(e, 0, 100, lambda i: -85.0 + 0.1 * i)
    assert e.trend == 1
    n = e._me.steps
    for i in range(30):
        e.update(10000 + 50 * i, None, None, MotionInfo(ACT_STILL, 0.0, n), None)
    assert e.trend == 0 and e.trend_conf == 0.0


def test_calibrate_scales_distance():
    out = []
    for cal in (-45.0, -50.0):
        e = Estimator()
        e.calibrate(cal)
        feed(e, 0, 300, lambda i: -72.0, walking=False)
        out.append(e.dist_m)
    assert abs(out[0] / out[1] - 10.0 ** (5.0 / (10.0 * e.pl.n))) < 0.05, out


def test_jitter_sets_sigma_and_rough_channel_reads_closer():
    """sig is the clamped noise_db and sets the body/fade bias
    p0 = cal - (BIAS_A + BIAS_B * sig): a rough channel reads closer."""
    out = []
    for amp in (0.0, 8.0):
        e = Estimator()
        e.calibrate(-45.0)
        feed(e, 0, 400, lambda i: -70.0 + (amp if i & 1 else -amp), walking=False)
        b = kalman2.BIAS_A + kalman2.BIAS_B * e.sig
        assert abs(e.pl.p0 - (-45.0 - b)) <= 0.5, (amp, e.sig, e.pl.p0)   # _publish hysteresis
        out.append((e.sig, e.dist_m))
    assert out[0][0] == kalman2.SIG_MIN and out[1][0] == kalman2.SIG_MAX, out
    assert out[1][1] < out[0][1], out


def test_long_gap_drops_trend_and_resets_rate():
    e = Estimator()
    feed(e, 0, 100, lambda i: -85.0 + 0.1 * i)
    assert e.trend == 1 and e.rate_db_s > 0.3
    t = 9900
    for _ in range(40):                     # 4 s without packets, still walking
        t += 100
        e.update(t, None, None, walk(t), STILL)
    assert e.trend == 0 and e.trend_conf == 0.0
    t += 100
    e.update(t, e.rssi_f, None, walk(t), STILL)
    assert abs(e.rate_db_s) < 0.1, e.rate_db_s


def test_chip_rate_steps_keep_walking():
    """BMA423 feature-engine steps are seen ~1.1 s apart (1 s poll + logic tick): still walking."""
    me = MotionInfo(ACT_WALK, 1.8, 0)
    e = Estimator()
    e.calibrate(-45.0)
    for i in range(300):
        if i % 11 == 0:
            me.steps = i * 18 // 100
        e.update(10000 + 100 * i, -90.0 + 0.04 * i + (2.0 if i % 3 == 0 else -1.0), None, me, STILL)
        if i >= 50:
            assert e.speed > 0.0, i
    assert e.trend == 1 and e.trend_conf >= TREND_CONF_MIN


def test_unknown_activity_without_steps_is_still():
    """A fidgeting wrist (activity unknown, step count unchanged) does not loosen the filter."""
    m = MotionInfo(ACT_UNKNOWN, 0.0, 5)
    e = Estimator()
    e.update(0, -72.0, None, m, m)
    assert e.speed == 0.0                   # the first step count is a baseline, not a step
    for i in range(1, 300):
        e.update(100 * i, -72.0 + (3.0 if i % 2 else -3.0), None, m, m)
    assert e.speed == 0.0 and e.trend == 0 and abs(e.rate_db_s) < 0.05


def test_fade_clipped_more_than_spike():
    moves = []
    for jump in (-30.0, 30.0):
        e = Estimator()
        feed(e, 0, 100, lambda i: -70.0, walking=False)
        e.update(10000, -70.0 + jump, None, STILL, STILL)
        moves.append(abs(e.rssi_f + 70.0))
    assert moves[0] < 0.6 * moves[1], moves


def test_fade_outliers_rejected():
    e = Estimator()
    feed(e, 0, 100, lambda i: -70.0, walking=False)
    feed(e, 10000, 10, lambda i: -100.0 if i % 2 == 0 else -70.0, walking=False)
    assert abs(e.rssi_f + 70.0) < 2.0


def test_peer_offset_learnt():
    e = Estimator()
    feed(e, 0, 300, lambda i: -70.0, walking=False, peer=lambda i: -64.0)
    assert abs(e.rssi_f + 70.0) < 1.0 and abs(e.peer_bias - 6.0) < 1.0


def test_first_peer_reports_only_learn_offset():
    """peer_bias is learnt against rssi_f, so the first peer reports after a reset
    (a fade, or a stale value from before a relink) only teach it: rssi_f matches
    an own-only filter for the first 2 s (PEER_MIN_N reports) and stays close after."""
    assert kalman2.PEER_MIN_N >= 20
    own = lambda i: -70.0 + (3.0 if i & 1 else -3.0)
    a = Estimator()
    b = Estimator()
    feed(a, 0, 20, own, walking=False)
    feed(b, 0, 20, own, walking=False, peer=lambda i: -95.0 if i < 2 else -68.0)
    assert a.rssi_f == b.rssi_f and a.p00 == b.p00, (a.rssi_f, b.rssi_f)
    feed(a, 2000, 30, own, walking=False)
    feed(b, 2000, 30, own, walking=False, peer=lambda i: -68.0)
    assert abs(b.rssi_f - a.rssi_f) < 1.0, (a.rssi_f, b.rssi_f)   # was 2.6 dB off


def test_sim_approach_beats_chance():
    from tools import bakeoff
    m = bakeoff.evaluate(Estimator, bakeoff.record("approach", "typical", 0, duration=20.0))
    assert m["trend_acc"] > 0.6 and m["dist_log_rmse"] < 0.8
