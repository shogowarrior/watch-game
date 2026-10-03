"""Shared interface for range estimators.

An estimator receives, at irregular times, the RSSI (dBm) this watch measured
from the partner's beacon, optionally the RSSI the partner reported measuring
from us (symmetric link), and motion hints from both BMA423s. After each
``update`` it exposes the fields listed on ``RangeEstimator``.

Conventions
  * time: integer milliseconds (monotonic; use compat.ticks_diff for deltas)
  * rssi: int/float dBm, ``None`` when no packet arrived this tick
  * trend: +1 = getting closer (warmer), -1 = farther (colder), 0 = unsure
  * distances in metres; all fields must be finite floats after first update
"""

import math

ACT_UNKNOWN = 0
ACT_STILL = 1
ACT_WALK = 2
ACT_RUN = 3


class MotionInfo:
    """Motion hint for one watch (derived from the BMA423 step counter/activity)."""

    __slots__ = ("activity", "step_rate_hz", "steps")

    def __init__(self, activity=ACT_UNKNOWN, step_rate_hz=0.0, steps=0):
        self.activity = activity
        self.step_rate_hz = step_rate_hz
        self.steps = steps

    def speed_mps(self, stride_m=0.7):
        """Upper-bound-ish walking speed from cadence."""
        return self.step_rate_hz * stride_m


class PathLoss:
    """Log-distance path-loss model: rssi = p0 - 10*n*log10(d / 1 m)."""

    def __init__(self, p0_dbm=-45.0, n=2.2):
        self.p0 = p0_dbm
        self.n = n

    def rssi_to_dist(self, rssi):
        return math.pow(10.0, (self.p0 - rssi) / (10.0 * self.n))

    def dist_to_rssi(self, d):
        if d < 0.05:
            d = 0.05
        return self.p0 - 10.0 * self.n * math.log10(d)


class RangeEstimator:
    """Base class. Subclasses override ``update`` and set the public fields."""

    name = "base"

    def __init__(self, path_loss=None):
        self.pl = path_loss or PathLoss()
        self.reset()

    def reset(self):
        self.rssi_f = None      # filtered RSSI, dBm
        self.rssi_var = 100.0   # variance of rssi_f, dB^2
        self.rate_db_s = 0.0    # d(rssi_f)/dt, + means getting closer
        self.dist_m = None      # point estimate
        self.dist_lo_m = None   # ~1-sigma lower bound
        self.dist_hi_m = None   # ~1-sigma upper bound
        self.trend = 0          # +1 warmer, -1 colder, 0 unsure
        self.trend_conf = 0.0   # 0..1
        self.last_t = None      # ms of last accepted measurement

    def calibrate(self, rssi_at_1m):
        """Bump-to-pair calibration: RSSI measured with the watches ~1 m apart."""
        self.pl.p0 = rssi_at_1m

    def set_exponent(self, n):
        """Path-loss exponent for RSSI -> metres (outdoor/indoor setting)."""
        self.pl.n = n

    def update(self, t_ms, rssi, peer_rssi=None, my_motion=None, peer_motion=None):
        raise NotImplementedError

    # helper for subclasses
    def _set_distance_from_rssi(self):
        if self.rssi_f is None:
            return
        sd = math.sqrt(self.rssi_var) if self.rssi_var > 0 else 0.0
        self.dist_m = self.pl.rssi_to_dist(self.rssi_f)
        self.dist_lo_m = self.pl.rssi_to_dist(self.rssi_f + sd)
        self.dist_hi_m = self.pl.rssi_to_dist(self.rssi_f - sd)
