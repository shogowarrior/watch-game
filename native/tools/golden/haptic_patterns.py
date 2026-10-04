"""finder/haptic_patterns.py: the 9 patterns, stronger, TOTAL_MS, min_period,
BlankWindow and HapticPlayer scenarios (every pattern in every mode, late
ticks, the drop guard, the one-slot queue, pre-emption, heartbeats, the
metronome grid, mode changes).

A scenario is a list of ops on one player; each op's line carries its inputs
and what the player returned and reported after it:

    new <mode>
    play <name> <t|n> -> <accepted> <state>
    hb <name> <t|n> -> <started> <state>
    cancel -> <dropped> <state>
    allowed <t|n> -> <hb_allowed>
    metro <period> <t|n> <name|n> -> <period_ms>
    mode <m> -> <state>
    run <t0> <t1> <step> -> <state> <level at t0> <t>:<level> ...   (only where it changes)

<state> is <busy> <active> <beat_due|n> <beat_playing>.

Times stay below 2**29, so the Python's 2**30 tick wrap never matters.
"""

from finder import haptic_patterns as hp
from finder import tuning as T

NAMES = tuple(T.HAPTIC_NAMES)


def _t(t):
    return "n" if t is None else "%d" % t


def _b(v):
    return "1" if v else "0"


def _lvl(v):
    return "%d" % int(v)


class Script:
    def __init__(self):
        self.out = []
        self.pl = None

    def new(self, mode=hp.MODE_FULL):
        self.pl = hp.HapticPlayer(mode)
        self.out.append("new %d" % mode)

    def _state(self):
        pl = self.pl
        return "%s %s %s %s" % (_b(pl.busy), _b(pl.active), _t(pl.beat_due), _b(pl.beat_playing))

    def play(self, name, t=None):
        r = self.pl.play_named(name, t)
        self.out.append("play %s %s -> %s %s" % (name, _t(t), _b(r), self._state()))
        return r

    def hb(self, name, t=None):
        r = self.pl.heartbeat(name, t)
        self.out.append("hb %s %s -> %s %s" % (name, _t(t), _b(r), self._state()))
        return r

    def cancel(self):
        r = self.pl.cancel_heartbeat()
        self.out.append("cancel -> %s %s" % (_b(r), self._state()))

    def allowed(self, t):
        self.out.append("allowed %s -> %s" % (_t(t), _b(self.pl.hb_allowed(t))))

    def metro(self, period, t=None, name=None):
        self.pl.set_metronome(period, t, name)
        self.out.append("metro %d %s %s -> %d" % (period, _t(t), name or "n", self.pl.period_ms))

    def mode(self, m):
        self.pl.set_mode(m)
        self.out.append("mode %d -> %s" % (m, self._state()))

    def run(self, t0, t1, step=1):
        """Tick every ``step`` ms in [t0, t1); record the level where it changes."""
        lv = []
        last = None
        t = t0
        while t < t1:
            v = self.pl.tick(t)
            assert v in (0.0, 1.0), v
            if last is None:
                lv.append(_lvl(v))
            elif v != last:
                lv.append("%d:%s" % (t, _lvl(v)))
            last = v
            t += step
        self.out.append("run %d %d %d -> %s %s" % (t0, t1, step, self._state(), " ".join(lv)))


def scenarios():
    s = Script()
    # every pattern as an event and as a heartbeat, in every mode
    for mode in (hp.MODE_FULL, hp.MODE_EVENTS, hp.MODE_OFF):
        for name in NAMES:
            s.new(mode)
            s.play(name, 1000)
            s.run(1000, 4000)
            s.new(mode)
            s.hb(name, 1000)
            s.run(1000, 2500)
        s.new(mode)                         # the metronome
        s.metro(1000, 0)
        s.run(0, 3500)
    # unknown names, nothing ticked yet
    s.new()
    s.play("found", 0)
    s.play("zone_closer", 0)
    s.run(0, 5)
    # a pending start takes the first tick
    s.new()
    s.play("FARTHER")
    s.allowed(None)
    s.run(500, 900)
    s.new()
    s.hb("DOUBLE")
    s.run(700, 1000)
    # late ticks never skip a pulse
    s.new()
    s.play("TICK", 0)
    s.run(70, 200, 10)
    s.new()
    s.play("FOUND", 1000)
    s.run(1005, 2400, 33)
    s.new()
    s.play("LOST", 0)
    s.run(0, 1200, 33)
    s.new()
    s.play("FOUND", 0)
    s.run(0, 80, 79)
    s.run(95, 96)
    s.run(96, 290)
    # the drop guard (same or higher rank within 1 s; TICK only by a higher one)
    s.new()
    s.play("CLOSER", 0)
    s.play("FARTHER", 500)
    s.play("TICK", 600)
    s.play("FOUND", 700)
    s.run(700, 3000)
    s.play("FARTHER", 3000)
    s.run(3000, 3400)
    s.new()
    s.play("CLOSER", 0)
    s.run(0, 1000)
    s.play("FARTHER", 1000)
    s.play("FARTHER", 1999)
    s.run(1000, 1400)
    s.new()
    s.play("TICK", 0)
    s.run(0, 990)
    s.play("TICK", 990)
    s.run(990, 1100)
    s.new()
    s.hb("DOUBLE", 0)
    s.play("DOUBLE", 100)
    s.run(0, 500)
    s.new()                                 # guard with t from the last tick
    s.run(0, 50)
    s.play("NOPE")
    s.play("NOPE")
    s.run(50, 900)
    # a lower event waits for a higher one, then starts MIN_GAP_MS after it
    s.new()
    s.play("HOLD", 0)
    s.run(0, 1200)
    s.play("TICK", 1200)
    s.run(1200, 2000)
    s.new()
    s.play("HOLD", 0)
    s.run(0, 1200)
    s.play("TICK", 1200)
    s.run(1200, 1600)
    s.run(1610, 1611)
    s.run(1611, 1800)
    # the queue: latest of the same or higher rank wins, a lower one is dropped
    s.new()
    s.play("HOLD", 0)
    s.run(0, 1100)
    s.play("CLOSER", 1100)
    s.play("FARTHER", 1200)
    s.play("TICK", 1250)
    s.play("BATT", 1300)
    s.run(1100, 3500)
    # heartbeats: a running pulse plays out, resume 1 s after the event
    s.new()
    s.hb("TICK", 0)
    s.run(0, 30)
    s.play("CLOSER", 30)
    s.run(30, 700)
    for t in (1559, 1060, 1560):
        s.allowed(t)
        s.hb("TICK", t)
    s.run(1560, 1700)
    s.new()
    s.play("FARTHER", 0)
    s.hb("TICK", 0)
    s.run(0, 1000)
    s.hb("TICK", 1000)
    s.hb("TICK", 1300)
    s.run(1000, 1500)
    s.hb("DOUBLE", 2000)
    s.run(2000, 2400)
    s.new()
    s.play("NOPE", 0)
    s.hb("TICK", 0)
    s.run(0, 1000)
    # a heartbeat handed over ahead of its time waits (beat_due) and is not active
    # until it starts; it can be cancelled until then, and an earlier event replaces it
    s.new()
    s.run(0, 10)
    s.hb("TICK", 400)
    s.run(10, 399)
    s.run(399, 401)
    s.cancel()                              # already on: plays out
    s.run(401, 700)
    s.hb("DOUBLE", 900)
    s.cancel()                              # dropped before it starts
    s.run(700, 1200)
    s.hb("TICK", 1500)
    s.play("CLOSER", 1300)
    s.run(1200, 2600)
    s.hb("TICK", 2800)                      # resumed 1 s after CLOSER ended
    s.run(2600, 2750, 25)
    s.play("FARTHER", 2790)                 # starts first: replaces the waiting beat
    s.run(2750, 3600)
    s.new()
    s.hb("TICK", 100)                       # nothing ticked yet: no time, so not waiting
    s.cancel()
    s.run(0, 300)
    # the metronome: grid, duty cap, tempo change keeps phase, overdue speed-up, stop
    s.new()
    s.metro(500, 0)
    s.run(0, 1100)
    s.new()
    s.metro(250, 0)
    s.metro(500, 0, "DOUBLE")
    s.run(0, 1300)
    s.new()
    s.metro(1000, 0)
    s.run(0, 2300)
    s.metro(500, 2300)
    s.metro(500, 2301)
    s.run(2300, 3100)
    s.metro(1000, 3100)
    s.run(3100, 5100)
    s.new()
    s.metro(2400, 0)
    s.run(0, 2000)
    s.metro(500, 2000)
    s.run(2000, 3100)
    s.new()
    s.metro(1000, 0)
    s.run(0, 1)
    s.metro(0)
    s.run(1, 3000)
    s.metro(-5, 3000)
    s.metro(1000)                           # restart with no time: beats at the next tick
    s.run(3500, 5000)
    s.new()
    s.metro(1600, 0)
    s.metro(1000, 10)                       # tempo change before the first beat
    s.run(0, 2600)
    # an event pre-empts the metronome; the grid continues
    s.new()
    s.metro(500, 0)
    s.run(0, 950)
    s.play("FOUND", 950)
    s.run(950, 3600)
    s.new()
    s.metro(1000, 0)
    s.run(0, 10)
    s.play("FARTHER", 10)
    s.run(10, 2100)
    # §7 gap after any pulse: an event right after a heartbeat, pre-emption mid-gap/mid-pulse
    for d in range(10, 120, 10):
        s.new()
        s.hb("TICK", 0)
        s.run(0, d)
        s.play("DOUBLE", d)
        s.run(d, 1000)
    for d in (310, 100, 400, 460):
        s.new()
        s.play("NOPE", 0)
        s.run(0, d)
        s.play("FOUND", d)
        s.run(d, 2000)
    s.new()
    s.hb("TICK", 0)
    s.run(0, 1)
    s.play("CLOSER", 30)
    s.run(30, 31)
    s.run(59, 60)
    s.run(75, 76)
    s.run(76, 250)
    # pre-emption chain: a higher event cuts one that pre-empted another
    s.new()
    s.play("TICK", 0)
    s.run(0, 30)
    s.play("CLOSER", 30)
    s.run(30, 150)
    s.play("FOUND", 150)
    s.run(150, 1500)
    # modes: EVENTS mutes heartbeats, OFF cancels and rejects, the grid survives
    s.new()
    s.metro(1000, 0)
    s.run(0, 2)
    s.mode(hp.MODE_EVENTS)
    s.run(2, 1100)
    s.hb("TICK", 1100)
    s.play("FARTHER", 1100)
    s.run(1100, 1101)
    s.mode(hp.MODE_OFF)
    s.run(1101, 1102)
    s.play("FOUND", 1102)
    s.run(1102, 3000)
    s.mode(hp.MODE_FULL)
    s.run(3000, 4100)
    s.new()
    s.play("HOLD", 0)
    s.run(0, 1100)
    s.play("TICK", 1100)
    s.mode(hp.MODE_EVENTS)
    s.run(1100, 2000)
    s.mode(hp.MODE_OFF)
    s.mode(hp.MODE_FULL)
    s.play("TICK", 2000)
    s.run(2000, 2200)
    # frame-rate ticking of a mixed session
    s.new()
    s.metro(1600, 0)
    t = 0
    for k, (name, at) in enumerate((("CLOSER", 2000), ("TICK", 2200), ("FOUND", 4100), ("NOPE", 4300),
                                    ("LOST", 6500), ("BATT", 9000))):
        s.run(t, at, 50)
        s.play(name, at)
        t = at
        if k == 2:
            s.metro(500, at)
    s.run(t, 13000, 50)
    return s.out


def lines():
    yield "# names <the 9 in priority order>; rank <name> <rank> <total_ms> <on_ms> <min_period> <pattern...>"
    yield "names " + " ".join(NAMES)
    yield "consts %d %d %d %d %d %d %d" % (hp.MIN_PULSE_MS, hp.MIN_GAP_MS, hp.MAX_DUTY_PCT, hp.EVENT_GUARD_MS,
                                           hp.HB_RESUME_MS, hp.BLANKING_MS, max(hp.RANK.values()) + 1)
    for n in NAMES:
        p = hp.PATTERNS[n]
        flat = []
        for on, off in p:
            flat += [on, off]
        yield "rank %s %d %d %d %d %s" % (n, hp.RANK[n], hp.TOTAL_MS[n], hp.on_ms(p), hp.min_period(p),
                                         " ".join("%d" % v for v in flat))
    yield "# stronger <cur|n> <name> -> <kept>"
    for cur in (None,) + NAMES:
        for n in NAMES:
            yield "stronger %s %s -> %s" % (cur or "n", n, hp.stronger(cur, n))
    yield "# blank: extend <t> <ms> | expire <now> | reset, then active over a probe range: <t>:<0|1> where it changes"
    w = hp.BlankWindow()
    probes = (0, 1200)

    def act():
        out = []
        last = None
        for t in range(*probes):
            a = w.active(t)
            if a != last:
                out.append("%d:%s" % (t, _b(a)))
            last = a
        return " ".join(out) + " until=%s" % _t(w.until)
    yield "blank new -> " + act()
    for op in (("extend", 100, hp.TOTAL_MS["TICK"] + hp.BLANKING_MS), ("extend", 400, 100), ("extend", 450, 20),
               ("extend", 600, 100), ("extend", 650, 200), ("extend", 640, 100), ("expire", 849),
               ("expire", 850), ("extend", 1000, 100), ("expire", 1099), ("expire", 1100), ("extend", 1000, 100),
               ("reset",), ("extend", 300, 0), ("extend", 300, 50), ("expire", 340), ("extend", 900, 50)):
        if op[0] == "extend":
            w.extend(op[1], op[2])
        elif op[0] == "expire":
            w.expire(op[1])
        else:
            w.reset()
        yield "blank %s -> %s" % (" ".join(str(x) for x in op), act())
    yield "# player scenarios (see native/tools/golden/haptic_patterns.py)"
    for ln in scenarios():
        yield ln
