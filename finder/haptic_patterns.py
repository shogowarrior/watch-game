"""Haptic patterns and a tick-driven player (pure: no machine imports).

Patterns, priorities and timing rules come from ``haptics`` in
docs/design/tokens.json v0.2 via finder/tuning.py (ui-spec §7): the nine
names TICK, DOUBLE, CLOSER, FARTHER, NOPE, LOST, HOLD, FOUND, BATT. A token
array ``[on, off, on, ...]`` becomes steps ``(on_ms, off_ms)``. Everything is
played by name, at full strength.

Two tracks feed one motor:

* **events** (``play_named``): ranked by ``tuning.HAPTIC_RANK``.
  An event is dropped only if one of the same or higher rank started less
  than ``EVENT_GUARD_MS`` earlier; a TICK (rank 0) only by a higher rank,
  because countdown TICKs come at 1 Hz. A higher rank pre-empts a playing
  event. While a higher event plays, at most one lower event waits and
  starts ``MIN_GAP_MS`` after it ends (so a pattern is never truncated by a
  lesser one); a newer waiting event of the same or higher rank replaces it
  (latest wins), a lower one is dropped.
* **heartbeats** (``heartbeat``; or the internal ``set_metronome`` grid when
  no renderer drives them): TICK/DOUBLE pulses, in FULL mode only. An event
  cuts a running heartbeat and heartbeats resume ``HB_RESUME_MS`` after the
  event ends (tokens: "an event replaces the next heartbeat pulse").

An accepted event lets a pulse that is on finish and starts ``MIN_GAP_MS``
after the motor last went off (§7: every gap >= 60 ms); the rest of the
heartbeat or pre-empted event is dropped.

The frame's ``params.haptic`` goes to ``play_named`` the first time it is
drawn (its result says whether it was accepted), then the renderer's heartbeats
(``()`` or ``[name]``) go to ``heartbeat`` (an event replaces one that starts
with it). A heartbeat can be handed over ahead of its time (the runtime does
this for the ring spawn a renderer announces): it waits as ``beat_due`` and
does not count as ``active`` until it starts. ``tick(t_ms)`` returns the motor
strength (0..1) to apply; call it every loop step (the runtime also calls it
between the bands of a frame and every 1 ms in ``idle`` while a pattern
plays). The hal Motor applies it only on
change. Nothing blocks or sleeps.
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


def steps(arr):
    """Token on/off array -> ((on_ms, off_ms), ...)."""
    out = []
    n = len(arr)
    for i in range(0, n, 2):
        out.append((arr[i], arr[i + 1] if i + 1 < n else 0))
    return tuple(out)


PATTERNS = {}
for _n in NAMES:
    PATTERNS[_n] = steps(_T.HAPTIC_PATTERNS[_n])
RANK = _T.HAPTIC_RANK
TOTAL_MS = {n: sum(p) for n, p in _T.HAPTIC_PATTERNS.items()}   # on + off, per name

TICK = PATTERNS["TICK"]
DOUBLE = PATTERNS["DOUBLE"]
CLOSER = PATTERNS["CLOSER"]
FARTHER = PATTERNS["FARTHER"]
NOPE = PATTERNS["NOPE"]
LOST = PATTERNS["LOST"]
HOLD = PATTERNS["HOLD"]
FOUND = PATTERNS["FOUND"]
BATT = PATTERNS["BATT"]

_NRANK = max(RANK.values()) + 1

# buzz modes, in MENU order (finder.game BUZZ_* and its BUZZ row cycle use these)
MODE_FULL = 0           # default
MODE_EVENTS = 1         # events only, no heartbeats
MODE_OFF = 2

_ZERO = 0.0
_ONE = 1.0


def stronger(cur, name):
    """The haptic to keep when ``name`` is raised in a frame that already has
    ``cur`` (None = none yet): the higher rank, the later one on a tie."""
    return name if cur is None or RANK[name] >= RANK[cur] else cur


class BlankWindow:
    """Accelerometer blanking window around haptic pulses: ``extend(t, ms)``
    when a pattern starts at ``t`` (``ms`` = its length + ``BLANKING_MS``),
    ``active(t)`` while inside, ``expire(now)`` once per tick so a passed
    window is never compared again. Overlapping windows merge; no allocation."""

    __slots__ = ("t0", "until")

    def __init__(self):
        self.reset()

    def reset(self):
        self.t0 = 0
        self.until = None

    def extend(self, t, ms):
        end = ticks_add(t, ms)
        u = self.until
        if u is None or ticks_diff(t, u) >= 0:
            self.t0 = t
            self.until = end
        elif ticks_diff(end, u) > 0:
            self.until = end

    def active(self, t):
        u = self.until
        return u is not None and ticks_diff(t, self.t0) >= 0 and ticks_diff(u, t) > 0

    def expire(self, now):
        u = self.until
        if u is not None and ticks_diff(u, now) <= 0:
            self.until = None


def on_ms(pattern):
    """Total motor-on time of ``pattern``."""
    s = 0
    for st in pattern:
        s += st[0]
    return s


def min_period(pattern):
    """Shortest repeat period keeping ``pattern`` at <= MAX_DUTY_PCT duty."""
    return -(-on_ms(pattern) * 100 // MAX_DUTY_PCT)


class _Track:
    """Plays one pattern; exact edges when ticked every ms, never skips a pulse."""

    def __init__(self):
        self.pat = None
        self.i = 0
        self.on = False
        self.shown = False
        self.stop = False        # end after the pulse that is on
        self.ph = None           # current phase start (None: at next tick)
        self.end = None          # when the last pattern ended

    def start(self, pat, t):
        self.pat = pat
        self.i = 0
        self.on = True
        self.shown = False
        self.stop = False
        self.ph = t

    def waiting(self, t):
        """True while the pattern is scheduled after ``t`` and nothing of it
        has been output yet."""
        return (self.pat is not None and self.on and not self.shown and self.i == 0
                and t is not None and self.ph is not None and ticks_diff(self.ph, t) > 0)

    def pulse_end(self):
        """End of the pulse being output now (None if no pulse is on)."""
        if self.pat is None or not self.on or not self.shown:
            return None
        return ticks_add(self.ph, self.pat[self.i][0])

    def advance(self, t):
        pat = self.pat
        if self.ph is None:
            self.ph = t
        while True:
            on_ms_, off_ms = pat[self.i]
            if self.on:
                if ticks_diff(t, self.ph) < 0:
                    return _ZERO                 # scheduled in the future
                if not self.shown:               # first output tick; late -> full pulse from t
                    self.ph = t
                    self.shown = True
                    return _ONE
                if ticks_diff(t, ticks_add(self.ph, on_ms_)) < 0:
                    return _ONE
                self.on = False
                self.ph = t                      # off phase from the real off edge (§7 gap)
                if self.stop:
                    self.stop = False
                    self.pat = None
                    self.end = self.ph
                    return _ZERO
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
    is never skipped or cut short: a pulse first output by a late tick starts
    at that tick and still plays its full ``on_ms``, and a pulse ended by a
    late tick still gets its full off time (gaps are timed from the real off
    edge). Metronome beats that are blocked (event
    playing or within ``HB_RESUME_MS`` after one, or not FULL) are dropped, but
    the beat grid is kept.
    """

    def __init__(self, mode=MODE_FULL):
        self._ev = _Track()
        self._hb = _Track()
        self._rank = -1          # rank of the playing event
        self._pend = None        # one-slot queue: waits for a higher event
        self._pend_rank = -1
        self._ev_t = [None] * _NRANK   # last start per rank (drop rule)
        self._ev_end = None      # last event end (heartbeat resume)
        self._now = None         # last tick time
        self._on = False         # motor output at the last tick
        self._off_t = None       # when the output last fell to 0 (< MIN_GAP_MS ago)
        # metronome
        self._period = 0
        self._next = None
        self._beat_pat = TICK
        self.set_mode(mode)

    # ---- settings ----
    def set_mode(self, mode):
        """MODE_*: OFF silences and cancels; EVENTS mutes heartbeats only."""
        self.mode = mode
        if mode != MODE_FULL:
            self._hb.pat = None
        if mode == MODE_OFF:
            self._ev.pat = None
            self._pend = None
            self._rank = -1

    # ---- events ----
    @property
    def busy(self):
        """True while an event (or its queued successor) plays."""
        return self._ev.pat is not None or self._pend is not None

    @property
    def active(self):
        """True while any pattern (event, queued event or heartbeat) plays; a
        heartbeat scheduled for later is not playing yet (``beat_due``)."""
        hb = self._hb
        return self.busy or (hb.pat is not None and not hb.waiting(self._now))

    @property
    def beat_due(self):
        """Start time of a heartbeat scheduled after the last tick, or None."""
        hb = self._hb
        return hb.ph if hb.waiting(self._now) else None

    @property
    def beat_playing(self):
        """True while a heartbeat is being output (not just scheduled)."""
        hb = self._hb
        return hb.pat is not None and not hb.waiting(self._now)

    def play_named(self, name, t_ms=None):
        """Start event ``name`` (e.g. 'FOUND') at ``t_ms`` (or the next tick);
        returns accepted. Unknown names return False.

        Dropped if the same or a higher rank started < ``EVENT_GUARD_MS`` ago
        (TICK: only a higher rank). While a higher event plays it waits in the
        one-slot queue; a newer waiting event of the same or higher rank
        replaces it (latest wins), a lower one is dropped.
        """
        pattern = PATTERNS.get(name)
        if pattern is None or self.mode == MODE_OFF:
            return False
        p = RANK[name]
        t = self._now if t_ms is None else t_ms
        if t is not None:
            g = EVENT_GUARD_MS
            evt = self._ev_t
            for r in range(p if p > 0 else 1, _NRANK):
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

    def _start_event(self, pattern, p, t_ms, t):
        hb = self._hb
        ev = self._ev
        e = ev.pulse_end()
        if e is not None:                        # pre-empted mid-pulse: ends on the other track
            self._hb = ev
            self._ev = hb
            hb, ev = ev, hb
            hb.stop = True
        else:
            e = hb.pulse_end()
            if e is not None:
                hb.stop = True                   # the heartbeat pulse plays out, then stops
            else:
                hb.pat = None                    # an event replaces the heartbeat
                e = self._off_t
        s = t_ms
        if e is not None:                        # §7: >= MIN_GAP_MS after the motor went off
            g = ticks_add(e, MIN_GAP_MS)
            if s is None or ticks_diff(g, s) > 0:
                s = g
        ev.start(pattern, s)
        self._rank = p
        self._ev_t[p] = t

    # ---- heartbeats ----
    def hb_allowed(self, t):
        """True if a heartbeat may start at ``t`` (FULL, no event, resumed)."""
        if self.mode != MODE_FULL or self._ev.pat is not None or self._pend is not None:
            return False
        e = self._ev_end
        return e is None or t is None or ticks_diff(t, e) >= HB_RESUME_MS

    def heartbeat(self, name, t_ms=None):
        """Start heartbeat ``name`` at ``t_ms`` (or the next tick) if allowed;
        returns started. ``t_ms`` may be ahead of the last tick: the beat then
        waits (``beat_due``), and an event that starts first replaces it."""
        pattern = PATTERNS.get(name)
        if pattern is None or not self.hb_allowed(self._now if t_ms is None else t_ms):
            return False
        self._hb.start(pattern, t_ms)
        return True

    def cancel_heartbeat(self):
        """Drop a heartbeat that is scheduled but has not started; returns
        whether one was dropped (a beat already on plays out)."""
        hb = self._hb
        if hb.waiting(self._now):
            hb.pat = None
            return True
        return False

    # ---- metronome (heartbeat source when no renderer drives them) ----
    def set_metronome(self, period_ms, t_ms=None, name=None):
        """Beat pattern ``name`` (default TICK) every ``period_ms``.

        0/None stops it. The period is raised to ``min_period`` of it so
        duty stays <= MAX_DUTY_PCT. Starting from stopped beats at ``t_ms``
        (or the next tick). A tempo change keeps phase: the next beat is the
        last beat + the new period (fired at once if that is already past).
        """
        if not period_ms or period_ms <= 0:
            self._period = 0
            return
        if name is not None:
            self._beat_pat = PATTERNS[name]
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
        elif self._ev_end is not None and ticks_diff(t, self._ev_end) >= HB_RESUME_MS:
            self._ev_end = None                  # resume window over (wrap-safe)
        if self._period:
            self._beat(t)
        if self._hb.pat is not None:
            h = self._hb.advance(t)
            if not lvl:
                lvl = h
        if self.mode == MODE_OFF:
            lvl = _ZERO
        if lvl:
            self._on = True
        elif self._on:
            self._on = False
            self._off_t = t
            if ev.pat is not None and ev.on and not ev.shown:   # §7: gap from the real off edge
                g = ticks_add(t, MIN_GAP_MS)
                if ticks_diff(g, ev.ph) > 0:     # (advance above has set ev.ph)
                    ev.ph = g
        elif self._off_t is not None and ticks_diff(t, self._off_t) >= MIN_GAP_MS:
            self._off_t = None                   # gap over (wrap-safe)
        return lvl

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
