"""IMU drift study: why double-integrating the BMA423 does not give position.

    python3 tools/drift_demo.py [--seeds 5] [--range 4] [--bench]
    node tools/mpy/run.mjs tools/drift_demo.py --seeds 2

Synthesises 50 Hz wrist accelerometer data (sim/accel_synth.py) for three
60 s scenarios and prints the mean |position error| (m) after 10/30/60 s for:
  (a)  naive double integration exactly like the old notebook
       (g * 0.981 as "m/s^2", 0.1 dead-band, all three axes)
  (a') same with correct units (g * 9.81)
  (b)  gravity removal via low-pass tilt + double integration (yaw given!)
  (c)  (b) + ZUPT: velocity reset while MotionTracker says still
  (e)  linear Kalman filter per axis, state [v, accel bias], ZUPT updates
  (d)  PDR: MotionTracker steps x stride -- distance only, no direction
(b), (c), (e) are told the true yaw (impossible on the watch: no gyro/compass),
so they are optimistic. Errors are horizontal; (a) includes z; (d) is
|distance - true path length|.
"""

import math
import sys


def _root():
    f = globals().get("__file__", "tools/drift_demo.py")
    i = f.rfind("/")
    d = f[:i] if i >= 0 else "."
    j = d.rfind("/")
    return d[:j] if j >= 0 else "."


_R = _root()
if _R not in sys.path:
    sys.path.insert(0, _R)

from finder.compat import ticks_ms, ticks_diff  # noqa: E402
from finder.motion import MotionTracker  # noqa: E402
from sim.accel_synth import WristSim, SCENARIOS, G, OFFSET_SD_G  # noqa: E402
from tools.cli import parse_args  # noqa: E402

CHECK_S = (10, 30, 60)


class Naive:
    """(a) notebook: per-axis dead-band then v += a dt, p += v dt."""

    def __init__(self, scale=0.981, dead=0.1):
        self.scale = scale
        self.dead = dead
        self.v = [0.0, 0.0, 0.0]
        self.p = [0.0, 0.0, 0.0]

    def update(self, dt, x, y, z, still):
        v = self.v
        p = self.p
        for i, a in ((0, x), (1, y), (2, z)):
            a *= self.scale
            if abs(a) < self.dead:
                a = 0.0
            v[i] += a * dt
            p[i] += v[i] * dt

    def err(self, s):
        p = self.p
        return math.sqrt((p[0] - s.true_x) ** 2 + (p[1] - s.true_y) ** 2 + p[2] ** 2)


class Tilt:
    """(b)/(c) low-pass gravity -> roll/pitch -> level frame, minus 1 g."""

    def __init__(self, tau=1.0, zupt=False):
        self.tau = tau
        self.zupt = zupt
        self.g = None
        self.vx = self.vy = self.px = self.py = 0.0

    def level(self, dt, x, y, z):
        g = self.g
        if g is None:
            g = self.g = [x, y, z]
        a = dt / (self.tau + dt)
        g[0] += a * (x - g[0])
        g[1] += a * (y - g[1])
        g[2] += a * (z - g[2])
        roll = math.atan2(g[1], g[2])
        pitch = math.atan2(-g[0], math.sqrt(g[1] * g[1] + g[2] * g[2]))
        cr, sr = math.cos(roll), math.sin(roll)
        cp, sp = math.cos(pitch), math.sin(pitch)
        y1 = cr * y - sr * z
        z1 = sr * y + cr * z
        return (cp * x + sp * z1) * G, y1 * G

    def update(self, dt, x, y, z, still):
        ax, ay = self.level(dt, x, y, z)
        if self.zupt and still:
            self.vx = self.vy = 0.0
        else:
            self.vx += ax * dt
            self.vy += ay * dt
        self.px += self.vx * dt
        self.py += self.vy * dt

    def err(self, s):
        return math.sqrt((self.px - s.true_x) ** 2 + (self.py - s.true_y) ** 2)


class _Axis:
    __slots__ = ("v", "b", "p00", "p01", "p11", "pos")

    def __init__(self):
        self.v = self.b = self.pos = 0.0
        self.p00 = 0.01
        self.p01 = 0.0
        self.p11 = 0.02 ** 2


class Kf(Tilt):
    """(e) per-axis KF, x = [v, b]; v' = a - b; ZUPT: z = 0 = v when still."""

    def __init__(self, tau=1.0, sig_a=2.0, sig_b=0.0005, r_zupt=0.02):
        Tilt.__init__(self, tau)
        self.qa = sig_a * sig_a
        self.qb = sig_b * sig_b
        self.r = r_zupt * r_zupt
        self.ax = (_Axis(), _Axis())

    def update(self, dt, x, y, z, still):
        acc = self.level(dt, x, y, z)
        for k in (0, 1):
            s = self.ax[k]
            s.v += (acc[k] - s.b) * dt
            p00 = s.p00 - 2.0 * dt * s.p01 + dt * dt * s.p11 + self.qa * dt * dt
            p01 = s.p01 - dt * s.p11
            s.p11 += self.qb * dt
            s.p00 = p00
            s.p01 = p01
            if still:
                S = s.p00 + self.r
                k0 = s.p00 / S
                k1 = s.p01 / S
                y_ = -s.v
                s.v += k0 * y_
                s.b += k1 * y_
                s.p11 -= k1 * s.p01
                s.p01 *= 1.0 - k0
                s.p00 *= 1.0 - k0
            s.pos += s.v * dt

    def err(self, s):
        a = self.ax
        return math.sqrt((a[0].pos - s.true_x) ** 2 + (a[1].pos - s.true_y) ** 2)


class Pdr:
    """(d) steps x stride from the MotionTracker."""

    def __init__(self, mt):
        self.mt = mt

    def update(self, dt, x, y, z, still):
        pass

    def err(self, s):
        return abs(self.mt.dist_m - s.true_x)


ROWS = (
    ("(a) naive, as notebook (x0.981, dead-band)", lambda mt: Naive()),
    ("(a') naive, correct units (x9.81)", lambda mt: Naive(G, 0.1)),
    ("(b) LP-tilt gravity removal + 2x integrate", lambda mt: Tilt()),
    ("(c) (b) + ZUPT velocity reset", lambda mt: Tilt(zupt=True)),
    ("(e) KF [v, bias] + ZUPT pseudo-meas.", lambda mt: Kf()),
    ("(d) PDR: steps x 0.7 m (distance only)", lambda mt: Pdr(mt)),
)


def run_one(scenario, seed, range_g):
    s = WristSim(scenario, seed, range_g=range_g)
    mt = MotionTracker(stride_m=0.7, rate_hz=50)
    ms = [mk(mt) for _, mk in ROWS]
    errs = [[0.0] * len(CHECK_S) for _ in ROWS]
    dt = s.dt
    ci = 0
    while s.step():
        mt.add_sample(s.t_ms, s.ax, s.ay, s.az)
        if s.n == 1:
            continue
        for m in ms:
            m.update(dt, s.ax, s.ay, s.az, mt.is_still)
        if ci < len(CHECK_S) and s.t_ms >= CHECK_S[ci] * 1000:
            for j, m in enumerate(ms):
                errs[j][ci] = m.err(s)
            ci += 1
    return errs, mt.steps, s.true_steps, s.true_x


def fmt(v):
    if v >= 1000.0:
        return "%.0f" % v
    if v >= 10.0:
        return "%.1f" % v
    return "%.2f" % v


def bench(n=1500):
    s = WristSim("walk", 1)
    xs = []
    while s.step() and len(xs) < n:
        xs.append((s.t_ms, s.ax, s.ay, s.az))
    mt = MotionTracker()
    t0 = ticks_ms()
    for t, x, y, z in xs:
        mt.add_sample(t, x, y, z)
    el = ticks_diff(ticks_ms(), t0)
    return el * 1000.0 / len(xs)


def main(args):
    o = parse_args(args, {"seeds": "5", "range": "4", "bench": False}, ("bench",))
    seeds = int(o["seeds"])
    range_g = int(o["range"])
    nc = len(CHECK_S)
    tot = {}
    steps = {}
    for sc in SCENARIOS:
        acc = [[0.0] * nc for _ in ROWS]
        st = [0, 0.0, 0.0]
        for seed in range(1, seeds + 1):
            e, n, tn, tx = run_one(sc, seed, range_g)
            for j in range(len(ROWS)):
                for c in range(nc):
                    acc[j][c] += e[j][c] / seeds
            st[0] += n
            st[1] += tn
            st[2] = tx
        tot[sc] = acc
        steps[sc] = st
    head = "| method |"
    rule = "|---|"
    for sc in SCENARIOS:
        for c in CHECK_S:
            head += " %s %ds |" % (sc, c)
            rule += "---:|"
    print("Mean |error| in metres over %d seeds, BMA423 +-%dg @ 50 Hz, bias sd %d mg/axis." % (
        seeds, range_g, int(OFFSET_SD_G * 1000 + 0.5)))
    print("")
    print(head)
    print(rule)
    for b_mg in (40, 5):
        b = b_mg * 0.001 * G
        line = "| theory 0.5*b*t^2, b=%d mg |" % b_mg
        for sc in SCENARIOS:
            for c in CHECK_S:
                line += " %s |" % fmt(0.5 * b * c * c)
        print(line)
    for j, (name, _) in enumerate(ROWS):
        line = "| %s |" % name
        for sc in SCENARIOS:
            for c in range(nc):
                line += " %s |" % fmt(tot[sc][j][c])
        print(line)
    print("")
    for sc in SCENARIOS:
        n, tn, tx = steps[sc]
        print("%s: detected %.1f steps vs %.1f true per run (%+.1f%%), true path %.1f m" % (
            sc, n / seeds, tn / seeds, 100.0 * (n - tn) / tn if tn > 0 else 0.0, tx))
    if o["bench"]:
        print("")
        print("MotionTracker.add_sample: %.1f us/sample on %s" % (bench(), sys.implementation.name))


if __name__ == "__main__":
    main(sys.argv[1:])
