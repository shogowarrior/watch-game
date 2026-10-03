"""Shared interface for range estimators.

An estimator receives, at irregular times, the RSSI (dBm) this watch measured
from the partner's beacon, optionally the RSSI the partner reported measuring
from us (symmetric link), and motion hints from both BMA423s. After each
``update`` it exposes the fields listed on ``RangeEstimator``.

Conventions
  * time: integer milliseconds (monotonic; use compat.ticks_diff for deltas)
  * rssi: int/float dBm, or ``None`` on an idle tick. The game calls ``update``
    once per received packet; tools/bakeoff.py also sends ``None`` every 50 ms.
    Correctness must not depend on idle ticks (the game's delivery and
    LINK_LOST gates handle gaps)
  * trend: +1 = getting closer (warmer), -1 = farther (colder), 0 = unsure
  * distances in metres; all fields must be finite floats after first update
"""

import math

from finder import tuning as T
from finder.compat import ticks_diff

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


KDB = 10.0 / math.log(10.0)   # dB per neper: rssi = p0 - n*KDB*ln(d)
SD_PER_STEP = math.sqrt(math.pi) / 2.0   # Gaussian sd from mean |x_k - x_(k-1)| (0.886)

JIT_INIT = 4.0      # mean |rssi step| assumed before any data, dB
JIT_A = T.NOISE_EMA_ALPHA          # its adaptation per packet pair (ui-spec 5.5)
JIT_GAP_MS = T.NOISE_PAIR_GAP_MS   # packets further apart than this don't form a pair


class PathLoss:
    """Log-distance path-loss model: rssi = p0 - 10*n*log10(d / 1 m)."""

    def __init__(self, p0_dbm=T.P1M_NOMINAL_DBM, n=T.PATH_LOSS_N):
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
        # mean |rssi_k - rssi_k-1| of own packets, dB (noise_db's source). Kept across
        # reset(): relink/SEARCHING forget the range, not the channel roughness.
        self.jit = JIT_INIT
        self.reset()

    def reset(self):
        self.rssi_f = None      # filtered RSSI, dBm
        self.rssi_var = 100.0   # variance of rssi_f, dB^2 (some add shadowing for dist_lo/hi)
        self.noise_db = None    # learnt packet-to-packet RSSI noise sd, dB (channel roughness)
        self._jz = None         # last own RSSI and its time, for the next pair
        self._jt = 0
        self.rate_db_s = 0.0    # d(rssi_f)/dt, + means getting closer
        self.dist_m = None      # point estimate
        self.dist_lo_m = None   # ~1-sigma lower bound
        self.dist_hi_m = None   # ~1-sigma upper bound
        self.trend = 0          # +1 warmer, -1 colder, 0 unsure
        self.trend_conf = 0.0   # 0..1
        self.last_t = None      # ms of last accepted measurement

    def calibrate(self, rssi_at_1m):
        """1 m calibration (PAIRING calibrate, ui-spec §5.8): RSSI measured with the watches ~1 m apart."""
        self.pl.p0 = rssi_at_1m

    def set_exponent(self, n):
        """Path-loss exponent for RSSI -> metres (outdoor/indoor setting)."""
        self.pl.n = n

    def update(self, t_ms, rssi, peer_rssi=None, my_motion=None, peer_motion=None):
        raise NotImplementedError

    # helpers for subclasses
    def _note_noise(self, t_ms, rssi):
        """Once per own packet: learn ``noise_db`` (ui-spec 5.5), the same way in
        every estimator so the unreliable gate does not depend on which one runs."""
        z = self._jz
        if z is not None and ticks_diff(t_ms, self._jt) < JIT_GAP_MS:
            d = rssi - z
            self.jit += JIT_A * ((d if d > 0 else -d) - self.jit)
        self._jz = rssi
        self._jt = t_ms
        self.noise_db = SD_PER_STEP * self.jit

    def _set_distance_from_rssi(self):
        if self.rssi_f is None:
            return
        sd = math.sqrt(self.rssi_var) if self.rssi_var > 0 else 0.0
        self.dist_m = self.pl.rssi_to_dist(self.rssi_f)
        self.dist_lo_m = self.pl.rssi_to_dist(self.rssi_f + sd)
        self.dist_hi_m = self.pl.rssi_to_dist(self.rssi_f - sd)
