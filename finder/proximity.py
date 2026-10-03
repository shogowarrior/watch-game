"""Distance -> proximity, zone, readout band and gated trend (ui-spec §5.1-5.5).

Pure logic, no allocation per update. Feed it the estimator's outputs every
logic tick (~10 Hz); read ``zone``, ``intensity``, ``band_idx``/``band``,
``trend``, ``trend_strong`` and ``unreliable`` for ``RenderParams``.

    px = Proximity()
    px.update_est(now, est, my_activity, delivery=meter.ratio(now))
"""

import math
from array import array

from finder import tuning as T
from finder.compat import ticks_diff, ticks_add
from finder.estimators.base import ACT_UNKNOWN, ACT_WALK, ACT_RUN

# tokens.json thresholds.* (via finder.tuning) under this module's names
ZONE_ENTER_M = T.ZONE_ENTER_M          # boundary k: zone k -> k+1
ZONE_EXIT_M = T.ZONE_EXIT_M            # boundary k: zone k+1 -> k
ZONE_DWELL_MS = T.ZONE_DWELL_MS        # both directions
BAND_EDGES_M = T.BAND_EDGES_M
BAND_LABELS = T.BAND_LABELS
BAND_HYST = T.BAND_HYST
ZONE_BANDS = T.ZONE_BANDS              # zone -> (lo, hi)
PROX_FAR_M = T.PROX_D_FAR_M            # p = 0 at this distance
PROX_MIN_M = T.PROX_D_MIN_M            # d floor inside the log
INTENSITY_TAU_MS = T.INTENSITY_TAU_MS
TREND_CONF_MIN = T.TREND_CONF_MIN
TREND_WINDOW_MS = T.TREND_WINDOW_MS
TREND_EVAL_MS = T.TREND_EVAL_MS
TREND_HOLD_EVALS = T.TREND_HOLD_EVALS
TREND_MIN_DELTA_DB = T.TREND_START_DB  # §5.5 starting +1 threshold
TREND_STRONG_CONF = T.TREND_STRONG_CONF
TREND_STRONG_DB = T.TREND_STRONG_DB
TREND_FLIP_MIN_MS = T.TREND_FLIP_MIN_MS
UNRELIABLE_SD_DB = T.UNRELIABLE_SD_DB
UNRELIABLE_DELIVERY = T.UNRELIABLE_DELIVERY
DELIVERY_WINDOW_MS = T.UNRELIABLE_WINDOW_MS

FAR, NEAR, WARM, HOT = T.ZONE_FAR, T.ZONE_NEAR, T.ZONE_WARM, T.ZONE_HOT

_LN_RATIO = T.PROX_LN_RATIO            # ln(PROX_RATIO): p = 1 at PROX_FAR_M / PROX_RATIO (2 m)


# ---- §5.1 proximity and intensity -----------------------------------------

def prox(d_m):
    """Log-spaced proximity: 60 m -> 0.0, 2 m -> 1.0, clamped."""
    if d_m < PROX_MIN_M:
        d_m = PROX_MIN_M
    p = math.log(PROX_FAR_M / d_m) / _LN_RATIO
    return 0.0 if p < 0.0 else 1.0 if p > 1.0 else p


def intensity(i_prev, p, dt_ms, tau_ms=INTENSITY_TAU_MS):
    """First-order smoothing of ``p`` toward the displayed intensity."""
    if dt_ms <= 0:
        return i_prev
    return i_prev + (p - i_prev) * (1.0 - math.exp(-dt_ms / tau_ms))


# ---- §5.2 zones ------------------------------------------------------------

def zone_for(d_m):
    """Zone for a first fix (no hysteresis): enter edges decide."""
    z = 0
    while z < 3 and d_m <= ZONE_ENTER_M[z]:
        z += 1
    return z


def _want(z, d_m):
    """+1 toward the closer zone, -1 toward the farther one, 0 stay."""
    if z < 3 and d_m <= ZONE_ENTER_M[z]:
        return 1
    if z > 0 and d_m >= ZONE_EXIT_M[z - 1]:
        return -1
    return 0


class ZoneTracker:
    """Zone with enter/exit hysteresis and symmetric per-boundary dwell.

    One step per decision; the dwell timer restarts after every commit. The
    first fix (or the first after ``rearm``) jumps straight to its zone.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.zone = None
        self.changed = 0      # +1 closer / -1 farther / 0, for this update only
        self._arm = True
        self._dir = 0
        self._since = 0

    def rearm(self):
        """Next fix jumps without dwell (relink after LINK_LOST). Zone is kept."""
        self._arm = True

    def update(self, now, d_m):
        self.changed = 0
        if d_m is None:
            return self.zone
        z = self.zone
        if self._arm or z is None:
            nz = zone_for(d_m)
            if z is not None and nz != z:
                self.changed = 1 if nz > z else -1
            self.zone = nz
            self._arm = False
            self._dir = 0
            self._since = now
            return nz
        want = _want(z, d_m)
        if want != self._dir:
            self._dir = want
            self._since = now
        if want:
            dwell = ZONE_DWELL_MS[z if want > 0 else z - 1]
            if ticks_diff(now, self._since) >= dwell:
                self.zone = z = z + want
                self.changed = want
                self._dir = _want(z, d_m)   # the next boundary's dwell starts at this commit
                self._since = now
        return self.zone


# ---- §5.4 readout band -----------------------------------------------------

def band_raw(d_m):
    """Band index 0..5 with no hysteresis."""
    b = 0
    while b < 5 and d_m >= BAND_EDGES_M[b]:
        b += 1
    return b


def band(prev, d_m, zone, trend=0):
    """Band index with 15 % hysteresis, zone clamp and trend-monotonic rule.

    ``prev`` None means no previous band. The zone clamp is applied last, so
    the band never contradicts the zone; the caller must drop a trend that the
    final clamp contradicts (``Proximity`` does).
    """
    if prev is None:
        b = band_raw(d_m)
    else:
        b = prev
        while b < 5 and d_m >= BAND_EDGES_M[b] * BAND_HYST:
            b += 1
        while b > 0 and d_m < BAND_EDGES_M[b - 1] / BAND_HYST:
            b -= 1
        if trend > 0 and b > prev:
            b = prev
        elif trend < 0 and b < prev:
            b = prev
    if zone is not None:
        lo, hi = ZONE_BANDS[zone]
        b = lo if b < lo else hi if b > hi else b
    return b


# ---- delivery ratio --------------------------------------------------------

class DeliveryMeter:
    """Packet delivery ratio over the last ``window_ms`` in 1 s buckets."""

    def __init__(self, expected_hz=10.0, window_ms=DELIVERY_WINDOW_MS):
        self.expected_hz = expected_hz
        self._nb = max(1, window_ms // 1000)
        self._b = array("H", [0] * self._nb)
        self.reset()

    def reset(self):
        for i in range(self._nb):
            self._b[i] = 0
        self._head = 0
        self._t = None       # start of the head bucket
        self._span = 0       # completed buckets (capped at nb - 1)

    def _roll(self, now):
        if self._t is None:
            self._t = now
            return
        dt = ticks_diff(now, self._t)
        if dt < 1000:
            return
        k = dt // 1000
        if k > self._nb:
            k = self._nb
        for _ in range(k):
            self._head = (self._head + 1) % self._nb
            self._b[self._head] = 0
            if self._span < self._nb - 1:
                self._span += 1
        self._t = ticks_add(self._t, (dt // 1000) * 1000)

    def expire(self, now):
        """Roll the window without a packet, so a long silence never wraps ``_t``."""
        if self._t is not None:
            self._roll(now)

    def note(self, now):
        """Record one received partner packet."""
        self._roll(now)
        if self._b[self._head] < 65535:
            self._b[self._head] += 1

    def ratio(self, now):
        """Received / expected over the window (1.0 until 1 s of history)."""
        self._roll(now)
        if self._t is None:
            return 1.0
        span_ms = self._span * 1000 + ticks_diff(now, self._t)
        if span_ms < 1000 or self.expected_hz <= 0:
            return 1.0
        n = 0
        for i in range(self._nb):
            n += self._b[i]
        r = n / (self.expected_hz * span_ms / 1000.0)
        return 1.0 if r > 1.0 else r


# ---- §5.5 trend gate -------------------------------------------------------

class TrendGate:
    """Decides the displayed warmer/colder verdict from the estimator's trend.

    Hard gates (checked every update, close at once): own activity walk/run,
    zone known and not HOT, not ``unreliable``. Every ``TREND_EVAL_MS`` the
    gate samples ``rssi_f`` into an 8 s ring; a candidate sign needs
    ``trend_conf >= TREND_CONF_MIN`` and an agreeing 8 s delta of at least
    ``TREND_MIN_DELTA_DB``. A candidate is shown after ``TREND_HOLD_EVALS``
    consecutive evaluations; it is hidden at the first evaluation that does not
    support it. A reversal of sign is blocked for ``TREND_FLIP_MIN_MS`` after
    the last change of the shown value.
    """

    def __init__(self):
        self._n = TREND_WINDOW_MS // TREND_EVAL_MS + 1
        self._r = array("f", [0.0] * self._n)
        self.reset()

    def reset(self):
        self.trend = 0
        self.trend_strong = False
        self.unreliable = False
        self.delta_db = 0.0       # rssi_f change over the window (+ = closer)
        self._cnt = 0             # valid samples in the ring
        self._head = -1
        self._next = None         # time of the next evaluation
        self._cand = 0
        self._cand_n = 0
        self._last_sign = 0       # last non-zero sign shown
        self._changed_t = None    # time the shown value last changed

    def _set(self, now, v):
        if v != self.trend:
            self.trend = v
            self._changed_t = now
            if v:
                self._last_sign = v
        if not v:
            self.trend_strong = False

    def force_off(self, now):
        """Hide the trend now (e.g. the zone clamp moved the band against it)."""
        self._cand_n = 0
        self._set(now, 0)

    def _sample(self, now, rssi_f):
        if self._next is None:
            self._next = now
        late = ticks_diff(now, self._next)
        if late < 0:
            return False
        if late >= 2 * TREND_EVAL_MS or rssi_f is None:
            self._cnt = 0       # gap: the 1 s spacing is broken, refill
        self._next = ticks_add(now, TREND_EVAL_MS) if late >= TREND_EVAL_MS \
            else ticks_add(self._next, TREND_EVAL_MS)
        if rssi_f is None:
            return True
        self._head = (self._head + 1) % self._n
        self._r[self._head] = rssi_f
        if self._cnt < self._n:
            self._cnt += 1
        if self._cnt == self._n:
            self.delta_db = rssi_f - self._r[(self._head + 1) % self._n]
        else:
            self.delta_db = 0.0
        return True

    def update(self, now, est_trend, trend_conf, rssi_f, noise_db, activity, zone,
               delivery=None):
        sd_bad = noise_db is not None and noise_db > UNRELIABLE_SD_DB
        del_bad = delivery is not None and delivery < UNRELIABLE_DELIVERY
        self.unreliable = sd_bad or del_bad
        evald = self._sample(now, rssi_f)
        open_ = (activity == ACT_WALK or activity == ACT_RUN) and zone is not None \
            and zone != HOT and not self.unreliable
        if not open_:
            self._cand_n = 0
            self._set(now, 0)
            return self.trend
        if not evald:
            return self.trend
        c = 0
        if self._cnt == self._n and trend_conf >= TREND_CONF_MIN:
            d = self.delta_db
            if est_trend > 0 and d >= TREND_MIN_DELTA_DB:
                c = 1
            elif est_trend < 0 and d <= -TREND_MIN_DELTA_DB:
                c = -1
        if c == 0 or c != self._cand:
            self._cand = c
            self._cand_n = 1 if c else 0
        else:
            self._cand_n += 1
        if c != self.trend:
            if self.trend:
                self._set(now, 0)   # evidence no longer supports the shown sign
            if c and self._cand_n >= TREND_HOLD_EVALS:
                flip = c == -self._last_sign and self._changed_t is not None \
                    and ticks_diff(now, self._changed_t) < TREND_FLIP_MIN_MS
                if not flip:
                    self._set(now, c)
        if self.trend:
            d = self.delta_db
            self.trend_strong = trend_conf >= TREND_STRONG_CONF and \
                (d if self.trend > 0 else -d) >= TREND_STRONG_DB
        return self.trend


# ---- combined --------------------------------------------------------------

class Proximity:
    """Zone, intensity, band and gated trend from one distance estimate."""

    def __init__(self):
        self.zones = ZoneTracker()
        self.gate = TrendGate()
        self.reset()

    def reset(self):
        """Forget everything (new round / SEARCHING)."""
        self.zones.reset()
        self.gate.reset()
        self.intensity = 0.0
        self.band_idx = None
        self._t = None

    def rearm(self):
        """After LINK_LOST: next fix jumps zone/band/intensity; trend restarts."""
        self.zones.rearm()
        self.gate.reset()
        self.band_idx = None

    @property
    def zone(self):
        return self.zones.zone

    @property
    def zone_changed(self):
        return self.zones.changed

    @property
    def band(self):
        return None if self.band_idx is None else BAND_LABELS[self.band_idx]

    @property
    def trend(self):
        return self.gate.trend

    @property
    def trend_strong(self):
        return self.gate.trend_strong

    @property
    def unreliable(self):
        return self.gate.unreliable

    def update(self, now, d_m, rssi_f=None, noise_db=None, est_trend=0, trend_conf=0.0,
               activity=ACT_UNKNOWN, delivery=None):
        if d_m is None:
            z = self.zones.update(now, None)
            self.gate.update(now, 0, 0.0, None, noise_db, activity, z, delivery)
            self._t = now
            return
        p = prox(d_m)
        if self.band_idx is None:      # first fix after reset/rearm: jump
            self.intensity = p
        else:
            self.intensity = intensity(self.intensity, p, ticks_diff(now, self._t))
        self._t = now
        z = self.zones.update(now, d_m)
        g = self.gate
        g.update(now, est_trend, trend_conf, rssi_f, noise_db, activity, z, delivery)
        prev = self.band_idx
        tr = g.trend
        b = band(prev, d_m, z, tr)
        if prev is not None and ((tr > 0 and b > prev) or (tr < 0 and b < prev)):
            g.force_off(now)
        self.band_idx = b

    def update_est(self, now, est, activity=ACT_UNKNOWN, delivery=None):
        """Convenience: read a ``RangeEstimator`` after its ``update``.

        The unreliable gate takes the learnt packet-to-packet noise ``noise_db``
        (ui-spec §5.5); it is None until the first packet, which the gate reads
        as not unreliable.
        """
        self.update(now, est.dist_m, est.rssi_f, est.noise_db, est.trend, est.trend_conf,
                    activity, delivery)
