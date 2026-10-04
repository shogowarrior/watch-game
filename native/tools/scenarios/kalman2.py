"""finder/estimators/kalman2.py (the default estimator): every public field
after every update, on two simulated walks (sim/, watch A's view as
tools/bakeoff.py records it: packets with the partner's reported RSSI and
motion, idle ticks without) and on scripted cases for the branches a walk
rarely takes. Set up as the game does: set_exponent (Game.set_place) then
calibrate (PAIRING); the place also switches mid-run (MENU PLACE)."""

from finder import tuning as T
from finder.estimators.base import MotionInfo, ACT_UNKNOWN, ACT_STILL, ACT_WALK, ACT_RUN
from finder.estimators.kalman2 import Estimator
from sim import Sim
from sim import scenarios
from sim.radio import INDOOR_PROFILES

T0 = 10000
STILL = MotionInfo(ACT_STILL, 0.0, 0)


def r(v):
    return "n" if v is None else repr(float(v))


def mot(m):
    return "n n n" if m is None else "%d %s %d" % (m.activity, r(m.step_rate_hz), m.steps)


def walk(t_ms, hz=1.8, act=ACT_WALK):
    """Walking hint whose step count advances with time (tests/est_helpers.py)."""
    return MotionInfo(act, hz, int(t_ms * hz / 1000.0))


class Run:
    def __init__(self):
        self.e = None
        self.out = []

    def cmd(self, line):
        w = line.split()
        if w[0] == "new":
            self.e = Estimator()
        elif w[0] == "exp":
            self.e.set_exponent(float(w[1]))
        elif w[0] == "cal":
            self.e.calibrate(float(w[1]))
        elif w[0] == "reset":
            self.e.reset()
        self.out.append(line)

    def up(self, t, rssi, peer=None, me=None, pm=None):
        e = self.e
        e.update(t, rssi, peer, me, pm)
        self.out.append("up %d %s %s %s %s -> %s %s %s %s %s %s %d %s %s %s %s %s %s %d %s %s" % (
            t, r(rssi), r(peer), mot(me), mot(pm), r(e.rssi_f), r(e.rssi_var), r(e.rate_db_s), r(e.dist_m),
            r(e.dist_lo_m), r(e.dist_hi_m), e.trend, r(e.trend_conf), r(e.noise_db), r(e.speed), r(e.sig),
            r(e.vmax), r(e.peer_bias), e.peer_n, r(e.bias), r(e.pl.p0)))


def sim_trace(run, scenario, prof, seed, start_s, end_s, switch_s=None):
    """Simulate from 0 s; the estimator joins at start_s (the motion before it unseen)."""
    world, _ = scenarios.make(scenario, seed)
    s = Sim(world, prof, seed, "typical", 10.0)
    indoor = prof in INDOOR_PROFILES
    k0 = int(start_s / 0.1 + 0.5)
    peer = None
    for k in range(int(end_s / 0.1 + 0.5)):
        if k == k0:
            run.cmd("new")
            run.cmd("exp " + r(T.PATH_LOSS_N_INDOOR if indoor else T.PATH_LOSS_N))
            run.cmd("cal " + r(s.cal_p0(0)))
        if switch_s is not None and k == int(switch_s / 0.1 + 0.5):
            indoor = not indoor
            run.cmd("exp " + r(T.PATH_LOSS_N_INDOOR if indoor else T.PATH_LOSS_N))
        pa, _pb = s.step(0.1)
        mm = s.imus[0].info
        if k < k0:
            continue
        if not pa:
            run.up(T0 + int(world.t * 1000.0 + 0.5), None, None, mm, peer)
        for p in pa:
            if p.peer_motion is not None:
                peer = p.peer_motion
            run.up(T0 + p.t_ms, p.rssi, p.peer_rssi, mm, peer)


def scripted(run):
    run.cmd("new")
    run.cmd("exp " + r(T.PATH_LOSS_N))
    run.cmd("cal -45.0")
    t = T0
    fidget = MotionInfo(ACT_UNKNOWN, 0.0, 5)       # a fidgeting wrist: no new steps -> still
    for i in range(12):
        run.up(t, -72 + (3 if i & 1 else -3), None, fidget, fidget)
        t += 100
    for _ in range(3):                             # idle ticks while both still: trend stays 0
        run.up(t, None, None, fidget, fidget)
        t += 50
    for i in range(60):                            # walking closer ~1 dB/s, partner reports 6 dB up
        rssi = -85 + i // 10 + (1, -1, 0)[i % 3]
        run.up(t, rssi, rssi + 6 + (i % 2), walk(t), STILL)
        t += 100
    run.up(t, -100, -94, walk(t), STILL)           # deep fade: clipped at K_DOWN sigma
    t += 100
    run.up(t, -48, -42, walk(t), STILL)            # spike: clipped at K_UP sigma
    t += 100
    run.up(t, -79, -73, walk(t), STILL)
    run.up(t, -78, -72, walk(t), STILL)            # same ms: no predict
    for _ in range(36):                            # 3.6 s silence, still walking: trend dropped
        t += 100
        run.up(t, None, None, walk(t), STILL)
    t += 100
    run.up(t, -77, -71, walk(t), STILL)            # after the gap: rate reset
    for i in range(8):
        t += 100
        run.up(t, -77 + i // 3, None, walk(t), None)   # no partner motion: V_UNKNOWN
    n = walk(t).steps
    for i in range(6):                             # stopped: steps freeze, trend cleared on idle ticks
        t += 250
        run.up(t, None, None, MotionInfo(ACT_STILL, 0.0, n), STILL)
    run.cmd("cal -50.0")
    run.cmd("exp " + r(T.PATH_LOSS_N_INDOOR))      # MENU PLACE: IN
    for i in range(10):                            # my motion unknown, packets 1.5 s apart (no noise pair)
        t += 1500
        run.up(t, -70 + (4 if i & 1 else -4), None, None, STILL)
    for i in range(30):                            # both still on a rough channel: sig hits SIG_MAX
        t += 100
        run.up(t, -70 + (9 if i & 1 else -9), None, STILL, STILL)
    run.cmd("reset")                               # forgets the range, keeps the noise
    run.cmd("exp " + r(T.PATH_LOSS_N))
    for i in range(10):                            # still, then a walk starts: rate spread kicked
        t += 100
        run.up(t, -66, None, MotionInfo(ACT_STILL, 0.0, 40), STILL)
    for i in range(25):                            # falling 3 dB/s, faster than the walk allows: clamp
        t += 100
        run.up(t, -66 - (3 * i) // 10, None, walk(t, 1.2), STILL)
    for i in range(25):                            # running away, the partner walking toward me
        t += 100
        run.up(t, -75 - i // 4, -70 - i // 4, walk(t, 2.9, ACT_RUN), walk(t, 1.6))
    for i in range(20):                            # still activity, slow step creep (< STILL_HZ): not moving
        t += 100
        run.up(t, -82 + (i % 3), None, MotionInfo(ACT_STILL, 0.5, 300 + i // 5), STILL)
    run.cmd("reset")
    for i in range(12):                            # walking in close (< D_MIN): the rate bound's floor
        t += 100
        run.up(t, -44 + i // 4, None, walk(t), STILL)


def lines():
    yield "# new | exp <n> | cal <p0> | reset | up <t> <rssi> <peer_rssi> <my act hz steps> <peer act hz steps> ->"
    yield "#   rssi_f rssi_var rate_db_s dist_m dist_lo_m dist_hi_m trend trend_conf noise_db"
    yield "#   speed sig vmax peer_bias peer_n bias p0          (n: None)"
    run = Run()
    sim_trace(run, "both_approach", "typical", 2, 0.0, 8.0)
    sim_trace(run, "pause_and_go", "harsh", 1, 6.0, 14.0, switch_s=10.0)
    scripted(run)
    for line in run.out:
        yield line
