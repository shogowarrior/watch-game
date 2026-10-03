"""Lightweight wrist motion tracker for the BMA423 (accelerometer only).

Feed raw samples (``add_sample``, g units, 25-50 Hz) and/or the on-chip step
counter + activity register (``set_chip``). Exposes steps, cadence, activity,
a still flag, gravity/tilt/face-up and stride odometry; the game copies them
for the range estimators with ``session.MotionSnap.from_tracker``.

No position from double integration, on purpose: a residual accel bias b
(BMA423 offset: tens of mg, 1 mg/K) grows as 0.5*b*t^2 (40 mg -> ~20 m after
10 s, ~700 m after 60 s), every tilt error leaks gravity in, and without a
gyro/compass yaw is unobservable, so no filter can recover direction. ZUPT
only resets velocity while truly still. Steps x stride is bounded (a few %),
so that is what we use -- see docs/estimation/imu-drift.md.
"""

import math
from finder.compat import ticks_diff
from finder.estimators.base import ACT_UNKNOWN, ACT_STILL, ACT_WALK, ACT_RUN

# BMA423 ACTIVITY_TYPE (reg 0x27) codes 0..3 -> ACT_*
CHIP_ACT = (ACT_STILL, ACT_WALK, ACT_RUN, ACT_UNKNOWN)

STILL_ON_G = 0.012     # sd of |a| over the window to enter "still"
STILL_OFF_G = 0.025    # ... and to leave it
FACE_ON_COS = math.cos(20.0 * math.pi / 180.0)
FACE_OFF_COS = math.cos(30.0 * math.pi / 180.0)
TAU_G_S = 0.5          # gravity low-pass
TAU_BP_FAST_S = 0.053  # step band-pass: ~3 Hz low-pass ...
TAU_BP_SLOW_S = 0.25   # ... minus ~0.6 Hz low-pass
STEP_MIN_G = 0.05
STEP_MIN_MS = 250      # max 4 steps/s
STEP_MAX_MS = 2000     # longer gap ends a walking streak
STEP_CONFIRM = 4       # steps in a regular streak before any are credited
CHIP_LIVE_MS = 5000
RUN_HZ = 2.5
WALK_HZ = 0.5
_R2D = 180.0 / math.pi


def tilt_from_gravity(gx, gy, gz, z_sign=1):
    """Angle (deg) between the display normal and up; 0 = face-up flat."""
    n = math.sqrt(gx * gx + gy * gy + gz * gz)
    if n <= 0.0:
        return 180.0
    c = gz * (1.0 if z_sign >= 0 else -1.0) / n
    c = -1.0 if c < -1.0 else 1.0 if c > 1.0 else c
    return math.acos(c) * _R2D


class MotionTracker:
    """Per-watch motion state; all per-sample work is O(1) and allocation-free
    apart from float temporaries."""

    def __init__(self, stride_m=0.7, rate_hz=50, run_stride_k=1.3, z_sign=1):
        self.stride_m = stride_m
        self.run_stride_k = run_stride_k
        self.z_sign = 1.0 if z_sign >= 0 else -1.0
        self._dt0 = 1.0 / rate_hz
        n = int(rate_hz)
        self._buf = [0.0] * (n if n > 8 else 8)
        self.reset()

    def reset(self):
        self.steps = 0
        self.sw_steps = 0          # software detector total (credited steps)
        self.step_rate_hz = 0.0
        self.activity = ACT_UNKNOWN
        self.is_still = False
        self.mag_sd_g = 0.0
        self.gx = 0.0
        self.gy = 0.0
        self.gz = 1.0
        self.face_up = False
        self.dist_m = 0.0
        self.chip_live = False
        self._t = None
        self._i = 0
        self._k = 0
        self._s = 0.0
        self._ss = 0.0
        self._lpf = 0.0
        self._lps = 0.0
        self._armed = True
        self._pk = 0.0
        self._pk_t = 0
        self._amp = 0.15
        self._last_step_t = None
        self._streak = 0
        self._chip_steps = None
        self._chip_act = None
        self._chip_t = None
        self._sw_out = 0           # steps the software path credited since the last chip step read
        self._cad_n = 0
        self._cad_t0 = None
        self._iv_hz = 0.0
        self._have_raw = False

    @property
    def tilt_deg(self):
        """Display tilt from face-up flat (deg), from the gravity estimate."""
        return tilt_from_gravity(self.gx, self.gy, self.gz, self.z_sign)

    @property
    def roll_deg(self):
        return math.atan2(self.gy, self.gz * self.z_sign) * _R2D

    @property
    def pitch_deg(self):
        return math.atan2(-self.gx, math.sqrt(self.gy * self.gy + self.gz * self.gz)) * _R2D

    # ---- raw accelerometer path -------------------------------------------------
    def add_sample(self, t_ms, x, y, z):
        """One accelerometer sample in g. Returns True if a step was detected."""
        if self._t is None:
            dt = self._dt0
            self.gx, self.gy, self.gz = x, y, z
            m0 = math.sqrt(x * x + y * y + z * z)
            self._lpf = self._lps = m0
        else:
            dt = ticks_diff(t_ms, self._t) * 0.001
            if dt <= 0.0 or dt > 0.5:
                dt = self._dt0
        self._t = t_ms
        self._have_raw = True
        m = math.sqrt(x * x + y * y + z * z)

        # 1 s window sd of |a| (running sums of |a|-1 g for float32, re-summed per wrap)
        buf = self._buf
        n = len(buf)
        i = self._i
        old = buf[i]
        d = m - 1.0
        buf[i] = d
        i += 1
        if i == n:
            i = 0
        self._i = i
        if self._k < n:
            self._k += 1
            old = 0.0
        self._s += d - old
        self._ss += d * d - old * old
        if i == 0:
            s = 0.0
            ss = 0.0
            for v in buf:
                s += v
                ss += v * v
            self._s = s
            self._ss = ss
        k = self._k
        mu = self._s / k
        var = self._ss / k - mu * mu
        sd = math.sqrt(var) if var > 0.0 else 0.0
        self.mag_sd_g = sd
        if self.is_still:
            if sd > STILL_OFF_G:
                self.is_still = False
        elif k == n and sd < STILL_ON_G:
            self.is_still = True

        # gravity + tilt + face-up
        a = dt / (TAU_G_S + dt)
        gx = self.gx + a * (x - self.gx)
        gy = self.gy + a * (y - self.gy)
        gz = self.gz + a * (z - self.gz)
        self.gx = gx
        self.gy = gy
        self.gz = gz
        gn = math.sqrt(gx * gx + gy * gy + gz * gz)
        if gn > 0.0:
            c = gz * self.z_sign / gn
            self.face_up = c > (FACE_OFF_COS if self.face_up else FACE_ON_COS)

        # software step detector: band-pass |a|, peak with hysteresis
        self._lpf += dt / (TAU_BP_FAST_S + dt) * (m - self._lpf)
        self._lps += dt / (TAU_BP_SLOW_S + dt) * (m - self._lps)
        bp = self._lpf - self._lps
        self._amp -= self._amp * dt * 0.25  # forget old step strength (tau 4 s)
        thr = 0.4 * self._amp
        if thr < STEP_MIN_G:
            thr = STEP_MIN_G
        stepped = False
        if self._armed:
            if bp > self._pk:
                self._pk = bp
                self._pk_t = t_ms
            elif self._pk > thr and bp < 0.5 * self._pk:
                self._amp += 0.25 * (self._pk - self._amp)
                self._armed = False
                stepped = self._sw_step(self._pk_t)
        elif bp < -0.3 * thr:
            self._armed = True
            self._pk = 0.0

        self._update_rate(t_ms)
        self._update_activity(t_ms)
        return stepped

    def _sw_step(self, t):
        last = self._last_step_t
        d = STEP_MAX_MS + 1 if last is None else ticks_diff(t, last)
        if d < STEP_MIN_MS:
            return False
        self._last_step_t = t
        if d > STEP_MAX_MS:
            self._streak = 1
            return True
        self._streak += 1
        k = STEP_CONFIRM if self._streak == STEP_CONFIRM else 1 if self._streak > STEP_CONFIRM else 0
        if k:
            self.sw_steps += k
            if not self.chip_live:
                self._add_steps(k)
                self._sw_out += k
        if not self.chip_live:
            f = 1000.0 / d
            self._iv_hz = f if self._streak == 2 else self._iv_hz + 0.3 * (f - self._iv_hz)
        return True

    # ---- on-chip path -------------------------------------------------------------
    def set_chip(self, t_ms, steps=None, act_code=None):
        """Feed BMA423 STEP_COUNTER (cumulative) and/or ACTIVITY_TYPE (0..3)."""
        if steps is not None:
            last = self._chip_steps
            self._chip_steps = steps
            if last is not None and steps > last:
                d = steps - last
                self._cad_n += d
                d -= self._sw_out          # counted by the software path while the chip was out
                if d > 0:
                    self._add_steps(d)
            self._sw_out = 0
            self._chip_t = t_ms
        if act_code is not None:
            self._chip_act = CHIP_ACT[act_code & 3]
            self._chip_t = t_ms
        self.chip_live = True
        self._update_rate(t_ms)
        self._update_activity(t_ms)

    # ---- shared -------------------------------------------------------------------
    def _add_steps(self, k):
        self.steps += k
        s = self.stride_m * (self.run_stride_k if self.activity == ACT_RUN else 1.0)
        self.dist_m += k * s

    def _update_rate(self, t):
        if not self.chip_live:
            last = self._last_step_t
            if last is None or ticks_diff(t, last) > STEP_MAX_MS:
                self._streak = 0
            self.step_rate_hz = self._iv_hz if self._streak >= STEP_CONFIRM else 0.0
            return
        t0 = self._cad_t0
        if t0 is None:
            self._cad_t0 = t
            return
        el = ticks_diff(t, t0)
        if el >= 2000:
            inst = self._cad_n * 1000.0 / el
            self.step_rate_hz += 0.5 * (inst - self.step_rate_hz)
            if self.step_rate_hz < 0.05:
                self.step_rate_hz = 0.0
            self._cad_n = 0
            self._cad_t0 = t

    def _update_activity(self, t):
        ct = self._chip_t
        self.chip_live = ct is not None and ticks_diff(t, ct) < CHIP_LIVE_MS
        if not self._have_raw and self._chip_act is not None:
            self.is_still = self._chip_act == ACT_STILL
        if self._have_raw and self.is_still:
            act = ACT_STILL
        elif self.chip_live and self._chip_act is not None:
            act = self._chip_act
        elif self.step_rate_hz > RUN_HZ:
            act = ACT_RUN
        elif self.step_rate_hz > WALK_HZ:
            act = ACT_WALK
        else:
            act = ACT_UNKNOWN
        self.activity = act
