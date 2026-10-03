from finder.estimators.ema import Estimator


def test_constant_rssi_distance():
    e = Estimator()
    e.calibrate(-45.0)
    r = e.pl.dist_to_rssi(12.0)
    for i in range(100):
        e.update(1000 + 100 * i, r)
    assert abs(e.dist_m - 12.0) < 0.01
    assert e.trend == 0 and e.dist_lo_m <= e.dist_m <= e.dist_hi_m


def test_trend_follows_rssi_slope():
    e = Estimator()
    for i in range(100):
        e.update(100 * i, -80.0 + 0.1 * i)  # +1 dB/s: warmer
    assert e.trend == 1 and e.rate_db_s > 0.5
    for i in range(100, 250):
        e.update(100 * i, -70.0 - 0.1 * (i - 100))
    assert e.trend == -1
