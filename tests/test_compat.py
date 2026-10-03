from finder.compat import ticks_add, ticks_diff, clamp, lerp
from finder.estimators.base import PathLoss


def test_ticks_wrap():
    a = ticks_add(0, -5)
    assert ticks_diff(0, a) == 5
    assert ticks_diff(a, 0) == -5


def test_clamp_lerp():
    assert clamp(5, 0, 3) == 3 and clamp(-1, 0, 3) == 0
    assert lerp(0, 10, 0.25) == 2.5


def test_path_loss_roundtrip():
    pl = PathLoss(-45, 2.0)
    for d in (1.0, 3.0, 10.0, 40.0):
        r = pl.dist_to_rssi(d)
        assert abs(pl.rssi_to_dist(r) - d) < 1e-6 * d + 1e-9
    assert abs(pl.dist_to_rssi(10.0) - (-65.0)) < 1e-9
