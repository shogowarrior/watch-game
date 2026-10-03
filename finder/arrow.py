"""DIRECTION overlay: the arrow lifecycle (ui-spec §6 DIRECTION, §5.6).

An ``Arrow`` is born from a scan result ``(theta, s0)`` or a walk-test probe
and then runs by itself at the logic rate (10 Hz):

    reveal (1.5 s) -> turn (guided pacer) | face (static) -> lock -> walk -> done

Angles are degrees clockwise from screen-up; ``theta`` is relative to the
heading at scan start (screen-up). After every ``update`` the object exposes
the RenderParams fields it owns: ``glyph``, ``arrow_deg``, ``cone_deg``,
``arrow_style``, ``sweep``, ``word``, ``top_text``, ``sub`` plus the one-shot
events ``haptic`` and ``toast`` (set only on the update that started them;
two haptics on one update keep the higher rank, ``haptic_patterns.stronger``).
``sigma`` is the display half-angle before clamping. During the guided turn
``sweep`` is the §3 pacer shape ``(pacer_wedge_deg, 12×None, None, False)``.
On expiry (sigma > 60 or age > 120 s) the arrow is done at once, with FARTHER
and SCAN AGAIN on that update; the renderer runs the 600 ms shrink-out itself.

Design choices where the spec is silent (all calibration starting values):
  * steps and still-time grow sigma from birth, not only after lock
  * the turn term is 0.15*|theta| whenever the arrow locks (early tap, skipped
    turn or static face) because the player then claims to face theta
  * static mode auto-locks after FACE_MS; a probe arrow skips turn/lock and
    stays at theta (it is relative to the current heading)
  * the +15 deg unreliable widening is display only: it can drop the tier to
    ``outline`` but never hides or expires the arrow
  * link loss and ``hidden`` (MENU open, SAVER ON interstitial, or stashed
    under a scan) pause the reveal/turn/face clocks: the pacer cannot be seen
    or felt there. Only link loss starts the 20 s relink limit; a lost arrow
    that cannot come back (lost > 20 s, sigma > 60, age > 120 s) ends
    silently (no toast/haptic), a hidden one expires as usual
  * the WRONG WAY chip after a colder hit lasts the hint-chip time (4 s)
"""

import math
from finder import tuning as T
from finder.compat import const, ticks_diff, ticks_add
from finder.estimators.base import ACT_STILL
from finder.render_params import arrow_style
from finder.haptic_patterns import stronger

REVEAL_MS = T.DIRECTION_REVEAL_MS
PACER_DEG_S = T.PACER_DEG_PER_S
TICK_EVERY_DEG = T.PACER_TICK_DEG
LOCK_EASE_MS = T.DIRECTION_LOCK_EASE_MS
LOCK_WORD_MS = T.DIRECTION_WALK_WORD_MS
FACE_MS = const(6000)         # static mode auto-lock (= slowest guided turn; spec-silent)
MAX_AGE_MS = T.ARROW_MAX_AGE_MS
BORN_MAX_SIGMA = T.ARROW_BORN_MAX_SIGMA_DEG
CONE_MIN = T.CONE_DRAW_MIN_DEG
CONE_MAX = T.ARROW_HIDE_SIGMA_DEG   # drawn cone 12..60; a true sigma above it expires
SKIP_TURN_DEG = T.DIRECTION_AHEAD_DEG
CLOCK_MAX_SIGMA = T.DIRECTION_CLOCK_MAX_SIGMA_DEG
COLDER_HIT_MS = T.COLDER_HIT_MS
COLDER_EVERY_MS = T.COLDER_HIT_EVERY_MS
COLDER_HIT_DEG = T.SIGMA_COLDER_HIT_DEG
HINT_MS = T.HINT_CHIP_MS
RELINK_RESTORE_MS = T.ARROW_RELINK_RESTORE_MS
UNRELIABLE_DEG = T.SIGMA_UNRELIABLE_DEG
PROBE_MIN_SIGMA = T.PROBE_SIGMA_MIN_DEG
K_TURN = T.SIGMA_TURN_K
K_STEP = T.SIGMA_STEP_K
K_STILL = T.SIGMA_STILL_K

MODE_GUIDED = "guided"
MODE_STATIC = "static"

PH_REVEAL = "reveal"
PH_TURN = "turn"
PH_FACE = "face"
PH_LOCK = "lock"
PH_WALK = "walk"
PH_DONE = "done"

# RenderParams.sub for each drawn phase (lock belongs to the walk sub-screen)
_SUB = {PH_REVEAL: "reveal", PH_TURN: "turn", PH_FACE: "turn",
        PH_LOCK: "walk", PH_WALK: "walk"}

W_TURN_RIGHT = "TURN RIGHT"
W_TURN_LEFT = "TURN LEFT"
W_FACE = "FACE IT"
W_WALK = "WALK"
T_WRONG_WAY = "WRONG WAY? RESCAN"
T_RESCAN = "TAP TO RESCAN"
TOAST_EXPIRE = "SCAN AGAIN"

H_TICK = "TICK"
H_DOUBLE = "DOUBLE"
H_NOPE = "NOPE"
H_FARTHER = "FARTHER"

_NO_BINS = (None,) * T.SCAN_BINS   # turn pacer: wedge only, no bin bars (§3)


def wrap180(a):
    """Angle to (-180, 180]."""
    a = a % 360.0
    return a - 360.0 if a > 180.0 else a


def wrap360(a):
    """Angle to [0, 360)."""
    a = a % 360.0
    return 0.0 if a >= 360.0 else a


def sigma(s0, turn_deg, steps_walked, still_s, colder_hits):
    """Cone half-angle model of ui-spec §5.6 (degrees), without the display-only
    unreliable widening."""
    a = K_TURN * abs(turn_deg)
    b = K_STEP * steps_walked
    c = K_STILL * still_s
    return math.sqrt(s0 * s0 + a * a + b * b + c * c) + COLDER_HIT_DEG * colder_hits


def clock_word(theta):
    """Clock-hour word, e.g. 120 -> "4 O'CLOCK" (0 -> 12)."""
    h = int(wrap360(theta) / 30.0 + 0.5) % 12
    return "%d O'CLOCK" % (h or 12)


def quad_word(theta):
    """Four-way word for a coarse bearing."""
    a = wrap180(theta)
    m = abs(a)
    if m <= 45.0:
        return "AHEAD"
    if m >= 135.0:
        return "BEHIND"
    return "RIGHT" if a > 0 else "LEFT"


def reveal_word(theta, s):
    """Bottom word of the reveal phase: precision decides the wording."""
    if abs(wrap180(theta)) <= SKIP_TURN_DEG:
        return "AHEAD"
    return clock_word(theta) if s <= CLOCK_MAX_SIGMA else quad_word(theta)


def _out_cubic(x):
    x = 1.0 - x
    return 1.0 - x * x * x


def make(theta_deg, s0_deg, t_ms, mode=MODE_GUIDED, probe=False):
    """New Arrow, or None when sigma at birth exceeds 45 deg (no arrow is born)."""
    a = Arrow(theta_deg, s0_deg, t_ms, mode, probe)
    return a if a.s0 <= BORN_MAX_SIGMA else None


class Arrow:
    """One DIRECTION overlay from birth (scan/probe result) to expiry."""

    def __init__(self, theta_deg, s0_deg, t_ms, mode=MODE_GUIDED, probe=False):
        self.theta = wrap180(theta_deg)
        self.s0 = max(s0_deg, PROBE_MIN_SIGMA) if probe else s0_deg
        self.mode = mode
        self.probe = probe
        self.t0 = t_ms
        self.turn_deg = 0.0
        self.steps_walked = 0
        self.still_s = 0.0
        self.colder_hits = 0
        self.unreliable = False
        self.link_ok = True
        self.phase = PH_REVEAL
        self.pacer = 0.0
        self._ph_t = t_ms
        self._last_t = t_ms
        self._steps_ref = None
        self._lost_t = None
        self._hold_t = None       # hidden or lost since (phase clocks paused)
        self._cold_t = None
        self._hit_t = None
        self._lock_t = None
        self._lock_from = 0.0
        self._ticks = 0
        self._tap = False
        self._walk_deg = self.theta if probe else 0.0
        self._dir = 1.0 if self.theta >= 0 else -1.0
        self._word0 = reveal_word(self.theta, self.s0)
        self.haptic = None
        self.toast = None
        self._render(t_ms)

    # --- queries -------------------------------------------------------------
    @property
    def done(self):
        return self.phase == PH_DONE

    def sigma_true(self):
        """Model sigma without the display-only unreliable widening."""
        return sigma(self.s0, self.turn_deg, self.steps_walked, self.still_s,
                     self.colder_hits)

    # --- inputs --------------------------------------------------------------
    def tap(self):
        """Press/tap: "I'm facing it". True if consumed (turn/face phase)."""
        if self.link_ok and (self.phase == PH_TURN or self.phase == PH_FACE):
            self._tap = True
            return True
        return False

    def update(self, t_ms, activity=0, steps=None, trend=0, partner_walking=False,
               link_ok=True, unreliable=False, hidden=False):
        """Advance to ``t_ms``. ``steps`` is the own cumulative step counter.
        ``hidden`` pauses the phase clocks like link loss, without a relink limit."""
        self.haptic = None
        self.toast = None
        if self.phase == PH_DONE:
            return
        dt = ticks_diff(t_ms, self._last_t)
        if dt < 0:
            dt = 0
        self._last_t = t_ms
        self.unreliable = bool(unreliable)
        if steps is not None:
            ref = self._steps_ref
            if ref is None or steps < ref:
                ref = steps
            d = steps - ref
            self._steps_ref = steps
            # warmer confirms the half-plane ahead: no drift while it holds
            if d > 0 and not (link_ok and trend > 0 and not partner_walking):
                self.steps_walked += d
        if activity == ACT_STILL:
            self.still_s += dt / 1000.0
        if link_ok:
            self._lost_t = None
        elif self._lost_t is None:
            self._lost_t = t_ms
        self.link_ok = link_ok
        if hidden or not link_ok:
            self._update_hidden(t_ms)
            return
        if self._hold_t is not None:
            if self.phase in (PH_REVEAL, PH_TURN, PH_FACE):
                self._ph_t = ticks_add(self._ph_t, ticks_diff(t_ms, self._hold_t))
            self._hold_t = None
        self._step(t_ms, trend, partner_walking)
        if self._stale(t_ms):
            self._expire()
        self._render(t_ms)

    # --- internals -----------------------------------------------------------
    def _stale(self, t_ms):
        return self.sigma_true() > CONE_MAX or ticks_diff(t_ms, self.t0) > MAX_AGE_MS

    def _update_hidden(self, t_ms):
        self._cold_t = None
        self._tap = False
        if self._hold_t is None:
            self._hold_t = t_ms
        lt = self._lost_t
        if lt is not None and ticks_diff(t_ms, lt) > RELINK_RESTORE_MS:
            self.phase = PH_DONE
        elif self._stale(t_ms):
            if self.link_ok:
                self._expire()        # under the MENU: SCAN AGAIN waits for it to close
            else:
                self.phase = PH_DONE
        self._render(t_ms)

    def _lock(self, t_ms):
        self.turn_deg = abs(self.theta)
        self._lock_from = wrap180(self.theta - self.pacer)
        self.phase = PH_LOCK
        self._ph_t = t_ms
        self._lock_t = t_ms
        self._cold_t = None
        self._tap = False
        self.haptic = stronger(self.haptic, H_DOUBLE)

    def _expire(self):
        self.phase = PH_DONE
        self.toast = TOAST_EXPIRE
        self.haptic = stronger(self.haptic, H_FARTHER)   # a lock on the same update: FARTHER wins

    def _step(self, t, trend, partner_walking):
        ph = self.phase
        if ph == PH_REVEAL:
            if ticks_diff(t, self._ph_t) < REVEAL_MS:
                return
            start = ticks_add(self._ph_t, REVEAL_MS)
            if self.probe:
                self.phase = PH_WALK
                self._ph_t = start
                return
            if abs(self.theta) <= SKIP_TURN_DEG:
                self._lock(t)
                return
            self.phase = ph = PH_TURN if self.mode == MODE_GUIDED else PH_FACE
            self._ph_t = start
            self._ticks = 0
            self.pacer = 0.0
            self._tap = False
        if ph == PH_TURN:
            if self._tap:
                self._lock(t)
                return
            goal = abs(self.theta)
            p = PACER_DEG_S * ticks_diff(t, self._ph_t) / 1000.0
            if p >= goal:
                self.pacer = self.theta
                self._lock(t)
                return
            self.pacer = self._dir * p
            n = int(p / TICK_EVERY_DEG)
            if n > self._ticks:
                self._ticks = n
                self.haptic = stronger(self.haptic, H_TICK)
            return
        if ph == PH_FACE:
            if self._tap or ticks_diff(t, self._ph_t) >= FACE_MS:
                self._lock(t)
            return
        if ph == PH_LOCK:
            if ticks_diff(t, self._ph_t) >= LOCK_EASE_MS:
                self.phase = PH_WALK
                self._ph_t = t
        if ph == PH_LOCK or ph == PH_WALK:
            self._colder(t, trend, partner_walking)

    def _colder(self, t, trend, partner_walking):
        if trend >= 0 or partner_walking:
            self._cold_t = None
            return
        if self._cold_t is None:
            self._cold_t = t
            return
        if ticks_diff(t, self._cold_t) < COLDER_HIT_MS:
            return
        if self._hit_t is not None and ticks_diff(t, self._hit_t) < COLDER_EVERY_MS:
            return
        self.colder_hits += 1
        self._hit_t = t
        self._cold_t = t
        self.haptic = stronger(self.haptic, H_NOPE)

    def _render(self, t):
        s = self.sigma_true() + (UNRELIABLE_DEG if self.unreliable else 0.0)
        self.sigma = s
        self.sub = None
        self.glyph = None
        self.arrow_deg = None
        self.cone_deg = None
        self.arrow_style = None
        self.sweep = None
        self.word = None
        self.top_text = None
        ph = self.phase
        if ph == PH_DONE or not self.link_ok:
            return
        self.sub = _SUB[ph]
        self.glyph = "arrow"
        el = ticks_diff(t, self._ph_t)
        cone = CONE_MIN if s < CONE_MIN else CONE_MAX if s > CONE_MAX else s
        style = arrow_style(cone)      # clamped cone: the widening never hides it
        if ph == PH_REVEAL or ph == PH_FACE:
            deg = self.theta
            self.word = self._word0 if ph == PH_REVEAL else W_FACE
        elif ph == PH_TURN:
            deg = self.theta - self.pacer
            self.word = W_TURN_RIGHT if self._dir > 0 else W_TURN_LEFT
            self.sweep = (wrap360(self.pacer), _NO_BINS, None, False)
        else:
            if ph == PH_LOCK:
                x = el / LOCK_EASE_MS
                deg = self._lock_from * (1.0 - _out_cubic(x if x < 1.0 else 1.0))
            else:
                deg = self._walk_deg
            if self._lock_t is not None and ticks_diff(t, self._lock_t) < LOCK_WORD_MS:
                self.word = W_WALK
            if self._hit_t is not None and ticks_diff(t, self._hit_t) < HINT_MS:
                self.top_text = T_WRONG_WAY
            elif style == "outline":
                self.top_text = T_RESCAN
        self.arrow_deg = wrap360(deg)
        self.cone_deg = cone
        self.arrow_style = style
