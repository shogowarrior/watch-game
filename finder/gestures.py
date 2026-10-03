"""Touch gesture recognizer (pure Python, allocation-free per update).

Feed one sample per frame: ``update(t_ms, touching, x, y)``; it returns an
event code (``NONE`` = 0) and sets ``ev_x``/``ev_y`` to where the gesture
started. If several events fall due in one frame the extras are queued and
returned by later ``update``/``next`` calls, in order.

Rules (all thresholds are constructor arguments):
  * release gaps shorter than ``debounce_ms`` (60) are touch dropouts and are
    bridged; a real release is therefore reported ``debounce_ms`` after lift
  * LONG_PRESS fires while still held, once ``long_ms`` (600) has passed
    without moving more than ``slop_px``; nothing else fires for that press
  * SWIPE_L/R/U/D on release when the start->end move on the dominant axis
    is >= ``swipe_px`` (40); screen y grows downwards
  * TAP: short press that stayed within ``slop_px``. With ``double_tap=True``
    a TAP is held back until the ``double_ms`` (350) window after the lift
    expires; a second tap starting inside the window (within ``double_px``
    of the first) yields DOUBLE_TAP instead. Without it TAP is not delayed.
"""

from finder.compat import ticks_diff

NONE = 0
TAP = 1
DOUBLE_TAP = 2
LONG_PRESS = 3
SWIPE_L = 4
SWIPE_R = 5
SWIPE_U = 6
SWIPE_D = 7
NAMES = ("NONE", "TAP", "DOUBLE_TAP", "LONG_PRESS",
         "SWIPE_L", "SWIPE_R", "SWIPE_U", "SWIPE_D")

_QN = 4


class GestureRecognizer:
    def __init__(self, double_tap=False, long_ms=600, swipe_px=40,
                 double_ms=350, debounce_ms=60, slop_px=20, double_px=60):
        self.double_tap = double_tap
        self.long_ms = long_ms
        self.swipe_px = swipe_px
        self.double_ms = double_ms
        self.debounce_ms = debounce_ms
        self.slop_px = slop_px
        self.double_px = double_px
        self._qe = [0] * _QN
        self._qx = [0] * _QN
        self._qy = [0] * _QN
        self.reset()

    def reset(self):
        """Forget any press, pending tap and queued events."""
        self.down = False       # debounced finger state
        self.x = 0              # last touching position
        self.y = 0
        self.ev_x = 0
        self.ev_y = 0
        self._up = False        # lifted, still inside the debounce gap
        self._t_up = 0
        self._t0 = 0
        self._x0 = 0
        self._y0 = 0
        self._moved = False
        self._long = False
        self._pend = False      # tap waiting for the double-tap window
        self._pend_t = 0
        self._pend_x = 0
        self._pend_y = 0
        self._qh = 0
        self._qn = 0

    def pending(self):
        """Number of queued events not yet returned."""
        return self._qn

    def next(self):
        """Pop a queued event (``NONE`` if empty) without feeding a sample."""
        if not self._qn:
            return NONE
        i = self._qh
        self._qh = (i + 1) % _QN
        self._qn -= 1
        self.ev_x = self._qx[i]
        self.ev_y = self._qy[i]
        return self._qe[i]

    def update(self, t, touching, x=0, y=0):
        """Feed one sample; returns the next event code or ``NONE``."""
        if self.down:
            if self._up and ticks_diff(t, self._t_up) >= self.debounce_ms:
                self._finish()
            elif touching:
                self._up = False
                self._track(t, x, y)
            elif not self._up:
                self._up = True
                self._t_up = t
        if not self.down:
            if self._pend and ticks_diff(t, self._pend_t) >= self.double_ms:
                self._flush()
            if touching:
                self._press(t, x, y)
        return self.next()

    def _push(self, ev, x, y):
        if self._qn == _QN:     # overflow: drop the oldest
            self._qh = (self._qh + 1) % _QN
            self._qn -= 1
        i = (self._qh + self._qn) % _QN
        self._qe[i] = ev
        self._qx[i] = x
        self._qy[i] = y
        self._qn += 1

    def _flush(self):
        if self._pend:
            self._pend = False
            self._push(TAP, self._pend_x, self._pend_y)

    def _press(self, t, x, y):
        self.down = True
        self._up = False
        self._t0 = t
        self._x0 = x
        self._y0 = y
        self._moved = False
        self._long = False
        self._track(t, x, y)

    def _track(self, t, x, y):
        self.x = x
        self.y = y
        if self._long:
            return
        if not self._moved:
            dx = x - self._x0
            dy = y - self._y0
            s = self.slop_px
            if dx > s or dx < -s or dy > s or dy < -s:
                self._moved = True
                self._flush()
            elif ticks_diff(t, self._t0) >= self.long_ms:
                self._long = True
                self._flush()
                self._push(LONG_PRESS, self._x0, self._y0)

    def _finish(self):
        self.down = False
        self._up = False
        if self._long:
            return
        dx = self.x - self._x0
        dy = self.y - self._y0
        ax = dx if dx >= 0 else -dx
        ay = dy if dy >= 0 else -dy
        if ax >= ay and ax >= self.swipe_px:
            self._flush()
            self._push(SWIPE_R if dx > 0 else SWIPE_L, self._x0, self._y0)
            return
        if ay > ax and ay >= self.swipe_px:
            self._flush()
            self._push(SWIPE_D if dy > 0 else SWIPE_U, self._x0, self._y0)
            return
        if self._moved:         # short drag: no gesture
            self._flush()
            return
        if not self.double_tap:
            self._push(TAP, self._x0, self._y0)
            return
        if self._pend:          # this press began inside the window
            ddx = self._x0 - self._pend_x
            ddy = self._y0 - self._pend_y
            d = self.double_px
            if -d <= ddx <= d and -d <= ddy <= d:
                self._pend = False
                self._push(DOUBLE_TAP, self._x0, self._y0)
                return
            self._flush()
        self._pend = True
        self._pend_t = self._t_up
        self._pend_x = self._x0
        self._pend_y = self._y0
