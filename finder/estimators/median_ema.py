"""Median prefilter -> motion-adaptive EMA; trend from a windowed LS slope.

Own RSSI and the peer's report of us both go through a running median (kills
fast-fading dips and outliers). The EMA time constant follows the step
counters: long when neither watch is walking, short (scaled by cadence speed)
while walking. Trend is the least-squares slope of the median output over the
last few seconds, set only when significant (slope / stderr) and only while at
least one watch is walking. The IMU only gates and scales; steps are never
integrated into a position, so step-counter/stride drift cannot build up.

Ranging uses a steep path-loss exponent (shrinks log errors) and adds back a
body/fade loss that grows with the packet-to-packet jitter (rough channels
also block and fade more). The displayed log-distance has a small backlash.
"""

import math

from finder.compat import ticks_diff
from finder.estimators.base import RangeEstimator, PathLoss

MED_N = 7           # running-median length (own + peer samples)
TAU_STILL = 15.0    # EMA time constant (s) when neither watch is walking
TAU_MOVE = 2.0      # ... when walking at 1.3 m/s (scales as 1/speed)
V_MIN = 1.0         # m/s floor while stepping (cadence estimate lags ~3 s)
MAX_DT_S = 2.0      # cap on one EMA step after a packet gap
STEP_HOLD_MS = 1000  # no new step for this long -> that watch is still
STEP_INT_MS = 1000  # slower stepping than this is shuffling, not walking
STEP_RUN = 3        # steps in a streak before a watch counts as walking
WIN_MS = 6000       # LS slope window
WIN_CAP = 128       # ring capacity (>= samples in WIN_MS)
MIN_PTS = 6         # sparse packets: keep this many even if older than WIN_MS ...
MAX_AGE_MS = 15000  # ... but never older than this
Z_ON = 0.5          # |slope|/stderr to set or reverse the trend; kept until the slope flips
N_PL = 3.0          # effective path-loss exponent (open air 2.0 .. indoor 3.0; steep errs small)
BODY_DB = 2.0       # body-blocking/fade loss (dB) added back at JIT_REF jitter ...
BODY_B = 1.5        # ... plus this per dB of jitter above JIT_REF
JIT_REF = 5.0       # jitter: mean |rssi_k - rssi_k-1| of own packets, dB
JIT_INIT = 4.0      # jitter assumed before any data
JIT_A = 0.02        # jitter adaptation per packet pair
JIT_GAP_MS = 1000   # packets further apart than this don't form a pair
DEAD_LOG = 0.04     # displayed log10(distance) backlash (fewer zone flips)
SHADOW_VAR = 16.0   # dB^2 of slow shadowing no filter can remove (for dist_lo/hi)
REBASE_MS = 4000    # re-origin the LS sums this often (float32 on ESP32)
STALE_MS = 3000     # no packets for this long -> trend unsure


class _Mover:
    """Step-counter 'is walking' flag for one watch: a streak of at least STEP_RUN
    steps, each within STEP_INT_MS of the last (not shuffling round or a
    wrist-gesture blip), and the latest one under STEP_HOLD_MS ago."""

    __slots__ = ("steps", "t", "run")

    def __init__(self):
        self.steps = None
        self.t = None
        self.run = 0

    def moving(self, m, t_ms):
        if m is None:
            return False
        s = m.steps
        if s != self.steps:
            k = s - self.steps if self.steps is not None else 0
            if k > 0 and self.t is not None and ticks_diff(t_ms, self.t) < STEP_INT_MS * k:
                self.run += k
            else:
                self.run = 0
            self.t = t_ms
            self.steps = s
        return self.run >= STEP_RUN and ticks_diff(t_ms, self.t) < STEP_HOLD_MS


class Estimator(RangeEstimator):
    name = "median_ema"

    def __init__(self, path_loss=None):
        self._med = [0.0] * MED_N
        self._srt = [0.0] * MED_N
        self._wt = [0] * WIN_CAP
        self._wy = [0.0] * WIN_CAP
        self._me = _Mover()
        self._peer = _Mover()
        pl = path_loss or PathLoss(-45.0, N_PL)
        self._cal = pl.p0
        RangeEstimator.__init__(self, pl)

    def calibrate(self, rssi_at_1m):
        self._cal = rssi_at_1m
        self._ranging()

    def _ranging(self):
        """p0 from the calibration and the measured channel roughness."""
        self.pl.p0 = self._cal - BODY_DB - BODY_B * (self.jit - JIT_REF)

    def reset(self):
        RangeEstimator.reset(self)
        self._mn = 0
        self._mi = 0
        self._h = 0
        self._n = 0
        self._t0 = 0
        self._y0 = 0.0
        self._sx = self._sy = self._sxx = self._sxy = self._syy = 0.0
        self.var_f = 9.0
        self._k = 0
        self.moving = False
        self.jit = JIT_INIT
        self._lg = None
        self._raw = None
        self._raw_t = 0
        self._me.__init__()
        self._peer.__init__()

    def _median(self, y):
        """Running median: ring ``_med`` plus the same values kept sorted in ``_srt``."""
        b = self._med
        s = self._srt
        n = self._mn
        if n == MED_N:
            old = b[self._mi]
            i = 0
            while s[i] != old:
                i += 1
            while i < n - 1:
                s[i] = s[i + 1]
                i += 1
            n -= 1
        b[self._mi] = y
        self._mi = (self._mi + 1) % MED_N
        i = n
        while i > 0 and s[i - 1] > y:
            s[i] = s[i - 1]
            i -= 1
        s[i] = y
        n += 1
        self._mn = n
        if n & 1:
            return s[n >> 1]
        return 0.5 * (s[(n >> 1) - 1] + s[n >> 1])

    def _acc(self, t_ms, y, w):
        """Add (w=1) or remove (w=-1) one point from the LS sums."""
        x = ticks_diff(t_ms, self._t0) * 0.001
        y -= self._y0
        self._sx += w * x
        self._sy += w * y
        self._sxx += w * x * x
        self._sxy += w * x * y
        self._syy += w * y * y

    def _rebase(self, t_ms, y0):
        """New origin (keeps the sums small for float32); recompute from the ring."""
        self._t0 = t_ms
        self._y0 = y0
        wt = self._wt
        wy = self._wy
        sx = sy = sxx = sxy = syy = 0.0
        i = self._h
        for _ in range(self._n):
            x = ticks_diff(wt[i], t_ms) * 0.001
            y = wy[i] - y0
            sx += x
            sy += y
            sxx += x * x
            sxy += x * y
            syy += y * y
            i += 1
            if i == WIN_CAP:
                i = 0
        self._sx = sx
        self._sy = sy
        self._sxx = sxx
        self._sxy = sxy
        self._syy = syy

    def _evict(self, t_ms):
        while self._n:
            i = self._h
            age = ticks_diff(t_ms, self._wt[i])
            if not (self._n >= WIN_CAP or age > MAX_AGE_MS or (age > WIN_MS and self._n > MIN_PTS)):
                break
            self._acc(self._wt[i], self._wy[i], -1.0)
            self._h = (i + 1) % WIN_CAP
            self._n -= 1

    def _push(self, t_ms, y):
        self._evict(t_ms)
        if self._n == 0 or ticks_diff(t_ms, self._t0) > REBASE_MS:
            self._rebase(t_ms, y)
        i = (self._h + self._n) % WIN_CAP
        self._wt[i] = t_ms
        self._wy[i] = y
        self._n += 1
        self._acc(t_ms, y, 1.0)

    def _slope(self):
        n = self._n
        if n < MIN_PTS:
            return 0.0, 0.0
        sxx = self._sxx - self._sx * self._sx / n
        if sxx <= 1e-6:
            return 0.0, 0.0
        sxy = self._sxy - self._sx * self._sy / n
        syy = self._syy - self._sy * self._sy / n
        b = sxy / sxx
        r = (syy - b * sxy) / (n - 2)
        if r < 1e-6:
            r = 1e-6
        return b, b / math.sqrt(r / sxx)

    def update(self, t_ms, rssi, peer_rssi=None, my_motion=None, peer_motion=None):
        mv = self._me.moving(my_motion, t_ms)
        pv = self._peer.moving(peer_motion, t_ms)
        self.moving = mv or pv
        if rssi is None:
            if not self.moving or (self.last_t is not None and ticks_diff(t_ms, self.last_t) > STALE_MS):
                self.trend = 0
                self.trend_conf = 0.0
            return
        if self._raw is not None and ticks_diff(t_ms, self._raw_t) < JIT_GAP_MS:
            d = rssi - self._raw
            self.jit += JIT_A * ((d if d > 0 else -d) - self.jit)
        self._raw = rssi
        self._raw_t = t_ms
        y = self._median(float(rssi))
        if peer_rssi is not None:
            y = self._median(float(peer_rssi))
        self._push(t_ms, y)
        if self.rssi_f is None:
            self.rssi_f = y
            self._k = 1
            self.rssi_var = self.var_f + SHADOW_VAR
        else:
            dt = ticks_diff(t_ms, self.last_t) * 0.001
            if dt > MAX_DT_S:
                dt = MAX_DT_S
            tau = TAU_STILL
            if self.moving:
                v = 0.0
                if mv:
                    v += my_motion.speed_mps()
                if pv:
                    v += peer_motion.speed_mps()
                if v < V_MIN:
                    v = V_MIN
                tau = TAU_MOVE * 1.3 / v
            a = dt / (tau + dt)
            if self._k < 10000:
                self._k += 1
            if a * self._k < 1.0:  # warm start: running mean until the EMA takes over
                a = 1.0 / self._k
            e = y - self.rssi_f
            self.rssi_f += a * e
            self.var_f += 0.05 * (e * e - self.var_f)
            self.rssi_var = self.var_f * a / (2.0 - a) + SHADOW_VAR
        self.last_t = t_ms
        self._evict(t_ms)
        b, z = self._slope()
        self.rate_db_s = b
        tr = 0
        if self.moving and b != 0.0:
            az = z if z > 0.0 else -z
            s = 1 if b > 0.0 else -1
            if az >= Z_ON or s == self.trend:
                tr = s
            c = az / (2.0 * Z_ON)
            self.trend_conf = 1.0 if c > 1.0 else c
        else:
            self.trend_conf = 0.0
        self.trend = tr
        self._ranging()
        self._distance()

    def _distance(self):
        """Distance from rssi_f with a backlash on log10(d), so it doesn't dither."""
        k = 0.1 / self.pl.n
        lg = (self.pl.p0 - self.rssi_f) * k
        if self._lg is None:
            self._lg = lg
        elif lg > self._lg + DEAD_LOG:
            self._lg = lg - DEAD_LOG
        elif lg < self._lg - DEAD_LOG:
            self._lg = lg + DEAD_LOG
        sd = math.sqrt(self.rssi_var) * k
        self.dist_m = math.pow(10.0, self._lg)
        self.dist_lo_m = math.pow(10.0, self._lg - sd)
        self.dist_hi_m = math.pow(10.0, self._lg + sd)
