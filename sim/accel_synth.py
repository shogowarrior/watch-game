"""Synthetic 3-axis wrist accelerometer (BMA423-like) for the IMU drift study.

World frame: x forward (walking direction), y left, z up. The walker moves
along +x; the watch sits on a swinging forearm (pendulum about the shoulder),
the body bounces/surges at the step frequency, the wrist tilt wanders slowly
and yaw wobbles with the swing. The sensor reports specific force
f = a_wrist - g_vec rotated into the watch frame, in g, with BMA423 errors:

  * noise 0.7 mg rms/axis at ODR 50 Hz (datasheet: 140 ug/sqrtHz, table 13)
  * zero-g offset ~N(0, 40 mg)/axis (datasheet typ 80 mg), tempco +-1 mg/K
    with the watch warming ~6 K on the wrist (tau 4 min)
  * sensitivity error ~1 %, tempco 0.02 %/K, cross-axis ~1 % (max 2 %)
  * 12-bit quantisation and clipping at +-range_g, like bma423.get_xyz()
"""

import math
from sim.rng import Rng

G = 9.80665
ODR_HZ = 50
NOISE_G = 0.0007
OFFSET_SD_G = 0.040
TCO_G_K = 0.001
TCS_K = 0.0002
SENS_SD = 0.01
CROSS_SD = 0.01
WARM_K = 6.0
WARM_TAU_S = 240.0

SPEED = 1.3        # m/s
CADENCE_HZ = 1.9   # steps/s
ARM_L = 0.65       # shoulder -> wrist, m
ARM_SWING = 0.30   # rad amplitude
BOUNCE = 0.025     # m, vertical body bounce
SURGE = 0.015      # m, fore-aft body surge
SWAY = 0.010       # m, lateral sway at stride frequency
YAW_WOBBLE = 0.09  # rad
RAMP_S = 1.0

SCENARIOS = ("still", "walk", "stopgo")
_D2R = math.pi / 180.0


def schedule(name):
    """List of (duration_s, walking) phases for a named 60 s scenario."""
    if name == "still":
        return [(60.0, False)]
    if name == "walk":
        return [(60.0, True)]
    if name == "stopgo":
        return [(20.0, True), (10.0, False), (20.0, True), (10.0, False)]
    raise ValueError(name)


def _smooth(u):
    """Smoothstep value, 1st and 2nd derivative wrt u."""
    return u * u * (3.0 - 2.0 * u), 6.0 * u * (1.0 - u), 6.0 - 12.0 * u


def _osc(a, w, ph, t, e, e1, e2):
    """f = a*e(t)*sin(w t + ph): value, f', f''."""
    s = math.sin(w * t + ph)
    c = math.cos(w * t + ph)
    return (a * e * s, a * (e1 * s + e * w * c),
            a * (e2 * s + 2.0 * e1 * w * c - e * w * w * s))


class WristSim:
    """Steps a scenario at ODR; read ``t_ms, ax, ay, az`` (g) and truth fields."""

    def __init__(self, scenario="walk", seed=1, range_g=4):
        self.phases = schedule(scenario)
        self.duration = sum(p[0] for p in self.phases)
        rng = Rng(seed)
        self.rng = rng.fork(1)
        r = rng.fork(2)
        self.bias = [r.gauss(0.0, OFFSET_SD_G) for _ in range(3)]
        self.tco = [r.uniform(-TCO_G_K, TCO_G_K) for _ in range(3)]
        self.sens = [1.0 + r.gauss(0.0, SENS_SD) for _ in range(3)]
        self.tcs = [r.uniform(-TCS_K, TCS_K) for _ in range(3)]
        self.cross = [r.gauss(0.0, CROSS_SD) for _ in range(6)]
        self.ph = [r.uniform(0.0, 6.2832) for _ in range(8)]
        self.range_g = range_g
        self.lsb = range_g / 2047.0
        self.dt = 1.0 / ODR_HZ
        self.w = 2.0 * math.pi * CADENCE_HZ
        pose = (8.0, -12.0) if scenario == "still" else (75.0, 0.0)
        self.roll0 = pose[0] * _D2R
        self.pitch0 = pose[1] * _D2R
        starts = []
        x = 0.0
        t = 0.0
        prev = 0.0
        for dur, walk in self.phases:
            tgt = 1.0 if walk else 0.0
            starts.append((t, x, prev, tgt))
            x += SPEED * self._int_e(dur, prev, tgt)
            t += dur
            prev = tgt
        self._starts = starts
        self.n = 0
        self.t = 0.0
        self.t_ms = 0
        self.ax = self.ay = self.az = 0.0
        self.true_x = self.true_y = 0.0
        self.true_steps = 0.0

    @staticmethod
    def _int_e(tau, prev, tgt):
        d = tgt - prev
        if tau < RAMP_S:
            u = tau / RAMP_S
            return prev * tau + d * RAMP_S * (u * u * u - 0.5 * u * u * u * u)
        return prev * RAMP_S + d * RAMP_S * 0.5 + tgt * (tau - RAMP_S)

    def _envelope(self, t):
        st = self._starts
        i = len(st) - 1
        while i > 0 and st[i][0] > t:
            i -= 1
        t0, x0, prev, tgt = st[i]
        tau = t - t0
        d = tgt - prev
        if tau < RAMP_S:
            s, s1, s2 = _smooth(tau / RAMP_S)
            e, e1, e2 = prev + d * s, d * s1 / RAMP_S, d * s2 / (RAMP_S * RAMP_S)
        else:
            e, e1, e2 = tgt, 0.0, 0.0
        return e, e1, e2, x0 + SPEED * self._int_e(tau, prev, tgt)

    def step(self):
        """Advance one sample; returns False past the end of the scenario."""
        t = self.n * self.dt
        if t > self.duration + 1e-9:
            return False
        self.t = t
        self.t_ms = int(t * 1000.0 + 0.5)
        ph = self.ph
        e, e1, e2, x = self._envelope(t)
        w = self.w
        wa = 0.5 * w
        # body
        ax = SPEED * e1
        _, _, sx = _osc(SURGE, w, ph[0], t, e, e1, e2)
        _, _, sy = _osc(SWAY, wa, ph[1], t, e, e1, e2)
        _, _, sz = _osc(BOUNCE, w, ph[0] + 1.3, t, e, e1, e2)
        # forearm pendulum
        th, thd, thdd = _osc(ARM_SWING, wa, ph[2], t, e, e1, e2)
        c = math.cos(th)
        s = math.sin(th)
        ax += sx + ARM_L * (c * thdd - s * thd * thd)
        ay = sy
        az = sz + ARM_L * (s * thdd + c * thd * thd)
        # tremor + postural sway (always present)
        ax += 0.03 * math.sin(2 * math.pi * 9.0 * t + ph[3]) + 0.02 * math.sin(2 * math.pi * 0.3 * t + ph[4])
        ay += 0.02 * math.sin(2 * math.pi * 11.0 * t + ph[5])
        az += 0.02 * math.sin(2 * math.pi * 10.0 * t + ph[6])
        # attitude: slow wander + swing (pitch about lateral axis) + yaw wobble
        roll = self.roll0 + 4.0 * _D2R * math.sin(2 * math.pi * t / 17.0 + ph[7])
        pitch = self.pitch0 + 4.0 * _D2R * math.sin(2 * math.pi * t / 23.0 + ph[4]) + th
        yaw = YAW_WOBBLE * e * math.sin(wa * t + ph[2] + 0.7)
        fx, fy, fz = ax, ay, az + G
        cy, sy_ = math.cos(yaw), math.sin(yaw)
        x1 = cy * fx + sy_ * fy
        y1 = -sy_ * fx + cy * fy
        cp, sp = math.cos(pitch), math.sin(pitch)
        x2 = cp * x1 - sp * fz
        z2 = sp * x1 + cp * fz
        cr, sr = math.cos(roll), math.sin(roll)
        y3 = cr * y1 + sr * z2
        z3 = -sr * y1 + cr * z2
        self.ax, self.ay, self.az = self._sense(x2 / G, y3 / G, z3 / G, t)
        self.true_x = x
        self.true_y = 0.0
        self.true_steps += e * w / (2.0 * math.pi) * self.dt if self.n else 0.0
        self.n += 1
        return True

    def _sense(self, gx, gy, gz, t):
        dT = WARM_K * (1.0 - math.exp(-t / WARM_TAU_S))
        c = self.cross
        sn = self.sens
        tc = self.tcs
        o = self.bias
        k = self.tco
        r = self.rng
        vx = sn[0] * (1.0 + tc[0] * dT) * gx + c[0] * gy + c[1] * gz + o[0] + k[0] * dT + r.gauss(0.0, NOISE_G)
        vy = sn[1] * (1.0 + tc[1] * dT) * gy + c[2] * gx + c[3] * gz + o[1] + k[1] * dT + r.gauss(0.0, NOISE_G)
        vz = sn[2] * (1.0 + tc[2] * dT) * gz + c[4] * gx + c[5] * gy + o[2] + k[2] * dT + r.gauss(0.0, NOISE_G)
        return self._q(vx), self._q(vy), self._q(vz)

    def _q(self, v):
        lsb = self.lsb
        n = int(math.floor(v / lsb + 0.5))
        if n > 2047:
            n = 2047
        elif n < -2048:
            n = -2048
        return n * lsb
