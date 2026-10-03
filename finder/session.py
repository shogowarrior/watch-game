"""Game helpers: own motion snapshot, partner view, live mirror, state byte.

The beacon ``state`` byte (finder/proto.py: "game-defined") is laid out as

    bits 0-3  screen code = index in ``SCREENS`` (MENU is never sent: the
              screen under the menu is); ``SC_BYE`` while shutting down
    bit 4     fallback bump press armed (short press in HOT, 3 s)
    bit 5     goodbye (3 % battery shutdown)
    bit 6     runes confirmed (PAIRING)

Bump timing: a beacon carries ``bump_ago_ms`` (ms since the sender's last
accepted accelerometer tap, measured at transmit time). The receiver puts it
on its own clock as ``t_rx - bump_ago_ms - AIR_MS``. Because only the age is
sent, the unknown offset between the two tick clocks cancels; what remains is
the one-way air/queue latency, a constant ``AIR_MS`` (ESP-NOW ~1-3 ms).
"""

import math
from array import array

from finder.compat import ticks_diff, ticks_add
from finder.estimators.base import MotionInfo, ACT_UNKNOWN, ACT_STILL, ACT_WALK, ACT_RUN
from finder import proto
from finder import tuning as T

SCREENS = T.SCREENS
SC_PAIRING = 0
SC_SEARCHING = 1
SC_FAR = 2
SC_HOT = 5
SC_FOUND = 6
SC_SCANNING = 7
SC_LINK_LOST = 8
SC_BYE = 15
ST_PRESS = 0x10
ST_GOODBYE = 0x20
ST_CONFIRMED = 0x40

AIR_MS = 2
PEER_FRESH_MS = 1500       # partner flags older than this are ignored
STEP_RATE_MS = 2000        # partner cadence window
TAP_KEEP_MS = 70000        # partner tap forgotten after this (> proto BUMP_MAX)


def screen_code(name):
    """Beacon screen code for a RenderParams screen name."""
    i = 0
    for s in SCREENS:
        if s == name:
            return i
        i += 1
    return SC_BYE


def fmt_mss(ms):
    """``m:ss`` up to 9:59, then ``10M+`` (LINK_LOST timer)."""
    s = ms // 1000 if ms > 0 else 0
    if s > T.LOST_TIMER_MAX_S:
        return "10M+"
    return "%d:%02d" % (s // 60, s % 60)


class MotionSnap:
    """Own motion inputs for one logic tick (from finder.motion or the sim)."""

    def __init__(self):
        self.info = MotionInfo()
        self.tilt_deg = None
        self.face_up = True
        self.t = None

    @property
    def activity(self):
        return self.info.activity

    @property
    def steps(self):
        return self.info.steps

    @property
    def walking(self):
        a = self.info.activity
        return a == ACT_WALK or a == ACT_RUN

    def set(self, t_ms, activity=ACT_UNKNOWN, steps=0, step_rate_hz=0.0,
            tilt_deg=None, face_up=True):
        m = self.info
        m.activity = activity
        m.steps = steps
        m.step_rate_hz = step_rate_hz
        self.tilt_deg = tilt_deg
        self.face_up = face_up
        self.t = t_ms

    def from_tracker(self, t_ms, mt):
        """Copy a ``finder.motion.MotionTracker``."""
        from finder.scan import tilt_from_gravity
        self.set(t_ms, mt.activity, mt.steps, mt.step_rate_hz,
                 tilt_from_gravity(mt.gx, mt.gy, mt.gz, mt.z_sign), mt.face_up)


class PeerView:
    """Latest partner beacon fields, packet timing and derived motion."""

    def __init__(self):
        self.motion = MotionInfo()
        self._rx = array("i", [0] * T.RELINK_PACKETS)
        self.reset()

    def reset(self):
        self.last_t = None
        self.n_rx = 0
        self.state = SC_PAIRING
        self.flags = 0
        self.battery = None
        self.rssi_last = None     # partner's raw RSSI of us (None if none)
        self.rssi_filt = None
        self.tap_t = None         # partner's last accepted tap, on our clock
        self._taps = None
        self._st0 = None
        self._sl = 0
        self._st_t = None
        self.goodbye = False
        m = self.motion
        m.activity = ACT_UNKNOWN
        m.steps = 0
        m.step_rate_hz = 0.0

    def on_beacon(self, t_rx, b):
        """Record one partner beacon (``proto.Beacon`` fields) received at ``t_rx``."""
        i = self.n_rx % T.RELINK_PACKETS
        self._rx[i] = t_rx
        self.n_rx += 1
        self.last_t = t_rx
        self.state = b.state
        self.flags = b.flags
        self.battery = None if b.battery == proto.BATT_UNKNOWN else b.battery
        self.rssi_last = None if b.rssi_last == proto.RSSI_NONE else b.rssi_last
        self.rssi_filt = None if b.rssi_filt == proto.RSSI_NONE else b.rssi_filt
        if b.state & ST_GOODBYE:
            self.goodbye = True
        ba = b.bump_ago_ms
        if ba != proto.BUMP_NONE:
            tt = ticks_add(t_rx, -(ba + AIR_MS))
            taps = (b.flags & proto.F_TAPS) >> proto.F_TAPS_SHIFT
            if self.tap_t is None or taps != self._taps or abs(ticks_diff(tt, self.tap_t)) > 100:
                self.tap_t = tt
            self._taps = taps
        # motion: activity + cadence from the u16 step counter
        m = self.motion
        m.activity = b.activity
        if self._st0 is None:
            self._st0 = self._sl = b.steps
            self._st_t = t_rx
            m.steps = 0
        else:
            m.steps += proto.steps_delta(b.steps, self._sl)
            self._sl = b.steps
            el = ticks_diff(t_rx, self._st_t)
            if el >= STEP_RATE_MS:
                m.step_rate_hz = proto.steps_delta(b.steps, self._st0) * 1000.0 / el
                self._st0 = b.steps
                self._st_t = t_rx

    def expire(self, t_ms, stale_ms):
        """Cap old stamps so ticks_diff never wraps: ``last_t`` ages at most
        ``stale_ms`` (then ``live3`` needs new packets), ``tap_t`` is dropped
        after ``TAP_KEEP_MS``."""
        lt = self.last_t
        if lt is not None and ticks_diff(t_ms, lt) > stale_ms:
            self.last_t = ticks_add(t_ms, -stale_ms)
            self.n_rx = 0
        st = self._st_t
        if st is not None and ticks_diff(t_ms, st) > stale_ms:
            self._st_t = ticks_add(t_ms, -stale_ms)
        tt = self.tap_t
        if tt is not None and ticks_diff(t_ms, tt) > TAP_KEEP_MS:
            self.tap_t = None

    def live3(self, t_ms):
        """``RELINK_PACKETS`` packets within ``RELINK_WINDOW_MS`` (relink / SEARCHING exit)."""
        k = T.RELINK_PACKETS
        if self.n_rx < k:
            return False
        oldest = self._rx[self.n_rx % k]
        return (ticks_diff(t_ms, oldest) <= T.RELINK_WINDOW_MS
                and ticks_diff(self.last_t, oldest) <= T.RELINK_WINDOW_MS)

    def age(self, t_ms):
        return None if self.last_t is None else ticks_diff(t_ms, self.last_t)

    def fresh(self, t_ms, ms=PEER_FRESH_MS):
        return self.last_t is not None and ticks_diff(t_ms, self.last_t) <= ms

    @property
    def screen(self):
        return self.state & 0x0F

    @property
    def sweeping(self):
        return bool(self.flags & proto.F_SWEEP)

    @property
    def walking(self):
        a = self.motion.activity
        return bool(self.flags & proto.F_WALK) or a == ACT_WALK or a == ACT_RUN

    @property
    def still(self):
        return self.motion.activity == ACT_STILL

    @property
    def pressed(self):
        return bool(self.state & ST_PRESS)

    @property
    def confirmed(self):
        return bool(self.state & ST_CONFIRMED)


class LiveMirror:
    """Live signal mirror (ui-spec §5.7): 150 ms EMA of raw RSSI, min/max since start."""

    def __init__(self):
        self.reset(0)

    def reset(self, t_ms):
        self.ema = None
        self._t = t_ms
        self.lo = 0.0
        self.hi = 0.0
        self.value = None

    def add(self, t_ms, rssi):
        e = self.ema
        if e is None:
            e = float(rssi)
            self.lo = self.hi = e
        else:
            dt = ticks_diff(t_ms, self._t)
            a = 1.0 - math.exp(-(dt if dt > 0 else 0) / T.MIRROR_EMA_MS)
            e += a * (rssi - e)
            if e < self.lo:
                self.lo = e
            if e > self.hi:
                self.hi = e
        self._t = t_ms
        self.ema = e
        span = self.hi - self.lo
        if span < T.MIRROR_MIN_SPAN_DB:
            span = T.MIRROR_MIN_SPAN_DB
        v = (e - self.lo) / span
        self.value = 0.0 if v < 0.0 else 1.0 if v > 1.0 else v
