"""Scalar Kalman filter on RSSI (dB) with motion-scaled process noise.

Level: random walk whose process noise comes from the step counters of both
watches (cadence x stride -> max range rate -> dB/s at the current distance),
never from integrating them, so IMU scale drift only loosens or tightens the
filter. Own and peer-reported RSSI are fused as two measurements with
asymmetric innovation clipping (deep fades are commoner than spikes).
Trend: exponentially weighted regression slope of the clipped measurements,
plus a CUSUM on level jumps (turning towards/away from the partner swaps
body blocking, a several-dB step); forced to 0 while both watches are still.
"""

import math

from finder.compat import ticks_diff
from finder.estimators.base import RangeEstimator, PathLoss, ACT_STILL, ACT_UNKNOWN

STRIDE_M = 0.7
V_MOVE = 0.45        # m/s floor for a walking watch
V_UNKNOWN = 1.0      # assumed speed when a watch sends no motion hints
STOP_MS = 1100       # no step for this long -> still
START_MS = 1500      # two steps at most this far apart -> walking
R_OWN = 25.0         # own RSSI noise, dB^2
R_PEER = 36.0        # peer-reported RSSI noise (stale by up to a period), dB^2
Q_STILL = 0.05       # dB^2/s: slow environment drift with both still
Q_SH = 1.0           # dB^2 per metre moved: shadowing decorrelation
Q_RANGE = 1.0        # scale on (range-rate in dB/s)^2
CLIP_LO = 1.5        # innovation clip, sigmas below the prediction (fades)
CLIP_HI = 2.5        # ... above it (rare spikes, or turning to face)
P_MAX = 400.0
TAU_S = 5.0          # slope regression time constant
T_ON = 0.5           # |t-stat| to leave 0 while moving
T_FLIP = 1.0         # |t-stat| against the held sign to flip it
TAU_REF_S = 3.0      # CUSUM reference level time constant
TAU_SIG_S = 10.0     # measurement noise estimate time constant
CUSUM_K = 0.5        # CUSUM drift, sigmas
CUSUM_H = 6.0        # CUSUM alarm, sigmas
USE_CUSUM = True
CUSUM_DOWN = True
MAX_GAP_MS = 4000    # longer silence -> trend_conf 0 (trend kept)
BODY_DB = 3.0        # mean extra loss vs. bump calibration (bodies in the way)
N_PL = 2.8           # path-loss exponent used for distance
PLAY = 0.15          # backlash on ln(distance): jitter below this never moves the readout
LN10_10 = 10.0 / math.log(10.0)


class _Gait:
    """Walking/still from step-counter timing: faster than the activity flag,
    which lags 2-3 s. ``speed`` is m/s from cadence, 0 when still."""

    def __init__(self):
        self.steps = None
        self.t1 = None      # time of the last step-count increase
        self.t2 = None      # ... and of the one before

    def speed(self, t_ms, m):
        if m is None:
            return V_UNKNOWN
        n = m.steps
        if self.steps is None:
            self.steps = n
        elif n != self.steps:
            self.steps = n
            self.t2 = self.t1
            self.t1 = t_ms
        if self.t1 is None:
            if m.activity == ACT_STILL or m.activity == ACT_UNKNOWN:
                return 0.0
            return V_MOVE
        if ticks_diff(t_ms, self.t1) > STOP_MS or self.t2 is None \
                or ticks_diff(self.t1, self.t2) > START_MS:
            return 0.0
        v = m.step_rate_hz * STRIDE_M
        return v if v > V_MOVE else V_MOVE


class Estimator(RangeEstimator):
    name = "kalman1d"

    def __init__(self, path_loss=None):
        # N_PL is set once here, not in reset(): Game.set_place() (MENU PLACE)
        # must survive the reset at every round end and after calibration.
        RangeEstimator.__init__(self, path_loss or PathLoss(-45.0, N_PL))

    def reset(self):
        RangeEstimator.reset(self)
        self.moving = False
        self.held = 0
        self._g1 = _Gait()
        self._g2 = _Gait()
        self._tp = None     # last predict
        self._tr = None     # last regression sample
        self._lnd = None
        self._ref = None
        self._sig2 = R_OWN
        self._cp = 0.0
        self._cn = 0.0
        self._clear()

    def _clear(self):
        self._sw = self._st = self._stt = self._sy = self._sty = self._syy = 0.0
        self._t0 = self._tp

    def calibrate(self, rssi_at_1m):
        self.pl.p0 = rssi_at_1m - BODY_DB

    def _predict(self, t_ms, my_motion, peer_motion):
        dt = ticks_diff(t_ms, self._tp) * 0.001 if self._tp is not None else 0.0
        self._tp = t_ms
        v = self._g1.speed(t_ms, my_motion) + self._g2.speed(t_ms, peer_motion)
        self.moving = v > 0.0
        if dt <= 0.0 or self.rssi_f is None:
            return
        d = self.dist_m if self.dist_m is not None and self.dist_m > 1.0 else 1.0
        g = self.pl.n * LN10_10 * v / d
        q = Q_STILL + Q_SH * v + Q_RANGE * g * g * dt
        p = self.rssi_var + q * dt
        self.rssi_var = p if p < P_MAX else P_MAX

    def _correct(self, z, r):
        """Clipped Kalman update; returns the clipped measurement."""
        x = self.rssi_f
        p = self.rssi_var
        s = p + r
        e = z - x
        lim = math.sqrt(s)
        if e < -CLIP_LO * lim:
            e = -CLIP_LO * lim
        elif e > CLIP_HI * lim:
            e = CLIP_HI * lim
        k = p / s
        self.rssi_f = x + k * e
        self.rssi_var = (1.0 - k) * p
        return x + e

    def _regress(self, t_ms, y, w):
        if self._tr is not None:
            dt = ticks_diff(t_ms, self._tr) * 0.001
            if dt > 0.0:
                f = math.exp(-dt / TAU_S)
                self._sw *= f
                self._st *= f
                self._stt *= f
                self._sy *= f
                self._sty *= f
                self._syy *= f
        self._tr = t_ms
        if self._t0 is None:
            self._t0 = t_ms
        t = ticks_diff(t_ms, self._t0) * 0.001
        if t > 60.0:  # re-centre time so the sums stay well conditioned
            sw = self._sw
            st = self._st
            self._stt += t * (t * sw - 2.0 * st)
            self._sty -= t * self._sy
            self._st = st - t * sw
            self._t0 = t_ms
            t = 0.0
        self._sw += w
        self._st += w * t
        self._stt += w * t * t
        self._sy += w * y
        self._sty += w * t * y
        self._syy += w * y * y

    def _slope(self):
        """(slope dB/s, its standard error)."""
        sw = self._sw
        if sw < 5.0:
            return 0.0, 1e9
        mt = self._st / sw
        my = self._sy / sw
        vt = self._stt / sw - mt * mt
        if vt < 0.25:
            return 0.0, 1e9
        b = (self._sty / sw - mt * my) / vt
        res = self._syy / sw - my * my - b * b * vt
        if res < 1.0:
            res = 1.0
        return b, math.sqrt(2.0 * res / (sw * vt))

    def _cusum(self, y, dt):
        """Level-jump detector; returns +1/-1 on an alarm, else 0."""
        ref = self._ref
        if ref is None:
            self._ref = y
            return 0
        e = y - ref
        a = dt / TAU_SIG_S
        self._sig2 += (a if a < 1.0 else 1.0) * (e * e - self._sig2)
        a = dt / TAU_REF_S
        self._ref = ref + (a if a < 1.0 else 1.0) * e
        z = e / math.sqrt(self._sig2 + 1.0)
        z = -3.0 if z < -3.0 else 3.0 if z > 3.0 else z
        cp = self._cp + z - CUSUM_K
        cn = self._cn - z - CUSUM_K
        self._cp = cp if cp > 0.0 else 0.0
        self._cn = cn if cn > 0.0 else 0.0
        if cp > CUSUM_H or cn > CUSUM_H:
            self._cp = self._cn = 0.0
            self._ref = self.rssi_f
            return 1 if cp > CUSUM_H else -1
        return 0

    def _still(self):
        if self.trend:
            self.held = self.trend
        self.trend = 0
        self.trend_conf = 0.0

    def _set_distance(self):
        x = self.rssi_f
        if x is None:
            return
        k = 1.0 / (LN10_10 * self.pl.n)
        ln = (self.pl.p0 - x) * k
        a = self._lnd
        if a is None:
            a = ln
        elif a < ln - PLAY:
            a = ln - PLAY
        elif a > ln + PLAY:
            a = ln + PLAY
        self._lnd = a
        sd = math.sqrt(self.rssi_var) * k
        self.dist_m = math.exp(a)
        self.dist_lo_m = math.exp(a - sd)
        self.dist_hi_m = math.exp(a + sd)

    def update(self, t_ms, rssi, peer_rssi=None, my_motion=None, peer_motion=None):
        was_moving = self.moving
        t_last = self._tr
        self._predict(t_ms, my_motion, peer_motion)
        if self.moving and not was_moving:
            self._clear()
            self.trend = self.held
        if rssi is None and peer_rssi is None:
            if not self.moving:
                self._still()
            elif self.last_t is not None and ticks_diff(t_ms, self.last_t) > MAX_GAP_MS:
                self.trend_conf = 0.0  # keep the last guess, but flag it
            self._set_distance()
            return
        if self.rssi_f is None:
            self.rssi_f = float(rssi if rssi is not None else peer_rssi)
            self.rssi_var = R_OWN
        dt = ticks_diff(t_ms, t_last) * 0.001 if t_last is not None else 0.0
        jump = 0
        if rssi is not None:
            y = self._correct(rssi, R_OWN)
            self._regress(t_ms, y, 1.0)
            jump = self._cusum(y, dt)
            dt = 0.0
        if peer_rssi is not None:
            y = self._correct(peer_rssi, R_PEER)
            self._regress(t_ms, y, R_OWN / R_PEER)
            j = self._cusum(y, dt)
            if j:
                jump = j
        self.last_t = t_ms
        b, sd = self._slope()
        self.rate_db_s = b
        if not self.moving:
            self._still()
        else:
            ts = b / sd
            tr = self.trend
            if USE_CUSUM and jump and jump != tr and (CUSUM_DOWN or jump > 0):
                tr = jump
                self._clear()
            elif tr == 0:
                if ts > T_ON:
                    tr = 1
                elif ts < -T_ON:
                    tr = -1
            elif tr * ts < -T_FLIP:
                tr = -tr
            self.trend = tr
            c = abs(ts) / 3.0
            self.trend_conf = 1.0 if c > 1.0 else c
        self._set_distance()
