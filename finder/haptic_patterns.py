"""Haptic patterns and a tick-driven player (pure: no machine imports).

Patterns, priorities and timing rules come from ``haptics`` in
docs/design/tokens.json v0.2 via finder/tuning.py (ui-spec §7): the nine
names TICK, DOUBLE, CLOSER, FARTHER, NOPE, LOST, HOLD, FOUND, BATT. A token
array ``[on, off, on, ...]`` becomes steps ``(on_ms, off_ms, strength)``.

Two tracks feed one motor:

* **events** (``play_named`` / ``play``): ranked by ``tuning.HAPTIC_RANK``.
  An event is dropped only if one of the same or higher rank started less
  than ``EVENT_GUARD_MS`` earlier. A higher rank pre-empts a playing event;
  a lower rank arriving while a higher one still plays waits in a one-slot
  queue and starts when it ends (so a pattern is never truncated by a
  lesser one).
* **heartbeats** (``heartbeat``; or the internal ``set_metronome`` grid when
  no renderer drives them): TICK/DOUBLE pulses, in FULL mode only. An event
  cuts a running heartbeat and heartbeats resume ``HB_RESUME_MS`` after the
  event ends (tokens: "an event replaces the next heartbeat pulse").

The renderer returns ``[event, heartbeat]``, ``[event]`` or ``[heartbeat]``
per frame; pass it to ``play_frame``. ``tick(t_ms)`` is called once per
main-loop frame and returns the motor strength (0..1) to apply; the hal
Motor applies it only on change. Nothing blocks or sleeps.
"""

from finder import tuning as _T
from finder.compat import ticks_add, ticks_diff

MIN_PULSE_MS = _T.HAPTIC_MIN_PULSE_MS          # ERM spin-up floor (60)
MIN_GAP_MS = _T.HAPTIC_MIN_GAP_MS
MAX_DUTY_PCT = int(_T.HAPTIC_MAX_DUTY * 100 + 0.5)   # 12
EVENT_GUARD_MS = _T.HAPTIC_EVENT_GUARD_MS      # 1000
HB_RESUME_MS = _T.HAPTIC_HB_RESUME_MS          # 1000
BLANKING_MS = _T.HAPTIC_BLANKING_MS            # 150: accel ignores pulse .. end + this
NAMES = _T.HAPTIC_NAMES                        # priority order, highest first


def steps(arr, strength=1.0):
    """Token on/off array -> ((on_ms, off_ms, strength), ...)."""
    out = []
    n = len(arr)
    for i in range(0, n, 2):
        out.append((arr[i], arr[i + 1] if i + 1 < n else 0, strength))
    return tuple(out)


PATTERNS = {}
for _n in NAMES:
    PATTERNS[_n] = steps(_T.HAPTIC_PATTERNS[_n])
RANK = _T.HAPTIC_RANK

TICK = PATTERNS["TICK"]
DOUBLE = PATTERNS["DOUBLE"]
CLOSER = PATTERNS["CLOSER"]
FARTHER = PATTERNS["FARTHER"]
NOPE = PATTERNS["NOPE"]
LOST = PATTERNS["LOST"]
HOLD = PATTERNS["HOLD"]
FOUND = PATTERNS["FOUND"]
BATT = PATTERNS["BATT"]

_PRIO = tuple((PATTERNS[_n], RANK[_n]) for _n in NAMES)
_NRANK = max(RANK.values()) + 1

# ---- proximity tempo (zone heartbeat periods, ui-spec §5.3) ---------------
TEMPO_COLD_MS = _T.ZONE_PERIOD_MS[0]           # FAR 2400
TEMPO_HOT_MS = _T.ZONE_PERIOD_MS[-1]           # HOT 500

MODE_OFF = 0
MODE_EVENTS = 1         # events only, no heartbeats
MODE_FULL = 2           # default
MODE_NAMES = ("OFF", "EVENTS", "FULL")         # index == MODE_*

_ZERO = 0.0


def on_ms(pattern):
    """Total motor-on time of ``pattern``."""
    s = 0
    for st in pattern:
        s += st[0]
    return s


def min_period(pattern):
    """Shortest repeat period keeping ``pattern`` at <= MAX_DUTY_PCT duty."""
    return -(-on_ms(pattern) * 100 // MAX_DUTY_PCT)


def priority_of(pattern):
    """Rank of a named pattern (by identity); unnamed patterns rank lowest."""
    for p, n in _PRIO:
        if p is pattern:
            return n
    return 0


TEMPO_STEPS = 32        # closeness buckets
# Geometric COLD..HOT, built once at import so per-frame lookups skip float pow.
_TEMPO = tuple(int(TEMPO_COLD_MS * (TEMPO_HOT_MS / TEMPO_COLD_MS) ** (i / TEMPO_STEPS) + 0.5)
               for i in range(TEMPO_STEPS + 1))


def tempo_bucket(closeness):
    """Nearest tempo bucket 0..TEMPO_STEPS for closeness 0..1 (clamped)."""
    if closeness <= 0:
        return 0
    if closeness >= 1:
        return TEMPO_STEPS
    return (int(closeness * (2 * TEMPO_STEPS)) + 1) >> 1


def tempo_for_bucket(b):
    """Metronome period for bucket ``b`` (int, clamped); allocation-free."""
    return _TEMPO[0 if b < 0 else TEMPO_STEPS if b > TEMPO_STEPS else b]


def tempo_ms(closeness):
    """Metronome period for closeness 0 (cold) .. 1 (hot), geometric COLD..HOT.

    Table lookup quantised to TEMPO_STEPS buckets; costs one float multiply
    (none for int 0/1). Cache ``tempo_bucket`` to skip even that per frame.
    """
    return _TEMPO[tempo_bucket(closeness)]


class _Track:
    """Plays one pattern; exact edges when ticked every ms, never skips a pulse."""

    def __init__(self):
        self.pat = None
        self.i = 0
        self.on = False
        self.shown = False
        self.ph = None           # current phase start (None: at next tick)
        self.end = None          # when the last pattern ended

    def start(self, pat, t):
        self.pat = pat
        self.i = 0
        self.on = True
        self.shown = False
        self.ph = t

    def advance(self, t):
        pat = self.pat
        if self.ph is None:
            self.ph = t
        while True:
            on_ms_, off_ms, st = pat[self.i]
            if self.on:
                if ticks_diff(t, self.ph) < 0:
                    return _ZERO                 # scheduled in the future
                if ticks_diff(t, ticks_add(self.ph, on_ms_)) < 0:
                    self.shown = True
                    return st
                if not self.shown:               # late tick: never skip a pulse
                    self.ph = t
                    self.shown = True
                    return st
                self.on = False
                self.ph = ticks_add(self.ph, on_ms_)
            else:
                end = ticks_add(self.ph, off_ms)
                if ticks_diff(t, end) < 0:
                    return _ZERO
                self.i += 1
                if self.i >= len(pat):
                    self.pat = None
                    self.end = end
                    return _ZERO
                self.on = True
                self.shown = False
                self.ph = end


class HapticPlayer:
    """Tick-driven event + heartbeat player (see module docstring).

    Timing contract (exact when ticked every ms): a step's pulse is on for
    ``[start, start + on_ms)`` and off for the following ``off_ms``. A pulse
    is never skipped: if a late tick finds one that was never output, it
    starts at that tick instead. Metronome beats that are blocked (event
    playing or within ``resume_ms`` after one, or not FULL) are dropped, but
    the beat grid is kept.
    """

    def __init__(self, intensity=1.0, mode=MODE_FULL, guard_ms=EVENT_GUARD_MS,
                 resume_ms=HB_RESUME_MS):
        self.intensity = 1.0
        self.mode = MODE_FULL
        self.guard_ms = guard_ms
        self.resume_ms = resume_ms
        self._ev = _Track()
        self._hb = _Track()
        self._rank = -1          # rank of the playing event
        self._pend = None        # one-slot queue: waits for a higher event
        self._pend_rank = -1
        self._ev_t = [None] * _NRANK   # last start per rank (drop rule)
        self._ev_end = None      # last event end (heartbeat resume)
        self._now = None         # last tick time
        # metronome
        self._period = 0
        self._next = None
        self._beat_pat = TICK
        # output cache (avoid float allocation unless the level changes)
        self._lvl = _ZERO
        self._out = _ZERO
        self._off_t = None       # when output last went to zero (blanking)
        self.set_intensity(intensity)
        self.set_mode(mode)

    # ---- settings ----
    @property
    def enabled(self):
        return self.mode != MODE_OFF

    def set_enabled(self, on):
        self.set_mode(MODE_FULL if on else MODE_OFF)

    def set_mode(self, mode):
        """MODE_* or a token name 'FULL'/'EVENTS'/'OFF'.

        OFF silences and cancels; EVENTS mutes heartbeats only.
        """
        if isinstance(mode, str):
            mode = MODE_NAMES.index(mode)
        self.mode = mode
        if mode != MODE_FULL:
            self._hb.pat = None
        if mode == MODE_OFF:
            self._ev.pat = None
            self._pend = None
            self._rank = -1

    def set_intensity(self, x):
        self.intensity = 0.0 if x < 0 else 1.0 if x > 1 else float(x)
        self._lvl = -1.0         # force output recompute on next tick

    # ---- events ----
    @property
    def busy(self):
        """True while an event (or its queued successor) plays."""
        return self._ev.pat is not None or self._pend is not None

    def play(self, pattern, t_ms=None, priority=None):
        """Start an event at ``t_ms`` (or the next tick); returns accepted."""
        if self.mode == MODE_OFF or not pattern:
            return False
        p = priority_of(pattern) if priority is None else priority
        if p >= _NRANK:
            p = _NRANK - 1
        t = self._now if t_ms is None else t_ms
        if t is not None:
            g = self.guard_ms
            evt = self._ev_t
            for r in range(p if p > 0 else 0, _NRANK):
                s = evt[r]
                if s is not None:
                    d = ticks_diff(t, s)
                    if -g < d < g:
                        return False             # same/higher started < guard ago
        if self._ev.pat is not None and self._rank > p:
            if self._pend is None or p >= self._pend_rank:
                self._pend = pattern             # after the higher one ends
                self._pend_rank = p
                return True
            return False
        self._start_event(pattern, p, t_ms, t)
        return True

    def play_named(self, name, t_ms=None):
        """Event by token name (e.g. 'FOUND'); unknown names return False."""
        return self.play(PATTERNS.get(name), t_ms)

    def play_frame(self, events, t_ms=None, has_event=False):
        """Start a renderer frame's list: ``events[0]`` is an event when
        ``has_event`` (params.haptic started this frame), the rest heartbeats."""
        for i in range(len(events)):
            if i == 0 and has_event:
                self.play_named(events[0], t_ms)
            else:
                self.heartbeat(events[i], t_ms)

    def stop(self):
        """Cancel the current event and the queue."""
        self._ev.pat = None
        self._pend = None
        self._rank = -1

    def _start_event(self, pattern, p, t_ms, t):
        self._hb.pat = None                      # an event replaces the heartbeat
        self._ev.start(pattern, t_ms)
        self._rank = p
        self._ev_t[p if p > 0 else 0] = t

    # ---- heartbeats ----
    def hb_allowed(self, t):
        """True if a heartbeat may start at ``t`` (FULL, no event, resumed)."""
        if self.mode != MODE_FULL or self._ev.pat is not None or self._pend is not None:
            return False
        e = self._ev_end
        return e is None or t is None or ticks_diff(t, e) >= self.resume_ms

    def heartbeat(self, pattern, t_ms=None):
        """Start a heartbeat (name or pattern) if allowed; returns started."""
        if isinstance(pattern, str):
            pattern = PATTERNS.get(pattern)
        if not pattern:
            return False
        if not self.hb_allowed(self._now if t_ms is None else t_ms):
            return False
        self._hb.start(pattern, t_ms)
        return True

    # ---- metronome (heartbeat source when no renderer drives them) ----
    def set_metronome(self, period_ms, t_ms=None, pattern=None):
        """Beat ``pattern`` (default TICK; name or steps) every ``period_ms``.

        0/None stops it. The period is raised to ``min_period(pattern)`` so
        duty stays <= MAX_DUTY_PCT. Starting from stopped beats at ``t_ms``
        (or the next tick). A tempo change keeps phase: the next beat is the
        last beat + the new period (fired at once if that is already past).
        """
        if not period_ms or period_ms <= 0:
            self._period = 0
            return
        if pattern is not None:
            if isinstance(pattern, str):
                pattern = PATTERNS[pattern]
            if pattern is not self._beat_pat:
                self._beat_pat = pattern
        period_ms = int(period_ms)
        lo = min_period(self._beat_pat)
        if period_ms < lo:
            period_ms = lo
        old = self._period
        if old == period_ms:
            return
        if old and self._next is not None:
            self._next = ticks_add(self._next, period_ms - old)
        else:
            self._next = t_ms
        self._period = period_ms

    def stop_metronome(self):
        self.set_metronome(0)

    def sync(self, t_ms):
        """Re-phase so the next beat is at ``t_ms`` (e.g. a ripple spawn)."""
        self._next = t_ms

    @property
    def period_ms(self):
        return self._period

    # ---- main loop ----
    def tick(self, t):
        """Advance to ``t`` (ticks_ms) and return the motor strength 0..1."""
        self._now = t
        lvl = _ZERO
        ev = self._ev
        if ev.pat is not None:
            lvl = ev.advance(t)
            if ev.pat is None:
                self._ev_end = ev.end
                self._rank = -1
                if self._pend is not None:
                    pat, p = self._pend, self._pend_rank
                    self._pend = None
                    t0 = ticks_add(ev.end, MIN_GAP_MS)   # keep pulses distinct
                    self._start_event(pat, p, t0, t0)
                    lvl = ev.advance(t)
        elif self._ev_end is not None and ticks_diff(t, self._ev_end) >= self.resume_ms:
            self._ev_end = None                  # resume window over (wrap-safe)
        if self._period:
            self._beat(t)
        if self._hb.pat is not None:
            h = self._hb.advance(t)
            if not lvl:
                lvl = h
        if self.mode == MODE_OFF:
            lvl = _ZERO
        if lvl != self._lvl:
            if not lvl and self._lvl > 0:
                self._off_t = t
            self._lvl = lvl
            self._out = lvl * self.intensity if lvl else _ZERO
        return self._out

    def blanked(self, t):
        """True from a pulse start until BLANKING_MS after it ends (accel guard)."""
        if self._lvl > 0:
            return True
        o = self._off_t
        return o is not None and 0 <= ticks_diff(t, o) < BLANKING_MS

    def _beat(self, t):
        """Advance the beat grid; start a heartbeat on each allowed beat."""
        if self._next is None:
            self._next = t
        d = ticks_diff(t, self._next)
        if d >= 0:
            sched = ticks_add(self._next, d - d % self._period)
            self._next = ticks_add(sched, self._period)
            if self.hb_allowed(t):               # else: drop beat, keep grid
                self._hb.start(self._beat_pat, sched)
