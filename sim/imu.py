"""BMA423-like motion hints (step counter + activity) derived from walker motion.

Error sources ("IMU drift") the estimators must tolerate:
  * per-person stride length (+-stride_err) and step-count gain (+-count_err):
    a constant scale error on cadence -> speed and on steps -> odometry
  * slow random walk of that scale (pace/terrain/fatigue), ``drift`` per sqrt(min)
  * missed steps when shuffling slowly, phantom step bursts from arm gestures
  * activity classification latency (2-3 s) and 3 s cadence smoothing
"""

import math
from finder.estimators.base import MotionInfo, ACT_STILL, ACT_WALK, ACT_RUN

IMU_NAMES = ["ideal", "typical", "drifty"]

IMU_PROFILES = {
    "ideal": dict(stride_err=0.0, count_err=0.0, drift=0.0, phantom_hz=0.0,
                  miss_slow=0.0, lat_lo=2.0, lat_hi=2.0),
    "typical": dict(stride_err=0.10, count_err=0.05, drift=0.03, phantom_hz=0.02,
                    miss_slow=0.3, lat_lo=2.0, lat_hi=3.0),
    "drifty": dict(stride_err=0.15, count_err=0.08, drift=0.08, phantom_hz=0.06,
                   miss_slow=0.5, lat_lo=2.5, lat_hi=3.5),
}

STRIDE_M = 0.7
RATE_WIN_S = 3.0
RATE_SAMPLE_S = 0.5
V_STILL = 0.25
V_RUN = 2.3


def stride_at(stride, v):
    """People lengthen their stride as they speed up."""
    f = math.pow(v / 1.3, 0.4) if v > 0.0 else 0.7
    if f < 0.7:
        f = 0.7
    elif f > 1.5:
        f = 1.5
    return stride * f


class Imu:
    """Motion hints for one walker; ``step(dt)`` after ``world.step``; read ``info``."""

    def __init__(self, walker, rng, prof="typical", stride_m=STRIDE_M):
        p = IMU_PROFILES[prof] if isinstance(prof, str) else prof
        self.p = p
        self.w = walker
        self.rng = rng
        self.stride = stride_m * (1.0 + rng.uniform(-p["stride_err"], p["stride_err"]))
        self.gain = 1.0 + rng.uniform(-p["count_err"], p["count_err"])
        self.latency = rng.uniform(p["lat_lo"], p["lat_hi"])
        self.scale = 1.0
        self.steps = 0
        self._frac = 0.0
        self._phantom = 0.0
        self.activity = ACT_STILL
        self._cand = ACT_STILL
        self._cand_t = 0.0
        self._hist = [0] * (int(RATE_WIN_S / RATE_SAMPLE_S) + 1)
        self._hi = 0
        self._hn = 0
        self._since = RATE_SAMPLE_S
        self.step_rate_hz = 0.0
        self.cadence = 0.0  # true cadence, Hz (ground truth for analysis)
        self.info = MotionInfo(ACT_STILL, 0.0, 0)

    def step(self, dt):
        p = self.p
        rng = self.rng
        w = self.w
        v = w.v
        if v > 5.0:
            v = 5.0
        if v >= V_STILL:
            cad = v / stride_at(self.stride, v)
        else:
            cad = 0.0
            tr = abs(w.turn) / dt if dt > 0 else 0.0
            if tr > 0.1:  # shuffling round on the spot
                cad = min(1.5, 1.5 * tr)
        self.cadence = cad
        if p["drift"] > 0.0:
            s = self.scale + rng.gauss(0.0, p["drift"] * math.sqrt(dt / 60.0))
            self.scale = 0.8 if s < 0.8 else 1.2 if s > 1.2 else s
        g = self.gain * self.scale
        if cad < 1.2:
            g *= 1.0 - p["miss_slow"] * (1.2 - cad) / 1.2
        inc = cad * dt * g
        if cad == 0.0 and p["phantom_hz"] > 0.0 and rng.random() < p["phantom_hz"] * dt:
            self._phantom += rng.randint(2, 6)
        if self._phantom > 0.0:
            q = 2.0 * dt
            if q > self._phantom:
                q = self._phantom
            self._phantom -= q
            inc += q
        self._frac += inc
        k = int(self._frac)
        if k:
            self._frac -= k
            self.steps += k
        self._since += dt
        if self._since >= RATE_SAMPLE_S - 1e-9:
            self._since = 0.0
            h = self._hist
            m = len(h)
            h[self._hi] = self.steps
            self._hi = (self._hi + 1) % m
            if self._hn < m:
                self._hn += 1
            if self._hn > 1:
                oldest = h[self._hi] if self._hn == m else h[0]
                self.step_rate_hz = (self.steps - oldest) / ((self._hn - 1) * RATE_SAMPLE_S)
        raw = ACT_STILL if (v < V_STILL and cad < 0.8) else ACT_RUN if v > V_RUN else ACT_WALK
        if raw == self.activity:
            self._cand = raw
            self._cand_t = 0.0
        else:
            if raw != self._cand:
                self._cand = raw
                self._cand_t = 0.0
            self._cand_t += dt
            if self._cand_t >= self.latency:
                self.activity = raw
                self._cand_t = 0.0
        self.info = MotionInfo(self.activity, self.step_rate_hz, self.steps)
        return self.info
