"""Touch gesture recognizer (ui-spec §8; pure Python, allocation-free per update).

Feed every touch sample: ``update(t_ms, touching, x, y, multi)``; it returns
an event code (``NONE`` = 0) and sets ``ev_x``/``ev_y`` to where the gesture
started and ``ev_t`` to when its press landed (``began`` is True on the sample
that starts a press). At most one event falls due per sample.

Rules (constructor arguments; long_ms, slop_px and the tap window default to
finder/tuning.py, swipe_px 40 and debounce_ms 60 are spec-silent):
  * release gaps shorter than ``debounce_ms`` (60) are touch dropouts and are
    bridged; a real release is therefore reported ``debounce_ms`` after lift
  * LONG_PRESS fires while still held, once ``long_ms`` (800) has passed
    without moving more than ``slop_px`` (12); nothing else fires for that press
  * SWIPE_L/R/U/D on release when the start->end move on the dominant axis
    is >= ``swipe_px`` (40); screen y grows downwards
  * TAP: a press that stayed within ``slop_px`` and lasted ``tap_min_ms`` ..
    ``tap_max_ms`` (60..400). Samples can be a ~50 ms frame apart on the
    watch, so the floor is checked against the longest the contact can have
    lasted (last untouched to first lifted sample), the ceiling against first
    touching to first lifted sample. Shorter contacts (rain) and 401..799 ms
    holds give nothing
  * a press that ever reports ``multi`` (two fingers) gives nothing
"""

from finder import tuning as T
from finder.compat import ticks_diff

NONE = 0
TAP = 1
DOUBLE_TAP = 2          # retired (never emitted); the slot keeps NAMES indices stable
LONG_PRESS = 3
SWIPE_L = 4
SWIPE_R = 5
SWIPE_U = 6
SWIPE_D = 7
NAMES = ("NONE", "TAP", "DOUBLE_TAP", "LONG_PRESS",
         "SWIPE_L", "SWIPE_R", "SWIPE_U", "SWIPE_D")


class GestureRecognizer:
    def __init__(self, long_ms=T.LONG_PRESS_MS, swipe_px=40, debounce_ms=60,
                 slop_px=T.TAP_MOVE_PX, tap_min_ms=T.TAP_MIN_MS, tap_max_ms=T.TAP_MAX_MS):
        self.long_ms = long_ms
        self.swipe_px = swipe_px
        self.debounce_ms = debounce_ms
        self.slop_px = slop_px
        self.tap_min_ms = tap_min_ms
        self.tap_max_ms = tap_max_ms
        self.reset()

    def reset(self):
        """Forget any press and a not yet returned event."""
        self.down = False       # debounced finger state
        self.began = False      # the last sample started a press (touch-down)
        self.x = 0              # last touching position
        self.y = 0
        self.ev_x = 0
        self.ev_y = 0
        self.ev_t = 0           # when the gesture's press landed
        self._up = False        # lifted, still inside the debounce gap
        self._t_up = 0
        self._t0 = 0
        self._t_pre = 0         # last sample before the press
        self._t_s = None        # last sample time
        self._x0 = 0
        self._y0 = 0
        self._moved = False
        self._spent = False     # long press fired or multi-touch: the press is spent
        self._ev = NONE

    def update(self, t, touching, x=0, y=0, multi=False):
        """Feed one sample; returns the event code or ``NONE``."""
        self.began = False
        if self.down:
            if self._up and ticks_diff(t, self._t_up) >= self.debounce_ms:
                self._finish()
            elif touching:
                self._up = False
                self._track(t, x, y)
            elif not self._up:
                self._up = True
                self._t_up = t
        if not self.down and touching:
            self._press(t, x, y)
        if multi and self.down:
            self._spent = True  # ui-spec §8: ignore multi-touch
        self._t_s = t
        e = self._ev
        self._ev = NONE
        return e

    def _push(self, ev, x, y):
        self._ev = ev
        self.ev_x = x
        self.ev_y = y
        self.ev_t = self._t0

    def _press(self, t, x, y):
        self.down = True
        self.began = True
        self._up = False
        self._t0 = t
        ts = self._t_s
        self._t_pre = t if ts is None else ts
        self._x0 = x
        self._y0 = y
        self._moved = False
        self._spent = False
        self._track(t, x, y)

    def _track(self, t, x, y):
        self.x = x
        self.y = y
        if self._spent:
            return
        if not self._moved:
            dx = x - self._x0
            dy = y - self._y0
            s = self.slop_px
            if dx > s or dx < -s or dy > s or dy < -s:
                self._moved = True
            elif ticks_diff(t, self._t0) >= self.long_ms:
                self._spent = True
                self._push(LONG_PRESS, self._x0, self._y0)

    def _finish(self):
        self.down = False
        self._up = False
        if self._spent:
            return
        dx = self.x - self._x0
        dy = self.y - self._y0
        ax = dx if dx >= 0 else -dx
        ay = dy if dy >= 0 else -dy
        if ax >= ay and ax >= self.swipe_px:
            self._push(SWIPE_R if dx > 0 else SWIPE_L, self._x0, self._y0)
        elif ay > ax and ay >= self.swipe_px:
            self._push(SWIPE_D if dy > 0 else SWIPE_U, self._x0, self._y0)
        elif not self._moved:   # a short drag gives nothing
            # sampled contact: it may have lasted from _t_pre to _t_up
            if (ticks_diff(self._t_up, self._t_pre) >= self.tap_min_ms
                    and ticks_diff(self._t_up, self._t0) <= self.tap_max_ms):
                self._push(TAP, self._x0, self._y0)
