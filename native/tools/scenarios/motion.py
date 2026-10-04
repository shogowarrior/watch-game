"""finder/motion.py: MotionTracker fed with synthetic wrist accelerometer
streams (sim/accel_synth.py: walk, stop-and-go, still; at 50 Hz and at the
runtime's 25 Hz) and scripted poses and gestures, plus the BMA423 chip path
(step counter and activity register, alone, alongside the software detector,
and with an outage). Every tracker output is compared after each batch of
samples. Samples are BMA423 counts at 4 g (lsb = 4/2047 g), as accel_synth
quantises them."""

import math

from finder.motion import MotionTracker, tilt_from_gravity
from sim.accel_synth import WristSim

LSB = 4 / 2047.0
BATCH = 25


def r(v):
    return "n" if v is None else repr(float(v))


def q(v):
    """g -> BMA423 count (accel_synth's WristSim._q)."""
    n = int(math.floor(v / LSB + 0.5))
    return -2048 if n < -2048 else 2047 if n > 2047 else n


class Run:
    def __init__(self):
        self.mt = None
        self.out = []
        self.batch = []

    def state(self):
        mt = self.mt
        return "%d %d %s %d %d %s %s %s %s %d %s %d %s %s %s %d" % (
            mt.steps, mt.sw_steps, r(mt.step_rate_hz), mt.activity, mt.is_still, r(mt.mag_sd_g), r(mt.gx),
            r(mt.gy), r(mt.gz), mt.face_up, r(mt.dist_m), mt.chip_live, r(mt.tilt_deg), r(mt.roll_deg),
            r(mt.pitch_deg), mt.has_gravity)

    def new(self, stride=0.7, rate=50, run_k=1.3, z_sign=1):
        self.flush()
        self.mt = MotionTracker(stride, rate, run_k, z_sign)
        self.out.append("new %s %d %s %d -> %s" % (r(stride), rate, r(run_k), z_sign, self.state()))

    def reset(self):
        self.flush()
        self.mt.reset()
        self.out.append("reset -> " + self.state())

    def sample(self, t, cx, cy, cz):
        """One sample in counts; a batch line every BATCH samples."""
        self.batch.append((t, cx, cy, cz))
        if len(self.batch) == BATCH:
            self.flush()

    def flush(self):
        if not self.batch:
            return
        steps = 0
        for t, cx, cy, cz in self.batch:
            steps += self.mt.add_sample(t, cx * LSB, cy * LSB, cz * LSB)
        self.out.append("b %d %s -> %d %s" % (len(self.batch), " ".join("%d %d %d %d" % s for s in self.batch),
                                             steps, self.state()))
        self.batch = []

    def chip(self, t, steps=None, act=None):
        self.flush()
        self.mt.set_chip(t, steps, act)
        self.out.append("c %d %s %s -> %s" % (t, "n" if steps is None else steps, "n" if act is None else act,
                                              self.state()))

    def g(self, t, x, y, z):
        self.sample(t, q(x), q(y), q(z))

    def pose(self, t, roll_deg, pitch_deg, n):
        """``n`` samples 20 ms apart of a still wrist at this roll/pitch (tests/test_motion.py _pose)."""
        rr = roll_deg * math.pi / 180.0
        p = pitch_deg * math.pi / 180.0
        for i in range(n):
            self.g(t + 20 * i, -math.sin(p), math.cos(p) * math.sin(rr), math.cos(p) * math.cos(rr))
        return t + 20 * n


def wrist(run, scenario, seed, until_s, every=1, chip=None):
    """A WristSim stream (every n-th sample); chip(s, run) may read the chip after each sample."""
    s = WristSim(scenario, seed)
    while s.step():
        if s.n % every == 1 % every:
            run.sample(s.t_ms, q(s.ax), q(s.ay), q(s.az))
            if chip is not None:
                chip(s, run)
        if s.t >= until_s:
            break


def lines():
    yield "# lsb <g per count>; new <stride> <rate_hz> <run_k> <z_sign> | reset | c <t> <steps> <act> (set_chip)"
    yield "# | b <n> (<t> <x> <y> <z> counts) x n -> <steps detected in the batch> then the state:"
    yield "#   steps sw_steps step_rate_hz activity is_still mag_sd_g gx gy gz face_up dist_m chip_live"
    yield "#   tilt_deg roll_deg pitch_deg has_gravity"
    yield "lsb " + r(LSB)
    yield "# tilt <gx> <gy> <gz> <z_sign> -> tilt_from_gravity"
    s40, c40 = math.sin(40 * math.pi / 180), math.cos(40 * math.pi / 180)
    for v in ((0.0, 0.0, 1.0, 1), (0.0, 0.0, -1.0, 1), (s40, 0.0, c40, 1), (0.0, 0.0, 1.0, -1), (0.0, 0.0, 0.0, 1),
              (0.3, -0.2, -0.9, -1), (0.0, 0.0, 2.5, 1), (1e-9, 0.0, -1e-9, 1)):
        yield "tilt %s %s %s %d -> %s" % (r(v[0]), r(v[1]), r(v[2]), v[3], r(tilt_from_gravity(*v)))
    run = Run()
    # walking at 50 Hz (tests: steps within 10 %, cadence 1.9 Hz, walk)
    run.new(0.7, 50)
    wrist(run, "walk", 1, 12.0)
    # stop-and-go at the runtime's 25 Hz: walk 20 s, still 10 s, walk again
    run.new(0.7, 25)
    wrist(run, "stopgo", 2, 34.0, every=2)
    # a still wrist: no steps, still, face-up
    run.new(0.7, 50)
    wrist(run, "still", 3, 6.0)
    # chip only (tests/test_motion.py test_chip_path): still, 20 steps 0.5 s apart, run, unknown
    run.new(0.8, 50)
    run.chip(0, 1000, 0)
    t = 0
    n = 1000
    for _ in range(20):
        t += 500
        n += 1
        run.chip(t, n, 1)
    run.chip(t + 500, None, 2)
    for k in range(3):                              # running: the run stride
        run.chip(t + 1000 + 500 * k, n + 2 * (k + 1), None)
    run.chip(t + 3000, None, 3)
    run.chip(t + 3500, 990, None)                   # counter went back (chip reset): nothing credited
    run.chip(t + 9000)                              # neither read: chip not live any more
    # chip read once a second alongside the software detector (it overrides)
    run.new(0.7, 25)
    st = {"chip": 500}

    def every_second(s, rn):
        if s.n % 50 == 1:
            st["chip"] += 2
            rn.chip(s.t_ms, st["chip"], 1)
    wrist(run, "walk", 1, 10.0, every=2, chip=every_second)
    # chip unreadable 8-16 s: the software path covers, the delta after it is not counted twice
    run.new(0.7, 25)
    st["t"] = -1000

    def outage(s, rn):
        if s.t_ms - st["t"] >= 1000:
            st["t"] = s.t_ms
            if not 8.0 <= s.t < 16.0:
                rn.chip(s.t_ms, 500 + int(s.true_steps), 1)
    wrist(run, "walk", 1, 22.0, every=2, chip=outage)
    # poses: tilt, roll, pitch, face-up hysteresis (20 deg on, 30 deg off)
    run.new()
    t = run.pose(0, 30, -20, 75)
    t = run.pose(t, 5, 5, 50)
    t = run.pose(t, 25, 0, 50)
    t = run.pose(t, 45, 0, 50)
    t = run.pose(t, 25, 0, 50)
    t = run.pose(t, 0, 10, 50)
    run.new(z_sign=-1)
    t = run.pose(0, 180, 0, 50)
    t = run.pose(t, 150, 0, 50)
    # still hysteresis: a wobble between the on and off thresholds keeps the state
    run.new()
    t = run.pose(0, 0, 0, 75)
    for i in range(50):
        run.g(t, 0.0, 0.0, 1.0 + (0.018 if i & 1 else -0.018))
        t += 20
    for i in range(10):
        run.g(t, 0.0, 0.0, 1.3 if i & 1 else 0.7)
        t += 20
    for i in range(50):
        run.g(t, 0.0, 0.0, 1.0 + (0.018 if i & 1 else -0.018))
        t += 20
    t = run.pose(t, 0, 0, 75)
    # arm flicks: short of a confirmed streak, no steps
    for _ in range(3):
        for i in range(25):
            run.g(t, 0.0, 0.0, 1.0 + 0.6 * math.sin(2 * math.pi * 2.0 * i * 0.02))
            t += 20
        t = run.pose(t, 0, 0, 75)
    # running in software: 3 steps/s, the run stride; a 0.8 s gap and a repeated stamp take dt0
    run.new(0.75, 50, 1.4)
    t = 0
    for i in range(300):
        run.g(t, 0.05, 0.0, 1.0 + 0.7 * math.sin(2 * math.pi * 3.0 * i * 0.02))
        t += 20
    t += 800
    for i in range(50):
        run.g(t, 0.05, 0.0, 1.0 + 0.7 * math.sin(2 * math.pi * 3.0 * i * 0.02))
        if i != 10:
            t += 20
    for i in range(75):                             # 5 Hz shaking: peaks under STEP_MIN_MS apart are dropped
        run.g(t, 0.05, 0.0, 1.0 + 0.9 * math.sin(2 * math.pi * 5.0 * i * 0.02))
        t += 20
    run.reset()
    t = run.pose(t, 10, 0, 30)
    run.chip(t, None, 1)                            # chip activity while the software sees a still wrist
    t = run.pose(t, 10, 0, 30)
    # chip activity before any raw sample: still from the chip
    run.new(0.7, 5)
    run.chip(100, None, 0)
    run.chip(200, 7, 1)
    t = run.pose(300, 0, 0, 20)
    run.flush()
    for line in run.out:
        yield line
