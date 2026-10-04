"""End-to-end headless two-watch episode (sim/world + sim/radio + sim/imu).

Watch A (the seeker) and watch B (standing still) each run a ``Game``. The
director below plays the humans: pair by bumping, split up (A runs behind a
building so the split ends in SEARCHING), come back into view (FAR), walk
closer (NEAR, WARM), stop and scan by turning in place at the sweep rate
(body shadow makes the cosine pattern), follow the arrow's turn pacer, walk
on towards B (HOT) and bump -> FOUND on both watches.

A emerges from the building's shadow ~96 m out so the first fix is FAR: the
default estimator (kalman2, n 2.6 outdoors) reads distances short, so FAR (estimate
>= 28 m) needs roughly 75 m or more of true distance in the ``typical`` channel.

Every tick's RenderParams must pass ``validate`` and the §12 rules (no arrow
without a scan, no zone words, no metres). Run it for the timeline:
python3 -m tests.test_episode [seed ...]  (or node tools/mpy/run.mjs tests/test_episode.py [seed ...])
"""

import math
import sys

from sim import Sim
from sim.link import GameLink
from sim.world import World, Walker, PI, wrap
from finder import arrow as A
from finder import scan as S
from finder.arrow import wrap180
from finder.game import Game
from finder.render_params import validate
from finder.tuning import LOGIC_MS as TICK_MS   # logic rate (10 Hz)

MAC_A = b"\x24\x0a\xc4\x10\x00\x0a"
MAC_B = b"\x24\x0a\xc4\x10\x00\x0b"
DT = 0.05                  # sim step (s)
TILT_FLAT = 5.0            # watch held flat at the chest during the scan
BUILDING = (20.0, -10.0, 30.0, 10.0, 40.0)   # x0 y0 x1 y1 dB, a block east of B
HIDE = ((15.0, -15.0), (35.0, -12.0))    # into the building's radio shadow
FAR_OUT = ((85.0, -12.0),)               # still in the shadow, ~86 m out
EMERGE = ((85.0, -45.0),)                # step out of the shadow ~96 m from B
APPROACH = ((10.0, -9.0),)               # stop about 13 m from B and scan
MAX_T = 400.0
ZONE_WORDS = ("FAR", "NEAR", "WARM", "HOT")
MPY = sys.implementation.name == "micropython"


def _deg(r):
    return r * 180.0 / PI


def _text_ok(s):
    """§12: no zone words, no metres/dBm/degrees."""
    if s is None:
        return True
    for w in s.replace(",", " ").replace("?", " ").split(" "):
        if w in ZONE_WORDS:
            return False
    if "DB" in s or "DEG" in s or "." in s:
        return False
    i = 0
    n = len(s)
    while i < n - 1:
        if s[i] in "0123456789" and s[i + 1] == "M":
            return False
        i += 1
    return True


class Episode:
    """Two games on one simulated world, driven by a scripted pair of humans."""

    def __init__(self, seed=1, prof="typical", imu="typical", verbose=False):
        self.seed = seed
        self.verbose = verbose
        a = Walker(1.0, 0.0, PI, 1.3, "A")       # 1 m apart, facing each other
        b = Walker(0.0, 0.0, 0.0, 1.3, "B")
        w = World(a, b)
        w.add_obstacle(*BUILDING)
        self.world = w
        self.sim = Sim(w, prof, seed, imu)
        self.games = (Game(MAC_A), Game(MAC_B))
        self.link = GameLink(self.games, (MAC_A, MAC_B))
        self.t_ms = 0
        self.phase = "pair"
        self.phase_t = 0
        self.timeline = ([], [])     # (t_ms, screen, sub) on change, per watch
        self.haptics = ([], [])
        self.violations = []
        self.scan_errors = []        # |theta_est - theta_true| per fix
        self.scan_attempts = 0
        self.theta_true = None
        self.heading_goal = None
        self.bumps = 0
        self.found_t = [None, None]
        self._last = [None, None]
        self._scan_sub = None
        self._walk_left = 0.0
        self._wp = 0
        self._bump_next = None
        self._rng_heading = (seed * 2654435761) % 360

    # ---- sim plumbing ----------------------------------------------------------
    def _step_sim(self):
        self.link.set_rates(self.sim.radio)
        self.link.deliver(self.sim.step(DT))

    def _tick(self):
        t = self.t_ms
        for i in (0, 1):
            g = self.games[i]
            info = self.sim.motion(i)
            g.set_motion(t, info.activity, info.steps, info.step_rate_hz, TILT_FLAT, True)
            g.set_battery(t, 90)
            p = g.tick(t)
            self._check(i, p)

    def _check(self, i, p):
        t = p.t_ms
        v = validate(p)
        if v:
            self.violations.append((i, t, "validate", v))
        for s in (p.word, p.top_text, p.banner[0] if p.banner else None):
            if not _text_ok(s):
                self.violations.append((i, t, "text", s))
        if p.arrow_deg is not None and self.games[i].scans == 0:
            self.violations.append((i, t, "arrow without scan", p.arrow_deg))
        key = (p.screen, p.sub)
        if key != self._last[i]:
            self._last[i] = key
            self.timeline[i].append((t, p.screen, p.sub))
        if p.haptic:
            self.haptics[i].append((t, p.haptic))
        if p.screen == "FOUND" and self.found_t[i] is None:
            self.found_t[i] = t

    # ---- the humans -------------------------------------------------------------
    def _set(self, phase):
        self.phase = phase
        self.phase_t = self.t_ms
        self._wp = 0
        if self.verbose:
            print("  %6.1f s  director: %s" % (self.t_ms / 1000.0, phase))

    def _move(self, dt):
        """Walker A's motion for this step, from the director phase."""
        w = self.world.a
        ga = self.games[0]
        ph = self.phase
        if ph == "split_run":
            if self._route(w, HIDE, dt, 2.4) and ga.mode == "SEARCHING":
                self._set("far_out")
        elif ph == "far_out":
            if self._route(w, FAR_OUT, dt, 2.0):
                self._set("emerge")
        elif ph == "emerge":
            if self._route(w, EMERGE, dt, 1.3):
                self._set("approach")
        elif ph == "approach":
            if self._route(w, APPROACH, dt, 1.3):
                self._set("settle")
        elif ph == "settle":
            # stop, turn to some heading (people rarely face their friend) and wait for WARM
            if self.heading_goal is None:
                self.heading_goal = wrap(self._rng_heading * PI / 180.0)
            w.face(self.heading_goal, dt)
            if (abs(wrap(w.heading - self.heading_goal)) < 0.02 and ga.mode == "HUNT"
                    and self.t_ms - self.phase_t > 4000):
                self._set("scan_tap")
        elif ph == "sweep":
            sc = ga.scan
            if ga.mode == "SCANNING" and sc.sub == "sweep" and not sc.paused:
                w.heading = wrap(w.heading - S.DEG_PER_S * PI / 180.0 * dt)     # clockwise
        elif ph == "turn":
            a = ga.arrow
            if a is not None and a.phase == "turn":
                rate = A.PACER_DEG_S * PI / 180.0
                w.heading = wrap(w.heading - (rate if a.theta >= 0 else -rate) * dt)
        elif ph == "walk_on":
            h = w.heading
            s = 1.3 * dt
            w.x += math.cos(h) * s
            w.y += math.sin(h) * s
            self._walk_left -= s
            if self._walk_left <= 0.0:
                self._set("close_in")
        elif ph == "close_in":
            b = self.world.b
            if self._go(w, (b.x + 0.8, b.y + 0.3), dt, 1.1):
                self._set("bump")

    def _route(self, w, pts, dt, v):
        """Walk the waypoints of ``pts`` in turn; True once at the last one."""
        k = self._wp
        while k < len(pts) and self._go(w, pts[k], dt, v):
            k += 1
        self._wp = k
        return k >= len(pts)

    def _go(self, w, p, dt, v):
        dx = p[0] - w.x
        dy = p[1] - w.y
        d = math.sqrt(dx * dx + dy * dy)
        if d < 0.05:
            return True
        w.face(math.atan2(dy, dx), dt)
        s = v * dt
        if s > d:
            s = d
        w.x += dx / d * s
        w.y += dy / d * s
        return False

    def _direct(self):
        """Tick-rate decisions (taps, bumps) for both humans."""
        t = self.t_ms
        ga, gb = self.games
        ph = self.phase
        if ph == "pair":
            if ga.pair.sub == "seen" and gb.pair.sub == "seen" and t - self.phase_t > 1500:
                ga.on_accel_tap(t)                  # bump the watches together
                gb.on_accel_tap(t + 90)
                self._set("calibrate")
            elif ga.pair.sub == "seen" and self.phase_t == 0:
                self.phase_t = t
        elif ph == "calibrate":
            if ga.pair.sub == "split" and gb.pair.sub == "split":
                self._set("split_run")
        elif ph == "scan_tap":
            self.scan_attempts += 1
            ga.on_gesture(t, 1, 120, 120)       # centre tap -> SCANNING ready
            self._set("sweep")
            self._scan_sub = None
        elif ph == "sweep":
            sc = ga.scan
            sub = sc.sub if ga.mode == "SCANNING" else None
            if sub == "sweep" and self._scan_sub != "sweep":
                self.theta_true = (-_deg(self.world.rel_bearing(0))) % 360.0
            self._scan_sub = sub
            if ga.mode == "HUNT":
                if ga.arrow is not None:
                    th = ga.arrow.theta % 360.0
                    self.scan_errors.append(abs(wrap180(th - self.theta_true)))
                    self._set("turn")
                elif self.scan_attempts < 3:
                    self._set("scan_tap")
                else:
                    self._walk_left = 0.0
                    self._set("close_in")
        elif ph == "turn":
            a = ga.arrow
            if a is None or a.phase in ("lock", "walk", "done"):
                self._walk_left = 4.0
                self._set("walk_on")
        elif ph == "bump":
            both_hot = ga.px.zone == 3 and gb.px.zone == 3 and ga.mode == "HUNT" \
                and gb.mode == "HUNT"
            if ga.mode == "FOUND" and gb.mode == "FOUND":
                self._set("found")
            elif both_hot and (self._bump_next is None or t >= self._bump_next):
                self.bumps += 1                     # knock the watches together, every 2 s while both HOT
                ga.on_accel_tap(t)
                gb.on_accel_tap(t + 120)
                self._bump_next = t + 2000

    def run(self, max_t=MAX_T):
        while self.world.t < max_t:
            self._move(DT)
            self._step_sim()
            self.t_ms = int(self.world.t * 1000.0 + 0.5)
            if self.t_ms % TICK_MS == 0:
                self._tick()
                self._direct()
            if self.phase == "found" and self.t_ms - self.phase_t >= 3000:
                break
        return self

    # ---- report ------------------------------------------------------------------
    def screens(self, i):
        out = []
        for _, s, _ in self.timeline[i]:
            if not out or out[-1] != s:
                out.append(s)
        return out

    def report(self):
        for i in (0, 1):
            print("watch %s timeline:" % "AB"[i])
            for t, s, sub in self.timeline[i]:
                print("  %7.1f s  %-9s %s" % (t / 1000.0, s, sub if sub is not None else ""))
        print("scan errors (deg):", ["%.0f" % e for e in self.scan_errors],
              "attempts", self.scan_attempts, "bumps", self.bumps)
        print("violations:", len(self.violations))
        for v in self.violations[:10]:
            print("  ", v)


def _has_order(seq, want):
    i = 0
    for s in seq:
        if i < len(want) and s == want[i]:
            i += 1
    return i == len(want)


SEEDS = (1, 2, 3) if MPY else (1, 2, 3, 4, 5, 6, 7, 8)
_cache = {}


def _episodes():
    if "e" not in _cache:
        _cache["e"] = [Episode(s).run() for s in SEEDS]
    return _cache["e"]


def test_episode_reaches_found_on_both_watches():
    for e in _episodes():
        assert e.found_t[0] is not None and e.found_t[1] is not None, (e.seed, e.phase)
        assert abs(e.found_t[0] - e.found_t[1]) <= 1000, (e.seed, e.found_t)


def test_episode_state_progression():
    full = 0
    for e in _episodes():
        sa = e.screens(0)
        # every seed: pair -> search -> a zone screen -> scan -> HOT -> FOUND
        assert _has_order(sa, ["PAIRING", "SEARCHING"]), (e.seed, sa)
        first = sa[sa.index("SEARCHING") + 1]
        assert first in ZONE_WORDS, (e.seed, sa)
        assert _has_order(sa, ["SEARCHING", first, "SCANNING", "HOT", "FOUND"]), (e.seed, sa)
        subs = [sub for _, s, sub in e.timeline[0] if s == "PAIRING"]
        assert _has_order(subs, ["looking", "seen", "calibrate", "split"]), (e.seed, subs)
        assert _has_order(e.screens(1), ["PAIRING", "SEARCHING", "HOT", "FOUND"]), \
            (e.seed, e.screens(1))
        if _has_order(sa, ["SEARCHING", "FAR", "NEAR", "WARM", "SCANNING", "HOT", "FOUND"]):
            full += 1
    # most seeds walk the whole ladder; the default estimator reads ~30 % short
    # at 96 m, so some first fixes land in NEAR (see the module docstring)
    assert full * 3 >= len(SEEDS) * 2, (full, len(SEEDS))


def test_episode_render_params_always_valid():
    for e in _episodes():
        assert not e.violations, (e.seed, e.violations[:5])


def test_episode_scan_bearing_mostly_right():
    fixes = 0
    good = 0
    for e in _episodes():
        for err in e.scan_errors:
            fixes += 1
            if err <= 45.0:
                good += 1
    assert fixes >= max(1, len(SEEDS) - 1), fixes
    assert good * 3 >= fixes * 2, (good, fixes)


def test_episode_found_haptic_and_arrow_lifecycle():
    for e in _episodes():
        for i in (0, 1):
            assert any(h == "FOUND" for _, h in e.haptics[i]), (e.seed, i)
        subs = [sub for _, s, sub in e.timeline[0] if s in ("FAR", "NEAR", "WARM", "HOT")]
        if e.scan_errors:
            assert "reveal" in subs, (e.seed, subs)


def test_episode_harsh_channel_relinks_without_searching():
    e = Episode(3, "harsh").run()
    assert not e.violations, e.violations[:5]
    assert e.found_t[0] is not None and e.found_t[1] is not None
    sa = e.screens(0)
    assert "LINK_LOST" in sa, sa
    for i in range(len(sa) - 1):
        if sa[i] == "LINK_LOST":
            assert sa[i + 1] in ZONE_WORDS, sa          # relink -> zone, never SEARCHING
    assert any(h == "LOST" for _, h in e.haptics[0])


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:] if a.isdigit()] or [1]
    for s in args:
        print("=== seed %d" % s)
        Episode(s, verbose=True).run().report()
