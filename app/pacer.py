"""Frame lock: frames on an even time grid, at a rate the watch can hold.

    pc = FramePacer(now)
    if ticks_diff(now, pc.t_next) >= 0:              # a frame is due
        slot = pc.begin(now, params.fps_cap, busy_ms)
        renderer.frame(params, display, slot)        # animate to the slot, not to now
        pc.shown(clock())                            # the frame is on the panel

Frames start on a grid of ``period`` ms. ``begin(now)`` returns the frame's
slot, the latest grid time at or before ``now``, and the renderer animates to
it: every drawn frame moves the animation on by a whole number of periods, so
rings travel the same distance each frame however late the loop starts it.
Slots the loop misses entirely are skipped (``missed``), never drawn late in
a burst.

The rate is one of ``T.FPS_LOCKS`` (20, 10, 8, 7, 6, 5), at most the params
cap. 10 and 20 fps divide every zone period (2400, 1600, 1000, 500 ms), so
ring spawns and their heartbeats land on a frame; 8 keeps WARM and HOT on
one. ``busy_ms`` is the loop's busy time since the previous drawn frame
(inputs, logic, render, tx, haptics; idle and gc left out), and ``cost`` the
second largest of the last ``T.FPS_COST_N``, so one stray slow pass (a radio
burst) never moves the lock. The lock drops at once when ``cost`` passes the
period (frames would miss slots), and rises one step after ``T.FPS_RAISE_MS``
in which ``cost`` plus ``T.FPS_MARGIN_PCT`` fitted the faster period all
along. A rate change keeps the grid: the next slot is the current one plus
the new period.

``restart(now)`` (screen woken) puts the next slot at ``now`` and ignores the
busy time measured across the wake (the panel's SLPOUT wait). ``begin`` with
``busy_ms`` None (a state-only frame, screen off) notes nothing and keeps the
lock. Counters for the serial fps line (``window()`` reads and clears them):
frames shown, slots missed, how late frames started after their slot, and
the interval between shown frames (its sd is the frame-time jitter). All
integer work: nothing allocates per frame.
"""

from array import array

from finder.compat import ticks_add, ticks_diff
from finder import tuning as T

LOCKS = T.FPS_LOCKS
N_COST = T.FPS_COST_N
SUM_LIMIT = 0x1FFFFFFF          # interval sums halve past this (MicroPython small ints)


class FramePacer:
    def __init__(self, now=0, cap=None):
        self._cost = array("i", [0] * N_COST)
        self._cap = -1
        self.reset(now, cap)

    def reset(self, now, cap=None):
        self.i = self._cap_index(cap or LOCKS[0])
        self.fps = LOCKS[self.i]
        self.period = 1000 // self.fps
        self.t_next = now
        self._k = 0                 # cost ring: next slot
        self._n = 0                 # ... filled entries
        self.cost = 0               # second largest busy of the ring, ms
        self._up_t = None           # since then a faster lock has fitted
        self._skip = False          # next busy_ms spans a wake: ignore it
        self._anchor = False        # next frame restarts the grid at its start
        self._last = None           # when the previous frame was shown
        self.changes = 0            # lock changes since reset
        self.window()

    def restart(self, now):
        """Screen back on: the next frame is due at once and drawn at the time
        it starts (the grid restarts there: no stale slot after the panel's
        wake wait); the busy time across the wake is ignored."""
        self.t_next = now
        self._anchor = True
        self._skip = True
        self._last = None

    def _cap_index(self, cap):
        """Index of the fastest lock at or under ``cap`` (the last one cached)."""
        if cap == self._cap:
            return self._ic
        i = 0
        n = len(LOCKS) - 1
        while i < n and LOCKS[i] > cap:
            i += 1
        self._cap = cap
        self._ic = i
        return i

    # ---- per frame -----------------------------------------------------------
    def begin(self, now, cap, busy_ms=None):
        """Frame due at ``now`` (``t_next`` reached): returns its slot time."""
        if busy_ms is not None:
            if self._skip:
                self._skip = False
            else:
                self._note(busy_ms)
                self._choose(now, cap)
        else:
            ic = self._cap_index(cap)
            if ic > self.i:            # the cap still applies with the screen off
                self._set(ic)
        if self._anchor:
            self._anchor = False
            self.t_next = now
        per_old = self.period
        late = ticks_diff(now, self.t_next)
        if late < 0:
            late = 0                   # called early (tests): draw the slot anyway
        k = late // per_old
        slot = ticks_add(self.t_next, k * per_old)
        late -= k * per_old
        self.missed += k
        self.t_next = ticks_add(slot, self.period)
        if busy_ms is not None:
            self.late_sum += late
            if late > self.late_max:
                self.late_max = late
        return slot

    def shown(self, t):
        """The frame is on the panel at ``t``: interval stats."""
        self.frames += 1
        last = self._last
        self._last = t
        if last is None:
            return
        d = ticks_diff(t, last)
        self.iv_n += 1
        self.iv_sum += d
        self.iv_sq += d * d
        if d > self.iv_max:
            self.iv_max = d
        if self.iv_sq > SUM_LIMIT:      # no log reading the window: stay small ints
            self.iv_n >>= 1
            self.iv_sum >>= 1
            self.iv_sq >>= 1

    def _note(self, busy_ms):
        c = self._cost
        k = self._k
        old = c[k]
        c[k] = busy_ms
        self._k = (k + 1) % N_COST
        b = self.cost
        if self._n < N_COST:
            self._n += 1
        elif busy_ms < b and old < b:
            return                      # neither touches the top two: cost unchanged
        a = b = 0                       # largest, second largest
        for j in range(self._n):
            v = c[j]
            if v > a:
                b = a
                a = v
            elif v > b:
                b = v
        self.cost = b if self._n > 1 else a

    def _choose(self, now, cap):
        ic = self._cap_index(cap)
        i = self.i
        cost = self.cost
        if ic > i or cost > self.period:        # over the cap, or missing slots: drop now
            j = ic if ic > i else i
            n = len(LOCKS) - 1
            while j < n and cost > 1000 // LOCKS[j]:
                j += 1
            self._set(j)
            self._up_t = None
            return
        if i > ic and cost * (100 + T.FPS_MARGIN_PCT) <= (1000 // LOCKS[i - 1]) * 100:
            if self._up_t is None:
                self._up_t = now
            elif ticks_diff(now, self._up_t) >= T.FPS_RAISE_MS:
                self._set(i - 1)
                self._up_t = None
        else:
            self._up_t = None

    def _set(self, i):
        if i != self.i:
            self.i = i
            self.fps = LOCKS[i]
            self.period = 1000 // self.fps
            self.changes += 1

    # ---- serial log ------------------------------------------------------------
    def window(self):
        """Clear the log counters (``frames``, ``missed``, ``late_*``, ``iv_*``)."""
        self.frames = 0
        self.missed = 0
        self.late_sum = 0
        self.late_max = 0
        self.iv_n = 0
        self.iv_sum = 0
        self.iv_sq = 0
        self.iv_max = 0

    def jitter_ms(self):
        """Standard deviation of the shown-frame interval this window, ms."""
        n = self.iv_n
        if n < 2:
            return 0.0
        m = self.iv_sum / n
        v = self.iv_sq / n - m * m
        return v ** 0.5 if v > 0 else 0.0
