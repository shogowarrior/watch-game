"""Two-state Kalman filter [rssi, rate] with IMU-bounded process noise.

The rate is a mean-reverting (Singer) state whose spread follows what the step
counters allow, |d rssi/dt| <= 10 n / ln10 * (v_me + v_peer) / d, with a hard
clamp at CAP_K times that bound. Both watches still -> the rate is pinned to 0
and the RSSI is averaged hard. The IMU is never integrated, so step-counter
drift only loosens or tightens the bound. Peer-reported RSSI is a second
measurement once its offset has been averaged over PEER_MIN_N reports (earlier
it would only echo rssi_f, and a first fade or stale value would skew the start).
Innovations are Huber-clipped, tighter on the fade side. Measurement noise is
the shared packet-to-packet noise_db (base, ui-spec 5.5) clamped to
[SIG_MIN, SIG_MAX], which also sets the body/fade bias.
"""

import math

from finder.compat import ticks_diff
from finder.estimators.base import (RangeEstimator, PathLoss, KDB, SD_PER_STEP, JIT_INIT,
                                    ACT_STILL)
from finder.tuning import PATH_LOSS_N as N_EFF   # outdoor; Game.set_place() switches

BIAS_A = -6.0       # p0 = cal - (BIAS_A + BIAS_B * fading sigma): -4.5 dB (p0 above cal) on the
BIAS_B = 1.0        # cleanest channel (sigma 1.5) to +2 dB on the roughest (sigma 8): body blocking
STRIDE_M = 0.8      # generous stride for the speed bound
V_UNKNOWN = 1.5     # m/s assumed when a watch sends no motion
STILL_HZ = 0.7      # cadence below this with activity "still" -> not walking
STEP_HOLD_MS = 1300  # no new step for this long -> still (> 1 s BMA423 counter poll + logic tick)
D_MIN = 1.5         # m, floor for the rate bound
TAU_V = 60.0        # s, rate mean reversion while moving
TAU_V_STILL = 0.3   # s, rate decay while both still
START_K = 0.5       # rate spread (units of the bound) when a walk starts
CAP_K = 1.5         # hard clamp on |rate| in units of the bound
Q_R_STILL = 0.1     # dB^2/s, slow environment drift
Q_R_MOVE = 0.15     # dB^2/s per m/s walked (shadowing decorrelation)
SIG_MIN = 1.5       # dB, clamp on the fading sigma (noise_db)
SIG_MAX = 8.0
K_UP = 2.5          # Huber clip (sigmas) for innovations above the prediction
K_DOWN = 1.0        # ... and below it (fades, blocking)
PEER_ALPHA = 0.02   # bias learning for peer-reported RSSI
PEER_MIN_N = 20     # peer reports averaged into peer_bias (vs rssi_f) before the peer RSSI is used
Z_ON = 0.05         # |rate|/sd to start a trend while moving
Z_FLIP = 0.3        # opposite-sign |rate|/sd to flip it
MAX_GAP_MS = 3000   # longer silence -> trend unsure, rate reset
SH_VAR = 9.0        # dB^2 of shadowing added to the distance spread


class _Mot:
    """Speed hint from one watch: cadence * stride, zeroed as soon as steps stop
    (whatever the activity code: an unknown one is usually a fidgeting wrist)."""

    __slots__ = ("steps", "t", "v")

    def __init__(self):
        self.steps = None
        self.t = None
        self.v = V_UNKNOWN

    def feed(self, t_ms, m):
        if m is None:
            self.v = V_UNKNOWN
            return
        if self.steps is None:
            self.steps = m.steps      # first sight: a count, not a step
        elif m.steps != self.steps:
            self.steps = m.steps
            self.t = t_ms
        hz = m.step_rate_hz
        if self.t is None or ticks_diff(t_ms, self.t) >= STEP_HOLD_MS:
            self.v = 0.0
        elif m.activity == ACT_STILL and hz < STILL_HZ:
            self.v = 0.0
        else:
            v = hz * STRIDE_M
            self.v = v if v > 0.5 else 0.5


class Estimator(RangeEstimator):
    name = "kalman2"

    def __init__(self, path_loss=None):
        pl = path_loss or PathLoss(n=N_EFF)
        self.cal = pl.p0
        RangeEstimator.__init__(self, pl)

    def reset(self):
        RangeEstimator.reset(self)
        self.bias = None
        self.p00 = 100.0
        self.p01 = 0.0
        self.p11 = 1.0
        self.sig = SD_PER_STEP * JIT_INIT   # noise_db's prior; set from it on every packet
        self.peer_bias = 0.0
        self.peer_n = 0
        self.speed = V_UNKNOWN
        self.vmax = 1.0
        self._me = _Mot()
        self._peer = _Mot()

    def calibrate(self, rssi_at_1m):
        self.cal = rssi_at_1m
        self.bias = None

    def update(self, t_ms, rssi, peer_rssi=None, my_motion=None, peer_motion=None):
        self._me.feed(t_ms, my_motion)
        if rssi is not None:
            self._peer.feed(t_ms, peer_motion)
        self.speed = self._me.v + self._peer.v
        if rssi is None:
            if self.speed <= 0.0 or (self.last_t is not None
                                     and ticks_diff(t_ms, self.last_t) > MAX_GAP_MS):
                self.trend = 0
                self.trend_conf = 0.0
            return
        self._note_noise(t_ms, rssi)
        s = self.noise_db           # fading sigma = the shared ui-spec 5.5 noise, clamped
        self.sig = SIG_MIN if s < SIG_MIN else SIG_MAX if s > SIG_MAX else s
        if self.rssi_f is None:
            self.rssi_f = float(rssi)
            self.p00 = self.sig * self.sig
            self.last_t = t_ms
            self._publish()
            return
        dt = ticks_diff(t_ms, self.last_t) * 0.001
        if dt > 0.0:
            self.last_t = t_ms
            if dt > MAX_GAP_MS * 0.001:
                self.rate_db_s = 0.0
                self.p01 = 0.0
                self.p11 = self.vmax * self.vmax + 0.01
            self._predict(dt)
        rv = self.sig * self.sig
        self._correct(rssi, rv)
        if peer_rssi is not None:
            if self.peer_n < 50:
                self.peer_n += 1
                a = 1.0 / self.peer_n
            else:
                a = PEER_ALPHA
            self.peer_bias += a * ((peer_rssi - self.rssi_f) - self.peer_bias)
            if self.peer_n >= PEER_MIN_N:
                self._correct(peer_rssi - self.peer_bias, rv)
        self._trend()
        self._publish()

    def _predict(self, dt):
        s = self.speed
        d = self.dist_m if self.dist_m is not None else 5.0
        if d < D_MIN:
            d = D_MIN
        vm = KDB * self.pl.n * s / d
        tau = TAU_V if s > 0.0 else TAU_V_STILL
        a = tau / (tau + dt)
        v = self.rate_db_s
        self.rssi_f += v * dt
        self.rate_db_s = v * a
        p11 = self.p11
        if s > 0.0 and self.vmax <= 0.0:
            p11 += START_K * START_K * vm * vm
        self.vmax = vm
        p01 = self.p01
        self.p00 += dt * (2.0 * p01 + dt * p11) + (Q_R_STILL + Q_R_MOVE * s) * dt
        self.p01 = a * (p01 + dt * p11)
        self.p11 = a * a * p11 + vm * vm * (1.0 - a * a) + 1e-4

    def _correct(self, z, rv):
        p00 = self.p00
        p01 = self.p01
        S = p00 + rv
        e = z - self.rssi_f
        lim = K_UP * K_UP * S
        if e * e > lim or (e < 0.0 and e * e > K_DOWN * K_DOWN * S):
            e = (K_UP if e > 0.0 else -K_DOWN) * math.sqrt(S)
        k0 = p00 / S
        k1 = p01 / S
        self.rssi_f += k0 * e
        v = self.rate_db_s + k1 * e
        cap = CAP_K * self.vmax
        self.rate_db_s = cap if v > cap else -cap if v < -cap else v
        self.p00 = p00 - k0 * p00
        self.p01 = p01 - k0 * p01
        p11 = self.p11 - k1 * p01
        self.p11 = p11 if p11 > 1e-6 else 1e-6

    def _trend(self):
        z = self.rate_db_s / math.sqrt(self.p11)
        tr = self.trend
        if self.speed <= 0.0:
            tr = 0
        elif tr == 0 or tr * z < -Z_FLIP:
            tr = 1 if z > Z_ON else -1 if z < -Z_ON else tr
        self.trend = tr
        c = abs(z)
        self.trend_conf = 0.0 if tr == 0 else 1.0 if c > 1.0 else c

    def _publish(self):
        b = BIAS_A + BIAS_B * self.sig
        if self.bias is None or abs(b - self.bias) > 0.5:
            self.bias = b
            self.pl.p0 = self.cal - b
        self.rssi_var = self.p00 + SH_VAR
        self._set_distance_from_rssi()   # display hysteresis is proximity's (ui-spec 5.2/5.4)
