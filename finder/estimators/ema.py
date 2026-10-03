"""Baseline: EMA on RSSI, EMA of its derivative for rate, thresholded trend."""

from finder.compat import ticks_diff
from finder.estimators.base import RangeEstimator

ALPHA = 0.2         # RSSI smoothing per packet
BETA = 0.1          # rate smoothing per packet
TREND_DB_S = 0.25   # |rate| above this -> warmer/colder
MAX_GAP_MS = 3000   # longer gaps don't produce a derivative


class Estimator(RangeEstimator):
    name = "ema"

    def reset(self):
        RangeEstimator.reset(self)
        self.var_f = 16.0

    def update(self, t_ms, rssi, peer_rssi=None, my_motion=None, peer_motion=None):
        if rssi is None:
            return
        if self.rssi_f is None:
            self.rssi_f = float(rssi)
            self.last_t = t_ms
            self._set_distance_from_rssi()
            return
        dt = ticks_diff(t_ms, self.last_t) * 0.001
        prev = self.rssi_f
        e = rssi - prev
        self.rssi_f = prev + ALPHA * e
        self.var_f += ALPHA * (e * e - self.var_f)
        self.rssi_var = self.var_f * ALPHA / (2.0 - ALPHA)
        if 0.0 < dt <= MAX_GAP_MS * 0.001:
            d = (self.rssi_f - prev) / dt
            self.rate_db_s += BETA * (d - self.rate_db_s)
        self.last_t = t_ms
        r = self.rate_db_s
        self.trend = 1 if r > TREND_DB_S else -1 if r < -TREND_DB_S else 0
        c = abs(r) / (4.0 * TREND_DB_S)
        self.trend_conf = 1.0 if c > 1.0 else c
        self._set_distance_from_rssi()
