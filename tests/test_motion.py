import math
from finder.compat import ticks_add
from finder.estimators.base import ACT_STILL, ACT_WALK, ACT_RUN, ACT_UNKNOWN
from finder.motion import MotionTracker, tilt_from_gravity
from sim.accel_synth import WristSim


def _run(scenario, seed=1, until_s=None, t0=0, **kw):
    s = WristSim(scenario, seed, **kw)
    mt = MotionTracker(stride_m=0.7, rate_hz=50)
    while s.step():
        mt.add_sample(ticks_add(t0, s.t_ms), s.ax, s.ay, s.az)
        if until_s is not None and s.t >= until_s:
            break
    return s, mt


def _pose(mt, roll_deg, pitch_deg, n=100, t0=0):
    r = roll_deg * math.pi / 180.0
    p = pitch_deg * math.pi / 180.0
    x = -math.sin(p)
    y = math.cos(p) * math.sin(r)
    z = math.cos(p) * math.cos(r)
    for i in range(n):
        mt.add_sample(t0 + i * 20, x, y, z)
    return t0 + n * 20


def test_steps_walking_within_10pct():
    for seed in (1, 2, 3):
        s, mt = _run("walk", seed)
        assert abs(mt.steps - s.true_steps) <= 0.1 * s.true_steps, (mt.steps, s.true_steps)
        assert abs(mt.step_rate_hz - 1.9) < 0.19, mt.step_rate_hz
        assert mt.activity == ACT_WALK
        assert not mt.is_still
        assert abs(mt.dist_m - 0.7 * mt.steps) < 1e-6


def test_steps_stopgo_and_still_windows():
    s, mt = _run("stopgo", 2, until_s=15.0)
    assert not mt.is_still and mt.activity == ACT_WALK
    n15 = mt.steps
    s2, mt2 = _run("stopgo", 2, until_s=28.0)
    assert mt2.is_still and mt2.activity == ACT_STILL
    assert mt2.step_rate_hz == 0.0
    assert abs(mt2.steps - s2.true_steps) <= 0.1 * s2.true_steps + 2
    assert mt2.steps >= n15
    s3, mt3 = _run("stopgo", 2)
    assert abs(mt3.steps - s3.true_steps) <= 0.1 * s3.true_steps


def test_still_no_steps_face_up():
    s, mt = _run("still", 3)
    assert mt.steps == 0 and mt.dist_m == 0.0
    assert mt.is_still and mt.activity == ACT_STILL
    assert mt.face_up


def test_still_hysteresis():
    mt = MotionTracker(rate_hz=50)
    t = _pose(mt, 0, 0, 60)
    assert mt.is_still
    # small wobble (between on/off thresholds) keeps it still
    for i in range(50):
        z = 1.0 + (0.018 if i & 1 else -0.018)
        mt.add_sample(t, 0.0, 0.0, z)
        t += 20
    assert mt.is_still
    for i in range(10):
        mt.add_sample(t, 0.0, 0.0, 1.3 if i & 1 else 0.7)
        t += 20
    assert not mt.is_still
    for i in range(50):
        z = 1.0 + (0.018 if i & 1 else -0.018)
        mt.add_sample(t, 0.0, 0.0, z)
        t += 20
    assert not mt.is_still  # needs < on-threshold to re-enter
    t = _pose(mt, 0, 0, 60, t)
    assert mt.is_still


def test_tilt_and_face_up():
    mt = MotionTracker()
    _pose(mt, 30, -20, 150)
    assert abs(mt.roll_deg - 30) < 1.0, mt.roll_deg
    assert abs(mt.pitch_deg + 20) < 1.0, mt.pitch_deg
    mt = MotionTracker()
    t = _pose(mt, 5, 5, 100)
    assert mt.face_up
    t = _pose(mt, 25, 0, 100, t)
    assert mt.face_up  # hysteresis: still inside 30 deg
    t = _pose(mt, 45, 0, 100, t)
    assert not mt.face_up
    t = _pose(mt, 25, 0, 100, t)
    assert not mt.face_up  # must come back under 20 deg
    _pose(mt, 0, 10, 100, t)
    assert mt.face_up
    mt = MotionTracker(z_sign=-1)
    _pose(mt, 180, 0, 100)
    assert mt.face_up


def test_gestures_not_counted():
    mt = MotionTracker()
    t = _pose(mt, 0, 0, 50)
    # three short arm flicks, then rest: shorter than STEP_CONFIRM streak
    for k in range(3):
        for i in range(25):
            a = 0.6 * math.sin(2 * math.pi * 2.0 * i * 0.02)
            mt.add_sample(t, 0.0, 0.0, 1.0 + a)
            t += 20
        t = _pose(mt, 0, 0, 150, t)
    assert mt.steps == 0, mt.steps


def test_tick_wrap():
    s, mt = _run("walk", 1, until_s=20.0, t0=ticks_add(0, -5000))
    s2, mt2 = _run("walk", 1, until_s=20.0)
    assert mt.steps == mt2.steps and mt.steps > 0


def test_chip_path():
    mt = MotionTracker(stride_m=0.8)
    mt.set_chip(0, steps=1000, act_code=0)
    assert mt.steps == 0 and mt.activity == ACT_STILL and mt.is_still
    t = 0
    n = 1000
    for i in range(20):
        t += 500
        n += 1
        mt.set_chip(t, steps=n, act_code=1)
    assert mt.steps == 20
    assert abs(mt.dist_m - 16.0) < 1e-6
    assert mt.activity == ACT_WALK and not mt.is_still
    assert abs(mt.step_rate_hz - 2.0) < 0.3, mt.step_rate_hz
    mt.set_chip(t + 500, act_code=2)
    assert mt.activity == ACT_RUN
    mt.set_chip(t + 1000, act_code=3)
    assert mt.activity == ACT_UNKNOWN


def test_chip_overrides_software_steps():
    s = WristSim("walk", 1)
    mt = MotionTracker()
    chip = 500
    while s.step():
        mt.add_sample(s.t_ms, s.ax, s.ay, s.az)
        if s.n % 50 == 0:
            chip += 2
            mt.set_chip(s.t_ms, steps=chip, act_code=1)
        if s.t >= 20.0:
            break
    assert mt.chip_live
    assert mt.steps == chip - 502, (mt.steps, chip)
    assert mt.sw_steps > 20


def test_tilt_from_gravity():
    assert tilt_from_gravity(0.0, 0.0, 1.0) < 1e-6
    assert abs(tilt_from_gravity(0.0, 0.0, -1.0) - 180.0) < 1e-6
    s = math.sin(40 * math.pi / 180)
    c = math.cos(40 * math.pi / 180)
    assert abs(tilt_from_gravity(s, 0.0, c) - 40.0) < 1e-3
    assert abs(tilt_from_gravity(0.0, 0.0, 1.0, z_sign=-1) - 180.0) < 1e-6
    mt = MotionTracker(z_sign=-1)
    mt.gx, mt.gy, mt.gz = s, 0.0, -c       # the tracker's property uses its z_sign
    assert abs(mt.tilt_deg - 40.0) < 1e-3


def test_software_steps_cover_a_chip_outage():
    """No chip reads for > CHIP_LIVE_MS: the software detector takes over, and
    the chip delta after the outage is not counted twice."""
    s = WristSim("walk", 1)
    mt = MotionTracker(rate_hz=50)
    t_chip = -1000
    at = {}
    while s.step():
        mt.add_sample(s.t_ms, s.ax, s.ay, s.az)
        if s.t_ms - t_chip >= 1000:
            t_chip = s.t_ms
            if not 10.0 <= s.t < 20.0:                  # chip unreadable 10-20 s
                mt.set_chip(s.t_ms, steps=500 + int(s.true_steps), act_code=1)
        for k in (16.0, 19.9):
            if k not in at and s.t >= k:
                at[k] = (mt.chip_live, mt.steps)
        if s.t >= 30.0:
            break
    assert not at[16.0][0] and not at[19.9][0]
    assert at[19.9][1] > at[16.0][1]                    # software steps while the chip is out
    assert abs(mt.steps - int(s.true_steps)) <= 0.1 * s.true_steps, (mt.steps, s.true_steps)
