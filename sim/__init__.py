"""Two-watch simulation: world geometry + RSSI channel + BMA423-like motion hints.

    from sim import Sim
    from sim.scenarios import make
    world, dur = make("approach", seed=3)
    s = Sim(world, "typical", seed=3)
    while s.t < dur:
        pk_a, pk_b = s.step(0.05)
"""

from sim.rng import Rng
from sim.world import World, Walker
from sim.radio import Radio, add_walls, profile
from sim.imu import Imu


class Sim:
    """Binds a World to a Radio and one Imu per watch (index 0 = A, 1 = B)."""

    def __init__(self, world, prof="typical", seed=0, imu="typical", rate_hz=10.0):
        rng = Rng(seed)
        self.world = world
        self.prof = profile(prof)
        self.imus = (Imu(world.a, rng.fork(11), imu), Imu(world.b, rng.fork(12), imu))
        if self.prof["walls"] and not world.obstacles:
            add_walls(world, rng.fork(13))
        self.radio = Radio(world, self.prof, rng.fork(14), self.imus, rate_hz)

    @property
    def t(self):
        return self.world.t

    def step(self, dt):
        """Advance dt seconds; returns (packets received by A, packets received by B)."""
        self.world.step(dt)
        self.imus[0].step(dt)
        self.imus[1].step(dt)
        return self.radio.step(dt)

    def motion(self, i):
        return self.imus[i].info

    def cal_p0(self, i):
        return self.radio.cal_p0(i)
