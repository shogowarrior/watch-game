"""Named scenarios: ``make(name, seed) -> (world, duration_s)``.

Watch A (index 0) is the one evaluated. ``world.meta`` may hold event times,
e.g. ``turn_t`` (walk_away_back: when A turns round).
"""

import math

from sim.rng import Rng
from sim.world import World, Walker, WalkTo, Call, PI, wrap

NAMES = ["approach", "both_approach", "stationary", "walk_away_back", "orbit",
         "rotate_in_place", "zigzag_search", "nlos_wall", "far_edge", "pause_and_go"]
QUICK = ["approach", "stationary", "walk_away_back", "pause_and_go"]


def _spd(rng, v=1.3):
    return v * rng.uniform(0.9, 1.1)


def _rh(rng):
    return rng.uniform(-PI, PI)


def approach(rng):
    a = Walker(0.0, 0.0, 0.0, _spd(rng), "A")
    b = Walker(40.0, 0.0, _rh(rng), 1.3, "B")
    a.walk_to(b, stop_at=1.5).still()
    return World(a, b), 36.0


def both_approach(rng):
    a = Walker(0.0, 0.0, 0.0, _spd(rng), "A")
    b = Walker(40.0, 0.0, PI, _spd(rng), "B")
    a.walk_to(b, stop_at=1.5).still()
    b.walk_to(a, stop_at=1.5).still()
    return World(a, b), 24.0


def stationary(rng):
    a = Walker(0.0, 0.0, _rh(rng), 1.3, "A")
    b = Walker(15.0, 0.0, _rh(rng), 1.3, "B")
    a.still()
    b.still()
    return World(a, b), 60.0


def walk_away_back(rng):
    a = Walker(5.0, 0.0, 0.0, _spd(rng), "A")
    b = Walker(0.0, 0.0, _rh(rng), 1.3, "B")
    w = World(a, b)

    def mark(wk, dt):
        w.meta["turn_t"] = w.t
        return True

    a.walk_heading(0.0, dist=25.0).add(Call(mark)).walk_to(b, stop_at=2.0).still()
    return w, 50.0


def orbit(rng):
    b = Walker(0.0, 0.0, _rh(rng), 1.3, "B")
    a = Walker(20.0, 0.0, 0.5 * PI, _spd(rng), "A")
    a.orbit(b, 20.0)
    return World(a, b), 60.0


def rotate_in_place(rng):
    a = Walker(0.0, 0.0, _rh(rng), 1.3, "A")
    b = Walker(10.0, 0.0, _rh(rng), 1.3, "B")
    a.rotate_in_place(rng.choice((-1.0, 1.0)) * rng.uniform(0.25, 0.35))
    b.still()
    return World(a, b), 60.0


class Search:
    """Noisy hot/cold search: legs of 4-10 s roughly towards the target, some
    wrong turns and pauses; done within ``done_r`` metres."""

    def __init__(self, target, rng, done_r=2.5):
        self.target = target
        self.rng = rng
        self.done_r = done_r
        self.leg = 0.0
        self.hd = 0.0
        self.pause = 0.0

    def step(self, w, dt):
        dx = self.target.x - w.x
        dy = self.target.y - w.y
        if dx * dx + dy * dy <= self.done_r * self.done_r:
            return True
        if self.pause > 0.0:
            self.pause -= dt
            return False
        rng = self.rng
        if self.leg <= 0.0:
            if rng.random() < 0.12:
                self.pause = rng.uniform(1.5, 4.0)
                return False
            true = math.atan2(dy, dx)
            if rng.random() < 0.2:
                self.hd = wrap(true + rng.uniform(-PI, PI))
            else:
                self.hd = wrap(true + rng.gauss(0.0, 0.6))
            self.leg = rng.uniform(4.0, 10.0)
        w.face(self.hd, dt)
        s = w.cruise * dt
        w.x += math.cos(self.hd) * s
        w.y += math.sin(self.hd) * s
        self.leg -= dt
        return False


def zigzag_search(rng):
    a = Walker(0.0, 0.0, _rh(rng), _spd(rng, 1.2), "A")
    b = Walker(50.0, 0.0, _rh(rng), 1.3, "B")
    a.add(Search(b, rng.fork(1))).still()
    return World(a, b), 100.0


def nlos_wall(rng):
    a = Walker(0.0, 0.0, 0.0, _spd(rng), "A")
    b = Walker(40.0, 0.0, _rh(rng), 1.3, "B")
    a.waypoints(((15.0, -6.0), (30.0, -6.0))).walk_to(b, stop_at=1.5).still()
    w = World(a, b)
    w.add_obstacle(15.0, -4.0, 30.0, 4.0, 12.0)
    return w, 38.0


def far_edge(rng):
    a = Walker(95.0, 0.0, 0.0, _spd(rng, 1.2), "A")
    b = Walker(0.0, 0.0, _rh(rng), 1.3, "B")
    a.walk_heading(0.0, dist=15.0).walk_to((80.0, 0.0)).still()
    return World(a, b), 45.0


class _PauseGo:
    """``WalkTo`` on a duty cycle: walk ``walk_s``, stand ``stop_s``, repeat."""

    def __init__(self, target, walk_s, stop_s, stop_at):
        self.walk_s = walk_s
        self.stop_s = stop_s
        self.t = 0.0
        self._walk = WalkTo(target, None, stop_at)

    def step(self, w, dt):
        ph = self.t % (self.walk_s + self.stop_s)
        self.t += dt
        if ph >= self.walk_s:
            return False
        return self._walk.step(w, dt)


def pause_and_go(rng):
    a = Walker(0.0, 0.0, 0.0, _spd(rng), "A")
    b = Walker(45.0, 0.0, _rh(rng), 1.3, "B")
    a.add(_PauseGo(b, 10.0, 10.0, 2.0)).still()
    return World(a, b), 72.0


_BUILDERS = {
    "approach": approach, "both_approach": both_approach, "stationary": stationary,
    "walk_away_back": walk_away_back, "orbit": orbit, "rotate_in_place": rotate_in_place,
    "zigzag_search": zigzag_search, "nlos_wall": nlos_wall, "far_edge": far_edge,
    "pause_and_go": pause_and_go,
}


def make(name, seed=0):
    """Build scenario ``name`` for ``seed``; returns (world, duration_s)."""
    return _BUILDERS[name](Rng(seed).fork(100))
