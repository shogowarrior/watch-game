"""2-D world with two walkers (watch A and watch B) and optional RF obstacles.

Coordinates in metres; headings in radians, 0 = +x, counter-clockwise positive.
"""

import math

PI = math.pi
TWO_PI = 2.0 * math.pi
TURN_RATE = 2.5  # rad/s, how fast a walker swings round to face its path


def wrap(a):
    """Wrap an angle to (-pi, pi]."""
    a = math.fmod(a + PI, TWO_PI)
    if a <= 0.0:
        a += TWO_PI
    return a - PI


def _pos(p):
    if hasattr(p, "x"):
        return p.x, p.y
    return p[0], p[1]


class Still:
    def __init__(self, dur=None):
        self.dur = dur
        self.el = 0.0

    def step(self, w, dt):
        self.el += dt
        return self.dur is not None and self.el >= self.dur


class WalkTo:
    """Walk towards a point (or a Walker) and stop ``stop_at`` metres short."""

    def __init__(self, target, speed=None, stop_at=0.0):
        self.target = target
        self.speed = speed
        self.stop_at = stop_at

    def step(self, w, dt):
        tx, ty = _pos(self.target)
        dx = tx - w.x
        dy = ty - w.y
        d = math.sqrt(dx * dx + dy * dy)
        left = d - self.stop_at
        if left <= 1e-6:
            return True
        w.face(math.atan2(dy, dx), dt)
        s = (self.speed or w.cruise) * dt
        if s >= left:
            s = left
        w.x += dx / d * s
        w.y += dy / d * s
        return s >= left - 1e-9


class WalkHeading:
    def __init__(self, heading, dur=None, dist=None, speed=None):
        self.heading = heading
        self.dur = dur
        self.dist = dist
        self.speed = speed
        self.el = 0.0
        self.walked = 0.0

    def step(self, w, dt):
        w.face(self.heading, dt)
        s = (self.speed or w.cruise) * dt
        if self.dist is not None and self.walked + s > self.dist:
            s = self.dist - self.walked
        w.x += math.cos(self.heading) * s
        w.y += math.sin(self.heading) * s
        self.walked += s
        self.el += dt
        if self.dist is not None and self.walked >= self.dist - 1e-9:
            return True
        return self.dur is not None and self.el >= self.dur


class Orbit:
    """Circle ``center`` (point or Walker) at radius r, facing along the path."""

    def __init__(self, center, r, speed=None, dur=None, ccw=True):
        self.center = center
        self.r = r
        self.speed = speed
        self.dur = dur
        self.sgn = 1.0 if ccw else -1.0
        self.el = 0.0

    def step(self, w, dt):
        cx, cy = _pos(self.center)
        phi = math.atan2(w.y - cy, w.x - cx)
        phi += self.sgn * (self.speed or w.cruise) * dt / self.r
        w.x = cx + self.r * math.cos(phi)
        w.y = cy + self.r * math.sin(phi)
        w.face(phi + self.sgn * 0.5 * PI, dt)
        self.el += dt
        return self.dur is not None and self.el >= self.dur


class Rotate:
    def __init__(self, rate, dur=None):
        self.rate = rate
        self.dur = dur
        self.el = 0.0

    def step(self, w, dt):
        w.heading = wrap(w.heading + self.rate * dt)
        self.el += dt
        return self.dur is not None and self.el >= self.dur


class Call:
    """Wrap ``fn(walker, dt) -> done`` as a behaviour."""

    def __init__(self, fn):
        self.fn = fn

    def step(self, w, dt):
        return self.fn(w, dt)


class Walker:
    """A person wearing a watch. Queue behaviours with the chaining helpers."""

    def __init__(self, x=0.0, y=0.0, heading=0.0, speed=1.3, name=""):
        self.x = float(x)
        self.y = float(y)
        self.heading = wrap(heading)
        self.cruise = speed
        self.name = name
        self.plan = []
        self.v = 0.0        # actual speed over the last step, m/s
        self.moved = 0.0    # distance moved over the last step, m
        self.odo = 0.0      # total distance moved, m
        self.turn = 0.0     # heading change over the last step, rad
        self._px = self.x
        self._py = self.y
        self._ph = self.heading

    def face(self, target_heading, dt):
        e = wrap(target_heading - self.heading)
        m = TURN_RATE * dt
        if e > m:
            e = m
        elif e < -m:
            e = -m
        self.heading = wrap(self.heading + e)

    def add(self, behaviour):
        self.plan.append(behaviour)
        return self

    def still(self, dur=None):
        return self.add(Still(dur))

    def walk_to(self, target, speed=None, stop_at=0.0):
        return self.add(WalkTo(target, speed, stop_at))

    def walk_heading(self, heading, dur=None, dist=None, speed=None):
        return self.add(WalkHeading(heading, dur, dist, speed))

    def orbit(self, center, r, speed=None, dur=None, ccw=True):
        return self.add(Orbit(center, r, speed, dur, ccw))

    def rotate_in_place(self, rate, dur=None):
        return self.add(Rotate(rate, dur))

    def waypoints(self, pts, speed=None):
        """Points (x, y) or (x, y, pause_s)."""
        for p in pts:
            self.add(WalkTo((p[0], p[1]), speed))
            if len(p) > 2 and p[2]:
                self.add(Still(p[2]))
        return self

    def set_pose(self, x=None, y=None, heading=None, clear=True):
        """External placement (e.g. dragged in a UI). Motion shows up on the next step."""
        if clear:
            self.plan = []
        if x is not None:
            self.x = float(x)
        if y is not None:
            self.y = float(y)
        if heading is not None:
            self.heading = wrap(heading)

    def step(self, dt):
        plan = self.plan
        if plan and plan[0].step(self, dt):
            plan.pop(0)
        dx = self.x - self._px
        dy = self.y - self._py
        self.moved = math.sqrt(dx * dx + dy * dy)
        self.v = self.moved / dt if dt > 0 else 0.0
        self.odo += self.moved
        self.turn = wrap(self.heading - self._ph)
        self._px = self.x
        self._py = self.y
        self._ph = self.heading

    @property
    def idle(self):
        return not self.plan


class Obstacle:
    """Axis-aligned rectangle that attenuates any radio path crossing it."""

    def __init__(self, x0, y0, x1, y1, db):
        self.x0 = min(x0, x1)
        self.y0 = min(y0, y1)
        self.x1 = max(x0, x1)
        self.y1 = max(y0, y1)
        self.db = db

    def crosses(self, ax, ay, bx, by):
        """Liang-Barsky: does segment a-b intersect the rectangle?"""
        t0 = 0.0
        t1 = 1.0
        dx = bx - ax
        dy = by - ay
        for p, q in ((-dx, ax - self.x0), (dx, self.x1 - ax), (-dy, ay - self.y0), (dy, self.y1 - ay)):
            if p == 0.0:
                if q < 0.0:
                    return False
                continue
            r = q / p
            if p < 0.0:
                if r > t1:
                    return False
                if r > t0:
                    t0 = r
            else:
                if r < t0:
                    return False
                if r < t1:
                    t1 = r
        return True


class World:
    """Two walkers A and B plus obstacles; advance with ``step(dt)``."""

    def __init__(self, a=None, b=None, obstacles=None):
        self.a = a or Walker(name="A")
        self.b = b or Walker(10.0, 0.0, PI, name="B")
        self.obstacles = list(obstacles or ())
        self.t = 0.0
        self.meta = {}
        self.radial_speed = 0.0
        self._d = self.distance()

    @property
    def walkers(self):
        return (self.a, self.b)

    def add_obstacle(self, x0, y0, x1, y1, db):
        self.obstacles.append(Obstacle(x0, y0, x1, y1, db))

    def distance(self):
        dx = self.b.x - self.a.x
        dy = self.b.y - self.a.y
        return math.sqrt(dx * dx + dy * dy)

    def rel_bearing(self, i):
        """Bearing from walker i to the other, relative to i's heading, in (-pi, pi]."""
        w, o = (self.a, self.b) if i == 0 else (self.b, self.a)
        return wrap(math.atan2(o.y - w.y, o.x - w.x) - w.heading)

    def bearing_ab(self):
        return self.rel_bearing(0)

    def bearing_ba(self):
        return self.rel_bearing(1)

    def los_db(self):
        """Total obstacle attenuation on the A-B line of sight, dB."""
        if not self.obstacles:
            return 0.0
        a = self.a
        b = self.b
        s = 0.0
        for o in self.obstacles:
            if o.crosses(a.x, a.y, b.x, b.y):
                s += o.db
        return s

    def set_pose(self, i, x=None, y=None, heading=None):
        (self.a if i == 0 else self.b).set_pose(x, y, heading)

    def step(self, dt):
        self.a.step(dt)
        self.b.step(dt)
        self.t += dt
        d = self.distance()
        self.radial_speed = (d - self._d) / dt if dt > 0 else 0.0
        self._d = d
        return d
