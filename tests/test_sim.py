import math

from sim import Sim
from sim.rng import Rng
from sim.world import World, Walker, Obstacle, PI
from sim.radio import Radio, profile, body_loss
from sim.imu import Imu
from sim.scenarios import make, NAMES
from finder.estimators.base import ACT_STILL, ACT_WALK


def test_rng_known_sequence():
    r = Rng(42)
    assert [r.u32() for _ in range(4)] == [2247996655, 1600775871, 343464800, 3985588650]
    r = Rng(0)
    assert [r.u32() for _ in range(2)] == [931894489, 3875691633]


def test_rng_repeatable_and_forks():
    a = Rng(7)
    b = Rng(7)
    assert [a.gauss() for _ in range(5)] == [b.gauss() for _ in range(5)]
    assert Rng(8).u32() != Rng(7).u32()
    f1 = Rng(7).fork(3)
    f2 = Rng(7).fork(3)
    assert f1.u32() == f2.u32() and Rng(7).fork(4).u32() != Rng(7).fork(3).u32()


def test_rng_distributions():
    r = Rng(1)
    n = 4000
    xs = [r.gauss(2.0, 3.0) for _ in range(n)]
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / n)
    assert abs(m - 2.0) < 0.2 and abs(sd - 3.0) < 0.2
    us = [r.uniform(-1.0, 1.0) for _ in range(n)]
    assert min(us) >= -1.0 and max(us) < 1.0 and abs(sum(us) / n) < 0.05
    ks = [r.randint(1, 3) for _ in range(300)]
    assert min(ks) == 1 and max(ks) == 3
    assert r.choice((5, 6)) in (5, 6)


def test_bearing_signs():
    a = Walker(0.0, 0.0, 0.0)
    b = Walker(10.0, 0.0, PI)
    w = World(a, b)
    assert abs(w.bearing_ab()) < 1e-9 and abs(w.bearing_ba()) < 1e-9
    b.set_pose(0.0, 10.0)
    assert abs(w.bearing_ab() - PI / 2) < 1e-9
    b.set_pose(-10.0, 0.0)
    assert abs(abs(w.bearing_ab()) - PI) < 1e-9


def test_radial_speed_signs():
    a = Walker(0.0, 0.0, 0.0, 1.0)
    b = Walker(20.0, 0.0, PI)
    w = World(a, b)
    a.walk_to(b)
    for _ in range(20):
        w.step(0.05)
    assert abs(w.radial_speed + 1.0) < 1e-6
    a.set_pose()
    a.walk_heading(PI)
    for _ in range(80):
        w.step(0.05)
    assert abs(w.radial_speed - 1.0) < 1e-6
    b.set_pose(b.x + 0.5)  # external drag
    w.step(0.05)
    assert w.radial_speed > 5.0


def test_orbit_keeps_distance():
    b = Walker(0.0, 0.0)
    a = Walker(20.0, 0.0, PI / 2)
    a.orbit(b, 20.0)
    w = World(a, b)
    for _ in range(200):
        w.step(0.05)
        assert abs(w.radial_speed) < 1e-6
    assert abs(w.distance() - 20.0) < 1e-6


def test_obstacle_los():
    o = Obstacle(4.0, -1.0, 6.0, 1.0, 10.0)
    assert o.crosses(0.0, 0.0, 10.0, 0.0)
    assert not o.crosses(0.0, 2.0, 10.0, 2.0)
    assert not o.crosses(0.0, 0.0, 3.0, 0.0)
    w = World(Walker(0.0, 0.0), Walker(10.0, 0.0), [o])
    assert w.los_db() == 10.0


def test_body_loss_shape():
    assert body_loss(9.0, 0.0) == 0.0
    assert abs(body_loss(9.0, PI) - 9.0) < 1e-9
    assert 0.0 < body_loss(9.0, PI / 2) < body_loss(9.0, 2.5) < 9.0


def _avg_rssi(d, seed):
    w = World(Walker(0.0, 0.0, 0.0), Walker(d, 0.0, PI))
    r = Radio(w, profile({"sigma_sh": 0.0, "sigma_t": 0.0}), Rng(seed))
    s = 0.0
    n = 0
    for _ in range(200):
        w.step(0.05)
        for p in r.step(0.05)[0]:
            s += p.rssi
            n += 1
    return s / n if n else None, n


def test_radio_rssi_decreases_with_distance():
    prev = None
    for d in (2.0, 6.0, 20.0, 60.0):
        m, n = _avg_rssi(d, 3)
        assert n > 50
        if prev is not None:
            assert m < prev - 3.0
        prev = m


def test_radio_rate_floor_and_peer():
    w = World(Walker(0.0, 0.0, 0.0), Walker(10.0, 0.0, PI))
    r = Radio(w, "clean", Rng(1))
    got = []
    by_b = []
    for _ in range(200):
        w.step(0.05)
        pa, pb = r.step(0.05)
        got.extend(pa)
        by_b.extend(p.rssi for p in pb)
    assert 90 <= r.sent[1] <= 110 and len(got) > 80
    assert got[0].peer_rssi is None or isinstance(got[0].peer_rssi, int)
    assert all(p.peer_rssi in by_b for p in got[5:])
    w = World(Walker(0.0, 0.0, 0.0), Walker(400.0, 0.0, PI))
    r = Radio(w, "typical", Rng(1))
    for _ in range(100):
        w.step(0.05)
        assert not r.step(0.05)[0]


def test_imu_steps_and_activity():
    a = Walker(0.0, 0.0, 0.0, 1.4)
    a.walk_to((28.0, 0.0)).still()
    w = World(a, Walker(50.0, 0.0))
    imu = Imu(a, Rng(2), "ideal")
    acts = []
    for _ in range(600):
        w.step(0.05)
        acts.append(imu.step(0.05).activity)
    assert 28.0 / 0.7 * 0.75 < imu.steps < 28.0 / 0.7 * 1.25
    assert acts[10] == ACT_STILL and acts[100] == ACT_WALK and acts[-1] == ACT_STILL
    assert imu.info.step_rate_hz == 0.0


def test_scenarios_build_and_run():
    for name in NAMES:
        w, dur = make(name, 0)
        assert dur > 10.0
        s = Sim(w, "indoor" if name == "orbit" else "typical", 0)
        for _ in range(40):
            s.step(0.05)
        assert s.world.distance() > 0.5


def test_sim_deterministic():
    def run():
        w, _ = make("zigzag_search", 5)
        s = Sim(w, "harsh", 5)
        out = []
        for _ in range(300):
            out.extend(p.rssi for p in s.step(0.05)[0])
        return out, round(w.a.x, 6), s.imus[0].steps
    assert run() == run()


def test_bakeoff_end_to_end():
    from tools import bakeoff
    tr = bakeoff.record("walk_away_back", "clean", 0, duration=30.0)
    assert tr.meta.get("turn_t") and tr.ticks
    m = bakeoff.evaluate(bakeoff.load("ema"), tr)
    assert 0.0 <= m["trend_acc"] <= 1.0 and m["dist_log_rmse"] >= 0.0
    assert m["reversal_lag_s"] is not None and m["us_per_update"] > 0.0
    assert m["us_per_packet"] is not None and m["us_per_packet"] >= 0.0
    o = bakeoff.parse_args(["--est", "ema", "--seeds=0", "--wrap", "--duration", "10"])
    assert o["est"] == "ema" and o["wrap"] and o["seeds"] == "0" and not o["quick"]
    rows = bakeoff.run(["ema"], ["approach"], ["clean"], [0], wrap=True, duration=10.0)
    assert len(rows) == 1 and rows[0]["dist_cov"] > 0.5
    md = bakeoff.report(rows)
    assert "| ema |" in md and "us/pkt" in md
    assert bakeoff.parse_seeds("100-102,7") == [100, 101, 102, 7]


def test_bench_est_runs():
    from tools import bench_est
    calls, cal = bench_est.packets("approach", "clean", 0)
    assert len(calls) > 100 and len(calls[0]) == 5
    us, b = bench_est.bench("kalman2", calls[:50], cal, reps=1)
    assert us >= 0.0 and (b is None or b > 0.0)
    us, b = bench_est.bench("particle", calls[:50], cal, reps=1, kw={"n": 8})
    assert us >= 0.0
