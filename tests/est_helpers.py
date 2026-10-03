"""Shared fixtures for the estimator tests (tests/test_est_*.py)."""

from finder.estimators.base import MotionInfo, ACT_STILL, ACT_WALK

STILL = MotionInfo(ACT_STILL, 0.0, 0)


def walk(t_ms, hz=1.8):
    """Walking hint whose step count advances with time."""
    return MotionInfo(ACT_WALK, hz, int(t_ms * hz / 1000.0))


def feed(e, t0, n, rssi_fn, walking=True, peer=None):
    """``n`` packets 100 ms apart from ``t0``: own RSSI ``rssi_fn(i)``, the
    partner's reported RSSI ``peer(i)`` (or None); the partner stands still."""
    for i in range(n):
        t = t0 + 100 * i
        m = walk(t) if walking else STILL
        e.update(t, rssi_fn(i), peer(i) if peer else None, m, STILL)
